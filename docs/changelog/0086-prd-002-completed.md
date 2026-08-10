# 0086 — 2026-08-10 — PRD-002 closed out: moved to completed, roadmap updated

## Summary

Bookkeeping after PR #9 (change proposals & geometric diff) merged to main
with all CI checks green: the PRD moves to `docs/prd/completed/` and the
roadmap index reflects the new status.

## Changes

- `docs/prd/in-progress/PRD-002-change-proposals-geometric-diff.md` →
  `docs/prd/completed/`, status line now "completed — merged to main in
  PR #9 (AC1–AC9 verified)".
- `docs/roadmap.md`: PRD-002 row links to `prd/completed/` with status
  "completed (PR #9, AC1–AC9 verified)".

## Files

- `docs/prd/completed/PRD-002-change-proposals-geometric-diff.md` — moved + status
- `docs/roadmap.md` — index row
- `docs/changelog/0086-prd-002-completed.md` — this entry

## Notes

Feature history: built in 6 TDD slices (changelog 0077–0082), hardened by
three review passes (0083–0085). Final suite: 666 passed, 1 skipped; CI
green on macOS, Linux, Windows. Known follow-up recorded in 0085: render
asset generation-namespacing (deferred; render URLs are a hand-called tool
contract).
