"""Windows confinement and quotas: an AppContainer, and a job object per worker.

The backend half of :mod:`agentcad.kernel.sandbox` for ``sys.platform ==
"win32"``. Two halves, and they are applied by two different mechanisms:

* **Quotas** are a job object. It caps committed memory
  (``JOB_OBJECT_LIMIT_PROCESS_MEMORY`` — an allocation over the limit *fails*,
  so a runaway script gets a ``MemoryError`` with a line number and the warm
  worker survives), the number of processes it may start
  (``JOB_OBJECT_LIMIT_ACTIVE_PROCESS``) and its CPU share
  (``JobObjectCpuRateControlInformation``, a hard cap that throttles). Closing
  the job handle kills whatever is still in it
  (``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``), which is how an orphaned worker's
  children go away with it.
* **Confinement** is an AppContainer (PRD-006b, design spec Decisions 1-4).
  The worker runs under a **lowbox token** built from a per-installation
  package SID (``CreateAppContainerProfile``, re-derived on later runs) with
  **no capabilities at all** — the absence of ``INTERNET_CLIENT`` *is* the
  network denial (``WSAEACCES``/``[WinError 10013]``) — and the only paths it
  can reach are the ones carrying an ACE for that SID: the plan's write roots
  and private temp dir at ``M``, the interpreter, the venv and the app tree at
  ``RX`` (:func:`acl_grant`, ``icacls``). Everything else on the machine —
  the user's home, another worker's scratch, ``C:\\Users\\Public`` — answers
  ``[Errno 13]``.

  CPython's ``subprocess`` cannot pass
  ``PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES``, so a confined worker is
  started by :class:`ConfinedProcess` — ``CreateProcessW`` +
  ``STARTUPINFOEX`` through ctypes, with exactly the ``Popen`` surface the
  client uses — which the client reaches through :meth:`WindowsBackend.spawn`.
  It starts **suspended**, joins the job object, and only then resumes, so
  there is no window in which the worker runs unquotaed (Decision 4).

  Honesty (Decision 3/8): this module only ever *intends* the confinement. The
  worker reads ``TokenIsAppContainer`` off **its own token** in
  ``_preamble.apply_from_env`` and reports it back on ``ping``; a plan whose
  worker answers anything else is downgraded to ``off`` by
  ``client.confinement_holds`` and ``sandbox.report``. A profile, an ACL or a
  spawn that failed is ``off`` **here**, with the step and the path in
  ``warnings`` — never ``active`` by intent — and a Windows too old for
  ``userenv!CreateAppContainerProfile`` (or a machine with no ``icacls``) is
  ``unsupported``.

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
import hashlib
import json
import os
import shutil
import subprocess
import sys

from .._resources import resource_root
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


# ------------------------------------------------- the AppContainer constants

#: ``UpdateProcThreadAttribute`` attributes. The first is the whole reason
#: this module spawns its own processes: ``subprocess.Popen`` can pass a
#: handle list and nothing else, so there is no way to ask CPython for a
#: lowbox token (design spec, Decision 1).
PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES = 0x00020009
PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002

#: ``CreateProcessW`` flags.
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
CREATE_SUSPENDED = 0x00000004
CREATE_NO_WINDOW = 0x08000000
CREATE_UNICODE_ENVIRONMENT = 0x00000400

STARTF_USESTDHANDLES = 0x00000100
HANDLE_FLAG_INHERIT = 0x00000001

#: ``GetExitCodeProcess`` says the process is still running, and
#: ``WaitForSingleObject`` says the wait expired.
STILL_ACTIVE = 259
WAIT_TIMEOUT = 0x00000102
INFINITE = 0xFFFFFFFF

#: ``HRESULT_FROM_WIN32(ERROR_ALREADY_EXISTS)``: the profile outlives the
#: process that made it (and reboots), so this is the *normal* answer from the
#: second run onwards and means "derive the SID instead" (Decision 2).
HRESULT_ALREADY_EXISTS = 0x800700B7

#: One profile per installation, named from the app tree so two checkouts do
#: not share a SID and a machine does not accumulate one per worker.
#: AppContainer names are <= 64 chars of ``[A-Za-z0-9._-]``.
PROFILE_PREFIX = "agentcad-worker-"
PROFILE_DISPLAY = "AgentCAD kernel worker"
PROFILE_DESCRIPTION = "Confinement for AgentCAD part-script execution"

#: ``icacls`` rights. ``(OI)(CI)`` makes the ACE inheritable, which is what
#: covers a directory's **existing** children as well as its future ones —
#: measured on windows-latest before this code relied on it (probe round 2,
#: `acl.propagation=yes`), so no ``/T`` tree walk is needed.
READ_RIGHTS = "(OI)(CI)RX"
WRITE_RIGHTS = "(OI)(CI)M"          # create/write/delete, but not WRITE_DAC

#: A grant is ~50-100 ms; a plan runs <= 8 of them. The timeout is only there
#: so a wedged `icacls` cannot hang a server thread forever.
ICACLS_TIMEOUT_S = 180.0

#: The facets the parent declares in the worker's payload once it really did
#: spawn through the AppContainer path — the macOS precedent, and what lets
#: `denials.classify` name a filesystem or network denial the worker itself
#: cannot observe (`_preamble` copies it through verbatim).
CONFINEMENT_FACETS = ["filesystem", "network"]

#: The package-profile tree a lowbox token expects under ``%LOCALAPPDATA%``.
#: Load-bearing, and not obvious (probe round 1 died on it): the token rewrites
#: the child's ``TEMP``/``TMP`` to ``%LOCALAPPDATA%\\Packages\\<name>\\AC\\Temp``,
#: the plan points ``LOCALAPPDATA`` at the private temp dir, and nothing else
#: creates that path — so ``tempfile.gettempdir()`` raised ``FileNotFoundError``
#: in the first child that touched it. :func:`make_package_tree` makes it.
PACKAGE_SUBDIRS = ("AC/Temp", "AC/INetCache", "AC/INetCookies",
                   "AC/INetHistory", "LocalState", "TempState",
                   "RoamingState", "Settings")


class SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("nLength", ctypes.c_uint32),
                ("lpSecurityDescriptor", ctypes.c_void_p),
                ("bInheritHandle", ctypes.c_int)]


class SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p),
                ("Attributes", ctypes.c_uint32)]


class SECURITY_CAPABILITIES(ctypes.Structure):
    _fields_ = [("AppContainerSid", ctypes.c_void_p),
                ("Capabilities", ctypes.POINTER(SID_AND_ATTRIBUTES)),
                ("CapabilityCount", ctypes.c_uint32),
                ("Reserved", ctypes.c_uint32)]


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [("cb", ctypes.c_uint32),
                ("lpReserved", ctypes.c_wchar_p),
                ("lpDesktop", ctypes.c_wchar_p),
                ("lpTitle", ctypes.c_wchar_p),
                ("dwX", ctypes.c_uint32),
                ("dwY", ctypes.c_uint32),
                ("dwXSize", ctypes.c_uint32),
                ("dwYSize", ctypes.c_uint32),
                ("dwXCountChars", ctypes.c_uint32),
                ("dwYCountChars", ctypes.c_uint32),
                ("dwFillAttribute", ctypes.c_uint32),
                ("dwFlags", ctypes.c_uint32),
                ("wShowWindow", ctypes.c_uint16),
                ("cbReserved2", ctypes.c_uint16),
                ("lpReserved2", ctypes.c_void_p),
                ("hStdInput", ctypes.c_void_p),
                ("hStdOutput", ctypes.c_void_p),
                ("hStdError", ctypes.c_void_p)]


class STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [("StartupInfo", STARTUPINFOW),
                ("lpAttributeList", ctypes.c_void_p)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", ctypes.c_void_p),
                ("hThread", ctypes.c_void_p),
                ("dwProcessId", ctypes.c_uint32),
                ("dwThreadId", ctypes.c_uint32)]


# ------------------------------------------------- the profile, the SID, ACLs

def profile_name() -> str:
    """The AppContainer name for **this installation**.

    Derived from :func:`~agentcad._resources.resource_root` so two checkouts
    (or a frozen bundle beside a source tree) never share a package SID, and so
    a machine accumulates one profile per install rather than one per worker.
    The profile is deliberately never deleted on the worker path: creating one
    is not free, concurrent clients share it, and removing it is a documented
    PowerShell one-liner (Decision 2).
    """
    digest = hashlib.sha256(str(resource_root()).encode("utf-8")).hexdigest()
    return f"{PROFILE_PREFIX}{digest[:12]}"


class AppContainerProfile:
    """A package SID: the name, the ``PSID`` and its string form.

    :attr:`sid` is a raw pointer into memory Windows allocated for us and it is
    held for the life of the process on purpose — every spawn passes it in
    ``SECURITY_CAPABILITIES``, so freeing it after the first one would leave
    later spawns pointing at nothing. :attr:`sid_str` is what the ACLs, the
    payload and health carry, because a pointer means nothing to a reader.
    """

    def __init__(self, name: str, sid: int, sid_str: str) -> None:
        self.name = name
        self.sid = int(sid)
        self.sid_str = str(sid_str)

    @classmethod
    def ensure(cls, name: str) -> "AppContainerProfile":
        """Create the profile, or derive its SID when it already exists.

        Raises ``OSError`` with the HRESULT: a confinement that could not be
        prepared must not be claimed, and the caller turns this into ``off``
        plus a warning naming the step.
        """
        hr, sid = _userenv_create_profile(name, PROFILE_DISPLAY,
                                          PROFILE_DESCRIPTION)
        origin = "CreateAppContainerProfile"
        if (hr & 0xFFFFFFFF) == HRESULT_ALREADY_EXISTS:
            origin = "DeriveAppContainerSidFromAppContainerName"
            hr, sid = _userenv_derive_sid(name)
        if hr != 0 or not sid:
            raise OSError(f"{origin}({name!r}) failed: "
                          f"HRESULT 0x{hr & 0xFFFFFFFF:08X}")
        return cls(name, sid, _sid_to_string(sid))


def acl_grant(path: str, sid_str: str, rights: str) -> tuple[bool, str]:
    """``icacls <path> /grant *<SID>:<rights>``; ``(ok, output tail)``.

    ``icacls`` rather than a hand-built DACL, and no ``/T``: setting an
    inheritable ACE through ``SetNamedSecurityInfo`` (what ``icacls`` calls)
    makes Windows propagate it to the existing children whose DACLs have
    inheritance enabled, which is the default — so a pre-existing
    ``project/.cache/`` is covered without a tree walk. That is measured, not
    assumed (probe round 2).

    Never raises: the tail is what the caller puts in the warning, and a step
    that could not run is a confinement that is ``off``, not a server that
    fails to start.
    """
    icacls = _icacls()
    if icacls is None:
        return False, "icacls is not on PATH"
    try:
        completed = subprocess.run(
            [icacls, str(path), "/grant", f"*{sid_str}:{rights}"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=ICACLS_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    output = ((completed.stdout or "") + " " + (completed.stderr or "")).strip()
    tail = " ".join(output.split())[-200:]
    return completed.returncode == 0, tail


def make_package_tree(tmp_dir: str, name: str) -> str:
    """Create ``<tmp_dir>\\Packages\\<name>\\...`` and return the package dir.

    See :data:`PACKAGE_SUBDIRS`: the lowbox token redirects the worker's
    ``%TEMP%`` into this tree, and it has to exist **before** the worker
    starts or the first ``tempfile`` call in the container raises.
    """
    package = os.path.join(tmp_dir, "Packages", name)
    for relative in PACKAGE_SUBDIRS:
        os.makedirs(os.path.join(package, *relative.split("/")), exist_ok=True)
    return package


# ----------------------------------------------------------- ConfinedProcess

class ConfinedProcess:
    """A worker inside the AppContainer, with the ``Popen`` surface.

    Exactly the members ``KernelClient`` and the supervisor touch:
    ``stdin``/``stdout``/``stderr`` (text, UTF-8, line-buffered), ``pid``,
    ``_handle``, ``poll()``, ``wait(timeout=None)`` (raising
    ``subprocess.TimeoutExpired``, which is what the client catches),
    ``kill()`` and ``returncode`` — plus ``job_assigned``, which is how
    :meth:`WindowsBackend.attach` knows the job already has it.

    Three pipes with only the child ends inheritable, a two-entry
    ``STARTUPINFOEX`` attribute list (``SECURITY_CAPABILITIES`` for the lowbox
    token, ``HANDLE_LIST`` so nothing but those three handles crosses over), a
    **suspended** start, ``AssignProcessToJobObject``, then ``ResumeThread`` —
    so the worker's first instruction already runs under both the token and
    the quota (Decision 4).
    """

    def __init__(self, argv: list[str], env: dict[str, str] | None, *,
                 sid: int, job: int | None = None,
                 cwd: str | None = None) -> None:
        import msvcrt  # Windows-only; imported here so the module loads anywhere

        kernel32 = _kernel32()
        self.args = list(argv)
        self.pid: int | None = None
        self._handle: int | None = None
        self.returncode: int | None = None
        self.job_assigned = False
        self._attr_list: int | None = None

        # stdin: the child READS it, so the read end is the inheritable one.
        stdin_r, stdin_w = _pipe(child_end="read")
        stdout_r, stdout_w = _pipe(child_end="write")
        stderr_r, stderr_w = _pipe(child_end="write")
        child_handles = (stdin_r, stdout_w, stderr_w)

        # Attributes, not locals: `UpdateProcThreadAttribute` stores POINTERS
        # into the attribute list and does not copy, so every one of these has
        # to outlive the `CreateProcessW` call.
        self._caps = SECURITY_CAPABILITIES()
        # The raw address: a `c_void_p` *field* takes an int or None, and
        # handing it a `c_void_p` instance is a TypeError.
        self._caps.AppContainerSid = int(sid)
        # No capabilities at all. The absence of `INTERNET_CLIENT` IS the
        # network denial (Decision 2) — there is nothing to add here.
        self._caps.Capabilities = None
        self._caps.CapabilityCount = 0
        self._caps.Reserved = 0
        self._handle_array = (ctypes.c_void_p * 3)(*child_handles)

        try:
            self._attr_buffer, self._attr_list = self._attribute_list()
            siex = STARTUPINFOEXW()
            siex.StartupInfo.cb = ctypes.sizeof(STARTUPINFOEXW)
            siex.StartupInfo.dwFlags = STARTF_USESTDHANDLES
            siex.StartupInfo.hStdInput = stdin_r
            siex.StartupInfo.hStdOutput = stdout_w
            siex.StartupInfo.hStdError = stderr_w
            siex.lpAttributeList = self._attr_list

            cmdline = ctypes.create_unicode_buffer(
                subprocess.list2cmdline(argv))
            env_buffer = _environment_block(env) if env is not None else None
            info = PROCESS_INFORMATION()
            # Explicit argtypes throughout, and POINTER(...) rather than
            # c_void_p for the two out-structs: `byref()` into a `c_void_p`
            # slot is the kind of conversion that works until it does not.
            kernel32.CreateProcessW.argtypes = [
                ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_void_p,
                ctypes.c_void_p, ctypes.c_int, ctypes.c_uint32,
                ctypes.c_void_p, ctypes.c_wchar_p,
                ctypes.POINTER(STARTUPINFOEXW),
                ctypes.POINTER(PROCESS_INFORMATION)]
            flags = (EXTENDED_STARTUPINFO_PRESENT | CREATE_SUSPENDED
                     | CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT)
            ok = kernel32.CreateProcessW(
                None, ctypes.cast(cmdline, ctypes.c_void_p), None, None, True,
                flags,
                ctypes.cast(env_buffer, ctypes.c_void_p) if env_buffer else None,
                cwd, ctypes.byref(siex), ctypes.byref(info))
            if not ok:
                raise _win_error("CreateProcessW")
        except BaseException:
            for handle in (stdin_r, stdin_w, stdout_r, stdout_w,
                           stderr_r, stderr_w):
                _close_handle(handle)
            self._free_attribute_list()
            raise

        self._handle = int(info.hProcess)
        self.pid = int(info.dwProcessId)
        # The attribute list only has to stay valid until `CreateProcessW`
        # returns; from here it is one more thing that could leak.
        self._free_attribute_list()
        # The child ends belong to the child now: a parent that keeps them
        # never sees EOF on stdout.
        for handle in child_handles:
            _close_handle(handle)

        if job is not None:
            try:
                _assign(job, self._handle)
            except OSError:
                # Never resume a process the job refused: it would run
                # unquotaed, and a worker outside its cap is not a worker.
                self.kill()
                _close_handle(int(info.hThread))
                for handle in (stdin_w, stdout_r, stderr_r):
                    _close_handle(handle)
                self.close()
                raise
            self.job_assigned = True

        if kernel32.ResumeThread(ctypes.c_void_p(int(info.hThread))) == -1:
            error = _win_error("ResumeThread")
            self.kill()
            _close_handle(int(info.hThread))
            for handle in (stdin_w, stdout_r, stderr_r):
                _close_handle(handle)
            self.close()
            raise error
        _close_handle(int(info.hThread))

        # `Popen(text=True, encoding="utf-8", bufsize=1)`, by hand. The
        # worker's stderr is whatever the runner's stdio encoding is, and a
        # mojibake traceback tail is worth more than a UnicodeDecodeError in
        # the client's reader thread — hence `errors="replace"`.
        self.stdin = open(msvcrt.open_osfhandle(stdin_w, 0), "w",
                          encoding="utf-8", errors="replace", buffering=1,
                          newline="\n")
        self.stdout = open(msvcrt.open_osfhandle(stdout_r, os.O_RDONLY), "r",
                           encoding="utf-8", errors="replace", buffering=1)
        self.stderr = open(msvcrt.open_osfhandle(stderr_r, os.O_RDONLY), "r",
                           encoding="utf-8", errors="replace", buffering=1)

    # -- construction helpers

    def _attribute_list(self):
        kernel32 = _kernel32()
        size = ctypes.c_size_t(0)
        kernel32.InitializeProcThreadAttributeList.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_size_t)]
        # The documented two-call form: the first call ALWAYS returns FALSE
        # with ERROR_INSUFFICIENT_BUFFER and fills in the size.
        kernel32.InitializeProcThreadAttributeList(None, 2, 0,
                                                   ctypes.byref(size))
        if size.value == 0:
            raise _win_error("InitializeProcThreadAttributeList (size)")
        buffer = (ctypes.c_char * size.value)()
        # A plain address carried as an int: it is what a `c_void_p` argtype
        # and the `STARTUPINFOEXW.lpAttributeList` field both accept
        # unambiguously. `buffer` is kept by the caller so it outlives the call.
        attr_list = ctypes.addressof(buffer)
        if not kernel32.InitializeProcThreadAttributeList(attr_list, 2, 0,
                                                          ctypes.byref(size)):
            raise _win_error("InitializeProcThreadAttributeList")
        kernel32.UpdateProcThreadAttribute.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_size_t,
            ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_size_t)]
        if not kernel32.UpdateProcThreadAttribute(
                attr_list, 0, PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
                ctypes.addressof(self._caps), ctypes.sizeof(self._caps),
                None, None):
            raise _win_error("UpdateProcThreadAttribute(SECURITY_CAPABILITIES)")
        if not kernel32.UpdateProcThreadAttribute(
                attr_list, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                ctypes.addressof(self._handle_array),
                ctypes.sizeof(self._handle_array), None, None):
            raise _win_error("UpdateProcThreadAttribute(HANDLE_LIST)")
        return buffer, attr_list

    def _free_attribute_list(self) -> None:
        attr_list, self._attr_list = getattr(self, "_attr_list", None), None
        if attr_list is None:
            return
        try:
            kernel32 = _kernel32()
            # Explicit argtypes: without them ctypes marshals a Python int as
            # a 32-bit C int and truncates a 64-bit address.
            kernel32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
            kernel32.DeleteProcThreadAttributeList(attr_list)
        except (OSError, AttributeError):      # pragma: no cover - defensive
            pass

    # -- the Popen surface

    def poll(self) -> int | None:
        if self._handle is None:
            return self.returncode
        code = ctypes.c_uint32(0)
        if not _kernel32().GetExitCodeProcess(ctypes.c_void_p(self._handle),
                                              ctypes.byref(code)):
            return self.returncode
        if int(code.value) == STILL_ACTIVE:
            return None
        self.returncode = int(code.value)
        return self.returncode

    def wait(self, timeout: float | None = None) -> int | None:
        """Like ``Popen.wait``: ``subprocess.TimeoutExpired`` on expiry.

        The exception type matters — ``client._kill`` and
        ``client._explain_exit`` both catch exactly that one, and anything
        else would escape into a request.
        """
        if self._handle is None:
            return self.returncode
        milliseconds = INFINITE if timeout is None else int(timeout * 1000)
        result = _kernel32().WaitForSingleObject(
            ctypes.c_void_p(self._handle), ctypes.c_uint32(milliseconds))
        if result == WAIT_TIMEOUT:
            raise subprocess.TimeoutExpired(self.args, timeout or 0)
        return self.poll()

    def kill(self) -> None:
        if self._handle is None or self.poll() is not None:
            return
        try:
            _kernel32().TerminateProcess(ctypes.c_void_p(self._handle), 9)
        except (OSError, AttributeError):      # pragma: no cover - defensive
            pass

    def close(self) -> None:
        """Drop the process handle and the pipe wrappers. Idempotent."""
        for name in ("stdin", "stdout", "stderr"):
            stream = getattr(self, name, None)
            if stream is not None:
                try:
                    stream.close()
                except OSError:                # pragma: no cover - defensive
                    pass
        self._free_attribute_list()
        handle, self._handle = self._handle, None
        if handle is not None:
            _close_handle(handle)

    def __del__(self) -> None:                 # pragma: no cover - GC timing
        # The client never closes a process object explicitly (`Popen` does
        # not need it), so a respawn would leak one process handle per worker.
        try:
            self.close()
        except BaseException:
            pass


def _environment_block(env: dict[str, str]):
    """``KEY=VALUE\\0...\\0\\0`` in UTF-16.

    Sorted case-insensitively because Windows requires a sorted block, and
    UTF-16 because the spawn passes ``CREATE_UNICODE_ENVIRONMENT``.
    """
    items = sorted(((str(key), str(value)) for key, value in env.items() if key),
                   key=lambda pair: pair[0].upper())
    block = "".join(f"{key}={value}\0" for key, value in items) + "\0"
    return ctypes.create_unicode_buffer(block)


def supported() -> bool:
    """Whether an AppContainer can be planned on this machine at all.

    ``userenv!CreateAppContainerProfile`` is Windows 8+, and ``icacls`` is what
    grants the SID its paths. Either missing is ``unsupported`` — a folder
    status, not ``off``: there is no switch for the operator to look for.
    """
    return _userenv_symbol("CreateAppContainerProfile") and _icacls() is not None


# ----------------------------------------------------------------- the build

def build(argv: list[str], write_roots: list[str], quotas: Quotas,
          posture: str, server_pid: int | None, *, confine: bool = True,
          pool_size: int = 1):
    """Plan a Windows worker: ``(argv, env, confinement, quotas, backend)``.

    The argv comes back unchanged — the confinement is not a wrapper here but
    a **token**, applied by :class:`ConfinedProcess` at spawn time, so what
    this function does is prepare the two things that must exist first: the
    package SID (:class:`AppContainerProfile`) and the ACEs that let it read
    the interpreter and write the plan's roots (:func:`acl_grant`).

    The environment addition is the ``AGENTCAD_CONFINE`` payload. It carries
    ``quotas`` when the job object really exists (Windows has no rlimits for
    the worker to apply, but a job object's ``MemoryError`` *is* a cap being
    enforced, and `denials.classify` never names a denial no worker reported)
    and ``confinement``/``appcontainer`` when the AppContainer path really was
    taken. With neither, there is no payload at all — an unconfined,
    unquotaed worker's ``MemoryError`` stays what it is: the machine running
    out of memory.

    Honesty (Decision 8): the ``active`` here is an **intent**. The worker
    reports ``appcontainer`` off its own token and the client downgrades this
    to ``off`` if it disagrees. A step that failed is ``off`` **now**, with the
    step and the path in ``backend.warnings``.

    *confine* ``False`` (``AGENTCAD_NO_SANDBOX``, ``"sandbox": false``) is
    ``off`` with the quotas untouched. *pool_size* is unused: Windows has no
    ``RLIMIT_NPROC`` to scale, the process cap is the job object's, and each
    worker gets its own job.
    """
    backend = WindowsBackend(quotas)
    if posture != "local":
        backend.warnings.append(
            f"Windows keeps the local read posture (requested {posture!r}): "
            f"the hosted read allow-list is Landlock, and so Linux-only")

    env: dict[str, str] = {}
    tiers: list[str] = []
    payload: dict = {"posture": "local"}
    if backend.open_job():
        tiers.append("job_object")
        payload["quotas"] = ["job_object"]
    if quotas.memory_mb > 0:
        tiers.append("supervisor")

    confinement = _confine_appcontainer(backend, write_roots, payload,
                                        confine)
    # Only when there is something to tell the worker: a payload of nothing but
    # the posture would make `_preamble` publish a report claiming a cap and a
    # confinement that are both absent.
    if len(payload) > 1:
        env[CONFINE_ENV] = json.dumps(payload, sort_keys=True)
    return (list(argv), env, confinement, enforcement(quotas, tiers), backend)


def _confine_appcontainer(backend: "WindowsBackend", write_roots: list[str],
                          payload: dict, confine: bool) -> dict:
    """Prepare the AppContainer and return the confinement report.

    Everything or nothing: the payload declares two facets to the worker, so a
    profile that could not be made or a root that could not be granted means
    the worker is spawned the ordinary way and this says ``off`` — with the
    step and the path in ``backend.warnings``, because "off" with no reason
    reads as a bug in the sandbox.
    """
    local = {"posture": "local"}
    if not supported():
        # Asked first, and before the opt-out, for the reason `sandbox.plan`
        # leaves `unsupported` alone: on a machine that cannot confine at all,
        # `off` would say "there is a switch and it is down" and send the
        # operator looking for the switch.
        return {"status": "unsupported", "mechanism": None,
                "detail": {**local, "reason":
                           "AppContainer needs Windows 8 or later "
                           "(userenv!CreateAppContainerProfile) and icacls"}}
    if not confine:
        # The reason is refined by `sandbox.plan` (it knows whether this was
        # the env var or the config file); naming the switch is its job.
        return {"status": "off", "mechanism": None,
                "detail": {**local, "reason": "confinement is switched off"}}

    def off(reason: str) -> dict:
        backend.warnings.append(f"the AppContainer confinement is off: {reason}")
        return {"status": "off", "mechanism": None,
                "detail": {**local, "reason": reason}}

    name = profile_name()
    try:
        profile = AppContainerProfile.ensure(name)
    except (OSError, AttributeError) as exc:
        return off(f"the profile could not be prepared: {exc}")

    # Reads first: an interpreter the container cannot open is a worker that
    # never starts, and the write roots are worthless without it. `C:\\Windows`
    # and `Program Files` already carry an ALL APPLICATION PACKAGES RX ACE, so
    # a base interpreter there is readable with or without ours — but a grant
    # that FAILED is still a confinement we cannot vouch for, and saying
    # `active` over it would be exactly the overstatement Decision 8 forbids.
    for path in _read_roots():
        ok, tail = acl_grant(path, profile.sid_str, READ_RIGHTS)
        if not ok:
            return off(f"read access to {path} could not be granted: {tail}")
    for path in write_roots:
        if not os.path.isdir(path):
            # One lost grant, not a lost confinement — the Landlock
            # `landlock_root` precedent (review I2): the container is in force
            # and *narrower* than intended, so `off` would be the overstatement
            # in reverse. The write really will be denied, hence the warning.
            backend.warnings.append(
                f"the AppContainer was not granted {path} (it does not "
                f"exist); writes there will be denied")
            continue
        ok, tail = acl_grant(path, profile.sid_str, WRITE_RIGHTS)
        if not ok:
            return off(f"write access to {path} could not be granted: {tail}")

    backend.profile = profile
    payload["confinement"] = list(CONFINEMENT_FACETS)
    payload["appcontainer"] = {"sid": profile.sid_str, "name": profile.name}
    return {"status": "active", "mechanism": "appcontainer",
            "detail": {**local, "sid": profile.sid_str}}


def _read_roots() -> list[str]:
    """What the container has to be able to READ to be a Python at all.

    The base interpreter, the venv and the app tree — deduplicated (outside a
    venv ``sys.prefix`` *is* ``sys.base_prefix``) and skipping anything that is
    not there, because `icacls` on a missing path is a failure that would take
    the whole confinement down for no reason.
    """
    roots: list[str] = []
    for path in (sys.base_prefix, sys.prefix, str(resource_root())):
        try:
            real = os.path.realpath(path)
        except (OSError, ValueError):          # pragma: no cover - defensive
            continue
        if real not in roots and os.path.isdir(real):
            roots.append(real)
    return roots


class WindowsBackend:
    """The live half: the profile, the job object, the psapi sampler."""

    def __init__(self, quotas: Quotas) -> None:
        self.warnings: list[str] = []
        self.quotas = quotas
        #: The AppContainer this backend spawns into, or ``None`` when the
        #: confinement is off/unsupported — which is also what
        #: :meth:`spawn` reads to decide whether to spawn at all.
        self.profile: AppContainerProfile | None = None
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

    def spawn(self, argv: list[str], env: dict[str, str] | None):
        """The confined worker, or ``None`` to let the client ``Popen``.

        ``None`` is the honest answer whenever this plan has no AppContainer
        (off, unsupported, or a failed grant): the worker still runs, still
        under the job object, and the confinement facets are not in its
        payload, so nothing claims a containment it does not have.

        A spawn that *fails* is the same answer plus a warning. Refusing to
        start a worker at all would take the whole service down over a token,
        and the honesty rule already covers the difference: the payload was
        written with the facets, the worker's own report says
        ``appcontainer: false``, and health reads ``off``.
        """
        if self.profile is None:
            return None
        try:
            return ConfinedProcess(argv, env, sid=self.profile.sid,
                                   job=self.job)
        except (OSError, AttributeError, ValueError) as exc:
            self.warnings.append(
                f"the AppContainer spawn failed, so this worker is NOT "
                f"confined: {type(exc).__name__}: {exc}")
            return None

    def prepare_tmp_hook(self, tmp_dir: str) -> None:
        """Make the private temp dir usable from inside the container.

        Two things, and both have to happen at every spawn rather than once at
        plan time: ``stop()`` removes the directory and a restarted client
        recreates it, taking the ACE with it.

        1. The **package tree** (:data:`PACKAGE_SUBDIRS`) — the lowbox token
           redirects ``%TEMP%`` into ``<tmp>\\Packages\\<name>\\AC\\Temp``, and
           a worker whose ``tempfile.gettempdir()`` raises is a worker that
           cannot export anything.
        2. The **grant**, after it, so one inheritable ACE covers the tree.

        Never raises: a failure here is a confinement the worker will report
        for itself, not a reason to refuse to start.
        """
        if self.profile is None:
            return
        try:
            make_package_tree(tmp_dir, self.profile.name)
        except OSError as exc:
            self.warnings.append(
                f"the AppContainer package tree under {tmp_dir} could not be "
                f"created (the worker's %TEMP% points into it): {exc}")
        ok, tail = acl_grant(tmp_dir, self.profile.sid_str, WRITE_RIGHTS)
        if not ok:
            self.warnings.append(
                f"the worker's private temp dir {tmp_dir} could not be "
                f"granted to the AppContainer: {tail}")

    def attach(self, proc) -> None:
        """Assign the worker to the job, right after the spawn.

        A no-op for a process :meth:`spawn` started: it was assigned while it
        was still suspended (Decision 4), which is strictly better — this path
        leaves the worker's first milliseconds outside the job.
        """
        if getattr(proc, "job_assigned", False):
            # Remembered anyway: `rss_bytes` reads `attached` to decide whether
            # sampling the job's process list is worth anything.
            self.attached = self.job is not None
            return
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


# ---------------------------------------------- the Win32 calls: AppContainer
#
# Same rule as above: one function per entry point, module-level, so a test on
# another OS can stub the boundary instead of the behaviour. `supported()` and
# the profile go through `_userenv_*`; `acl_grant` goes through `_icacls`.


def _win_error(call: str) -> OSError:
    """The last Win32 error, named after the call that produced it."""
    code = ctypes.get_last_error()
    describe = getattr(ctypes, "FormatError", None)
    return OSError(f"{call} failed: WinError {code}: "
                   f"{describe(code) if describe else ''}".rstrip())


def _advapi32():
    return _library("advapi32")


def _userenv():
    return _library("userenv")


def _userenv_symbol(name: str) -> bool:
    """Whether ``userenv.dll`` exports *name* (it does not below Windows 8)."""
    try:
        return hasattr(_userenv(), name)
    except (OSError, AttributeError):          # non-Windows, or no such DLL
        return False


def _icacls() -> str | None:
    """The ``icacls`` executable, or ``None`` when it is not on PATH."""
    return shutil.which("icacls")


def _userenv_create_profile(name: str, display: str,
                            description: str) -> tuple[int, int]:
    """``CreateAppContainerProfile`` -> ``(HRESULT, PSID)``.

    ``HRESULT_FROM_WIN32(ERROR_ALREADY_EXISTS)`` is a normal answer, not an
    error; the caller derives the SID instead. The PSID is Windows-allocated
    and deliberately never freed — every spawn passes it in
    ``SECURITY_CAPABILITIES`` (see :class:`AppContainerProfile`).
    """
    userenv = _userenv()
    userenv.CreateAppContainerProfile.restype = ctypes.c_long
    userenv.CreateAppContainerProfile.argtypes = [
        ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_wchar_p,
        ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
    sid = ctypes.c_void_p()
    hr = userenv.CreateAppContainerProfile(name, display, description, None, 0,
                                           ctypes.byref(sid))
    return int(hr), int(sid.value or 0)


def _userenv_derive_sid(name: str) -> tuple[int, int]:
    """``DeriveAppContainerSidFromAppContainerName`` -> ``(HRESULT, PSID)``."""
    userenv = _userenv()
    userenv.DeriveAppContainerSidFromAppContainerName.restype = ctypes.c_long
    userenv.DeriveAppContainerSidFromAppContainerName.argtypes = [
        ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p)]
    sid = ctypes.c_void_p()
    hr = userenv.DeriveAppContainerSidFromAppContainerName(name,
                                                           ctypes.byref(sid))
    return int(hr), int(sid.value or 0)


def _sid_to_string(psid: int) -> str:
    """``ConvertSidToStringSidW``: ``S-1-15-2-...``, which is what ACLs use."""
    advapi32 = _advapi32()
    advapi32.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    out = ctypes.c_void_p()
    if not advapi32.ConvertSidToStringSidW(ctypes.c_void_p(int(psid)),
                                           ctypes.byref(out)):
        raise _win_error("ConvertSidToStringSidW")
    try:
        return ctypes.wstring_at(out)
    finally:
        _kernel32().LocalFree(out)


def _pipe(child_end: str) -> tuple[int, int]:
    """``CreatePipe`` with **only** the child's end inheritable.

    *child_end* is ``"read"`` (the worker's stdin) or ``"write"`` (its stdout
    and stderr); the parent's end has ``HANDLE_FLAG_INHERIT`` cleared so it
    does not cross into the container — which, with the attribute list's
    ``HANDLE_LIST``, is what keeps the worker holding exactly three handles.
    """
    kernel32 = _kernel32()
    attributes = SECURITY_ATTRIBUTES()
    attributes.nLength = ctypes.sizeof(SECURITY_ATTRIBUTES)
    attributes.lpSecurityDescriptor = None
    attributes.bInheritHandle = 1
    read = ctypes.c_void_p()
    write = ctypes.c_void_p()
    kernel32.CreatePipe.argtypes = [
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(SECURITY_ATTRIBUTES), ctypes.c_uint32]
    if not kernel32.CreatePipe(ctypes.byref(read), ctypes.byref(write),
                               ctypes.byref(attributes), 0):
        raise _win_error("CreatePipe")
    parent = write if child_end == "read" else read
    kernel32.SetHandleInformation.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                              ctypes.c_uint32]
    if not kernel32.SetHandleInformation(parent, HANDLE_FLAG_INHERIT, 0):
        raise _win_error("SetHandleInformation")
    return int(read.value or 0), int(write.value or 0)
