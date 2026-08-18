"""macOS confinement and quotas: the seatbelt profile and the rlimit tier.

The backend half of :mod:`agentcad.kernel.sandbox` for ``sys.platform ==
"darwin"``. Two jobs:

* **Confinement** — wrap the worker's argv in ``/usr/bin/sandbox-exec -p
  <profile>``. The profile is the v3 one, moved here unchanged: deny by
  default, global read (the ``local`` posture, which is the only posture
  macOS has), writes only inside the roots the client granted, no network.
* **Quotas** — macOS can enforce far less than Linux. ``RLIMIT_AS``/``DATA``/
  ``RSS`` are ``EINVAL`` on Darwin and ``RLIMIT_CPU`` is lifetime-cumulative
  (a script with a ``SIGXCPU`` handler ran 100 s past its hard limit in the
  spike), so the memory cap is the supervisor's and the only rlimit worth
  emitting is ``RLIMIT_NPROC``. It is per-*uid*, not per-process: a fixed 32
  killed the worker during ``import build123d``, so the payload is the live
  uid process count measured at spawn plus the configured headroom.

The rlimits travel to the child as ``AGENTCAD_CONFINE`` (JSON) and are applied
by the worker's own preamble — no ``preexec_fn`` anywhere, because CPython
documents it as unsafe in a threaded parent and the server is threaded. In
this slice nothing reads the variable yet; the preamble arrives with the
Linux backend.

Deliberately importable from server code and from any OS (the ``ctypes``
calls are lazy): no ``OCP``/build123d, and no macOS symbol touched at import.
"""

from __future__ import annotations

import ctypes
import functools
import json
import os
import signal
import struct
import subprocess

from .quotas import Quotas, enforcement

SANDBOX_EXEC = "/usr/bin/sandbox-exec"

#: ``libproc.h``: ``proc_listpids(PROC_UID_ONLY, uid, ...)`` lists the pids
#: whose effective uid matches — the number ``RLIMIT_NPROC`` is measured
#: against. (1 = ALL_PIDS, 2 = PGRP_ONLY, 3 = TTY_ONLY, 4 = UID_ONLY,
#: 5 = RUID_ONLY.)
PROC_UID_ONLY = 4

#: ``proc_pidinfo(pid, PROC_PIDTASKINFO, ...)`` fills a 96-byte
#: ``struct proc_taskinfo`` whose ``pti_resident_size`` is at offset 8.
PROC_PIDTASKINFO = 4

#: Enough for any developer machine or container; ``proc_listpids`` truncates
#: rather than failing, and a truncated count only under-reports the headroom.
_MAX_PIDS = 8192

#: Used only if both libproc and ``ps`` fail. Deliberately generous: this
#: number is a *floor* under the worker's own fork budget, and guessing low
#: would kill the worker at import for no security gain.
_FALLBACK_UID_PROCESSES = 512

_SIGXCPU = getattr(signal, "SIGXCPU", None)


def build_profile(writable_dirs: list[str]) -> str:
    """Seatbelt profile: deny by default, global read, write only where allowed.

    The allow set was validated on macOS by running the real worker (CPython
    + build123d/OCCT) through ping+build under profile variants with each
    allow removed in turn (and the full profile through build/export/assembly
    flows); per-line comments record which denials were observed and which
    lines are kept deliberately beyond the observed minimum.

    *writable_dirs* is granted **exactly**: since PRD-006 the shared system
    temp dir is no longer appended here. The caller (``sandbox.plan``) passes
    the project roots plus the worker's own private ``agentcad-worker-*``
    directory, which is the only temp root a worker gets — granting
    ``tempfile.gettempdir()`` wholesale let one worker's script write into a
    sibling's scratch (design spec, Decision 1).
    """
    roots: list[str] = []
    for d in writable_dirs:
        real = os.path.realpath(d)
        if real not in roots:
            roots.append(real)
    write_rules = "\n".join(
        f'(allow file-write* (subpath "{_escape(r)}"))' for r in roots
    )
    return f"""(version 1)
(deny default)
; required: sandbox-exec applies the profile then execvp()s the worker python
; (observed: removing it -> "execvp() ... failed: Operation not permitted").
(allow process-exec*)
; not observed as required for ping/build, but forked children inherit the
; sandbox, so allowing fork keeps scripts using multiprocessing/os.fork from
; dying in surprising ways without weakening confinement.
(allow process-fork)
; lets Python raise signals at itself (faulthandler, KeyboardInterrupt);
; target self only — the worker cannot signal other processes.
(allow signal (target self))
; required (dyld + interpreter + site-packages/OCP + /dev/urandom); global
; read is the accepted v1 posture — confinement here is about writes/network.
(allow file-read*)
; write access ONLY inside the configured project roots + the temp dir
; (mesh caches under <project>/.cache, exports/, tessellation scratch):
{write_rules}
; harmless device sink; kept so scripts writing to os.devnull don't trip a
; denial (not load-bearing for ping/build in experiments).
(allow file-write* (literal "/dev/null"))
; required: build123d's import chain calls os.uname()/sysctl (observed:
; removing it -> PermissionError: [Errno 1] in platform machine lookup).
(allow sysctl-read)
; already covered by (deny default); kept explicit as documentation of the
; second confinement goal.
(deny network*)
"""


def _escape(path: str) -> str:
    # Seatbelt string literals use double quotes; escape embedded quotes/backslashes.
    return path.replace("\\", "\\\\").replace('"', '\\"')


# ----------------------------------------------------------------- the build

def build(argv: list[str], write_roots: list[str], quotas: Quotas,
          posture: str, server_pid: int | None, *, confine: bool = True):
    """Plan a macOS worker: ``(argv, env, confinement, quotas, backend)``.

    *confine* is ``False`` when the operator opted out (``AGENTCAD_NO_SANDBOX``
    / ``{"sandbox": false}``): the argv is left unwrapped and confinement is
    reported ``off``, but the **quotas are still applied** — opting out of the
    sandbox is not opting out of the caps, and the rlimit payload is emitted
    either way.
    """
    backend = MacBackend()
    if posture != "local":
        # macOS has one posture. Saying so out loud beats reporting `hosted`
        # over a profile that grants global read (design spec, Decision 8).
        backend.warnings.append(
            f"macOS keeps the local read posture (requested {posture!r}): the "
            f"seatbelt profile grants global read, and the hosted allow-list "
            f"is Linux-only")

    env: dict[str, str] = {}
    tiers: list[str] = []
    rlimits: dict[str, list[int]] = {}
    if quotas.pids_headroom > 0:
        nproc = live_uid_process_count() + quotas.pids_headroom
        rlimits["RLIMIT_NPROC"] = [nproc, nproc]  # hard == soft: no raising it back
    if rlimits:
        env["AGENTCAD_CONFINE"] = json.dumps({"rlimits": rlimits}, sort_keys=True)
        tiers.append("rlimit")
    if quotas.memory_mb > 0:
        tiers.append("supervisor")

    if not confine:
        confinement = {"status": "off", "mechanism": None, "detail": {}}
    elif not has_seatbelt():
        confinement = {"status": "unsupported", "mechanism": None,
                       "detail": {"reason": f"{SANDBOX_EXEC} is not present"}}
    else:
        argv = [SANDBOX_EXEC, "-p", build_profile(write_roots), *argv]
        confinement = {"status": "active", "mechanism": "seatbelt",
                       "detail": {"posture": "local"}}
    return list(argv), env, confinement, enforcement(quotas, tiers), backend


def has_seatbelt() -> bool:
    """The seatbelt CLI is present. (It ships with macOS; a stripped image or
    a `sandbox-exec` removed by policy is the case this catches.)"""
    return os.path.isfile(SANDBOX_EXEC)


class MacBackend:
    """The live half: what the client asks after the worker is running."""

    def __init__(self) -> None:
        self.warnings: list[str] = []

    def attach(self, proc) -> None:
        """Nothing to place: macOS has no cgroup, and the rlimits are applied
        by the child itself. Kept so the client needs no platform branch."""

    def rss_bytes(self, proc) -> int | None:
        """Resident size of *proc*, for the supervisor's sample.

        ``proc_pidinfo(PROC_PIDTASKINFO)`` measured at 1.26 us in the spike —
        cheap enough for the 0.25 s sampling loop. ``None`` means "could not
        measure" (the process is gone, or libproc refused), never "zero".
        """
        pid = getattr(proc, "pid", None)
        libproc = _libproc()
        if not pid or pid <= 0 or libproc is None:
            return None
        buf = ctypes.create_string_buffer(256)
        try:
            written = libproc.proc_pidinfo(pid, PROC_PIDTASKINFO, 0, buf,
                                           len(buf))
        except OSError:
            return None
        if written <= 0:
            return None
        return struct.unpack_from("<Q", buf.raw, 8)[0]

    def explain_exit(self, proc, returncode: int | None) -> dict | None:
        """Why the worker died, when macOS can say. ``None`` means "not mine"
        and the caller falls back to its own crash reporting."""
        if _SIGXCPU is not None and returncode == -_SIGXCPU:
            return {"reason": "cpu_cap", "tier": "rlimit"}
        return None

    def release(self) -> None:
        """No cgroup directory and no job object to drop."""


# ------------------------------------------------------------------- libproc

def live_uid_process_count() -> int:
    """How many processes this uid is running *right now*.

    ``RLIMIT_NPROC`` is a per-uid ceiling, so the worker's budget has to be
    measured against everything else the user is already running (an IDE, a
    browser, the server itself) rather than assumed. libproc first, ``ps`` if
    the ctypes call fails, and a generous constant if both do.
    """
    uid = os.getuid()
    libproc = _libproc()
    if libproc is not None:
        buf = (ctypes.c_int * _MAX_PIDS)()
        try:
            written = libproc.proc_listpids(PROC_UID_ONLY, uid, buf,
                                            ctypes.sizeof(buf))
        except OSError:
            written = 0
        if written > 0:
            return written // ctypes.sizeof(ctypes.c_int)
    try:
        done = subprocess.run(["ps", "-u", str(uid), "-o", "pid="],
                              capture_output=True, text=True, timeout=10,
                              check=False)
        lines = [line for line in done.stdout.splitlines() if line.strip()]
        if lines:
            return len(lines)
    except (OSError, subprocess.SubprocessError):
        pass
    return _FALLBACK_UID_PROCESSES


@functools.lru_cache(maxsize=1)
def _libproc():
    """libSystem, with the two libproc entry points typed. ``None`` if it
    cannot be loaded — every caller degrades rather than raising."""
    try:
        lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        lib.proc_listpids.restype = ctypes.c_int
        lib.proc_listpids.argtypes = [ctypes.c_uint32, ctypes.c_uint32,
                                      ctypes.c_void_p, ctypes.c_int]
        lib.proc_pidinfo.restype = ctypes.c_int
        lib.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int,
                                     ctypes.c_uint64, ctypes.c_void_p,
                                     ctypes.c_int]
        return lib
    except (OSError, AttributeError):
        return None
