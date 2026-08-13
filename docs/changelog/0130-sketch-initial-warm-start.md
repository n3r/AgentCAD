# 0130 — PRD-009 slice 4: `initial` activated (branch selection, not speed)

- **Commit:** pending
- **Date:** 2026-08-12
- **Author:** Nikita Fedorov

## Summary
`initial` was declared in the `solve_sketch` schema as *"unused; reserved"*,
never read by the solver, and **not even forwarded by the route** — the route
pack whitelists explicit keys and `initial` was not one of them, so the
parameter was dead twice over. It is real now: it seeds the starting parameter
vector, it can never change the spec, an unknown entity name is a
`validation_error`, and a stale or partial `initial` degrades to a cold start
with a warning instead of crashing or seeding half a sketch.

## What `initial` is for, in the tool description
**It selects the solution branch. It is not the speed mechanism.** Measured on
the v1 solver (design spec, Ground Truth): 20 ms seeded *exactly* at the
solution and 51 ms seeded 0.4 mm away — iterations were never the cost, the
finite-difference Jacobian was, and slice 2 is what fixed that. Planning the
drag path as "wire up `initial` and it gets fast" lands a 51 ms slice; the tool
description now says so, so nobody has to re-derive it.

The mirror triangle in `tests/test_sketch_initial.py` is the demonstration:
`a` and `b` pinned on the x axis, `c` held by two distances, two solutions at
`(18, ±24)`. The same spec, with the same coordinates in `points`, returns
`+24` or `-24` depending only on `initial`.

## Changes
- **`Sketch.seed(initial)`** applies `{"points": {name: {x, y}},
  "circles": {name: {r}}}` to the entities' starting values and reports whether
  it warm-started. Semantics, in the order they are easy to get wrong:
  1. **It overrides the starting point, never the spec.** A value given for a
     `fixed` point or a `fixed_r` circle is accepted and has **no effect** —
     those are not parameters. Tested both ways.
  2. **An unknown entity name raises** `SketchError` → `validation_error`
     (FR4, verbatim). A silent ignore turns a client desync into a sketch that
     mysteriously stops warm-starting. An unknown *section* key raises too, so
     a typo like `{"pionts": …}` cannot masquerade as a cold start.
  3. **A stale or partial `initial` degrades to a cold start.** If it does not
     cover every free entity — the spec gained an entity mid-drag, or an entity
     is covered only halfway (`{"x": 12}` with no `y`) — **nothing is seeded**,
     the result carries `warnings: [{"code": "initial_incomplete", "message":
     …, "entities": [...]}]` and `warm_started: false`. Never crash, never seed
     half a sketch, never claim a warm start that did not happen.
- **`parse_sketch` calls `seed` after ingestion**, so `initial` reaches the
  solver through the same front door as everything else, and `solve()` reports
  the two new additive keys **`warm_started`** and **`warnings`**.
- **`core/tools_sketch.py`** forwards `initial` into the spec and documents it
  in the schema (it previously read `"unused; reserved"`).
- **`server/routes_sketch.py` whitelists `initial`**, with a comment recording
  why the route-pack contract makes that a two-place change: an un-whitelisted
  key never reaches the solver, however well the solver supports it.

## Files
- `agentcad/toolkit/sketch.py` — `Sketch.seed`, `warm_started`/`warnings` on
  the result, the `initial` paragraph in the module docstring
- `agentcad/core/tools_sketch.py` — `initial` forwarded and documented
- `agentcad/server/routes_sketch.py` — `initial` whitelisted
- `tests/test_sketch_initial.py` — **new**, 13 tests
- `docs/agent-api.md`, `docs/part-authoring.md` — `initial` documented as a
  branch-selection seed (the full prose sweep is the plan's slice 14)
- `docs/changelog/0130-sketch-initial-warm-start.md` — this entry

## Verification
```
uv run pytest -q tests/test_sketch_initial.py                -> 13 passed in 3.62s
uv run pytest -q tests/test_sketch_diagnostics.py            -> 25 passed in 0.71s
make test-fast (uv run pytest -q -n 2 --dist loadscope -m "not slow")
                                             -> 1215 passed, 1 skipped in 266.83s
```
`make test` was run **in chunks** — this sandbox caps a foreground command at
600 s and the full suite needs ~25 min — covering all **1529 collected** tests,
`-n 2 --dist loadscope` throughout (`-n 4` for the examples group):

```
-m "not slow"                                             1215 passed, 1 skipped  266.83s
-m slow  checks_pipeline/checks_ref/checks_cli/checks_api        93 passed        205.25s
-m slow  specs/specs_gate/specs_api/packet                      120 passed         92.96s
-m slow  anchors_kernel/pool/sandbox/proposals*/prd00{1,2,3,4,8}_acceptance
                                                                 69 passed        137.72s
-m slow  mcp/kernel/geometry_ci_action/comments_proposals/
         sketch_bench/sketch_diagnostics                         11 passed         18.19s
-m slow  examples -k "not engine"                                16 passed         82.66s
-m slow  examples engine (defaults, step export, interference)    3 passed        286.40s
-m slow  examples engine param extremes                            1 passed        897.05s
                                                          --------------------------------
                                                              1528 passed, 1 skipped
```
`test_parts_build_at_param_extremes[engine]` is the 63-instance sweep that
needs ~15 minutes of uninterrupted execution; it passed in **897.05 s**, three
seconds inside its own `pytest.mark.timeout(900)`, and it does not touch the
sketch solver (nothing under `examples/` imports `toolkit.sketch`).

Against the 1441/1 baseline this is the 49 tests slices 1–2 added plus the
**38** these two slices add (25 diagnostics + 13 `initial`), and 1529 collected
confirms the arithmetic.

## Notes
- **Public API compatibility.** `solve_sketch(spec)` keeps its shape; `spec`
  gains an optional `"initial"` key and the result gains `warm_started` and
  `warnings` (additive, per FR3). `solve_sketch` also grows a keyword-only
  `analysis_budget_ms` from slice 3. The v1 corpus is untouched and green.
- **`initial` is not the drag protocol.** Slice 8 adds the `drag` soft-pull
  block; warm-starting *from the cursor* is measured to **cause** mirror flips
  rather than prevent them (design Decision 9d), so the stable drag seeds from
  the previous frame's solution and pulls with a weak residual. `initial` is
  the "seed from the previous solution" half of that, and it is deliberately
  landing before the half that needs it.
- `frontend/js/api.js` still posts only `{entities, constraints}`; the GUI
  gains `initial`/`drag`/`diagnostics`/`emit` in slice 9, which is where the
  plan puts it.
