# 0110 — 2026-08-11 — Windows-safe deletion of a git dir in the ref tests

## Summary

`tests/test_checks_ref.py::test_a_ref_without_git_is_a_validation_error_naming_git`
failed on the Windows portability job of PR #11 with
`PermissionError: [WinError 5] Access is denied` under
`.history/objects/48/…`. The test simulates "this project has no git repo"
by deleting `.history`, and git marks everything under `objects/` read-only.
POSIX consults the parent directory when unlinking, so the delete succeeds on
macOS and Linux; Windows consults the file itself and refuses.

## Changes

- `_rmtree_repo(path)` helper: `shutil.rmtree(..., onexc=…)` clearing the
  read-only bit and retrying the failed operation — the standard remedy, with
  a comment naming the OS difference rather than the symptom.
- The one site that deletes a whole git directory uses it. The `rmtree` calls
  in `tests/test_branches.py` were checked and left alone: they remove
  worktree and admin directories, which contain no read-only object files,
  and they already pass on Windows.

## Files

- `tests/test_checks_ref.py` — the helper, its use, and the `stat` import
- `docs/changelog/0110-windows-safe-git-dir-delete-in-tests.md` — this entry

## Notes

Verified rather than assumed: `os.unlink` was wrapped to reproduce Windows'
semantics (refuse a file without the write bit) on macOS. Plain `rmtree` then
raised the same `PermissionError` CI reported, and the `onexc` handler
deleted the tree cleanly. Product code is unaffected — every `rmtree` in
`agentcad/` passes `ignore_errors=True`, so on Windows it degrades to leaving
files behind rather than raising.

Verification: `uv run pytest -q tests/test_checks_ref.py` → 24 passed.
