# 0305 — 2026-08-20 — CI: drop the windows-latest portability leg

## Summary

Removes the `windows-latest` / `portability` job from `ci.yml`. AgentCAD is
deployed on Linux (docker containers), so Windows is not a target platform, and
its portability leg had become pure cost: an intermittent
`kernel_crash: kernel worker exited unexpectedly` in the PRD-006b Windows
AppContainer sandbox tests (`test_sandbox_windows.py`) that cost signal without
covering a shipped surface (it flaked on `main` and blocked PRD-015's PR #28).

## Changes

- **`.github/workflows/ci.yml`** — the CI matrix drops the `windows-latest`
  `include` row; it now runs `macos-latest` (the full PR suite) and
  `ubuntu-latest` (the `portability` marker). The macOS + Linux sandbox honesty
  gates (`AGENTCAD_EXPECT_SANDBOX`/`AGENTCAD_EXPECT_QUOTAS`) are unchanged.
- **`tests/test_prd006_acceptance.py`** — `test_ac8_the_ci_matrix_carries_the_
  honesty_gate` now expects `expect_sandbox: active` **2** rows (macOS + Linux),
  not 3; the `>= 2` sandbox/quota-gate assertions are unchanged.
- **`tests/test_sandbox_windows.py`** — docstring updated: these Windows-only
  tests no longer have a CI leg in `ci.yml`; the Windows AppContainer surface
  keeps its own opt-in `windows-probe.yml` (path/dispatch-triggered), and locally
  the battery skips on non-Windows.

## Notes

The Windows AppContainer code and its tests are untouched — only the *blocking
PR CI leg* is removed. Anyone doing Windows work runs `windows-probe.yml` (or a
local Windows host) deliberately.

`make test` — **5087 passed** (unchanged from the PRD-015 merged tree; this is a
`.github/workflows/ci.yml` edit plus two acceptance-test string adjustments that
add no tests, so the suite count is the same). CI on ubuntu + macOS is
authoritative.
