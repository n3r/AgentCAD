# 0034 — Undo/redo: service-layer snapshot history with Ctrl+Z/Cmd+Z in the UI

- **Commit:** pending
- **Date:** 2026-08-09
- **Author:** Nikita Fedorov + Claude

## Summary
Adds undo/redo for every project mutation — parameter changes, instance
moves, script saves, part add/delete, mates, materials — backed by one
shared per-project history in the service layer, so the browser (Ctrl+Z /
Cmd+Z), the chat agent, and MCP clients all see and revert the same
timeline. A user can undo what the agent just did.

## Changes
- New `agentcad/core/history.py` — `HistoryManager`, an in-memory two-stack
  (undo `deque(maxlen=50)`, redo list) of byte snapshots of the complete
  mutable state (`project.json` + `parts/*.py`). `checkpoint(proj, label)`
  captures the pre-mutation state (called after validation, right before the
  first store write); `undo`/`redo` restore files atomically, invalidate the
  service's `_status` via an `on_restore` callback, publish
  `project_changed`, and return `{label, undo, redo}`. Snapshot content
  hashes give two guards: a checkpoint whose operation failed before writing
  is replaced (not stacked) by the next one, and `undo`/`redo` silently drop
  entries identical to the current state. Empty stack raises
  `ConflictError` (→ HTTP 409). Because the content-hash mesh cache is never
  garbage-collected, undoing an already-built state restores with
  `cached: true` and zero kernel work (asserted by test).
- `AgentCADService` gains the `self.history` seam and `_forget_status`;
  `create_part` / `update_part` / `set_params` / `delete_part` /
  `set_assembly` checkpoint with human labels ("Change params of box",
  "Delete part box", "Edit assembly"…).
- The three writers that bypass the service mutators checkpoint too:
  `routes_assembly2.py` instance PATCH ("Move <id>" — the gizmo/placement
  path), `tools_mates._set_instance_mate` ("Set/Clear mate on <id>"),
  `tools_materials.set_project_materials` ("Edit project materials").
- New route pack `agentcad/server/routes_undo.py`: `POST
  /api/projects/{proj}/undo` and `/redo` (return `undone`/`redone` label,
  stack depths, and full post-state project detail), `GET
  /api/projects/{proj}/history` (label lists, newest first).
- New tool pack `agentcad/core/tools_undo.py`: `undo`, `redo`,
  `get_history` — registry grows 25 → 28 tools (29 with `[fem]`).
- Frontend: Cmd/Ctrl+Z undoes, Shift+Cmd+Z / Ctrl+Y redoes — suppressed in
  text-editing contexts (CodeMirror keeps its own text undo; `input
  type=range` is deliberately *not* suppressed so undo works right after a
  slider drag). ↩/↪ toolbar buttons next to Fit. Toasts name the action
  ("Undid: Move b1"); an empty stack toasts "Nothing to undo". The undo
  action resets the `localPatchUntil` echo-suppression window and refreshes
  explicitly rather than relying on the WS `project_changed` echo.
- Docs: agent-api (new History section, 28 tools), architecture (service
  description + diagram count), user-guide (toolbar + shortcuts), roadmap
  (undo bullet → shipped; durable git-backed history stays future), AGENTS
  (fourth service seam: packs must checkpoint before mutating).

## Files
- `agentcad/core/history.py` — new: HistoryManager (snapshot two-stack)
- `agentcad/core/service.py` — history seam, `_forget_status`, 5 checkpoints
- `agentcad/server/routes_assembly2.py` — checkpoint "Move <id>"
- `agentcad/core/tools_mates.py` — checkpoint "Set/Clear mate on <id>"
- `agentcad/core/tools_materials.py` — checkpoint "Edit project materials"
- `agentcad/server/routes_undo.py` — new: undo/redo/history routes
- `agentcad/core/tools_undo.py` — new: undo/redo/get_history tools
- `frontend/js/api.js` — `api.undo` / `api.redo`
- `frontend/js/main.js` — `undoRedo` action, keydown branch, button wiring
- `frontend/index.html` — ↩/↪ toolbar buttons
- `tests/test_history.py` — new: 19 tests (store-level unit, service-level
  with kernel incl. cache-hit assertion, route + tool layer, 409s)
- `docs/agent-api.md`, `docs/architecture.md`, `docs/user-guide.md`,
  `docs/roadmap.md`, `AGENTS.md` — surface docs
- `docs/superpowers/specs/2026-08-09-undo-redo-design.md`,
  `docs/superpowers/plans/2026-08-09-undo-redo.md` — design + plan

## Notes
- History is in-memory (like chat history) by design; the `service.history`
  seam is where a durable git-backed store would slot in without touching
  callers.
- Undoing an import removes the part entry but leaves the uploaded file in
  `imports/` — harmless orphan, and redo/re-import reuses it.
- The transaction unit is one mutating entry-point call; the UI's 250 ms
  param debounce means a slider drag can produce a few steps rather than
  one. Coalescing was deliberately skipped (YAGNI) until real use shows
  noise.
- Verified end-to-end in a real (headless SwiftShader) Chromium against a
  scratch server: 15/15 checks including param and move round-trips via
  Cmd+Z/Shift+Cmd+Z, CodeMirror text-undo isolation, empty-stack toasts,
  and zero unexpected console errors. Full suite: 155 passed, 1 skipped
  (FEM extra absent).
- Gotcha found while verifying: `agentcad serve` auto-registers the bundled
  `examples/` as projects, and the UI opens the alphabetically-first one on
  a fresh browser profile — a browser-automation run must pin
  `localStorage["agentcad.project"]` or it will mutate
  `examples/construction` in the repo (this happened; the file was
  restored from git before commit).
