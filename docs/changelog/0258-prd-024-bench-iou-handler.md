# 0258 — PRD-024: the kernel-internal `iou` handler pack

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
  reader can see when the clamp could have bitten. A two-cube candidate against
  a one-cube reference scores exactly 0.5, with `candidate_solids == 2`.
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
- **`rotations_deg` is a finite declared list** (default `[[0,0,0]]`). Every
  entry is evaluated, the maximum IoU wins, ties keep the first, and the
  winning entry comes back as `rotation_deg`. Deterministic because the list is
  ordered and checked in order. The cap of 8 belongs to the bench task loader,
  not here.
- **Every boolean is guarded** into
  `WorkerError(ERROR_KERNEL, "iou unavailable: …", {"stage": …})` with stages
  `candidate_volume` / `reference_volume` / `align` / `intersect`, so the
  scorer can record `status: "error"` (FR7) instead of crashing the harness or
  banking a silent zero. A pre-existing `WorkerError` (e.g. a `script_error`
  from `build_shape`) passes through with its type intact rather than being
  relabelled `kernel_error`.
- Contract errors, not tracebacks, for bad input: an unknown `align` mode, an
  empty/non-list `rotations_deg`, a non-triple or non-finite rotation or
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
- `tests/test_bench_kernel_iou.py` — new, 14 tests, `portability`-marked.

## Notes
- Test evidence:
  `uv run pytest tests/test_bench_kernel_iou.py -q` → **14 passed in 6.48s**.
  Neighbouring kernel-handler suites unaffected:
  `uv run pytest -q tests/test_geom_diff.py tests/test_analysis.py
  tests/test_reference.py tests/test_kernel.py tests/test_tools.py` →
  **58 passed, 5 skipped in 15.79s**. Full suite: `make test — <orchestrator fills>`.
- Every volume assertion in the test module is analytic (a 10 mm cube is
  1000 mm³; a half overlap is 500, so IoU is 500/1500 = 1/3 exactly), so the
  test checks the handler's arithmetic rather than echoing it.
- The `Location` composition order is the one thing here that no obvious test
  arbitrates: the spec's rotation case (`rotations_deg=[[0,0,90]]`,
  `align="world"`) has a zero anchor and the alignment cases have a zero
  rotation, so both orders pass them. `test_alignment_and_rotation_compose_in_
  that_order` combines a non-zero anchor with a non-zero rotation — a 30×10×10
  slab centred at (25,0,0) against the same slab spun 90° about Z at the origin
  — and only `translate(anchor_ref) ∘ rotate ∘ translate(-anchor_cand)` scores
  1.0; rotating first sends the candidate to (0,25,0) and scores 0.
- `IOU_TIMEOUT_S` and the budget-truncation rule live in
  `agentcad/bench/scoring.py` (a separate slice), not in the handler; the
  handler holds no clock.
