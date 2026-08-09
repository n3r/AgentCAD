# 0027 — v2 Wave 2: frontend, docs, examples

- **Commit:** 9ab821b
- **Date:** 2026-08-09
- **Author:** Claude Fable 5

## Summary
Surfaces the Wave 1 backend in the UI: on-canvas move/rotate gizmos with a
numeric placement panel, a material picker, CAD import, drawing preview, and
inline analysis. Also updates all docs to as-built v2 and adds a mated rocketry
example plus a new fasteners demo.

## Changes
- **Gizmos** (`viewport.js`): vendored `TransformControls`; `setGizmo`/
  `setGizmoMode`/`setGizmoSnap` attach a move/rotate gizmo to the selected
  instance's group (tagged via `userData.instanceId`), emit live/commit
  transforms in the assembly's intrinsic-XYZ-degrees convention, disable orbit
  while dragging, suppress the click-as-selection after a grab, and detach on
  content clear so it never renders against a destroyed group.
- **Placement panel** (`placement.js`, new): floating card mirroring the gizmo —
  numeric x/y/z + rx/ry/rz, Move/Rotate toggle (g/r), Shift-to-snap hint
  (1 mm / 5°), and a "no scale handle — resize via Parameters" explainer. For
  mate-driven instances it shows a read-only derived-transform note instead.
- **Instance write-back** (`main.js`, `api.patchInstance`): commits go through
  `PATCH .../assembly/instances/{id}`; 409 surfaces "positioned by a mate".
  `updateGizmo` wires selection → gizmo → placement.
- **Materials** (`inspector.js`, `api.listMaterials`): a categorized `<select>`
  material picker with density/E/yield/service-temp props, source, and caveat;
  changing it calls `updatePart` and rebuilds. Materials load per project.
- **CAD import** (`main.js`, `api.uploadImport`/`callTool`): toolbar Import
  button uploads a `.step/.stp/.brep/.stl` raw body to `.../imports`, prompts
  for a part id, then calls the `import_cad_file` tool; the sidebar/inspector
  badge reference parts (kind learned lazily via `learnPartKind` / `partKinds`).
- **Reference-aware inspector**: hides the Code tab and shows a provenance pane
  (source/format, mesh-only flag) for imported parts; analysis buttons are
  disabled for references.
- **Drawings** (`drawings.js`, new): Export menu gains "SVG preview" (in-app
  modal, object-URL image + download) and "DXF" (written to exports/, toast);
  both script-part only.
- **Analysis** (`inspector.js`): Section/Wall/Inertia buttons run `analyzePart`
  and render result cards that survive metric re-renders.
- **Registry/route tolerance**: `ToolRegistry` skips type-checking a `None`
  value on an optional arg; the analyze route only forwards `min_required` when
  non-null.
- **Vendor pin** (`scripts/vendor_frontend.sh`): three pinned to 0.185.1 so the
  copied `examples/jsm` controls match the committed core; adds
  `TransformControls.js`.
- **Docs & examples**: README/agent-api/part-authoring/user-guide/architecture/
  roadmap rewritten to as-built v2; `templates.py` part-authoring guide gains
  robustness toolkit, sketch solver, threads, and connectors/mates sections;
  rocketry example converted to mates (nozzle datum + flange/injector
  connectors); new `fasteners` M8 bolted-joint example.

## Files
- `frontend/js/viewport.js` — TransformControls gizmo lifecycle + transform read
- `frontend/js/placement.js` — numeric/mate-aware placement panel (new)
- `frontend/js/drawings.js` — SVG-preview modal + DXF save (new)
- `frontend/js/inspector.js` — material picker, analysis actions, reference pane
- `frontend/js/main.js` — gizmo wiring, instance PATCH, import flow, drawing menu, g/r keys
- `frontend/js/api.js` — patchInstance, listMaterials, analyzePart, drawing, uploadImport, callTool
- `frontend/js/state.js`, `frontend/js/tree.js`, `frontend/index.html`, `frontend/css/app.css` — state fields, ref badge, import/placement/drawing DOM + styles
- `agentcad/core/tools.py` — optional-arg null tolerance
- `agentcad/core/templates.py` — expanded part-authoring guide (toolkit/sketch/threads/mates)
- `agentcad/server/routes_analysis.py` — omit null `min_required`
- `scripts/vendor_frontend.sh`, `frontend/vendor/*` — pin three, vendor TransformControls
- `docs/*`, `examples/rocketry/*`, `examples/fasteners/*` — as-built docs + mated/fastener examples

## Notes
Frontend is offline-only (no CDN); three is pinned so vendored controls share
the exact core lineage. Full suite: 133 passed, 1 skipped.
