"""Linux confinement and quotas: the payload the worker confines itself with.

The backend half of :mod:`agentcad.kernel.sandbox` for ``sys.platform ==
"linux"``. Unlike macOS there is nothing to wrap the argv in — the argv comes
back unchanged — because Linux confinement is **in-process**: this module
computes the roots and writes them into ``AGENTCAD_CONFINE``, and the worker's
own preamble (``_preamble`` -> ``_confine``) applies Landlock and seccomp to
itself before it imports build123d. That is what makes it work in the shipped
container at all: ``bwrap`` needs ``unshare``, which Docker's default seccomp
profile denies, and the Dockerfile refuses to hand out ``SYS_ADMIN`` to get it
(design spec, Decision 1).

Two read postures (Decision 2):

* ``local`` — read anywhere (``/``), the historical stance, matching the
  seatbelt's documented posture. Writes are still only the granted roots.
* ``hosted`` — an allow-list, so a member's part script can no longer read
  ``<state-dir>/secret.key`` and forge a session. The same uid runs the server
  and the worker, so DAC cannot express this; Landlock can.

Quotas come in three tiers, and the mechanism string names only the ones this
client actually installed (Decision 3):

* ``cgroup`` — :class:`CgroupTier`, opt-in **by delegation**: a directory the
  operator handed us (``AGENTCAD_CGROUP_DIR=<path>``) or, with an explicit
  ``=auto``, a genuinely delegated own cgroup. Unset means the tier is not
  probed at all. It is the only tier that OOM-kills, and the only one that can
  be missing without anything being wrong.
* ``rlimit`` — ``RLIMIT_AS``/``RLIMIT_NPROC``, applied by the worker to itself
  from the payload. A breach here is a recoverable ``MemoryError`` or
  ``BlockingIOError`` *inside the script*, with a line number, and the warm
  worker survives — the single best property of the tier.
* ``supervisor`` — the parent sampling ``/proc/<pid>/statm``
  (:meth:`LinuxBackend.rss_bytes`) in its request loop.

Deliberately importable from server code and from any OS (every syscall is
lazy, and :func:`landlock_abi` answers ``0`` off Linux): no ``OCP``/build123d.
"""

from __future__ import annotations

import errno
import functools
import json
import os
import platform
import secrets
import signal
import sys

from ._confine import ARCH, LANDLOCK_MIN_ABI, landlock_abi
from .quotas import Quotas, enforcement

#: The ``hosted`` posture's read allow-list: what a Python process genuinely
#: needs to run, and nothing that belongs to the instance or to a person. The
#: state dir and ``HOME`` are the deliberate omissions (Decision 2). Entries
#: that do not exist on the machine are dropped rather than granted, so a
#: missing ``/lib32`` is not a failed rule in the worker's report.
HOSTED_READ_ROOTS = ["/usr", "/lib", "/lib64", "/lib32", "/bin", "/sbin",
                     "/etc", "/opt", "/proc", "/dev", "/sys"]

#: Two pseudo-files that need their own *file* rules: the ``/`` read grant does
#: not cover writes to a pseudo-fs, and the spike measured both as EACCES
#: without them. ``/dev/null`` is a sink scripts use; ``/proc/self/clear_refs``
#: is how ``_meter`` turns a lifetime RSS peak into a per-request one.
EXTRA_FILES = ["/dev/null", "/proc/self/clear_refs"]

#: Used only if ``/proc`` cannot be walked. ``RLIMIT_NPROC`` is a per-*uid*
#: ceiling, so this number is a floor under the worker's own fork budget and
#: guessing low would kill it during ``import build123d`` for no security gain.
_FALLBACK_UID_PROCESSES = 256

#: An operator-delegated cgroup v2 directory (Decision 4's Model 2), or
#: :data:`CGROUP_AUTO`. **Unset means no cgroup tier at all**: the whole tier is
#: opt-in, so nothing is probed and nothing is mutated unless an operator asked
#: for it. ``off`` is an explicit "not even auto", which is how a test pins the
#: supervisor as the memory tier on a host that has a delegated subtree.
CGROUP_ENV = "AGENTCAD_CGROUP_DIR"

#: ``AGENTCAD_CGROUP_DIR=auto`` — look at the process's *own* cgroup (the
#: systemd ``Delegate=yes`` shape) instead of a directory the operator named.
#: Opt-in on purpose: that route can move the server's own pids, and doing that
#: to a machine nobody offered us is exactly the "activation by capability"
#: Decision 4 rejects.
CGROUP_AUTO = "auto"

#: Where the unified hierarchy is mounted. Only used to resolve the path
#: ``/proc/self/cgroup`` reports.
CGROUP_ROOT = "/sys/fs/cgroup"

#: What a cgroup has to delegate before the tier means anything. ``cpu`` is
#: wanted too but is not required: it throttles, it never kills, so a host that
#: delegates memory and pids without it still gets a real cap.
CGROUP_CONTROLLERS = ("memory", "pids")
CGROUP_WANTED = ("memory", "pids", "cpu")

#: ``cpu.max`` is "quota period" in microseconds. 100000 us is the kernel's own
#: default period, so ``cpu_percent`` x 1000 is that many percent of one CPU.
CGROUP_CPU_PERIOD_US = 100000

_SIGXCPU = getattr(signal, "SIGXCPU", None)

_OFF = {"off", "0", "false", "no", "none"}


def build(argv: list[str], write_roots: list[str], quotas: Quotas,
          posture: str, server_pid: int | None, *, confine: bool = True):
    """Plan a Linux worker: ``(argv, env, confinement, quotas, backend)``.

    The argv is returned **unchanged**: there is no wrapper binary. Everything
    this backend decides travels in ``env["AGENTCAD_CONFINE"]``, which the
    worker's preamble reads before importing anything heavy.

    *confine* is ``False`` when the operator opted out
    (``AGENTCAD_NO_SANDBOX`` / ``{"sandbox": false}``): the ``landlock`` and
    ``seccomp`` keys are omitted and confinement reports ``off``, but the
    **rlimits are still emitted** — opting out of the sandbox is not opting
    out of the caps.
    """
    backend = LinuxBackend()
    payload: dict = {"posture": posture, "rlimits": _rlimits(quotas)}

    # Tier order, and every entry is something this client actually installed:
    # a cgroup directory that exists and carries the limits, an rlimit payload
    # the worker will apply, a sampler the request loop will run. `mechanism`
    # is read as a promise, so it is derived from the installation and never
    # from the intention (design spec, Decision 8).
    tiers: list[str] = []
    if _wants_cgroup(quotas):
        cgroup = CgroupTier.probe(backend.warnings)
        if cgroup is not None and backend.place_in(cgroup, quotas):
            tiers.append("cgroup")
            # A tier the worker does not apply and cannot see, told to it for
            # one reason: `pids.max` surfaces as a `BlockingIOError` *inside*
            # the script, and `denials.classify` refuses to name a denial no
            # worker reported a live cap for (Decision 9).
            payload["quotas"] = ["cgroup"]
    if payload["rlimits"]:
        tiers.append("rlimit")
    if quotas.memory_mb > 0:
        tiers.append("supervisor")

    abi = landlock_abi() if confine else 0
    machine = platform.machine()
    detail = {"landlock_abi": abi, "posture": posture}
    if not confine:
        confinement = {"status": "off", "mechanism": None, "detail": detail}
    else:
        payload["landlock"] = {
            "read_roots": _read_roots(posture, write_roots),
            "write_roots": list(write_roots),
            "extra_files": list(EXTRA_FILES),
        }
        payload["seccomp"] = {"server_pid": server_pid}
        reason = _unsupported_reason(abi, machine)
        if reason is None:
            confinement = {"status": "active", "mechanism": "landlock+seccomp",
                           "detail": detail}
        else:
            # The payload still goes: `landlock_apply` refuses below the ABI
            # floor by itself, and seccomp is worth having even where Landlock
            # is not. The status stays honest either way — and the client
            # replaces it with the worker's own report regardless.
            confinement = {"status": "unsupported", "mechanism": None,
                           "detail": {**detail, "reason": reason}}
            backend.warnings.append(f"Linux confinement is unsupported: {reason}")

    env = {"AGENTCAD_CONFINE": json.dumps(payload, sort_keys=True)}
    return list(argv), env, confinement, enforcement(quotas, tiers), backend


def _unsupported_reason(abi: int, machine: str) -> str | None:
    """Why this machine cannot be confined, or ``None`` when it can."""
    if machine not in ARCH:
        return (f"unknown machine {machine!r} (no seccomp syscall table; "
                f"known: {', '.join(sorted(ARCH))})")
    if abi < LANDLOCK_MIN_ABI:
        if abi <= 0:
            return ("no Landlock: the kernel is older than 5.13, or Landlock "
                    "is missing from the boot-time lsm= list")
        return (f"Landlock ABI {abi} < {LANDLOCK_MIN_ABI}: without the "
                f"TRUNCATE right every truncating open would be denied")
    return None


def _rlimits(quotas: Quotas) -> dict[str, list[int]]:
    """The caps the worker applies to itself. Hard equals soft, so a script
    cannot raise one back after the preamble lowered it."""
    rlimits: dict[str, list[int]] = {}
    if quotas.address_space_mb > 0:
        # Deliberately loose (3x the memory cap by default): RLIMIT_AS exists
        # to turn a runaway virtual reservation into a recoverable
        # `MemoryError` with a line number, not to be the memory cap.
        limit = quotas.address_space_mb * 1024 * 1024
        rlimits["RLIMIT_AS"] = [limit, limit]
    if quotas.pids_headroom > 0:
        # Per-uid, and it counts threads on Linux: a fixed 32 killed the
        # worker during `import build123d` in the spike.
        nproc = live_uid_process_count() + quotas.pids_headroom
        rlimits["RLIMIT_NPROC"] = [nproc, nproc]
    return rlimits


def _read_roots(posture: str, write_roots: list[str]) -> list[str]:
    """What the worker may read, for the posture in effect."""
    if posture != "hosted":
        return ["/"]
    from .._resources import resource_root

    candidates = [*HOSTED_READ_ROOTS, sys.prefix, sys.base_prefix,
                  str(resource_root()), *write_roots]
    roots: list[str] = []
    for path in candidates:
        # Dropped rather than granted: a rule on a missing path is an ENOENT
        # the worker would report as a failure, and `failures` is what makes
        # the client call the confinement degraded.
        if path and path not in roots and os.path.exists(path):
            roots.append(path)
    return roots


def live_uid_process_count() -> int:
    """What this uid's ``RLIMIT_NPROC`` budget is already spending, right now.

    ``RLIMIT_NPROC`` is measured against everything else the user already has
    (an IDE, a browser, the server itself), so the budget has to be measured
    rather than assumed — hence a ``/proc`` walk rather than a constant.

    It counts **tasks, not processes**, because that is what the Linux kernel
    counts: every thread is a ``task_struct`` against the limit. The difference
    is not cosmetic here — a warm kernel worker runs 15-22 threads (TBB, BLAS),
    so a per-*process* count under-measures a three-worker pool by roughly
    sixty tasks and the third worker dies inside ``import build123d`` with a
    ``pthread_create`` EAGAIN. Measured: the second module-scoped worker in
    ``tests/test_sandbox_linux.py`` did exactly that.
    """
    uid = os.getuid()
    count = 0
    try:
        entries = os.listdir("/proc")
    except OSError:
        return _FALLBACK_UID_PROCESSES
    for name in entries:
        if not name.isdigit():
            continue
        try:
            mine, threads = False, 1
            with open(f"/proc/{name}/status", encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith("Uid:"):
                        mine = int(line.split()[1]) == uid
                        if not mine:
                            break
                    elif line.startswith("Threads:"):
                        threads = int(line.split()[1])
                        break
            if mine:
                count += threads
        except (OSError, IndexError, ValueError):
            continue  # the process exited between listdir and open
    return count or _FALLBACK_UID_PROCESSES


# ------------------------------------------------------------- the cgroup tier

def _wants_cgroup(quotas: Quotas) -> bool:
    """Whether a cgroup would cap anything at all. With every knob off there is
    nothing to put in one, and an empty directory would still make health say
    ``cgroup`` — a mechanism that limits nothing."""
    return bool(quotas.memory_mb > 0 or quotas.pids > 0 or quotas.cpu_percent)


class CgroupTier:
    """A cgroup v2 subtree this process may create worker cgroups under.

    Opt-in **by delegation, never by capability** (design spec, Decision 4),
    and opt-in by the operator: with ``AGENTCAD_CGROUP_DIR`` unset nothing here
    runs. The shipped container mounts ``/sys/fs/cgroup`` read-only and
    root-owned, and the two ways to a writable subtree are ``--cap-add
    SYS_ADMIN`` (a near-root capability, rejected) or a host-delegated subtree
    bind-mounted in — Model 2, verified end to end in the spike as an
    unprivileged uid:

    .. code-block:: sh

        # on the host, once
        mkdir /sys/fs/cgroup/agentcad
        echo "+memory +pids +cpu" > /sys/fs/cgroup/cgroup.subtree_control
        echo "+memory +pids +cpu" > /sys/fs/cgroup/agentcad/cgroup.subtree_control
        chown -R 10001:10001 /sys/fs/cgroup/agentcad
        # in compose
        cgroup_parent: /agentcad
        volumes: ["/sys/fs/cgroup/agentcad:/cg:rw"]
        environment: {AGENTCAD_CGROUP_DIR: /cg}

    ``AGENTCAD_CGROUP_DIR=auto`` is the second route, the systemd
    ``Delegate=yes`` shape — see :meth:`_from_own_cgroup` for why it is a
    separate opt-in and not something discovered.

    Every step of the probe is *verified* rather than assumed, and any failure
    returns ``None`` **with a reason that reaches health's warnings**, so the
    caller falls back to the rlimit and supervisor tiers and the operator can
    see that it did.
    """

    def __init__(self, root: str) -> None:
        #: The delegated directory worker cgroups are made *under*.
        self.root = root

    def __repr__(self) -> str:  # pragma: no cover - diagnostics
        return f"CgroupTier({self.root!r})"

    # ------------------------------------------------------------- discovery

    @staticmethod
    def probe(warnings: list[str] | None = None) -> "CgroupTier | None":
        """The usable subtree, or ``None``.

        **Nothing is probed unless an operator asked for it.** Unset (and
        ``off``) means no cgroup tier: no directory is read, no cgroup is
        created, and the server's own placement is left alone — which is the
        state of the shipped container and of every developer laptop, and is
        not a warning. ``AGENTCAD_CGROUP_DIR=<path>`` is Model 2 and
        ``=auto`` is the own-cgroup route.

        Every failure of a probe that *was* asked for lands in *warnings*:
        Decision 4 says the tier "falls back with a health warning", and a
        silent fallback is how an operator ends up believing in a cap that is
        not there.
        """
        configured = os.environ.get(CGROUP_ENV, "").strip()
        if not configured or configured.lower() in _OFF:
            return None
        if configured.lower() == CGROUP_AUTO:
            tier, reason = CgroupTier._from_own_cgroup()
        else:
            tier, reason = CgroupTier._from_dir(configured)
        if tier is None and warnings is not None:
            warnings.append(
                f"the cgroup quota tier is off: {reason} "
                f"(from {CGROUP_ENV}={configured!r})")
        return tier

    @staticmethod
    def _from_dir(path: str) -> tuple["CgroupTier | None", str | None]:
        """An operator-delegated directory, checked step by step."""
        if not os.path.isdir(path):
            return None, f"{path} is not a directory"
        try:
            with open(os.path.join(path, "cgroup.subtree_control"),
                      encoding="utf-8") as handle:
                handle.read()
        except OSError as exc:
            return None, (f"{path} is not a cgroup v2 directory this process "
                          f"can read: {exc.strerror or exc}")
        reason = _enable_controllers(path)
        if reason is not None:
            return None, reason
        reason = _probe_mkdir(path)
        if reason is not None:
            return None, reason
        return CgroupTier(path), None

    @staticmethod
    def _from_own_cgroup() -> tuple["CgroupTier | None", str | None]:
        """This process's own cgroup, *when it was genuinely delegated to it*.

        The ``systemd Delegate=yes`` shape, reached only through
        ``AGENTCAD_CGROUP_DIR=auto``. This is the route that can move the
        server's own pids, so every refusal below exists to keep Decision 4's
        rule — opt-in **by delegation, never by capability**:

        * **root is refused outright.** ``os.access`` answers ``W_OK`` for uid
          0 almost everywhere, so a root server would "discover" a delegated
          subtree on any machine at all. That is activation by capability, and
          the whole point of the ruling is that a near-root capability is not
          a licence to reorganise the host's cgroups.
        * **the subtree must be ours**: ``st_uid`` has to equal our euid.
          Delegation is someone handing us a directory, which on every
          documented route (systemd ``Delegate=``, the Model 2 ``chown``)
          means it is chowned to us.
        * the **root cgroup** (``0::/``) is never taken: a process there is not
          in a delegated subtree, it is on the machine.
        * everything is checked **before** anything is mutated — the
          controllers are present and ``subtree_control`` is writable *first*,
          and only then does the server move **its own** process into a
          ``server`` leaf (the no-internal-process rule). Only its own:
          migrating strangers out of a shared cgroup would be someone else's
          decision. If a later step still fails, the reason says the move
          happened, so the fallback warning is the whole truth.
        """
        euid = getattr(os, "geteuid", None)
        if euid is None:
            return None, "this platform has no uid to check delegation against"
        euid = euid()
        if euid == 0:
            return None, ("this process is root: a delegated subtree has to be "
                          "given to us, and root can write any cgroup on the "
                          "machine — use AGENTCAD_CGROUP_DIR=<path> instead")
        path = _own_cgroup_path()
        if path is None:
            return None, "cannot read /proc/self/cgroup"
        if path == "/":
            return None, "this process is in the root cgroup, not a delegated one"
        own = os.path.join(CGROUP_ROOT, path.lstrip("/"))
        if not os.path.isdir(own):
            return None, f"{own} is not a directory"
        try:
            owner = os.stat(own).st_uid
        except OSError as exc:
            return None, f"cannot stat {own}: {exc.strerror or exc}"
        if owner != euid:
            return None, (f"{own} belongs to uid {owner}, not to this process "
                          f"(uid {euid}): it was not delegated to us")
        if not os.access(own, os.W_OK | os.X_OK):
            return None, f"{own} is not a writable cgroup directory"
        if not os.access(os.path.join(own, "cgroup.subtree_control"), os.W_OK):
            return None, f"{own} does not delegate cgroup.subtree_control"
        available = _read_words(own, "cgroup.controllers")
        absent = [name for name in CGROUP_CONTROLLERS if name not in available]
        if absent:
            return None, (f"{own} has no {', '.join(absent)} controller to "
                          f"delegate (it offers: {' '.join(available) or 'nothing'})")
        # Nothing above this line changed anything. Past it, the server may be
        # in a different cgroup than it started in.
        reason, moved = _leave_internal_cgroup(own)
        if reason is not None:
            return None, reason
        moved_note = (f" (this process was moved into {own}/server and stays "
                      f"there)" if moved else "")
        reason = _enable_controllers(own)
        if reason is not None:
            return None, reason + moved_note
        reason = _probe_mkdir(own)
        if reason is not None:
            return None, reason + moved_note
        return CgroupTier(own), None

    # ----------------------------------------------------------- per worker

    def make_worker(self, name: str, quotas: Quotas) -> str:
        """Create ``<root>/<name>`` with the caps written into it.

        Raises ``OSError`` if a *required* limit could not be written — the
        caller drops the tier rather than reporting a cap nothing applies.
        """
        path = os.path.join(self.root, name)
        os.makedirs(path, exist_ok=True)
        if quotas.memory_mb > 0:
            _write(path, "memory.max", str(quotas.memory_mb * 1024 * 1024))
            # Load-bearing: with swap left at `max` the spike's 400 MB
            # allocation under a 200 MB cap swapped instead of dying. Absent
            # (ENOENT) means the kernel has no swap accounting, and then there
            # is no swap to escape into.
            _write(path, "memory.swap.max", "0", optional=True)
        if quotas.pids > 0:
            _write(path, "pids.max", str(quotas.pids))
        # Throttles, never kills — so it is safe to set tight, and optional
        # because `cpu` is the one controller the tier does not require.
        _write(path, "cpu.max",
               f"{quotas.cpu_percent * 1000} {CGROUP_CPU_PERIOD_US}"
               if quotas.cpu_percent else "max", optional=True)
        return path

    def attach(self, cg_dir: str, pid: int) -> None:
        """Place *pid* in the worker's cgroup. The parent writes it right after
        ``Popen`` — never a ``preexec_fn`` (unsafe in a threaded parent, and
        the server is threaded). The child has only begun interpreter start-up
        by then, so everything that matters (the 500 MB OCCT import included)
        is charged to the worker's cgroup."""
        _write(cg_dir, "cgroup.procs", str(pid))

    def oom_kills(self, cg_dir: str) -> int:
        """``memory.events``' ``oom_kill`` counter, or 0 if unreadable.

        The difference between this before and after is what separates a kernel
        OOM kill from the supervisor's kill and from a timeout kill — all three
        leave ``returncode == -9``.
        """
        try:
            with open(os.path.join(cg_dir, "memory.events"),
                      encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith("oom_kill "):
                        return int(line.split()[1])
        except (OSError, IndexError, ValueError):
            return 0
        return 0

    def release(self, cg_dir: str) -> None:
        """Remove the worker's cgroup. EBUSY (a process still in it) and a
        second release must both be quiet."""
        try:
            os.rmdir(cg_dir)
        except OSError:
            pass


def _write(directory: str, name: str, value: str, *,
           optional: bool = False) -> None:
    """Write one cgroup control file. cgroup writes are single-shot and
    unbuffered; the value never ends in a newline the kernel would reject."""
    try:
        with open(os.path.join(directory, name), "w", encoding="utf-8") as handle:
            handle.write(value)
    except OSError as exc:
        if optional and exc.errno in (errno.ENOENT, errno.EACCES, errno.EPERM):
            return
        raise


def _enable_controllers(root: str) -> str | None:
    """Make sure *root* delegates what a worker cgroup needs; a reason if not."""
    enabled = _read_words(root, "cgroup.subtree_control")
    missing = [name for name in CGROUP_CONTROLLERS if name not in enabled]
    if not missing:
        return None
    available = _read_words(root, "cgroup.controllers")
    absent = [name for name in missing if name not in available]
    if absent:
        return (f"{root} has no {', '.join(absent)} controller to delegate "
                f"(it offers: {' '.join(available) or 'nothing'})")
    try:
        _write(root, "cgroup.subtree_control",
               " ".join(f"+{name}" for name in CGROUP_WANTED
                        if name in available))
    except OSError as exc:
        return f"cannot enable {', '.join(missing)} in {root}: {exc.strerror or exc}"
    still = [name for name in CGROUP_CONTROLLERS
             if name not in _read_words(root, "cgroup.subtree_control")]
    if still:
        return f"{root} still does not delegate {', '.join(still)}"
    return None


def _leave_internal_cgroup(own: str) -> tuple[str | None, bool]:
    """Move this process out of *own* into ``<own>/server``, if it is in it.

    ``(reason, moved)`` — *moved* says whether the server's own placement
    changed, so a later failure can name it. A cgroup that holds processes
    cannot enable ``subtree_control`` (the no-internal-process rule), and this
    is the only cgroup we are allowed to reorganise: it is ours, checked.

    Only **this** process moves. Other pids in the cgroup are somebody else's,
    and if they keep the no-internal-process rule unsatisfiable that is a
    refusal, not an invitation to migrate them.
    """
    if str(os.getpid()) not in _read_words(own, "cgroup.procs"):
        return None, False
    leaf = os.path.join(own, "server")
    try:
        os.makedirs(leaf, exist_ok=True)
        _write(leaf, "cgroup.procs", str(os.getpid()))
    except OSError as exc:
        return f"cannot move this process into {leaf}: {exc.strerror or exc}", False
    if str(os.getpid()) in _read_words(own, "cgroup.procs"):
        return (f"this process is still in {own} (the no-internal-process "
                f"rule)"), False
    return None, True


def _probe_mkdir(root: str) -> str | None:
    """Actually create and remove a child cgroup. `os.access` answers for the
    *uid*, and root passes it on a directory it still cannot write through a
    read-only mount — so the last step is the real one."""
    probe = os.path.join(root, f".agentcad-probe-{os.getpid()}")
    try:
        os.mkdir(probe)
    except OSError as exc:
        return f"cannot create a cgroup under {root}: {exc.strerror or exc}"
    try:
        os.rmdir(probe)
    except OSError:
        pass
    return None


def _own_cgroup_path() -> str | None:
    """This process's cgroup v2 path (``0::<path>``), or ``None``.

    Its own function so a test can put the process anywhere without faking
    ``/proc``.
    """
    try:
        with open("/proc/self/cgroup", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return None
    return next((line.split("::", 1)[1] for line in lines
                 if line.startswith("0::")), None)


def _read_words(directory: str, name: str) -> list[str]:
    """One whitespace-separated cgroup file, or ``[]`` if it cannot be read."""
    try:
        with open(os.path.join(directory, name), encoding="utf-8") as handle:
            return handle.read().split()
    except OSError:
        return []


@functools.lru_cache(maxsize=1)
def _page_size() -> int:
    """``/proc/<pid>/statm`` counts pages, not bytes. Cached: the supervisor
    asks on every sample."""
    try:
        return int(os.sysconf("SC_PAGE_SIZE"))
    except (AttributeError, ValueError, OSError):  # pragma: no cover - non-POSIX
        return 4096


class LinuxBackend:
    """The live half: what the client asks after the worker is running.

    Confinement needs nothing from here — the worker applied it to itself.
    What does live here is the *quota* half: the worker's cgroup (created at
    plan time, joined right after ``Popen``), the RSS sampler the supervisor
    calls, and the reading of the corpse that turns a bare ``kernel_crash``
    into a named one.
    """

    def __init__(self) -> None:
        self.warnings: list[str] = []
        #: The tier and this worker's directory in it, or ``None`` when the
        #: machine delegated nothing (the shipped container's normal state).
        self.cgroup: CgroupTier | None = None
        self.cg_dir: str | None = None
        self._oom0 = 0

    def place_in(self, cgroup: CgroupTier, quotas: Quotas) -> bool:
        """Create this worker's cgroup under *cgroup*. ``False`` (with a
        warning) if the directory or a limit could not be written — the caller
        then does not name the tier, because it did not get one."""
        name = f"worker-{os.getpid()}-{secrets.token_hex(4)}"
        try:
            self.cg_dir = cgroup.make_worker(name, quotas)
        except OSError as exc:
            self.cg_dir = None
            self.warnings.append(
                f"the cgroup quota tier is off: cannot set up "
                f"{os.path.join(cgroup.root, name)}: {exc.strerror or exc}")
            return False
        self.cgroup = cgroup
        return True

    def attach(self, proc) -> None:
        """Place the worker in its cgroup, and record the OOM counter it
        starts from (an OOM kill is a *delta*: the directory may be reused by
        a respawn, and a previous breach must not be attributed twice)."""
        if self.cgroup is None or self.cg_dir is None:
            return
        try:
            self.cgroup.attach(self.cg_dir, proc.pid)
        except OSError as exc:
            # The worker is running, just not in the cgroup. Say so, and stop
            # claiming the tier: an OOM counter that cannot rise must never be
            # read as "this worker did not breach".
            self.warnings.append(
                f"the cgroup quota tier is off: cannot place pid {proc.pid} "
                f"in {self.cg_dir}: {exc.strerror or exc}")
            self.cgroup = None
            return
        self._oom0 = self.cgroup.oom_kills(self.cg_dir)

    def can_sample(self) -> bool:
        """``/proc/<pid>/statm`` is always there on a Linux worker."""
        return True

    def rss_bytes(self, proc) -> int | None:
        """Resident size of *proc*, for one supervisor sample.

        Field 2 of ``/proc/<pid>/statm`` in pages. Measured at 47-65 us per
        open+read+parse in the spike (0.46 us with a kept-open fd, which is not
        worth the fd bookkeeping at one sample per 0.25 s). ``None`` means
        "could not measure" — the process is gone — never "zero".
        """
        pid = getattr(proc, "pid", None)
        if not pid or pid <= 0:
            return None
        try:
            with open(f"/proc/{pid}/statm", encoding="utf-8") as handle:
                fields = handle.read().split()
            return int(fields[1]) * _page_size()
        except (OSError, IndexError, ValueError):
            return None

    def explain_exit(self, proc, returncode: int | None) -> dict | None:
        """Why the worker died, when Linux can say.

        A kernel OOM kill, the supervisor's kill and a timeout kill all leave
        ``returncode == -9``; only the cgroup's ``oom_kill`` counter tells them
        apart, and it has to be read **before** the directory is released.
        """
        if self.cgroup is not None and self.cg_dir is not None:
            if self.cgroup.oom_kills(self.cg_dir) > self._oom0:
                return {"reason": "memory_cap", "tier": "cgroup"}
        if _SIGXCPU is not None and returncode == -_SIGXCPU:
            return {"reason": "cpu_cap", "tier": "rlimit"}
        return None

    def release(self) -> None:
        """Drop this worker's cgroup directory."""
        if self.cgroup is not None and self.cg_dir is not None:
            self.cgroup.release(self.cg_dir)
        self.cg_dir = None
