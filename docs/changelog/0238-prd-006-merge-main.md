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

## Files
- `agentcad/cli.py`, `agentcad/kernel/sandbox_linux.py`, `agentcad/kernel/denials.py`
- `agentcad/core/model.py`, `agentcad/server/app.py`, `agentcad/kernel/worker.py`, `compose.yaml` — merged
- `docs/roadmap.md`, `AGENTS.md`, `CLAUDE.md`, `docs/deployment.md`, `docs/architecture.md`, the PRD-006 header — merged/renumbered references
- `docs/changelog/0230-…0237-prd-006-*.md` — renamed from `0213-…0220`
- `tests/test_sandbox_plan.py`, `tests/test_sandbox.py`, `tests/test_deploy_config.py`, `tests/test_denials.py`, `tests/test_checks_cli.py`

## Notes
Verification on the merged tree: `make test-linux` — 112 passed, 2 skipped
(the shipped image, Landlock ABI 6); `make test` — 4349 passed, 36 skipped,
1 failed — the failure was the xdist temp-glob race above, fixed in this
commit and re-run green (`tests/test_sandbox_plan.py` 77 passed); PRD-007's
own suites (`tests/test_share_*.py`) 52 passed on the merged tree. CI on PR
#22 is the first run on x86_64 Linux and on Windows.
