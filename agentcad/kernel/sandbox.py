"""Confinement and quotas for kernel workers: the platform-independent seam.

Part scripts execute arbitrary Python inside the worker subprocess, so every
worker is spawned under two separate promises:

* **confinement** — what the process may reach: writes only inside the roots
  the client granted, no network, no signals at anything but itself. On macOS
  that is the seatbelt profile (``sandbox_macos``); on Linux it is the worker
  confining *itself* with Landlock and seccomp before it imports build123d
  (``sandbox_linux`` -> ``_preamble`` -> ``_confine``). On Windows the worker
  is started inside an **AppContainer** — a package SID with no capabilities,
  the roots granted by ``icacls``, spawned through ``CreateProcessW`` because
  ``subprocess`` cannot pass a lowbox token (``sandbox_windows``, PRD-006b) —
  and capped by a job object.
* **quotas** — how much of the machine it may take (``quotas.py``). These are
  *tiers*: a knob may be enforced by a cgroup, an rlimit, a job object or the
  parent's supervisor loop, and health names the tier in effect rather than
  promising one.

The two are independent. ``AGENTCAD_NO_SANDBOX=1`` (env, wins) or
``{"sandbox": false}`` in the user config file opts out of **confinement**;
the quotas still apply, because a runaway script may not take the machine
down whether or not the operator trusts it with the filesystem.

:func:`plan` is the entry point: it creates the worker's **private temp dir**,
builds the child's environment, picks the platform backend and returns a
:class:`SandboxPlan` the client spawns from. Deliberately importable from
server code: no ``OCP``/build123d imports here.

Honesty (design spec, Decision 8): ``confinement.status`` on a plan is what
this process *intends* to apply. The client downgrades it from the worker's
own ping report — a preamble that failed is ``off`` with a warning, never
``active`` by intent.
"""

from __future__ import annotations

import importlib
import os
import platform
import shutil
import sys
import tempfile
from dataclasses import dataclass, field

from .quotas import Quotas, enforcement
from .quotas import resolve as resolve_quotas
from .sandbox_macos import SANDBOX_EXEC, build_profile  # noqa: F401 — re-exported

_TRUTHY = {"1", "true", "yes", "on"}

#: ``sys.platform`` -> the module implementing ``build(...)``. Anything else
#: falls to :class:`NullBackend`: confinement ``unsupported``, quotas left to
#: the platform-independent supervisor.
_BACKENDS = {"darwin": "sandbox_macos", "linux": "sandbox_linux",
             "win32": "sandbox_windows"}

#: The read postures (design spec, Decision 2). ``local`` is the historical
#: stance: read anywhere, write only in the roots. ``hosted`` narrows reads to
#: an allow-list that excludes the state dir and other users' homes; it is
#: Linux-only, and a backend that cannot apply it says so in ``warnings``.
LOCAL, HOSTED = "local", "hosted"

#: Every worker's private scratch dir is named this way, so an orphan left by
#: a killed server is identifiable in ``/tmp`` (or ``/var/folders/...``).
TMP_PREFIX = "agentcad-worker-"


class Backend:
    """What a platform module provides. Not a base class — the three backends
    are independent — but the protocol the client and the supervisor code
    against, so neither needs a platform branch of its own.

    ``build(argv, write_roots, quotas, posture, server_pid, *, confine=True,
    pool_size=1)`` is the module-level factory; it returns
    ``(argv, env_additions, confinement, quotas_report, backend)``.
    """

    #: Everything the backend could not do as asked, in plain language.
    warnings: list[str] = []

    def refresh(self) -> dict:
        """Environment additions recomputed for the spawn about to happen.

        Almost everything a backend decides is fixed at plan time and must
        stay fixed (that is what makes a respawn identical). ``RLIMIT_NPROC``
        is the exception, and it has to be: it is a **per-uid** ceiling that
        the kernel checks against the *calling* process's own limit, so a
        number measured once at ``KernelClient.__init__`` is wrong for every
        worker but the first — worker 2's own threads have already moved the
        live count by the time worker 3 forks, and worker 3 died inside
        ``import build123d`` (measured, review C2). Recomputing here, at each
        spawn and each respawn, is what keeps the cap a *headroom* rather than
        a race.

        ``{}`` means "nothing to recompute", which is the honest answer for a
        backend with no rlimits at all.
        """
        return {}

    def spawn(self, argv: list[str], env: dict[str, str] | None):
        """Launch the worker **yourself**, or ``None`` to let the client
        ``subprocess.Popen`` it as it always has.

        It exists for one reason (design spec, Decision 1): a Windows
        AppContainer needs ``PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES`` on
        the ``STARTUPINFOEX``, and CPython's ``subprocess`` can pass a handle
        list and nothing else — so the Windows backend spawns through
        ``CreateProcessW`` and returns an object with the ``Popen`` surface.
        Every other backend returns ``None`` and the spawn path is unchanged.
        """
        return None

    def prepare_tmp_hook(self, tmp_dir: str) -> None:
        """Called after the private temp dir is (re)created, before each spawn.

        Windows uses it to grant the directory to the package SID and to build
        the package tree the lowbox token redirects ``%TEMP%`` into; the others
        have nothing to do — a 0700 directory owned by the same uid is already
        exactly what the seatbelt and Landlock granted.
        """

    def attach(self, proc) -> None:
        """Called right after the spawn: cgroup placement, job-object
        assignment. Never a ``preexec_fn`` — CPython documents it as unsafe in
        a threaded parent, and the server is threaded. A backend that spawned
        the process itself may already have done it (Windows assigns the job
        while the worker is still suspended)."""

    def can_sample(self) -> bool:
        """Whether :meth:`rss_bytes` can actually measure this platform.

        The supervisor tier is only *named* where this is true: a sampler that
        always answers ``None`` enforces nothing, and `mechanism` is read as a
        promise (design spec, Decision 8).
        """
        return True

    def rss_bytes(self, proc) -> int | None:
        """Resident size for one supervisor sample; ``None`` if unmeasurable."""
        return None

    def explain_exit(self, proc, returncode: int | None) -> dict | None:
        """``{"reason", "tier"}`` when the platform can say why a worker died
        (an OOM kill, a CPU signal); ``None`` when it cannot."""
        return None

    def release(self) -> None:
        """Drop whatever the backend created for this worker."""


class NullBackend(Backend):
    """The backend for a platform AgentCAD has no support for at all.

    It confines nothing **and caps nothing**: the supervisor is
    platform-independent in its *logic*, but its one measurement is not, so on
    a platform with no ``rss_bytes`` the request loop would sample ``None``
    forever and kill nobody. Health says ``off`` rather than naming a tier that
    is armed and blind.
    """

    def __init__(self) -> None:
        self.warnings = []

    def can_sample(self) -> bool:
        return False


@dataclass
class SandboxPlan:
    """Everything the client needs to spawn one confined, capped worker.

    Built once per client and reused for every respawn, so a worker that is
    killed and restarted comes back under identical terms.
    """

    #: What to ``Popen`` (on macOS, sandbox-exec-wrapped).
    argv: list[str]
    #: Child env *overrides* **as they were at construction**, merged over
    #: ``os.environ`` by the client. This is the snapshot health and the tests
    #: read; what a *spawn* uses is :meth:`spawn_env`, which is this plus the
    #: backend's freshly measured ``RLIMIT_NPROC``.
    env: dict[str, str]
    #: The private per-worker temp dir, already created.
    tmp_dir: str
    #: The read posture actually in effect (a backend that cannot apply the
    #: requested one reports the one it applied, plus a warning).
    posture: str
    #: ``{"status": "active"|"off"|"unsupported", "mechanism": str|None,
    #: "detail": dict}`` — **intended** confinement (see the module docstring).
    confinement: dict
    #: ``{"status": "active"|"off", "mechanism": str|None, "limits": dict}``.
    quotas: dict
    #: The same caps as an object: the supervisor reads ``memory_mb`` and
    #: ``sample_interval_s`` as numbers on every sample.
    quotas_obj: Quotas
    warnings: list[str] = field(default_factory=list)
    backend: Backend | None = None
    #: How many workers share this uid's ``RLIMIT_NPROC`` budget — the pool
    #: size, or 1 for a lone client. It scales the fork headroom (see
    #: :func:`plan`), so the cap a fork bomb hits is at most
    #: ``pids_headroom x pool_size`` extra tasks rather than a number that
    #: starves the siblings.
    pool_size: int = 1

    def spawn_env(self) -> dict[str, str]:
        """The child's environment overrides for the spawn about to happen.

        :attr:`env` plus whatever the backend recomputes (:meth:`Backend.
        refresh`) — today only the ``RLIMIT_NPROC`` half of the payload, which
        is measured against the uid's *live* task count and is therefore stale
        the moment a sibling worker starts its own threads. ``env`` itself is
        left alone: it is the construction-time snapshot, and health and the
        tests read it as such.
        """
        env = dict(self.env)
        # `getattr`, because `Backend` is a protocol and not a base class: the
        # Windows backend and a test double implement only what they need.
        refresh = getattr(self.backend, "refresh", None)
        if refresh is not None:
            env.update(refresh() or {})
        return env

    def prepare_tmp(self) -> str:
        """Make sure the private temp dir exists (0700), and return it.

        Called before every spawn: a client that was stopped and started again
        must not hand a worker a ``$TMPDIR`` that no longer exists.

        The backend gets it too (:meth:`Backend.prepare_tmp_hook`), and for the
        same reason it is re-run rather than done once: on Windows the ACE that
        makes this directory reachable from inside the AppContainer, and the
        package tree the container's ``%TEMP%`` is redirected into, both live
        *in* the directory — so a recreated one arrives without them.
        `getattr`, because `Backend` is a protocol and not a base class.
        """
        os.makedirs(self.tmp_dir, mode=0o700, exist_ok=True)
        hook = getattr(self.backend, "prepare_tmp_hook", None)
        if hook is not None:
            hook(self.tmp_dir)
        return self.tmp_dir

    def wipe_tmp(self) -> None:
        """Empty the private temp dir, keeping the directory itself.

        A killed worker's scratch is nobody's, and the respawned worker's
        environment still points here.
        """
        try:
            entries = list(os.scandir(self.tmp_dir))
        except OSError:
            return
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    shutil.rmtree(entry.path, ignore_errors=True)
                else:
                    os.unlink(entry.path)
            except OSError:
                pass

    def release(self) -> None:
        """Remove the temp dir and release the backend. Idempotent: ``stop()``
        after a crash, and a second ``stop()``, must both be quiet."""
        shutil.rmtree(self.tmp_dir, ignore_errors=True)
        if self.backend is not None:
            self.backend.release()


def plan(argv: list[str], writable_dirs: list[str], *,
         quotas: Quotas | dict | None = None, posture: str | None = None,
         server_pid: int | None = None, pool_size: int = 1) -> SandboxPlan:
    """Plan one worker: private temp dir, environment, confinement, quotas.

    *quotas* may be a resolved :class:`~agentcad.kernel.quotas.Quotas`, a dict
    of overrides, or ``None`` to resolve the configured layers here.
    *posture* defaults to :func:`default_posture`. *server_pid* is passed to
    the backend so a seccomp filter can refuse signals at the server.

    *pool_size* is how many workers will share this uid's ``RLIMIT_NPROC``
    budget. It exists because that limit is **per uid** but is checked against
    the *calling* process's own ceiling: a three-worker pool whose every slot
    was capped at "the live count when the client was constructed + headroom"
    starved itself — the third worker died inside ``import build123d``
    (measured, review C2). The cap is therefore ``live task count, measured at
    each spawn + pids_headroom x pool_size``, which bounds a fork bomb at
    ``headroom x pool_size`` extra tasks and leaves every sibling room to run.

    The plan owns a directory: call :meth:`SandboxPlan.release` when the
    worker is gone for good.
    """
    if not isinstance(quotas, Quotas):
        quotas = resolve_quotas(quotas)
    posture = posture or default_posture()
    pool_size = max(1, int(pool_size))
    # The process planning the worker IS the server, so a caller that leaves
    # this out still gets a filter that protects the right pid — a `None` here
    # would silently reduce the seccomp rule to "no signals at pid 0".
    server_pid = os.getpid() if server_pid is None else server_pid

    # The one temp root a worker gets. Never `tempfile.gettempdir()` itself:
    # that directory is shared, so granting it lets one worker's script read
    # and overwrite a sibling's scratch (design spec, Decision 1).
    tmp_dir = tempfile.mkdtemp(prefix=TMP_PREFIX)

    # A write root has to EXIST before Landlock can grant it (`os.open` on a
    # missing path is ENOENT: the grant is lost AND the failure downgrades the
    # worker's own report). But creating one here would be wrong: `plan()` is
    # handed caller-supplied paths whose acceptance is decided elsewhere —
    # `agentcad check --work-dir <project>/scratch` is REFUSED (by the CLI
    # before the service is built, and by `CheckRunner._work_dir` again), and
    # "a refused path leaves nothing behind" is a promise with a test on it.
    # Creation therefore belongs to whoever owns the directory:
    # `cli._writable_roots` makes the projects dir because that one is the
    # server's own, and the CLI makes an ACCEPTED `--work-dir` because by then
    # it has been accepted.
    write_roots = [os.path.realpath(d) for d in writable_dirs]
    write_roots.append(os.path.realpath(tmp_dir))

    env = {
        # `tempfile` honours TMPDIR/TEMP/TMP; HOME also silences ezdxf's
        # ~/.cache warning and keeps a script out of the user's home.
        "TMPDIR": tmp_dir, "TEMP": tmp_dir, "TMP": tmp_dir,
        "XDG_CACHE_HOME": tmp_dir, "HOME": tmp_dir,
        # The roots deny writes to site-packages; don't even attempt .pyc
        # writes there (each would be a denied open + a sandbox log line).
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if sys.platform == "win32":
        # Everything a Windows process reads for "somewhere of my own", pointed
        # at the one directory an AppContainer worker may write.
        # `LOCALAPPDATA` is the load-bearing one and not for the obvious
        # reason: the lowbox token derives the container's own ``%TEMP%`` from
        # it as ``%LOCALAPPDATA%\Packages\<profile>\AC\Temp``, which is why the
        # backend's `prepare_tmp_hook` creates exactly that tree in here.
        env.update({"USERPROFILE": tmp_dir, "APPDATA": tmp_dir,
                    "LOCALAPPDATA": tmp_dir})

    backend: Backend | None = None
    try:
        opt_out = _opt_out_reason()
        build = _backend_build()
        if build is None:
            backend = NullBackend()
            argv, additions, confinement = list(argv), {}, {
                "status": "unsupported", "mechanism": None,
                "detail": {
                    "reason": f"no confinement backend for {sys.platform!r}"},
            }
            # No tier at all: this backend cannot sample, so naming the
            # supervisor would promise a cap nothing can observe.
            report = enforcement(
                quotas,
                ["supervisor"] if quotas.memory_mb > 0 and backend.can_sample()
                else [])
        else:
            argv, additions, confinement, report, backend = build(
                argv, write_roots, quotas, posture, server_pid,
                confine=opt_out is None, pool_size=pool_size)
        if opt_out is not None and confinement.get("status") != "unsupported":
            # The backend was told not to confine; name *why* it is off,
            # because "off" with no reason reads as a bug in the sandbox.
            #
            # `unsupported` is left alone deliberately: on a platform with no
            # confinement to begin with (Windows, an unknown OS) "off" would
            # read as "there is a switch and it is down", and the operator
            # would go looking for the switch. Nothing was opted out of.
            confinement = {"status": "off", "mechanism": None,
                           "detail": {"reason": opt_out}}
        env.update(additions)

        return SandboxPlan(
            argv=list(argv), env=env, tmp_dir=tmp_dir,
            posture=confinement.get("detail", {}).get("posture") or posture,
            confinement=confinement, quotas=report, quotas_obj=quotas,
            warnings=list(getattr(backend, "warnings", [])), backend=backend,
            pool_size=pool_size)
    except BaseException:
        # Everything from the temp dir onwards is inside the try, because until
        # a SandboxPlan exists nobody holds any of it: nobody would remove the
        # directory this function just made, close the job handle a backend
        # opened, or rmdir the cgroup it created. "A plan() that raises leaves
        # nothing behind" is only true if it is true of every step.
        if backend is not None:
            backend.release()
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise


def _backend_build():
    """The current platform's ``build`` function, or ``None`` if it has no
    backend. The import is deferred so a module for another OS is never
    loaded, and so adding one is a single entry in :data:`_BACKENDS`."""
    name = _BACKENDS.get(sys.platform)
    if name is None:
        return None
    return importlib.import_module(f".{name}", __package__).build


def default_posture() -> str:
    """:data:`HOSTED` on a hosted instance, else :data:`LOCAL`.

    A malformed ``AGENTCAD_MODE`` is *not* this function's refusal to make —
    server startup already refuses it, loudly — so it falls back to ``local``
    rather than making a worker unspawnable.
    """
    from ..core.appmode import ModeError, resolve_mode

    try:
        return HOSTED if resolve_mode().hosted else LOCAL
    except ModeError:
        return LOCAL


def supported() -> bool:
    """Platform can confine at all: macOS with the seatbelt CLI, Linux with a
    Landlock ABI this build knows how to use on a machine it has a syscall
    table for, Windows 8+ with ``icacls`` (the AppContainer, PRD-006b).

    A *capability*, not a claim: this says a newly spawned worker would be
    confined, which is what ``status()``/``available()`` have always meant and
    what ``agentcad check`` records beside a timing. Whether a **running**
    worker actually is confined is a different question, answered only by that
    worker's own ping report — :func:`report` is where the two meet.
    """
    if sys.platform == "darwin":
        from . import sandbox_macos

        return sandbox_macos.has_seatbelt()
    if sys.platform == "linux":
        from ._confine import ARCH, LANDLOCK_MIN_ABI, landlock_abi

        return landlock_abi() >= LANDLOCK_MIN_ABI and platform.machine() in ARCH
    if sys.platform == "win32":
        from . import sandbox_windows

        return sandbox_windows.supported()
    return False


def _opt_out_reason() -> str | None:
    """Why confinement is switched off, or ``None`` when it is not.

    The env var wins over the config file either way: AGENTCAD_NO_SANDBOX=1
    disables even if config says otherwise, and an explicit
    AGENTCAD_NO_SANDBOX=0 re-enables over ``{"sandbox": false}``.
    """
    env = os.environ.get("AGENTCAD_NO_SANDBOX")
    if env is not None and env.strip() != "":
        return "AGENTCAD_NO_SANDBOX" if env.strip().lower() in _TRUTHY else None
    from ..config import load_config

    if load_config().get("sandbox") is False:
        return 'the config file sets "sandbox": false'
    return None


def _disabled() -> bool:
    """User opt-out, as a boolean."""
    return _opt_out_reason() is not None


def available() -> bool:
    """True when a newly spawned worker would be confined."""
    return supported() and not _disabled()


def wrap_argv(argv: list[str], writable_dirs: list[str]) -> list[str]:
    """Wrap a worker argv in sandbox-exec when confinement is on; else unchanged.

    The narrow, plan-free form kept for callers that only want the argv (and
    for the historical contract). :func:`plan` is what the client uses: it
    also owns the private temp dir, the environment and the quotas.
    """
    if not writable_dirs or not available():
        return list(argv)
    return [SANDBOX_EXEC, "-p", build_profile(writable_dirs), *argv]


def report(kernel) -> dict:
    """The health object for a running kernel: per-facet, measured, honest.

    *kernel* is a :class:`~agentcad.kernel.client.KernelClient`, a
    :class:`~agentcad.kernel.pool.KernelPool` or anything exposing the same
    three attributes — everything is read through ``getattr`` with a default,
    so a bare client with no plan (the test suite's session fixture, a
    ``KernelClient()`` in a script) answers the object rather than raising.

    ``{"status", "mechanism", "posture", "confinement", "quotas", "warnings"}``,
    where the top-level ``status``/``mechanism`` are the **confinement**'s —
    that is what the historical ``sandbox`` field in health has always meant
    (FR11) and renaming the meaning would silently flip every reader.

    The rule that makes it worth publishing (Decision 8): a plan that intends
    to confine only stays ``active`` while the worker's own ping report agrees.
    A preamble that failed is ``off``, with the failure in ``warnings`` and the
    mechanism dropped — a mechanism named beside ``off`` claims something is in
    force. A worker that has not been pinged yet has no report to disagree
    with, so the intent stands until it answers.

    Two corollaries of the same rule (review M1/M2): the kernel's own
    ``sandboxed`` flag wins wherever it is present, so this function and
    ``client.sandboxed`` can never say different things; and a quota **tier**
    is dropped from ``quotas.mechanism`` the moment the worker reports it did
    not apply — an empty ``rlimits`` list under a mechanism naming ``rlimit``
    is a cap nothing is enforcing.
    """
    from .client import confinement_holds

    plan_obj = getattr(kernel, "_plan", None) or getattr(kernel, "plan", None)
    live = getattr(kernel, "sandbox_report", None) or {}
    if plan_obj is None:
        # No plan: the client was built the historical way (no writable dirs,
        # no quotas), so there is nothing to confine it with and no cap.
        conf = {"status": status(getattr(kernel, "sandboxed", False)),
                "mechanism": None, "detail": {}}
        return {"status": conf["status"], "mechanism": None, "posture": LOCAL,
                "confinement": conf,
                "quotas": {"status": "off", "mechanism": None, "limits": {}},
                "warnings": []}

    conf = dict(plan_obj.confinement)
    conf["detail"] = dict(conf.get("detail") or {})
    warnings = list(plan_obj.warnings)
    quotas_report = dict(plan_obj.quotas)
    if conf["status"] == "active" and live and not confinement_holds(live):
        conf["status"] = "off"
        conf["mechanism"] = None
    # `client.sandboxed` is this same rule applied at ping time, and it is what
    # every other reader in the system consults. Preferring it here (review M1)
    # closes the one gap where the two could disagree: a worker that answered
    # `ping` with no `sandbox` object at all leaves `live` empty, so the plan's
    # `active` stood while `client.sandboxed` was already False. `None` means
    # the object has no such attribute, which is not a denial.
    if conf["status"] == "active" and getattr(kernel, "sandboxed", None) is False:
        conf["status"] = "off"
        conf["mechanism"] = None
        if not live:
            warnings.append(
                "the worker did not report what it applied, so the "
                "confinement this plan intended cannot be claimed")
    if live:
        # `appcontainer`/`appcontainer_sid` are the Windows pair, and they are
        # the *measured* ones: the plan's detail carries the SID it intended,
        # this is the SID the worker read off its own token.
        for key in ("landlock_abi", "seccomp", "rlimits", "appcontainer",
                    "appcontainer_sid"):
            if key in live:
                conf["detail"][key] = live[key]
        for failure in live.get("failures") or []:
            stage = failure.get("stage")
            if stage == "landlock_root":
                # Not "could not apply landlock": the ruleset landed and the
                # process IS confined — one root out of it was not granted
                # (review I2). Saying it the other way sent operators looking
                # for a broken sandbox instead of a missing directory.
                warnings.append(
                    f"the worker lost a Landlock grant (the ruleset is in "
                    f"force; writes there will be denied): "
                    f"{failure.get('error')}")
            elif stage == "appcontainer":
                # Not "could not apply": the AppContainer is applied to the
                # worker by its parent, before its first instruction. What
                # failed is the *check* — and a confinement nobody could verify
                # is one nobody may claim, which `confinement_holds` has
                # already done above.
                warnings.append(
                    f"the worker could not read its own token, so the "
                    f"AppContainer confinement cannot be claimed: "
                    f"{failure.get('error')}")
            else:
                warnings.append(
                    f"the worker could not apply {stage}: "
                    f"{failure.get('error')}")
        # A tier is a promise, so it is dropped the moment the worker says it
        # did not apply it (review M2). `setrlimit` can be refused — an
        # existing hard limit below ours, a Darwin `EINVAL` — and `mechanism`
        # naming `rlimit` over an empty `rlimits` list claimed a cap that is
        # not in force.
        if "rlimits" in live and not live["rlimits"]:
            tiers = (quotas_report.get("mechanism") or "").split("+")
            if "rlimit" in tiers:
                tiers = [tier for tier in tiers if tier != "rlimit"]
                quotas_report["mechanism"] = "+".join(tiers) or None
                quotas_report["status"] = "active" if tiers else "off"
                warnings.append(
                    "the worker applied no rlimits, so the rlimit quota tier "
                    "is not in force")
    # A backend can also fail *after* the plan was built (a cgroup that refused
    # the pid, a job object that refused the assignment), and that warning is
    # only on the backend.
    for warning in getattr(plan_obj.backend, "warnings", []) or []:
        if warning not in warnings:
            warnings.append(warning)
    return {"status": conf["status"], "mechanism": conf.get("mechanism"),
            "posture": plan_obj.posture, "confinement": conf,
            "quotas": quotas_report, "warnings": warnings}


def status(sandboxed: bool | None = None) -> str:
    """Effective sandbox status: "active" | "off" | "unsupported".

    With ``sandboxed`` (the actual state of the running kernel client) the
    answer reflects the live service; without it, it reflects what a NEW
    KernelClient constructed with writable dirs would get.
    """
    if not supported():
        return "unsupported"
    if sandboxed is None:
        sandboxed = available()
    return "active" if sandboxed else "off"
