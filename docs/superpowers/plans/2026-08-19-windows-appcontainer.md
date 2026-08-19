# Windows AppContainer (PRD-006b) — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Windows kernel workers run inside an AppContainer (no network, writes
only under the granted roots + the private temp dir, inherited by children),
the worker proves it from its own token, `/api/health` reports
`confinement: active / appcontainer` on `windows-latest`, and PRD-006's AC3
loses its Windows asterisk — without touching the job-object quota tier.

**Architecture (one paragraph):** `sandbox_windows.py` gains an
`AppContainerProfile` (create-or-derive the per-installation SID), an
`acl_grant()` over `icacls`, and a `ConfinedProcess` (ctypes `CreateProcessW`
+ `STARTUPINFOEX` with `SECURITY_CAPABILITIES`, suspended start, job
assignment, resume) exposing the `Popen` subset the client uses; the
`Backend` protocol gains `spawn(argv, env)` and the client consults it before
`subprocess.Popen`; the preamble self-reports `TokenIsAppContainer`; the
parent declares the two facets so `details.denied` is unchanged. Because no
Windows box exists here, **Slice 1 is a standalone probe** run through a
dispatch-only workflow until it proves the negative (OCCT imports and builds
inside the container) and the positives; only then does Slice 2 implement.

**Tech stack:** Python 3.12 `ctypes` (kernel32/userenv/advapi32/psapi),
`icacls`, GitHub Actions `windows-latest`, pytest.

**Spec:** [`docs/superpowers/specs/2026-08-19-windows-appcontainer-design.md`](../specs/2026-08-19-windows-appcontainer-design.md) — "Decision N" below refers to it.

## Global constraints (encode these in every slice)

- **Nothing here executes locally.** The controller pushes and runs
  `.github/workflows/windows-probe.yml` (`gh workflow run windows-probe.yml
  --ref prd-006b-windows-appcontainer`) and feeds the log back. Write
  everything so that one run answers as many questions as possible: every
  probe step prints `PROBE <step> OK|FAIL <detail>` and never aborts the run
  on the first failure.
- **No change to the job-object quota tier** beyond the suspended start;
  `tests/test_sandbox_windows.py`'s existing tests keep passing.
- **Honesty (006 Decision 8):** `confinement.status == "active"` only from the
  worker's own `appcontainer: true` self-report; a failed profile/ACL/spawn is
  `off` + `warnings`; below Windows 8 `unsupported`.
- **`KernelClient()` with no args stays byte-identical**; the macOS/Linux
  spawn path is untouched (`spawn()` returns `None` there).
- **`AGENTCAD_NO_SANDBOX=1`** opts out of confinement, not of quotas.
- **Text I/O names `encoding="utf-8"`**; the environment block handed to
  `CreateProcessW` is UTF-16 (`CREATE_UNICODE_ENVIRONMENT`).
- **Every commit stages a `docs/changelog/NNNN-<slug>.md`** (next free
  0240) citing `make test — N passed` (macOS, run by the controller) and the
  probe/CI evidence; subagents never run `git`, `uv sync`, `pip install`.

## Slice map

| # | Slice | Delivers | Verified where |
|---|---|---|---|
| 1 | Probe loop | `scripts/win_appcontainer_probe.py` + `.github/workflows/windows-probe.yml`; iterated until the report is all-OK | windows-latest (dispatch) |
| 2 | Implementation | `sandbox_windows.py` (profile, ACLs, `ConfinedProcess`, `spawn`), `sandbox.py` hook + report, `client.py` one line + `confinement_holds`, `_preamble.py` token self-report, tests | macOS (stubbed seams) + windows-latest |
| 3 | Docs, CI gate, close-out prep | `ci.yml` `expect_sandbox: active` on windows, docs, PRD-006 AC3 asterisk removed, PRD-006b header | all |

---

## Slice 1 — the probe loop

### Files
- Create: `scripts/win_appcontainer_probe.py`, `.github/workflows/windows-probe.yml`

### The probe (one file, standalone, stdlib + ctypes only; it imports
`agentcad` only to find `resource_root()` and to spawn the real worker module)
Steps, each printing `PROBE <name> OK|FAIL <detail>` and continuing:
1. `userenv` — resolve `CreateAppContainerProfile`, `DeriveAppContainerSidFromAppContainerName`,
   `DeleteAppContainerProfile`; `advapi32.ConvertSidToStringSidW`; print the OS
   build (`sys.getwindowsversion()`).
2. `profile` — create `agentcad-probe-<sha8>` (or derive on
   `ERROR_ALREADY_EXISTS`); print the SID string.
3. `acl` — make a scratch `project` dir with a **pre-existing** `.cache/sub`
   inside, then `icacls project /grant "*<SID>:(OI)(CI)M"`; read ACEs for
   `sys.base_prefix`, `sys.prefix`, `resource_root()` with `(OI)(CI)RX`; print
   each icacls exit code + output tail.
4. `spawn` — `ConfinedProcess` prototype: pipes, `STARTUPINFOEX` with
   `SECURITY_CAPABILITIES` (no capabilities) + `HANDLE_LIST`, `CreateProcessW`
   of `[sys.executable, "-u", "-m", "agentcad.kernel.worker"]` with env
   `{**os.environ, TEMP/TMP/USERPROFILE/APPDATA/LOCALAPPDATA: scratch_tmp,
   PYTHONDONTWRITEBYTECODE: 1, AGENTCAD_CONFINE: json({"posture":"local",
   "confinement":["filesystem","network"]})}`, `CREATE_SUSPENDED |
   EXTENDED_STARTUPINFO_PRESENT | CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT`,
   then a job object (`JOB_OBJECT_LIMIT_PROCESS_MEMORY` 1 GiB + `ACTIVE_PROCESS` 64 +
   `KILL_ON_JOB_CLOSE`) assigned, `ResumeThread`. Print `pid`, and after 1 s
   `GetExitCodeProcess`.
5. `token` — inside the child, via a `ping` request: the probe sends
   `{"id":1,"method":"ping","params":{}}` and ALSO spawns a second confined
   child running `python -c` that prints `GetTokenInformation(TokenIsAppContainer)`
   and `TokenAppContainerSid` (string) — so the token check is proven
   independently of the worker.
6. `ping/build/export` — `ping` (print stderr tail on failure — the first
   denied path is the prize), `build` of a Box with `mesh_path` under
   `project/.cache/sub/box.acm` (the pre-existing subdir: tests ACE
   propagation), `export` STEP to `project/exports/box.step`.
7. `battery` — part scripts through the worker: `socket.create_connection(("1.1.1.1",80),timeout=3)`
   → expect `PermissionError [WinError 10013]`; `open(r"C:\Users\Public\pwned","w")`
   → expect PermissionError; `open(os.path.join(other_scratch_tmp,"x"),"w")`
   (a second scratch dir NOT ACL'd) → PermissionError; `subprocess.run([sys.executable,"-c","import socket;socket.create_connection(('1.1.1.1',80),timeout=3)"])`
   → non-zero (inheritance); `bytearray(2<<30)` under the 1 GiB job limit →
   MemoryError. Print each outcome verbatim (exception type + message + the
   worker's `details`).
8. `cleanup` — terminate, close job (kills survivors), `DeleteAppContainerProfile`
   for the probe's own profile name (the real one in Slice 2 is never deleted).
Exit 0 always; the controller reads the report.

### Workflow
```yaml
name: Windows AppContainer probe
on: { workflow_dispatch: {} }
jobs:
  probe:
    runs-on: windows-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with: { enable-cache: true, cache-dependency-glob: uv.lock }
      - run: uv sync --locked
      - run: uv run python scripts/win_appcontainer_probe.py
        shell: pwsh
```

### Tasks
- [ ] **Step 1:** write the probe + workflow; controller commits (changelog 0240), pushes, dispatches, pastes the log back.
- [ ] **Step 2:** iterate (expected rounds: first denied DLL/dir, ACE propagation, env vars) until every PROBE line is OK; record the final report in the changelog Notes.

### Verification
A dispatch run whose report has every step OK, including `build`/`export` inside the container and all battery denials.

---

## Slice 2 — the implementation

### Files
- Modify: `agentcad/kernel/sandbox_windows.py` (profile, ACL, `ConfinedProcess`, `build()` confine path, `WindowsBackend.spawn`, `prepare_tmp` ACL hook), `agentcad/kernel/sandbox.py` (`Backend.spawn` default, `plan()` passes the tmp-dir ACL hook / win32 env additions, `report()` win32 mechanism), `agentcad/kernel/client.py` (the one-line spawn hook; `confinement_holds` win32 rule), `agentcad/kernel/_preamble.py` (win32 token self-report), `tests/test_sandbox_plan.py`, `tests/test_sandbox_windows.py`, `tests/test_protocol_ids.py` (if it pins `confinement_holds` cases)

### The shapes
```python
# sandbox.py
class Backend:
    def spawn(self, argv: list[str], env: dict[str, str] | None):
        """Launch the worker yourself (AppContainer needs CreateProcessW); None → the client uses subprocess.Popen."""
        return None
    def prepare_tmp_hook(self, tmp_dir: str) -> None: ...   # win32: acl_grant(tmp_dir, sid, "M")

# sandbox_windows.py
class AppContainerProfile:            # name, sid (PSID handle), sid_str
    @classmethod
    def ensure(cls, name: str) -> "AppContainerProfile"      # create or derive; raises OSError with the HRESULT
def profile_name() -> str             # "agentcad-worker-" + sha256(str(resource_root()))[:12]
def acl_grant(path: str, sid_str: str, rights: str) -> tuple[bool, str]   # icacls; (ok, output tail)
class ConfinedProcess:                # stdin/stdout/stderr (text, utf-8, line-buffered), pid, _handle, poll(), wait(timeout=None), kill(), returncode, job_assigned
    def __init__(self, argv, env, *, sid, job: int | None, cwd=None): ...
def build(argv, write_roots, quotas, posture, server_pid, *, confine=True, pool_size=1):
    # confine=True and supported(): profile.ensure → acl_grant(read roots RX, write roots M) → env additions
    #   (TEMP/TMP/USERPROFILE/APPDATA/LOCALAPPDATA → set by plan per tmp; PYTHONDONTWRITEBYTECODE) → payload gains
    #   "confinement": ["filesystem","network"], "appcontainer": {"sid", "name"} → confinement {"status":"active",
    #   "mechanism":"appcontainer","detail":{"posture":"local","sid":...}} (INTENDED; the client refines from the report)
    # any failure → confinement {"status":"off","mechanism":None,"detail":{"reason":...}} + warnings; quotas unchanged
    # confine=False → today's path (unsupported→ now "off" with reason "AGENTCAD_NO_SANDBOX"); below Win8 → "unsupported"
class WindowsBackend:
    def spawn(self, argv, env): return ConfinedProcess(argv, env, sid=self.profile.sid, job=self.job) if self.profile else None
    def attach(self, proc): no-op when getattr(proc, "job_assigned", False) else today's AssignProcessToJobObject
def supported() -> bool               # userenv.CreateAppContainerProfile resolves and icacls exists

# _preamble.py (win32)
REPORT["appcontainer"] = bool(GetTokenInformation(TokenIsAppContainer=29)); REPORT["appcontainer_sid"] = str or None
# client.py
def confinement_holds(report): ... on win32: if the plan intended appcontainer (report carries "confinement") → report.get("appcontainer") is True
```

### Tasks
- [ ] **Step 1:** `tests/test_sandbox_plan.py` (run everywhere, stubbed seams: `_userenv_*`, `acl_grant`, `ConfinedProcess`): win32 plan `confine=True` → payload has `confinement` + `appcontainer`, confinement intended `active/appcontainer`; an `acl_grant` failure → `off` + warning naming the path; `confine=False` → `off`, quotas on; `supported()` False when `userenv` lacks the symbol → `unsupported`; the client calls `backend.spawn` before `Popen` (fake process object; assert `Popen` not called); `confinement_holds` win32 requires `appcontainer: True`.
- [ ] **Step 2:** implement; the `ConfinedProcess` is lifted from the probe's proven prototype.
- [ ] **Step 3:** `tests/test_sandbox_windows.py`: battery per the spec's Testing section (network, write outside, write into another plan's tmp, inheritance, normal build + STEP export, report shape, `NO_SANDBOX` → off + job-object balloon still `denied == "memory"`), plus `_preamble` self-report (`client.sandbox_report["appcontainer"] is True`).
- [ ] **Step 4:** controller commits (changelog 0241), pushes; CI round(s) on the PR until the windows job is green; `make test` on macOS cited.

---

## Slice 3 — docs, CI gate, close-out prep

### Files
- Modify: `.github/workflows/ci.yml` (`expect_sandbox: active` on the windows row), `docs/deployment.md` (Windows row of the per-OS table; the profile name + removal one-liner `Remove-AppxPackage`? no — `(New-Object -ComObject …)`: document `Get-AppContainerProfile`/PowerShell `Remove-AppContainerProfile` if available, else the registry path under `HKCU\Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion\AppContainer\Mappings\<SID>`), `docs/architecture.md` (per-OS contract table), `AGENTS.md` + `CLAUDE.md` (Windows gotchas: launcher child inherits the lowbox token; icacls SID syntax; suspended start; `spawn()` hook), `docs/agent-api.md` (nothing new — verify), the PRD-006 `completed/` header (AC3 Windows clause closed by 006b), the PRD-006b header (status), `docs/roadmap.md` (006b row), `tests/test_prd006_acceptance.py` (AC3 on win32 now expects `active`/`appcontainer`).

### Tasks
- [ ] **Step 1:** edits above; `make test` cited; controller commits (changelog 0242), pushes; PR CI green on all three; merge; close-out commit on main (PRD-006b → completed, roadmap, changelog 0243).

## Self-review against the spec
Decision 1 → Slice 2 (`spawn`, `ConfinedProcess`); Decision 2 → Slice 1 (ACL verification) + Slice 2; Decision 3 → Slice 2 (`_preamble`, `confinement_holds`, `report`); Decision 4 → Slice 2 (`ConfinedProcess` suspended start); Decision 5 → Slice 1; Decision 6 → scope as listed. ✓
