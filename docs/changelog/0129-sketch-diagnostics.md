# 0129 — PRD-009 slice 3: DOF, free entities, and a named conflicting set

- **Commit:** pending
- **Date:** 2026-08-12
- **Author:** Nikita Fedorov

## Summary
`solve_sketch` now returns a `diagnostics` block on every solve: rank-based
`dof`, the entities an under-constrained sketch can still move, and — for an
over-constrained one — **the specific constraint that was added**, split into
`redundant` (satisfied) and `conflicting` (violated). `over_constrained` is
*not* an error; only a non-empty `conflicting` set raises, and it raises with
the whole block in `details.diagnostics`. **This is AC3**, and the algorithm it
ships is not the textbook one, for a measured reason.

## The measurement that chose the algorithm (design Decision 6)

The textbook method for "which constraints are dependent" is **column-pivoted
QR of `Jᵀ`**: the trailing pivots are the dependent rows. Reproduced in this
repo, on the design's four-case rectangle (six constraints, exactly
constrained, plus one added as constraint **#6**):

| case | pivoted QR blames | declaration-order greedy blames | correct |
|---|---|---|---|
| + redundant `parallel(ab, cd)` | #6 `parallel` | #6 `parallel` — redundant | greedy ✅ QR ✅ |
| + duplicate `distance(d,c)=50` | **#3 `vertical`** | #6 `distance` — redundant | greedy ✅ **QR ❌** |
| + contradictory `distance(d,c)=60` | **#3 `vertical`** | #6 `distance` — **conflicting** | greedy ✅ **QR ❌** |
| + duplicate `horizontal(cd)` | #6 `horizontal` | #6 `horizontal` — redundant | greedy ✅ QR ✅ |

QR blames `vertical(da)` — an **original, innocent** constraint the user drew
first — in two of three cases, because column pivoting selects by column norm,
an artifact of residual scaling rather than of intent. AC3 ("the conflicting
set naming the added constraint") is *unsatisfiable* with the obvious
implementation. Greedy forward selection **in declaration order** was 4 for 4:
a row that does not raise the rank of the rows declared before it is dependent
on them, so the blame lands on the later constraint — the one just added.
`tests/test_sketch_diagnostics.py::test_pivoted_qr_blames_an_innocent_constraint_where_greedy_does_not`
runs both methods side by side and fails loudly if anyone "simplifies" back
to QR.

Cost of the greedy pass, measured here (p50 of 9, printed by
`test_the_greedy_analysis_cost_is_measured_and_off_the_drag_path`):

| residual rows | greedy pass | `analyze()` on a well-constrained sketch |
|---:|---:|---:|
| 50 | **0.30 ms** | 0.08 ms |
| 100 | **0.72 ms** | 0.30 ms |
| 200 | **3.50 ms** | 2.15 ms |

The design measured 1.82 / 6.35 / 25.78 ms for the same pass. The gap is the
orthogonalization: this implementation runs **classical Gram–Schmidt twice**
against a stacked basis (two matrix-vector products per row) instead of a
Python loop over basis vectors — numerically equivalent to the modified
Gram–Schmidt the design specified, 5–7× cheaper. The selection decisions are
identical, which the four-case table is the check on.

**Diagnostics stay off the drag path by construction, before slice 8's cache
exists**: `rank == n_residuals` means no row can be dependent, so a
well-constrained sketch never runs the greedy pass at all. The FR6 benchmark is
unchanged by this slice — 3.21 ms cold / 2.92 ms warm-drag at `n_seg = 50`,
against 3.21 / 3.16 before it.

## Changes
- **`Sketch.analyze(J, f, *, ok, budget_ms)`** returns the block FR5 specifies:
  `{status, dof, rank, n_params, n_residuals, free_entities, redundant,
  conflicting, analysis_ms, analysis_complete}`. `solve()` calls it once, on
  the Jacobian at the solution, and returns it under `diagnostics`.
- **`Sketch.dependent_rows(J, budget_ms)`** — the greedy forward selection
  above, with the "do not replace this with pivoted QR" reason in its
  docstring. Returns `(rows, complete)`; **on an exhausted budget it returns
  an empty list and `complete=False`**, and `analyze` then **omits**
  `redundant`/`conflicting` entirely. "We did not look" is never rendered as
  "nothing found" — the PRD-008 `unverified` rule applied to numerics. Budget:
  `ANALYSIS_BUDGET_MS = 50.0`, overridable per solve
  (`solve_sketch(spec, analysis_budget_ms=…)`).
- **`Sketch.free_entities(J, rank)`** reads the null space off the SVD's right
  singular vectors and maps parameter slots back to their owning entity via a
  new `Sketch.slot_owner` list. It uses the **column norms of the null-space
  basis**, not one singular vector at a time: any orthonormal basis spans the
  same subspace, so a per-vector reading depends on an arbitrary rotation and a
  column norm does not. `full_matrices` is only requested when the system has
  fewer rows than parameters, so the extra SVD stays cheap, and it is skipped
  entirely when `dof == 0`.
- **The redundant/conflicting split** is one measurement at the solution: a
  dependent row with `|f| <= SATISFIED_TOL` (1e-7) is redundant, a larger one
  is conflicting. A constraint with both kinds of dependent row is reported as
  conflicting.
- **A dependent row is always reported as the constraint the caller *wrote***:
  the entry's `type` comes from `Sketch.con_types[con_index]`, not from the
  compiled row's `kind`, so the three compiled rows of a
  `tangent_line_circle(at=…)` report `tangent_line_circle`. `origin` is carried
  through for slice 6's slot sub-entities.
- **`status`** is `well_constrained | under_constrained | over_constrained |
  did_not_converge`. Rank deficiency wins over `dof > 0` when a sketch is both
  (the `dof` field still tells that half of the story), and a *full-rank*
  failure to converge is `did_not_converge` rather than a constraint set the
  analysis never found.
- **`core/tools_sketch.py`: `over_constrained` alone is no longer an error.**
  A redundant-but-consistent sketch returns `ok: true` with `redundant: [...]`;
  a non-empty `conflicting` raises `ValidationError` naming the constraints and
  carrying `details.diagnostics`; a non-convergence with an empty `conflicting`
  raises with a message that says it is a solver failure rather than blaming a
  constraint pair. The previous code raised on `not ok` alone and passed no
  diagnostics.
- **The tool description states the honesty rule** the design demands:
  `conflicting` is *a* dependent set, "not necessarily the unique culprit",
  chosen by **declaration order**, so a spec submitted in arbitrary order gets
  an arbitrary (but still correct) member. `test_the_tool_description_never_claims_the_set_is_unique`
  asserts it rather than trusting it.
- **`parse_sketch(spec)` split out of `solve_sketch(spec)`** — the plan's
  slice-2 hook, needed here so a test can get at the compiled residuals and the
  Jacobian without re-implementing ingestion.

## Files
- `agentcad/toolkit/sketch.py` — `analyze`, `dependent_rows`, `free_entities`,
  `row_owners`, `slot_owner`, `parse_sketch`, four new tolerance constants, and
  the diagnostics paragraph in the module docstring
- `agentcad/core/tools_sketch.py` — the `ValidationError`-only-on-conflicting
  contract, the diagnostics text in the tool description
- `tests/test_sketch_diagnostics.py` — **new**, 25 tests
- `docs/agent-api.md`, `docs/part-authoring.md` — the `solve_sketch` row and
  the sketch section gain the `diagnostics` block (the full prose sweep is the
  plan's slice 14)
- `docs/changelog/0129-sketch-diagnostics.md` — this entry

## Verification
```
uv run pytest -q tests/test_sketch_diagnostics.py            -> 25 passed in 0.71s
uv run pytest -q tests/test_sketch.py tests/test_sketch_v1_corpus.py \
  tests/test_sketch_jacobian.py tests/test_sketch_bench.py -s -> 53 passed in 3.67s
make test-fast (with slice 4)                     -> 1215 passed, 1 skipped in 266.83s
```
The full-suite chunk table is in `0130-sketch-initial-warm-start.md`, which
lands with this slice: **`make test` — 1528 passed, 1 skipped** (1529
collected), run in chunks for the sandbox's 600 s foreground cap.

## Notes
- **`dof` never goes negative again.** Every row of the four-case table reports
  `dof: 0` where the shipped row count reported `-1`. The rank half of that fix
  shipped in 0128; this entry is the rest of it.
- **Declaration order is a heuristic, not a proof**, and nothing in this slice
  claims otherwise — in the payload, in the tool description, or in the error
  message. The GUI half (highlighting *all* members of the set) is slice 10.
- The QR regression test asserts the load-bearing half — that pivoted QR blames
  a constraint that was *already there* — rather than pinning the pivot index,
  because the index is LAPACK's pivot order. Locally it is **#3, `vertical`**,
  reproducing the design's measurement exactly.
- `tests/test_sketch_v1_corpus.py` deliberately did not freeze `dof`; it stays
  green unchanged, and this slice asserts `dof` in its own file instead.
- Pre-existing test files were not touched. The two-circle tangent-line sketch
  in `test_sketch_jacobian.py` now reports `over_constrained` with two
  redundant entries (its `at` form contributes two identically-zero rows,
  which *are* dependent rows) — `ok` stays `true`, which is exactly the
  contract this slice writes down.
