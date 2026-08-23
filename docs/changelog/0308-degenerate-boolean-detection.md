# 0308 — Degenerate-boolean detection: an intersection OCCT cannot compute fails closed

- **Commit:** pending
- **Date:** 2026-08-23
- **Author:** Claude (Opus 5) with Nikita Fedorov

## Summary

OCCT 7.9's `BRepAlgoAPI_Common` (build123d's `&`) silently returns a wrong
answer — usually **empty**, sometimes negative-volume — for solids carrying
G1-tangent face junctions, i.e. any filleted swept solid. Both places that
boolean solid pairs read that empty result as `0.0` and banked it: the bench
scored a perfect candidate `iou: 0.0`, and `worker.pairwise_interference`
reported an overlapping assembly **clean**. This adds
`agentcad/kernel/handlers/_bop.py`, a shared detector, and makes both callers
fail closed on it — the bench with a kernel error (subscore `error`, excluded,
FR7), the product path with the pair listed and marked `degenerate: true`.

## The corrected root cause

The recorded narrative (changelog `0280`, `benchmarks/.../fix_005_invalid_shell/prompt.md`,
`docs/bench.md`, `AGENTS.md`) says the **STEP round trip** is the trigger and
that STEP-vs-STEP intersects cleanly. That is wrong, and changelog `0282:214-223`
already had the measurement right: STEP ⊗ STEP is degenerate too. Measured live
against the pinned venv, on the bench's own coolant elbow (`fix_005` reference
defaults — a Ø24 × 3 mm annulus swept along a 24 mm-radius right-angle bend,
21 711.685 mm³):

| operands | `A & B` |
|---|---|
| script ⊗ *itself* (same Python object) | 21 711.685 (correct) |
| script ⊗ independently rebuilt script | 21 711.685 (correct) |
| STEP ⊗ *itself* | 21 711.685 (correct) |
| **script ⊗ STEP re-import** | **empty** |
| **STEP ⊗ second STEP re-import** | **empty** |
| script(bend_r=24) ⊗ script(bend_r=30), both at the origin | **empty** |
| script ⊗ script shifted 1 mm in Z | 19 093.56 in a fresh process, **empty** when another boolean ran first |

So provenance is not the trigger; *sameness* is the thing that hides the bug —
when the two operands are the same shape OCCT takes a shortcut and answers
correctly. Two genuinely distinct tangent-jointed swept solids that overlap can
intersect to nothing. Worse, the shifted-pair row above shows the failure is
**order-dependent inside one process**: an earlier boolean changes whether the
next one tells the truth. `IsDone()` is `True` throughout, nothing is raised,
and this OCP build exposes no `HasErrors()`. Fuzzy booleans at every tolerance,
`ShapeFix`, `UnifySameDomain`, sewing, `Copy`, `Glue` and OBB alignment were all
probed during the research pass and none of them help — which is why this
change is **detection**, not healing. Prose corrections land separately (T3).

## The detector (`agentcad/kernel/handlers/_bop.py`)

`checked_common_volume(solid_a, box_a, solid_b, box_b, volume_of) -> (volume, degenerate)`:

- a **positive** volume is trusted and returned as-is — the detector is never
  the measurement;
- a **negative** volume is impossible, so it is `(0.0, True)`. Both call sites
  used to launder exactly this: the bench with `max(volume, 0.0)`, the worker by
  summing it into the pair total;
- a **non-finite** volume is `(0.0, True)` too (every comparison against NaN is
  false, so it walked through both guards below);
- an **empty** result is rechecked *only when suspicious* — when the two AABBs
  overlap by at least `SUSPECT_OVERLAP_FRACTION = 0.5` of the smaller box. The
  recheck crops `solid_a` to each **octant** of the overlap box and booleans the
  pieces against `solid_b`, short-circuiting on the first hit. The subdivision
  is the mechanism, not an optimisation: an octant boundary cuts *through* the
  tangent junctions, leaving pieces OCCT handles correctly. Any piece that
  intersects means the two computations disagree, so the whole-solid boolean
  lied. An exception raised inside the recheck is also a disagreement.

Measured on this machine: the degenerate coincident-elbow pair is detected in
0.38–0.76 s; a legitimate empty with 100 % AABB overlap (a hollow shell and a
core sitting in its cavity) costs 0.02 s and is **not** flagged; a near-miss
never reaches the recheck at all (0.00 s). Two elbows meeting face-to-face at
the origin (one rotated 180°) have zero AABB overlap volume, so the correct
empty is returned without a recheck.

**Cost analysis.** Nothing changes for a pair whose boolean succeeds — the only
added work is one `min()` and two comparisons. Only an *empty* result on a pair
whose AABBs already overlap by half the smaller box pays anything, and the bill
is then bounded at 8 crops plus one boolean per resulting piece, short-circuited
on the first hit.

That combination is **not** vanishingly rare, and the changelog should not
pretend otherwise: forcing `_disagrees` to return `True` drops the interference
subscore of `asm_001_thrust_chamber` (1.0000 → 0.7500) and
`asm_003_bolted_joint` (1.0000 → 0.5583) while leaving `asm_004_truss_node`
untouched, which proves the recheck *does* run on the first two — a cropped
piece of a casting sitting entirely inside a fastener's AABB and not touching it
is exactly the suspicious-and-empty shape. What matters is that it costs
nothing measurable and never lies: with the real detector all three still score
**1.0000**, and three timed runs of `asm_003` (the most fastener-heavy
reference) are 16.61/13.52/16.39 s with the recheck enabled against
13.45/17.72/16.25 s with `_suspicious` stubbed to `False` — indistinguishable
from run-to-run noise.

**Residual blind spot.** A degenerate *plausible-positive* result — some wrong
non-zero volume — is not detected. Catching it would mean re-deriving every
intersection by an independent method, which costs more than the measurement it
checks. What is covered is the empty-that-should-not-be (the false "clean", the
false IoU 0) and the negative/NaN cases.

## `worker.py` — a deliberate core edit

`AGENTS.md` forbids editing `worker.py` **"to add a feature"**. This is not a
feature: `pairwise_interference` is inside `worker.py` with no pack seam, and
its `_common_vol` is the exact line that banks the wrong answer. The edit is
kept minimal and in place:

- the function's **signature and return type are unchanged**;
- entries gain **one optional key**, `"degenerate": True`, emitted only when
  set. A reader that does not know the key sees an ordinary interfering pair —
  which is the safe reading;
- the pair-emit condition becomes `if volume > min_volume or degenerate:`, so a
  pair whose boolean failed is reported even though its measured volume is
  `0.0`;
- `_common_vol` now measures with `_shape_volume` (the solids sum) instead of
  `.volume`, closing the nested-Compound undercount on this path as a side
  effect.

`handle_interference`, `service.check_interference` and `handlers/motion.py`
need no change: `motion` reads `pairs → first_collision → clear=False`, which is
fail-closed by construction, and the extra key rides through the JSON protocol
untouched.

## Before and after (measured through the real kernel)

Pre-fix, with the detector stubbed back to "empty means clean":

```
PRE-FIX iou: {'iou': 0.0, 'intersection_mm3': 0.0, 'union_mm3': 43423.37,
              'candidate_volume_mm3': 21711.685, 'reference_volume_mm3': 21711.685,
              'status': 'ok'}
PRE-FIX interference: {'pairs': []}
```

A candidate that *is* the reference scored 0.0, and two coincident elbows were
reported as a clean assembly. Post-fix:

```
POST-FIX iou: kernel_error | iou unavailable: degenerate boolean — OCCT could
  not intersect this solid pair (tangent-jointed swept solids are unreliable BOP
  operands in OCCT 7.9); refusing to bank the empty result as a measurement | intersect
POST-FIX interference: {'pairs': [{'a': 'elbow_r24', 'b': 'elbow_r30',
                                   'volume_mm3': 0.0, 'degenerate': True}]}
ping: True
```

## AC1 safety (all 25 references still score 1.0)

- `fix_005`'s `task.json` is byte-unchanged; its `geometry` weight is `0.00`, so
  the degenerate boolean never reaches its total. Verified: `bench score` on its
  reference project still reports **1.0000 over 4 subscore(s)**.
- A geometry-weighted reference (`mfd_003_head_flange`, geometry 0.50) still
  **1.0000**; the three interference-weighted `assemble_and_clear` references
  checked (`asm_001`, `asm_003`, `asm_004`, interference 0.40) still **1.0000**
  each, with `interference` `ok`/1.0000 — and two of the three demonstrably run
  the recheck (see the cost analysis above), so that is a *measured* absence of
  false positives, not an untested path.
- Structurally, a reference whose booleans succeed today takes the *positive*
  branch and is untouched; only an empty-and-suspicious result runs new code.
- `interference_fraction` needs no change: a degenerate pair is *in* `pairs`, so
  it already costs the assembly its clean count. `score.json` gains the
  `degenerate` key only when set, so a clean run's bytes are unchanged (AC3).

## Changes

- **New** `agentcad/kernel/handlers/_bop.py` — the detector. Underscore-prefixed,
  so `worker._load_handler_packs` skips it (the `_pdf.py` precedent): it is a
  shared helper, not a handler pack.
- `agentcad/kernel/worker.py` — `pairwise_interference` routes every
  Solid-vs-Solid boolean through the detector and threads `degenerate` up
  through `_solid_common`'s crop branch into the emitted pair.
- `agentcad/kernel/handlers/bench.py` — `_common_volume` takes both AABBs and
  raises `WorkerError(ERROR_KERNEL, "iou unavailable: degenerate boolean — …",
  {"stage": "intersect"})` on a degenerate pair. It is raised inside `_guarded`'s
  reach, whose `except WorkerError: raise` keeps the type and stage intact, so
  `scoring._geometry_part`'s `KernelError` arm records `status: "error"` and
  **excludes** the subscore rather than banking a false 0.0. The module header's
  stale `worker.py:650-653` line reference is replaced with a name.
- `agentcad/core/specs.py` — `_eval_interference` still fails on a degenerate
  pair (it is in `pairs`) and now says why: `"; N indeterminate (OCCT could not
  boolean the pair — counted as interfering, fail-closed)"`. The clause is
  conditional, so an ordinary interfering row's message is byte-unchanged. It
  matters because a degenerate pair's `volume_mm3` is `0.0`, and `measured: 0.0`
  on its own reads as "they barely touch".
- `agentcad/bench/scoring.py` — `_interference` preserves `degenerate` into the
  pair detail, emitted only when set.

## Files

- `agentcad/kernel/handlers/_bop.py` — new; `checked_common_volume`,
  `SUSPECT_OVERLAP_FRACTION`, `_suspicious`, `_crop`, `_disagrees`. The module
  docstring is where the measured root cause and the residual blind spot live.
- `agentcad/kernel/worker.py` — `pairwise_interference` docstring, `_common_vol`,
  `_solid_common`, the pair-emit block; one new import.
- `agentcad/kernel/handlers/bench.py` — module docstring (a fifth trap, "Four"
  → "Five", and the stale line reference), `_common_volume`, its one call site,
  one new import.
- `agentcad/core/specs.py` — `_eval_interference`'s fail message.
- `agentcad/bench/scoring.py` — `_interference`'s pair detail.
- `tests/test_kernel.py` — `test_interference_reports_a_boolean_it_could_not_compute`
  (the product-path regression) and
  `test_interference_does_not_cry_degenerate_on_a_clean_assembly` (the other
  half of the guard); `ELBOW_SCRIPT`.
- `tests/test_bench_kernel_iou.py` — `test_a_degenerate_boolean_is_refused_not_banked_as_a_zero`
  (STEP exported through the kernel, `KernelError`, `"degenerate"` in the
  message, `stage: "intersect"`, and the worker still answering `ping`);
  `ELBOW`; a docstring correction on `test_a_non_finite_intersection_is_an_error_too`,
  whose mechanism moved from `max(nan, 0.0)` into `_bop`.
- `tests/test_specs.py` — `test_a_pair_the_kernel_could_not_boolean_fails_and_says_so`
  and `test_a_measurable_overlap_keeps_the_message_it_always_had`, both
  kernel-free over a stub `check_interference`.
- `tests/test_bench_scoring.py` — `test_a_degenerate_pair_keeps_its_marker_in_the_detail`
  and `test_an_ordinary_pair_carries_no_degenerate_key`.

## Notes

- **Plan deviation, argued.** The plan's product-path regression called for two
  elbows with the second at `position=[0,0,5]`. Measured, that pair is **not**
  degenerate (11 194.235 mm³, reproducibly, in a fresh process); the shifted
  pairs that do fail (1 mm, 0.1 mm) fail only *order-dependently* and would make
  a flaky test. The test instead uses two elbows at the **same origin**
  differing only in `bend_r` (24 vs 30) — coincident legs down both axes,
  obviously overlapping, and empty from OCCT in five out of five fresh
  processes. Same code path, same claim, deterministic.
- **`_bop` is never imported from `agentcad/bench/**`** — it imports build123d,
  and `test_prd024_acceptance.py::test_ac9_no_bench_module_imports_ocp_or_build123d`
  is the guard. It is reached from the bench only through the kernel handler.
- A degenerate pair shows in the UI as an interfering pair with volume 0.0.
  Cosmetic, known, not fixed here.
- `_disagrees` crops `solid_a` specifically. `pairwise_interference`'s
  `_solid_common` may have swapped the operands by then, so which solid gets
  cropped is not stable across callers — acceptable for a detector, and stated
  here so nobody reads determinism into it.

## Verification

```
$ uv run pytest -q tests/test_bench_kernel_iou.py tests/test_kernel.py \
      tests/test_specs.py tests/test_bench_scoring.py tests/test_motion.py \
      tests/test_bench_runner.py
191 passed, 2 skipped in 54.43s
```

RED first: with `checked_common_volume`'s empty branch stubbed back to
`(0.0, False)` — the pre-fix behaviour — both new kernel tests fail
(`DID NOT RAISE KernelError`, and the interference pair list is empty). Restored,
both pass.

```
$ uv run agentcad bench score benchmarks/tasks/fix_the_broken_part/fix_005_invalid_shell/reference/project \
      --task fix_the_broken_part/fix_005_invalid_shell
bench score: fix_the_broken_part/fix_005_invalid_shell — 1.0000 over 4 subscore(s)
$ uv run agentcad bench score benchmarks/tasks/assemble_and_clear/asm_004_truss_node/reference/project \
      --task assemble_and_clear/asm_004_truss_node
bench score: assemble_and_clear/asm_004_truss_node — 1.0000 over 5 subscore(s)
```

`ruff check` clean on every touched file (`worker.py` keeps its pre-existing
E402 block — 15 before, 16 after, the new import line; the file's imports all
sit below `apply_from_env()` by design).

`make test` — <orchestrator fills>
