"""Confinement and quotas for kernel workers: the platform-independent seam.

Part scripts execute arbitrary Python inside the worker subprocess, so every
worker is spawned under two separate promises:

* **confinement** — what the process may reach: writes only inside the roots
  the client granted, no network, no signals at anything but itself. On macOS
  that is the seatbelt profile (``sandbox_macos``); Linux and Windows arrive
  in later slices and report ``unsupported`` until they do.
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
import shutil
import sys
import tempfile
from dataclasses import dataclass, field

from .quotas import Quotas, enforcement
from .quotas import resolve as resolve_quotas
from .sandbox_macos import SANDBOX_EXEC, build_profile  # noqa: F401 — re-exported

_TRUTHY = {"1", "true", "yes", "on"}

#: ``sys.platform`` -> the module implementing ``build(...)``. Slice 3 adds
#: ``"win32": "sandbox_windows"``; anything else (and, for now, Windows) falls
#: to :class:`NullBackend`.
_BACKENDS = {"darwin": "sandbox_macos", "linux": "sandbox_linux"}

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

    ``build(argv, write_roots, quotas, posture, server_pid, *, confine=True)``
    is the module-level factory; it returns
    ``(argv, env_additions, confinement, quotas_report, backend)``.
    """

    #: Everything the backend could not do as asked, in plain language.
    warnings: list[str] = []

    def attach(self, proc) -> None:
        """Called right after ``Popen``: cgroup placement, job-object
        assignment. Never a ``preexec_fn`` — CPython documents it as unsafe in
        a threaded parent, and the server is threaded."""

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
    """The backend for a platform with no confinement of its own. Quotas fall
    to the supervisor, which is platform-independent."""

    def __init__(self) -> None:
        self.warnings = []


@dataclass
class SandboxPlan:
    """Everything the client needs to spawn one confined, capped worker.

    Built once per client and reused for every respawn, so a worker that is
    killed and restarted comes back under identical terms.
    """

    #: What to ``Popen`` (on macOS, sandbox-exec-wrapped).
    argv: list[str]
    #: Child env *overrides*, merged over ``os.environ`` by the client.
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

    def prepare_tmp(self) -> str:
        """Make sure the private temp dir exists (0700), and return it.

        Called before every spawn: a client that was stopped and started again
        must not hand a worker a ``$TMPDIR`` that no longer exists.
        """
        os.makedirs(self.tmp_dir, mode=0o700, exist_ok=True)
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
         server_pid: int | None = None) -> SandboxPlan:
    """Plan one worker: private temp dir, environment, confinement, quotas.

    *quotas* may be a resolved :class:`~agentcad.kernel.quotas.Quotas`, a dict
    of overrides, or ``None`` to resolve the configured layers here.
    *posture* defaults to :func:`default_posture`. *server_pid* is passed to
    the backend so a seccomp filter can refuse signals at the server.

    The plan owns a directory: call :meth:`SandboxPlan.release` when the
    worker is gone for good.
    """
    if not isinstance(quotas, Quotas):
        quotas = resolve_quotas(quotas)
    posture = posture or default_posture()
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
    # `agentcad check --work-dir <project>/scratch` is REFUSED by
    # `CheckRunner._work_dir`, and "a refused path leaves nothing behind" is a
    # promise with a test on it. Creation therefore belongs to whoever owns the
    # directory: `cli._writable_roots` makes the projects dir and `~/.agentcad`
    # because those are the server's own.
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

    try:
        opt_out = _opt_out_reason()
        build = _backend_build()
        if build is None:
            backend: Backend = NullBackend()
            argv, additions, confinement = list(argv), {}, {
                "status": "unsupported", "mechanism": None,
                "detail": {
                    "reason": f"no confinement backend for {sys.platform!r}"},
            }
            report = enforcement(
                quotas, ["supervisor"] if quotas.memory_mb > 0 else [])
        else:
            argv, additions, confinement, report, backend = build(
                argv, write_roots, quotas, posture, server_pid,
                confine=opt_out is None)
    except BaseException:
        # Nobody holds the plan yet, so nobody would ever remove the directory
        # it just made.
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    if opt_out is not None:
        # The backend was told not to confine; name *why* it is off, because
        # "off" with no reason reads as a bug in the sandbox.
        confinement = {"status": "off", "mechanism": None,
                       "detail": {"reason": opt_out}}
    env.update(additions)

    return SandboxPlan(
        argv=list(argv), env=env, tmp_dir=tmp_dir,
        posture=confinement.get("detail", {}).get("posture") or posture,
        confinement=confinement, quotas=report, quotas_obj=quotas,
        warnings=list(getattr(backend, "warnings", [])), backend=backend)


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
    """Platform can confine at all: macOS with the seatbelt CLI present.

    **Deliberately still ``False`` on Linux.** Linux workers ARE confined since
    PRD-006 slice 2 (``sandbox_linux`` + the worker's own Landlock/seccomp
    preamble), but this function and :func:`status` are the *legacy* strings
    that `/api/health` and ``agentcad check`` read today, and the live answer
    for Linux can only come from the worker's ping report — which is what the
    health **object** (``sandbox.report(kernel)``, a later slice) is for.
    Flipping the string here would make health claim `active` from intent, the
    one thing Decision 8 forbids. Windows has no confinement at all.
    """
    if sys.platform == "darwin":
        from . import sandbox_macos

        return sandbox_macos.has_seatbelt()
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
