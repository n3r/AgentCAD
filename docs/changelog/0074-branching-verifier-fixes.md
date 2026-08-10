# 0074 — PRD-001 verifier fixes: landed-merge conflicts, detached branch trees, staged status leak, guarded imports

- **Commit:** pending
- **Date:** 2026-08-10
- **Author:** Nikita Fedorov

## Summary
Four defects a verification pass demonstrated against the PRD-001 branching
feature with live repro scripts: a merge that had already landed being reported
as a conflict, a branch working tree that lost its `.git` link silently eating
writes, per-part build state leaking one entry per staged merge worktree, and
the import ingest path bypassing the branch write guard. All four are fixed
with a failing regression test written first.

## Changes
- **D1 — a completed merge can no longer fail on its own lock release.**
  `MergeOrchestrator._holding_target` holds the target branch's turn across
  validation and finalization, but that hold carries the ordinary 120 s TTL. A
  validation pass that outlived it freed the turn; when another client then
  legitimately took it, the `turnlock.release` in the `finally` raised
  `ConflictError` — *after* the ref had moved, the tree had been synced and
  `merge_completed` had been published. The release now swallows
  `ConflictError`: a lock that is no longer ours is not this merge's problem.
  The turn is deliberately NOT re-acquired before finalization — that would
  turn the same scenario into a pre-landing refusal, and `_verify_clean` plus
  the ref CAS already guard the bytes and the ref.
- **D2 — a branch tree that lost its `.git` link is repaired, never
  discarded.** `BranchManager.ensure_checkout`'s fast path returned the tree on
  a `project.json` stat alone. A tree whose `.git` file was gone still passes
  that stat, so the write landed there, the next snapshot `git init`-ed a
  throwaway repo *inside* the tree (the edit never reaching `refs/heads/<branch>`,
  invisible to `branch_list` and to merges), and the next materialize `rmtree`d
  the lot. The fast path now also stats `.git`, and `_materialize` gained a
  repair step: `git worktree repair` re-attaches the tree **before** the
  `worktree prune` that would delete the admin directory repair reads. Content
  is only replaced when the tree belongs to a *different* repository (a copied
  project — new `_points_elsewhere` helper); any other unattachable tree is a
  `ConflictError` naming the directory, not a deletion.
- **D3 — staged merge worktrees no longer leak `_status` entries.** The
  validation pass builds parts with the resolver pinned to the staged worktree,
  so each build recorded `AgentCADService._status` under that temp directory's
  `lock_key`. The directory is deleted on finalize/abort; the entries lived for
  the life of the process. New `AgentCADService._forget_status(lock_key)`,
  called from `MergeOrchestrator._discard` — the single choke point where a
  staged worktree is removed (finalize, abort, and re-stage).
- **G1 — imports go through the branch write guard.** `ProjectStore.imports_dir`
  takes `write: bool = False`; with `write=True` it runs `write_guard` (branch
  `ensure_checkout` + turn lock) before resolving, so an ingested STEP/STL
  lands in the caller's branch tree or is refused, instead of following the
  read resolver's canonical fallback. The two write call sites —
  `imports.ingest_file` and the `POST /projects/{proj}/imports` route — pass
  `write=True`; the read call sites (rebuilds, existence checks) are unchanged
  and stay unguarded, so a rebuild cannot fail because another client holds the
  turn.

## Files
- `agentcad/core/merge.py` — `_holding_target` swallows `ConflictError` from
  the release; `_discard` purges the staged tree's `_status` entries.
- `agentcad/core/branches.py` — `_points_elsewhere` helper; `_repair_link`;
  `ensure_checkout` stats `.git` on the fast path and re-raises `ConflictError`
  from `_materialize` unwrapped; `_materialize` repairs or refuses instead of
  discarding a tree that holds content.
- `agentcad/core/service.py` — `_forget_status(lock_key)`.
- `agentcad/core/project.py` — `imports_dir(proj, *, write=False)`.
- `agentcad/core/imports.py`, `agentcad/server/routes_import.py` — ingest and
  upload pass `write=True`.
- `tests/test_merge.py` — `TestHeldTurnOutlivingItsTtl` (D1),
  `TestStagedTreeLeavesNoStatus` (D3, finalize + abort).
- `tests/test_branches.py` — `TestDetachedWorktreeIsRepaired` (D2, repair and
  the unrepairable refusal), `TestImportsFollowTheBranch` (G1).

## Notes
- `git worktree repair` exits **non-zero** while reporting what it fixed, so
  `_repair_link` reads the outcome from `_is_linked_worktree`, never from the
  status code. It also only works while `.history/worktrees/<name>/` survives —
  `git worktree prune` deletes that directory for a tree whose `.git` is
  missing ("gitdir file points to non-existent location"), which is why the
  repair attempt sits ahead of the prune in `_materialize`.
- The unrepairable case (admin directory gone as well) is a refusal on both the
  write path and the next materialize; the tree's contents are left untouched
  for the user to move aside. Switching *away* from such a branch still
  succeeds — its checkpoint snapshot creates a throwaway repo inside the tree —
  but nothing is deleted, and coming back reports the damage loudly.
- Contract changes: `ProjectStore.imports_dir` has a new keyword-only `write`
  argument (default `False` — every existing caller is unchanged);
  `BranchManager.ensure_checkout` and `_materialize` can now raise
  `ConflictError` for a tree that exists but cannot be attached.
