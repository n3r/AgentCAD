# 0050 — Cross-platform CI workflow + Windows spawn guard

- **Commit:** pending
- **Date:** 2026-08-09
- **Author:** Claude (with Nikita Fedorov)

## Summary

The roadmap's "Windows / Linux" item: a GitHub Actions matrix
(ubuntu/macos/windows) running the full suite with uv, plus the one POSIX-only
construct in the codebase guarded for Windows. The architecture was already
portable (pure Python, OCP wheels on all three OSes, browser UI) — this is
the packaging/CI proof half; the suite itself is the compat harness.

## Changes

- **`.github/workflows/ci.yml`** (new): matrix over ubuntu-latest,
  macos-latest, windows-latest; astral-sh/setup-uv with lockfile-keyed
  caching; `uv sync --locked`; OCCT's headless X/GL system libraries on
  Linux; `uv run pytest -q` with `AGENTCAD_KERNEL_POOL_SIZE=1`; 45-minute
  timeout, per-ref concurrency cancellation.
- **`agentcad/agent/mcp_server.py`**: the auto-started server is detached
  via `start_new_session` only on POSIX; Windows uses
  `CREATE_NEW_PROCESS_GROUP` (start_new_session raises there).
- Platform audit findings (no changes needed): sandbox module is
  darwin-gated with `status() == "unsupported"` elsewhere; the history git
  driver sets `HOME` which git honors on Windows too; all path handling is
  pathlib; the packaging scripts are documented macOS-targeted.

## Files

- `.github/workflows/ci.yml`
- `agentcad/agent/mcp_server.py`
- `docs/changelog/0050-ci-cross-platform.md`

## Notes

CI proof on the Windows/Linux runners completes when this branch is pushed
to GitHub; the workflow is written against the same commands the local suite
uses. The darwin-only sandbox tests skip themselves off-platform, and the
FEM extras are intentionally absent in CI (the suite stays green without
them by design).
