# 0257 — PRD-006b slice 2: the Windows worker runs inside an AppContainer

- **Commit:** pending
- **Date:** 2026-08-19
- **Author:** Claude

## Summary

Windows kernel workers are now **confined**, not just capped: each one is
created inside an AppContainer built from a per-installation package SID with
**no capabilities**, reaching only the paths that carry an ACE for that SID.
The prototypes the Slice-1 probe proved on `windows-latest` moved into the
product (`kernel/sandbox_windows.py`), the client gained the one spawn hook
`CreateProcessW` needs, and the worker verifies the containment from **its own
token** so `confinement.status: "active"` stays measured rather than intended.

## Changes

- **`kernel/sandbox_windows.py`** — the AppContainer half:
  - `profile_name()` (`agentcad-worker-<sha256(resource_root())[:12]>`) and
    `AppContainerProfile.ensure()` (create, or derive on
    `HRESULT_FROM_WIN32(ERROR_ALREADY_EXISTS)`), carrying the `PSID` for the
    life of the process — every spawn passes it in `SECURITY_CAPABILITIES` —
    and its `S-1-15-2-…` string for the ACLs, the payload and health.
  - `acl_grant(path, sid, rights)` — `icacls … /grant *<SID>:(OI)(CI)M|RX`,
    **no `/T`**: the inheritable ACE reaches pre-existing children (measured,
    probe round 2, `acl.propagation=yes`). Never raises; returns
    `(ok, output tail)`.
  - `make_package_tree(tmp_dir, name)` — `<tmp>\Packages\<name>\AC\Temp` and
    friends, because the lowbox token redirects `%TEMP%` there and nothing
    else creates it (probe round 1 died on exactly this).
  - `ConfinedProcess` — `CreatePipe` ×3 with only the child ends inheritable,
    a two-entry `STARTUPINFOEX` attribute list
    (`SECURITY_CAPABILITIES` + `HANDLE_LIST`), `CreateProcessW` with
    `EXTENDED_STARTUPINFO_PRESENT | CREATE_SUSPENDED | CREATE_NO_WINDOW |
    CREATE_UNICODE_ENVIRONMENT`, `AssignProcessToJobObject` **while
    suspended**, then `ResumeThread`. Exposes exactly the `Popen` surface the
    client uses: text UTF-8 line-buffered `stdin`/`stdout`/`stderr` (via
    `msvcrt.open_osfhandle`), `pid`, `_handle`, `poll()`,
    `wait(timeout=None)` (raising `subprocess.TimeoutExpired`), `kill()`,
    `returncode`, plus `job_assigned`.
  - `build()` gained the confine path: profile → `RX` on `sys.base_prefix`,
    `sys.prefix`, `resource_root()` → `M` on the plan's write roots → payload
    `{"confinement": ["filesystem","network"], "appcontainer": {"sid","name"}}`
    and confinement `{"status":"active","mechanism":"appcontainer",
    "detail":{"posture":"local","sid":…}}` (an **intent**). Any failure is
    `off` with the step and the path in `warnings`; `confine=False` is `off`
    with the quotas untouched; no `userenv!CreateAppContainerProfile` or no
    `icacls` is `unsupported`, checked **before** the opt-out.
  - `WindowsBackend.spawn()` (the `ConfinedProcess`, or `None` when there is
    no profile — and `None` + a warning when the spawn itself failed, so a
    token problem degrades one worker instead of the server),
    `prepare_tmp_hook()` (package tree, then the `M` grant, at every spawn
    because `stop()` removes the directory), `attach()` now a no-op for a
    process it spawned itself (and it remembers `attached` so the psapi
    sampler still walks the job).
  - `supported()`: `userenv!CreateAppContainerProfile` resolves **and**
    `icacls` is on PATH.
- **`kernel/sandbox.py`** — `Backend.spawn()` (default `None`) and
  `Backend.prepare_tmp_hook()` (default no-op); `SandboxPlan.prepare_tmp()`
  calls the hook; `plan()` adds `USERPROFILE`/`APPDATA`/`LOCALAPPDATA` →
  private temp dir on win32 (`LOCALAPPDATA` is what the token derives `%TEMP%`
  from); `report()` copies the worker's `appcontainer`/`appcontainer_sid` into
  `confinement.detail` and words a failed **token read** as "could not read its
  own token" rather than "could not apply"; `supported()` answers for Windows
  through `sandbox_windows.supported()`.
- **`kernel/client.py`** — `_ensure_started` consults `backend.spawn(...)`
  before `subprocess.Popen` (nothing else about the spawn changed; a plan-free
  `KernelClient()` is byte-identical); `confinement_holds` on win32 requires
  `report["appcontainer"] is True` whenever the report carries the parent's
  declared facets.
- **`kernel/_preamble.py`** — on win32 the worker reads
  `TokenIsAppContainer`/`TokenAppContainerSid` off its own token and reports
  `appcontainer` / `appcontainer_sid`; a failure is
  `failures: [{"stage": "appcontainer", …}]` and `False`. Every HANDLE crosses
  as `c_void_p`, which is the probe's `token FAIL` (`OverflowError: int too
  long to convert` on `GetCurrentProcess()`'s pseudo-handle) fixed.
- **`kernel/denials.py`** — a `PermissionError` naming `WinError 10013`
  (`WSAEACCES`, what a capability-less token gets from Winsock) is the
  `network` denial when the facet is active. The socket-frame requirement
  stands.
- **`scripts/win_appcontainer_probe.py`** — now **imports** the profile, the
  ACL grant, the package tree and `ConfinedProcess` from the product module
  (its subclass adds only the probe's line-JSON protocol, reader threads and
  stderr tail), and the token child declares `argtypes` on every call.
- **`.github/workflows/ci.yml`** — the windows row moves to
  `expect_sandbox: active`.
- **Tests** — `tests/test_sandbox_plan.py`: the `windows` fixture stubs the
  AppContainer seams too (so the file never creates a profile or rewrites ACLs
  on the Windows runner), plus the payload/ACL order, the derive path, a
  failed grant, a missing write root, a failed profile, `unsupported`, the
  opt-out, the spawn hook end-to-end through a fake process (`Popen` made to
  raise), the downgrade when the worker says it is not in a container, and the
  `confinement_holds` win32 rules. `tests/test_denials.py`: `WinError 10013`.
  `tests/test_sandbox_windows.py`: rewritten as the live battery (network,
  public write, another worker's scratch, an inherited child, the private temp
  dir, a normal build + STEP export, the balloon, the report shape, and the
  `AGENTCAD_NO_SANDBOX` client whose job object still bites).
  `tests/test_prd006_acceptance.py`: AC3's Windows clause regraded for 006b,
  and the PRD is located by a folder-as-status glob.
- **Docs** — `docs/architecture.md`, `docs/deployment.md`, `docs/packages.md`.

## Files

- `agentcad/kernel/sandbox_windows.py` — profile, ACLs, package tree,
  `ConfinedProcess`, the confine path in `build()`, `spawn`/`prepare_tmp_hook`
  /`attach`, `supported()`, the new Win32 entry points
- `agentcad/kernel/sandbox.py` — the two Backend hooks, the win32 env
  additions, `prepare_tmp` calling the hook, `report()`/`supported()`
- `agentcad/kernel/client.py` — the spawn hook, the win32 `confinement_holds`
  rule
- `agentcad/kernel/_preamble.py` — the win32 token self-report
- `agentcad/kernel/denials.py` — `WSAEACCES`
- `scripts/win_appcontainer_probe.py` — imports the product plumbing; the
  token check's `c_void_p` handles
- `.github/workflows/ci.yml` — `expect_sandbox: active` on windows-latest
- `tests/test_sandbox_plan.py`, `tests/test_sandbox_windows.py`,
  `tests/test_denials.py`, `tests/test_prd006_acceptance.py`
- `docs/architecture.md`, `docs/deployment.md`, `docs/packages.md`

## CI round 1 (PR #24) — the one thing the container caught

Probe 28/28 OK; macOS and ubuntu green; windows portability `2 failed, 803
passed, 43 skipped`, both failures the same root cause and **not** in the
sandbox: `cli._is_path` was `"/" in project or project.startswith(".")`, and a
Windows absolute path (`C:\Users\...`) contains no forward slash — so
`agentcad check --project <abs path>` never recognised it as a path, never
appended the canonical project dir to `extra_writable`, the AppContainer never
got the write ACE, and every build/drawing row failed with
`PermissionError: [WinError 5]` on its `.cache/` write
(`tests/test_checks_cli.py::test_a_project_outside_the_usual_roots_is_still_writable`,
`tests/test_prd004_acceptance.py::test_ac5_the_three_exit_codes_and_a_report_that_validates`).

- `agentcad/cli.py` — `_is_path` now accepts either separator (`/`, `\\`,
  `os.sep`, `os.altsep`), a leading `.`, and a drive spec (`C:`, `C:\x`,
  `C:x`); `cmd_export` uses the shared helper instead of repeating the old
  idiom inline. `cmd_package_validate`/`cmd_publish` were re-read: both take
  `Path(args.path).expanduser().resolve()` unconditionally, so neither has a
  POSIX-only path test to fix.
- `tests/test_checks_cli.py` — `_is_path` pinned on both platforms:
  `C:\x\y`, `C:\`, `C:x`, `x\y`, `/x`, `/x/y`, `x/y`, `.`, `./x`,
  `../sibling` are paths; `rocketry`, `my-project`, `widget_2`, `a`, `""` are
  names.

This was a **real bug of PRD-004's**, latent on macOS/Linux because the
argument always carried a `/` there, and visible on Windows only once there
was a confinement to enforce the missing grant.

## Review fixes (whole-branch review, after CI round 2)

CI round 2: the windows portability job is **green under
`AGENTCAD_EXPECT_SANDBOX=active`** (the battery, the token self-report and the
report shape all measured on a real lowbox worker), ubuntu green, macOS green
but for the known `test_sketch_diagnostics` wall-clock flake. The review that
followed asked for these, and all of them landed:

- **An unconfined Windows worker no longer claims a denial**
  (`kernel/denials.py::active_facets`). The payload declares
  `confinement: [filesystem, network]` before the spawn is known to have
  worked, so a lowbox spawn that fell back to `Popen` left an ordinary worker
  labelling every `[Errno 13]` a sandbox denial. Parent-declared facets now
  count only when the worker's own token check said `True` — the mirror of
  `confinement_holds`, and the macOS seatbelt path (no `appcontainer` key) is
  untouched.
- **The package SID is no longer derivable by another local account.** A SID is
  a *hash of the profile name*, so a name that was only a hash of the install
  path could be re-derived — and a profile created for it — by any other user
  on the machine, while our ACEs (`M` on the projects dir, the work root and
  `<state>/publications/build`; `RX` on the venv and the whole app tree,
  `.git/` and `catalog/` included) are permanent and inheritable.
  `profile_name()` now mixes a per-installation salt persisted 0600 at
  `<state-dir>/appcontainer.salt` (`O_EXCL`, beside `secret.key`); a state dir
  that cannot be written is a **warning in health**, not a refusal, and says
  exactly what is lost. The name never leaves the server — the worker is told
  the SID.
- **The uninstall the docs promised now exists.** `AppContainerProfile.delete`
  (`DeleteAppContainerProfile` — there is no reliable in-box cmdlet) and
  `acl_revoke` (`icacls <root> /remove "*<SID>"`), with a two-step recipe in
  `docs/deployment.md`: the profile, then the ACEs, which outlive it.
- **`active` now needs the token flag *and* the SID** (`client.sid_mismatch`,
  used by both `KernelClient._ensure_started` and `sandbox.report`): a worker
  inside *some* AppContainer is no evidence for a plan that granted its roots
  to a different one. A worker that could not read its own SID is left alone —
  `_preamble` already filed that failure.
- **Handle hygiene in `ConfinedProcess`**: the three pipes are created inside
  the constructor's `try` (a second pipe failing used to leak the first),
  `SetHandleInformation` failing closes both ends, an
  `UpdateProcThreadAttribute` failure deletes the attribute list before
  re-raising (`_delete_attribute_list`), and `client._kill` calls
  `proc.close()` when the object has one, so a respawn releases the process
  handle and the pipe wrappers deterministically instead of at GC.
- **The worker is given an explicit `cwd`** — `resource_root()`, which is
  RX-granted and is what `python -m` puts on `sys.path[0]`. `CreateProcessW`
  otherwise inherits wherever the operator ran `agentcad` from, a directory the
  container has no ACE for.
- **`icacls` exit 0 is not success**: `Failed processing N files` (N > 0) is now
  read as a failure (`_icacls_result`), because `icacls` reports a per-path
  outcome and exits 0 having said it.
- **`AGENTS.md`** — the two false statements (`expect_sandbox` "empty on
  Windows", "Windows reports `unsupported`") are corrected, and a five-point
  Windows AppContainer gotcha sits beside the launcher-stub one: the spawn
  hook, the suspended start, `%TEMP%` → `Packages\<name>\AC\Temp`, `icacls`
  `*<SID>` syntax and its exit-0 trap, and the salted name.
- **`tests/test_prd006_acceptance.py`** no longer calls the live
  `sandbox_windows.build()`: on the windows-latest job (and on a contributor's
  machine) it would create a real profile and rewrite the ACLs of `sys.prefix`
  and the checkout. The seams are stubbed and the live grading is
  `tests/test_sandbox_windows.py`, which also gained `_requires_container()` so
  the three unconditional `PermissionError` asserts skip on an unconfined box
  and stay hard under `AGENTCAD_EXPECT_SANDBOX=active`.
- **`docs/roadmap.md`** points at `prd/in-progress/`, and the PRD's own
  `Status:` says in-progress with the probe/CI evidence.
- New tests: the unconfined-worker facet rule (`tests/test_denials.py`), the
  salt (stable, per-installation, and the unwritable-state-dir warning), the
  SID mismatch, the `icacls` exit-0 trap and `acl_revoke`, and
  `AppContainerProfile.delete`.

## Notes

- **`make test` — 4366 passed, 42 skipped in 523.97s** (macOS, this branch,
  before the review fixes; re-run after the CI-round-1 fix:
  `tests/test_checks_cli.py tests/test_prd004_acceptance.py
  tests/test_sandbox_plan.py` — 151 passed). After the review fixes:
  **`make test` — 4387 passed, 42 skipped in 675.81s** (macOS, the review-fixed tree; the implementer's own run, read by the controller after the implementer's harness stalled).
  Everything Windows in that run is the *stubbed* plan shape: **the live
  evidence — the AppContainer battery, the token self-report, the report shape
  under `AGENTCAD_EXPECT_SANDBOX=active` — lands with the next
  windows-probe/PR CI run**, and until it does this entry claims nothing about
  a running Windows worker.
- **Honesty, twice.** `build()` only ever *intends*; `client.confinement_holds`
  and `sandbox.report` turn that into `active` only while the worker's own
  `TokenIsAppContainer` agrees. A spawn that fell back to `Popen` therefore
  reports `off` rather than silently claiming the container.
- **A residual, stated rather than papered over.** The package SID is **per
  installation** (per-worker profiles are out of scope, design Decision 2), so
  the ACE one plan puts on its private temp dir is an ACE for every worker of
  the same install: two live workers are separated by DAC alone, unlike macOS's
  per-worker seatbelt profile and Linux's per-worker Landlock ruleset. The
  Windows test asserts what the container really does prove — a directory the
  installation granted to nobody — and says so in its docstring.
- **A missing write root costs its grant, not the confinement** (the
  `landlock_root` precedent, review I2): the container is in force and
  narrower than intended, so `off` would be the overstatement in reverse. A
  grant that *fails* on a path that exists is still `off`.
- `ci.yml`'s windows row now expects `active`, which is what makes a silent
  degradation red. If the first CI round needs to be exploratory rather than
  gating, that one line is the switch.
