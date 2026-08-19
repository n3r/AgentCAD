# 0241 — PRD-006b Slice 1: the Windows AppContainer probe, and the meter bug it found

- **Commit:** pending
- **Date:** 2026-08-19
- **Author:** Nikita Fedorov (orchestrated; Claude Fable 5)

## Summary
Adds the PRD-006b probe loop: one standalone script that performs the whole
AppContainer experiment on a `windows-latest` runner (profile, ACLs, a
`CreateProcessW`/`STARTUPINFOEX` spawn of the **real** kernel worker, the
token self-check, `ping`/`build`/`export`, and the denial battery) and prints
a structured `PROBE <step> OK|FAIL <detail>` report, plus the workflow that
runs it. No product code changes: nothing here is imported by the server, the
kernel or the tests.

## Changes
- `scripts/win_appcontainer_probe.py` (new, stdlib + ctypes only; imports
  `agentcad._resources.resource_root` — with a fallback — and spawns
  `python -m agentcad.kernel.worker` as the child):
  - `userenv` — resolves `CreateAppContainerProfile`,
    `DeriveAppContainerSidFromAppContainerName`,
    `DeleteAppContainerProfile`, `advapi32!ConvertSidToStringSidW` and the
    `kernel32` spawn entry points, prints `sys.getwindowsversion()` and
    whether `icacls` is on PATH.
  - `profile` — creates `agentcad-probe-<sha8 of resource_root()>`, re-deriving
    the SID on `HRESULT_FROM_WIN32(ERROR_ALREADY_EXISTS)` (0x800700B7), and
    prints the SID string.
  - `acl` — builds a scratch tree whose `project/.cache/sub` and
    `project/exports` exist **before** the grant, then
    `icacls <path> /grant "*<SID>:(OI)(CI)M"` on `project` and the private
    temp dir and `(OI)(CI)RX` on `sys.base_prefix`, `sys.prefix` and
    `resource_root()`; prints every exit code and output tail, reads the ACEs
    back off the pre-existing `.cache/sub` to answer the ACE-propagation
    question directly, and reports whether the venv's `site-packages` holds
    reparse points into the uv cache (a read root the implementation would
    otherwise miss).
  - `spawn` — a `ConfinedProcess` prototype: three pipes with only the child
    ends inheritable, a two-entry attribute list
    (`PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES` with no capabilities +
    `PROC_THREAD_ATTRIBUTE_HANDLE_LIST` naming exactly those three handles),
    `CreateProcessW` with `EXTENDED_STARTUPINFO_PRESENT | CREATE_SUSPENDED |
    CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT`, a job object
    (`PROCESS_MEMORY` + `ACTIVE_PROCESS` 64 + `KILL_ON_JOB_CLOSE`) assigned
    while the process is still suspended, then `ResumeThread`; the parent
    wraps its pipe ends with `msvcrt.open_osfhandle` + `open(..., encoding=
    "utf-8", errors="replace", buffering=1)` so the object has the `Popen`
    surface the kernel client uses.
  - `token` — a second confined child reads its **own** token
    (`TokenIsAppContainer`, `TokenAppContainerSid`) and maps reachability
    (listdir of the three read roots, writes into the granted/ungranted dirs,
    an outbound and a loopback connect), so the confinement is proven
    independently of the worker and before `import build123d` has finished.
  - `ping`/`build`/`export` — the real worker's line-JSON protocol with a
    per-request deadline on a reader thread (180 s for the first `ping`, 120 s
    afterwards) and the child's stderr tail printed on every failure; the
    build writes its mesh into the pre-existing `project/.cache/sub/`.
  - `battery` — seven part scripts through the worker, each outcome printed
    verbatim (type, message, `details.denied`, traceback tail): outbound
    connect, a write to `C:\Users\Public`, a write into a second scratch dir
    that was never ACL'd, a write into the `RX`-only app tree, a write into
    the private temp dir (the one expected to **succeed**), a child process
    that tries to connect (token inheritance), and a 2 GiB allocation under
    the job's commit limit.
  - `cleanup` — terminate, close the job (which kills survivors), and
    `DeleteAppContainerProfile` for the probe's own profile. The real
    installation profile of Slice 2 is never deleted.
  - `--job-memory-mb` / `--balloon-gib` / `--ping-timeout` /
    `--request-timeout` / `--keep-scratch`, and a `jobpeak` line reporting the
    job's `PeakProcessMemoryUsed`, so a `MemoryError` inside
    `import build123d` can be re-tested in the same round instead of costing
    one.
- `.github/workflows/windows-probe.yml` (new) — `workflow_dispatch` (with an
  `args` input passed through `$env:PROBE_ARGS`, never interpolated into the
  shell line) **and** `push` on `prd-006b-**` limited to the two probe files,
  because the workflow is not on the default branch and dispatch cannot reach
  it until it merges. `windows-latest`, `timeout-minutes: 20`,
  `uv sync --locked`, `shell: pwsh`.

- `agentcad/kernel/_meter.py` — **product fix**, found by probe round 1.
  `_windows_memory_counters` reached `psapi.GetProcessMemoryInfo` with
  `kernel32.GetCurrentProcess()` and no `restype`, so ctypes marshalled the
  `(HANDLE)-1` pseudo-handle through a 32-bit `c_int` and passed
  `0x00000000FFFFFFFF` on 64-bit Windows. Outside a container that is a
  `FALSE` return; **inside** one it is a structured `STATUS_INVALID_HANDLE`
  (0xC0000008), because AppContainer processes run with strict handle
  checking — and ctypes turns that into `OSError: [WinError -1073741816]`,
  which escaped `Meter.finish()` and killed the worker on the way out of
  *every* request, build and error alike. Now: `GetCurrentProcess.restype =
  c_void_p`, explicit `argtypes`/`restype` on `GetProcessMemoryInfo`,
  `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, …, GetCurrentProcessId())`
  (with `CloseHandle`) as a fallback when the pseudo-handle is refused, and
  the whole call behind a `try` in `_windows_memory_counters` so it answers
  `None` on any failure — the sampler may degrade a measurement, never end a
  request. `sandbox_windows.py`'s own seams were checked and were already
  correct (`CreateJobObjectW`/`OpenProcess` both declare `restype =
  c_void_p`, every handle is passed as `c_void_p`, and it never uses
  `GetCurrentProcess`).
- `tests/test_meter.py` — two tests that run on every platform (they fake
  `sys.platform`): `_memory_mb` tolerates a sampler returning `None` and
  `finish()` still returns a complete usage object with
  `peak_rss_mb=None, rss_mb=None, peak_rss_is_lifetime=True`; and
  `_windows_memory_counters` answers `None` rather than propagating the exact
  `OSError` the container produced.

## Files
- `scripts/win_appcontainer_probe.py` — new
- `.github/workflows/windows-probe.yml` — new
- `agentcad/kernel/_meter.py` — the Windows sampler no longer raises
- `tests/test_meter.py` — two regression tests for it
- `docs/changelog/0241-prd-006b-probe.md` — this entry

## Notes

### Round 1 (windows-latest, build 26100, Python 3.12.10) — the container works
12/24 PROBE lines OK, and the two that mattered most were among them: the
profile, all five `icacls` grants and — the load-bearing unknown —
`acl.propagation OK`, an inheritable ACE reaching the **pre-existing**
`project\.cache\sub` (icacls shows the package SID there as `(I)(OI)(CI)(M)`),
so Slice 2 does **not** need `icacls /T`. `spawn OK pid=3156
job_assigned=True job_pids=[3156, 3196, 1076]` — the venv launcher, the real
interpreter and the job object all behaved, and the worker's own preamble line
came back on stderr (`posture=local quotas=job_object`), which means
**build123d and OCCT imported inside the AppContainer**. Two findings stopped
the run, both now fixed:

1. **The lowbox token redirects `%TEMP%`.** It rewrites the child's
   `TEMP`/`TMP` to `%LOCALAPPDATA%\Packages\<profile name>\AC\Temp`; the plan
   points `LOCALAPPDATA` at the private temp dir, so the redirect landed
   inside it — at a path nobody had created. `tempfile.gettempdir()` then
   raised `FileNotFoundError: No usable temporary directory found`, and it did
   so while the token probe was building its output dict, so that child
   produced no report at all. The probe now creates the package tree
   (`AC/Temp`, `AC/INetCache`, `AC/INetCookies`, `AC/INetHistory`,
   `LocalState`, `TempState`, `RoamingState`, `Settings`) inside the private
   temp dir **before** the ACL grant, so inheritance covers it; reads the ACEs
   back as `PROBE acl.appcontainer_temp`; reports the container's own
   `gettempdir()` and a real `NamedTemporaryFile` write as
   `PROBE token.tempdir`; and computes every value in the token child through
   the same `attempt()` guard, so no single failure can silence the report
   again. **Slice 2 must do the same in `prepare_tmp()`** — same list, same
   ordering.
2. **The meter bug above**, which is a PRD-006 defect that only an
   AppContainer's strict handle checking could expose. It is fixed in the
   product here rather than deferred, because with it in place no request can
   complete on the Windows confined path.

Round 2 will be the first run that can reach `build`/`export` and the battery.

### The probe itself
The probe runs **only** on `windows-latest`; on any other platform it prints
`PROBE platform FAIL` and exits 0, and nothing in the repository imports it.
The controller dispatches the workflow and pastes the report into this entry
after the run — the Slice 1 verification is "every PROBE line OK", and this
Notes section is where that evidence lands.

Two things it does that the slice brief did not spell out, both deliberate:
the `AGENTCAD_CONFINE` payload also carries `"quotas": ["job_object"]` (the
design spec's own Data-shapes payload, and what PRD-006's
`sandbox_windows.build` already emits when the job exists) so the balloon's
`MemoryError` arrives with `details.denied` rather than unattributed — the
battery's pass criterion is still the exception type, so removing the key
changes what the run explains, not what it proves; and the scratch root's
ancestors are left ungranted on purpose, so a run also answers whether an
AppContainer's `SeChangeNotifyPrivilege` really bypasses traverse checks on
the way to a granted directory.

`make test` — the probe and the workflow are unchanged, no product code (a
standalone script and a CI job, neither imported by the package or the
suite); the one product change is `_meter.py`, covered by the two new
`tests/test_meter.py` cases. Targeted run on macOS:
`uv run pytest -q tests/test_meter.py tests/test_sandbox_windows.py
tests/test_sandbox_plan.py tests/test_quotas.py` → **103 passed, 5 skipped**.
The full `make test` count is the controller's to cite (4349 passed on the
PRD-006 tree, changelog 0238).
