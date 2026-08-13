# 0146 — 2026-08-13 — PRD-009 closed out: moved to completed, roadmap updated

## Summary

Bookkeeping after PR #13 (Sketcher v2) merged to main with all checks green
on the first run. The PRD moves to `docs/prd/completed/` and the roadmap
index reflects it. This is the first v5 feature to land, and the first that
was primarily a numerical rather than an orchestration problem.

## Changes

- `docs/prd/in-progress/PRD-009-sketcher-v2.md` → `docs/prd/completed/`,
  status "completed — merged to main in PR #13 (AC1–AC7 verified)".
- `docs/roadmap.md`: the PRD-009 row links to `prd/completed/` and reads
  "completed (PR #13, AC1–AC7 verified)".

## Files

- `docs/prd/completed/PRD-009-sketcher-v2.md` — moved + status
- `docs/roadmap.md` — index row
- `docs/changelog/0146-prd-009-completed.md` — this entry

## Notes

Feature history: 14 TDD slices (changelog 0127–0141) and four review rounds
(0142–0145). Final suite: 2018 passed, 1 skipped; CI green on macOS, Linux
and Windows plus four geometry-CI dogfood jobs.

The junction degeneracy is the record worth keeping: the same defect class
recurred six times, and every fix was correct along the axis someone had
thought to test while reopening along one nobody had — entity handles, then
constraint kinds, then seed distance, then distance from the origin. What
finally held was a criterion naming no constraint kinds, read at a
configuration that actually solves, guarded by sweeps on both the scale and
translation axes. The general lesson is in AGENTS.md's sketcher gotchas: a
residual that is second-order flat at a pinned junction leaves the Jacobian
rank-deficient AT the solution, so the solver reports degrees of freedom
that do not exist and blames a constraint doing real work.

Suite growth: 292 tests at the start of PRD-001, 2018 now.
