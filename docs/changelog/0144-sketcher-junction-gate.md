# 0144 — PRD-009 review 3: the junction criterion is read at a solution, not at a projection

- **Commit:** pending
- **Date:** 2026-08-13
- **Author:** Nikita Fedorov

## Summary

A verification review of 0143 found that its junction criterion — which is
correct — was defeated by the numerical gate in front of it, and that the fix
had **regressed** solves that previously worked. 0143 manufactured the
configuration it reads the criterion at by running a budgeted `least_squares`
projection of the non-tangency rows and using `fit.x` without ever looking at
`fit.status`. This entry deletes the projection. The solver already produces a
configuration that solves those rows — the solution — so the tangency is
compiled in its provisional (distance) form, **solved**, and the criterion is
read there; a junction that is found recompiles the direction form and
re-solves from that configuration. Plus six smaller findings, three of them
factual corrections to 0142 and 0143.

## P1 — the projection failed silently, and the class reopened (H1)

`_junction_probe` ran `least_squares(..., max_nfev=JUNCTION_PROJECT_NFEV=500)`
on the non-tangency rows and took `fit.x` unconditionally. Those rows are
under-determined by construction, and `trf` on an under-determined system from
a distant seed does not converge inside a fixed budget. Measured on 0143's own
C4 configuration, seeded `off` mm from its junction:

```
off=0     nfev   1  ||f|| 6.1e-16  status 1   junction found
off=100   nfev  63  ||f|| 3.9e-11  status 3   junction found
off=300   nfev 500  ||f|| 76.2     status 0   phi_arc reads 76, not 0 -> MISSED
off=400   nfev 500  ||f|| 63.6     status 0   MISSED
off=1000  nfev 244  ||f|| 1.7e-10  status 3   junction found
off=5000  nfev 500  ||f|| 2564     status 0   MISSED
```

`status 0` is `max_nfev` exhausted. It was never read.

Condition (1) of the criterion then fails, the tangency compiles to the
degenerate distance form, and **nothing says so** — no warning, no
`analysis_complete: false`. Swept 0–1000 mm in 25 mm steps (probe `p7.py`):

| | flat form | direction form | solve failures |
|---|---|---|---|
| 0143, junction spelled dimensionally | **21** | 20 | **6** |
| 0143, the identical junction spelled `coincident` | 0 | 41 | 0 |
| pre-0143 (junction pass off) | 41 | 0 | 2 |
| **now**, spelled dimensionally | **2** | 39 | **2** |
| **now**, spelled `coincident` | 0 | 41 | 0 |

and non-monotonically: 275 flat, 575 direction, 600 direction, 650 flat. That
is numerical noise deciding which residual a sketch compiles to.

**The fix.** There is no projection. `_junction_probe` now returns a
configuration that genuinely solves the non-tangency rows, obtained one of two
ways:

1. **the seed itself**, when it already solves them to
   `JUNCTION_MANIFOLD_TOL` — which is the case on every drag frame, because
   the client warm-starts from the previous solution; or
2. **`_provisional_solution()`** — the sketch solved exactly as compiled, with
   every tangency still in its distance form, under `solve`'s own tolerance
   and evaluation budget (`SOLVE_TOL`, `SOLVE_MAX_NFEV`, now named constants
   so the two solves cannot drift apart). Computed at most once per `Sketch`.

If neither yields a configuration that solves those rows, the criterion has no
answer and the pass **says so**: the tangency keeps its provisional form and
the result carries

```json
{"code": "tangency_junction_undecided",
 "message": "the constraints other than the tangency (l1/a1) could not be
             solved, so whether the two curves already meet is unknown; the
             tangency keeps its distance form, which is rank-deficient at a
             junction",
 "entities": ["l1/a1"]}
```

in `warnings`. No silent fallback anywhere on the path — including the tool's
error path: `core/tools_sketch.py` raises a `ValidationError` instead of
returning the result when a sketch does not converge, and `warnings` now rides
in that error's `details`. It has to. When this warning fires, nothing was
swapped, so `solve` then runs the *identical* system from the *identical* seed
under the *identical* settings that `_provisional_solution` just failed on —
and reports `ok: false` for the same reason. Without `warnings` in `details` the
warning would never reach a caller at all. (The one configuration where the two
can come apart is a drag block, whose extra objective can pull the solve
somewhere the constraint rows do happen to hold; there the result is returned
normally and the warning rides in it.)

**The residual limit, stated honestly.** There is no seed-distance gate any
more, but there is still a limit: detection is attempted exactly when the
provisional system solves, and refused out loud when it does not. Measured on
`dimensional_junction_spec` swept -1000..1000 mm in 25 mm steps (only `p2`
moves, so the line grows with the offset): 59 seeds compile the direction form
and 22 the distance form — and those 22 are *exactly* the 22 seeds at which the
sketch does not solve at all, each carrying the warning. Not one seed both
solves and gets the flat form. `test_detection_reaches_exactly_as_far_as_the_
solve_does` asserts that identity, so a future change that widens the gap
fails. On the p7 configuration (both endpoints move) the same identity holds at
2 of 41 seeds, which is the pre-0143 solve-failure count.

## P2 — the direction residual's own stationary point (H2)

`t_L x t_C` is at an extremum, not a zero, when the two tangents are
**perpendicular**. Seed a horizontal line's junction at a circle's 3 o'clock
point: the cross product is 1, its gradient along the circle is zero, and
Gauss-Newton cannot move. 0143 compiled the direction form there and reported
`ok: false, max_residual 1, rank 3/4, conflicting: [tangent]` on a sketch the
pre-0143 code solved to 3.8e-11. In the C4 sweep it is the difference between
6 solve failures and 2: seeds 600, 625, 825 and 1000 were solving before 0143
and stopped after it.

The re-solve now starts from a configuration that is already a solution rather
than from the caller's seed (`Sketch._junction_x0`, consumed by `solve`). Two
cases, and the distinction is what keeps a drag frame free:

- the direction rows are already satisfied at `x*` (a warm drag frame) — start
  there, no extra solve;
- they are not — start from `_provisional_solution()`. The **distance** form
  has no stationary point at that configuration, so it walks the junction onto
  the tangency first and the direction form only has to sharpen it.

`_junction_x0` is set **only** when a row was actually swapped: a sketch this
pass leaves alone must solve exactly as it did.

Measured on the stationary-point configuration: `ok: false, max_residual 1` →
`ok: true, max_residual 3.6e-10`, junction at the top of the circle. The C4
sweep's solve failures go 6 → 2, which is the pre-0143 count.

## P3 — the projection ran on every drag frame (H3)

It was unconditional: every compile, i.e. every frame. Measured on a
50-segment staircase plus one dimensionally spelled junction, compile and
solve separated (probe `p12_cold.py`; p50 of 7, one machine, one sitting —
0143's own cost table used `solve_sketch` inside the "solve" loop, which
re-compiles, so it counted the compile twice):

```
cold, with a drag block          0143              now              pre-0143
seeded on the junction     9.8 + 7.2 = 17.0    8.6 + 8.3 = 16.9   0.3 + 7.0 =  7.3
seeded 3 mm off            9.5 +10.6 = 20.1    9.3 + 9.0 = 18.2   0.3 + 9.6 =  9.9
seeded 300 mm off        123.1 +425.5 = 548.6  316.5 + 8.1 = 324.6 0.4 +418.8 = 419.2
compiled form at 300 mm  tangent_line_circle    tangent_dir       tangent_line_circle
                          (wrong, silently)
```

0143's claim that the pass is cheap was measured only on sketches that never
reach it. The honest picture now: a **cold** solve of a sketch with a junction
this pass has to decide costs one extra solve — roughly 2x, well inside FR6's
250 ms cold budget at the bench size — and the 300 mm case is *faster* than it
was before 0143 (325 ms against 419 ms) because the direction form converges in
36 evaluations where the distance form needed 1265. The 300 mm cold case is
still the worst number on the page; it is a sketch drawn 300 mm from its own
junction, and it is now at least correct.

**A drag frame costs nothing at all**, because a drag frame arrives with
`initial` set to the previous solution, which already solves the rows the
criterion is about, so branch (1) above is taken and no extra solve happens:

```
staircase-50, warm frame (compile + solve), p50 over 60 frames
  no tangency                                     7.66 ms   (solve_ms 7.29)
  + dimensionally spelled junction                7.75 ms   (solve_ms 6.87)
  + the same junction spelled `coincident`        6.90 ms   (solve_ms 6.48)
  FR6 budget                                     16.00 ms
```

`test_the_junction_pass_costs_a_drag_frame_nothing` encodes it, asserting the
budget on the fastest frame with a 4x ceiling on the median — AC2's rule, for
AC2's reason.

## P4 — three configurations were outside the class tests (H4)

`CLASS_SPECS` was mutated at the bottom of
`tests/test_sketch_tangent_direction.py`, **after** the two
`@pytest.mark.parametrize("name", sorted(CLASS_SPECS))` decorators had been
evaluated at import. The three configurations 0143 added — the ones the whole
criterion was rewritten for — were never rank-tested: 6 ids collected for 9
specs. The sweeps now live at the bottom of the file, after every registration,
and `CLASS_SPEC_IDS` plus `test_every_class_spec_is_parametrized` fail loudly
if a tenth spec is ever registered below them.

Turning them on failed one immediately: `symmetric_junction_spec` returned
`ok: false`, rank 2 of 3, `conflicting: [symmetric]`.

**`symmetric` was review 2's finding C2 again.** Its second row was
`unit(q - p) . u` — the *sine* of the angle between the pair and the axis. At a
pair the constraint itself holds together, `q - p` is 6.1e-16 mm of
floating-point dust, `_unit` normalizes it into a full unit vector, and the row
reads 1.0 on geometry that is already right. The row is now `(q - p) . u` in
millimetres: identical zero set, smooth at `p == q`, and in the same unit as
the midpoint row above it. `test_symmetric_is_the_midpoint_offset_and_the_off_
perpendicular` is updated from 0.447 to 4.0 — the same configuration, measured
in millimetres of along-axis offset instead of in sines.

## P5 — the value gate hard-coded the millimetre (H5)

`max(1.0, curve_scale)` in `_held_on` meant the same drawing disagreed with
itself about its own junctions depending on the unit it was authored in.
`distance(p, c, r(1+d))` + `radius(C, r)` puts `p` a *relative* `d` off the
circle, so the verdict must be a function of `d` alone. Probe `p8_scale.py`,
before and after, `r = 10` units:

```
rel offset      x1e-6  x1e-3   x1    x1e3  x1e6         x1e-6  x1e-3  x1   x1e3 x1e6
   1e-09         dir    dir    dir    dir   dir          dir    dir   dir   dir  dir
   1e-08         dir    dir    dir    dir   dir          dir    dir   dir   dir  dir
   1e-07         dir    dir   FLAT   FLAT  FLAT          dir    dir   dir   dir  dir
   1e-06         dir    dir   FLAT   FLAT  FLAT         FLAT   FLAT  FLAT  FLAT FLAT
   1e-05         dir   FLAT   FLAT   FLAT  FLAT         FLAT   FLAT  FLAT  FLAT FLAT
   1e-03         dir   FLAT   FLAT   FLAT  FLAT         FLAT   FLAT  FLAT  FLAT FLAT
   1e-02        FLAT   FLAT   FLAT   FLAT  FLAT         FLAT   FLAT  FLAT  FLAT FLAT
                    before (0143)                              after
```

Twelve orders of magnitude, one answer, with the threshold at
`JUNCTION_TOL_REL = 1e-7` of the curve's own size. Two changes got it there:
`JUNCTION_TOL_MM` became `JUNCTION_TOL_REL` measured against
`_curve_scale` with no floor, and `JUNCTION_MANIFOLD_TOL` (the "is this a
solution" gate) is taken against `_configuration_scale` — the sketch's largest
coordinate or radius, coordinates and radii only, because the raw parameter
vector's angle slots are radians and would put a floor of ~pi under a drawing
authored in metres. The **gradient** floor `max(|g|, 1)` is unchanged and still
justified: those gradients are built from unit vectors and are dimensionless.

## P6 — a zero-length line was "held" everything on it (H6)

A line whose two fixed endpoints sit on the same spot has no direction, so
`cross(h - a, u_line)` is identically zero with an identically zero gradient.
The gradient floor read "carries no information" as "fully determined" and the
tangency compiled to the direction form, on a line that has no direction.
`_held_on` now refuses any curve whose `_curve_scale` is not positive.
Measured: `[point_on_circle, tangent_dir]` → `[point_on_circle,
tangent_line_circle]`, which is the correct form.

## Corrections to 0142 and 0143

Recorded here rather than by rewriting those entries, except where a line in
the code said the same wrong thing and has been fixed in place.

- **0143's instance-4 table does not reproduce (H7).** It printed `svals 10.05
  1.005 1.61e-16 → 1.142 1.000 0.834`, `dof 7 (true 6) → 6`. `dof 7` is
  impossible: the spec has `n_params 6`. Re-measured on the same spec, junction
  pass off and on:

  | | singular values | rank | dof | status | blame |
  |---|---|---|---|---|---|
  | distance form | 10.05, 1.414, **4.3e-17** | 2/3 | 4 | over_constrained | `redundant: [tangent]` |
  | direction form | 10.05, 1.005, **0.1404** | 3/3 | 3 | under_constrained | none |

  The test docstring carries the corrected table.

- **The elliptical fallback is not "already a pair of direction residuals"
  (H8).** 0142 and 0143 both say so; measured, neither supported pairing is.
  Against a line it compiles `point_on_line(P(t))` + `tangent_dir` — one length
  residual and one direction residual. Against a circle or arc it compiles
  `point_on_circle(P(t))` + `tangent_point_perp` — two *length* residuals and
  no direction row at all. Ellipse-to-ellipse is refused outright. What
  actually keeps the elliptical form out of this class is the **auxiliary
  anomaly**: the touch point is a free parameter rather than a point the rest
  of the sketch pins, so there is no junction for a distance form to go flat
  at. A junction *on* an ellipse remains a genuine coverage gap —
  `_on_curve_residual` has no closed form for one, so only the symbolic
  detector can see it. The comments in `_tangent_ellipse` and
  `resolve_tangencies` are corrected.

- **"The value half runs first … before any projection is computed" is
  backwards (H9).** The projection was computed unconditionally in
  `_junction_probe`, before `_probe_junction` evaluated a single value. The
  ordering claim was about two halves of `_held_on`, not about the projection.
  Moot now — there is no projection — and the `_held_on` comment no longer
  claims it.

- **"CONSOLE ERRORS: NONE" was unqualified (H10).** 0142 and 0143 both record
  it that way. Review 3 observed two 404s on
  `GET /api/projects/{proj}/parts/{id}/mesh/faces` on first load of the bundled
  `construction` example, which the browser logs as console errors. Verified
  here in code rather than by re-running the browser (this diff touches no
  frontend file): `app.py`'s `get_mesh_faces` raises `NotFoundError` by design
  when the triangle→face sidecar has not been written yet — a stale cache entry
  or a reference import — and `api.js`'s `getMeshFaces` expects and handles the
  404. Expected behaviour, but still console output: the claim should have read
  "no console errors **other than** the pre-existing `mesh/faces` 404s". The
  route is outside this diff and is not changed here.

## Behaviour changes a caller can see

- `warnings` may now contain `tangency_junction_undecided`. It is the one and
  only signal that a tangency kept its distance form for a reason other than
  "these curves do not meet".
- A `symmetric` constraint's second residual is now a length (mm) rather than a
  sine, so `max_residual` on a sketch containing one is on a different scale.
  The zero set, the rank and the solution are unchanged.
- A sketch with a junction this pass has to decide costs one extra solve on a
  **cold** solve. Warm (drag) frames are unaffected.

## Files

- `agentcad/toolkit/sketch.py` — `JUNCTION_TOL_REL` (was `JUNCTION_TOL_MM`),
  `JUNCTION_MANIFOLD_TOL`, `SOLVE_TOL`, `SOLVE_MAX_NFEV`;
  `JUNCTION_PROJECT_NFEV` removed. `resolve_tangencies` (the plan/start/swap
  restructure and its docstring), `_warn_undecided`, `_junction_probe`,
  `_provisional_solution`, `_configuration_scale`, `_held_on`, `solve`'s `x0`,
  `Sketch.__init__` (`_junction_x0`, `_provisional_x`), `_symmetric_points`
  and `symmetric`'s docstring, `_tangent_ellipse`'s scope comment.
- `tests/test_sketch_tangent_direction.py` — the two class sweeps moved below
  every `CLASS_SPECS` registration plus `CLASS_SPEC_IDS` /
  `test_every_class_spec_is_parametrized`; the seed sweep extended to ±575 mm;
  new `test_the_seed_never_changes_which_residual_a_junction_compiles_to`,
  `test_an_undecidable_junction_says_so_instead_of_falling_back_quietly`,
  `test_detection_reaches_exactly_as_far_as_the_solve_does`,
  `test_a_tangency_seeded_at_the_direction_residuals_own_stationary_point`,
  `test_the_junction_verdict_does_not_depend_on_the_unit_it_was_drawn_in`,
  `test_a_curve_with_no_size_holds_nothing_on_it`,
  `test_a_symmetric_pair_the_axis_runs_through_is_not_a_conflict`,
  `test_symmetric_is_smooth_where_the_pair_meets`,
  `test_the_undecided_warning_survives_the_tools_error_path`,
  `test_the_junction_pass_costs_a_drag_frame_nothing`; corrected numbers in
  `test_a_dimensionally_pinned_junction_is_recognised`.
- `agentcad/core/tools_sketch.py` — `warnings` added to the `ValidationError`
  details both failure gates raise.
- `tests/test_sketch_semantics.py` — `symmetric`'s second row measured in
  millimetres.

## Notes

- The criterion itself (0143's) is unchanged and is still the right one. Only
  the configuration it is read at changed, from a manufactured projection to a
  solution.
- **Not fixed:** the `mesh/faces` 404s (H10) are pre-existing behaviour of a
  route outside this diff; only the claim about them is corrected. A junction
  on an ellipse or a spline is still invisible to the Jacobian criterion (no
  closed-form on-curve function); the symbolic detector remains the only cover
  there, and that limit is now written down in `resolve_tangencies` instead of
  being papered over with a false claim about direction residuals.
- Verification: `uv run pytest -q tests/test_sketch*.py
  tests/test_prd009_acceptance.py` — 544 passed. `make test-fast` — 1658
  passed, 1 skipped. Full suite in two chunks (a single `make test` exceeds the
  600 s tool cap): chunk A `-n 4 --dist loadscope tests/
  --ignore=tests/test_examples.py` — **1969 passed, 1 skipped** (312 s); chunk
  B `-n 2 tests/test_examples.py` — **20 passed** (917 s). 1989 passed and 1
  skipped across the 1990 collected; the skip is the pre-existing
  `test_analysis.py:166` ("agentcad[fem] installed; the 501 fallback is
  unreachable"), the same one the 1952-test baseline reports.
  Collection went 1952 → 1990 (+38). Every new test
  was run against the pre-change `agentcad/toolkit/sketch.py` first and fails
  there: 12 red, including `[line_arc_symmetric]` of the newly-parametrized
  rank sweep.
- Review 3's own probes were re-run before and after and are quoted throughout
  (`p4` far-seed solutions and the stationary point, `p5` the projection trace,
  `p7` the 0–1000 mm sweep, `p8_scale` the unit table, `p11_cost`/`p12_cold`
  the cost split, `part2` the 0142/0143 claim harness — 39 pass / 1 fail, the
  same emitter case that fails on `0bde937`, unchanged by this diff).
- Driven for real over `POST /api/sketch/solve` on a scratch server
  (`--port 8731 --projects-dir <scratch>`, never the user's 8630): the C4
  junction seeded 300 mm out returns `ok: true`, `rank 3/3`, `dof 3`,
  `max_residual 2.19e-10`, `status under_constrained`, `redundant: []` — the
  configuration 0143 silently compiled the flat form for. The undecidable
  seed (590 mm) returns `did_not_converge` with the
  `tangency_junction_undecided` warning in the error details. **No browser
  run**: this diff touches no frontend file, and the Chrome extension was not
  connected in this session.
