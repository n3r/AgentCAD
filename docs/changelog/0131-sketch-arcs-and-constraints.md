# 0131 — PRD-009 slice 5: arcs, virtual handles and generalized tangency

- **Commit:** pending
- **Date:** 2026-08-12
- **Author:** Nikita Fedorov

## Summary
The solver gains its first parametrized curve. An **arc** is a centre point
plus `r`, `theta1`, `theta2`, and its endpoints are **virtual handles** —
`arc1.start` and `arc1.end` are names that resolve through `PointRef` to
derived coordinates and a gradient chain-ruled over `{cx, cy, r, theta}`. They
add **no parameters and no residuals**, so `coincident {p: "arc1.end", q:
"p3"}` is the same two rows as any other coincidence and the entire v1
constraint vocabulary applies to arc endpoints for free (design Decision 3b).
The rejected alternative — free endpoint points tied back by residuals — costs
7 parameters per arc and puts machinery the user never wrote into every
conflict report.

On top of that: `tangent` as **one constraint with a dispatch table**,
`symmetric`, `equal_length` and `concentric`; and `radius`, `equal_radius`,
`point_on_circle`, `tangent_line_circle` and `tangent_circles` now accept an
arc wherever they accepted a circle, because an arc's radius is a radius.

## Changes
- **`_Arc` + `_ArcEndPoint(PointRef)`** in `agentcad/toolkit/sketch.py`. An arc
  costs exactly **3 parameters** (2 with `fixed_r`), plus the 2 its centre
  point costs when that point is new — asserted, because "never 7" is the
  whole point of the indirection. `_ArcEndPoint.accum` is the third
  implementation of `PointRef.accum` and the first derived one:
  `d/dcx = dfdx`, `d/dr = dfdx·cos t + dfdy·sin t`,
  `d/dt = r·(dfdy·cos t − dfdx·sin t)`.
- **Angles are degrees in the spec, radians in the parameter vector, and never
  wrapped mid-solve.** Output normalizes `start_deg` into `[0, 360)` and
  reports `end_deg = start_deg + sweep`, so the full signed sweep survives the
  round trip (`-10 → 20` comes back `350 → 380`). Wrapping a parameter is a
  Jacobian discontinuity and is how an arc jumps the long way round in a drag.
- **The dotted namespace is reserved.** `Sketch._claim` rejects any user
  entity name containing `.` and enforces one shared namespace across points,
  lines, circles and arcs (circles and arcs share `_rads`, so they always did).
  Virtual handles (`arc1.end`), an authored 3-point arc's compiled centre
  (`a1.center`) and slice 6's slot sub-entities all live behind that dot.
- **Both authoring forms.** The centre form, and the **3-point form**
  (`{name, start, mid, end}`) which compiles to the centre form at ingestion
  through a circumcircle, unwraps the sweep so it passes through `mid`
  (clockwise sweeps come back as a negative `end_deg`), and records
  `authored: "three_point"` so slice 7's emitter can write `ThreePointArc`.
  Three collinear points raise.
- **`tangent {a, b, at?, kind?}`** dispatches on `_kind_of` over the pair:
  line+circle/arc reuses v1's `tangent_line_circle` residual verbatim
  (including the 3-row `at` form), curve+curve reuses `tangent_circles`. **No
  new residual kinds**, and `tangent_line_circle`/`tangent_circles` stay
  registered under their own names for ever (FR3) — `tangent` is a new front
  door, not a rename.
- **`symmetric {a, b, about}` is two rows, not one**: the midpoint of `ab` on
  the axis **and** `ab ⟂ axis`. A line pair mirrors endpoint-for-endpoint in
  declaration order (4 rows). `equal_length {l1, l2}` is 1 row; `concentric
  {a, b}` is 2 and reuses the `coincident` residual on the two centre handles.
  Two new residual kinds only: `symmetric` and `equal_length`.
- **`initial` grows an `arcs` section** (`{r, start_deg, end_deg}`, degrees),
  with the same rules as before: it seeds, it never edits the spec, an unknown
  arc name raises, and a partial cover degrades to a cold start.
- **`parse_sketch` declares lines last**, because a line's endpoints may now be
  virtual handles and those exist only once their arc has been declared.
- **`core/tools_sketch.py`** forwards `entities["arcs"]` and documents the arc
  shape, the handles and the four new constraints in the tool description.
  `server/routes_sketch.py` needed no new key — entity *kinds* travel inside
  the already-whitelisted `entities` dict — and now says so in a comment, so
  the next person does not go looking.
- **Result payload gains `arcs`** (additive, per FR3): per arc `center, cx,
  cy, r, start_deg, end_deg, start{x,y}, end{x,y}, authored`.

## Measured — tangency is quadratically flat in an arc's angle
The line–arc–line fillet test asserts its junction against a hand calculation
to **1e-4 mm, not 1e-9**, and that is a property of the geometry rather than
slop. At a tangency `sin(theta) = -cy/r` pins nothing to first order near −1,
so `theta` carries ~4e-6 rad of genuine slack while both residuals sit at
1e-11. Measured on that sketch: the junction lands **1.7e-5 mm** along the
line from the ideal tangency point, and the arc departs the line by
`(1.7e-5)² / 2r` = **2e-11 mm**.

Two consequences, both recorded next to the assertion:
- **Slice 7 must anchor arcs on the shared solved endpoint** and never
  recompute an endpoint from centre + radius + angle. The design already says
  so for rounding reasons; this is an independent second reason.
- **Tangency costs iterations**, and it costs them only in this formulation.
  Writing the same fillet with the line's endpoint *being* the handle
  (`p2: "f.start"`) uses the perpendicular residual instead: `nfev 5`,
  `max_residual 0.0`, and the junction exactly on the ideal tangency point.
  0132 has the full measurement and the rule.

## Measured — FR6 with arcs
`uv run pytest -q tests/test_sketch_arcs.py -m slow -s`, M1 Max:

```
50-entity half-arc ring: n_params=123 n_residuals=123 nfev=4
warm-drag p50=6.09 ms (max 6.60 ms, budget 16.0 ms), max_residual=2.84e-14
```

(One transcribed run; five repeats of the same test ranged 6.09–7.03 ms p50 on
an otherwise busy machine, all well inside the budget.)

25 arcs alternating with 25 lines around a closed ring, every line running
between virtual handles and every junction a tangency — the worst-conditioned
realistic profile of its size. It clears FR6's 16 ms with margin, against the
all-line staircase's 0.8 ms; the difference is iteration count, not the
Jacobian (one pass, 320 µs), times scipy's `tr_solver="exact"`, which factors
the 123×123 system every iteration.

**These are the numbers after the tangency-form correction that landed with
slice 6** (see 0132). Slice 5 as first written used `dist(centre, line) − r`
at every junction and measured **11.52 ms, nfev 7, max_residual 3.60e-08** on
this same sketch; the perpendicular form for a junction that *is* an arc's
virtual handle took it to the numbers above. Measured alternatives on the same
sketch, for whoever owns the drag budget in slice 8: `tr_solver="lsmr"` was
5.26 ms against the old 11.52 ms baseline (same `nfev`, same solution to
1e-12), `x_scale="jac"` no change. Not adopted — changing the solve strategy
is slice 2's territory and the v1 corpus is its gate — but it is the lead to
pull if slice 8's budget gets tight.

## Files
- `agentcad/toolkit/sketch.py` — `_Arc`, `_ArcEndPoint`, `Sketch.arc`,
  `Sketch.arc_three_point`, `_circumcircle`, `_claim`, `_pref`, `_kind_of`,
  `tangent`, `symmetric`, `equal_length`, `concentric`, the `_tangent_*`
  splits, arc seeding, arc output, the module docstring
- `agentcad/core/tools_sketch.py` — `arcs` forwarded; `_ARCS` and
  `_NEW_CONSTRAINTS` in the tool description; `initial` doc
- `agentcad/server/routes_sketch.py` — comment recording why entity kinds are
  not route-level keys
- `tests/test_sketch_arcs.py` — **new**, 36 tests (1 `slow`)
- `tests/test_sketch_jacobian.py` — the derivative harness is now **shared**:
  `assert_df_matches_central_difference` / `assert_df_stays_inside_params` are
  public helpers, and the `RESIDUAL_KINDS` coverage gate unions the
  `DERIV_BUILDERS` mapping of every `tests/test_sketch_*.py` module, so a new
  kind must still be covered somewhere but need not be covered *here*
- `docs/agent-api.md`, `docs/part-authoring.md` — arcs, handles and the four
  new constraint types (the full prose sweep is the plan's slice 14)
- `docs/changelog/0131-sketch-arcs-and-constraints.md` — this entry

## Verification
```
uv run pytest -q tests/test_sketch_arcs.py                     -> 36 passed
uv run pytest -q tests/test_sketch_v1_corpus.py tests/test_sketch.py \
              tests/test_sketch_jacobian.py \
              tests/test_sketch_diagnostics.py \
              tests/test_sketch_initial.py                     -> 85 passed
```
The v1 corpus is untouched and green to 1e-9 — **FR3**. `make test` was run
once for slices 5 and 6 together, in chunks (this sandbox caps a foreground
command at 600 s); **0132** has the per-chunk table. The union:

```
make test (in 8 chunks)                    1599 passed, 1 skipped
```
Against the 1528/1 baseline that is +71 — the 70 fast tests the two slices add
(35 arcs + 16 splines + 19 slots) plus the one `slow` arc benchmark.

## Notes
- **`point_on_circle` on an arc constrains the arc's full circle**, not the
  bounded span. That is the honest reading of the residual and it is what the
  tool description says; a span-bounded version needs an inequality, which a
  least-squares system does not have.
- `concentric` reuses the `coincident` residual, so it adds no kind. Two
  curves that already share a centre point make it an identically-zero row,
  which the diagnostics correctly report as redundant.
- Arc angle parameters are always free even when `fixed_r` is set; there is no
  `fixed_angles`. An arc pinned in place is expressed with constraints, like
  everything else.
