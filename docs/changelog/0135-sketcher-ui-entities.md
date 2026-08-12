# 0135 — PRD-009 slice 9: GUI entities, the new constraint palette, and the deleted emitter

- **Commit:** pending
- **Date:** 2026-08-12
- **Author:** Nikita Fedorov

## Summary

The sketcher grows the vocabulary slices 5–6 gave the solver — arcs (three
authoring tools), splines and slots — plus the four new constraints
(`tangent`, `symmetric`, `equal_length`/`equal_radius`, `concentric`). At the
same time **`buildSnippet`, `fmtNum` and `findChains` are deleted from
`sketcher.js`**: "Insert → script" now posts `emit: "function"` and pastes the
code the server returns. That is what makes AC1 ("one emitter, both layers")
true in the product rather than only in `tests/test_sketch_emit.py` — there is
no longer a second emitter in the browser that could disagree.

## Changes

- **Toolbar.** `Arc` (centre → start → end), `Arc3` (start → end → a point on
  the arc, via the circumcircle), `ArcT` (continue the chain with an arc
  tangent to the last segment), `Spline` (click through points, Esc ends),
  `Slot` (two cap centres, then a width). All follow the existing
  `skButton`/`setTool`/`chainPrev`/`pending` state machine; no second
  interaction model.
- **Refs, not just points.** `refCoords(ref)` resolves `p3` *and* the solver's
  virtual handles `a1.start` / `a1.end`, so a line can be built on an arc's
  endpoint and the chain tools can hand off between curves. Arc endpoints are
  **derived, never stored** — the solver owns centre/radius/angles and the
  endpoints follow, which is the same rule the residual IR uses. Handles are
  drawn as small squares and are selectable (`coincident`, `distance`).
- **`CHAIN_TOOLS`.** Switching *between* `line`, `arc3` and `arcTan` keeps
  `chainPrev` alive. Found in the browser: the first cut cleared the chain in
  `setTool`, so the tangent-arc tool had nothing to be tangent to and every
  attempt toasted "draw a line or an arc first". This is the whole point of
  that tool, and only a real browser session catches it.
- **Rendering.** Arcs as an SVG `A` path from centre/r/θ₁/θ₂ (sweep-flag from
  the sign of the sweep, since a growing angle is a positive sweep inside the
  `scale(1,-1)` world group; a full turn is drawn as two halves because one
  `A` command cannot express it). Splines as a Catmull-Rom cubic path, marked
  **display only** — build123d's `Spline` owns the real end conditions. Slots
  as their compiled outline (two half-turn caps plus two tangent sides),
  computed client-side from `c1`, `c2` and `width/2`, which the solver's own
  contract makes exact.
- **Constraint palette.** `Tan` (line+curve or curve+curve — never two lines),
  `Sym` (two points + the axis line), `Eq` (dispatches to `equal_length` for
  two lines and `equal_radius` for two curves), `Conc`.
  `updateConstraintButtons` counts by kind (`point`/`handle`/`line`/`circle`/
  `arc`/`spline`/`slot`) and `constraintLabel` covers all four new types.
- **A generic delete cascade.** `constraintRefs(con)` reads every entity name a
  constraint points at (`p q at ln l1 l2 c c1 c2 a b about` — `kind` and `type`
  are not refs), and the cascade iterates **to a fixed point**: a deleted point
  takes the circles and arcs centred on it, the lines and slots built on it,
  and the splines it leaves under two points; a deleted arc takes the lines
  built on its handles; a deleted slot takes every constraint naming
  `slot1.arc_a`. Deleting an arc *handle* deletes the arc that owns it.
- **`api.solveSketch(entities, constraints, opts)`** — `opts` carries
  `initial`, `drag`, `diagnostics` and `emit`, spread into the same body. It
  deliberately goes through the shared `request()`, with a comment naming why
  (slice 10's connection-reuse requirement).
- **Emission deleted from the browser.** `insertSnippet` is now async: it asks
  the route for `emit: "function"` with `diagnostics: "full"`, pastes
  `res.emit.code`, and surfaces `res.emit.warnings[].message` verbatim in the
  toast. An emission that would not rebuild arrives as a `validation_error`
  naming the junction and is shown instead of pasted.
- **The canvas follows the theme.** The SVG palette moves to `--sk-*` custom
  properties defined in both `:root` and `:root[data-theme="light"]`;
  `sketcher.js` reads them once per render and invalidates on a
  `MutationObserver` over `<html data-theme>` (theme.js publishes no state key,
  and it is not a file this plan may edit). Before this the sketch canvas was a
  hard-coded dark ramp that was unreadable on the light theme.
- **`fmtVal`** replaces `fmtNum` for prompts and chips, documented **display
  only** — the emitter formats its own literals at 9 decimals behind a closure
  gate, and reusing a display formatter for code is exactly the bug slice 7
  measured.

## Files

- `frontend/js/sketcher.js` — rewritten around refs, the new entity kinds, the
  palette and server-side emission
- `frontend/js/api.js` — `solveSketch` takes `opts`
- `frontend/css/app.css` — `--sk-*` canvas palette in both themes
- `docs/changelog/0135-sketcher-ui-entities.md` — this entry

## Verification

Real browser (headless Chrome for Testing 1228 via Playwright, SwiftShader
WebGL so Three.js has a real context, against a scratch server on port 52328
with a scratch projects dir — the user's 8630 was never touched). Every flow
below is driven through the **real pointer handlers**, not by calling
functions:

```
line-arc-line chain   chips ['coin a1.start=p2', 'tan ln1,a1']   over-constrained (1)
arc3 + centre arc     8 DOF
spline (4 points)     6 DOF
slot (width 14)       2 DOF
equal_length          Eq enabled, chip 'eq len ln1,ln2'
concentric            Conc enabled, chip 'conc c1,c2'            9 DOF
tangent line+circle   Tan enabled, chip 'tan ln1,c1'             4 DOF
symmetric             Sym enabled, chip 'sym p4,p5 ⟂ ln1'        6 DOF
light theme           grid #d7dae0 / curves #3a4048; toggling live -> #232529 / #c9ced6

CONSOLE ERRORS: NONE
```

Screenshots: `s9-a-chain`, `s9-b-arcs`, `s9-c-spline`, `s9-d-slot`,
`s9-e-constraints`, `s9-f-tangent`, `s9-g-symmetric`, `s9-light-theme`,
`s9-dark-theme`.

The emission round trip is verified end-to-end in slice 10's entry (a profile
drawn in the GUI, inserted through the button, saved, and rebuilt green).

`make test-fast` → **1328 passed, 1 skipped** (259.20 s), identical to the
slice-8 baseline. **No Python changed in this slice**, so the `slow` chunks are
untouched and the full-suite total stands at the **1646 passed, 1 skipped**
recorded with its per-chunk table in
`docs/changelog/0134-sketch-drag-protocol.md`; a spot-check of
`-m slow tests/test_sketch_bench.py tests/test_sketch_arcs.py
tests/test_prd001_acceptance.py tests/test_prd002_acceptance.py` → 18 passed.

## Notes

- **A tangent chain junction reports `over-constrained (1)`, and that is the
  rank analysis telling the truth about the linearization.** Measured, on the
  line→tangent-arc chain the tool builds:

  ```
                          dof   singular values of J        rank tol
  constructed tangent      5    2.04e+1  2.45e+0  1.85e-16  1.43e-8
  seeded off-tangent       4    2.03e+1  2.45e+0  1.07e-06  1.42e-8
  ```

  At **exact** tangency the tangency residual `dist(centre, line) − r` is at a
  minimum, so its gradient collapses into the span of the coincidence rows and
  the third singular value is numerically zero — the constraint is invisible to
  a rank count. It is still doing real work: from an off-tangent seed the same
  constraint moves the geometry to tangency with an error of **2.5e-12** and a
  DOF count of 4. So the report is honest about the Jacobian and misleading
  about the model, and no change inside `sketcher.js` can fix it.
  **The fix belongs in `agentcad/toolkit/sketch.py`**: when the two curves
  already share a coincident endpoint, `tangent` should compile to a
  **direction** residual (the arc's tangent at that endpoint is parallel to the
  line), whose gradient does not vanish there. Recorded here so slices 11–14
  inherit the measurement rather than the symptom.
- **The construction/reference toggle is deferred, deliberately.** A
  construction entity must constrain but not emit, and emission is now
  server-side — so a browser-only flag would be a lie the emitter never sees.
  It needs `construction: true` on the entity in `toolkit/sketch.py` and a skip
  in `core/sketch_emit.py`, both outside this slice's permitted file list.
- **3-point arcs are authored as centre-form.** The GUI computes the
  circumcircle and declares `{center, r, start_deg, end_deg}` with a real
  centre *point* entity, rather than sending the solver's
  `{start, mid, end}` form. The reason is uniformity: a centre the user can
  see, select, constrain and drag, and one rendering path. The cost is that
  `arcs[n]["authored"]` is never `"three_point"`, so the emitter writes
  `RadiusArc` rather than `ThreePointArc` — which is the *preferred*,
  endpoint-anchored constructor anyway (changelog 0133).
- **Spline end tangency is not on the palette.** The solver takes it as
  `tangent {a: "sp1.start", b: "ln4"}`, which needs spline-end handles as
  selectable entities; on-curve point constraints are already a documented
  non-goal, and shipping half of the spline tangency surface would be worse
  than shipping none. The control points are ordinary points, so every point
  constraint works on them today.
- **A circle's radius is not draggable** — `drag` takes a point name or a
  virtual handle, and a circle exposes neither for its radius. An arc's
  `.start`/`.end` handles are draggable, and a circle's radius stays a `Rad`
  constraint.
- Two hit targets can overlap exactly at a tangency point, and the topmost wins
  (the circle, drawn after the lines). Not a bug, but it cost a debugging round
  in the browser session and is worth knowing before writing another one.
