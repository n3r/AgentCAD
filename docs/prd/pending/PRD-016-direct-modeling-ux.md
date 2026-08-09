# PRD-016 — Direct modeling and measurement UX

- **Status:** pending
- **Phase:** v5 — daily-driver depth
- **Created:** 2026-08-09
- **Origin:** competitive analysis (Aug 2026) · founder idea #3 (powerful manual CAD with AI assistance)
- **Depends on:** PRD-010 (soft — hole standards behind hole-on-face) · PRD-013 (soft — drag-to-place at assembly scale) · PRD-026 (soft — the shell hosting these controls)
- **Related:** PRD-009 (sketcher v2 — sketch-entity selection), PRD-002 (emitted edits are reviewable diffs), PRD-008 (comment anchors share the entity-ref vocabulary), PRD-018 (selection context grounds generation)

## Problem & motivation

The viewport can show and select, but barely *do*: you can pick a face, read
its area on the face card, and push/pull it (`push_pull`, recorded as a
visible `push_face(...)` script edit); everything else — measuring a
distance, checking a draft angle, adding a fillet where you're pointing,
seeing inside an assembly — means writing code or asking the agent with ids.
There is no measure tool, no section view, no curvature/zebra display
(though `analyze_part kind=curvature` already computes the data), no way to
drag an instance into a mate, and the chat agent cannot see what your cursor
sees — "fillet this edge" requires knowing an index.

The competitive evidence: Shapr3D proves adaptive direct+parametric UX
*sells* (market_research.md, "The workflow ring"); Solid Edge 2026 ships AI
magnetic-snap mating ("The desktop incumbents"); Zoo's marketing treats
selection-aware assistance as the ergonomic baseline ("AI-native CAD").
Founder idea #3 states the house position: powerful manual CAD *with* AI
assistance — not a chat window bolted onto a viewer. The architecture makes
our version different: every direct manipulation is an **emitter of code**
(the push/pull precedent — GUI writes visible script edits, never a hidden
feature store), so manual work stays reviewable (PRD-002), undoable
(history), and legible to agents. Humans steer best by pointing; the point
must become data.

## Users & jobs

- **Design engineer (human):** measure, section, and finish (fillet/chamfer/
  hole) by pointing at geometry — with the result landing in the script they
  can read, tune, and keep.
- **Surfacing/quality engineer (human):** judge continuity and wall trends
  visually — zebra stripes, curvature maps, live sections — using analysis
  the kernel already runs.
- **Assembly builder (human):** drag a part near its home and accept a
  suggested mate instead of naming connectors in a form.
- **Chat/design agent:** receive the user's current selection as structured
  context, so "make this wall 1 mm thicker" resolves to a face index without
  interrogation; use `measure` for exact B-rep dimensions instead of
  eyeballing renders.

## Goals

- G1. Exact measurement (distance, angle, radius, edge length) between
  picked entities — computed on the B-rep in the kernel, not the mesh — as
  both a viewport tool and an agent tool.
- G2. Live viewport section views and analysis overlays (curvature color
  map, zebra stripes) plus appearance modes, reusing existing analysis data.
- G3. The direct-manipulation family grows beyond push/pull —
  fillet/chamfer from selection, extrude-from-face, hole-on-face — every one
  emitting a visible, editable script edit. The GUI is an emitter of code,
  never a bypass.
- G4. Edges become first-class selectable entities (today only faces have a
  picking sidecar).
- G5. Drag-to-place instances with connector-aware mate snapping: proximity
  + type compatibility suggests mates; accepting writes `set_mate`.
- G6. Selection-aware chat: the current selection (part / face / edge /
  instance / sketch entity) rides into agent context as structured data, so
  pointing composes with every tool.

## Non-goals

- History-free direct modeling (editing geometry without a script trace) —
  the inversion is the product; a bypass mode will never ship.
- Sketch drawing/editing UX — PRD-009 owns the sketcher; this PRD only
  carries sketch-entity selection into chat context.
- Feature vocabulary itself (hole standards tables, pattern dialogs) —
  PRD-010; this PRD provides the on-face placement gesture that fronts it.
- Full shell/IA redesign, toolbars, command palette — PRD-026.
- Rendering-quality work (ray-traced appearance, HDRI environments) —
  appearance modes here are engineering displays, not marketing renders.

## Experience

**Human path.** A small viewport toolstrip (hosted by PRD-026's shell when
it lands; a minimal strip before): **Measure** (M) — click two entities,
read distance/angle in a HUD chip and a dimension overlay; click one edge
for length/radius, one cylindrical face for diameter. **Section** — drag a
plane through the model (XY/XZ/YZ + offset slider); geometry clips live;
Phase 2 caps the cut with hatching from the kernel's section outlines.
**Overlays** — Shaded / Edges / Hidden-line / Material colors / Curvature /
Zebra. Selecting an edge highlights it (fat-line hover targeting); the
entity card (the face card generalized) shows measurements plus actions:
**Fillet…**, **Chamfer…** (edge), **Push/Pull**, **Extrude…**, **Hole…**
(face). Each action previews, then *appends visible code* — the editor
scrolls to the new `fillet_edges(...)` block, exactly like push/pull today,
and the rebuild proves it. Dragging an unmated instance near another
(assembly mode) ghosts candidate connector pairs; releasing on a highlighted
candidate writes the mate; Esc cancels.

**Agent path.** `measure {a, b?}` returns exact values with the entity refs
echoed. The new mutating tools (`fillet_edges`, `chamfer_edges`,
`extrude_face`, `hole_on_face`) mirror `push_pull`'s contract: validate,
append a script wrapper, rebuild, return post-state. `get_selection` returns
the user's live selection; in built-in chat the same payload is injected
into the turn automatically, so "fillet this edge at 2 mm" becomes
`fillet_edges` on the selected indices with no id round-trip.

**Handoff.** The human points; the agent acts on the pointed-at entity; the
result is a script diff both can read — and PRD-002 can review.

## Functional requirements

**Selection & entity refs**
- FR1. A shared entity-ref shape used by tools, chat context, and (PRD-008)
  comment anchors: `{project, part_id?, instance?, face_index?, edge_index?,
  sketch_entity?}` — face indices in mesh order (the existing
  `mesh/faces` sidecar convention), edge indices in a new stable edge order.
- FR2. An edge sidecar (`GET …/mesh/edges`) maps the rendered edge-overlay
  segments to B-rep edge ordinals, cached next to the mesh like the faces
  sidecar; a new `edge_info` tool mirrors `face_info` (`{kind: line|circle|
  arc|spline, length_mm, radius_mm?, center?, endpoints}`).
- FR3. Selection state (single or multi-entity) is tracked per browser
  session and published to the server (debounced) so `get_selection` and
  chat-context injection see what the viewport shows; stale selections
  (after a rebuild changed topology) are invalidated client-side.

**Measurement**
- FR4. `measure {project, a, b?}` computes on the B-rep in a kernel handler:
  entity–entity minimum distance, angle (face–face, edge–edge,
  face–edge), radius/diameter (circular edge, cylindrical face), edge
  length, and point coordinates; results carry `kind`, value(s) in mm/deg,
  and the closest-point pair for drawing the overlay. Mesh-only reference
  parts measure against the mesh with `approx: true` flagged.
- FR5. The viewport measure tool draws the dimension (leader + value) as an
  overlay, keeps it until dismissed, and shows the same numbers the tool
  returned — one source of truth.

**Sections & overlays**
- FR6. Live section view: a clip plane (axis-aligned XY/XZ/YZ + offset in
  v1) applied to part or assembly rendering client-side; the HUD names the
  active section. Phase 2: capped sections using section outlines from the
  analysis pack (the same machinery PRD-014 FR6 uses).
- FR7. Curvature overlay: per-face color mapping from
  `analyze_part {kind: "curvature"}` (gaussian K / mean H per face, already
  computed) applied via the faces sidecar; a legend shows the scale; the
  overlay re-fetches after rebuilds.
- FR8. Zebra overlay: shader-based reflective stripes (client-only, no new
  data) for visual continuity judgment on the surfacing toolkit's output.
- FR9. Appearance modes: shaded (today), shaded+edges, hidden-line,
  material colors (per-part material category → color, per-solid materials
  honored), matcap. Mode is view state, never persisted into the model.

**Direct manipulation emits code**
- FR10. `fillet_edges {project, part_id, edge_indices, radius_mm}` and
  `chamfer_edges {…, distance_mm}` append a visible toolkit wrapper (the
  `push_face` pattern: rename `build` to `_agentcad_prev_build_<n>`, define
  a new `build(p)` applying `toolkit.facemod.fillet_edges(...)`) and
  rebuild; fillet uses `safe_fillet` underneath so partial success returns
  the achieved radius + warning, matching house honesty.
- FR11. `extrude_face {project, part_id, face_index, distance_mm}` adds a
  prismatic boss (positive) or pocket (negative) from the face's outer wire —
  distinct from `push_pull`'s whole-face offset; planar faces only
  (`validation_error` otherwise, same guard as push/pull).
- FR12. `hole_on_face {project, part_id, face_index, at, diameter_mm,
  depth_mm?}` drills at `at` (`[u, v]` in face frame or `[x, y, z]` world,
  projected); through-hole when `depth_mm` omitted. When PRD-010 lands, a
  `standard` arg (e.g. `"M5 clearance"`) supersedes raw diameter and stamps
  hole metadata for PRD-014's hole tables.
- FR13. Every FR10–FR12 edit is one history snapshot (undoable via Cmd+Z),
  visibly composable (repeated actions append blocks), and round-trips: the
  emitted code rebuilds to the same geometry on a fresh session
  (determinism contract).

**Drag-to-place & snapping**
- FR14. Dragging an unmated instance (existing gizmo path) computes mate
  suggestions server-side — `mate_suggestions {project, instance}` ranks
  compatible connector pairs (moving rigid ↔ anchor rigid/revolute/
  cylindrical/PRD-013 types) by frame distance and alignment; the client
  ghosts the top candidates within a snap radius.
- FR15. Accepting a suggestion calls `set_mate` (the manifest is the record;
  no client-side pose forking); mated instances keep refusing direct
  transforms exactly as today.

**Selection-aware chat**
- FR16. Built-in chat turns automatically carry the current selection
  (FR1 shape + human-readable summary: "face 12 of part `nozzle`: planar,
  1252 mm², normal +Z") in the turn context; MCP agents call
  `get_selection`. The injected context names entities only — never
  substitutes for tool calls.
- FR17. "This/here" resolution is testable: with a face selected, "make
  this wall 1 mm thicker" must resolve to a tool call referencing that
  face's part and index without the agent asking for ids.

## Agent surface

New tools: `measure {project, a, b?}` · `edge_info {project, part_id,
edge_index}` · `fillet_edges {project, part_id, edge_indices, radius_mm}` ·
`chamfer_edges {project, part_id, edge_indices, distance_mm}` ·
`extrude_face {project, part_id, face_index, distance_mm}` ·
`hole_on_face {project, part_id, face_index, at, diameter_mm, depth_mm?}` ·
`mate_suggestions {project, instance}` · `get_selection {project?}`.
All mutating tools follow the house contract: validate → append visible
script edit → rebuild → return post-state (metrics, warnings, the emitted
block's line range). New event: `selection_changed {project, client,
selection}` (drives presence/PRD-008 later; server keeps only the latest per
client). Errors: `validation_error` for non-planar/unsuitable entities with
the entity echoed in details.

## Technical approach

- **Kernel:** new handler pack `handlers/measure.py` (BRepExtrema distance,
  angle/radius from surface/curve adaptors); the mesh pipeline
  (`kernel/mesh.py`) emits the edge sidecar alongside the existing
  faces-in-mesh-order mapping; `toolkit/facemod.py` grows `fillet_edges` /
  `chamfer_edges` / `extrude_face` / `hole_on_face` helpers (script-callable,
  like `push_face`).
- **Tool pack** `tools_measure.py` + extensions in `tools_facemod.py` (the
  wrapper-appending machinery there is the template — same rename-and-wrap
  emission); **route pack** `routes_measure.py` plus the edge-sidecar route
  beside the faces route in `app.py`'s static-mesh section.
- **Selection plumbing:** `state.js` holds the multi-entity selection;
  a debounced POST publishes it; `chat.py` injects the stored selection into
  the system context per turn — additive, no registry change.
- **Frontend:** `viewport.js` (edge picking via fat-line raycast threshold,
  clip planes, overlay materials/shaders for curvature/zebra/matcap,
  suggestion ghosts), `inspector.js` (entity card actions with preview),
  `placement.js` (drag-snap accept/cancel affordances).
- **No manifest changes**; emitted edits are ordinary script writes through
  `service.update_part` (turn locks, history, events all apply for free).

## MVP & phasing

- **MVP:** measure tool + `measure`/`edge_info` + edge sidecar (FR1–FR5),
  clip-plane sections (FR6 v1), curvature + zebra overlays (FR7–FR8),
  `fillet_edges`/`chamfer_edges` from selection (FR10), selection-aware chat
  (FR3, FR16–FR17).
- **Phase 2:** `extrude_face` + `hole_on_face` (FR11–FR12), appearance
  modes (FR9), capped sections, mate snapping (FR14–FR15).
- **Phase 3 (with PRD-010/026):** standards-driven holes, toolstrip
  integration into the revamped shell, multi-entity measurement chains.

## Acceptance criteria

- AC1. With only a face selected in the browser, the chat prompt "make this
  wall 1 mm thicker" produces a correct tool call on that face's part —
  no id questions asked (scripted chat test with a stubbed selection +
  manual browser session).
- AC2. `measure` between two parallel faces of a box fixture returns the
  exact modeled distance (±1e-6 mm); edge length and cylindrical-face
  diameter match modeled values; a mesh-only STL measurement carries
  `approx: true` (tests).
- AC3. Fillet-from-selection on a picked edge appends a visible
  `fillet_edges` block, rebuilds green, and reports the achieved radius when
  `safe_fillet` fell back (test + browser session showing the editor
  scrolled to the emitted code).
- AC4. The section slider slices the rocketry assembly live in the browser;
  zebra stripes render on the surfacing example; the curvature overlay's
  per-face colors match `analyze_part` values for a known fixture (browser
  session + data-mapping test). Zero console errors throughout.
- AC5. Dragging the bracket near the plate's `hole1` connector ghosts the
  suggestion; accepting writes the same manifest mate `set_mate` would;
  Esc leaves the assembly untouched (browser session + suggestion-ranking
  test).
- AC6. Emitted-edit determinism: a script carrying appended
  fillet/extrude/hole blocks rebuilds identically on a fresh kernel session
  (test), and each action is one undo step (history test).
- AC7. Edge sidecar integrity: every rendered overlay segment maps to a
  valid B-rep edge ordinal and `edge_info` answers for each (test on the
  three bundled examples).
- AC8. Full suite green; `push_pull`, the face card, and existing gizmo
  behavior unchanged.

## Risks & open questions

- **Index fragility across rebuilds:** face/edge indices are mesh-order and
  shift when upstream topology changes, so emitted wrappers can silently
  target a different edge later. Mitigation: wrappers validate index bounds
  (the `push_face` guard) and record entity fingerprints (area/length +
  centroid) to warn on drift; true topological naming is explicitly out —
  document the limitation honestly.
- **Edge picking ergonomics:** thin edges need a generous raycast threshold
  without stealing face clicks; prototype early, tune with the drag
  threshold that already separates click from orbit.
- **Zebra/curvature honesty:** client shaders judge *rendered* meshes;
  coarse tessellation can fake discontinuities. Pair overlays with the
  numeric `curvature` analysis in the entity card, and say so in the UI.
- **Suggestion noise at scale:** dense assemblies (PRD-013) could ghost
  dozens of candidates; cap to top-3 within radius and require an explicit
  accept.
- **Selection privacy/scope:** selection context is injected only into
  sessions on the same project; MCP agents get it via `get_selection` only —
  no ambient broadcast beyond the `selection_changed` event's project
  channel.

## Competitive references

Shapr3D: adaptive direct+parametric UX as the purchase reason — proof the
manual experience sells (market_research.md, "The workflow ring"). Solid
Edge 2026: AI magnetic-snap mating — our FR14, grounded in declared
connectors instead of inferred geometry ("The desktop incumbents"). Zoo:
selection-aware copilot ergonomics, but code hidden from the engineer
("AI-native CAD"). Incumbent direct modeling (Fusion press-pull, SolidWorks
Instant3D) edits a feature store the user can't diff. We differ by the house
inversion: every gesture emits reviewable code, measurement answers come
from the same kernel the agent trusts, and the human's cursor becomes a
structured argument to the agent's next call — pointing and prompting are
one loop, not two products.
