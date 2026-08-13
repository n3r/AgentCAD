# 0134 — PRD-009 slice 8: the drag protocol (AC2, solver half)

- **Commit:** pending
- **Date:** 2026-08-12
- **Author:** Nikita Fedorov

## Summary

A drag frame is now a first-class shape on the solver, the tool and the route:
the full spec, plus `initial` seeded from the **previous frame's solution**,
plus `drag {point, x, y, weight?}`. The cursor enters as a **weighted soft
objective, not a constraint**, and every reported quantity is computed over the
constraint rows alone. Diagnostics come off the drag path behind a cache keyed
on the compiled residual structure, and the route handler becomes a sync `def`
so a solve runs in FastAPI's threadpool instead of on the event loop.

This is AC2's solver half. The GUI interaction is slice 10.

## The measurement, reproduced

The design's mirror-flip probe, re-run against this implementation — a, b
pinned on the x axis, c held by two distances, two solutions at
`(23.4375, ±18.7265)`:

```
naive: seed the dragged point AT the cursor       weak pull + previous-frame seeding
  cursor y=  18 -> c=(23.4375, +18.7265)            cursor y=  18 -> (23.4375, +18.7265)
  cursor y=   1 -> c=(23.4375, +18.7265)            cursor y=   1 -> (23.4375, +18.7265)
  cursor y=  -1 -> c=(23.4375, -18.7265)  <-- flip  cursor y=  -1 -> (23.4375, +18.7265)
  cursor y= -18 -> c=(23.4375, -18.7265)            cursor y= -18 -> (23.4375, +18.7265)
  cursor y= -30 -> c=(23.4375, -18.7265)            cursor y= -30 -> (23.4375, +18.7265)
```

Both directions are pinned as tests, the naive one **asserting the flip**, so
the "simplification" of seeding from the on-screen state cannot be reintroduced
as an improvement.

## Changes

- **`Sketch.drag(point, x, y, weight=None)`** — two rows, `w·(p − cursor)`,
  with an analytic `df` through the same `PointRef` indirection every other
  residual uses, so a **virtual handle** (`arc1.end`) is draggable with no new
  machinery. `DRAG_WEIGHT = 0.05`, the measured value, recorded as a named
  constant with the measurement beside it. Dragging a fixed point, an unknown
  handle, or passing a non-positive/NaN weight is a `SketchError`.
- **The exclusion contract, implemented as one rule.** The drag block lives
  outside `self.residuals` and is appended after the constraint rows only
  inside `make_functions`; `solve` slices `[:n_res]` off the residual vector
  and the Jacobian before anything is reported. So `ok`, `max_residual`,
  `n_residuals`, `rank`, `dof`, `free_entities`, `redundant` and `conflicting`
  all describe the constraints, and the pull's own slack is reported once, as
  `drag.gap`. A test asserts every one of those is **identical** with and
  without a drag block on the same spec.
- **`_settle`: one constraint-only re-solve, seeded at the drag's answer.** The
  soft pull is a compromise, and on a fully-constrained entity the compromise is
  visible: measured over a 48.7 mm drag of the triangle, the point lands
  **0.170 mm** off with `max_residual` **0.104** — so `ok: false` would be
  *correct*, and the design's own "returns ok: true" would not be. Settling
  fixes the cause instead of the report: the point comes back to exactly
  `(23.4375, 18.7265)` with `max_residual` 3.6e-15. It costs nothing when the
  drag moved only free DOF (the constraint rows are already satisfied, and it
  returns immediately); on a frame that needs it, **0.24 ms → 0.42 ms**.
- **Diagnostics caching** (`spec["diagnostics"] ∈ {auto, full, cached}`,
  default `auto`). `auto` recomputes except on a drag frame; `cached` prefers
  the cache; `full` always recomputes. The result carries
  **`diagnostics_source`** (`computed`/`cached`) — a cached block reports the
  `analysis_ms` of the solve that produced it, and saying so is the difference
  between a cache and a quiet lie.
  - **The cache key is the residual structure *and* the constraint targets.**
    Keying on structure alone would be wrong in a way that is easy to miss: a
    rectangle with a duplicated `distance 50` and the same rectangle with a
    contradictory `distance 60` compile to an *identical* residual structure
    and have opposite verdicts (`redundant` vs `conflicting`). A test pins
    that. Coordinates are deliberately **not** in the key: the GUI resends the
    whole spec each frame with its points at the last solution, so a
    coordinate-sensitive key would miss on every frame.
  - A `Sketch` built by hand (not through `parse_sketch`) has `con_args is
    None` and is never cached — there is no declared constraint list to key on.
- **`agentcad/core/tools_sketch.py`** — `drag` and `diagnostics` arguments,
  schema entries and tool description (including, plainly, that dragging a
  fully-constrained entity moving nothing is the correct behaviour and the old
  "responsive" version was teleporting to another solution).
- **`agentcad/server/routes_sketch.py`** — `drag` and `diagnostics`
  whitelisted, and the handler is now a **sync `def`** taking its body as a
  declared parameter (a sync handler cannot `await request.json()`). FastAPI
  runs it in the threadpool; the `async def` it replaces ran a synchronous
  solver directly on the event loop, where one long solve blocks `/ws` and
  every other request. A test asserts `not inspect.iscoroutinefunction(...)`.

## Files

- `agentcad/toolkit/sketch.py` — `DRAG_WEIGHT`, `DIAGNOSTICS_MODES`,
  `_DIAG_CACHE`, `Sketch.drag`, `Sketch.structure_key`, `Sketch._settle`,
  `Sketch._diagnostics`, drag-aware `make_functions`/`solve`, `parse_sketch`
  ingestion for `drag`/`diagnostics`, module docstring
- `agentcad/core/tools_sketch.py`, `agentcad/server/routes_sketch.py`
- `tests/test_sketch_drag.py` — **new**, 17 tests
- `tests/test_sketch_bench.py` — the scripted 100-step drag table (slow)
- `docs/agent-api.md`, `docs/part-authoring.md`
- `docs/changelog/0134-sketch-drag-protocol.md` — this entry

## Verification

```
uv run pytest -q tests/test_sketch_drag.py                     -> 17 passed
  (printed) 156 rows: diagnostics full    3.26 ms/frame (analysis 0.89 ms)
  (printed) 156 rows: diagnostics cached  2.24 ms/frame
uv run pytest -q -m slow -s tests/test_sketch_bench.py tests/test_sketch_arcs.py
                                                               -> 11 passed
```

**The drag frame, 100 scripted steps, warm-started from each previous frame, a
12 mm sweep (~0.75 mm between consecutive frames — a fast drag):**

```
          sketch   par  rows       p50       p95       max  flips    max_res
        cam lobe     8     6    0.45ms    0.65ms    0.69ms      0    9.8e-15
    staircase 50   100   100    6.72ms    7.61ms   22.77ms      0    4.9e-19
 arc ring + slot   132   132   10.12ms   11.98ms   13.33ms      0    1.2e-11
```

`arc ring + slot` is the answer to "what does a drag frame cost with arcs and
slots in the sketch": **10.1 ms p50 / 12.0 ms p95** on a 50-entity ring of
tangent arcs and lines with a slot, 132 parameters and 132 constraint rows,
against the 16 ms FR6 budget — and every one of those frames served **cached**
diagnostics, which the bench also asserts. Zero branch flips on all three, the
invariant being the sign of every arc's signed sweep (a mirror flip *is* an arc
taking the other way round).

`make test` was run **in chunks** — this sandbox caps a foreground command at
600 s and the full suite needs ~35 min — `-n 2 --dist loadscope` throughout
(`-n 4` for the non-engine examples, serial for the param sweep). The union
covers all 1 647 collected tests:

```
-m "not slow"                                             1328 passed, 1 skipped  287.81s
-m slow  checks_pipeline/checks_ref/checks_cli/checks_api        93 passed        208.98s
-m slow  specs/specs_gate/specs_api/packet                      120 passed         91.60s
-m slow  anchors_kernel/pool/sandbox/proposals*/prd00{1,2,3,4,8}
                                                                 69 passed        135.69s
-m slow  mcp/kernel/geometry_ci_action/comments_proposals/
         sketch_diagnostics                                       5 passed         17.28s
-m slow  sketch_bench + sketch_arcs                              11 passed          2.57s
-m slow  examples -k "not engine"  (-n 4)                        16 passed         64.45s
-m slow  examples engine (defaults, step export, interference)    3 passed        338.44s
-m slow  examples engine param extremes (serial, alone)           1 passed        887.42s
                                                          --------------------------------
                                                              1646 passed, 1 skipped
```

Against the 1599/1 baseline that is **+47**: 26 emitter tests (slice 7), 17
drag tests, and 4 `slow` drag-benchmark tests. (PRD-008's
`test_ac9_the_full_suite_count_is_cited` red-lined the fast suite until this
block existed — "the newest changelog entry cites no suite count" — which is
the check working exactly as designed. The run above is with it present.)

**`test_parts_build_at_param_extremes[engine]` must run alone.** It needs ~15
minutes inside a `pytest.mark.timeout(900)`: run concurrently with another
chunk it timed out at 900.14 s, and run by itself it passed in **887.42 s**.
Nothing under `examples/` imports `toolkit.sketch`.

**The `staircase 50` max is harness noise, and it is quoted rather than
hidden.** Across runs it ranged 14.6 / 17.3 / 22.8 ms while p50 stayed at
6.3-6.7. Driving the identical 100-frame sweep in a bare interpreter puts the
five slowest frames at **7.78 / 7.40 / 7.38 / 7.31 / 7.27 ms** — no outlier at
all — so the spikes belong to the pytest process (GC and allocator behaviour
after the preceding benchmark module), not to the solver. The assertion is on
p50, as the plan specifies; the max is printed so a real regression in it would
still be visible.

## Notes

- **`tr_solver="lsmr"` was measured on the drag path and rejected.** Slice 5's
  note recorded it as the lead to pull if the budget tightened. Measured here
  over a 100-frame drag with warm caches it is not a free win — it trades one
  sketch shape for another:

  ```
                       exact p50 / max     lsmr p50 / max
  staircase 50          6.27 /  6.77 ms   11.63 / 23.69 ms   <- over budget
  arc ring + slot       9.09 /  9.97 ms    6.28 /  7.01 ms
  ```

  `exact` (scipy's default) clears the budget on both; `lsmr` does not. The
  finding is recorded as a comment at the top of the solve section so the next
  person does not re-litigate it from the slice-5 note alone.
- **The 16 ms budget is a per-frame budget, so the maximum matters as much as
  the median.** That is why the bench reports p50, p95 *and* max, and why the
  sweep is 12 mm rather than the 3 mm a slow drag produces: a small sweep flatters
  every configuration (the same ring measures 5.77 ms p50 at 3 mm).
- **`drag.gap` is not a residual and never becomes one.** On the fully
  constrained triangle it reads 48.7265 mm; multiplied by the weight that is
  the 2.43 the design measured as `max_residual` when the objective was counted
  — the test asserts that product explicitly, so the number the design named is
  still visible after the thing it described was fixed.
- The diagnostics cache is module-level and bounded at 32 entries (FIFO). The
  route is stateless — every frame compiles a fresh `Sketch` — so a per-object
  cache would never hit.
- Slice 10 (the GUI) needs: one request in flight at a time, `initial` from the
  previous frame's solution, `drag` with the coalesced cursor, `diagnostics`
  left at its default during the drag and `"full"` on `pointerup`, and nothing
  on that path that breaks HTTP connection reuse (measured: 0.72 ms p50 with
  keep-alive, 12.55 ms p50 / 16.47 ms p95 without — 78% of the budget).
