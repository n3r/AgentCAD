# 0139 — PRD-009 slice 12: sketch-on-face, projected references, AC5

- **Commit:** pending
- **Date:** 2026-08-12
- **Author:** Nikita Fedorov

## Summary

Pick a planar face, sketch on it. The plane comes from build123d's
`Plane(face)` through **one new kernel handler pack**, the face's own boundary
edges come back in that plane's 2D coordinates as **fixed, construction-marked
reference entities**, and emission writes the basis and the face reference into
the script with the renumbering caveat inline. This is AC5, and it opened with
the plan's mandatory spike — because the whole design rests on `x_dir` being
deterministic, and PRD-008 is the precedent for what an unmeasured assumption
about OCCT face behaviour costs.

## The spike (design risk: `Plane(face).x_dir` stability)

Run through the **kernel worker**, not by importing build123d anywhere else
(`scratchpad/spike_sketchplane.py`, `spike_sketchplane2.py`), on the two parts
the plan named plus a third built to provoke the awkward case:

```
part                faces  planar  chosen face   boundary edges          x_dir
enclosure_base         87      59      37        4 LINE + 4 CIRCLE arc   (1, 0, -0)
nozzle                 10       2       6        2 CIRCLE                (1, 0, -0)
spline/ellipse part     6       4       4        BSPLINE + 2 LINE + ELLIPSE
```

- **`x_dir` is bit-identical** — max delta **0.0**, not "close" — across 3
  rebuilds in one worker, across a **fresh worker process**, and across the
  parameter changes `wall: 3.0` and `length: 110.0`, which resize that face
  without renumbering the part's faces. The basis is deterministic, so the
  design stands and AC5 could be built on it.
- **The instability that is real is the face *ordinal*, not the basis.**
  `corner_r: 6.0` turns the enclosure's face 37 from its 5989 mm² base plate
  into a **51 mm² sliver** — a topology change renumbering the mesh-order
  walk. That is the same instability push/pull already documents, and it is
  why the emitted block carries the caveat rather than pretending to be stable.
- **Non-line/non-circle boundary edges exist and are handled honestly.** The
  third part's planar face returns `BSPLINE` and `ELLIPSE` edges; they come
  back `kind: "other"` with a 25-point polyline and `constrainable: false`,
  are **not** offered as constraint targets, and the GUI toasts how many were
  skipped. A documented gap, not a silent one.
- The enclosure's face 0 returns **36 edges** (16 LINE + 20 CIRCLE) — a face
  with many holes — which is what a "reference geometry" list has to survive.

## Changes

- **`agentcad/kernel/handlers/sketchplane.py`** — the plan's one kernel file,
  exporting `sketch_plane`. Returns `{origin, x_dir, y_dir, normal, area_mm2,
  planar, refs, ref_kinds, n_faces}`. `face_info` reports a normal and a
  centre — a plane, but **no basis**, and without a deterministic in-plane X
  axis every emitted coordinate is arbitrary. Face indices are
  `toolkit.facemod.faces_in_mesh_order` ordinals, the single source of truth
  the sidecar, picking and push/pull already share. A non-planar face is a
  worker error naming the `geom_type`.
- **The face's own boundary, not a whole-part section** (design Decision 12):
  bounded, it is what a user means by "sketch on this face", and it cannot
  produce the degenerate near-tangential intersections a section can. An arc's
  sweep direction is read from the edge's own midpoint rather than assumed.
- **`sketch_plane` tool** in the existing tool pack, returning the plane, the
  raw `refs`, and `entities` — the same references **already in
  `solve_sketch`'s entity shape**, so a caller merges them instead of
  reimplementing the conversion. Every one of them is `fixed: true` (and
  `fixed_r`/`fixed` for radial curves) and `construction: true`.
- **Fixed reference arcs own no parameters at all.** An arc normally allocates
  its two angles whatever its radius does; a reference that could be re-swept
  is not a reference. `_Arc.fixed` pins radius *and* angles, and
  `_ArcEndPoint`/`_ArcTangent` take a constant angle. Asserted in the one
  number that proves it: a sketch of nothing but references reports
  `n_params == 0`.
- **`construction: true` on any entity** — it constrains, it never emits.
  `core/sketch_emit.py` drops construction members at `_members`, the single
  place that decides what the emitted code contains. This also closes slice
  9's deliberately deferred construction toggle, and for the reason that entry
  gave: a browser-only flag would have been a lie the emitter never sees.
- **Emission onto the plane (FR8).** `spec["plane"]` makes the emitter write
  `BuildSketch(Plane(origin=…, x_dir=…, z_dir=…))` instead of `Plane.XY`,
  under a header naming the face and stating the caveat:

  ```python
  # --- agentcad sketch on face 5 of build(p) ---
  # NOTE: face indices are mesh-order ordinals; a parameter change that
  # alters the part's topology can renumber them. Re-pick the face if
  # the rebuild moves. The plane's basis below is the one the sketch
  # was solved in.
  ```

  Sketch-on-face coordinates without their basis are arbitrary, so the basis
  goes in the script — visible, editable, annotated, the way `push_pull`
  records its face.
- **Route**: `plane` whitelisted explicitly (no `**body`, as ever).
- **GUI**: a **Sketch on face** button on the existing face card (enabled only
  for a planar face), `sketcher.openOnFace()`, references drawn **ghosted**
  (dashed, dimmed, through `strokeFor`), the plane carried on every solve
  through one `solveOpts()` helper so the three call sites cannot diverge, and
  `Insert → script` disabled when the sketch is nothing but references.

## Files

- `agentcad/kernel/handlers/sketchplane.py` — **new**, the handler pack
- `agentcad/core/tools_sketch.py` — `reference_entities`, the `sketch_plane`
  tool, `plane` on `solve_sketch`
- `agentcad/core/sketch_emit.py` — construction members dropped, `_plane_expr`,
  `_plane_header`, `_render(plane)`
- `agentcad/toolkit/sketch.py` — `_Arc.fixed`, constant-angle handles,
  `mark_construction`, the `construction` payload
- `agentcad/server/routes_sketch.py` — `plane`
- `frontend/js/sketcher.js`, `frontend/js/main.js` — the button and the flow
- `tests/test_sketch_on_face.py` — **new** (19 tests: the spike as tests, the
  zero-parameter proof, emission, the tool, the route)
- `docs/agent-api.md` — the `sketch_plane` row, the tool count 70 → 71
- `docs/changelog/0139-sketch-on-face.md` — this entry

## Verification

```
uv run pytest -q tests/test_sketch_on_face.py                   19 passed
uv run pytest -q tests/test_sketch_*.py tests/test_facemod.py   275 passed
```

**AC5** is `test_ac5_a_sketch_on_the_enclosures_top_face_rebuilds_green`:
sketch on the prototyping enclosure's top face, anchored to a projected edge
(`point_on_line` + 20 mm along it), solved `dof 0`, emitted, and the profile
extruded 2 mm through the **real kernel** — `volume + 12 × 8 × 2` to `rel=1e-6`
— with `face 37` and `Re-pick the face` in the script. The example is read and
rebuilt from a copy; `examples/` is never mutated.

**Real browser** (headless Chrome for Testing via Playwright, SwiftShader
WebGL, scratch server on port 52411 with a scratch projects dir; the user's
8630 was never touched and the server was stopped afterwards):

```
click the part in the viewport   face card: 'Face 5 · planar · 3600.0 mm² · n [0,0,1]'
Sketch on face                   enabled; toast 'Sketching on face 5 · 4 reference edge(s)'
sketcher opens                   4 dashed reference lines · 'fully constrained' · Insert disabled
draw a closed 25x25 profile      8 DOF
select new line + projected edge Par/Perp enabled
apply Par                        chip 'par ln1,ref3' · 4 DOF -> 3 DOF
                                 solved line (-20.271, -18.000000027) -> (8.271, -17.999999973)
Insert -> script                 '# --- agentcad sketch on face 5 of build(p) ---'
                                 '# NOTE: face indices are mesh-order ordinals; ...'
                                 'BuildSketch(Plane(origin=(30.0, 30.0, 10.0),
                                                   x_dir=(1.0, 0.0, 0.0), z_dir=(0.0, 0.0, 1.0)))'
Save & Rebuild                   error None · volume 37875.0 mm^3
                                 (60x60x10 box = 36000, plus a 25x25 pad x 3 mm = 1875)

CONSOLE ERRORS: NONE
```

Screenshots: `s12-a-refs.png`, `s12-b-profile.png`, `s12-c-inserted.png`,
`s12-d-rebuilt.png`, `s12-e-constrained-to-ref.png`.

`node --check` on both changed JS files.

**`make test`**, run in chunks (this sandbox caps a foreground command at
600 s; `test_parts_build_at_param_extremes[engine]` alone is ~890 s):

| chunk | result | time |
|---|---|---|
| `-m "not slow"` | 1381 passed, 1 skipped | 257.97 s |
| `-m slow` checks_pipeline/checks_ref/checks_cli/checks_api | 93 passed | 196.89 s |
| `-m slow` specs/specs_gate/specs_api/packet | 120 passed | 81.92 s |
| `-m slow` anchors_kernel/pool/sandbox/proposals*/prd00{1,2,3,4,8} | 69 passed | 130.44 s |
| `-m slow` mcp/kernel/geometry_ci/comments_proposals/sketch_diagnostics/sketch_bench/sketch_arcs/**sketch_on_face** | 24 passed | 23.31 s |
| `-m slow` examples `-k "not engine"` (-n 4) | 16 passed | 82.23 s |
| `-m slow` examples `-k "engine and not param_extremes"` | 3 passed | 283.89 s |
| `-m slow` examples `-k "param_extremes"` (serial; 4 of these 5 are already in the `not engine` chunk, so **1** is new) | 5 passed | 917.65 s |
| **total** | **1707 passed, 1 skipped** | |

The chunks overlap by exactly the four non-engine `param_extremes` cases, which
are counted once: `93 + 120 + 69 + 24 + 16 + 3 + 1 = 326`, and
`uv run pytest --collect-only -m slow` collects **326**. `-m "not slow"`
collects **1382** = 1381 passed + 1 skipped. Total collected **1708**.

Against the **1646 passed, 1 skipped** baseline: **+61**, every one accounted
for — 12 in `tests/test_sketch_tangent_direction.py` (0137), 30 in
`tests/test_sketch_ellipses.py` (0138) and 19 in `tests/test_sketch_on_face.py`
(this entry, of which 8 are `slow`). No test was deleted; one was rewritten
(0137's Notes).

## Notes

- **This adds a tool, and the design said "no new tools".** That sentence is
  about the *solver* surface (`solve_sketch` grows keys rather than sprouting
  siblings). Sketch-on-face needs a way to ask the kernel for a basis, and the
  same design forbids new routes — so a tool is the smaller divergence, and it
  is the surface the GUI (`callTool`) and an agent already share. Recorded
  here, and the count in `docs/agent-api.md` is updated.
- **Reference *ellipses* are not projected**, because a `kind: "other"` edge
  is not a constraint target and an ellipse-shaped boundary edge comes back
  `ELLIPSE`, which is `other`. A future slice could map it to the ellipse
  entity slice 11 just added; today it is a drawable polyline and says so.
- **`x_dir` is stable per face, and the face ordinal is not.** Those are two
  different claims and only the second is a caveat. Conflating them would
  have made the honest measurement ("bit-identical") sound like a warning.
- **The GUI's `Insert → script` guard now asks whether any *non-construction*
  curve exists**, not whether any curve exists. Otherwise a freshly opened
  sketch-on-face would offer to emit the face's own boundary back into the
  part.
