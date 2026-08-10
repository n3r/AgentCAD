# 0069 — Branch merge orchestration, validation pass, versioning packs

- **Commit:** pending
- **Date:** 2026-08-10
- **Author:** Claude (PRD-001 slice 3)

## Summary
Slice 3 of PRD-001 (FR6–FR10): merging branches. A new
`MergeOrchestrator` (`agentcad/core/merge.py`) three-way merges part scripts
with `git merge-tree --write-tree` and `project.json` with slice 1's
structure-aware driver, stages the result in a detached worktree that is never
partially applied, revalidates it with the real kernel, and lands it as a
two-parent merge commit behind a compare-and-swap ref update. The capability
reaches agents and the browser through a tool pack
(`agentcad/core/tools_versioning.py`, 10 tools) and a route pack
(`agentcad/server/routes_versioning.py`), which also install the slice-2 seams
(`service.branches`, per-branch write guard). The UI is slice 4.

## Changes
- **New `agentcad/core/merge.py`**
  - `MergeOrchestrator.merge(proj, source, target=None, allow_invalid=False)`:
    preconditions (git ≥ 2.38 probe, both branches exist and differ, target's
    turn lock, both working trees clean after a checkpoint snapshot, no staged
    merge for a *different* pair) → merge base → fast-forward /
    already-up-to-date short circuits → `git merge-tree --write-tree -z` →
    `merge_manifests` → stage → conflicts or validate+finalize.
  - `resolve(proj, choices)` records resolutions and re-runs the merge from the
    recorded heads; `abort(proj)` removes the staged worktree and state;
    `status(proj)` reports the staged merge (UI/agent re-entry).
  - `project.json` is **always** re-merged by the driver, whether or not git
    flagged it — a *clean* line-wise JSON merge is the dangerous case.
  - Conflicts are **returned** as `{"error": {"type": "merge_conflict", …}}`,
    never raised, so the registry's class-name → type mapping cannot rename
    them. Entries are `{kind: "script"|"file", path, part, base, ours, theirs,
    merged, truncated}` (marked text via `git merge-file --diff3` labelled with
    the branch names) and slice 1's `{kind: "manifest", key, base, ours,
    theirs}`. `ours` = the target branch, `theirs` = the source, everywhere.
    Bodies over 256 KB per side are omitted with `truncated: true`; the staged
    file on disk always has the full text.
  - Staging: `git worktree add --detach .history/agentcad/merge-<id> <target>`
    + `read-tree -u --reset <merged tree>`, then the driver's manifest and each
    conflicted script are written over it, then `add -A` + `write-tree` fixes
    the final tree. State lives in `.history/agentcad/merge.json` (atomic
    write) with both heads, the base, the staged dir, the conflicts and the
    recorded resolutions.
  - Validation pass (FR9) runs `service._ensure_built`,
    `service._resolved_instances` and `service.check_interference` under
    `BranchManager.pinned(proj, staged_dir)` — ordinary service calls, so the
    kernel pool, the canonical mesh cache and the mates resolver are reused
    verbatim (a part already built on either branch reports `cached: true` with
    zero kernel builds). The report carries `built`, `failures`, `integrity`
    (dangling instances/mates — the structural damage a clean key-wise merge
    can do) and `interference`. Interference blocks **only newly introduced**
    pairs (the pre-merge target is checked under a second pin and the pair sets
    diffed) and is skipped with `skipped: "instances"` below 2 or above
    `MERGE_INTERFERENCE_MAX_INSTANCES = 40` instances, and with
    `skipped: "validation"` when a build or integrity failure already blocks.
  - Blocked validation raises `ValidationError` carrying
    `details.validation` + `details.merge_id` and leaves the staged merge on
    disk, so `merge_branch {allow_invalid: true}` finishes it without redoing
    the work; the landed commit message then records
    `Validation: FAILED (allow_invalid) — …` naming the failing part or pair.
  - Finalize (FR10): `commit-tree <tree> -p <target_head> -p <source_head>`
    with `Merged-by: <client identity>` and `Conflicts-resolved: N`, then
    `update-ref refs/heads/<target> <new> <old>` (compare-and-swap: a commit
    that landed on the target while the merge was staged fails it with a
    `conflict_error` instead of being clobbered), then `reset --hard` in the
    target's working tree, `undo_cursor.on_snapshot` under the *target's* key
    (so Cmd+Z restores the pre-merge target), and the two publishes.
- **New `agentcad/core/tools_versioning.py`** — registers `branch_create`,
  `branch_list`, `branch_switch`, `branch_delete`, `version_tag`,
  `list_versions`, `merge_branch`, `resolve_merge`, `merge_abort`,
  `merge_status`. On import it installs `service.branches = BranchManager(...)`
  (which registers `store.branch_resolver`), `service.merges`, and the
  per-branch write guard (`turnlock.check(store.lock_key(proj), client)`).
  When `git` is absent the pack registers **nothing** and installs no seams, so
  the product degrades to today's linear history (FEM-pack precedent). Tool
  descriptions state the ours = target / theirs = source convention and the
  resolution recipe, because agents read those and not the design docs.
- **New `agentcad/server/routes_versioning.py`** — `GET/POST
  /projects/{proj}/branches`, `POST …/branches/switch`, `DELETE
  …/branches/{name}`, `GET/POST …/versions`, `GET …/merge`, `POST …/merge`,
  `POST …/merge/resolve`, `POST …/merge/abort`. Body keys are whitelisted and
  `null` values dropped (never `**body`). `notfound_error`/`validation_error`/
  `conflict_error` payloads are re-raised so the app maps them to 404/422/409;
  `merge_conflict` deliberately stays an `{"error": …}` body at HTTP 200 so a
  UI can render the conflict list. The router is empty when the tool pack
  registered nothing.
- **`agentcad/core/project.py`** — `lock_key` now returns the project *name*
  whenever the resolved tree is the canonical one, instead of the path string,
  so a project on its default branch keeps bit-identical turn-lock and undo
  keys once the versioning pack (and therefore the resolver) is always
  installed. Branch trees still key by path.
- **New `tests/test_merge.py`** (25 cases) — merge-tree stage extraction and
  the git-version refusal; clean disjoint merge with two parents; fast-forward;
  already-up-to-date; script conflict (payload shape, markers, and the proof
  that nothing outside `.history/agentcad/` moved and no ref moved); manifest
  conflict keying; FR8 param-vs-script; partial and completing `resolve_merge`
  including `{"content": …}`; unknown-key refusal that preserves the staged
  merge; abort (and re-abort no-op); a staged merge of another pair refused;
  a turn held on the target blocking the merge; concurrent-target-move CAS;
  broken-script block then `allow_invalid` land; dangling instance; AC4
  interference block + land; pre-existing overlap not blocking; instance cap;
  zero-kernel-build cache reuse; and a hand-built mates fixture (no bundled
  example declares connectors) for mate re-resolution and dangling mates.
- **New `tests/test_versioning_api.py`** (10 cases) — registration and schemas,
  the no-git degradation (no tools, no seams, `lock_key` unchanged), argument
  validation, the branch/version/merge routes, whitelisted-body forwarding,
  `merge_conflict` at HTTP 200, 404/422 mapping, the `branch_changed` and
  `merge_completed` event payloads, and undo across a merge.

## Files
- `agentcad/core/merge.py` — new: merge orchestration, staging, validation
- `agentcad/core/tools_versioning.py` — new: the 10-tool pack + seam install
- `agentcad/server/routes_versioning.py` — new: the route pack
- `agentcad/core/project.py` — `lock_key` keeps the project name on the default
  branch
- `tests/test_merge.py`, `tests/test_versioning_api.py` — new suites
- `docs/changelog/0069-branch-merge-orchestration.md` — this entry

## Notes
- **Why the merge is recomputed on every `resolve_merge`** rather than patched
  in place: the merge is a pure function of (target head, source head,
  recorded choices), so re-running `merge-tree` + the manifest driver keeps the
  staged tree honest and makes partial resolution trivially correct. The
  recorded heads are compared first, so a branch that moved under a staged
  merge is a `conflict_error` with the staged state preserved rather than a
  silent resolve against stale content.
- **Choice shapes are validated before the staged worktree is rebuilt**, so a
  malformed `resolve_merge` cannot leave `merge.json` pointing at a directory
  that was already removed.
- `git merge-tree`'s `-z` output is parsed only for the tree oid and the
  conflicted-file section; the trailing informational-message section is
  treated as opaque, as the design spec requires.
- Deviation from the design spec: the finalize step syncs the target working
  tree with `reset --hard <merge commit>` instead of `git merge --ff-only`.
  The CAS `update-ref` has already moved the branch, so `merge --ff-only`
  would report "already up to date" and leave the files stale; `reset --hard`
  is the operation that actually matches tree to ref (the tree was verified
  clean and the turn lock is held).
- Deviation: routes map ordinary `AppError` types to 404/422/409 rather than
  returning every payload at HTTP 200. `merge_conflict` — the one type FR7
  fixes — still arrives at 200, which is what the conflict UI needs; the rest
  behave like every other REST route in the app.
- Follow-ups for slice 4: the frontend must check `res.error` in addition to
  catching `ApiError` on `POST /merge` and `/merge/resolve`, re-enter a staged
  merge via `GET /merge`, and treat `merge_completed.validation.ok === false`
  as the "landed with failures" case.
