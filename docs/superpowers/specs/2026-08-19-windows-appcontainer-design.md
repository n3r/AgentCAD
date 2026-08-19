# PRD-006b — Windows AppContainer confinement: design

- **Date:** 2026-08-19
- **PRD:** [PRD-006b](../../prd/in-progress/PRD-006b-windows-appcontainer.md)
- **Builds on (completed):** PRD-006 — the `sandbox.plan()` seam and the
  `Backend` protocol (`kernel/sandbox.py`), the Windows backend with its job
  object and psapi sampler (`kernel/sandbox_windows.py`), the worker preamble
  and its `ping` self-report (`kernel/_preamble.py`), the parent-declared
  confinement facets in the `AGENTCAD_CONFINE` payload (006's F1, built for
  the seatbelt — the worker cannot observe a confinement its parent applied),
  `confinement_holds` (`kernel/client.py`), `denials.classify`, and the CI
  honesty gate (`AGENTCAD_EXPECT_SANDBOX`).
- **Constraint that shapes everything:** there is no Windows machine in this
  environment. Every observation comes from `windows-latest` on GitHub
  Actions, so the design front-loads a **probe workflow** (dispatch-only,
  minutes per round) and lands the implementation only once the probe has
  shown containment and the load-bearing negative (OCCT imports and builds
  inside the container).
- **Ruling ledger:** decisions marked **[ruling]** were taken by the
  orchestrator under the founder's `/goal`; each carries its reason so the
  founder can overturn it.

## The one-paragraph version

A Windows kernel worker will be created **inside an AppContainer** — a
per-installation package SID (`CreateAppContainerProfile`, derived again on
later runs), no capabilities (the absence of `INTERNET_CLIENT` *is* the
network denial), and the write roots PRD-006 already computes made reachable
by an ACE for that SID (`icacls … /grant *S-1-15-2-…`), plus read ACEs on
the interpreter, the venv and the app tree that an AppContainer cannot
otherwise see. Because CPython's `subprocess` cannot pass
`PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES`, the Windows backend gains a
`spawn()` hook that launches the worker through a small ctypes
`CreateProcessW`/`STARTUPINFOEX` wrapper and returns a process object with
exactly the `Popen` surface the client uses; the client gains one line
(`proc = backend.spawn(...) or subprocess.Popen(...)`). The worker proves the
confinement **itself** — `GetTokenInformation(TokenIsAppContainer)` on its own
token in the preamble — so `confinement.status: "active"` stays measured, never
intended, and the `script_error` + `details.denied` contract is unchanged
(the parent declares the two facets the way the seatbelt does). Quotas (job
object + supervisor) are untouched except that the suspended start lets the
job be assigned before the first instruction.

## Decision 1 — A `spawn()` hook on the backend, one line in the client **[ruling]**

`subprocess.Popen(startupinfo=…)` only carries `PROC_THREAD_ATTRIBUTE_HANDLE_LIST`
(`lpAttributeList={"handle_list": …}`); there is no way to add
`SECURITY_CAPABILITIES`. So:

- `Backend.spawn(argv, env) -> proc | None` (default `None`). The Windows
  backend returns a `ConfinedProcess` when AppContainer is planned; every other
  backend returns `None`.
- `client._ensure_started`: `self._proc = (self._plan.backend.spawn(self._argv, env)
  if plan and backend else None) or subprocess.Popen(...)`. Nothing else in
  the client changes: `_drain_stdout/_drain_stderr` iterate `proc.stdout/
  proc.stderr`, `_request_locked` writes `proc.stdin`, `_kill` calls
  `proc.kill()`/`proc.wait(timeout=5)`, the supervisor reads `proc.pid`/`proc._handle`,
  `poll()`/`returncode` feed `explain_exit`.
- `ConfinedProcess` (in `sandbox_windows.py`): `CreatePipe` ×3 with
  inheritable child ends (`SECURITY_ATTRIBUTES.bInheritHandle=1`, the parent
  ends marked non-inheritable via `SetHandleInformation`), `STARTUPINFOEX` with
  `STARTF_USESTDHANDLES` and a two-entry attribute list
  (`PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES = 0x00020009`,
  `PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002` naming exactly the three
  child pipe ends so no other handle leaks), `CreateProcessW(None, cmdline,
  None, None, TRUE, EXTENDED_STARTUPINFO_PRESENT | CREATE_SUSPENDED |
  CREATE_NO_WINDOW, envblock, cwd, &siex, &pi)`, then
  `AssignProcessToJobObject` (the job object PRD-006 already opens),
  `ResumeThread`, `CloseHandle(hThread)`. The parent wraps its pipe ends with
  `msvcrt.open_osfhandle` + `open(fd, "w"/"r", encoding="utf-8", buffering=1)`
  so `.stdin/.stdout/.stderr` behave like `Popen(text=True, encoding="utf-8",
  bufsize=1)`. `pid`, `_handle`, `poll()` (`GetExitCodeProcess`, 259 = still
  active), `wait(timeout)` (`WaitForSingleObject`), `kill()`
  (`TerminateProcess(handle, 9)`), `returncode`. The command line is built
  with `subprocess.list2cmdline(argv)`; the environment block is the sorted
  `KEY=VALUE\0…\0\0` UTF-16 block (`CREATE_UNICODE_ENVIRONMENT`).

## Decision 2 — The profile, the SID, the ACLs **[ruling]**

- **One profile per installation**: name `agentcad-worker-<sha256(resource_root())[:12]>`
  (AppContainer names are ≤ 64 chars, `[A-Za-z0-9._-]`), display name
  "AgentCAD kernel worker". `CreateAppContainerProfile(name, display, desc,
  NULL, 0, &sid)`; `HRESULT_FROM_WIN32(ERROR_ALREADY_EXISTS)` (0x800700B7) →
  `DeriveAppContainerSidFromAppContainerName`. The SID string
  (`ConvertSidToStringSidW`) is what the ACLs and the report carry. Never
  deleted on the worker path (profile creation is not free, and concurrent
  instances share it); the docs name the PowerShell one-liner to remove it.
- **Capabilities: none.** `SECURITY_CAPABILITIES{AppContainerSid=sid,
  Capabilities=NULL, CapabilityCount=0}`. No `INTERNET_CLIENT`, no
  `PRIVATE_NETWORK_CLIENT_SERVER` → outbound and loopback connects fail with
  `WSAEACCES` (`PermissionError: [WinError 10013]`), which `denials.classify`
  labels `network` (PermissionError + a socket frame) once the parent has
  declared the facet.
- **ACLs via `icacls`, not hand-built DACLs**: `icacls <path> /grant
  "*<SID>:(OI)(CI)<rights>"` — no `/T`. Setting an inheritable ACE on a
  directory through `SetNamedSecurityInfo` (what `icacls` calls) makes Windows
  propagate it to the existing children whose DACLs have inheritance enabled
  (the default), so a pre-existing `project/.cache/` is covered without a tree
  walk; the probe verifies exactly that (a write into a pre-existing subdir)
  before the implementation relies on it, and `/T` on the projects dir is the
  documented fallback (slow only on first run).
  - Write roots: the plan's `write_roots` (projects dir, examples, work root,
    `<state>/publications/build`) and the private temp dir → `(OI)(CI)M`
    (modify: create/write/delete, no WRITE_DAC).
  - Read roots: `sys.base_prefix`, `sys.prefix`, `resource_root()` (the app
    tree; the editable install), the uv cache only if the venv symlinks into
    it (probe checks) → `(OI)(CI)RX`. `C:\Windows` and `Program Files`
    already carry an `ALL APPLICATION PACKAGES` RX ACE; a base interpreter
    under `Program Files` is therefore readable without our ACE, and an
    `icacls` failure there is a warning, not a refusal.
  - Applied once per plan (`build()` time; the roots are known) and for the
    private temp dir at `prepare_tmp()`; a respawn reuses both. Each `icacls`
    call is ~50–100 ms; a plan runs ≤ 8 of them.
- **Child environment** (added to the plan's env on win32): `USERPROFILE`,
  `APPDATA`, `LOCALAPPDATA`, `TEMP`, `TMP` → the private temp dir (CPython's
  `expanduser`, ezdxf's cache and anything that reads `%LOCALAPPDATA%` land
  somewhere writable), `PYTHONDONTWRITEBYTECODE=1` as today.

## Decision 3 — Honesty: the worker proves it from its own token **[ruling]**

- `_preamble.apply_from_env()` on `win32`: `OpenProcessToken(GetCurrentProcess(),
  TOKEN_QUERY)` → `GetTokenInformation(TokenIsAppContainer = 29)` → a DWORD;
  `REPORT["appcontainer"] = bool(value)`; `REPORT["appcontainer_sid"]` from
  `TokenAppContainerSid = 31` (`ConvertSidToStringSidW`) so the report names
  the SID the parent expects. Failures → `failures: [{"stage": "appcontainer",
  …}]`.
- The parent declares `payload["confinement"] = ["filesystem", "network"]`
  only when it really spawned through the AppContainer path (the macOS
  precedent), so `details.denied` works on Windows with no change to
  `denials.classify`.
- `client.confinement_holds(report)` on `win32`: when the plan intended
  AppContainer, `report.get("appcontainer") is True` and the SID matches;
  otherwise the claim clears exactly as a Linux `landlock` stage failure does.
- `sandbox.report()`: `confinement: {"status": "active", "mechanism":
  "appcontainer", "detail": {"posture": "local", "sid": "S-1-15-2-…"}}` from
  the live report; `off` + `warnings` when the profile, an ACL or the spawn
  failed; `unsupported` below Windows 8 (`CreateAppContainerProfile` absent
  from `userenv.dll`) or when `icacls` is missing.
- `sandbox.supported()` on `win32`: `True` iff `userenv.CreateAppContainerProfile`
  resolves. `AGENTCAD_NO_SANDBOX=1` → `confine=False`: plain `subprocess.Popen`,
  no ACLs, confinement `off`, **job-object quotas still on**.

## Decision 4 — The job object is assigned before the first instruction

The suspended start (`CREATE_SUSPENDED` → `AssignProcessToJobObject` →
`ResumeThread`) closes the 006 race where the worker ran its first
milliseconds outside the job; `attach()` becomes a no-op for a process the
backend spawned itself (it checks `proc.job_assigned`). The psapi sampler and
`_job_process_ids` (the launcher finding from 006) are unchanged — the venv
`python.exe` launcher's child inherits the lowbox token *and* the job.

## Decision 5 — Probe first, in CI **[ruling]**

`.github/workflows/windows-probe.yml` (dispatch-only, `windows-latest`,
`uv sync --locked`) runs `scripts/win_appcontainer_probe.py`, which does the
whole experiment standalone — profile, ACLs, `ConfinedProcess` spawn of the
**real** worker, `ping` + `build` + `export`, the malicious battery, a
write into a pre-existing subdir, the token self-check — and prints one
structured report (`PROBE <step> OK|FAIL <detail>`). The implementation slice
starts only when the probe shows the negative (OCCT imports and builds
inside the container) and the positives. The probe script and workflow stay
in the repo as the Windows dev loop (the `make test-linux` analogue);
they cost nothing on PRs.

## Decision 6 — Scope

In: FR1–FR6 of the PRD, the probe loop, the tests and docs, PRD-006's AC3
Windows clause. Out: Windows Sandbox/Hyper-V, a hosted Windows posture,
per-worker profiles, any change to the job-object tier beyond the suspended
start, `DeleteAppContainerProfile` plumbing (documented command instead).

## Architecture

```
kernel/sandbox.py             Backend.spawn(argv, env) -> proc|None (default None); report(): win32 branch
kernel/sandbox_windows.py     AppContainerProfile (create/derive/sid string), acl_grant(path, sid, rights) via icacls,
                              ConfinedProcess (CreateProcessW + STARTUPINFOEX), build(): confine path,
                              WindowsBackend.spawn()/prepare_tmp hook/attach no-op when self-spawned
kernel/_preamble.py           win32: TokenIsAppContainer / TokenAppContainerSid self-report
kernel/client.py              one line: spawn hook before subprocess.Popen; confinement_holds win32 rule
scripts/win_appcontainer_probe.py   the standalone experiment (the Windows dev loop)
.github/workflows/windows-probe.yml dispatch-only probe job
tests/test_sandbox_windows.py       the battery + negative + report shape (windows-latest)
tests/test_sandbox_plan.py          win32 plan/spawn/holds tests with stubbed Win32 seams (run everywhere)
```

## Data shapes

```jsonc
// AGENTCAD_CONFINE (win32, confine=True)
{"posture": "local", "quotas": ["job_object"], "confinement": ["filesystem", "network"],
 "appcontainer": {"sid": "S-1-15-2-…", "name": "agentcad-worker-1a2b3c4d5e6f"}}
// ping report (worker)
{"posture": "local", "rlimits": [], "appcontainer": true, "appcontainer_sid": "S-1-15-2-…",
 "confinement": ["filesystem", "network"], "failures": []}
// health
{"sandbox": {"status": "active", "mechanism": "appcontainer", "posture": "local",
  "confinement": {"status": "active", "mechanism": "appcontainer", "detail": {"posture": "local", "sid": "S-1-15-2-…"}},
  "quotas": {"status": "active", "mechanism": "job_object+supervisor", "limits": {...}}, "warnings": []}}
```

## Testing

- Everywhere (`tests/test_sandbox_plan.py`, stubbed seams): the win32 plan
  with `confine=True` carries the `appcontainer` payload and facets; the
  `spawn()` hook is consulted by the client before `Popen` (stub returns a
  fake process); `confinement_holds` on win32 requires `appcontainer: True`;
  `report()` shape; `AGENTCAD_NO_SANDBOX` → `off` + quotas on.
- Windows CI (`tests/test_sandbox_windows.py`, portability): the battery —
  `socket.create_connection(("1.1.1.1", 80))` → `script_error`, `denied ==
  "network"`; `open(r"C:\Users\Public\pwned", "w")` and a write into another
  plan's private temp → `denied == "filesystem"`; a normal build + STEP
  export succeed (AC2); `sandbox.report()["status"] == "active"` with
  mechanism `appcontainer` under `AGENTCAD_EXPECT_SANDBOX=active` (AC3);
  `AGENTCAD_NO_SANDBOX=1` → `off` and the job-object balloon still yields
  `denied == "memory"` (AC4); the 006 job-object tests keep passing.
- `ci.yml`: `expect_sandbox: active` on the windows row.

## Risks and residuals

- **OCCT/CPython reads the probe did not exercise** (a DLL resolved from an
  unexpected dir): the probe prints the first denied path; the fix is one
  more read ACE. AC2 is the gate.
- **`icacls` on a directory the user cannot change** (the base interpreter
  under `Program Files`): AAP RX normally covers it; otherwise confinement
  reports `off` with the path in `warnings`.
- **Pre-existing children and ACE propagation**: verified by the probe
  before the implementation relies on it; if propagation does not hold,
  `/T` on the projects dir is the fallback (slow only on first run).
- **CI round trips**: each probe run is ~4–6 minutes; budget ~5 rounds.
- **Job objects inside an AppContainer** — `AssignProcessToJobObject` on a
  suspended lowbox process is documented to work (nested jobs since Win8);
  the probe asserts the commit-limit balloon still fails.
