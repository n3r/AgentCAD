# 0137 — PRD-009: tangency at a coincident junction is a direction residual

- **Commit:** pending
- **Date:** 2026-08-12
- **Author:** Nikita Fedorov

## Summary

The correctness fix slice 10 measured and handed on. A tangent chain junction
read **`over-constrained (1)`** in the GUI while the constraint was doing real
work: at exact tangency `dist(centre, line) − r` sits at a minimum of the
distance it measures, so its gradient collapses into the span of the
coincidence rows and the row is invisible to the rank count. `tangent` now
compiles to a **direction** residual — the two curves' unit tangents are
parallel — whenever the sketch already pins the junction with a `coincident`
constraint. Same row count, same geometry, a first-order Jacobian.

**This is the same class slice 6 already fixed once** (changelog 0132) for a
junction two curves share *structurally*; that case keeps its perpendicular
form, which is this same residual scaled by `r`. What was missing was the
junction idiom the GUI and most agents actually write.

## The measurement

Three configurations, each solved and then differentiated at the solution.
`BEFORE` is the same tree with the new junction detection disabled at runtime,
so the two rows differ in nothing else
(`scratchpad/run_before_after.py`, `spike_tangent_rank.py`, `spike_arcarc.py`).

```
                    dof  rank  nfev  max_res    status              singular values (rank tol)
gui chain  BEFORE    5    2/3    1   0.00e+00   over_constrained    1.208e+01 2.449e+00 1.838e-16  (8.46e-09)
gui chain  AFTER     4    3/3    1   6.12e-17   under_constrained   1.212e+01 1.732e+00 1.198e-01  (8.49e-09)

arc-arc    BEFORE    4    2/3    1   7.35e-16   over_constrained    1.170e+01 1.414e+00 4.700e-16  (7.02e-09)
arc-arc    AFTER     3    3/3    1   7.35e-16   under_constrained   1.171e+01 1.372e+00 1.000e+00  (7.03e-09)

fillet     BEFORE    0    9/9   17   2.33e-11   well_constrained    ... 4.730e-01 1.892e-06 6.572e-07  (5.86e-09)
fillet     AFTER     0    9/9    4   1.84e-16   well_constrained    ... 4.723e-01 1.491e-01 1.471e-01  (5.92e-09)
```

- **gui chain** is the `ArcT` tool's output: fixed `p1` → `p2`, an arc whose
  `.start` handle is `coincident` with `p2`, `tangent {ln1, a1}`. 7 parameters,
  3 rows. It reproduces slice 10's numbers (`2.04e+1 2.45e+0 1.85e-16`, dof 5)
  on slightly different coordinates. The smallest singular value moves
  **1.84e-16 → 1.20e-01**, fifteen orders of magnitude, and the DOF count
  becomes 4 — the hand count, and the number the same sketch reports from an
  off-tangent seed.
- **arc-arc** is two arcs meeting at a coincident junction. There the distance
  form is *exactly* dependent on the coincidence rows, not merely small: the
  combination `û_x·row_x + û_y·row_y` reproduces it column for column at a
  shared point. `4.70e-16 → 1.00e+00`.
- **fillet** is `tests/test_sketch_arcs.py`'s line→arc→line profile, which was
  already `dof 0` — by 1.9e-6 and 6.6e-7 against a 5.9e-9 tolerance, i.e. three
  orders of margin on a quantity that should have had fifteen. Those two
  singular values become **1.49e-1 / 1.47e-1**, the solve goes **17 → 4**
  evaluations, `max_residual` **2.3e-11 → 1.8e-16**, and the junction lands
  exactly on the hand calculation instead of 1.7e-5 mm away.

## Changes

- **`Residual` kind `tangent_dir`** — `f = t_a × t_b` over two unit tangent
  directions, with its analytic `df`. New reference family `TangentRef`
  alongside `PointRef`/`ScalarRef`:
  - `_LineTangent` — `unit(p1 → p2)`, differentiated through the same
    normalization `parallel` uses (`_accum_dir`).
  - `_ArcTangent` — `(−sin θ, cos θ)` at a virtual handle. It touches **only
    the angle slot**: normalizing `d/dθ (c + r e(θ))` drops the centre and the
    radius, which is precisely why the row stops living in the span of the
    coincidence rows. Asserted as a test.
- **Junction detection.** `Sketch` keeps a union-find over handle names fed by
  every `coincident` constraint (`note_coincidence`, `_root`, `_union`,
  `_same_point`). `_joined_handle(line, curve)` and `_joined_handles(c1, c2)`
  answer "do these two curves already meet at a point?", transitively.
- **Dispatch, in order:** an explicit `at` → the 3-row form (unchanged); a
  structural shared endpoint → `tangent_point_perp` (slice 6, unchanged); a
  coincident junction → `tangent_dir`; otherwise the v1 distance form
  (unchanged). **A tangency with no junction is byte-identical to v1** — the
  only curve with virtual handles is an arc, so no circle-only sketch can take
  the new path.
- **`parse_sketch` registers every coincidence before compiling anything**, so
  a `tangent` written *ahead* of the `coincident` that joins its curves still
  sees the junction. A spec is a set, not a program. The direct `Sketch` API
  stays order-sensitive (declare the coincidence first, or call
  `note_coincidence`), and its docstring says so.
- **`kind` at a coincident arc–arc junction is accepted and unused**, and the
  tool description says so: external vs internal describes *where* two circles
  touch, and the coincidence has already chosen that point. Silently honouring
  a parameter that cannot mean anything would be worse than saying it.

## Files

- `agentcad/toolkit/sketch.py` — `TangentRef`/`_LineTangent`/`_ArcTangent`,
  the coincidence union-find, `_joined_handle`, `_joined_handles`,
  `_tangent_dir`, the dispatch in `_tangent_line_curve` and `_tangent_curves`,
  the `parse_sketch` pre-scan, `RESIDUAL_KINDS`, module docstring
- `agentcad/core/tools_sketch.py` — the tangency paragraph of the tool
  description (the junction rule, and that `kind` is unused there)
- `tests/test_sketch_tangent_direction.py` — **new**: the derivative coverage
  for `tangent_dir` (both pairings), the rank/DOF regression on slice 10's
  exact configuration, the arc–arc case, declaration-order independence, and
  three "must not change" guards (no junction → v1 residual; structural
  junction → `tangent_point_perp`; the `at` form untouched)
- `tests/test_sketch_arcs.py` — one test rewritten (see Notes)
- `docs/changelog/0137-sketch-tangent-direction-residual.md` — this entry

## Verification

```
uv run pytest -q tests/test_sketch_tangent_direction.py tests/test_sketch_jacobian.py
  31 passed

uv run pytest -q tests/test_sketch_arcs.py tests/test_sketch_slots.py \
  tests/test_sketch_splines.py tests/test_sketch_v1_corpus.py \
  tests/test_sketch_jacobian.py tests/test_sketch_diagnostics.py \
  tests/test_sketch.py tests/test_sketch_emit.py tests/test_sketch_drag.py \
  tests/test_sketch_initial.py tests/test_sketch_tangent_direction.py
  211 passed
```

`make test-fast` (`-m "not slow"`) → **1381 passed, 1 skipped** (257.97 s), and
`make test` in chunks → **1707 passed, 1 skipped** against the 1646/1 baseline;
the per-chunk table and the accounting for every added test are in
`docs/changelog/0139-sketch-on-face.md`, which lands alongside this entry.

The v1 corpus is green unchanged (FR3), and
`test_every_registered_residual_kind_is_covered` passes with the new kind
because the new module exports `DERIV_BUILDERS` — the coverage gate travels
with the kind, as slice 2 designed it to.

## Notes

- **One pre-existing test was rewritten, and the rewrite is the result.**
  `test_a_chain_on_the_handles_is_exact_where_a_coincident_tied_one_is_not`
  asserted `direct["nfev"] < via_point["nfev"]` — it *measured the defect*: the
  coincident-tied idiom cost 17 evaluations against 5 and landed 1.7e-5 mm off.
  With the fix the two idioms agree (4 and 5, both exact), so the assertion
  became `5 < 4`. It is now
  `test_both_chain_idioms_are_exact_since_the_junction_fix`, asserting the
  agreement and recording both sets of numbers. The obsolete comment in
  `test_a_line_arc_line_fillet_solves_and_is_analytically_tangent` — which
  explained the 1e-4 tolerance as "a measured property of tangency" — was
  corrected for the same reason; its assertions are untouched.
- **The rank count was never lying.** It was reporting the truth about the
  linearization, and the linearization was the wrong one. That is why no change
  inside `sketcher.js` could have fixed it, and why the fix is a residual form
  rather than a tolerance. Raising `RANK_TOL_REL` to hide a 1.8e-16 singular
  value would have hidden real redundancy everywhere else.
- **The rule, for the next residual:** if two entities are already tied
  together by rows the sketch declares, the residual for what remains must be
  written in the quantity those rows do *not* pin. Distance-to-a-thing-you-are-
  already-on is flat; direction is not. This is now the third time it has come
  up (slot sides, slice 6; chain junctions, here; and it is the same reason
  slice 11's elliptical tangency needs its own parameter rather than a distance).
- Ellipses (slice 11) inherit this dispatch: an elliptical arc's handles enter
  the same union-find, and its tangency uses the same direction residual with
  an `_EllipseTangent` in place of `_ArcTangent`.
