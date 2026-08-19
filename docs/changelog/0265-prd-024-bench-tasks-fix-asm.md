# 0265 — PRD-024 bench tasks: all five `fix_the_broken_part` and all five `assemble_and_clear`

- **Commit:** pending
- **Date:** 2026-08-19
- **Author:** Nikita Fedorov

## Summary

Ten new AgentCAD-Bench task bundles under `benchmarks/tasks/` — the whole
`fix_the_broken_part` category (a starter that is wrong in exactly one named
way, plus the corrected reference) and the whole `assemble_and_clear` category
(the parts present, the instance list empty, and a project-scope rubric that
measures the placement). Pure authoring plus one read-only test file; no
module under `agentcad/` changed. Every reference scores exactly **1.0** and
every starter scores **well under 0.95**, both numbers taken from
`agentcad bench score` and quoted below.

## Changes

### `fix_the_broken_part` (design §7.3) — weights `0.25/0.15/0.35/0.15/0/0.10`, `wall_s` 600, `turns` 24

| id | starter's defect (exactly one) | source | reference |
|---|---|---|---|
| `fix_001_contract` | `PARAMS` declares `"thicknes"` while `build` reads `p.thickness`, **and** `build` falls off the end without returning the shape | authored (`sensor_mount`) | both corrected |
| `fix_002_fillet` | the end break is hard-coded `p.thk * 2` = 12 mm on a 6 mm leg, ignoring the `edge_r` parameter the script already declares; OCCT refuses the build | authored (`shelf_bracket`) | `toolkit.fillet.safe_fillet` at `edge_r` = 4 mm |
| `fix_003_wall_red` | stored `wall` is 1.2 mm, not the 2.5 mm the tool was cut for; builds fine, and the shell weighs 21.77 g instead of 40.23 g | derived: `prototyping/enclosure_base` | `wall` = 2.5 |
| `fix_004_hole_pattern` | one edited line — `patterns.grid(3, 2, …)` instead of `(2, 2, …)` — pushes a whole column of anchor slots off the plate; valid, and wrong | derived: `construction/base_plate` | the example script, unedited |
| `fix_005_invalid_shell` | stored `bend_r` is 6 mm against a 12 mm tube outer radius, so the swept shell folds through itself: the build succeeds and `shape.is_valid` is `False` | authored (`coolant_elbow`) | `bend_r` = 24 (one tube diameter) |

- Each `prompt.md` states the **symptom** and what "fixed" means, never the
  fix; each states the datum in words that agree with `frame.datum`.
- Rubrics are `specs/parts/<part>.py`, re-binding `SPECS` with `_bench_`
  aliases. Two carry measured arguments a reader needs:
  - `fix_003` has **no `check_wall`**, and says why: this shell's four corner
    bosses are placed *tangent* to the inner walls (`… - wall - br + 0.5`), so
    the ray sampler reads the tangency and not the wall — **0.0095 mm at grid
    12, at (-46.67, 27.5, 3.65)**, at wall 2.5 *and* at wall 1.2. The 2.5 mm
    requirement is therefore stated as a mass window (measured 21.7708 /
    33.3084 / 40.2343 g at wall 1.2 / 2.0 / 2.5).
  - `fix_004` states its requirement as geometry: a `check_that` predicate
    pushes a 4 × 4 mm probe column down the Z axis at each of the four nominal
    slot centres (±100, ±100) and fails if it meets material. The intersection
    uses the **`&` operator** (`Shape.intersect()` returns a `ShapeList`).
    A `check_mass` window at ±0.5 % carries the arithmetic half (four slots
    take 278.25 g more out of the plate than the two the off-by-one leaves).

### `assemble_and_clear` (design §7.4) — weights `0.10/0.05/0.35/0/0.40/0.10`, `wall_s` 900, `turns` 30

| id | reference placement | rubric (`specs/project.py`) | measured |
|---|---|---|---|
| `asm_001_thrust_chamber` | `nozzle_1` (0,0,0), `flange_1` (0,0,-14.2), `injector_plate_1` (0,0,0.2) | `examples/rocketry/specs.py`'s block, **INT-003 kept** | 3 instances clean; `flange_bore_gap` 0.500, `injector_gasket_gap` 0.200 |
| `asm_002_lid_on_base` | `base_1` (0,0,0), `lid_1` (0,0,30.1) | interference-free + `seat_gap` ≥ 0.05 | 2 clean; 0.100 mm |
| `asm_003_bolted_joint` | `tapped_plate_1` (0,0,0), `clamp_plate_1` (0,0,0), `cap_screw_1` (0,0,8) | interference-free + `thread_clearance` ≥ 0.5 | 3 clean; 1.177 mm |
| `asm_004_truss_node` | `base_plate_1` (0,0,0), `gusset_1` (0,0,21) rot [90,0,0], `bracket_left` (-40,0.5,20.5) rot [0,0,90], `bracket_right` (40,-10.5,20.5) rot [0,0,-90] | interference-free + `gusset_seat` ≥ 1.0 + two `*_web_gap` ≥ 0.25 | 4 clean; 2.000 / 0.500 / 0.500 mm |
| `asm_005_rod_and_piston` | `rod_1` (0,0,0), `rod_cap_1` (0,0,-0.05), `rod_bolts_1` (0,0,0), `piston_1` (0,0,110), `wrist_pin_1` (0,0,110) — **no `engine_block`** | interference-free + `pin_bore_gap` ≥ 0.05 + `big_end_joint` ≥ 0.02 | 5 clean; 0.125 / 0.050 mm |

- Every `asm` starter ships the parts with **zero instances**, so
  `check_interference_free` skips `no_instances` — which the scorer counts as a
  **failure** (`CANDIDATE_SKIP_REASONS`), and the `check_clearance` rows come
  back as errors naming instances that are not there. `specs` measures 0.0 and
  `interference` measures 0.0 on every starter.
- Every prompt names the exact **instance ids** the rubric addresses; a new
  test pins that (see below), because a `check_clearance` row is addressed by
  name and an agent that was never told the name cannot pass it.
- The `asm` bundles declare `reference.steps: {}` and ship **no STEP datum**:
  the category's `geometry` weight is 0.00, so nothing reads one. Generating
  them cost 5.2 MB (3.1 MB for `asm_005` alone — `rod_bolt_pair` is 60 solids
  and 490 faces of real thread) for a file no subscore opens.

### One argued weight override

`fix_005_invalid_shell` sets `geometry` 0.00 and `metrics` 0.25, argued in an
HTML comment at the top of its `prompt.md` (design §7.6). Measured reason: the
part's swept pipe surface does not survive the STEP round trip as a boolean
operand. Script-vs-script and STEP-vs-STEP both intersect cleanly at
21711.685 mm³, but the boolean the IoU handler actually takes — the candidate's
script solid against the checked-in STEP — returns `None`, i.e. 0.0 mm³ of
intersection between two solids of *identical* volume, so the reference scored
`iou` 0.0 and totalled 0.85. A geometry weight there would score every
submission zero on shape; `metrics` measures the same fact through its mass,
volume and bbox windows and carries the 0.15 instead.

### Tests

`tests/test_bench_tasks_fix_asm.py` (new, 10 tests, no kernel, no writes into
`benchmarks/`, 0.2 s): each category ships five tasks with a starter; every
`fix` starter really differs from its reference (script bytes or stored
params); every `asm` starter places no instances and every `asm` reference
places at least two (fewer than two and `interference_free` skips
`no_instances`, so the reference could not reach 1.0); every instance id a
`check_clearance` names is one the reference places **and** one the prompt
names; no shipped starter or reference part script declares its own `SPECS`;
and a `fix` task with `geometry` 0.00 must argue it in its prompt front matter.

## Files

- `benchmarks/tasks/fix_the_broken_part/{fix_001_contract,fix_002_fillet,fix_003_wall_red,fix_004_hole_pattern,fix_005_invalid_shell}/`
  — `task.json`, `prompt.md`, `starter/`, `reference/project/`,
  `reference/steps/<part>.step`, `reference/metrics.json`,
  `specs/parts/<part>.py`
- `benchmarks/tasks/assemble_and_clear/{asm_001_thrust_chamber,asm_002_lid_on_base,asm_003_bolted_joint,asm_004_truss_node,asm_005_rod_and_piston}/`
  — `task.json`, `prompt.md`, `starter/`, `reference/project/`,
  `reference/metrics.json`, `specs/project.py`
- `tests/test_bench_tasks_fix_asm.py` — new
- `docs/changelog/0265-prd-024-bench-tasks-fix-asm.md` — this entry

## Notes

**AC1 — every reference scores exactly 1.0**
(`uv run agentcad bench score <bundle>/reference/project --task <id> --json`):

```
fix_the_broken_part/fix_001_contract        1.0
fix_the_broken_part/fix_002_fillet          1.0
fix_the_broken_part/fix_003_wall_red        1.0
fix_the_broken_part/fix_004_hole_pattern    1.0
fix_the_broken_part/fix_005_invalid_shell   1.0
assemble_and_clear/asm_001_thrust_chamber   1.0
assemble_and_clear/asm_002_lid_on_base      1.0
assemble_and_clear/asm_003_bolted_joint     1.0
assemble_and_clear/asm_004_truss_node       1.0
assemble_and_clear/asm_005_rod_and_piston   1.0
```

The five `asm` references report `interference` as
`{"checked": n, "pairs": [], "skipped_mesh": []}` with `n` = 3, 2, 3, 4, 5 —
a measured clean pair list over real instances, not a short circuit.

**The rubric discriminates — every starter, scored under its own task:**

```
fix_001_contract     0.0       built 0.0 (status "ok", not "error") — the script raises
fix_002_fillet       0.0       built 0.0 (status "ok") — OCCT refuses the fillet
fix_003_wall_red     0.778998  specs 0.6667 [shell_wall] · geometry 0.5267 · metrics 0.6667
fix_004_hole_pattern 0.782684  specs 0.5    [anchor_slots, plate_mass] · geometry 0.9401 · metrics 0.6667
fix_005_invalid_shell 0.504167 valid 0.0 · specs 0.25 [valid, duct_wall, swept_volume] · metrics 0.6667
asm_001_thrust_chamber 0.25    specs 0.0 · interference 0.0 (checked 0, "no_pairs")
asm_002_lid_on_base    0.25    same shape
asm_003_bolted_joint   0.25    same shape
asm_004_truss_node     0.25    same shape
asm_005_rod_and_piston 0.25    same shape
```

`fix_003` and `fix_004` are the least discriminating of the ten at ~0.78, and
the reason is structural rather than a loose rubric: the `fix` weights hand
`built` + `valid` = 0.40 to any starter that builds into a valid solid, which
is exactly what "valid but wrong" means. Both are under the 0.95 bar with room.

`uv run pytest -q tests/test_bench_tasks.py tests/test_bench_tasks_fix_asm.py`
→ **29 passed in 0.27s**; `load_tasks()` now returns **25 tasks** with zero
problems, `fast` on `fix_001_contract` and `asm_002_lid_on_base` (one per
category, the quickest in each).

`make test` — <orchestrator fills>

**Follow-ups / known limits.**

1. An `asm` rubric is built from *minimum* clearances, so a candidate that
   places every instance far apart satisfies it. The prompts state the seating
   in words and a reviewer reads them, but the scorer does not penalise it.
   `check_stackup` cannot close this — it measures worst-case tolerance
   accumulation along a mate chain, not the nominal distance, and these
   instances are placed rather than mated.
2. `fix_005`'s IoU finding is a product observation worth raising separately:
   a swept (pipe) surface exported to STEP and re-imported no longer booleans
   against the shape it came from. It is not specific to the bench.
3. No OCCT recipe was found in this build that makes an `offset`/shell produce
   an *invalid* solid — it either succeeds cleanly or raises
   (`RuntimeError: offset Error…`), which would have duplicated `fix_002`.
   The self-intersecting sweep is the mechanism that delivers §7.3's stated
   contract for `fix_005` (builds, `check_valid` red).
