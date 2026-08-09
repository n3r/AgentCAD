# 0066 — Focus PR CI and schedule exhaustive engine coverage

- **Commit:** pending
- **Date:** 2026-08-09
- **Author:** Codex (with Nikita Fedorov)

## Summary

CI now runs the merge-required suite on macOS and a focused 111-test
portability group on Linux and Windows. The four broad engine-example cases
run in scheduled or explicitly requested exhaustive macOS CI instead of every
PR. This retains differential coverage for filesystem, Git/encoding,
subprocess, kernel/OCCT, local server/MCP, binary mesh, CAD import/export, and
packaging-path boundaries while removing the suite's 20-minute PR bottleneck.

## Changes

- Replaced the three-identical-full-suite matrix with a 20-minute macOS PR job
  plus 20-minute Linux and Windows portability jobs.
- Marked the engine fixture parameters `exhaustive`; all four engine cases
  remain in local `make test` and a daily/manual 90-minute macOS gate.
- Added the `portability` pytest marker and applied it to 11 boundary-focused
  modules covering 111 of the suite's 319 collected cases.
- Added `make test-pr` and `make test-portability` so contributors and CI use
  the same selections.
- Documented when a test should receive the marker and updated the README and
  roadmap to describe the canonical-full-plus-portability model accurately.
- Kept two xdist workers and a one-worker AgentCAD kernel pool on every CI job;
  dependency sync and Linux OCCT system-library setup are unchanged.

## Files

- `.github/workflows/ci.yml` — PR matrix plus scheduled/manual exhaustive gate
- `pyproject.toml`, `Makefile` — marker registration and local test target
- `tests/test_examples.py` — engine-only exhaustive parameter marking
- `tests/test_config.py`, `tests/test_frozen_helpers.py`,
  `tests/test_history.py`, `tests/test_kernel.py`, `tests/test_mcp.py`,
  `tests/test_mesh.py`, `tests/test_pool.py`, `tests/test_project.py`,
  `tests/test_reference.py`, `tests/test_server.py`, `tests/test_threads.py` —
  portability classification
- `README.md`, `AGENTS.md`, `docs/roadmap.md` — contributor and product docs

## Notes

Local verification collected 111 portability cases and passed them in 36.93
seconds. The 315-case PR selection passed 310 tests with 5 expected FEM skips
in 102.12 seconds. The unchanged complete selection previously passed 314 with
5 skips in 1132.75 seconds. Using those local timings, per-change test compute
falls from roughly 56.6 runner-minutes for three full jobs to 2.9 for one PR
job and two portability jobs, about a 95% reduction before platform and setup
differences; the 18.9-minute complete gate runs once daily instead. Pure domain
tests should stay unmarked; new host-sensitive boundary coverage must opt into
`portability`, and ordinary regressions must not use `exhaustive` merely
because they are slow.
