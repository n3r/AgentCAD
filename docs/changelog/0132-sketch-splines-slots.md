# 0132 — PRD-009 slice 6: splines and slots (and the spike that decided them)

- **Commit:** pending
- **Date:** 2026-08-12
- **Author:** Nikita Fedorov

## The spike, first — because the slice was not allowed to start without it

The solver models a spline as its **through points**; build123d's `Spline`
interpolates a point list with its own end conditions, and `Bezier` treats the
same list as *control* points. The plan required that measured against the
1e-8 mm emission tolerance (design Decision 10) before anything was built on
it. Five representative control polygons, exact point-to-curve distance
through OCCT extrema:

| polygon | n | `Spline` max deviation | `Bezier` max deviation |
|---|---:|---:|---:|
| gentle | 4 | **7.105e-15 mm** | 1.514 mm |
| tight | 4 | **3.553e-15 mm** | 5.202 mm |
| s_curve | 5 | **3.662e-15 mm** | 6.911 mm |
| near_collinear | 4 | **1.776e-15 mm** | 8.854e-04 mm |
| closed_ish | 5 | **3.662e-15 mm** | 9.805 mm |

**Verdict: `Spline`, as designed** — worst deviation 7.105e-15 mm against a
1e-8 mm tolerance, seven orders of margin. `Bezier` is off by whole
millimetres; the fallback would have been a semantics change and it is not
needed. The solver's through-point model **is** the emitted curve's geometry.

The spike also measured something the design did not anticipate, and it
changes what an end-tangent constraint has to emit. The end *tangent* of a
free-end `Spline` is nowhere near the first control-polygon leg:

| polygon | free-end tangent vs the leg | with `tangents=(leg0, legN)` |
|---|---:|---:|
| gentle | 8.04 deg | −0.000000 deg |
| s_curve | 25.91 deg | 0.000000 deg |
| tight | −41.09 deg | 0.000000 deg |
| closed_ish | **44.61 deg** | −0.000000 deg |
| near_collinear | 0.016 deg | −0.000000 deg |

With `tangents=` the worst end-direction error is **7.105e-15 deg** and the
curve **still interpolates every point to 7.324e-15 mm**. So: the solver's
end-tangent residual (a direction residual against the control-polygon leg,
per design Decision 3) is a promise only the *emitter* can keep, and it keeps
it by passing `tangents=`. The solve result now carries
`splines[name]["end_tangent"]` (which ends are pinned) and
`splines[name]["tangents"]` (the solved unit directions) so slice 7 can do
that without re-deriving anything. Both halves are pinned as tests in
`tests/test_sketch_splines.py`, including the `Bezier` branch, so a future
"simplification" into it fails loudly.

## The second measurement — tangency had the wrong residual form

Building slots surfaced a real bug in slice 5's tangency, and it is the kind
that does not announce itself. A slot's side line runs between the two caps'
virtual handles. With the `dist(centre, line) − r` residual, sliding a
junction along its cap moves **both** endpoints of the side *along* the line,
so the line does not rotate to first order and the row is second-order flat in
every angle it touches. The Jacobian is therefore **rank-deficient at the
solution**:

```
slot, centres fixed, distance form:   rank 1 of 5, dof 4,
                                      free_entities ['s1'],  max_residual 0.0
slot, centres fixed, perpendicular:   rank 5 of 5, dof 0,    max_residual 1.0e-15
```

`dof 4` on a fully-determined slot is exactly the diagnostic this PRD exists
to fix, arrived at from the other direction. The fix is a residual choice, not
a hack: when the line's endpoint **is** the arc's own handle, that point is
already on the arc and already on the line *structurally*, so the remaining
condition is that the radius meets the line square — v1's `tangent_point_perp`
row, which is first-order exact. `Sketch._shared_endpoint` detects the case;
the row count is 1 either way. Measured on slice 5's 50-entity half-arc ring:

```
                     warm-drag p50    nfev   max_residual
distance form           11.52 ms       7       3.60e-08
perpendicular form       6.09 ms       4       2.84e-14
```

Twice as fast and six orders more accurate (the perpendicular-form p50 ranged
6.09–7.03 ms over five repeats; the distance form never went below 11.1 ms). It also explains why that ring
reported `rank 123` (full) before: the singular values in the flat directions
were only small, not zero, because the solve had stopped at 3.6e-8 — a "full
rank" that was an artifact of loose convergence.

A related authoring rule falls out, measured on the same fillet both ways:
writing a chain **directly on the handles** (`line p1: "arc1.end"`) rather
than through a junction point tied by `coincident` gives the exact residual —
`nfev 5` and `max_residual 0.0` with the junction landing exactly on the ideal
tangency point, against `nfev 17`, `max_residual 2.3e-11` and a 1.72e-5 mm
offset for the coincident-tied form. Both are correct; one is exact. Recorded
in `tests/test_sketch_arcs.py` and in the module docstring.

## Changes
- **`_Spline`** — an ordered tuple of point names, degree 3, non-periodic,
  **no parameters and no residuals of its own**. `<name>.start` / `<name>.end`
  alias the first and last point, so `tangent {a: "sp1.start", b: "ln4"}`
  compiles to the existing `parallel` residual between the line and the first
  control-polygon leg — **no new residual kind**. `tangent` on the spline as a
  whole raises and names the two handles; on-curve constraints stay out of
  scope, said plainly rather than approximated.
- **`_Slot`** — compiled at ingestion into `<name>.arc_a`, `<name>.arc_b`,
  `<name>.side_1`, `<name>.side_2`. **One shared radius parameter** for both
  caps (equal-radius is structural, never a row) and **structural junctions**
  (each side is built on the caps' handles, so the four coincidences are not
  rows either). Five rows total: `radius = width/2` and four tangencies.
  A slot with fixed centres solves at `dof 0`; with free centres at `dof 4` —
  position 2, orientation 1, length 1 — which is the hand count.
- **Provenance without shifting the caller's indices.** Every compiled
  residual carries the slot's `con_index` and `origin: "slot:<name>"`
  (stamped in `Sketch._add` from a compilation-scoped `_origin`, so a row
  built by the same helper a user constraint uses still carries where it came
  from). The new `Sketch.con_report` holds the **caller-visible** index per
  constraint: the position in `spec["constraints"]` for anything the caller
  wrote, and **`None`** for a constraint compiled from an entity. So a slot,
  which is declared before every user constraint, cannot renumber them, and
  `diagnostics.conflicting` never points at an entry that does not exist.
  `core/tools_sketch.py` names the origin instead of `#None` in that case.
- **Reserved namespace.** Slice 5's "no dots in an entity name" rule already
  makes `slot1.arc_a` undeclarable; slots make it load-bearing. Sub-entities
  remain *referenceable* from a constraint — that is what makes
  `details.origin` naming `s1.arc_a` useful.
- **`initial` seeds a slot by its radius alone** (`slots: {name: {r}}`); the
  caps' angles are re-derived from the seeded centres (`_reseed_slots`), so a
  client never has to send a compiled sub-entity's parameters. Forgetting the
  slot degrades to a cold start with `initial_incomplete`, as everything else
  does.
- **Validation at ingestion**: a slot needs a positive width and two centres
  that are not already at the same coordinates.
- **Result payload gains `splines` and `slots`** (additive, per FR3). A
  slot reports its centres, width, solved shared radius and the names of its
  four compiled primitives, which also appear in `arcs`/`lines` — slice 7
  emits a slot inside a closed profile as **those primitives**, never as
  `SlotCenterToCenter` (a BuildSketch face at the origin, not a `BuildLine`
  curve).

## Files
- `agentcad/toolkit/sketch.py` — `_Spline`, `_Slot`, `Sketch.spline`,
  `Sketch.slot`, `_slot_arc_angles`, `_reseed_slots`, `_shared_endpoint`,
  `_tangent_perp`, `_tangent_spline_end`, `_parallel_refs`, `_radius_ref`,
  `con_report`, origin stamping, slot/spline seeding and output, docstring
- `agentcad/core/tools_sketch.py` — `splines`/`slots` forwarded;
  `_SPLINES_AND_SLOTS` in the tool description; the `index: null` message
- `tests/test_sketch_splines.py` — **new**, 16 tests (the spike, pinned)
- `tests/test_sketch_slots.py` — **new**, 19 tests
- `tests/test_sketch_arcs.py` — one added test for the handles-vs-coincident
  measurement
- `docs/agent-api.md`, `docs/part-authoring.md` — splines and slots
- `docs/changelog/0132-sketch-splines-slots.md` — this entry

## Verification
```
uv run pytest -q tests/test_sketch_splines.py                  -> 16 passed
uv run pytest -q tests/test_sketch_slots.py                    -> 19 passed
uv run pytest -q tests/test_sketch_arcs.py                     -> 36 passed
uv run pytest -q tests/test_sketch_v1_corpus.py tests/test_sketch.py \
        tests/test_sketch_jacobian.py tests/test_sketch_diagnostics.py \
        tests/test_sketch_initial.py                           -> 85 passed
make test-fast (uv run pytest -q -n 2 --dist loadscope -m "not slow")
                                    -> 1285 passed, 1 skipped in 293.48s
```
`make test` was run **in chunks** — this sandbox caps a foreground command at
600 s and the full suite needs ~30 min — `-n 2 --dist loadscope` throughout
(`-n 4` for the non-engine examples, serial for the param sweep):

```
-m "not slow"                                             1285 passed, 1 skipped  293.48s
-m slow  checks_pipeline/checks_ref/checks_cli/checks_api        93 passed        203.32s
-m slow  specs/specs_gate/specs_api/packet                      120 passed         88.89s
-m slow  anchors_kernel/pool/sandbox/proposals*/prd00{1,2,3,4,8}_acceptance
                                                                 69 passed        140.11s
-m slow  mcp/kernel/geometry_ci_action/comments_proposals/
         sketch_bench/sketch_diagnostics/sketch_arcs              12 passed         17.19s
-m slow  examples -k "not engine"  (-n 4)                        16 passed         85.87s
-m slow  examples engine (defaults, step export, interference)    3 passed        304.00s
-m slow  examples engine param extremes (serial)                  1 passed        881.38s
                                                          --------------------------------
                                                              1599 passed, 1 skipped
```
Against the 1528/1 baseline that is **+71**: the 70 fast tests these two slices
add (35 arcs + 16 splines + 19 slots) plus the one `slow` arc benchmark.
`test_parts_build_at_param_extremes[engine]` is the 63-instance sweep that
needs ~15 minutes of uninterrupted execution; it passed in **881.38 s**,
inside its own `pytest.mark.timeout(900)`, and nothing under `examples/`
imports `toolkit.sketch`.

## Notes
- **The slot-origin branch of a conflict report is unreachable from outside.**
  A slot compiles before every user constraint, and it introduces its own
  parameters, so its rows can never be dependent on anything declared earlier;
  a conflicting user constraint is always the later row and always the one
  named. That is the design's rule holding by construction rather than by
  care, and the tests pin the structure (`con_index`, `origin`,
  `con_report == [None]`) instead of a scenario that cannot be built.
- **The spline spike's build123d half runs in the test process**, not through
  the kernel worker: it imports build123d inside the test function, the way
  `tests/test_toolkit.py` already does. The *solver* remains OCP-free, and
  that assertion (fresh interpreter, `OCP` blocked) still passes with slots
  and splines in the spec.
- Slice 7 inherits three things from this slice: emit a pinned spline end with
  `tangents=`; emit a slot inside a closed profile as its compiled primitives;
  and anchor arcs on the shared solved endpoint (slice 5's measurement, and
  the perpendicular-form measurement here, both say so).
