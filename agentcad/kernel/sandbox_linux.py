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

Deliberately importable from server code and from any OS (every syscall is
lazy, and :func:`landlock_abi` answers ``0`` off Linux): no ``OCP``/build123d.
"""

from __future__ import annotations

import json
import os
import platform
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

    tiers: list[str] = []
    if payload["rlimits"]:
        tiers.append("rlimit")
    if quotas.memory_mb > 0:
        # The parent-side sampler is Slice 3; naming it here would claim a cap
        # nothing is watching, so it is added only once it exists.
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
    """How many processes this uid is running right now.

    ``RLIMIT_NPROC`` is measured against everything else the user already has
    (an IDE, a browser, the server itself), so the budget has to be measured
    rather than assumed — hence a ``/proc`` walk rather than a constant.
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
            with open(f"/proc/{name}/status", encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith("Uid:"):
                        if int(line.split()[1]) == uid:
                            count += 1
                        break
        except (OSError, IndexError, ValueError):
            continue  # the process exited between listdir and open
    return count or _FALLBACK_UID_PROCESSES


class LinuxBackend:
    """The live half: what the client asks after the worker is running.

    Confinement needs nothing from here — the worker applied it to itself. The
    cgroup tier, the ``/proc`` RSS sampler and the OOM-kill attribution are
    Slice 3; until they exist this answers the protocol honestly rather than
    guessing, so the client and the supervisor need no platform branch.
    """

    def __init__(self) -> None:
        self.warnings: list[str] = []

    def attach(self, proc) -> None:
        """Nothing to place yet (Slice 3 writes ``proc.pid`` into a delegated
        cgroup here — the parent's job, never a ``preexec_fn``)."""

    def rss_bytes(self, proc) -> int | None:
        return None

    def explain_exit(self, proc, returncode: int | None) -> dict | None:
        return None

    def release(self) -> None:
        """No cgroup directory to remove yet."""
