# 0073 — Branching second-review fixes: ref ambiguity, fast-forward validation, finalize atomicity

- **Commit:** pending
- **Date:** 2026-08-10
- **Author:** Claude (PRD-001, Codex review follow-up)

## Summary
Fixes the eleven findings a second independent review (Codex) raised against
the PRD-001 branching feature, each with a failing regression test written
first. The load-bearing ones: `git rev-parse <name>` searches `refs/tags`
before `refs/heads`, so a tag could steer every branch operation that named a
branch bare; fast-forward merges skipped the FR9 validation pass entirely; the
turn/cleanliness checks at the top of a merge were not held through
finalization, so a concurrent write to the target tree was destroyed by the
`reset --hard` that lands the merge; and a `project.json` that failed to parse
read as `{}`, which the key-wise merge takes as "this side deleted everything".

Builds on 0072 (binary-safe merges, absent-side takes, ff-undo, moved-target
invalidation) — none of that is redone here.

## Changes

### X1 — a tag can no longer shadow a branch (major)
- `history.ProjectHistory` grows `resolve_branch` / `resolve_tag` (over a
  shared `_resolve_in`) which rev-parse `refs/heads/<name>^{commit}` and
  `refs/tags/<name>^{commit}` explicitly, plus a `branch_ref` helper.
  `resolve_ref` stays as-is and is now documented as *deliberately ambiguous*,
  for the surfaces that genuinely accept any ref (`project_history {ref}`,
  `project_restore`).
- `branches()` / `tags()` switch from `%(refname:short)` to
  `%(refname:lstrip=2)`. `:short` is the shortest *unambiguous* name, so a tag
  called `feat` renamed the branch `feat` to `heads/feat` in the branch list —
  and every name comparison against that listing (does the branch exist? is it
  checked out?) then missed. This was a second, independent facet of X1.
- `merge._merge`, `merge._heads_moved` resolve heads with `resolve_branch`;
  `merge._merge_base` passes `refs/heads/<name>` to `git merge-base`.
- `branches.create` uses `resolve_branch` when `from` is omitted (the default
  means "the branch I am on"); an explicit `from` keeps git precedence, as
  documented. `branches.tag` reports its commit via `resolve_tag`.
- `git worktree add <path> <name>` and `git branch -D <name>` were verified to
  operate in the heads namespace already and are unchanged.

### X2 — fast-forward merges run the validation pass (major)
- `merge._fast_forward` takes the source branch's own worktree (which already
  *is* the merged tree) and runs the same `_validate` pass against it: changed
  parts relative to the target, referential integrity, interference. Blocked by
  default with the usual `validation_error` + `details.validation`;
  `allow_invalid: true` lands it with the report.
- The result payload's `validation` is no longer `null` on a fast-forward;
  `fast_forward: true` is the flag callers should key on (it already existed).
  `already_up_to_date` still reports `validation: null` — nothing was merged.

### X3 — a skipped interference check is a warning (accepted cap, made loud)
- The 40-instance cap stays. `_validate`'s report grows a `warnings: [str]`
  list, and `_check_interference` appends
  `"interference skipped: N instances > 40; check the merged assembly by hand"`
  when it trips it, so an `ok: true` report never silently hides a check it did
  not run. The list rides `merge_completed` (which carries the whole report)
  and renders as a "Warnings" block in the merge report UI.

### X4 — a broken worktree fails closed on writes (major)
- `BranchManager.ensure_checkout(proj)` is the write-path counterpart of
  `resolve_path`: same cheap `project.json` stat on the fast path (no git
  call), but when the calling client's branch tree is missing it
  **re-materializes it from the branch ref** and, if that is impossible,
  raises `ConflictError`. `resolve_path` stays total — reads still degrade to
  the canonical directory — because a read that raises would break
  `list_projects`.
- `tools_versioning.install_write_guard(service)` (new, exported so the tests
  wire exactly what the pack wires) installs `ensure_checkout` **before** the
  turn-lock check in `store.write_guard`, the single choke point every
  persistent mutation already passes through.
- `UndoCursor._step` now calls the write guard *before* resolving
  `store.path_of(proj)`, since the guard is what may re-materialize that path.

### X5 — snapshot failures are no longer ignored (major)
- `BranchManager._checkpoint(tree, message, what)` snapshots and then asserts
  `git status --porcelain` is empty. `ProjectHistory.snapshot` returns `None`
  for both "nothing to commit" (fine) and "git failed" (not fine); the
  difference is whether the tree is still dirty afterwards.
- `switch()` and `tag()` use it: a clean tree is a no-op, a dirty tree that
  will not commit is a `conflict_error` (switching would abandon the work;
  tagging would pin a version to a stale head).
- `delete()` snapshots the branch's worktree before `worktree remove --force`
  and refuses with a `validation_error` when it cannot — `--force` now only
  ever discards a verified-committed tree.

### X6 + X9 — finalization is atomic and race-free (critical)
- `merge._holding_target(proj, tree)` acquires the **target branch's** turn
  lock and holds it across the validation pass and finalization (both the
  three-way and the fast-forward path). A competing client now gets the
  ordinary "project is locked by …" conflict instead of having its bytes
  destroyed. A caller that already held the turn keeps it; one that did not
  gets it released. `_check_turn` keeps the cheap up-front check and shares the
  new `_turn_key`.
- `merge._verify_clean(tree, branch)` re-asserts the target tree is unmodified
  immediately before the compare-and-swap — a *status* check, never a snapshot,
  which would move the ref out from under the CAS. A write that dodged the turn
  lock (raw file, other process) now fails the merge instead of being
  clobbered.
- `merge._land(canonical, branch, tree, commit, previous)` wraps the tree sync:
  when `reset --hard` fails, the ref is rolled back to `previous` under its own
  CAS, the staged merge is left intact, and the error reports whether the
  rollback succeeded (`details.ref_restored`). `_discard` only runs after
  **both** the ref move and the tree sync succeeded.

### X7 — only `merge_conflict` is an HTTP 200 error body (minor)
- `routes_versioning._result` inverts its logic: anything but `merge_conflict`
  raises, with unmapped types (`invalid_arguments` from the registry's own
  schema check, kernel errors, a future pack's type) defaulting to
  `ValidationError` → 422.

### X8 — branch delete in the UI (major, FR1)
- The branch menu renders non-default, non-current branches as a `.menu-row`
  holding the existing switch `.menu-item` plus a `.menu-del` "×" button (two
  siblings — never a button nested inside a button). `deleteBranch()` confirms
  with the same native `confirm()` every other destructive action in this app
  uses (version restore, merge abort), calls the previously unused
  `api.deleteBranch`, and surfaces in-use/current/default refusals as error
  toasts before refreshing the branch state.
- `.menu-row` / `.menu-del` styles added to `app.css`.

### X11 — a malformed historical manifest refuses the merge (critical)
- `merge._manifest_at(canonical, commit, ref=None)` raises a `ValidationError`
  naming the ref and `project.json` when the blob exists but does not parse (or
  is not an object). `{}` now means one thing only: the commit has no
  `project.json` at all — the legitimate orphan/empty-base case.
- Callers pass the ref they mean (`target`, `source`, `merge base <sha>`), so
  the message says which branch to fix.
- `_manifest_shape(merged)` is a new backstop prepended to the validation
  pass's `integrity` list: the merged document must still be a project (a
  string `name`, a `parts` list of dicts with unique string ids, an
  `assembly.instances` list) before anything is finalized, because
  `get_project` reads it seconds later. New integrity kind:
  `manifest_invalid`.

### X12 — absence and an authored `null` are different (major)
- `manifest_merge._conflict` now **omits** a side that has no value instead of
  encoding it as `None`; an authored JSON `null` reports `null`.
  `_choice_value` keys off key presence, so `take: "ours"` on an authored null
  writes that null instead of deleting the key.
- `merge.js`'s `valueTable` keys off `hasOwnProperty`, so `"— deleted —"` means
  absent and `null` renders as `null`.

### X13 — dotted ids no longer break conflict reversibility (major)
- Every conflict carries `path`, the exact key segments, alongside the dotted
  display `key`. The merge core threads segment tuples end to end
  (`_merge_section`/`_merge_entry_list`/`_merge_entry`/`_merge_scalar_dict`/
  `_merge_atomic`), and `apply_choices` writes through
  `conflict_path(conflict)` → `_write_path` — never by re-splitting the key.
  Previously `parts.body.solid_materials.wall.inner` was re-split into five
  segments and written as a bogus flat `solid_materials.wall.inner` field on
  the part; a dotted *part* id raised "entry not present".
- `CONFLICT_KEYS` gains `"path"`. `conflict_path` falls back to splitting the
  key for a conflict without one.

## Files
- `agentcad/core/history.py` — `resolve_branch`/`resolve_tag`/`_resolve_in`/
  `branch_ref`; `refname:lstrip=2` in `branches()`/`tags()`; write guard before
  path resolution in `UndoCursor._step`.
- `agentcad/core/branches.py` — `ensure_checkout`; `_checkpoint`; checkpointed
  `switch`/`tag`/`delete`; branch-qualified `create` default and `tag` commit.
- `agentcad/core/merge.py` — validated + rollback-safe `_fast_forward`;
  `_holding_target`, `_verify_clean`, `_land`, `_turn_key`; strict
  `_manifest_at`; `warnings` in the report; `_manifest_shape`; branch-qualified
  head/merge-base resolution.
- `agentcad/core/manifest_merge.py` — segment-based keys, `path` on every
  conflict, `conflict_path`, presence-based side encoding.
- `agentcad/core/tools_versioning.py` — `install_write_guard`.
- `agentcad/server/routes_versioning.py` — `_result` maps every non-conflict
  error to an HTTP error.
- `frontend/js/main.js` — per-row branch delete + `deleteBranch()`.
- `frontend/js/merge.js` — presence-based conflict rendering, warnings block,
  fast-forward wording.
- `frontend/css/app.css` — `.menu-row` / `.menu-del`.
- `docs/agent-api.md`, `docs/user-guide.md` — the changed contracts.
- `tests/test_merge.py`, `tests/test_branches.py`, `tests/test_manifest_merge.py`,
  `tests/test_versioning_api.py` — regression tests per finding.

## Notes
- **Contract changes for re-review:** `validation` is no longer `null` on a
  fast-forward (key on `fast_forward`); the validation report gains
  `warnings: [str]` and the `manifest_invalid` integrity kind; manifest
  conflicts omit absent sides and carry `path`; versioning routes return 422
  for `invalid_arguments`; `branch_delete` can now fail with a
  `validation_error` for an unsnapshottable dirty tree.
- `test_fast_forward_skips_the_validation_pass` was renamed to
  `test_fast_forward_moves_the_ref_and_the_tree` and its
  `validation is None` assertion inverted — the old name asserted the bug.
- Existing `manifest_merge` tests that asserted `"ours": None` for an absent
  side were updated to `"ours" not in conflict`; the exact-payload test now
  expects `path`.
- `test_branches.py`'s `stack` fixture calls `install_write_guard` instead of
  hand-rolling the guard, so the tests exercise what the pack installs.
- Deliberately *not* changed: the 40-instance interference cap (accepted
  design, now visible), and `resolve_ref`'s tag-before-branch precedence on the
  generic ref surfaces.
