# 0076 — 2026-08-10 — PRD-001 closed out: moved to completed, roadmap updated

## Summary

Bookkeeping after PR #8 (branching version control) merged to main with
all CI checks green: the PRD moves to `docs/prd/completed/` and the
roadmap index reflects the new status. The roadmap's status-model folder
for finished features is renamed from `shipped/` to `completed/` to match
the directory actually in use.

## Changes

- `docs/prd/in-progress/PRD-001-branching-version-control.md` →
  `docs/prd/completed/`, status line now "completed — merged to main in
  PR #8 (AC1–AC7 verified)".
- `docs/roadmap.md`: PRD-001 row links to `prd/completed/` with status
  "completed (PR #8, AC1–AC7 verified)"; the status-model and
  working-the-roadmap sections say `completed/` instead of `shipped/`.

## Files

- `docs/prd/completed/PRD-001-branching-version-control.md` — moved + status
- `docs/roadmap.md` — index row + folder naming
- `docs/changelog/0076-prd-001-completed.md` — this entry

## Notes

Feature history: built in 5 TDD slices (changelog 0067–0071), hardened by
three review passes (0072–0074) and a Windows CI fix (0075). Final suite:
510 passed, 1 skipped; CI green on macOS, Linux, Windows.
