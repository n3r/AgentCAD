# 0072 — Branching review fixes: binary-safe merges, absent-side resolutions, per-branch state

- **Commit:** pending
- **Date:** 2026-08-10
- **Author:** Claude (PRD-001 review follow-up)

## Summary
Fixes the eight findings an independent review raised against the PRD-001
branching feature, each with a regression test written first. The critical one:
the merge pipeline read conflicted blobs as UTF-8 text with
`errors="replace"`, so any non-text tracked file (everything under `imports/`
— STL/STEP) was silently rewritten into replacement garbage and committed as a
*successful* merge. Merge content is now handled as bytes end to end, with
binary conflicts as their own kind. The rest are correctness bugs around
resolutions that name an absent side, undo after a fast-forward, silently
dropped resolutions, and in-memory state that was not branch-aware.

## Changes

### F1 — binary files are no longer corrupted by a merge (critical)
- `history.ProjectHistory` grows `_run_bytes` next to `_run`; both delegate to
  a new `_exec` that only sets `text=True`/`encoding`/`errors` for the text
  path. Nothing about the text path's behavior changed.
- `merge._blob_bytes` reads a conflicted blob's exact bytes; `merge._blob`
  keeps its text signature (used by the plumbing tests) but now decodes
  strictly and returns `None` for binary rather than replacement text.
- `_file_conflicts` reads all three stages as bytes and classifies with
  `_is_binary` (git's heuristic: a NUL in the first 8000 bytes, plus "does not
  decode as UTF-8"). A binary path becomes a `{"kind": "binary", "path",
  "sides": {base|ours|theirs: {bytes, sha256} | null}, "truncated": true,
  "hint"}` conflict: no diff3 body, no per-side text, and no `merged` field.
- Resolution moved from `_resolved_text` to `_resolved_content`, which returns
  **bytes**; `_stage` writes those bytes verbatim. `{"content": …}` on a binary
  conflict is a `validation_error`.
- The per-path bodies map is now `{path: {"sides": {...bytes}, "merged":
  bytes|None}}` so the full diff3 text still reaches the staged worktree even
  when the payload elides it.

### F2 — a `take` whose side is absent is honored, not silently ignored
- `_resolved_content` returns the `_DELETED` sentinel when the chosen side has
  no blob (that branch deleted the path); `_stage` unlinks the path so
  `git add -A` records the deletion. Previously the choice was dropped and the
  file survived with the target's content while the merge reported success.
- `{"take": "base"}` on an add/add conflict (no stage-1 blob) is now a
  `validation_error` naming the path and the valid choices, instead of being
  accepted and leaving git conflict markers in the staged file.

### F3 — undo after a fast-forward merge
- `UndoCursor.on_snapshot` takes an optional `undo_to`; `_step` prefers it over
  `parent_of(entry.id)`. `merge._fast_forward` records the **pre-merge target
  head**, because the source head's first parent is the previous commit on the
  *source* branch — a state the target never had. Redo is unchanged.

### F4 — a moved target no longer discards recorded resolutions
- `MergeOrchestrator.merge` raises `ConflictError` (like `resolve` already did)
  when the staged merge's branches have moved, naming the count of recorded
  resolutions and pointing at `merge_abort`, instead of re-merging with
  `resolved={}`.
- `frontend/js/merge.js` `handleFailure` re-reads the staged state when the
  error names a `merge_id`, so the modal cannot show stale conflicts.

### F5 — `lock_changed` is branch-aware
- `tools_locks` publishes `{type, project, key, branch, holder}`: the turn lock
  is keyed by the caller's working tree, so the event now says which lock moved.
- `frontend/js/main.js` ignores a `lock_changed` whose `branch` differs from the
  client's current branch — the badge no longer claims an agent holds "the"
  editing turn when it holds another branch's.

### F6 — REST deletion of nested branch names
- `DELETE /api/projects/{proj}/branches/{name:path}` accepts the rest of the
  path, so `feat/x` no longer 404s. `BranchManager.delete` validates the name
  against the branch pattern before anything reaches git (also covers the tool).

### F7 — `_materialize` no longer adopts any directory with a `project.json`
- A tree is used as-is only when it really is this repo's registered linked
  worktree for that branch: its `.git` file must point inside
  `<project>/.history/worktrees/`, and `git worktree list --porcelain` must
  name that path on `refs/heads/<branch>`. Otherwise it is discarded and
  re-materialized.
- `_drop_foreign_registrations` removes worktree admin directories whose
  recorded path lies outside the project — what a copied project inherits from
  the original, and what makes `worktree add` refuse ("already checked out").
  Registrations inside the project (including staged merge worktrees) are
  untouched.
- Fixes two real failure modes: a copied project committing into the ORIGINAL
  project's repo, and a tree that lost its `.git` getting `git init`-ed into an
  invisible throwaway repo whose commits no branch ever sees.

### F8 — build-state badges are per branch
- `AgentCADService._status` is keyed by `_status_key(proj, part_id) =
  (store.lock_key(proj), part_id)`. `lock_key` is the project name while
  branching is inactive, so keys are unchanged for unbranched projects; after a
  `branch_switch`, `get_project` no longer reports the other branch's ok/error
  badges.

### Hardening
- **H1** — the diff3 `merged` body is capped at the same `_MAX_BODY_BYTES` as
  the sides: over the cap it is elided from the payload with `truncated: true`,
  while the staged file on disk keeps the full text.
- **H2** — `ProjectHistory._refresh_excludes` appends the managed lines it is
  missing instead of rewriting `info/exclude`, so user-added patterns survive.

### Docs
- `docs/agent-api.md` (binary conflicts, delete-by-take, `base` refusal, the
  moved-branch `conflict_error`, the `{name:path}` DELETE route),
  `docs/architecture.md` (bytes in the merge pipeline), and the `merge_branch`
  / `resolve_merge` tool descriptions.

## Files
- `agentcad/core/history.py` — `_run_bytes`/`_exec`, additive `_refresh_excludes`
  (`_EXCLUDE_LINES`), `UndoCursor.on_snapshot(undo_to=…)` + `_step` honoring it
- `agentcad/core/merge.py` — byte-safe blobs, `_is_binary`/`_binary_conflict`,
  `_resolved_content` + `_DELETED`, staging by bytes, `merged` cap, loud
  invalidation of a stale staged merge, fast-forward undo target
- `agentcad/core/branches.py` — `_is_linked_worktree`,
  `_drop_foreign_registrations`, `_inside`/`_registers`/`_resolved` helpers,
  name validation in `delete`
- `agentcad/core/service.py` — `_status_key` and its 8 call sites
- `agentcad/core/tools_locks.py` — `_scope()` on both `lock_changed` publishes
- `agentcad/core/tools_versioning.py` — tool descriptions
- `agentcad/server/routes_versioning.py` — `{name:path}` DELETE route
- `frontend/js/main.js` — branch-scoped `lock_changed`
- `frontend/js/merge.js` — binary conflict rendering (`binaryTable`), staged
  refresh on a `merge_id` error
- `tests/test_merge.py` — `TestBinaryFiles` (3), `TestAbsentSides` (2),
  `test_a_moved_target_invalidates_the_staged_merge_loudly`,
  `test_an_oversized_conflict_body_is_elided_from_the_payload`
- `tests/test_branches.py` — `test_a_tree_that_lost_its_git_link_is_rematerialized`,
  `test_a_copied_project_never_writes_into_the_original`,
  `test_build_state_badges_are_per_branch`
- `tests/test_versioning_api.py` — `test_undo_after_a_fast_forward_restores_the_pre_merge_target`,
  `test_a_nested_branch_name_can_be_deleted_over_rest`
- `tests/test_locks.py` — branch/key in the `lock_changed` payload assertions,
  `test_lock_changed_names_the_branch_whose_turn_moved`
- `tests/test_history.py` — `test_refresh_excludes_appends_instead_of_clobbering`
- `docs/agent-api.md`, `docs/architecture.md` — merge surface updates

## Notes
- Every regression test was verified to FAIL against the old behavior (each fix
  was temporarily reverted in place and the test re-run) before being left
  green — including reproducing the byte corruption (`b'\x80'` → `b'\xef\xbf\xbd'`).
- `_blob` keeps returning `str` because `tests/test_merge.py::TestPlumbing`
  reads script stages through it; it just refuses to invent text for binary.
- The binary conflict payload deliberately carries no bytes at all: a UI or an
  agent gets size + sha256 per side, which is enough to tell versions apart
  without shipping a mesh through JSON.
- `_drop_foreign_registrations` only runs on the re-materialize path and only
  for admin directories pointing outside the project, so a power user's own
  `git worktree add /elsewhere` inside the project is unaffected while a copied
  project heals itself.
- Not addressed here: `BranchManager.resolve_path` still accepts a tree on the
  weaker "has a project.json" test (it must never raise and must stay cheap —
  it runs on every store access); a stale tree is healed by the next
  materialize (create/switch/tree_of/merge).
