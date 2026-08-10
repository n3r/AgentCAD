# 0075 — 2026-08-10 — Fix Windows path comparison in worktree listing test

## Summary

`test_create_makes_a_ref_and_a_worktree` failed on Windows CI because it
compared `str(Path)` (backslash separators) against `git worktree list`
output, which prints forward-slash paths on every OS. The assertion now
compares in posix form. Test-only change; no product behavior involved.

## Changes

- The worktree-listing assertion uses `Path.as_posix()` for both the
  canonical project directory and the branch tree, matching git's output
  format on all platforms. On POSIX systems `as_posix()` equals `str()`,
  so the test is unchanged there.

## Files

- `tests/test_branches.py` — posix-form comparison in
  `TestBranchLifecycle::test_create_makes_a_ref_and_a_worktree`
- `docs/changelog/0075-windows-worktree-listing-test.md` — this entry

## Notes

Found by the PR #8 Windows portability CI job (1 failed, 227 passed);
the only cross-platform failure on the branch.
