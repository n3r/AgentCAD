# 0049 — Git-backed project history: automatic snapshots + undo/restore

- **Commit:** pending
- **Date:** 2026-08-09
- **Author:** Claude (with Nikita Fedorov)

## Summary

Every project mutation now snapshots into a per-project git repository
(roadmap "Undo/redo & history"): time travel via a `project_restore` tool and
a toolbar Undo (Cmd/Ctrl+Z), with derived data excluded and history kept
linear.

## Changes

- **`agentcad/core/history.py`** (new): stdlib subprocess-git driver. Repo at
  `<project>/.history` (`--git-dir`/`--work-tree`, nothing inside the project
  tree itself), lazy init with local identity + `commit.gpgsign false`,
  `info/exclude` for `.cache/`, `exports/`, `.history/`, `*.tmp`. Hermetic
  env (`GIT_CONFIG_NOSYSTEM`, HOME redirected) so user gitconfig/hooks can't
  break CAD saves; every call has a 10 s timeout; `snapshot` never raises.
  `restore` validates commit ids against a hex-only regex (blocks option
  injection), probes with `cat-file -e`, checks out the tree, and appends a
  `restore <id>` commit — history stays linear (redo = restore the pre-undo
  id).
- **Event-bus snapshot hook**: `EventBus.on_publish` (exception-guarded
  pre-fan-out seam); the service snapshots on every `project_changed` —
  covering service methods AND pack mutations (mates/materials/PMI/solids)
  with one hook. `set_params` now publishes `project_changed` (param
  overrides are authored state and must snapshot); part-CRUD events carry
  `"part"` for readable snapshot messages.
- **Tools** `project_history` / `project_restore` (turn-lock-aware: restore
  invokes the write guard explicitly since it writes outside the store choke
  point; `in_restore` suppresses the double snapshot). **Routes**
  `GET .../history`, `POST .../restore`. Git missing → graceful degradation
  (`available: false`, mutations unaffected).
- **UI**: toolbar Undo button + Cmd/Ctrl+Z (skips text fields/CodeMirror;
  Shift+Z untouched); restores `history[1]`; refresh rides the debounced
  `project_changed` path.

## Files

- `agentcad/core/history.py`, `agentcad/core/tools_history.py`
- `agentcad/core/service.py`, `agentcad/server/routes_history.py`
- `frontend/js/main.js`, `frontend/js/api.js`
- `tests/test_history.py` — 8 tests incl. the cache-consistency invariant
  (restore reverts script AND metrics rebuild to the old volume) and
  `.cache`/`exports` exclusion proven via `git ls-files`
- `docs/agent-api.md`, `docs/user-guide.md`

## Notes

`in_restore` is process-global: a concurrent mutation to a *different*
project during a sub-second restore window folds into that project's next
snapshot rather than getting its own (acceptable v1; per-project flag is the
upgrade path). Restore overlays tracked content and never `git clean`s —
scripts added after the target survive as invisible orphans until deleted.
Snapshot latency ~10–30 ms synchronous per mutation.
