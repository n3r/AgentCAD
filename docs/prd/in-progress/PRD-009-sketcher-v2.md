# PRD-009 — Sketcher v2

- **Status:** pending
- **Phase:** v5 — daily-driver depth
- **Created:** 2026-08-09
- **Origin:** competitive analysis (Aug 2026) + explicit v3 residual
- **Depends on:** — (none hard)
- **Related:** PRD-010 (profiles feed patterns/features), PRD-016 (viewport selection and direct-modeling UX), PRD-018 (generation emits solver-checked sketches), PRD-029 (auto-constrain assistance as an agent skill later)

## Problem & motivation

v3 shipped a GUI sketcher over the first-party scipy constraint solver
(`agentcad/toolkit/sketch.py`, the `solve_sketch` tool, `frontend/
sketcher.js`) that emits clean build123d code — but its entity vocabulary is
points, lines, and circles. The competitive analysis lists "arcs/splines in
the sketcher" among what does not exist (market_research.md, "Where AgentCAD
stands today") and the Gap matrix verdict is blunt: "Sketcher completeness
(arcs/splines) — points/lines/circles — every incumbent — **build**". A
constraint sketcher without arcs reads as a toy, and it blocks real profiles:
brackets with tangent fillet runs, cams, ports, slotted plates.

The gap is doubly expensive here because the solver *is* the agent's 2D
vocabulary — `solve_sketch` is a registered tool, and what it cannot express
an agent cannot draw either. Completing entities and constraints completes
both layers at once. Two adjacent weaknesses go with it: the solver reports a
bare `dof` number but cannot say *which* constraints conflict when a sketch
over-constrains (agents and humans both need the conflicting set, not a
shrug), and drag-editing re-solves cold from the spec's coordinates — the
`initial` argument on the tool is literally documented "unused; reserved"
(`agentcad/core/tools_sketch.py`). Meanwhile the assistance baseline is
rising: Fusion ships AutoConstrain (market_research.md, "The desktop
incumbents"), and FreeCAD 1.x carries a mature sketcher as the OSS bar
("Open-source CAD: FreeCAD, code-CAD, and the Ondsel lesson").

## Users & jobs

- **Mechanical designer (human):** draw real profiles — tangent arcs, slots,
  splined blends — constrain them, drag to explore, and trust the solve not
  to jump to a mirrored solution mid-drag.
- **Constraint debugger (human):** see at a glance whether the sketch is
  under/well/over-constrained, and when over-constrained, which constraints
  to blame.
- **Design agent:** express a full profile as entities + constraints in one
  `solve_sketch` call, read exact coordinates and diagnostics back, and emit
  idiomatic `BuildLine`/`BuildSketch` code into the part script.
- **Generation loop (PRD-018):** a validated sketch substrate — solver-green
  profiles instead of guessed coordinates.
- **Feature helpers (PRD-010):** profiles for ribs and pattern seeds.

## Goals

- G1. Entity vocabulary in **both** layers (scipy solver + GUI sketcher):
  arcs, splines, ellipses and elliptical arcs, slots — with conic sections
  covered as elliptical arcs at minimum.
- G2. Constraint vocabulary on the new entities: generalized tangency
  (line–arc, arc–arc, arc–circle, spline end-tangency), symmetry about a
  line, equal length/radius, concentric.
- G3. Drag-to-solve: warm-started re-solves via the reserved `initial` hook,
  fast enough for mouse-move rates and stable against mirror flips.
- G4. Diagnostics as data: under/well/over-constrained status, DOF, Jacobian
  rank, and — on over-constraint — a conflicting/redundant constraint set,
  in the tool payload and highlighted in the GUI.
- G5. Sketch-on-face: start a sketch on a planar face of existing geometry
  and reference projected edges/vertices as constraint targets.
- G6. Emitted code stays clean, idiomatic build123d `BuildLine`/`BuildSketch`
  — reviewable, portable, no runtime dependency on the sketcher.

## Non-goals

- Full conic primitives (parabola/hyperbola segments) — elliptical arcs
  cover the CAD-practical cases; revisit on demand.
- 3D sketching — out entirely.
- Auto-constraint inference (AutoConstrain-style suggestions) — a natural
  agent task on top of this vocabulary (PRD-029 territory), not solver core.
- A sketch file format — the part script remains the only artifact.

## Experience

**Human path.** The sketcher toolbar grows arc (center, 3-point, tangent),
spline, ellipse, and slot tools; the constraint palette grows tangent /
symmetric / equal / concentric. A status chip reads "3 DOF", "fully
constrained", or "over-constrained (2 conflicts)" — clicking it highlights
the conflicting set in red. Dragging any point or handle re-solves live,
warm-started from the on-screen state, so the profile deforms continuously
instead of snapping to a mirrored solution. Right-clicking a planar face in
the viewport offers "Sketch on face": the sketcher opens in that face's
plane with existing edges ghosted as reference geometry that constraints can
target. Finish emits (or updates) the `BuildLine`/`BuildSketch` block in the
script — the CodeMirror pane shows the edit — and the normal rebuild runs.

**Agent path.** `solve_sketch {entities, constraints, initial?}` now takes
`arcs`, `splines`, `ellipses`, `slots` alongside points/lines/circles, and
the new constraint types. The response carries solved coordinates for every
entity plus `diagnostics`. An over-constrained sketch returns a
`validation_error` whose `details.diagnostics.conflicting` names the
constraint subset to fix — the agent removes or relaxes one and re-solves.
Iterative work (parameter studies, generation loops) passes the previous
solution as `initial` so each solve starts warm and lands on the same
branch. The agent then writes the profile into the part script; the
cheat-sheet documents the entity → build123d mapping.

**Handoff.** A human drags an agent-authored sketch in the GUI; both drive
the same solver over the same spec.

## Functional requirements

**Solver**
- FR1. New entities in `agentcad/toolkit/sketch.py` and the `solve_sketch`
  JSON spec: `arcs` (center + radius + start/end angles, or 3-point),
  `ellipses` (center, major/minor radii, rotation; optional arc bounds),
  `splines` (named control points, fixed degree), `slots` (two centers +
  width, compiled at spec ingestion into the two-arc/two-line composite with
  internal tangency and equal-radius auto-applied).
- FR2. New constraints: `tangent` generalized to line–arc, arc–arc,
  arc–circle, and spline end-tangency; `symmetric {a, b, about}` for point
  pairs and entity pairs about a line; `equal_length {l1, l2}`;
  `equal_radius` extended to arcs; `concentric {a, b}`; arc endpoints
  participate in `coincident` so chains close.
- FR3. Backward compatibility: every v1 spec (points/lines/circles + the 17
  existing constraint types) solves to identical results; existing tests
  and tool payloads unchanged.
- FR4. `initial` activated: `{points: {name: {x, y}}, circles: {name: {r}},
  arcs: {…}, …}` overrides starting coordinates without changing the spec;
  unknown names are a `validation_error`. Solves with `initial` converge to
  the solution branch nearest that start (the solver's documented nearest-
  solution behavior becomes a steerable feature).
- FR5. Every solve returns `diagnostics`: `{status: well_constrained |
  under_constrained | over_constrained, dof, rank, n_params, n_residuals,
  redundant: […], conflicting: […]}` — the conflicting/redundant sets
  computed by rank analysis of the Jacobian at the solution (QR pivoting /
  greedy removal), bounded by a documented time budget.
- FR6. Performance: warm-started re-solve of a 50-entity sketch in ≤ 16 ms
  p50 on a dev laptop (drag rate); cold solve ≤ 250 ms. A benchmark test
  encodes both thresholds.

**Sketch-on-face**
- FR7. The GUI opens a sketch plane from a planar face picked in the
  viewport (the same face-index ordinal `face_info` and the `mesh/faces`
  sidecar use); part edges intersecting the plane project as reference
  (construction) entities the solver treats as fixed.
- FR8. Emitted sketch-on-face code records the face reference in the script
  the way `push_pull` records `push_face(build(p), i, d)` — visible,
  editable, annotated with the face index it came from; the documented
  caveat that face indices can shift under topology-changing parameter
  edits applies and is surfaced, not hidden.

**Emission & docs**
- FR9. GUI emission maps solved entities to idiomatic build123d: `Line`,
  `CenterArc`/`RadiusArc`/`ThreePointArc`/`TangentArc`, `Ellipse`/
  `EllipticalCenterArc`, `Spline`, `SlotCenterToCenter`/`SlotOverall`
  inside `BuildLine`/`BuildSketch`. Emitted scripts rebuild to geometry
  matching the solved coordinates exactly (numbers inlined).
- FR10. Round-trip editing: a sketch emitted by the GUI reopens with its
  constraint spec intact (the spec persists as a structured block alongside
  the emitted code; the code remains the source of truth for geometry —
  divergence is detected and reported, not silently overwritten).
- FR11. The `part_template` cheat-sheet (`agentcad/core/templates.py`) and
  `docs/part-authoring.md` gain the new spec vocabulary and the
  entity → build123d mapping table.
- FR12. GUI diagnostics: the DOF chip and conflict highlighting; a
  mirror-prone drag sequence never flips solution branch when warm-started
  (regression-tested).

## Agent surface

- Changed: `solve_sketch {entities, constraints, initial?}` — `entities`
  gains `arcs`/`ellipses`/`splines`/`slots`; `constraints` gains `tangent`
  (generalized), `symmetric`, `equal_length`, `concentric`; `initial` is no
  longer reserved; the result gains per-entity solved coordinates and
  `diagnostics`. Non-convergence and over-constraint return
  `validation_error` with `details.diagnostics` (including `conflicting`).
- No new tools — deliberately: one solver, richer vocabulary, both layers.
- Routes: `routes_sketch.py` passes the new fields through (whitelisted
  keys per the route-pack contract).
- Events: none new — sketching is client-side until emission, which fires
  the normal `rebuild_started`/`rebuild_finished` flow.

## Technical approach

- **Solver** (`agentcad/toolkit/sketch.py`): extend the residual system —
  arcs parametrized as (cx, cy, r, θ1, θ2) with endpoint derivation;
  ellipses as (cx, cy, a, b, φ); splines as free control points with
  constraints on points and end tangents; slots compile to existing
  primitives + auto-constraints at spec ingestion (no new solver math for
  them). `scipy.optimize.least_squares` stays; rank and dependent-set
  analysis is a pure-numpy post-pass on the Jacobian the solver already
  evaluates. Server process only — no kernel/OCP involvement, honoring the
  only-kernel-imports-OCP rule.
- **Tool pack** (`agentcad/core/tools_sketch.py`): spec plumbing for the new
  entity/constraint kinds, `initial` handling, diagnostics passthrough. The
  `register(registry, service)` shape is unchanged.
- **Frontend** (`frontend/sketcher.js`): entity tools and constraint
  palette; the drag loop debounces to animation frames and calls the solve
  route with `initial` = current on-screen state; conflict highlighting;
  the face-pick → sketch-plane handshake reuses the viewport's existing
  face-picking (the `mesh/faces` sidecar drives it today).
- **Emission:** move code emission server-side into the sketch route so the
  GUI and agents share one emitter (today's JS-side emission becomes a call)
  — a design-spec decision with a fallback of keeping emission in JS and
  documenting the mapping for agents in the cheat-sheet.
- **Tests:** per-entity/constraint solver units; the FR6 benchmark; golden
  emission tests (emitted script rebuilds, metrics match solved
  coordinates); the v1-spec compatibility corpus; a mirror-flip drag
  regression.

## MVP & phasing

- **MVP:** arcs (center + 3-point) + splines + slots in solver and GUI;
  tangent/symmetric/equal/concentric; `initial` warm start; diagnostics
  payload with conflicting-set; DOF chip; emission for the new entities.
- **Phase 2:** ellipses and elliptical arcs (the conic tier);
  sketch-on-face with projected references; conflict highlighting UX;
  server-side shared emitter if chosen.
- **Phase 3:** round-trip spec persistence hardening; 100+ entity
  performance work (sparse Jacobian if the benchmark demands); groundwork
  notes for auto-constrain assistance (out of this PRD).

## Acceptance criteria

- AC1. A slotted cam profile with tangent arcs solves; the emitted
  `BuildLine` code rebuilds to geometry whose metrics match the solved
  coordinates (golden test) — the roadmap's done-when case.
- AC2. A scripted 100-step drag of a cam lobe re-solves warm-started with
  zero mirror flips (test via `initial`); the FR6 benchmark passes its
  thresholds on the bench sketch (perf test with documented limits).
- AC3. Adding a redundant constraint to a fully-constrained rectangle
  returns `over_constrained` with the conflicting set naming the added
  constraint (test).
- AC4. An under-constrained sketch reports `dof > 0` and
  `under_constrained` in the tool payload and the GUI chip (test + browser
  session).
- AC5. Sketch-on-face on the prototyping enclosure's top face references a
  projected edge, emits, and rebuilds green; the recorded face reference
  and its caveat are visible in the script (test + browser session).
- AC6. The v1 spec corpus returns identical solutions; full suite green
  (existing tests untouched).
- AC7. Browser session: draw an arc-slot profile, constrain it, drag it,
  finish — script diff visible in the editor, rebuild green, zero console
  errors.

## Risks & open questions

- **Conflicting-set quality:** rank analysis finds *a* dependent set, not
  necessarily the constraint the user considers the culprit. Mitigation:
  return the smallest set found, highlight all members, and iterate with
  real usage; never claim uniqueness.
- **Spline constraint semantics:** on-curve point constraints are expensive
  and ambiguous; MVP restricts constraints to control points and end
  tangents, documented plainly.
- **Warm start vs topology edits:** an `initial` that no longer matches the
  spec (entity added mid-drag) must degrade to cold start with a warning,
  never crash — tested.
- **Round-trip persistence shape** (structured comment in the script vs
  sidecar file): a comment keeps single-file portability but invites
  hand-edit divergence; decide in the design spec together with the
  emitter-location question. Divergence detection is required either way.
- **Solver scaling:** dense finite-difference Jacobians on 100+ entities
  may miss the drag budget; the benchmark decides whether sparse Jacobians
  or constraint freezing during drag are needed — measure before building.
- **Slot compilation visibility:** compiled sub-entities appear in
  diagnostics; naming must keep them addressable (`slot1.arc_a`) but
  grouped, or conflict reports will confuse.

## Competitive references

Every incumbent ships the full sketch vocabulary; Fusion adds AutoConstrain
on top (market_research.md, "The desktop incumbents"); FreeCAD 1.x sets the
OSS bar ("Open-source CAD: FreeCAD, code-CAD, and the Ondsel lesson"); the
Gap matrix verdict is **build** — this is a table stake, not a
differentiator. What we do differently: the sketcher is a thin GUI over a
solver that is *also* an agent tool, so completing the vocabulary upgrades
humans and agents in the same commit; diagnostics are structured data an
agent can act on (the conflicting set is a work item, not a red glyph); and
the output is reviewable build123d code, not an opaque sketch blob inside a
feature tree.
