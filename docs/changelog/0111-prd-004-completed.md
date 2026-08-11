# 0111 — 2026-08-11 — PRD-004 closed out: moved to completed, roadmap updated

## Summary

Bookkeeping after PR #11 (geometry CI) merged to main with all checks green:
the PRD moves to `docs/prd/completed/` and the roadmap index reflects it.
This completes the v4 collaborative core apart from the deployment PRDs
(005 multi-tenant cloud, 006 sandboxing & quotas), which are deferred.

## Changes

- `docs/prd/in-progress/PRD-004-geometry-ci.md` → `docs/prd/completed/`,
  status "completed — merged to main in PR #11 (AC1–AC10 verified)".
- `docs/roadmap.md`: the PRD-004 row links to `prd/completed/` and reads
  "completed (PR #11, AC1–AC10 verified)".

## Files

- `docs/prd/completed/PRD-004-geometry-ci.md` — moved + status
- `docs/roadmap.md` — index row
- `docs/changelog/0111-prd-004-completed.md` — this entry

## Notes

Feature history: 8 TDD slices (changelog 0098–0105), three review passes
(0106–0108), and two cross-platform test fixes found by CI (0109 the Error
Doctor hint's OCCT-dependent wording, 0110 git's read-only objects vs
Windows unlink). AC1's live evidence is run 31492128698, cited in 0105.
Final suite: 1183 passed, 1 skipped; CI green on macOS, Linux and Windows,
plus four green geometry-CI dogfood jobs.
