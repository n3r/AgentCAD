# 0035 — GitHub Actions test workflow

- **Commit:** pending
- **Date:** 2026-08-09
- **Author:** Claude (goal session for Nikita Fedorov)

## Summary
Bootstraps continuous integration: the repo had no CI at all, so pull
requests had no check to gate on. A single `test` workflow now runs the
full pytest suite on every pull request and on pushes to `main`.

## Changes
- `.github/workflows/test.yml` (new): ubuntu-latest, 40-minute timeout,
  per-ref concurrency with cancel-in-progress. Installs the system GL/X11
  libraries the OCP/VTK wheels link against even headless, sets up uv with
  caching (`astral-sh/setup-uv@v5`), installs with `uv sync --frozen` (the
  pinned `uv.lock` is authoritative — a drifted lock fails the build rather
  than silently resolving), and runs `uv run pytest -q`.
- The `[fem]` extra is deliberately not installed: the suite is designed to
  stay green without it (`pytest.importorskip`), matching the local
  definition of done (139 passed, 1 skipped at time of writing).

## Files
- `.github/workflows/test.yml` — new CI workflow

## Notes
First run is slow (~2 GB of build123d/OCCT wheels); the uv cache makes
subsequent runs much faster. `pull_request` picks up workflows from the PR
branch, so this very PR gets the check. No lint/format step yet — the repo
has no configured linter; add one alongside this workflow if that changes.
