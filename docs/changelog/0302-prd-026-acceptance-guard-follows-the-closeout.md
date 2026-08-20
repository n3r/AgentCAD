# 0302 — the PRD-026 acceptance guard follows the close-out

- **Commit:** pending
- **Date:** 2026-08-20
- **Author:** Nikita Fedorov (with Claude)

## Summary

`test_the_prd_status_and_acceptance_record_are_on_the_page` pinned the literal
"in progress — acceptance" status string, so the 0301 close-out (which flips
the PRD to "completed — merged in PR #29" and moves it to `completed/`) turned
the guard red. The test's `_find_prd()` already followed the move; only the
status literal was stale.

## Changes

- `tests/test_prd026_acceptance.py` — the guard now pins that a `**Status:**`
  line exists and is one of the two lifecycle values, keeping the real
  assertions (the AC1–AC7 record, "Shipped vs. deferred", the corrected
  "21 sites" count) intact.

## Files

- `tests/test_prd026_acceptance.py`

## Notes

Caught by re-running the acceptance file right after pushing the close-out —
the same self-referential-guard family as PRD-013's count guard (changelog
0269's notes). `make test` — the acceptance file is **14 passed**; the suite
is otherwise unchanged from 0301 (**5303 passed, 48 skipped** plus this one
test's fix).
