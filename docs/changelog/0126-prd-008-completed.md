# 0126 — 2026-08-12 — PRD-008 closed out: the v4 collaborative core is complete

## Summary

Bookkeeping after PR #12 (review threads & presence) merged to main with all
checks green on the first run. The PRD moves to `docs/prd/completed/` and the
roadmap index reflects it. With this, **every v4 PRD that is in scope is
shipped** — 001 branching, 002 change proposals, 003 executable specs, 004
geometry CI, 008 review threads. The two remaining v4 entries (005
multi-tenant cloud, 006 sandboxing & quotas) are deferred as deployment work,
and 007 depends on both.

## Changes

- `docs/prd/in-progress/PRD-008-review-threads-presence.md` →
  `docs/prd/completed/`, status "completed — merged to main in PR #12
  (AC1–AC9 verified)".
- `docs/roadmap.md`: the PRD-008 row links to `prd/completed/` and reads
  "completed (PR #12, AC1–AC9 verified)".

## Files

- `docs/prd/completed/PRD-008-review-threads-presence.md` — moved + status
- `docs/roadmap.md` — index row
- `docs/changelog/0126-prd-008-completed.md` — this entry

## Notes

Feature history: 11 TDD slices (changelog 0112–0122) and three review rounds
(0123–0125). The rounds are worth reading in order: each one broke the fix
before it, and the sequence is why the anchor guarantee is now stated as two
measured rates instead of an assumption. Final suite: 1441 passed, 1 skipped;
CI green on macOS, Linux and Windows plus four geometry-CI dogfood jobs.

Suite growth across the v4 core: 292 tests at the start of PRD-001, 1441 now.
