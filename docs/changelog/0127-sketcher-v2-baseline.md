# 0127 — PRD-009 slice 1: the sketch baseline bench, the v1 corpus, declared numpy/scipy

- **Commit:** pending
- **Date:** 2026-08-12
- **Author:** Nikita Fedorov

## Summary
Slice 1 of the [sketcher-v2 plan](../superpowers/plans/2026-08-12-sketcher-v2.md)
changes no behaviour. It writes down what the shipped constraint solver
actually does — a 22-case compatibility corpus captured from the current
solver to `abs=1e-9`, and a benchmark that measures the FR6 numbers — so the
slice-2 rewrite cannot quietly change an answer, and so the 16 ms drag budget
is a measured regression rather than folklore. It also promotes `numpy` and
`scipy` from transitive to **declared** dependencies.

## The measured baseline (the point of this slice)

`uv run pytest -q tests/test_sketch_bench.py -s`, Apple M1 Max, Python 3.12,
numpy 2.5.1, scipy 1.18.0, 9 repetitions, p50:

| n_seg | params | residuals | nfev | cold p50 | warm-drag p50 |
|---:|---:|---:|---:|---:|---:|
| 10 | 20 | 20 | 5 | 2.59 ms | 2.08 ms |
| 25 | 50 | 50 | 5 | 13.44 ms | 10.76 ms |
| **50** | **100** | **100** | 5 | **50.48 ms** | **50.51 ms** |

The 50-segment warm-drag number is **3.2× over FR6's 16 ms budget**, before
any new entity type exists, and warm-starting buys nothing under a drag-sized
perturbation (50.51 vs 50.48 ms) — the cost is the finite-difference Jacobian,
not the iterations. That is what slice 2 fixes; **this slice deliberately
asserts no threshold**, because none of them can pass yet.

## Changes
- **`tests/test_sketch_v1_corpus.py` (new).** 22 cases: one per shipped
  constraint type (all 17, with `tangent_line_circle` covered in both its
  1-residual and its 3-residual `at` form and `tangent_circles` in both
  `external` and `internal`), plus the three sketches already in
  `tests/test_sketch.py` re-asserted to `abs=1e-9`. Each case asserts the
  solved coordinates, `ok`, `n_params` and `n_residuals` — the payload keys FR3
  freezes. `test_corpus_covers_every_v1_constraint_type` fails if a shipped
  type has no case; `test_result_keys_are_the_frozen_v1_set` pins the nine v1
  result keys as a subset (PRD-009 may only add).
  - Every case is **exactly constrained on purpose**: an under-constrained
    case's solution depends on the optimizer's path, which would make the
    corpus a test of scipy rather than of the solver.
  - `dof` is deliberately **not** asserted. The shipped
    `dof = n_params - n_residuals` is a row count, not a rank, and the
    `shipped_two_circle_tangent_line` case reports `dof: -2` today (its `at`
    form contributes two identically-zero residual rows). PRD-009 slice 2/3
    replaces it with `n_params - rank(J)`; the corpus must not freeze the bug.
- **`tests/test_sketch_bench.py` (new, marked `slow`).** The staircase
  generator (`n_seg` alternating H/V lines, each with an H/V constraint and a
  distance) at `n_seg ∈ {10, 25, 50}`, measuring cold p50 and warm-drag p50
  over 9 repetitions from a module-scoped fixture, printing the table. It
  asserts only that the sketches solve (`ok`, `max_residual < 1e-9`); FR6's
  inequalities are turned on in slice 2.
- **`pyproject.toml`.** `numpy>=2.0` and `scipy>=1.14` added to
  `[project] dependencies`. `agentcad/toolkit/sketch.py` imports both and runs
  in the **server** process, which is forbidden to import build123d — yet
  today those imports resolve only because build123d (and `ocp-gordon`,
  `scikit-fem`, `scikit-learn`, `svgpathtools`) happen to require scipy. A
  build123d release that drops scipy would silently break `solve_sketch`. See
  design Decision 1.

## Files
- `tests/test_sketch_v1_corpus.py` — new; the FR3 compatibility corpus
- `tests/test_sketch_bench.py` — new; the FR6 measurement harness (`slow`)
- `pyproject.toml` — `numpy`/`scipy` promoted to declared dependencies

## Verification
```
uv run pytest -q tests/test_sketch_v1_corpus.py tests/test_sketch.py
  -> 28 passed in 2.60s
uv run pytest -q tests/test_sketch_bench.py -s
  -> 4 passed in 1.46s   (table above)
make test-fast -> 1158 passed, 1 skipped   (baseline 1134 + 24 corpus cases)
```
`make test` was not run at this slice's tree state: this session's sandbox
caps a foreground command at 600 s and freezes background ones between calls,
and the full suite needs ~24 min, so it was run **in chunks at the end of
slice 2** and is reported there (1489 passed, 1 skipped of 1491 collected,
with the engine extremes sweep unable to complete in-sandbox). Nothing in this
slice changes production code, so the two states differ only by the tests
added here.
The first `make test-fast` on this tree also reported **1 failed**:
`test_prd008_acceptance.py::test_ac9_the_full_suite_count_is_cited`, which
requires the newest changelog entry to cite a `make test` count. It was
already failing before this work (`0126-prd-008-completed.md` cites "1441
passed" without the words "make test"); this entry citing one is what makes it
pass again.

## Notes
- **`uv.lock` still needs refreshing** — `uv lock --check` reports the lockfile
  out of date after the `pyproject.toml` edit. Both packages are already
  present in the lock at the versions in use (numpy 2.5.1, scipy 1.18.0) as
  transitive dependencies, so the refresh is expected to add exactly two
  entries to `agentcad`'s `dependencies` and two to `[package.metadata]
  requires-dist`, with no version churn anywhere else. Per the plan this is a
  **lead-only, run-once** task (`uv lock`) and was not run here.
- No production code changed in this slice. `agentcad/toolkit/sketch.py` is
  untouched.
