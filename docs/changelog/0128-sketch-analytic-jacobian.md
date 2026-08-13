# 0128 — PRD-009 slice 2: the typed residual IR and analytic Jacobians

- **Commit:** pending
- **Date:** 2026-08-12
- **Author:** Nikita Fedorov

## Summary
`agentcad/toolkit/sketch.py` is rebuilt around a **typed residual IR**: every
constraint now compiles to `Residual` records carrying the spec index that
produced them, the parameter slots they touch, their value function and their
**analytic derivative**. scipy no longer finite-differences the Jacobian — it
is handed one — and `dof` is now `n_params - rank(J)` instead of a row count.
Zero behaviour change on the v1 corpus: all 22 cases return the same
coordinates to `abs=1e-9` (FR3).

## The measurement (same corpus, same machine, both from `test_sketch_bench.py`)

| n_seg | params | cold p50 before | cold p50 after | warm-drag p50 before | warm-drag p50 after |
|---:|---:|---:|---:|---:|---:|
| 10 | 20 | 2.59 ms | **0.45 ms** | 2.08 ms | **0.47 ms** |
| 25 | 50 | 13.44 ms | **1.17 ms** | 10.76 ms | **1.18 ms** |
| **50** | **100** | **50.48 ms** | **3.21 ms** | **50.51 ms** | **3.16 ms** |

**16x at the FR6 size** (a second run of the same table measured 3.02 / 2.90 ms
there, so read it as 16-17x), and FR6's two inequalities now pass with room:
~3.2 ms against the 16 ms warm budget (5x headroom) and ~3.2 ms against the
250 ms cold budget. `nfev` drops from 5 to 3.

Where the time goes now, at `n_seg = 50` (`scratchpad/profile_v2.py`):

| component | cost |
|---|---:|
| one residual evaluation | 54.8 µs |
| one **analytic** Jacobian | **90.5 µs** (the 2-point FD equivalent was 101 evals ≈ **9410 µs** — 104x) |
| `least_squares` (trf, 3 iterations) | 2.23 ms |
| SVD for rank/dof | 0.30 ms |

**The plan's rollback note asks for ≥ 20x and this is 16-17x — the gap is
measured, and it is not the Jacobian.** The Jacobian itself improved 104x; the
remaining cost is scipy's trust-region machinery (`trf` factorizes J with an
SVD per iteration, ~0.9 ms of the 2.23 ms) plus the new rank SVD. The design's
0.78 ms figure came from `spike_analytic.py`'s bespoke Levenberg–Marquardt
loop over `numpy.linalg`, which measures **0.96 ms** here against the same
compiled residuals and converges to the same solution (max coordinate
difference 7.5e-12). Alternatives measured before choosing:
`tr_solver="lsmr"` is 8.82 ms (4x worse), MINPACK `lm` is 1.80 ms but requires
`m >= n`, which an under-constrained sketch violates. **`least_squares` with
`method="trf"` is kept**, per the plan's own task list: it clears every budget
this PRD names with 5.5x to spare, and swapping a battle-tested trust-region
solver for a 30-line loop to chase a headline multiplier is not a trade this
slice should make silently. If slice 8's drag path ever needs the last
millisecond, the numpy LM is a drop-in against the same `fun`/`jac` pair.

## Changes
- **`Residual` (frozen, slotted dataclass)** — `con_index`, `kind`, `rows`,
  `params`, `f`, `df`, `origin`. **It refuses to be constructed without a
  `df`**: a missing derivative does not crash, it silently reinstates the
  O(n²) finite-difference cost, so the check is at construction, not at solve.
- **`PointRef` / `ScalarRef`** — a handle resolves to `(value, gradient
  accumulator, parameter slots)`, with free and fixed implementations. Every
  v1 constraint is rewritten against the indirection rather than against point
  names; a fixed point contributes an **empty** `params` and writes no
  columns. This is the seam slice 5's virtual arc handles (`arc1.end`) plug
  into — they add a third implementation whose `accum` chain-rules through
  `{cx, cy, r, theta}` and need no new constraint types.
- **Analytic `df` for all 18 residual kinds** (the 17 constraint types, with
  the `at` form of `tangent_line_circle` contributing its own
  `tangent_point_perp` row). Direction-based residuals (`parallel`,
  `perpendicular`, `angle`, `point_on_line`, both tangency forms) share one
  `_accum_dir` helper that applies the normalization Jacobian
  `(I - u·uᵀ)/|b-a|` and chains onto the endpoints — derived once, tested
  eighteen times.
- **Parameter slots are assigned when an entity is declared**, not at solve
  time, so a residual knows its columns at compile time. `solve_sketch`
  declares all points before all circles, so the packing is identical to v1's.
- **Dense preallocated Jacobian.** `make_functions()` returns `(fun, jac)`;
  `jac` zeroes and refills one array by index assignment (`+=`, so a residual
  naming the same point twice is still correct). `fun` returns a **fresh**
  array each call — `least_squares` holds the previous residual vector across
  an iteration and would otherwise compare it against itself.
- **`method="trf"` uniformly**, with `jac=` supplied. MINPACK's `lm` cannot
  run an under-constrained sketch (`m >= n`).
- **`dof` is now `n_params - rank(J)`** with the SVD tolerance
  `max(m, n)·σ₀·1e-10`, and a new additive `rank` key. This is a **bug fix
  with a visible value change**: the shipped row count reported
  `dof: -2` for the two-circle tangent-line sketch (its `at` form contributes
  two identically-zero rows), and a *negative* dof for every redundant
  constraint. It is now 0 there. `n_params - n_residuals` remains derivable
  from two fields that are still present. (Pulled forward from the plan's
  slice 3, which adds the rest of the diagnostics block on top of this rank.)
- **A degenerate sketch no longer reaches scipy**: zero free parameters or
  zero residuals short-circuits to the initial values instead of letting
  `least_squares` raise on an empty problem.
- `RESIDUAL_KINDS` is exported so the derivative test can fail when a kind is
  added without one.

## Files
- `agentcad/toolkit/sketch.py` — rewritten in place; `solve_sketch(spec)`,
  `Sketch`, `SketchError` and every v1 result key keep their shape
- `tests/test_sketch_jacobian.py` — **new**, 19 tests
- `tests/test_sketch_bench.py` — FR6 assertions turned on at `n_seg = 50`
- `docs/changelog/0128-sketch-analytic-jacobian.md` — this entry

## The derivative test (`tests/test_sketch_jacobian.py`)
The highest-value test in the plan, because a wrong analytic derivative does
not crash — it converges slowly, to the wrong branch, or not at all.

- Four base sketches (lines/points, circles/tangency, the 3-row `at` form,
  and one with fixed entities) cover **all 18 registered kinds**; a fifth
  test asserts the union of kinds they build equals `RESIDUAL_KINDS`, so a
  future residual without a case fails loudly.
- For every residual, at **5 randomized parameter vectors** (seeded, ±1.5 mm
  around a deliberately non-degenerate base), each of `df`'s columns is
  compared against a central difference of *that residual's own* `f` at
  `rel=1e-6, abs=1e-8` — **including the columns it did not declare**, which
  is how an under-declared `params` tuple is caught rather than assumed.
- A separate test asserts `df` writes exactly zero outside `params`, one
  asserts the whole assembled Jacobian matches a finite difference of the
  whole residual vector (which catches a wrong row offset — invisible to a
  per-residual check), one pins the Jacobian buffer as reused and the residual
  buffer as not, one asserts a `Residual` without `df` raises at construction,
  and one asserts the compiled sub-rows of a `tangent_line_circle(at=…)` all
  carry the **slot's own** `con_index` so a future diagnostic cannot blame a
  row the caller never wrote.
- The fresh-interpreter probe (PRD-003/004 pattern) imports the module with
  `OCP`/`build123d` blocked at `sys.meta_path`, solves a sketch, and asserts
  neither landed in `sys.modules` — the solver runs in the server process.

## Verification
```
uv run pytest -q tests/test_sketch_v1_corpus.py tests/test_sketch.py \
  tests/test_sketch_jacobian.py            -> 47 passed in 2.91s
uv run pytest -q tests/test_sketch_bench.py -s -> 6 passed in 0.42s (table above)
make test-fast (uv run pytest -q -n 2 --dist loadscope -m "not slow")
                                           -> 1178 passed, 1 skipped in 251.92s
```
`make test` was run **in chunks**, because this session's sandbox caps a
foreground command at 600 s and freezes background ones between calls, while
the full suite needs ~24 min. All 1491 collected tests, `-n 2 --dist loadscope`
throughout (`-n 4` for the examples group):

```
-m "not slow"                                       1178 passed, 1 skipped  251.92s
-m slow  checks_pipeline/checks_ref/checks_cli/checks_api   93 passed      198.40s
-m slow  specs/specs_gate/specs_api/packet                 120 passed       80.98s
-m slow  anchors_kernel/pool/sandbox/proposals*/prd*_acceptance
                                                            69 passed      144.60s
-m slow  mcp/kernel/geometry_ci_action/comments_proposals     4 passed       16.93s
-m slow  examples -k "not engine"                           16 passed       97.65s
-m slow  examples engine (defaults, step export, interference) 3 passed    419.26s
-m slow  sketch_bench                                        6 passed        0.42s
                                                     ------------------------------
                                                          1489 passed, 1 skipped
```
**One test did not complete in this sandbox:**
`test_examples.py::test_parts_build_at_param_extremes[engine]` — the bundled
engine's 63-instance extremes sweep, which carries its own
`pytest.mark.timeout(900)`. It needs ~15 min of *uninterrupted* execution and
this sandbox cannot give it that; it failed on `Failed: Timeout (>900.0s) from
pytest-timeout`, not on an assertion, and it does not touch the sketch solver
(nothing under `examples/` imports `toolkit.sketch`). **A full `make test` on
a normal machine is expected to report 1490 passed, 1 skipped** — the 1441/1
baseline plus the 49 tests these two slices add (24 corpus + 19 jacobian +
6 bench), and 1491 collected confirms the arithmetic.

## Notes
- **Public API compatibility.** `solve_sketch(spec) -> dict`, the `Sketch`
  object API (`point`/`line`/`circle` + the 17 constraint methods + `solve`)
  and `SketchError` are unchanged. Result keys are a superset: `rank` is new,
  `dof` changes value as described. Private internals did change shape —
  `Sketch.residuals` is now a list of `Residual` records rather than of
  anonymous closures, `Sketch._add` takes a record, and `_Point.ix` is
  assigned at declaration. Nothing outside this module touched them.
- **`agentcad/core/templates.py`'s cheat-sheet needs no edit**: it says
  "dof>0 means UNDER-constrained", which the rank-based number makes reliably
  true rather than approximately true. The prose sweep is the plan's slice 14.
- `tests/test_prd008_acceptance.py::test_ac9_the_full_suite_count_is_cited`
  was **failing before this work** — it requires the newest changelog entry to
  cite a `make test` count, and `0126-prd-008-completed.md` cites its suite
  count without the words "make test". Entries 0127 and 0128 both cite one, so
  it passes again; the underlying gap in 0126 is left as-is (entries are
  historical records).
