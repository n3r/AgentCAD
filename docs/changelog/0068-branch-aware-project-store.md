# 0068 — Branch-aware project store, worktree history, refs and tags

- **Commit:** pending
- **Date:** 2026-08-09
- **Author:** Claude (PRD-001 slice 2)

## Summary
Slice 2 of PRD-001: the substrate for branching. `ProjectStore` gains a
branch-resolution seam keyed by the existing client-identity ContextVar,
`ProjectHistory` learns to drive a linked git worktree, and a new
`BranchManager` (`agentcad/core/branches.py`) creates/lists/switches/deletes
branches, materializes their working trees under `.history/trees/<branch>/`,
and creates immutable versions (annotated tags). Covers FR1–FR5 and FR11–FR13
at the store/history layer. No merge logic, no tool/route pack, no UI — those
are slices 3 and 4; the seam is dormant until a `BranchManager` is
constructed, so behavior without it is bit-identical to before.

## Changes
- **New `agentcad/core/branches.py`**
  - `pinned_tree_var: ContextVar[Path | None]` — highest-precedence path
    override (slice 3's merge validation pass runs ordinary service calls
    against a staged worktree through it), set only inside
    `BranchManager.pinned(proj, path)`.
  - `BranchManager(service)` installs `store.branch_resolver` on construction;
    `resolve_path(proj, canonical)` resolves pinned tree → the calling
    client's checked-out branch → the canonical project directory, and never
    raises (a missing/unreadable tree degrades to canonical).
  - `create` / `list` / `switch` / `delete` / `tree_of` / `default_branch` /
    `current`, plus `tag` / `versions`. Branch names must match
    `^[a-z0-9][a-z0-9_/-]{0,63}$` and survive the ref-name rejects; tags
    additionally allow dots (`v1.2`). Deleting the default branch or a branch
    any client has checked out is a `validation_error`; re-tagging an existing
    version is a `conflict_error` (there is deliberately no tag-delete tool,
    which is what makes FR5 hold).
  - Worktree directory names sanitize `/` → `-` with collision
    disambiguation; `git worktree prune` runs before every `worktree add`
    because a tree deleted out from under git stays registered as *prunable*
    and blocks re-adding the same path (verified empirically on git 2.50.1).
  - Sidecar state under `.history/agentcad/` (inside GIT_DIR, never
    committed), written with `ProjectStore._atomic_write`: `config.json`
    (discovered default branch), `checkouts.json` (`clients` + `trees` maps),
    `tags.json` (version `referrers`, for PRD-015). Checkout entries naming a
    branch that no longer exists are dropped on load.
  - Migration is a no-op (Decision 8): the repo/first snapshot is created
    lazily on the first branching call and the existing linear history simply
    *is* the default branch's, discovered via `symbolic-ref --short HEAD` and
    pinned in `config.json` rather than assumed.
- **`agentcad/core/project.py`** — `branch_resolver` attribute; today's
  `_resolve` body is now `_locate` (canonical) while `_resolve` applies the
  resolver, so `manifest`/`save_manifest`/`script_path`/`read_script`/
  `write_script`/`exports_dir`/`imports_dir` become branch-aware unchanged.
  New `canonical_path_of(proj)` and `lock_key(proj)` (the project name without
  a resolver, the resolved tree path with one). `cache_dir` is repointed at
  `canonical_path_of` so `.cache/` stays shared and content-addressed across
  branches (FR13); `list_projects` maps each discovered project through the
  resolver so part counts and paths are the caller's branch's.
- **`agentcad/core/history.py`** — `_locate(project_path)` returns the GIT_DIR
  for a working tree, following a linked worktree's `.git` *file* to its admin
  dir; `_run` uses it for `--git-dir` and the hermetic `HOME`/`XDG_CONFIG_HOME`,
  and `_has_repo` accepts a `.git` file. `_ensure_repo` now self-heals
  `info/exclude` whenever its content differs (`_refresh_excludes`), so older
  projects pick up new entries. New ref primitives: `resolve_ref`, `branches`,
  `tags`, `log(..., ref=None)`, plus the module-level guards
  `looks_like_commit` and `valid_ref_name` (`_REF_RE` + rejects for `..`,
  `@{`, `.lock`, `//`, trailing `/` or `.`); `_COMMIT_RE`'s existing use is
  untouched. `UndoCursor` re-keys its stacks by `store.lock_key(proj)`
  (`_key`, with a getattr fallback), giving each branch its own undo/redo.
- **`agentcad/core/tools_locks.py`** — `acquire_turn`/`release_turn`/`get_turn`
  key the `TurnLock` by `store.lock_key(project)`, so two clients on two
  branches never contend (FR2). Without the resolver `lock_key` returns the
  project name, so existing behavior and tests are unchanged.
- **`agentcad/core/tools_history.py`** — `project_history` gains an optional
  `ref` (read another branch's or a tag's history without switching);
  `project_restore`'s `commit` now also accepts a branch or tag name, resolved
  to a commit before anything is written. Malformed refs stay
  `validation_error`; well-formed but unknown ones are `notfound_error`.
- **`agentcad/core/service.py`** (the one change this plan allots it) —
  `_content_signature` keys reference parts on a SHA-256 of the imported
  file's bytes instead of `path + mtime + size`. `imports/` is per working
  tree, so a checkout restamps the mtime of byte-identical content and would
  otherwise mint a new cache key per branch. One-time cache invalidation for
  existing reference parts; strictly more correct.
- **`tests/test_branches.py`** (new, 36 tests) — store resolver seam
  (kernel-free), history in a linked worktree, ref primitives, branch
  lifecycle and per-client switching, the snapshot hook committing to the
  mutating client's branch, per-branch turn locks and undo stacks, tags +
  byte-identical restore + survival of `branch_delete` (AC5), cross-branch
  cache reuse with zero kernel builds (FR13), the reference cache signature,
  and both empirically-flagged git behaviors as regression tests (a worktree
  deleted out from under git is pruned and recreated; `.history/trees/**` is
  never picked up by `git add -A`). Git-touching classes carry `integration`
  + `portability` and skip without git.

## Files
- `agentcad/core/branches.py` — new: `BranchManager`, `pinned_tree_var`
- `agentcad/core/project.py` — resolver seam, `canonical_path_of`, `lock_key`,
  canonical `cache_dir`, branch-aware `list_projects`
- `agentcad/core/history.py` — `_locate`/`_refresh_excludes`, ref primitives,
  `log(ref=…)`, branch-keyed `UndoCursor`
- `agentcad/core/service.py` — content-hashed reference cache signature
- `agentcad/core/tools_locks.py` — per-branch lock keys
- `agentcad/core/tools_history.py` — `ref` on `project_history`, ref names on
  `project_restore`
- `tests/test_branches.py` — new suite for all of the above
- `docs/changelog/0068-branch-aware-project-store.md` — this entry

## Verification
- `make test` → **416 passed, 1 skipped** in 18:01 (381 before this change,
  plus the 36 new cases; the skip is the pre-existing one).
- `make test-fast` → 381 passed, 1 skipped · `make test-portability` → 143
  passed · `uv run pytest -q tests/test_history.py tests/test_locks.py` → 25
  passed, both files unedited.

## Notes
- **`.history/trees/`, not `.history/worktrees/`** — the latter is git's own
  per-worktree admin path. Nesting our checkouts inside GIT_DIR is safe
  because every project's `info/exclude` already lists `.history/`; verified
  both by hand and by `test_branch_worktrees_are_never_tracked`.
- **`git worktree prune` is mandatory before re-adding**: after `rm -rf` of a
  linked tree, `git worktree list` still shows it as `prunable` and
  `worktree add` at that path fails with "missing but already registered".
  `worktree remove --force` on an already-deleted directory succeeds (rc 0).
- Behavior with no `BranchManager` constructed is unchanged: `path_of`,
  `lock_key`, `TurnLock` and `UndoCursor` all behave exactly as before, which
  is why every pre-existing test passes unedited (FR11).
- `switch` snapshots the tree it is leaving ("checkpoint before switch"); that
  commit is intentionally not pushed onto the undo stack — it is a boundary,
  not an edit.
- `versions()` sorts newest-first by `(ts, name)` descending: tag timestamps
  have one-second resolution, so the name breaks ties deterministically.
- Follow-ups (slice 3): `MergeOrchestrator`, `tools_versioning.py` (which
  installs `service.branches` and rewires
  `store.write_guard → turnlock.check(store.lock_key(proj), client)`), and
  `routes_versioning.py`. `switch` already publishes
  `branch_changed {project, client, branch}`.
