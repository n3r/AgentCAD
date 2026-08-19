# 0273 — PRD-024: the kernel-internal `iou` handler pack

- **Commit:** pending
- **Date:** 2026-08-19
- **Author:** Nikita Fedorov

## Summary
Adds `agentcad/kernel/handlers/bench.py`, a worker handler pack registering
exactly one kernel method, `iou`, which is the geometry subscore of
AgentCAD-Bench (PRD-024, design spec Decision 5, FR5/FR7, AC4). It takes two
`worker._item_shape`-shaped sides and answers one number plus the evidence
behind it. It is deliberately **not** a model-facing tool.

## Changes
- New handler `iou`, params
  `{"candidate": <item>, "reference": <item>, "align": str,
  "rotations_deg": [[x, y, z], ...]}` where `<item>` is
  `{"script", "params"}` or `{"source"}` plus an optional
  `"position"`/`"rotation_deg"` — `worker._item_shape`'s grammar exactly.
  Result: `{"intersection_mm3", "union_mm3", "iou", "candidate_volume_mm3",
  "reference_volume_mm3", "candidate_solids", "reference_solids", "align",
  "rotation_deg", "status"}`, plus `"skipped_mesh": [...]` when a side is mesh.
- **Only the intersection is booleaned.** `union = volA + volB - inter` is
  arithmetic. A `|` on multi-solid Compound operands is exactly the operand
  shape `worker.pairwise_interference` warns about (`worker.py:650-653`), so a
  union boolean would double the OCCT failure surface for a number that
  subtraction already gives. Never `|`; the intersection is the `&` operator.
- **Both sides decompose to `shape.solids() or [shape]`** and are AABB-
  prefiltered, `pairwise_interference`'s two reasons verbatim. The pairwise sum
  equals `volume(A ∩ B)` only when each side's own solids are mutually
  disjoint, so the total is **clamped** to `min(sum, volA, volB)` and the ratio
  to `[0, 1]`; `candidate_solids`/`reference_solids` ride in the result so a
  reader can see when the clamp could have bitten. Two *disjoint* candidate
  cubes against a one-cube reference score exactly 0.5 with
  `candidate_solids == 2` — that case never reaches the clamp. The clamp doing
  real work is a candidate of two cubes overlapping *each other* by 5 mm over a
  coincident one-cube reference: the pairwise sum double-counts to 1500 mm³,
  more than the whole reference, which unclamped would be union 1500 and a
  perfect 1.0 for a visibly wrong candidate; clamped to `volB = 1000` it is
  union 2000 and 0.5.
- **Volumes come from `toolbox["shape_volume"]`** (`worker._shape_volume`, the
  sum over `shape.solids()`), never `.volume` — a boolean result is routinely a
  nested Compound whose `.volume` reports only the first child subtree.
- **A mesh side is never booleaned.** An imported STL is one welded Face and an
  OCCT boolean on it segfaults the worker, so a `kind == "mesh"` side
  short-circuits *before* any boolean to `status: "skipped_mesh"`,
  `skipped_mesh: ["candidate"]`, `iou: 0.0`, with both per-side volumes still
  reported — `handlers/diff.py:102-109`'s contract.
- **Alignment applies to the candidate only**; the reference is the datum.
  `world` (origin), `com` (`shape.center(b3d.CenterOf.MASS)`) and
  `bbox_center` (`shape.bounding_box().center()`). The transform is
  `translate(anchor_ref) ∘ rotate(r) ∘ translate(-anchor_cand)`, composed as
  `b3d.Location(anchor_r, rot) * b3d.Location(-anchor_c)` — the right operand
  is applied first — and applied with `shape.moved(loc)`, `worker._place`'s
  mechanism, intrinsic XYZ Euler degrees. Scale is never normalised: a part of
  the wrong size is a wrong part.
- **`rotations_deg` is a finite declared list.** Every entry is evaluated, the
  maximum IoU wins, ties keep the first, and the winning entry comes back as
  `rotation_deg`. Deterministic because the list is ordered and checked in
  order. The cap of 8 belongs to the bench task loader, not here. The **raw**
  value is tested before any defaulting: an absent (or `null`) key means
  `[[0,0,0]]`, while an explicit `[]` or a non-list is a **contract error**. A
  task that declares no permitted rotation and a task that omits the key are
  different claims, and only one of them is answerable — `params.get(...) or
  default` would have collapsed them and scored the first as the identity with
  no signal.
- **Every boolean is guarded** into
  `WorkerError(ERROR_KERNEL, "iou unavailable: …", {"stage": …})` with stages
  `candidate_volume` / `reference_volume` / `align` / `intersect`, so the
  scorer can record `status: "error"` (FR7) instead of crashing the harness or
  banking a silent zero. A pre-existing `WorkerError` (e.g. a `script_error`
  from `build_shape`) passes through with its type intact rather than being
  relabelled `kernel_error`.
- **`candidate_solids`/`reference_solids` are the true `len(shape.solids())`
  on every path** — `ok` and `skipped_mesh` alike. The boolean loop's
  `shape.solids() or [shape]` fallback exists so a solid-less shape is still
  intersected as itself; that convenience is deliberately not laundered into
  the reported count, which would otherwise say `1` for a shape with no solids.
- Contract errors, not tracebacks, for bad input: an unknown `align` mode, an
  empty or non-list `rotations_deg`, a non-triple or non-finite rotation or
  position, and a side that is neither `script` nor `source`.
- The boolean loop runs inside `contextlib.redirect_stdout(sys.stderr)`, as
  `pairwise_interference` does — OCCT chatters on stdout and the worker's
  protocol lives there.
- **No model-facing surface.** There is no `agentcad/core/tools_bench.py`; a
  test asserts `"iou"` is absent from `build_registry(service).list()`. The
  pack also does not shadow a builtin method name (`worker.py:800-803` prints
  no warning and `"iou" in worker.HANDLERS` is `True` after
  `_load_handler_packs()`).

## Files
- `agentcad/kernel/handlers/bench.py` — new pack: `register(toolbox)` closure,
  `_triple`, `_side`, `_anchor`, `_guarded`, `_boxes_touch`, `_common_volume`,
  `_decompose`, `_pass`, `handle_iou`; module constant `ALIGN_MODES`.
- `tests/test_bench_kernel_iou.py` — new, 17 tests, `portability`-marked.

## Notes
- Test evidence:
  `uv run pytest tests/test_bench_kernel_iou.py -q` → **17 passed in 9.72s**.
  Neighbouring kernel-handler suites unaffected:
  `uv run pytest -q tests/test_geom_diff.py tests/test_analysis.py
  tests/test_reference.py tests/test_kernel.py tests/test_tools.py` →
  **58 passed, 5 skipped in 37.99s**; `-n 2 --dist loadscope` over this module
  plus `test_geom_diff.py` → **25 passed**. Full suite:
  `make test — 4702 passed, 36 skipped (measured at branch tip 1ae80d1, all slices landed)`.
- Every volume assertion in the test module is analytic (a 10 mm cube is
  1000 mm³; a half overlap is 500, so IoU is 500/1500 = 1/3 exactly), so the
  test checks the handler's arithmetic rather than echoing it.
- The `Location` composition order is the one thing here that no obvious test
  arbitrates: the spec's rotation case (`rotations_deg=[[0,0,90]]`,
  `align="world"`) has a zero anchor and the alignment cases have a zero
  rotation, so both orders pass them. `test_alignment_and_rotation_compose_in_
  that_order` is the arbiter — a 30×10×10 slab centred at (25,0,0) against the
  same slab spun 90° about Z and parked at **(40,0,0)**. Both anchors are
  non-zero *and* the reference anchor is off the rotation axis, which matters:
  the first draft parked the reference at (0,0,40), an anchor invariant under a
  Z rotation, and a mutation that applied `translate(anchor_ref)` before the
  rotation passed it. Measured, not assumed — all three wrong compositions are
  now caught (they land the candidate at (0,25,0), (0,40,0) and (15,25,0)
  respectively).
- `IOU_TIMEOUT_S` and the budget-truncation rule live in
  `agentcad/bench/scoring.py` (a separate slice), not in the handler; the
  handler holds no clock.
- **Fix round 1** (review findings on `fbb00ec`, applied in place): the raw
  `rotations_deg` check above (the old `or`-default made the `not rotations`
  branch dead code and the entry's own "empty … is a contract error" claim
  false); a clamp test that actually reaches the clamp (the original used two
  *disjoint* solids, leaving the clamp sitting on its bound, and is now renamed
  `test_multi_solid_candidate_sums_over_its_solids` for what it does pin); the
  true solid count on the `ok` path; and the off-axis reference anchor in the
  composition-order test. Each of the four was confirmed by reverting the fix
  and watching the new test go red.
