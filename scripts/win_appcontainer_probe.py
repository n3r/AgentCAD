"""The PRD-006b experiment: can a kernel worker live inside an AppContainer?

There is no Windows machine in this development environment, so every fact
about AppContainer + CPython + OCCT has to come back from a
`windows-latest` runner (design spec, Decision 5). This script is that round
trip: one standalone file, stdlib + ctypes only, run by
`.github/workflows/windows-probe.yml`, which performs the **whole** experiment
and prints a structured report.

Two rules shape the code, and both come from the round-trip cost (~5 minutes
each, budget ~5 rounds):

1. **Nothing aborts.** Every step is wrapped; a failure prints
   `PROBE <step> FAIL <detail>` and the next step still runs. A run that dies
   at the first missing ACE would answer one question and cost a round; this
   one answers all of them and costs the same round.
2. **Everything is printed.** icacls exit codes and ACE listings, the first
   denied path, the child's stderr tail, the job object's peak commit, the
   verbatim exception of every battery item. The controller pastes the log
   back and the implementation slice starts from measured facts.

Since Slice 2 landed, the profile, the ACL grant, the package tree and the
`CreateProcessW` spawn are **imported from `agentcad.kernel.sandbox_windows`**
rather than duplicated here: this script proved them, and a probe that drifts
from the code it is supposed to be probing is worse than no probe. What stays
local is what only a probe needs — the job object with its own knobs, the
diagnostics that read ACLs back, and the line-JSON protocol helpers on top of
the spawned process. The child is the real
`python -m agentcad.kernel.worker`, because the point is to prove **the real
worker**, not a toy, builds and exports inside the container.

## What the steps prove

* `userenv`   — the AppContainer API exists on this OS at all.
* `profile`   — a package SID can be created (or re-derived) for this install.
* `acl`       — `icacls` grants land, and — the load-bearing unknown — an
                inheritable ACE set on a directory **propagates to children
                that already existed** (design spec, Decision 2; if it does
                not, the implementation needs `/T`). It also creates the
                ``Packages/<profile>/AC/Temp`` tree the lowbox token redirects
                ``%TEMP%`` into, which round 1 proved is nobody else's job.
* `spawn`     — `CreateProcessW` with `SECURITY_CAPABILITIES` starts the venv
                `python.exe` launcher, the job object takes it while it is
                suspended, and it is still alive a second later.
* `token`     — a second confined child reads **its own token**
                (`TokenIsAppContainer`, `TokenAppContainerSid`) and maps what
                it can reach, so the confinement is proven independently of
                the worker (this is what Decision 3 will move into the
                worker's preamble).
* `ping`      — build123d/OCCT **import** inside the container. This is the
                load-bearing negative: if a DLL is resolved from a directory
                we did not grant, the failure shows up here with the path.
* `build`     — a real tessellation, written into a **pre-existing**
                `project/.cache/sub/` (the ACE-propagation question, from the
                writing side).
* `export`    — a STEP export, the second half of PRD-006's AC2.
* `battery`   — the denials the confinement is *for*: outbound network, a
                write outside the granted roots, a child process inheriting
                the lowbox token, and the job object's memory cap.
* `cleanup`   — the probe's own profile is deleted (the real one, per
                Decision 2, never is).

## Knobs

`--job-memory-mb` (default 1024) and `--balloon-gib` (default 2) exist for one
reason: a warm worker measured 451-482 MB RSS on macOS/Linux, but a Windows
job object limits *committed* memory, which is not RSS. If `ping` dies with a
`MemoryError` inside `import build123d`, the cap was the cause and not the
container — re-run with `--job-memory-mb 2048` (the product default) rather
than spending a round on the wrong hypothesis. The run prints the job's
`PeakProcessMemoryUsed` so the answer arrives even when nothing fails.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import traceback
from collections import deque
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from agentcad._resources import resource_root
except Exception:  # noqa: BLE001 - the probe must run even from a broken tree
    def resource_root() -> Path:  # type: ignore[misc]
        return _REPO_ROOT

# The product's own AppContainer plumbing. Imported, not copied: these are the
# prototypes this probe proved, and they now live in the module the server
# uses — so a probe run exercises exactly what ships. The module is
# ctypes-lazy, so this import works on macOS too (where the file is edited).
from agentcad.kernel.sandbox_windows import (  # noqa: E402
    JOB_OBJECT_LIMIT_ACTIVE_PROCESS,
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    JOB_OBJECT_LIMIT_PROCESS_MEMORY,
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
    READ_RIGHTS,
    WRITE_RIGHTS,
    AppContainerProfile,
    JobObjectBasicProcessIdList,
    JobObjectExtendedLimitInformation,
    _process_id_list,
    acl_grant,
    make_package_tree,
)
from agentcad.kernel.sandbox_windows import (  # noqa: E402
    ConfinedProcess as _ConfinedProcess,
)


# ------------------------------------------------------------------ reporting

#: Every `PROBE ...` line, in order, reprinted as one block at the end so the
#: controller can copy the report without the interleaved context.
LINES: list[str] = []


def emit(text: str) -> None:
    print(text, flush=True)


def report(step: str, ok: bool, detail: str = "") -> bool:
    line = f"PROBE {step} {'OK' if ok else 'FAIL'} {detail}".rstrip()
    LINES.append(line)
    emit(line)
    return ok


def note(text: str) -> None:
    """Context under a PROBE line: never parsed, always read."""
    for chunk in str(text).splitlines() or [""]:
        emit(f"    {chunk}")


def note_block(label: str, text: str, limit: int = 20) -> None:
    lines = [line.rstrip() for line in str(text).splitlines() if line.strip()]
    note(f"{label}: {len(lines)} line(s)" + ("" if len(lines) <= limit
                                             else f", last {limit}"))
    for line in lines[-limit:]:
        note(f"  | {line}")


def oneline(text: str, limit: int = 600) -> str:
    """A message safe to put on a PROBE line: single line, bounded."""
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[:limit] + " ..."


# ------------------------------------------------------------ Win32 constants
#
# Only what the PROBE needs beyond the product's own module: the job object is
# built here with explicit knobs (`--job-memory-mb`), and the profile is
# deleted at the end, which the product deliberately never does.

#: `QueryInformationJobObject` says the id list did not fit.
ERROR_MORE_DATA = 234
JOB_PID_CAPACITY = 256

# --------------------------------------------------------------- Win32 access
#
# Every `WinDLL` lookup is inside a function so this file imports on macOS —
# which is where it is written and syntax-checked.

_LIBS: dict = {}


def lib(name: str):
    library = _LIBS.get(name)
    if library is None:
        library = _LIBS[name] = ctypes.WinDLL(name, use_last_error=True)
    return library


def win_error(call: str) -> OSError:
    code = ctypes.get_last_error()
    describe = getattr(ctypes, "FormatError", None)
    text = describe(code) if describe else ""
    return OSError(f"{call} failed: WinError {code}: {text}")


def hresult_text(hr: int) -> str:
    return f"0x{hr & 0xFFFFFFFF:08X}"


def job_process_ids(job: int) -> list[int]:
    """The pids currently in *job*; `[]` when it cannot be asked. Never raises:
    this is diagnostics, and a refused query must not end the run."""
    try:
        kernel32 = lib("kernel32")
    except Exception:  # noqa: BLE001
        return []
    capacity = JOB_PID_CAPACITY
    for _ in range(2):
        buffer = _process_id_list(capacity)()
        returned = ctypes.c_uint32(0)
        ok = kernel32.QueryInformationJobObject(
            ctypes.c_void_p(job), JobObjectBasicProcessIdList,
            ctypes.byref(buffer), ctypes.sizeof(buffer), ctypes.byref(returned))
        if ok:
            count = min(int(buffer.NumberOfProcessIdsInList), capacity)
            return [int(buffer.ProcessIdList[i]) for i in range(count)]
        if ctypes.get_last_error() != ERROR_MORE_DATA:
            return []
        assigned = int(buffer.NumberOfAssignedProcesses)
        if assigned <= capacity:
            return []
        capacity = assigned
    return []


def job_peak_bytes(job: int) -> tuple[int, int] | None:
    """`(PeakProcessMemoryUsed, PeakJobMemoryUsed)` — the measurement that says
    whether the product's 2048 MB commit default has headroom on Windows."""
    try:
        kernel32 = lib("kernel32")
    except Exception:  # noqa: BLE001
        return None
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    returned = ctypes.c_uint32(0)
    ok = kernel32.QueryInformationJobObject(
        ctypes.c_void_p(job), JobObjectExtendedLimitInformation,
        ctypes.byref(info), ctypes.sizeof(info), ctypes.byref(returned))
    if not ok:
        return None
    return int(info.PeakProcessMemoryUsed), int(info.PeakJobMemoryUsed)


def job_create(memory_bytes: int, active_processes: int) -> int:
    kernel32 = lib("kernel32")
    kernel32.CreateJobObjectW.restype = ctypes.c_void_p
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise win_error("CreateJobObjectW")
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = (
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        | JOB_OBJECT_LIMIT_PROCESS_MEMORY
        | JOB_OBJECT_LIMIT_ACTIVE_PROCESS)
    info.ProcessMemoryLimit = memory_bytes
    info.BasicLimitInformation.ActiveProcessLimit = active_processes
    ok = kernel32.SetInformationJobObject(
        ctypes.c_void_p(int(job)), JobObjectExtendedLimitInformation,
        ctypes.byref(info), ctypes.sizeof(info))
    if not ok:
        error = win_error("SetInformationJobObject")
        kernel32.CloseHandle(ctypes.c_void_p(int(job)))
        raise error
    return int(job)


def close_handle(handle: int | None) -> None:
    if not handle:
        return
    try:
        lib("kernel32").CloseHandle(ctypes.c_void_p(int(handle)))
    except Exception:  # noqa: BLE001 - cleanup never raises
        pass


# ------------------------------------------------------------ ConfinedProcess

class ProbeError(RuntimeError):
    """A step could not answer. Reported, never fatal."""


class ConfinedProcess(_ConfinedProcess):
    """The product's `ConfinedProcess`, plus what only a probe needs.

    The spawn itself — the pipes, the `SECURITY_CAPABILITIES` attribute list,
    the suspended start, the job assignment, `ResumeThread` — is
    `agentcad.kernel.sandbox_windows.ConfinedProcess`, which is where this
    probe's prototype ended up. What is added here is the line-JSON protocol
    (`request`/`collect`), the stderr tail and the reader threads: the kernel
    client has its own, and duplicating the *spawn* is what would let the two
    drift.
    """

    def __init__(self, argv: list[str], env: dict[str, str], cwd: str,
                 sid_ptr: int, job: int | None) -> None:
        super().__init__(argv, env, sid=sid_ptr, job=job, cwd=cwd)
        self.argv = list(argv)
        self.stderr_tail: deque[str] = deque(maxlen=200)
        self.lines: "queue.Queue[str | None]" = queue.Queue()
        self._next_id = 1
        threading.Thread(target=self._drain_stdout, daemon=True).start()
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    @property
    def handle(self) -> int | None:
        """The product calls it `_handle` (the `Popen` name the client reads);
        the probe's own steps were written against `handle`."""
        return self._handle

    def wait(self, timeout: float | None = None) -> int | None:
        """`Popen`-shaped upstream (it raises `TimeoutExpired`); here a
        deadline that passed is diagnostics, not an exception."""
        try:
            return super().wait(timeout)
        except subprocess.TimeoutExpired:
            return self.poll()

    def _drain_stdout(self) -> None:
        try:
            for line in self.stdout:
                self.lines.put(line)
        except Exception as exc:  # noqa: BLE001
            self.stderr_tail.append(f"[probe] stdout reader: {exc}")
        self.lines.put(None)

    def _drain_stderr(self) -> None:
        try:
            for line in self.stderr:
                self.stderr_tail.append(line.rstrip())
        except Exception as exc:  # noqa: BLE001
            self.stderr_tail.append(f"[probe] stderr reader: {exc}")

    def request(self, method: str, params: dict, timeout: float) -> dict:
        """One line-JSON round trip, with a deadline. Raises `ProbeError` on a
        timeout or an EOF — never blocks the probe forever."""
        request_id = self._next_id
        self._next_id += 1
        payload = json.dumps({"id": request_id, "method": method,
                              "params": params})
        try:
            self.stdin.write(payload + "\n")
            self.stdin.flush()
        except OSError as exc:
            raise ProbeError(f"writing {method}: {type(exc).__name__}: {exc} "
                             f"(worker rc={self.poll()})") from exc
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProbeError(f"{method} timed out after {timeout:.0f}s "
                                 f"(worker rc={self.poll()})")
            try:
                line = self.lines.get(timeout=min(remaining, 5.0))
            except queue.Empty:
                continue
            if line is None:
                raise ProbeError(f"{method}: worker stdout closed "
                                 f"(rc={self.poll()})")
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                self.stderr_tail.append(f"[probe] non-JSON stdout: {line[:200]}")
                continue
            if message.get("id") == request_id:
                return message

    def collect(self, timeout: float) -> list[str]:
        """Every stdout line until EOF or the deadline (the token probe)."""
        deadline = time.monotonic() + timeout
        collected: list[str] = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return collected
            try:
                line = self.lines.get(timeout=min(remaining, 5.0))
            except queue.Empty:
                continue
            if line is None:
                return collected
            collected.append(line.rstrip())

    def stderr_report(self, limit: int = 25) -> None:
        note_block("child stderr tail", "\n".join(self.stderr_tail), limit)


# --------------------------------------------------------- the child programs

#: Runs INSIDE the container, with no build123d and no product import. It
#: answers the Decision 3 question (`TokenIsAppContainer` on its own token)
#: independently of the worker, and — because it costs a second — maps what
#: the container can and cannot reach, so one round answers the reachability
#: matrix even if the worker never gets to `ping`.
TOKEN_PROBE_SOURCE = r'''
import ctypes
import json
import os
import socket
import sys
import tempfile

TOKEN_QUERY = 8
TokenIsAppContainer = 29
TokenAppContainerSid = 31

# Nothing eager that can raise. Round 1 died right here: the lowbox token
# rewrites TEMP to %LOCALAPPDATA%\Packages\<moniker>\AC\Temp, that directory
# did not exist, `tempfile.gettempdir()` raised FileNotFoundError while this
# dict was being built, and the whole probe child produced no report at all.
out = {
    "pid": os.getpid(),
    "executable": sys.executable,
    "prefix": sys.prefix,
    "base_prefix": sys.base_prefix,
    "env_TEMP": os.environ.get("TEMP"),
    "env_TMP": os.environ.get("TMP"),
    "env_USERPROFILE": os.environ.get("USERPROFILE"),
    "env_LOCALAPPDATA": os.environ.get("LOCALAPPDATA"),
}


def attempt(name, fn):
    try:
        out[name] = {"ok": True, "value": fn()}
    except BaseException as exc:
        out[name] = {"ok": False,
                     "error": "%s: %s" % (type(exc).__name__, exc)}


# Every HANDLE crosses as c_void_p. Round 2 died on exactly this: with no
# argtypes, `GetCurrentProcess()`'s pseudo-handle (0xFFFFFFFFFFFFFFFF) is
# marshalled as a 32-bit int and ctypes raises
# `ArgumentError: argument 1: OverflowError: int too long to convert`.
def token_api():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    advapi32.OpenProcessToken.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
    advapi32.GetTokenInformation.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32)]
    advapi32.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    handle = ctypes.c_void_p()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(),
                                     TOKEN_QUERY, ctypes.byref(handle)):
        raise OSError("OpenProcessToken: %d" % ctypes.get_last_error())
    return kernel32, advapi32, handle


def token_flag():
    _kernel32, advapi32, handle = token_api()
    value = ctypes.c_uint32(0)
    returned = ctypes.c_uint32(0)
    if not advapi32.GetTokenInformation(handle, TokenIsAppContainer,
                                        ctypes.byref(value), 4,
                                        ctypes.byref(returned)):
        raise OSError("GetTokenInformation(IsAppContainer): %d"
                      % ctypes.get_last_error())
    return bool(value.value)


def token_sid():
    kernel32, advapi32, handle = token_api()
    size = ctypes.c_uint32(0)
    # First call sizes the buffer; it is expected to fail.
    advapi32.GetTokenInformation(handle, TokenAppContainerSid, None, 0,
                                 ctypes.byref(size))
    if size.value == 0:
        raise OSError("GetTokenInformation(AppContainerSid) size: %d"
                      % ctypes.get_last_error())
    buffer = (ctypes.c_char * size.value)()
    if not advapi32.GetTokenInformation(handle, TokenAppContainerSid, buffer,
                                        size, ctypes.byref(size)):
        raise OSError("GetTokenInformation(AppContainerSid): %d"
                      % ctypes.get_last_error())
    psid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
    text = ctypes.c_void_p()
    if not advapi32.ConvertSidToStringSidW(ctypes.c_void_p(psid),
                                           ctypes.byref(text)):
        raise OSError("ConvertSidToStringSidW: %d" % ctypes.get_last_error())
    try:
        return ctypes.wstring_at(text)
    finally:
        kernel32.LocalFree(text)


def write_probe(path):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("probe\n")
    return path


def connect(host, port):
    sock = socket.create_connection((host, port), timeout=3)
    sock.close()
    return "connected"


def named_temp_file():
    handle = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         suffix=".probe", delete=False)
    try:
        handle.write("probe\n")
    finally:
        handle.close()
    return handle.name


attempt("is_appcontainer", token_flag)
attempt("appcontainer_sid", token_sid)
# The three that raised (or would have) in round 1, each on its own so one
# failure cannot take the report down with it.
attempt("gettempdir", tempfile.gettempdir)
attempt("expanduser", lambda: os.path.expanduser("~"))
attempt("named_temp_file", named_temp_file)
attempt("listdir_prefix", lambda: len(os.listdir(sys.prefix)))
attempt("listdir_base_prefix", lambda: len(os.listdir(sys.base_prefix)))
attempt("listdir_resource_root", lambda: len(os.listdir(@@RESOURCE_ROOT@@)))
attempt("read_executable",
        lambda: repr(open(sys.executable, "rb").read(2)))
attempt("import_agentcad",
        lambda: str(__import__("agentcad._resources",
                               fromlist=["resource_root"]).resource_root()))
attempt("write_project", lambda: write_probe(@@PROJECT@@ + "\\token.txt"))
attempt("write_tmp", lambda: write_probe(@@TMPDIR@@ + "\\token.txt"))
attempt("write_other", lambda: write_probe(@@OTHER@@ + "\\token.txt"))
attempt("write_public",
        lambda: write_probe("C:\\Users\\Public\\agentcad-probe.txt"))
attempt("network_1111", lambda: connect("1.1.1.1", 80))
attempt("loopback_80", lambda: connect("127.0.0.1", 80))

sys.stdout.write("TOKENPROBE " + json.dumps(out, sort_keys=True) + "\n")
sys.stdout.flush()
'''

#: A minimal, valid part script (`PARAMS` + `build(p)`), the AC2 positive.
BOX_SCRIPT = (
    "from build123d import *\n"
    "PARAMS = {\"size\": {\"type\": \"number\", \"default\": 10.0,\n"
    "                    \"min\": 1.0, \"max\": 100.0, \"unit\": \"mm\"}}\n"
    "def build(p):\n"
    "    return Box(p.size, p.size, p.size)\n"
)


def battery_script(body: str) -> str:
    """A part script whose `build(p)` performs one hostile operation.

    Nothing is caught: an expected denial must reach the worker's own
    `_script_error_from_exc`, so the probe sees the real exception type, the
    real message (the first denied path is the prize) and whatever
    `details.denied` the worker was able to attach. Where the operation does
    NOT raise, the body raises a `RuntimeError` carrying the outcome — so
    every battery item comes back through the same channel and a *silent
    success* is impossible to mistake for a pass.
    """
    indented = "\n".join("    " + line if line.strip() else ""
                         for line in body.strip("\n").splitlines())
    return ("PARAMS = {\"n\": {\"type\": \"number\", \"default\": 1.0,\n"
            "                 \"min\": 0.0, \"max\": 2.0}}\n"
            "def build(p):\n" + indented + "\n")


# --------------------------------------------------------------------- the run

class Probe:
    def __init__(self, args) -> None:
        self.args = args
        self.root = resource_root()
        digest = hashlib.sha256(str(self.root).encode("utf-8")).hexdigest()
        self.profile_name = f"agentcad-probe-{digest[:8]}"
        self.sid_ptr: int | None = None
        self.sid_text: str | None = None
        self.scratch: Path | None = None
        self.project: Path | None = None
        self.tmpdir: Path | None = None
        self.other: Path | None = None
        self.job: int | None = None
        self.worker: ConfinedProcess | None = None
        self.token_child: ConfinedProcess | None = None

    # -- 1: userenv

    def step_userenv(self) -> None:
        version = sys.getwindowsversion()
        note(f"windows version: {version}")
        note(f"python {sys.version.split()[0]} · executable {sys.executable}")
        note(f"sys.prefix       {sys.prefix}")
        note(f"sys.base_prefix  {sys.base_prefix}")
        note(f"resource_root()  {self.root}")
        note(f"cwd              {os.getcwd()}")
        note(f"profile name     {self.profile_name}")
        missing = []
        for library, names in (("userenv", ("CreateAppContainerProfile",
                                            "DeriveAppContainerSidFromAppContainerName",
                                            "DeleteAppContainerProfile")),
                               ("advapi32", ("ConvertSidToStringSidW",
                                             "OpenProcessToken",
                                             "GetTokenInformation")),
                               ("kernel32", ("CreateProcessW",
                                             "InitializeProcThreadAttributeList",
                                             "UpdateProcThreadAttribute",
                                             "AssignProcessToJobObject"))):
            module = lib(library)
            for name in names:
                if not hasattr(module, name):
                    missing.append(f"{library}!{name}")
        icacls = shutil.which("icacls")
        note(f"icacls: {icacls}")
        detail = (f"build={version.build} icacls={'yes' if icacls else 'NO'} "
                  f"missing={','.join(missing) or 'none'}")
        report("userenv", not missing and bool(icacls), detail)

    # -- 2: profile

    def step_profile(self) -> None:
        """The product's own create-or-derive (`AppContainerProfile.ensure`),
        under the PROBE's name so `cleanup` can delete it."""
        try:
            profile = AppContainerProfile.ensure(self.profile_name)
        except OSError as exc:
            report("profile", False, f"{self.profile_name}: {exc}")
            return
        self.sid_ptr = profile.sid
        self.sid_text = profile.sid_str
        report("profile", True, f"{self.profile_name} sid={self.sid_text}")

    # -- 3: acl

    def step_acl(self) -> None:
        base = Path(os.environ.get("RUNNER_TEMP") or os.environ.get("TEMP")
                    or os.getcwd())
        self.scratch = Path(base) / f"agentcad-probe-{os.getpid()}"
        self.project = self.scratch / "project"
        self.tmpdir = self.scratch / "tmp"
        self.other = self.scratch / "other"
        # `.cache/sub` and `exports` exist BEFORE the grant: that is the whole
        # point of the propagation question (Decision 2). If an inheritable
        # ACE set on `project` does not reach them, the implementation needs
        # `icacls /T` and this run says so.
        for path in (self.project / ".cache" / "sub", self.project / "exports",
                     self.tmpdir, self.other):
            path.mkdir(parents=True, exist_ok=True)
        note(f"scratch: {self.scratch}")
        self._package_tree()
        if self.sid_text is None:
            report("acl", False, "skipped: no SID")
            return

        grants = [("project", self.project, WRITE_RIGHTS),
                  ("tmp", self.tmpdir, WRITE_RIGHTS),
                  ("base_prefix", Path(sys.base_prefix), READ_RIGHTS),
                  ("prefix", Path(sys.prefix), READ_RIGHTS),
                  ("resource_root", self.root, READ_RIGHTS)]
        ok_count = 0
        for label, path, rights in grants:
            # The product's `acl_grant`, rights included: the probe asks the
            # same question the plan asks, or it is not answering it.
            ok, tail = acl_grant(str(path), self.sid_text, rights)
            ok_count += int(ok)
            note(f"icacls grant {label} {rights}: {tail}")
            report(f"acl.grant.{label}", ok, f"{path}")

        # The direct evidence, read back from the child that already existed.
        child = self.project / ".cache" / "sub"
        _code, listing = self._icacls([str(child)], "read .cache/sub")
        propagated = self.sid_text in listing
        report("acl.propagation", propagated,
               f"inheritable ACE {'reached' if propagated else 'DID NOT reach'} "
               f"pre-existing {child}")

        # The same question for the redirected AppContainer temp (round 1):
        # it is created before the grant too, so the `tmp` ACE must have
        # reached it or `tempfile` inside the container has nothing to use.
        ac_temp = self.appcontainer_temp()
        _code, ac_listing = self._icacls([str(ac_temp)], "read AC\\Temp")
        ac_ok = ac_temp.is_dir() and self.sid_text in ac_listing
        report("acl.appcontainer_temp", ac_ok,
               f"{ac_temp} exists={ac_temp.is_dir()} "
               f"ace={'yes' if self.sid_text in ac_listing else 'no'}")

        # Read-only context: whether the ancestors carry an ALL APPLICATION
        # PACKAGES ACE at all, and whether the venv reaches into the uv cache
        # through reparse points (a read root we would otherwise miss).
        for label, path in (("scratch parent", self.scratch.parent),
                            ("scratch", self.scratch),
                            ("resource_root", self.root)):
            self._icacls([str(path)], f"read {label}")
        self._report_reparse_points()
        report("acl", ok_count == len(grants) and propagated and ac_ok,
               f"{ok_count}/{len(grants)} grants, "
               f"propagation={'yes' if propagated else 'no'}, "
               f"appcontainer_temp={'yes' if ac_ok else 'no'}")

    def appcontainer_temp(self) -> Path:
        """Where Windows redirects the container's ``%TEMP%``."""
        return (Path(self.tmpdir) / "Packages" / self.profile_name / "AC"
                / "Temp")

    def _package_tree(self) -> None:
        """The package profile tree Windows expects inside the private temp
        dir — **before** the ACL grant, so inheritance covers it.

        Round 1's finding, and it is not obvious: the lowbox token rewrites the
        child's ``TEMP``/``TMP`` to ``%LOCALAPPDATA%\\Packages\\<moniker>\\AC\\
        Temp``. The plan sets ``LOCALAPPDATA`` to the private temp dir, so the
        redirect lands inside it — but nothing had created that path, so
        ``tempfile.gettempdir()`` found no usable directory and raised
        ``FileNotFoundError`` in the first child that touched it. The product
        makes it in `WindowsBackend.prepare_tmp_hook`, with the same list —
        which is exactly the function called here.
        """
        if self.tmpdir is None:
            return
        package = make_package_tree(str(self.tmpdir), self.profile_name)
        note(f"appcontainer package tree: {package}")

    def _icacls(self, arguments: list[str], label: str) -> tuple[int, str]:
        try:
            completed = subprocess.run(
                ["icacls", *arguments], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=180)
        except Exception as exc:  # noqa: BLE001
            note(f"icacls {label}: {type(exc).__name__}: {exc}")
            return -1, ""
        output = (completed.stdout or "") + (completed.stderr or "")
        note(f"icacls {label}: rc={completed.returncode}")
        note_block("  output", output, 12)
        return completed.returncode, output

    def _report_reparse_points(self) -> None:
        """Does this venv symlink/junction into the uv cache? If it does, that
        cache is a read root the implementation has to grant as well."""
        site = Path(sys.prefix) / "Lib" / "site-packages"
        links: list[str] = []
        try:
            for entry in sorted(site.iterdir())[:400]:
                if entry.is_symlink() or os.path.islink(entry):
                    links.append(f"{entry.name} -> {os.readlink(entry)}")
        except Exception as exc:  # noqa: BLE001
            note(f"site-packages scan: {type(exc).__name__}: {exc}")
        note(f"uv cache dir env: {os.environ.get('UV_CACHE_DIR')}")
        report("venvlinks", True,
               f"{len(links)} reparse point(s) in {site}"
               + (f"; first={links[0]}" if links else ""))

    # -- 4: spawn

    def child_env(self) -> dict[str, str]:
        # `LOCALAPPDATA` is load-bearing beyond "somewhere writable": the
        # lowbox token derives the container's own `%TEMP%` from it, as
        # `%LOCALAPPDATA%\Packages\<profile name>\AC\Temp`. `_make_package_tree`
        # creates exactly that path inside this directory, before the ACL
        # grant, or the redirect points at nothing (round 1).
        tmp = str(self.tmpdir)
        return {
            **os.environ,
            "TEMP": tmp, "TMP": tmp, "USERPROFILE": tmp,
            "APPDATA": tmp, "LOCALAPPDATA": tmp,
            "PYTHONDONTWRITEBYTECODE": "1",
            # The parent declares the two facets it really applied, exactly as
            # `sandbox_macos.build` does for the seatbelt, so the worker's
            # `denials.classify` may name a filesystem/network denial.
            #
            # `quotas: ["job_object"]` is here for the same reason and is the
            # design spec's own payload shape ("Data shapes"): PRD-006's
            # `sandbox_windows.build` already emits it whenever the job really
            # exists, and without it the balloon's `MemoryError` arrives with
            # no `details.denied` at all — the probe would then be unable to
            # tell "the cap bit and was named" from "the cap bit silently".
            # The battery's pass criterion is still the exception type, so
            # dropping this key back out changes what the run *explains*, not
            # what it *proves*.
            "AGENTCAD_CONFINE": json.dumps(
                {"posture": "local", "quotas": ["job_object"],
                 "confinement": ["filesystem", "network"]},
                sort_keys=True),
        }

    def step_spawn(self) -> None:
        if self.sid_ptr is None or self.tmpdir is None:
            report("spawn", False, "skipped: no SID")
            return
        memory_bytes = self.args.job_memory_mb * 1024 * 1024
        self.job = job_create(memory_bytes, 64)
        note(f"job object: commit limit {self.args.job_memory_mb} MiB, "
             f"64 active processes, KILL_ON_JOB_CLOSE")
        argv = [sys.executable, "-u", "-m", "agentcad.kernel.worker"]
        note(f"argv: {subprocess.list2cmdline(argv)}")
        note(f"cwd:  {self.root}")
        self.worker = ConfinedProcess(argv, self.child_env(), str(self.root),
                                      self.sid_ptr, self.job)
        time.sleep(1.0)
        code = self.worker.poll()
        pids = job_process_ids(self.job)
        alive = code is None
        if not alive:
            self.worker.stderr_report()
        report("spawn", alive,
               f"pid={self.worker.pid} job_assigned={self.worker.job_assigned} "
               f"exit={'STILL_ACTIVE' if alive else code} job_pids={pids}")

    # -- 5: token

    def step_token(self) -> None:
        if self.sid_ptr is None or self.project is None:
            report("token", False, "skipped: no SID")
            return
        source = (TOKEN_PROBE_SOURCE
                  .replace("@@RESOURCE_ROOT@@", repr(str(self.root)))
                  .replace("@@PROJECT@@", repr(str(self.project)))
                  .replace("@@TMPDIR@@", repr(str(self.tmpdir)))
                  .replace("@@OTHER@@", repr(str(self.other))))
        script = self.project / "token_probe.py"
        script.write_text(source, encoding="utf-8")
        argv = [sys.executable, "-u", str(script)]
        self.token_child = ConfinedProcess(argv, self.child_env(),
                                           str(self.root), self.sid_ptr,
                                           self.job)
        lines = self.token_child.collect(timeout=120)
        self.token_child.wait(5)
        payload = None
        for line in lines:
            if line.startswith("TOKENPROBE "):
                try:
                    payload = json.loads(line[len("TOKENPROBE "):])
                except ValueError as exc:
                    note(f"TOKENPROBE parse: {exc}")
        if payload is None:
            note_block("token child stdout", "\n".join(lines))
            self.token_child.stderr_report()
            report("token", False,
                   f"no TOKENPROBE line (rc={self.token_child.poll()})")
            return
        for key in sorted(payload):
            note(f"{key}: {json.dumps(payload[key], sort_keys=True)}")
        flag = payload.get("is_appcontainer") or {}
        sid = payload.get("appcontainer_sid") or {}
        in_container = bool(flag.get("ok") and flag.get("value") is True)
        sid_value = sid.get("value") if sid.get("ok") else None
        matches = sid_value == self.sid_text
        report("token.sid_match", matches,
               f"child={sid_value} parent={self.sid_text}")

        # Round 1's other half: where did the lowbox token put `%TEMP%`, and
        # can `tempfile` actually use it? The answer has to land inside the
        # private temp dir the plan granted, or every library that opens a
        # scratch file fails inside the container.
        tempdir = payload.get("gettempdir") or {}
        named = payload.get("named_temp_file") or {}
        tempdir_value = tempdir.get("value") if tempdir.get("ok") else None
        inside = bool(tempdir_value and self.tmpdir is not None
                      and str(tempdir_value).lower().startswith(
                          str(self.tmpdir).lower()))
        temp_ok = bool(inside and named.get("ok"))
        report("token.tempdir", temp_ok,
               f"gettempdir={tempdir_value or tempdir.get('error')} "
               f"inside_private_tmp={inside} "
               f"NamedTemporaryFile={named.get('value') or named.get('error')}")

        report("token", in_container and matches and temp_ok,
               f"TokenIsAppContainer={flag.get('value', flag.get('error'))} "
               f"sid={sid_value} gettempdir={tempdir_value}")

    # -- 6: ping / build / export

    def step_ping(self) -> None:
        if self.worker is None:
            report("ping", False, "skipped: no worker")
            return
        try:
            response = self.worker.request("ping", {},
                                           timeout=self.args.ping_timeout)
        except ProbeError as exc:
            self.worker.stderr_report(40)
            note(f"job pids: {job_process_ids(self.job) if self.job else []}")
            note("if this is a MemoryError inside `import build123d`, the job "
                 "commit limit is the suspect, not the container: re-run with "
                 "--job-memory-mb 2048")
            report("ping", False, oneline(str(exc)))
            return
        if "error" in response:
            self.worker.stderr_report(40)
            report("ping", False, oneline(json.dumps(response["error"])))
            return
        result = response.get("result") or {}
        note(f"sandbox self-report: {json.dumps(result.get('sandbox') or {}, sort_keys=True)}")
        note(f"usage: {json.dumps(response.get('usage') or {}, sort_keys=True)}")
        self.worker.stderr_report(10)
        report("ping", bool(result.get("ok")),
               f"build123d={result.get('build123d')} "
               f"peak_rss_mb={(response.get('usage') or {}).get('peak_rss_mb')}")
        self._report_job_peak()

    def _report_job_peak(self) -> None:
        if self.job is None:
            return
        peak = job_peak_bytes(self.job)
        if peak is None:
            report("jobpeak", False, "QueryInformationJobObject refused")
            return
        process_mb = peak[0] / (1024 * 1024)
        job_mb = peak[1] / (1024 * 1024)
        report("jobpeak", True,
               f"peak process commit {process_mb:.1f} MiB, peak job commit "
               f"{job_mb:.1f} MiB, limit {self.args.job_memory_mb} MiB")

    def step_build(self) -> None:
        if self.worker is None or self.project is None:
            report("build", False, "skipped: no worker")
            return
        mesh_path = self.project / ".cache" / "sub" / "box.acm"
        try:
            response = self.worker.request(
                "build", {"script": BOX_SCRIPT, "params": {},
                          "mesh_path": str(mesh_path), "tolerance": 0.1},
                timeout=self.args.request_timeout)
        except ProbeError as exc:
            self.worker.stderr_report(40)
            report("build", False, oneline(str(exc)))
            return
        if "error" in response:
            self.worker.stderr_report(40)
            report("build", False, oneline(json.dumps(response["error"])))
            return
        result = response.get("result") or {}
        exists = mesh_path.exists()
        size = mesh_path.stat().st_size if exists else 0
        note(f"metrics: {json.dumps(result.get('metrics') or {}, sort_keys=True)}")
        report("build", exists and size > 0,
               f"{mesh_path} exists={exists} bytes={size} "
               f"triangles={result.get('triangles')}")

    def step_export(self) -> None:
        if self.worker is None or self.project is None:
            report("export", False, "skipped: no worker")
            return
        out_path = self.project / "exports" / "box.step"
        try:
            response = self.worker.request(
                "export", {"script": BOX_SCRIPT, "params": {},
                           "format": "step", "out_path": str(out_path),
                           "tolerance": 0.05},
                timeout=self.args.request_timeout)
        except ProbeError as exc:
            self.worker.stderr_report(40)
            report("export", False, oneline(str(exc)))
            return
        if "error" in response:
            self.worker.stderr_report(40)
            report("export", False, oneline(json.dumps(response["error"])))
            return
        result = response.get("result") or {}
        exists = out_path.exists()
        report("export", exists and bool(result.get("size_bytes")),
               f"{out_path} exists={exists} bytes={result.get('size_bytes')}")

    # -- 7: battery

    def battery_items(self) -> list[tuple[str, str, str, float]]:
        """`(name, script body, expectation, timeout)`.

        The expectation is a *word* the outcome has to contain, and it is
        checked against the worker's error message — which is
        `"<ExceptionType>: <message>"`, so the type is part of the string.
        """
        other = str(self.other)
        public = "C:\\Users\\Public\\agentcad-pwned.txt"
        repo = str(self.root / "agentcad-pwned.txt")
        tmp = str(Path(self.tmpdir) / "worker-write.txt")
        return [
            ("network", (
                "import socket\n"
                "socket.create_connection(('1.1.1.1', 80), timeout=3)\n"
                "raise RuntimeError('PROBE_NO_DENIAL: connect succeeded')\n"
            ), "PermissionError", 60.0),
            ("public_write", (
                f"open({public!r}, 'w', encoding='utf-8').write('x')\n"
                "raise RuntimeError('PROBE_NO_DENIAL: public write succeeded')\n"
            ), "PermissionError", 60.0),
            ("other_tmp_write", (
                f"import os\n"
                f"open(os.path.join({other!r}, 'x'), 'w', "
                f"encoding='utf-8').write('x')\n"
                "raise RuntimeError('PROBE_NO_DENIAL: foreign temp write succeeded')\n"
            ), "PermissionError", 60.0),
            ("repo_write", (
                f"open({repo!r}, 'w', encoding='utf-8').write('x')\n"
                "raise RuntimeError('PROBE_NO_DENIAL: RX root was writable')\n"
            ), "PermissionError", 60.0),
            ("private_tmp_write", (
                f"open({tmp!r}, 'w', encoding='utf-8').write('x')\n"
                f"raise RuntimeError('PROBE_OK: wrote ' + {tmp!r})\n"
            ), "PROBE_OK", 60.0),
            ("child_network", (
                "import subprocess, sys\n"
                "completed = subprocess.run(\n"
                "    [sys.executable, '-c',\n"
                "     \"import socket;socket.create_connection"
                "(('1.1.1.1',80),timeout=3)\"],\n"
                "    capture_output=True, text=True, encoding='utf-8',\n"
                "    errors='replace', timeout=90)\n"
                "raise RuntimeError('PROBE_CHILD rc=%r tail=%s' % (\n"
                "    completed.returncode,\n"
                "    ' | '.join(completed.stderr.split())[-400:]))\n"
            ), "PROBE_CHILD", 150.0),
            ("memory_balloon", (
                f"buffer = bytearray({self.args.balloon_gib} << 30)\n"
                "raise RuntimeError('PROBE_NO_DENIAL: allocated %d bytes'\n"
                "                   % len(buffer))\n"
            ), "MemoryError", 180.0),
        ]

    def step_battery(self) -> None:
        if self.worker is None or self.project is None or self.tmpdir is None:
            report("battery", False, "skipped: no worker")
            return
        passed = 0
        items = self.battery_items()
        for name, body, expectation, timeout in items:
            passed += int(self._battery_item(name, body, expectation, timeout))
        report("battery", passed == len(items), f"{passed}/{len(items)}")

    def _battery_item(self, name: str, body: str, expectation: str,
                      timeout: float) -> bool:
        assert self.worker is not None
        mesh = Path(self.project) / ".cache" / "sub" / f"{name}.acm"
        try:
            response = self.worker.request(
                "build", {"script": battery_script(body), "params": {},
                          "mesh_path": str(mesh), "tolerance": 0.1},
                timeout=timeout)
        except ProbeError as exc:
            self.worker.stderr_report(20)
            return report(f"battery.{name}", False, oneline(str(exc)))
        if "error" not in response:
            return report(f"battery.{name}", False,
                          f"NO ERROR: {oneline(json.dumps(response.get('result')))}")
        error = response["error"]
        details = error.get("details") or {}
        message = str(error.get("message", ""))
        note(f"type={error.get('type')} denied={details.get('denied')}")
        note(f"message: {message}")
        note_block("traceback", str(details.get("traceback") or ""), 8)
        if name == "child_network":
            # The inheritance question: a child of the lowbox process must not
            # reach the network either, so a ZERO return code is the failure.
            ok = "rc=0" not in message and expectation in message
        else:
            ok = expectation in message
        return report(f"battery.{name}", ok,
                      f"denied={details.get('denied')} {oneline(message, 300)}")

    # -- 8: cleanup

    def step_cleanup(self) -> None:
        problems: list[str] = []
        for child in (self.worker, self.token_child):
            if child is None:
                continue
            try:
                child.kill()
                child.wait(5)
                child.close()
            except Exception as exc:  # noqa: BLE001
                problems.append(f"{type(exc).__name__}: {exc}")
        if self.job is not None:
            # KILL_ON_JOB_CLOSE: closing this takes any survivor with it.
            close_handle(self.job)
            self.job = None
        deleted = "skipped"
        if self.sid_text is not None:
            userenv = lib("userenv")
            userenv.DeleteAppContainerProfile.restype = ctypes.c_long
            userenv.DeleteAppContainerProfile.argtypes = [ctypes.c_wchar_p]
            # The PROBE's profile is deleted; the real worker's (Decision 2)
            # never is — it is per installation and creating one is not free.
            hr = userenv.DeleteAppContainerProfile(self.profile_name)
            deleted = f"HRESULT {hresult_text(hr)}"
            if hr != 0:
                problems.append(f"DeleteAppContainerProfile: {deleted}")
        if self.scratch is not None and not self.args.keep_scratch:
            shutil.rmtree(self.scratch, ignore_errors=True)
            note(f"scratch removed: {not self.scratch.exists()}")
        report("cleanup", not problems,
               f"profile={self.profile_name} delete={deleted} "
               f"problems={'; '.join(problems) or 'none'}")


def run(args) -> None:
    probe = Probe(args)
    steps = [("userenv", probe.step_userenv),
             ("profile", probe.step_profile),
             ("acl", probe.step_acl),
             ("spawn", probe.step_spawn),
             ("token", probe.step_token),
             ("ping", probe.step_ping),
             ("build", probe.step_build),
             ("export", probe.step_export),
             ("battery", probe.step_battery),
             ("cleanup", probe.step_cleanup)]
    for name, function in steps:
        emit("")
        emit(f"--- {name} " + "-" * (68 - len(name)))
        try:
            function()
        except Exception as exc:  # noqa: BLE001 - a step may not end the run
            report(name, False, f"{type(exc).__name__}: {oneline(exc)}")
            note_block("traceback", traceback.format_exc(), 30)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__ and __doc__.splitlines()[0])
    parser.add_argument("--job-memory-mb", type=int, default=1024,
                        help="job object per-process commit limit (MiB). The "
                             "product default is 2048; raise it here if the "
                             "worker dies inside `import build123d`.")
    parser.add_argument("--balloon-gib", type=int, default=2,
                        help="size of the battery's allocation, in GiB; must "
                             "exceed --job-memory-mb")
    parser.add_argument("--ping-timeout", type=float, default=180.0,
                        help="deadline for the first ping (the OCCT import)")
    parser.add_argument("--request-timeout", type=float, default=120.0,
                        help="deadline for build/export")
    parser.add_argument("--keep-scratch", action="store_true",
                        help="leave the scratch tree behind for inspection")
    args = parser.parse_args(argv)

    emit("=" * 78)
    emit("AgentCAD PRD-006b — Windows AppContainer probe")
    emit(f"platform={sys.platform} argv={sys.argv}")
    emit("=" * 78)

    if sys.platform != "win32":
        report("platform", False,
               f"this probe only runs on Windows (got {sys.platform!r})")
    else:
        try:
            run(args)
        except BaseException as exc:  # noqa: BLE001 - the report is the product
            report("probe", False, f"{type(exc).__name__}: {oneline(exc)}")
            note_block("traceback", traceback.format_exc(), 40)

    failures = [line for line in LINES if " FAIL " in line]
    emit("")
    emit("=" * 78)
    emit("PROBE REPORT")
    emit("=" * 78)
    for line in LINES:
        emit(line)
    emit("=" * 78)
    emit(f"PROBE summary {len(LINES) - len(failures)}/{len(LINES)} OK, "
         f"{len(failures)} FAIL")
    # Always 0: the controller reads the report, and a red job would hide it
    # behind a failed step in the Actions UI.
    return 0


if __name__ == "__main__":
    sys.exit(main())
