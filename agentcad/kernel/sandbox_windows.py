"""Windows quotas: a job object per worker. Confinement is ``unsupported``.

The backend half of :mod:`agentcad.kernel.sandbox` for ``sys.platform ==
"win32"`` (design spec, Decision 7). Two halves, and they are asymmetric:

* **Quotas** are real. A job object caps committed memory
  (``JOB_OBJECT_LIMIT_PROCESS_MEMORY`` — an allocation over the limit *fails*,
  so a runaway script gets a ``MemoryError`` with a line number and the warm
  worker survives), the number of processes it may start
  (``JOB_OBJECT_LIMIT_ACTIVE_PROCESS``) and its CPU share
  (``JobObjectCpuRateControlInformation``, a hard cap that throttles). Closing
  the job handle kills whatever is still in it
  (``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``), which is how an orphaned worker's
  children go away with it.
* **Confinement** is not. AppContainer plus CPython plus OCCT is the
  least-trodden path in the PRD's own words, cannot be exercised on the dev
  box this was built on, and each attempt is a Windows-CI round trip — so it
  is carved out as PRD-006b and reported ``unsupported`` here, in health and
  in the docs. Saying `off` would suggest a switch; there is none.

The job is created and **configured in** :func:`build`, in the server process,
before anything is spawned: the process is only *assigned* to it after
``Popen``. That ordering is what lets ``quotas.mechanism`` name ``job_object``
honestly — a mechanism string is a promise, and at plan time the job either
exists with its limits written or the tier reports itself off (Decision 8).
The assignment race is benign and recorded: the worker does nothing but import
until its first request.

Every ``ctypes.WinDLL`` lookup is inside a function, so this module imports
cleanly on macOS and Linux — which is what lets the plan-shape test run on
every OS with these entry points stubbed. No ``OCP``/build123d here either.
"""

from __future__ import annotations

import ctypes
import json
import os

from ._preamble import ENV as CONFINE_ENV
from .quotas import Quotas, enforcement

# --------------------------------------------------------- the Win32 constants

#: ``JOBOBJECT_BASIC_LIMIT_INFORMATION.LimitFlags``.
JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x8
JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x100
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000

#: ``JOBOBJECTINFOCLASS``.
JobObjectExtendedLimitInformation = 9
JobObjectCpuRateControlInformation = 15

#: ``JOBOBJECT_CPU_RATE_CONTROL_INFORMATION.ControlFlags``.
JOB_OBJECT_CPU_RATE_CONTROL_ENABLE = 0x1
JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP = 0x4

#: ``CpuRate`` is in hundredths of a percent of **total** machine CPU, so a
#: `cpu_percent` of 400 (four cores' worth) on an 8-CPU box is 50% = 5000.
CPU_RATE_MAX = 10000


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", ctypes.c_uint32),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32)]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t)]


class JOBOBJECT_CPU_RATE_CONTROL_INFORMATION(ctypes.Structure):
    _fields_ = [("ControlFlags", ctypes.c_uint32),
                ("CpuRate", ctypes.c_uint32)]


class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [("cb", ctypes.c_uint32),
                ("PageFaultCount", ctypes.c_uint32),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t)]


# ----------------------------------------------------------------- the build

def build(argv: list[str], write_roots: list[str], quotas: Quotas,
          posture: str, server_pid: int | None, *, confine: bool = True):
    """Plan a Windows worker: ``(argv, env, confinement, quotas, backend)``.

    The argv comes back unchanged: there is nothing to wrap it in. The one
    environment addition is the payload that tells the worker a quota is in
    force — Windows has no rlimits for it to apply, but a job object's
    ``MemoryError`` is a *cap being enforced*, and `denials.classify` refuses
    to name a denial that no worker reported (Decision 9). It is emitted only
    when the job really exists, so an unconfined worker's ``MemoryError`` stays
    what it is: the machine running out of memory.

    *confine* is ignored, honestly: there is no confinement here to opt out of.
    """
    backend = WindowsBackend(quotas)
    if posture != "local":
        backend.warnings.append(
            f"Windows keeps the local read posture (requested {posture!r}): "
            f"the hosted read allow-list is Landlock, and so Linux-only")

    env: dict[str, str] = {}
    tiers: list[str] = []
    if backend.open_job():
        tiers.append("job_object")
        env[CONFINE_ENV] = json.dumps(
            {"posture": "local", "quotas": ["job_object"]}, sort_keys=True)
    if quotas.memory_mb > 0:
        tiers.append("supervisor")

    confinement = {
        "status": "unsupported", "mechanism": None,
        "detail": {"posture": "local",
                   "note": "AppContainer confinement is PRD-006b"}}
    return (list(argv), env, confinement, enforcement(quotas, tiers), backend)


class WindowsBackend:
    """The live half: the job object, the psapi sampler, the handle to close."""

    def __init__(self, quotas: Quotas) -> None:
        self.warnings: list[str] = []
        self.quotas = quotas
        #: The job handle, or ``None`` when it could not be created or
        #: configured — in which case the tier is not named at all.
        self.job: int | None = None

    def open_job(self) -> bool:
        """Create the job and write its limits. ``False``, with a warning and
        no exception, if Windows refused: a worker with no job object still
        runs, capped by the supervisor alone, and health says so."""
        job = None
        try:
            job = _job_create()
            _set_information(job, JobObjectExtendedLimitInformation,
                             self._limits())
            rate = self._cpu_rate()
            if rate is not None:
                _set_information(job, JobObjectCpuRateControlInformation, rate)
        except (OSError, AttributeError) as exc:
            # AttributeError as well as OSError: `ctypes.WinDLL` does not exist
            # off Windows, and a plan built on a platform whose backend cannot
            # load must degrade to the supervisor, not crash the server.
            if job is not None:
                _close_handle(job)
            self.warnings.append(
                f"the job-object quota tier is off: {exc}")
            return False
        self.job = job
        return True

    def _limits(self) -> JOBOBJECT_EXTENDED_LIMIT_INFORMATION:
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        # KILL_ON_JOB_CLOSE is unconditional: it is what makes `release()` take
        # the worker's own children with it, however the server exits.
        flags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if self.quotas.memory_mb > 0:
            flags |= JOB_OBJECT_LIMIT_PROCESS_MEMORY
            info.ProcessMemoryLimit = self.quotas.memory_mb * 1024 * 1024
        if self.quotas.pids > 0:
            flags |= JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            info.BasicLimitInformation.ActiveProcessLimit = self.quotas.pids
        info.BasicLimitInformation.LimitFlags = flags
        return info

    def _cpu_rate(self) -> JOBOBJECT_CPU_RATE_CONTROL_INFORMATION | None:
        if not self.quotas.cpu_percent:
            return None
        info = JOBOBJECT_CPU_RATE_CONTROL_INFORMATION()
        info.ControlFlags = (JOB_OBJECT_CPU_RATE_CONTROL_ENABLE
                             | JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP)
        # A share of the whole machine, not of one core, and never 0 (which
        # the API rejects) nor above 100%.
        share = self.quotas.cpu_percent * 100 // (os.cpu_count() or 1)
        info.CpuRate = max(1, min(CPU_RATE_MAX, share))
        return info

    def attach(self, proc) -> None:
        """Assign the worker to the job, right after ``Popen``."""
        handle = getattr(proc, "_handle", None)
        if self.job is None or handle is None:
            return
        try:
            _assign(self.job, int(handle))
        except (OSError, AttributeError) as exc:
            self.warnings.append(
                f"the job-object quota tier is off: cannot assign pid "
                f"{getattr(proc, 'pid', '?')} to the job: {exc}")
            _close_handle(self.job)
            self.job = None

    def can_sample(self) -> bool:
        """psapi is part of the OS; a failing call degrades per sample."""
        return True

    def rss_bytes(self, proc) -> int | None:
        """Working-set size, for one supervisor sample; ``None`` if psapi
        could not answer (the process is gone)."""
        handle = getattr(proc, "_handle", None)
        if handle is None:
            return None
        counters = _memory_counters(int(handle))
        return None if counters is None else int(counters.WorkingSetSize)

    def explain_exit(self, proc, returncode: int | None) -> dict | None:
        """Nothing to read: a job-object memory breach is an allocation failure
        *inside* the script (a ``MemoryError``, classified by
        ``denials.classify``), not a kill, so there is no corpse to interpret.
        """
        return None

    def release(self) -> None:
        """Close the job handle, which kills anything still inside it."""
        job, self.job = self.job, None
        if job is not None:
            _close_handle(job)


# ------------------------------------------------------------- the Win32 calls
#
# One function per entry point, module-level, so a test on another OS can
# stub the boundary instead of the behaviour. Each raises `OSError` with the
# Win32 error attached; nothing above this line touches `ctypes.WinDLL`.

def _kernel32():
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _job_create() -> int:
    kernel32 = _kernel32()
    kernel32.CreateJobObjectW.restype = ctypes.c_void_p
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    return int(job)


def _set_information(job: int, klass: int, info: ctypes.Structure) -> None:
    kernel32 = _kernel32()
    ok = kernel32.SetInformationJobObject(
        ctypes.c_void_p(job), klass, ctypes.byref(info), ctypes.sizeof(info))
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())


def _assign(job: int, handle: int) -> None:
    kernel32 = _kernel32()
    ok = kernel32.AssignProcessToJobObject(ctypes.c_void_p(job),
                                           ctypes.c_void_p(handle))
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())


def _close_handle(job: int) -> None:
    try:
        _kernel32().CloseHandle(ctypes.c_void_p(job))
    except OSError:                       # pragma: no cover - defensive
        pass


def _memory_counters(handle: int) -> PROCESS_MEMORY_COUNTERS | None:
    try:
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
    except (OSError, AttributeError):     # pragma: no cover - non-Windows
        return None
    counters = PROCESS_MEMORY_COUNTERS()
    counters.cb = ctypes.sizeof(counters)
    ok = psapi.GetProcessMemoryInfo(ctypes.c_void_p(handle),
                                    ctypes.byref(counters),
                                    ctypes.sizeof(counters))
    if not ok:
        return None
    return counters
