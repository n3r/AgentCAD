# 0088 — spec kernel handlers: spec_declare, spec_eval, clearance

- **Commit:** pending
- **Date:** 2026-08-10
- **Author:** Claude

## Summary
PRD-003 Slice 2: a new worker handler pack `agentcad/kernel/handlers/specs.py`
contributing three kernel methods — `spec_declare` (read a script's `SPECS`
without building), `spec_eval` (evaluate the shape tier against the built shape
and its metrics, predicates included) and `clearance` (minimum distance between
two world-placed items via `BRepExtrema_DistShapeShape`, the one genuinely new
geometry op in the feature). Nothing calls these yet; Slice 3's `SpecRunner`
will.

## Changes
- **`spec_declare {script, scope}` → `{declared: [...], warnings: [...]}`.**
  Executes the module with `worker._exec_script` — *not* `build_shape_ns` — so a
  project-scope `specs.py` (no `PARAMS`, no `build(p)`) declares, and a part
  declares even when its `build(p)` raises. Declarations cross JSON-RPC through
  `toolkit.specs.json_safe`, so `check_that`'s callable becomes
  `"predicate": true` and never leaves the worker.
- **Structural vs. script errors (FR1's split).** `SPECS` that is not a list, or
  that holds a dict no constructor produced, is
  `WorkerError(ERROR_CONTRACT, …)` naming `agentcad.toolkit.specs`; a
  constructor rejecting its own argument (`check_wall(min_mm="thick")`) raises
  while the module executes and therefore surfaces as `script_error` with
  `details.line`, byte-identically to a malformed `PARAMS`.
- **Scope mismatches are warnings, never drops.** A part-scope check found in a
  project module (or vice versa) is still returned in `declared`, with a warning
  naming the check, its scope and the module's scope — the runner needs to see
  it to report a skip rather than pretend it does not exist.
- **`spec_eval {script, params, density_g_cm3, densities?, indices?}` →
  `{checks, declared, warnings}`.** Builds through `build_shape_ns` (so a
  `spec_eval` sent with `affinity=part_id` right after a `build` is a shape-LRU
  hit — pinned by a test whose `build(p)` appends to a file: two evals, one
  build), reads `SPECS` from the returned namespace, and evaluates `valid`,
  `mass`, `volume`, `bbox`, `wall` and `that`. Metrics are computed at most
  once, lazily, via the toolbox `metrics` with `SOLID_LABELS` and per-solid
  densities honoured. `indices` selects a subset in order; `null` selects
  everything, with non-shape-tier kinds returned as named skips
  (`fem_static` → `reason: "deferred"`, a project-scope declaration →
  `reason: "unsupported_scope"`), never silently dropped.
- **Check records** carry `index`, `name`, `kind`, `scope`, `status`,
  `measured`, `limit`, `unit`, `requirement`, `location`, `message`, `details`
  (+ `reason`/`hint` on a skip). No `id`/`part`: the worker does not know which
  part it is building — `SpecRunner` joins on `index` and adds `<part>:<name>`.
- **Predicates run confined, and a broken one is payload.** `check_that`'s
  callable is invoked as `fn(part, metrics)` inside the worker. `True` → `pass`,
  `False` → `fail`, a non-bool return → `error` naming the returned type, a
  raise → `error` with `details.traceback` and `details.line` (from
  `worker._script_error_from_exc`) and a worker that is still answering (AC5).
  Every per-check evaluation is individually guarded: the handler never raises
  for a bad check, only for a structural `SPECS` problem.
- **`clearance {a, b, min_mm?}` → `{distance_mm, point_a, point_b, ok?,
  skipped_mesh?}`.** Items are resolved with `worker._item_shape(analysis=True)`
  — the same conservative `analysis(p)` envelope `interference` uses, so a
  measured gap is an *under*-estimate of the real one — placed with the toolbox
  `place` (intrinsic XYZ), then `LoadS1`/`LoadS2`/`SetMultiThread(True)`/
  `Perform()`/`Value()`/`PointOnShape1(1)`/`PointOnShape2(1)`. `SetDeflection`
  is left at its exact default: an approximate distance reported as a
  measurement would be dishonest. `ok` appears only when `min_mm` was given;
  overlapping or touching solids report `0.0` (this handler does not also try to
  be `check_interference_free`).
- **Failures are structured with a stage.** A resolve/place failure is
  `WorkerError(ERROR_KERNEL, "clearance unavailable: …", {"stage": "resolve",
  "side", "a", "b"})`; a distance-query failure is the same with
  `"stage": "distance"`. A `WorkerError` from the script (a `build(p)` that
  raises) is re-raised untouched so it keeps its line number.
- `tests/test_specs_kernel.py` — 27 tests, session-scoped `kernel` fixture,
  `pytest.mark.portability` (the `test_geom_diff.py` precedent for a handler
  pack). Not marked `slow`: the whole file runs in ~9 s, most of it worker
  startup.

## Files
- `agentcad/kernel/handlers/specs.py` — new handler pack (three methods).
- `tests/test_specs_kernel.py` — new test module.
- `docs/changelog/0088-spec-kernel-handlers.md` — this entry.

## Measurements (the plan's Slice-2 obligations)

All on **copies** of `examples/`, Apple Silicon, one warm worker.
`cold` includes building both parts; `warm` is a shape-LRU hit, which is what
the runner will actually pay per pair.

**`clearance` on `examples/rocketry` (all 3 instance pairs):**

| pair | cold | warm | measured |
|---|---|---|---|
| `nozzle_1` / `flange_1` | 94.5 ms | 54.2 ms | 0.500 mm |
| `nozzle_1` / `injector_plate_1` | 251.2 ms | 116.0 ms | 0.200 mm |
| `flange_1` / `injector_plate_1` | 187.9 ms | 187.7 ms | 0.400 mm |

**`clearance` on `examples/engine` (8 representative pairs of the 2080):**

| pair | cold | warm | measured |
|---|---|---|---|
| `block_1` / `head_a` (the two biggest castings) | 3864.9 ms | 196.0 ms | 0.150 mm |
| `block_1` / `crankshaft_1` | 235.8 ms | 92.5 ms | 0.300 mm |
| `block_1` / `head_bolts_a` (multi-solid bolt set) | 612.0 ms | 18.0 ms | 1.012 mm |
| `head_a` / `valves_a` (both multi-solid) | 597.1 ms | 523.5 ms | 0.002 mm |
| `crankshaft_1` / `rod_a1` | 151.4 ms | 7.9 ms | 0.250 mm |
| `piston_a1` / `wrist_pin_a1` | 352.3 ms | 180.3 ms | 0.100 mm |
| `block_1` / `oil_pan_1` | 133.8 ms | 55.0 ms | 0.400 mm |
| `cam_a` / `rockers_a` | 221.7 ms | 10.2 ms | 0.250 mm |

**Verdict:** a declared `check_clearance` pair costs **8–524 ms warm** on the
worst assembly in the tree, and the cold outlier (3.9 s) is *build* time, not
distance time. That is comfortably inside the `timeout_s=300.0` budget and well
inside `GATE_BUDGET_S`, so `check_clearance` ships as designed with no bbox
pre-filter. The design spec's `dist(A,B) ≥ dist(boxA,boxB)` pre-filter stays a
separate, optional slice — it is only worth it if a project declares dozens of
pairs, and it must never replace a real `measured`.

**Mesh (STL) operand — the deliberate probe, out of the test suite.**
`examples/construction/imports/11.stl` loads as `kind="mesh"`, **1 face, 0
solids, 0 shells** (one welded mesh Face, as documented). Running the distance
query on it directly, bypassing the handler's skip:

```
perform False   isdone False   nbsolution 0   0.5 ms
Value() -> Standard_Failure: BRepExtrema_DistShapeShape::Value: There's no solution
```

**Decision, from the evidence: v1 keeps skipping mesh sides.** Two findings, and
they point the same way. (1) `BRepExtrema` does **not** segfault the way a
boolean does — it returns `False` from `Perform()` in 0.5 ms and raises a
catchable `Standard_Failure` from `Value()` — so the exclusion is *not* a
crash-safety rule here. (2) It also produces **no solution at all** on a welded
mesh Face, so admitting mesh operands would buy a structured `kernel_error`
instead of a number. A named `skipped_mesh` side (which the runner turns into
`reason: "mesh_only"`) is strictly more useful than an error, and the handler's
`not performed or not IsDone() or NbSolution() < 1` guard would catch the case
anyway if a future OCCT changed its mind. Revisit only if OCCT gains a real
mesh-vs-B-rep distance path.

## Notes
- **Deviations from the plan, both deliberate:**
  1. The plan says to write "a private `_item_shape` mirroring
     `worker._item_shape`"; this pack **imports** `_item_shape` (plus
     `_exec_script`, `_script_error_from_exc` and `_solid_labels`) from
     `worker` instead. The reasoning is the plan's own reason for importing
     `_min_wall` from the sibling analysis pack: a second implementation of the
     script/reference split and its `analysis(p)` envelope cache is the worst
     outcome available. `handlers/motion.py` already takes the same back-edge
     for `pairwise_interference`; the import is inside `register()`, which only
     runs from `worker._load_handler_packs()`, i.e. after `worker` is fully
     imported.
  2. The plan expects a build-failure on one `clearance` side to carry
     `{"stage": "distance"}`. The handler reports **which** stage actually
     failed — an empty `Compound` is legal to return and impossible to place, so
     it fails in `resolve` (with the offending `side`), while a genuine query
     failure carries `distance`. Strictly more informative, identically
     structured; the test asserts the real stage.
- `worker.py` is **not** edited: pack discovery is automatic and the three
  method names collide with none of the six builtins (pinned by a test that
  reads `worker.HANDLERS` in a subprocess before any pack loads — a colliding
  name is dropped with only a stderr warning).
- `agentcad/kernel/handlers/specs.py` is the **only** file in this feature
  allowed to import OCP/build123d, and it is the only one that does.
- Test suite: `make test` → **776 passed, 1 skipped** (20:18) against a baseline
  of 749 passed, 1 skipped — exactly the 27 new tests, with **zero edits to any
  pre-existing test file**. `uv run pytest tests/test_specs_kernel.py -q` → 27
  passed in ~7 s; `tests/test_kernel.py tests/test_analysis.py
  tests/test_geom_diff.py` (the neighbours this pack imports from or mirrors) →
  53 passed, 1 skipped.
