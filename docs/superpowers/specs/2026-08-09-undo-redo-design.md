# Undo/Redo design — service-layer snapshot history

**Date:** 2026-08-09 · **Status:** approved for implementation

## Problem

There is no undo. When a user moves an assembly instance, changes a
parameter, edits a script, or deletes a part — directly in the browser or
through the chat agent / MCP — the only way back is to remember the old
value and re-enter it. Ctrl+Z / Cmd+Z does nothing.

## Goals

- Ctrl+Z / Cmd+Z in the browser undoes the last project mutation;
  Ctrl+Shift+Z / Cmd+Shift+Z (and Ctrl+Y) redoes it.
- Undo covers **every** mutation path — browser UI, chat agent, MCP — with
  one shared per-project history, because all three are peers over the same
  service layer. A user can undo what the agent just did.
- Undo is fast: restoring a previous state must not re-run the kernel when
  the geometry was already built (the content-hashed `.cache` guarantees
  this — old `.acm` files are never deleted).
- Agents get `undo` / `redo` tools; the REST API gets undo endpoints.

## Non-goals (YAGNI)

- Persistence across server restarts (in-memory, like chat history). The
  roadmap's git-backed history remains the future durable option; this
  design isolates history behind one seam so a git backend can replace the
  in-memory store later without touching callers.
- Undo of project creation/open, exports, drawings, analysis (derived
  outputs, not project state).
- Text-level undo inside the code editor — CodeMirror already owns that;
  the global shortcut must not shadow it.
- Coalescing rapid param-slider flushes into one step (the UI's 250 ms
  debounce already batches; revisit only if real use shows noise).

## Chosen approach: snapshot two-stack in the service layer

The complete mutable state of a project is `project.json` + `parts/*.py`
(imports are content files referenced by the manifest; `.cache/` and
`exports/` are derived). A snapshot of those files is a complete,
internally consistent undo unit, small (KBs), and cheap to capture.

Alternatives rejected:
- **Frontend inverse-operation stack** — cannot see agent/MCP mutations
  (they arrive only as `project_changed` events with no diff), cannot
  invert `delete_part` (script bytes are gone), desyncs under concurrent
  agent edits.
- **Git-backed history (roadmap idea)** — durable and unbounded, but
  heavyweight for interactive Ctrl+Z: repos inside project dirs, redo via
  reflog, large import blobs in commits. Better as a later timeline
  feature behind the same seam.

## Components

### 1. `agentcad/core/history.py` — `HistoryManager` (new module)

```
HistoryManager(store: ProjectStore, bus: EventBus, lock, on_restore, limit=50)
  .checkpoint(project: str, label: str) -> None   # capture before-image
  .undo(project: str) -> dict                     # restore, return info
  .redo(project: str) -> dict
  .status(project: str) -> dict                   # {undo: [labels], redo: [labels]}
```

- **Snapshot** = `{relpath: bytes}` for `project.json` and every
  `parts/*.py`, plus a content hash (sha256 over sorted file hashes).
  Stored per project in `undo: deque(maxlen=50)` and `redo: list`.
- **`checkpoint(project, label)`** is called by every mutating entry point
  *after validation, immediately before the first store write*. The label
  names the action about to happen ("Change params of box", "Move
  box_1"). Pushing clears the redo stack. If the new snapshot's hash
  equals the top entry's hash (an earlier op failed before writing), the
  top entry is replaced instead of stacking a duplicate.
- **`undo(project)`** (under the service lock): skip-and-drop top entries
  whose hash equals the current state hash (no-op guard), then push the
  *current* state onto redo (labeled with the popped entry's label),
  restore the snapshot's files, call `on_restore(project)`, publish
  `project_changed`, and return `{"undone": label, ...}`. Empty stack →
  `ConflictError("Nothing to undo")` (→ HTTP 409). `redo` is symmetric.
- **Restore** = atomic-write each snapshot file (`ProjectStore.
  _atomic_write`), delete `parts/*.py` files not present in the snapshot.
  Import source files under `imports/` are never deleted (an undone import
  leaves an orphaned upload; redo or re-import reuses it).
- Thread-safe via the service's existing `RLock`, passed in (reentrant, so
  checkpoint-under-service-lock works).

### 2. Service wiring (small, deliberate core edits)

`AgentCADService` gains a seam like `self.materials`:
`self.history = HistoryManager(store, bus, self._lock, self._forget_status)`
where `_forget_status(project)` pops that project's `_status` entries so
the next `get_part`/mesh access re-resolves build state (it lands on the
content-hash cache, so restored states report `cached: true`).

One `self.history.checkpoint(...)` line in each mutating method:
`create_part` ("Add part X"), `update_part` ("Edit script of X" / "Edit
part X" when only label/material), `set_params` ("Change params of X"),
`delete_part` ("Delete part X"), `set_assembly` ("Replace assembly").

The three writers that bypass the service get the same one line:
- `routes_assembly2.py` instance PATCH → "Move <instance_id>"
- `tools_mates.py` `_set_instance_mate` → "Set mate on <id>" / "Clear mate on <id>"
- `tools_materials.py` → "Edit project materials"

`import_cad_file` needs nothing: it goes through `create_part`.

### 3. Route pack — `agentcad/server/routes_undo.py`

- `POST /api/projects/{proj}/undo` → `{"undone": label, "history":
  {"undo": n, "redo": m}, "project": <post-state>}` (mutations return
  post-state, per convention). 409 when empty.
- `POST /api/projects/{proj}/redo` → same shape with `"redone"`.
- `GET  /api/projects/{proj}/history` → `{"undo": [labels…], "redo": […]}`.

### 4. Tool pack — `agentcad/core/tools_undo.py`

Tools `undo`, `redo` (arg: `project`) and `get_history`, thin wrappers
over `service.history`, so agents can revert their own missteps.

### 5. Frontend

- **Shortcut**: in `main.js setupKeys()`, alongside the existing Cmd+S
  branch and *above* the `inField || metaKey || …` early-return:
  Cmd/Ctrl+Z → undo, Cmd/Ctrl+Shift+Z or Ctrl+Y → redo. Suppressed when
  the event target is a text-editing context (`.CodeMirror`, `textarea`,
  text-like `<input>`, contenteditable) so editor and field-local undo
  keep working; `input[type=range]` (param sliders) does **not** suppress.
- **Actions**: `api.undo/redo/history` in `api.js`; `actions.undo/redo` in
  `main.js` call the endpoint, toast "Undid: <label>" (or "Nothing to
  undo" on 409), reset the `localPatchUntil` echo-suppression window, and
  explicitly `refreshProject()` — not relying on the WS `project_changed`
  echo, which the post-gizmo suppression window could swallow.
- **Toolbar**: two compact Undo/Redo buttons next to Fit (there is no Edit
  menu, only Project/Export dropdowns), tooltips showing the shortcut.
  They call the same `actions.undo/redo`.

## Data flow (undo of a param change)

1. UI PATCHes params → `set_params` checkpoints ("Change params of box"),
   merges, rebuilds (new cache key), UI shows new mesh.
2. User presses Cmd+Z → `POST …/undo` → HistoryManager restores the prior
   `project.json`, drops the part's `_status`, publishes
   `project_changed`, returns post-state + label.
3. UI toasts "Undid: Change params of box" and refreshes; `get_part`
   triggers `_ensure_built`, which finds the old cache key's `.acm` on
   disk → `rebuild_finished {cached: true}` → mesh swaps back with no
   kernel work.

## Error handling

- Empty stacks → `ConflictError` → 409 → UI toast, no state change.
- A checkpoint whose operation later fails leaves a same-hash entry; the
  no-op guard in `undo` and the replace-on-push rule keep the stack clean.
- Restores are atomic per file; a crash mid-restore leaves valid files
  (same guarantee as every other store write).
- History memory is bounded: 50 snapshots × ~KBs per project.

## Testing

- `tests/test_history.py` (service-level, session `kernel` fixture):
  param undo/redo round-trip incl. mesh cache-hit; script-edit undo;
  instance-move undo (via route, `TestClient`); delete-part undo restores
  the script file; add-part undo removes it; redo cleared by a new action;
  stack bound; empty-stack 409; no-op-checkpoint guard; `project_changed`
  published on undo.
- Route/tool registration asserted in the existing server/tools test
  patterns (`base_url="http://127.0.0.1"`, `extra_allowed_hosts`).
- Browser verification (definition of done): drive the real app — move an
  instance, Cmd+Z, watch it return; change a param, Cmd+Z; verify
  CodeMirror's own undo still works in the Code tab; zero console errors.
