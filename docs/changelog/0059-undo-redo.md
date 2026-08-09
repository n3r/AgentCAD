# 0059 — Undo/redo cursor: the `undo` branch, reconciled onto git-backed history

- **Commit:** pending
- **Date:** 2026-08-09
- **Author:** Nikita Fedorov + Claude

## Summary

Lands the `undo` branch (originally commit 5d015b8: an in-memory two-stack
`HistoryManager` with Ctrl+Z/Cmd+Z in the UI), reconciled against main's
later git-backed `ProjectHistory` — the two implementations collided on the
same file, seam, and keyboard shortcut. The resolution keeps git as the sole
durable engine and ports the branch's real value on top: true one-keystroke
**redo**, the `undo`/`redo`/`get_history` tools, POST `/undo`|`/redo`
routes, and the ↩/↪ toolbar pair — while dropping the branch's per-callsite
`checkpoint()` burden (main's `EventBus.on_publish` snapshot hook already
covers every mutation path, packs included).

## Changes

- **`UndoCursor`** (`core/history.py`): in-memory two-stack cursor over the
  linear git history. Each real mutation snapshot pushes `{commit, label}`
  onto the undo stack (bound 100) and clears redo; `undo` restores the top
  mutation's *parent* tree, moving the entry to redo; `redo` restores it
  back. Every step rides `ProjectHistory.restore`, so the durable history
  stays append-only. Post-restart degradation: one fallback step via the git
  log, refused when the latest snapshot is itself a restore (prevents
  undo/redo oscillation). Root snapshots refuse cleanly ("nothing to undo")
  without consuming the stack entry. Turn-lock respected (`write_guard`
  invoked); `HistoryError` maps to validation errors, empty stacks to
  conflict errors. New `ProjectHistory` primitives: `head`, `parent_of`,
  `has_commit` (all hex-validated).
- **Service**: `_snapshot_on_event` feeds each snapshot's commit id + label
  to the cursor; `project_restore` records a *changed-head* manual restore as
  one undoable step (no-op restores record nothing).
- **Tools** `undo` / `redo` / `get_history` (`tools_undo.py`, from the
  branch, rewritten over the cursor — `get_history` is the label view; the
  durable commit log remains `project_history`). **Routes** POST
  `/projects/{proj}/undo` and `/redo` returning the fresh project payload
  (the branch's `GET /history` was dropped — it collided with
  routes_history's). Registry: 42 tools (45 with `[fem]`).
- **UI**: the branch's static ↩/↪ toolbar buttons replace the dynamically
  created Undo button; Cmd/Ctrl+Z → undo, Shift+Cmd/Ctrl+Z and Ctrl+Y →
  redo (text fields keep native editor undo); toasts name the undone/redone
  action; 409 empty-stack replies toast "Nothing to undo/redo".
- **Removed**: the branch's `service.history.checkpoint(...)` calls in
  tools_materials/tools_mates/routes_assembly2 (would have crashed against
  the git engine — the bus hook makes them unnecessary).
- Docs: agent-api rows for the three tools (+ the turn-lock/session rows
  moved into their own section — they had split the projects table in two),
  user-guide Undo/Redo paragraph rewritten for the durable-history
  semantics, AGENTS.md seam note corrected, tool counts 39/42 → 42/45.
  The branch's design docs (`docs/superpowers/specs|plans/2026-08-09-undo-
  redo*`) are kept as the original design record.

## Files

- `agentcad/core/history.py` — UndoCursor + head/parent_of/has_commit
- `agentcad/core/service.py` — cursor wiring in the snapshot hook
- `agentcad/core/tools_undo.py`, `agentcad/server/routes_undo.py`
- `agentcad/core/tools_history.py` — manual restore feeds the cursor
- `agentcad/core/tools_materials.py`, `agentcad/core/tools_mates.py`,
  `agentcad/server/routes_assembly2.py` — checkpoint residue removed
- `frontend/index.html`, `frontend/js/main.js`, `frontend/js/api.js`
- `tests/test_history.py` — 9 new cursor tests (round-trip, multi-level,
  redo-clears, labels, root refusal, restart fallback + oscillation guard,
  manual-restore undo, turn-lock, route payloads)
- `AGENTS.md`, `docs/agent-api.md`, `docs/user-guide.md`, `README.md`,
  `docs/architecture.md`

## Notes

This entry was renumbered from the branch's original `0034-undo-redo.md`
(0034 was claimed by light-ui on main) and rewritten from the reconciled
diff — the original commit message and design docs describe the in-memory
implementation as it was authored. Undo/redo stacks are process-memory by
design (the durable record is git); labels come from snapshot messages, so
they read as `project_changed <part>` rather than the branch's verb-style
labels — a follow-up could thread richer labels through the event reasons.
