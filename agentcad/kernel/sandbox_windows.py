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

**The handle ``Popen`` hands back is not the interpreter.** A Windows venv's
``python.exe`` — uv-managed ones included — is a *launcher*: it starts the real
interpreter as a **child** and stays around as a thin stub. The child inherits
the job object (which is why the commit limit bites the interpreter and a
balloon still gets its ``MemoryError``), but ``GetProcessMemoryInfo`` on
``proc._handle`` measures the stub, and answered ~3.9 MB for a worker with
build123d imported (Windows CI, changelog 0238). So :meth:`WindowsBackend.rss_bytes`
samples **the job's own process list** and reports the **largest** working set
in it — the interpreter dominates, and a sum would double-count the pages the
two share. The ``Popen`` handle stays as the fallback for a worker with no job
(``attach`` failed) or a query Windows refused.

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
JobObjectBasicProcessIdList = 3
JobObjectExtendedLimitInformation = 9
JobObjectCpuRateControlInformation = 15

#: ``OpenProcess`` access masks. ``GetProcessMemoryInfo`` is documented as
#: wanting ``PROCESS_VM_READ`` too, so it is asked for first and dropped if the
#: open is refused: on every Windows since Vista the query alone is enough.
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_VM_READ = 0x0010

#: ``QueryInformationJobObject`` says the id list did not fit.
ERROR_MORE_DATA = 234

#: How many pids the first id-list buffer has room for. A worker's job holds
#: the launcher stub and the interpreter; 256 is slack, and a job that somehow
#: holds more says so through ``ERROR_MORE_DATA`` and is asked once more.
JOB_PID_CAPACITY = 256

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


def _process_id_list(capacity: int):
    """``JOBOBJECT_BASIC_PROCESS_ID_LIST`` with room for *capacity* pids.

    The real structure ends in a variable-length ``ULONG_PTR ProcessIdList[1]``,
    so the type has to be made per call site; ``ctypes`` gets the alignment
    (two ``DWORD``s, then a pointer-sized array) right on its own.
    """
    class JOBOBJECT_BASIC_PROCESS_ID_LIST(ctypes.Structure):
        _fields_ = [("NumberOfAssignedProcesses", ctypes.c_uint32),
                    ("NumberOfProcessIdsInList", ctypes.c_uint32),
                    ("ProcessIdList", ctypes.c_size_t * capacity)]

    return JOBOBJECT_BASIC_PROCESS_ID_LIST


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
          posture: str, server_pid: int | None, *, confine: bool = True,
          pool_size: int = 1):
    """Plan a Windows worker: ``(argv, env, confinement, quotas, backend)``.

    The argv comes back unchanged: there is nothing to wrap it in. The one
    environment addition is the payload that tells the worker a quota is in
    force — Windows has no rlimits for it to apply, but a job object's
    ``MemoryError`` is a *cap being enforced*, and `denials.classify` refuses
    to name a denial that no worker reported (Decision 9). It is emitted only
    when the job really exists, so an unconfined worker's ``MemoryError`` stays
    what it is: the machine running out of memory.

    *confine* is ignored, honestly: there is no confinement here to opt out of.
    *pool_size* likewise — Windows has no ``RLIMIT_NPROC`` for it to scale; the
    process cap is the job object's, and each worker gets its own job.
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
        #: Whether a process was actually assigned to :attr:`job`. Sampling the
        #: job's process list is only worth anything once something is in it,
        #: and this is what `rss_bytes` reads to decide (the handle `Popen`
        #: returned is a launcher stub, not the interpreter — see the module
        #: docstring).
        self.attached: bool = False

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
            return
        # Remembered, not re-derived: from here `rss_bytes` measures the job's
        # processes rather than the launcher stub `Popen` handed us.
        self.attached = True

    def can_sample(self) -> bool:
        """psapi is part of the OS; a failing call degrades per sample."""
        return True

    def rss_bytes(self, proc) -> int | None:
        """Working-set size, for one supervisor sample; ``None`` if psapi
        could not answer (the process is gone).

        The *job's* largest working set where there is a job with something in
        it, because a venv ``python.exe`` is a launcher and the ``Popen``
        handle is its stub (module docstring). The stub's own working set is
        the fallback — an under-report, but the only number available when the
        job is absent or Windows refused the query, and a supervisor that
        sampled ``None`` forever would enforce nothing at all.
        """
        sample = self._job_working_set()
        if sample is not None:
            return sample
        handle = getattr(proc, "_handle", None)
        if handle is None:
            return None
        counters = _memory_counters(int(handle))
        return None if counters is None else int(counters.WorkingSetSize)

    def _job_working_set(self) -> int | None:
        """The largest working set among the job's processes, or ``None``.

        The **max**, never the sum: the launcher and the interpreter share
        their mapped pages, so adding them double-counts, and it is the
        interpreter — the one with build123d in it — that the memory cap is
        about. Every failure here answers ``None`` so the caller can fall back;
        a sampler must not raise into the supervisor's loop.
        """
        if self.job is None or not self.attached:
            return None
        pids = _job_process_ids(self.job)
        if not pids:
            return None
        largest: int | None = None
        for pid in pids:
            handle = _open_process(pid)
            if handle is None:
                continue                  # exited between the query and now
            try:
                counters = _memory_counters(handle)
            finally:
                _close_handle(handle)
            if counters is None:
                continue
            size = int(counters.WorkingSetSize)
            if largest is None or size > largest:
                largest = size
        return largest

    def explain_exit(self, proc, returncode: int | None) -> dict | None:
        """Nothing to read: a job-object memory breach is an allocation failure
        *inside* the script (a ``MemoryError``, classified by
        ``denials.classify``), not a kill, so there is no corpse to interpret.
        """
        return None

    def release(self) -> None:
        """Close the job handle, which kills anything still inside it."""
        job, self.job = self.job, None
        self.attached = False
        if job is not None:
            _close_handle(job)


# ------------------------------------------------------------- the Win32 calls
#
# One function per entry point, module-level, so a test on another OS can
# stub the boundary instead of the behaviour. The plan-time ones raise
# `OSError` with the Win32 error attached (a tier that cannot be installed must
# not be named); the sampling ones — `_job_process_ids`, `_open_process`,
# `_memory_counters` — answer empty instead, because a refused query is a
# sample that falls back, not a failed build. Nothing above this line touches
# `ctypes.WinDLL`.

#: Loaded ``WinDLL``s, by name. Cached because the sampling path runs several
#: times a second per worker and every ``WinDLL(...)`` is a fresh
#: ``LoadLibraryW`` whose module reference ctypes never drops.
_LIBRARIES: dict = {}


def _library(name: str):
    library = _LIBRARIES.get(name)
    if library is None:
        # A race here costs one extra load and nothing else.
        library = _LIBRARIES[name] = ctypes.WinDLL(name, use_last_error=True)
    return library


def _kernel32():
    return _library("kernel32")


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


def _close_handle(handle: int) -> None:
    try:
        _kernel32().CloseHandle(ctypes.c_void_p(handle))
    except OSError:                       # pragma: no cover - defensive
        pass


def _job_process_ids(job: int) -> list[int]:
    """The pids currently assigned to *job*; ``[]`` when it cannot be asked.

    Never raises — this is on the supervisor's sampling path, and a query that
    Windows refused is a sample that falls back, not a failed build.
    """
    try:
        kernel32 = _kernel32()
    except (OSError, AttributeError):     # pragma: no cover - non-Windows
        return []
    capacity = JOB_PID_CAPACITY
    for _ in range(2):                    # one grow, then give up
        buffer = _process_id_list(capacity)()
        returned = ctypes.c_uint32(0)
        ok = kernel32.QueryInformationJobObject(
            ctypes.c_void_p(job), JobObjectBasicProcessIdList,
            ctypes.byref(buffer), ctypes.sizeof(buffer), ctypes.byref(returned))
        if ok:
            count = min(int(buffer.NumberOfProcessIdsInList), capacity)
            return [int(buffer.ProcessIdList[index]) for index in range(count)]
        if ctypes.get_last_error() != ERROR_MORE_DATA:
            return []
        # ERROR_MORE_DATA still fills in the assigned count; grow to it once.
        assigned = int(buffer.NumberOfAssignedProcesses)
        if assigned <= capacity:
            return []
        capacity = assigned
    return []                             # pragma: no cover - defensive


def _open_process(pid: int) -> int | None:
    """A query handle for *pid*, or ``None`` if it cannot be opened.

    ``PROCESS_VM_READ`` is asked for first because ``GetProcessMemoryInfo`` is
    documented as wanting it, and dropped on refusal because in practice the
    query right alone answers on every supported Windows. The caller closes it.
    """
    try:
        kernel32 = _kernel32()
    except (OSError, AttributeError):     # pragma: no cover - non-Windows
        return None
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int,
                                     ctypes.c_uint32]
    for access in (PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ,
                   PROCESS_QUERY_LIMITED_INFORMATION):
        handle = kernel32.OpenProcess(access, 0, pid)
        if handle:
            return int(handle)
    return None


def _memory_counters(handle: int) -> PROCESS_MEMORY_COUNTERS | None:
    try:
        psapi = _library("psapi")
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
