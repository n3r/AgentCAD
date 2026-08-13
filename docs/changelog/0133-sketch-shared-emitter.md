# 0133 — PRD-009 slice 7: the shared server-side emitter (AC1)

- **Commit:** pending
- **Date:** 2026-08-12
- **Author:** Nikita Fedorov

## Summary

Emission moves off the front end. `agentcad/core/sketch_emit.py` is now the
**single** emitter for both layers: the browser posts `emit` to
`/api/sketch/solve`, an agent passes `emit` to the `solve_sketch` tool, a part
script can call `emit(solution, spec)` directly, and all three produce
**byte-identical** code — which is AC1's "one solver, both layers" thesis
applied to the second layer, and is asserted as bytes rather than described.
The emitter is pure Python (a fresh interpreter importing it has no `OCP` and
no `build123d` in `sys.modules`): emitting build123d source is not importing
build123d.

It ships the three rules the design measured, plus the gate that makes them
enforceable rather than aspirational.

## The measurement that shapes it

The old GUI (`sketcher.js`'s `fmtNum`) rounded to 6 decimals and wrote arcs
nowhere — a chain was always a `Polyline`. The moment arcs enter, a
centre-parametrized arc at 6 decimals leaves a gap between its *derived* end
and the next line's start, and `make_face()` refuses the wire. Reproduced here
on the slice-7 cam profile (two arcs of non-round radius joined by two tangent
lines), through the kernel:

```
6 decimals, centre-parametrized   worst junction gap 5.125e-07 mm  -> make_face() raises
                                  "Face can only be created with closed wires"
9 decimals, endpoint-anchored     worst junction gap 5.050e-10 mm  -> rebuilds
```

`tests/test_sketch_emit.py` runs both through the session kernel: the first is
asserted to *fail*, the second to rebuild to the solved metrics. **The bug does
not reproduce on round numbers** — a tidy slot closes at 3 decimals — so every
profile in that module is deliberately non-round. That is the whole reason this
failure reaches a user and not a reviewer.

## Changes

- **`agentcad/core/sketch_emit.py` — new.** `emit(solution, spec, *, style,
  decimals, arc_anchor, closure_tol, join_tol) -> {code, warnings, style}`.
  - **Chain discovery** ported from `sketcher.js`'s `findChains`, with **arcs
    and splines as chain members** (which the JS version could not express) and
    slot primitives folded in. Endpoints group into junctions by handle name or
    by proximity within `JOIN_TOL_MM` (1e-3), so a chain closed by a
    `coincident` constraint — which the solver leaves ~1e-11 mm open — is one
    junction, not two.
  - **Shared vertex literals.** Each junction is emitted **once** as a `v<n>`
    binding and referenced by both curves. A derived endpoint (`arc1.end`) is
    formatted from the **solved point**, never recomputed by the reader.
  - **9 decimals**, up from 6.
  - **Endpoint-anchored constructors**: `RadiusArc(start, end, ±r,
    short_sagitta=…)` in a chain, `ThreePointArc` when `arcs[n]["authored"] ==
    "three_point"`, `CenterArc` only for a sweep of a full turn (which a
    `RadiusArc` cannot express) and then with a warning. The
    `radius` sign / `short_sagitta` mapping was measured against build123d
    0.11.1 across ±30/90/170/190/270/350° sweeps: `short_sagitta = |sweep| <=
    180`, and the radius is negated when `(sweep > 0) == short_sagitta`.
  - **The closure gate.** Every junction's shared literal is parsed back
    exactly as the reader will parse it and compared against every solved
    endpoint it stands for (and, under `arc_anchor="center"`, against the
    endpoint the reader would derive from the rounded centre/radius/angles).
    Above `CLOSURE_TOL_MM` (1e-8) it **refuses to emit `make_face()`** with an
    `EmitError` naming the junction; on an open chain, where nothing will fail
    to close, the same measurement is a `junction_gap` warning. This is a
    superset of the design's "vertex-to-vertex gap": with shared literals that
    gap is zero by construction, so the honest question is how far the shared
    literal *moved* the geometry — and both failures the design names trip it.
  - **Splines** emit `tangents=` for a pinned end (slice 6 measured a free end
    drifting up to 44.6° from the control-polygon leg). build123d takes the two
    end tangents as a pair, so a spline with one pinned end has its free end
    emitted along its own leg and the caller gets a
    `spline_free_end_pinned` warning rather than a silent decision.
  - **Slots.** A slot that stands alone emits `SlotCenterToCenter(sep, 2r,
    rotation=…)` under `Locations`; a slot whose sub-entities carry constraints
    of their own — or whose primitives share a junction with anything else —
    emits as the primitives slice 6 compiled (`slots[n]["arcs"]/["sides"]`).
    `SlotCenterToCenter` is a BuildSketch **face**, not a curve that can join a
    `BuildLine` chain, and the emitter picks structurally rather than by hope.
  - Two styles: `"function"` (the `def sketch_profile():` block the GUI has
    always inserted, byte-compatible in shape) and `"buildline"` (the bare
    `with BuildSketch(...)` block).
  - `decimals` and `arc_anchor` are the two knobs the design measured. Their
    defaults are the safe values; the unsafe combination stays reachable **only**
    so the regression test can prove it fails instead of asserting it from
    memory.
- **`agentcad/core/tools_sketch.py`** — `emit` argument, schema entry and tool
  description; an `EmitError` becomes a `ValidationError` carrying the same
  `details` as the solver's own errors. Emission runs **after** the
  conflict/convergence gates: emitting code for a sketch that did not solve
  would be emitting the wrong geometry.
- **`agentcad/server/routes_sketch.py`** — `emit` whitelisted, with `false`
  mapped to "do not emit" so the GUI's natural payload does not become a type
  error.
- **`docs/agent-api.md`, `docs/part-authoring.md`** — the `emit` surface, the
  entity→build123d mapping and the closure gate.

## Files

- `agentcad/core/sketch_emit.py` — **new**
- `agentcad/core/tools_sketch.py`
- `agentcad/server/routes_sketch.py`
- `tests/test_sketch_emit.py` — **new**, 26 tests
- `docs/agent-api.md`, `docs/part-authoring.md`
- `docs/changelog/0133-sketch-shared-emitter.md` — this entry

## Verification

```
uv run pytest -q tests/test_sketch_emit.py                     -> 26 passed
  (printed) 6 decimals, centre-parametrized: 5.125e-07 mm  (make_face refuses)
  (printed) 9 decimals, endpoint-anchored:   5.050e-10 mm  (gate 1e-08)
```

AC1's golden test, through the session kernel — the slotted cam profile solved,
emitted, written into a scratch part and rebuilt (extruded 1 mm, so volume *is*
the profile's area), against the area computed from the solved coordinates by
Green's theorem and the bbox computed from the arcs' true extremes:

```
volume    1934.65198711856 mm^3   expected 1934.6519871803043   rel 3.2e-11
bbox min  (-18.369099999, -42.629850000)  expected (-18.3691, -42.62985)
bbox max  ( 43.654536018,  18.369100000)  expected ( 43.654536018, 18.3691)
```

(the acceptance threshold is `rel=1e-6`; the measured error is five orders
inside it.)

AC1's identity half is a byte comparison across the three call paths — the
route (`TestClient` POST), the tool handler, and `emit()` — plus a determinism
check that two calls on the same spec produce the same bytes.

`make test` was run in chunks together with slice 8 (this sandbox caps a
foreground command at 600 s); the union is **1646 passed, 1 skipped** against
the 1599/1 baseline, with the per-chunk table in
`docs/changelog/0134-sketch-drag-protocol.md`. This slice contributes the 26
tests of `tests/test_sketch_emit.py`.

## Notes

- **What the emitted cam looks like**, for anyone reviewing the shape of the
  output:

  ```python
  with BuildLine():
      v0 = (4.91070622, 17.700531044)
      v1 = (43.654536018, 6.951725512)
      v2 = (43.654536018, -6.951725512)
      v3 = (4.91070622, -17.700531044)
      Line(v0, v1)
      RadiusArc(v1, v2, 7.2143, short_sagitta=False)
      Line(v2, v3)
      RadiusArc(v3, v0, -18.3691, short_sagitta=False)
  make_face()
  ```

- **The gate is stricter than "will it close".** An endpoint-anchored chain
  closes at *every* precision (that was the second design measurement), so a
  6-decimal endpoint-anchored emission would rebuild — it would just be the
  wrong size (0.3% area error at 3 decimals, measured in the design spike).
  The gate refuses it anyway, because a shared literal that moves a vertex more
  than 1e-8 mm is emitting geometry the solver did not produce.
- **The slot side compilation is read from `toolkit.sketch`'s contract**, not
  re-derived: `side_1` runs `arc_a.end -> arc_b.start` and `side_2` runs
  `arc_b.end -> arc_a.start`. The solve payload names the four sub-entities but
  not their endpoints, and slice 7 does not touch the solver.
- **`n_faces`-style surprises avoided:** the emitter never returns partially
  emitted code. The gate runs before the return, so a caller either gets code
  that rebuilds or an error naming the junction.
- Slices 9–10 (the GUI) can now delete `buildSnippet`/`fmtNum`/`findChains`
  from `sketcher.js` and call the route with `emit: "function"`; the response
  shape is `{code, warnings, style}` and the warnings are already
  user-presentable strings.
