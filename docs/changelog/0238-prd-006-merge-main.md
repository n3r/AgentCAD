# 0238 — PRD-006 merge with main: renumbered changelogs, PRD-007's variant-build subtree granted, conflicts resolved

- **Commit:** pending
- **Date:** 2026-08-19
- **Author:** Nikita Fedorov (orchestrated; Claude Fable 5)

## Summary
`origin/main` (PRD-007 share links, PR #20; PRD-031a marketplace catalog,
PR #21) merged into `prd-006-sandboxing-quotas` before the PR merges. The
branch's changelogs `0213`–`0220` collided with 007's and became
`0230`–`0237` (the b24ef66 precedent); the one real seam between the two
features is closed: PRD-007 builds customizer variants through the **shared
kernel pool** into `<state-dir>/publications/build/`, and PRD-006 lets a
worker write only inside granted roots — so that exact subtree is now a
write root (never the state dir itself, never `~/.agentcad`, so `secret.key`
and `auth/` stay unreadable under the hosted posture).

## Changes
- `agentcad/cli.py::_writable_roots` — grants `state_dir()/publications/build`
  (created, warn-on-OSError like the projects dir); `_refuse_state_dir_in_a_write_root`
  is unaffected (a child of the state dir is not a container of it) — pinned
  by two new `tests/test_deploy_config.py` cases; `tests/test_sandbox_plan.py`
  and a second real-seatbelt client in `tests/test_sandbox.py` prove the
  subtree is writable and `<state>/secret.probe` is denied (`denied ==
  "filesystem"`).
- Conflict resolutions: `core/model.py` (`DiskBudgetError` + 007's
  `ServiceUnavailableError`, both kept), `server/app.py` (507 + 503),
  `kernel/worker.py` (both imports), `compose.yaml` (007's
  `AGENTCAD_KERNEL_POOL_SIZE: "2"` kept — 006's `RLIMIT_NPROC` headroom
  scales by it — plus 006's commented quota knobs), `docs/roadmap.md` (main's
  DONE rows for 007/031a, 006's rows), `AGENTS.md` (007's, 031a's and 006's
  gotcha sections all kept).
- `agentcad/kernel/sandbox_linux.py` — the Linux plan no longer declares
  `confinement` facets from intent (a failed in-worker stage must not inherit
  a parent-declared claim; the worker's own `landlock_abi`/`seccomp` report
  is the only source on Linux; macOS still declares its seatbelt facets
  because the worker cannot observe them). Two doc sentences aligned with F1.
- `docs/deployment.md` — the PRD-007 section's "peak memory is uncapped until
  PRD-006" paragraph rewritten to what is now bounded and what is still open
  (a disk budget for the variant cache itself).
- `tests/test_sandbox_plan.py` — the two tests that glob the shared temp for
  `agentcad-worker-*` now use a private temp root: under xdist a sibling test
  process creating or releasing its own scratch between the two globs was a
  race (seen once as `1 failed` in the merged-tree run below).

### CI round 1 (PR #22: `2 failed, 797 passed, 43 skipped` on `windows-latest`)

- **`agentcad/kernel/sandbox_windows.py` — the supervisor sampled a launcher
  stub.** `test_the_supervisor_can_sample_a_windows_worker` read 4 091 904
  bytes (~3.9 MB) for a worker with build123d imported. Root cause: on Windows
  a venv's `python.exe` (uv-managed ones included) is a **launcher** that
  starts the real interpreter as a *child* process, so `Popen(sys.executable…)`
  hands back the stub's handle and `GetProcessMemoryInfo` measures the stub.
  The quota tier was never affected — the child inherits the job object, which
  is why `denied == "memory"` passed in the same run. `WindowsBackend.rss_bytes`
  now walks the job's own process list
  (`QueryInformationJobObject(JobObjectBasicProcessIdList = 3)`, a
  `JOBOBJECT_BASIC_PROCESS_ID_LIST` sized for 256 pids and grown once on
  `ERROR_MORE_DATA = 234`), opens each pid
  (`PROCESS_QUERY_LIMITED_INFORMATION`, with `PROCESS_VM_READ` asked for first
  and dropped on refusal), and reports the **largest** working set — the max,
  never the sum, because the launcher and the interpreter share their mapped
  pages. `attach()` records that something is actually in the job
  (`WindowsBackend.attached`); with no job or a refused query the sample falls
  back to the `Popen` handle rather than answering `None`, which would enforce
  nothing. New module-level Win32 seams `_job_process_ids` / `_open_process`
  (and `_close_handle`, now used for process handles too), plus a `_library`
  cache so the sampling path stops re-`LoadLibrary`ing kernel32/psapi four
  times a second. `tests/test_sandbox_plan.py`'s `windows` fixture stubs the
  two new seams — `job_pids` and a working set per *opened* handle — and
  `test_a_windows_sample_measures_the_job_not_the_launcher_stub` drives the max
  (4 MB stub vs 480 MB interpreter), the handle-closing and both fallbacks on
  macOS; `tests/test_sandbox_windows.py` raises its bound from 4 MB to
  **≥ 100 MB** so a stub-only sample fails loudly instead of passing a sanity
  check. Documented in `docs/deployment.md` (under the quota-tier table) and as
  an `AGENTS.md` PRD-006 gotcha.
- **`tests/test_usage.py::test_since_filters_to_the_recent_window` — a 15.6 ms
  clock.** `time.time()` on Windows advances on the ~15.6 ms system tick, so
  `sleep(0.01)` moved it not at all: both records carried the same `at`, the
  `old` one survived the `since` filter and the assertion read
  `['old', 'new'] == ['new']`. `record()` stamps `at` itself, so the test now
  monkeypatches `usage.time` (the module's own global, not the stdlib
  attribute) with two explicit stamps and a `cut` between them — exact on every
  OS, and no sleep.
- **`tests/test_sandbox_linux.py::test_write_outside_roots_is_denied` — `/app`
  only exists in the shipped image.** The `/app/pwned` parameter case is
  `/etc/pwned` now: the ubuntu-latest runner has no `/app` (only the shipped
  image does), so the open raised `FileNotFoundError`, which `denials.classify`
  rightly does not call `denied == "filesystem"` — the case wants the Landlock
  `EACCES` on an ungranted directory that exists, and `/etc` exists everywhere
  and is granted by neither posture.

## Files
- `agentcad/cli.py`, `agentcad/kernel/sandbox_linux.py`, `agentcad/kernel/denials.py`
- `agentcad/core/model.py`, `agentcad/server/app.py`, `agentcad/kernel/worker.py`, `compose.yaml` — merged
- `docs/roadmap.md`, `AGENTS.md`, `CLAUDE.md`, `docs/deployment.md`, `docs/architecture.md`, the PRD-006 header — merged/renumbered references
- `docs/changelog/0230-…0237-prd-006-*.md` — renamed from `0213-…0220`
- `tests/test_sandbox_plan.py`, `tests/test_sandbox.py`, `tests/test_deploy_config.py`, `tests/test_denials.py`, `tests/test_checks_cli.py`
- CI round 1: `agentcad/kernel/sandbox_windows.py`, `tests/test_sandbox_windows.py`, `tests/test_sandbox_plan.py`, `tests/test_usage.py`, `tests/test_sandbox_linux.py`, `docs/deployment.md`, `AGENTS.md`

## Notes
Verification on the merged tree: `make test-linux` — 112 passed, 2 skipped
(the shipped image, Landlock ABI 6); `make test` — 4349 passed, 36 skipped,
1 failed — the failure was the xdist temp-glob race above, fixed in this
commit and re-run green (`tests/test_sandbox_plan.py` 77 passed); PRD-007's
own suites (`tests/test_share_*.py`) 52 passed on the merged tree. CI on PR
#22 is the first run on x86_64 Linux and on Windows.

CI round 1 fixes verified on macOS: `tests/test_sandbox_plan.py`
`tests/test_usage.py` `tests/test_supervisor.py` — 116 passed. The Windows
half is written blind: the `QueryInformationJobObject` / `OpenProcess` calls
themselves cannot be executed on this box (their seams are stubbed in the
macOS-runnable test), so the next `windows-latest` run is the real check —
both the job walk and whether a build123d worker's *working set* clears the new
100 MB bound (a working set is resident pages, not the ~300–400 MB of virtual
footprint the same import shows elsewhere).
