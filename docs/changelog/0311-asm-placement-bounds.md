# 0311 — `assemble_and_clear` grades placement: two-sided clearance windows in all five rubrics

- **Commit:** pending
- **Date:** 2026-08-23
- **Author:** Claude (Opus 5)

## Summary
`bench-v1`'s assembly category shipped with a disclosed hole: every rubric row
was a *floor*, so a candidate that created the instances the prompt names and
parked them 500 mm apart scored **1.0** — full marks on `specs` and on
`interference` alike. Changelog 0310 landed `check_clearance(…, max_mm=…)`;
this change spends it. Every seating relationship the five prompts state in
words is now graded as a two-sided window, derived from the gap **measured** on
each reference placement, and the prompts state the ceilings as graded. The
park-them-apart cheat now scores 0.7375 / 0.825 / 0.7375 / 0.708333 / 0.7 on
`asm_001…005`, and moving **any single** instance 500 mm off its seat reds at
least two rows in every bundle. All five references still score exactly 1.0 and
all five starters still score 0.25, so nothing about the difficulty of doing
the task correctly moved. `task_set` stays `bench-v1` (no results are
published, so the shipped rubrics are retuned in place) and every `weights`
block is byte-unchanged.

## How each bound was derived
Floors are as shipped. Ceilings are the measured gap taken to roughly **twice**
its value — the mirror of how the floors were set at roughly half — except
where the prompt already names an allowance (`asm_001`'s 0.5 mm gasket
allowance) or where the measurement is a hard modelled number a candidate might
reasonably round (`asm_005`'s 0.05 mm crush height gets 3×, so 0.1 mm passes).
Every measured value below came from the spec runner over the reference
placement, and every "what the ceiling actually reds" claim from a measured
perturbation of it. The per-row table is repeated in each bundle's `prompt.md`
reviewer comment (stripped from the prompt the agent sees) and in each
`specs/project.py` docstring.

## Changes
### `asm_001_thrust_chamber` — 3 pairs, all three bounded
- `flange_bore_gap` (`flange_1`–`nozzle_1`), measured **0.500 mm** → `[0.3,
  1.0]`. This is the *radial* bore gap and is blind to the flange's axial
  position: a flange dropped 3 mm still measures 0.500 mm here.
- `injector_gasket_gap` (`injector_plate_1`–`nozzle_1`), measured **0.200 mm**
  → `[0.15, 0.5]`, the ceiling being INT-003's own stated 0.2–0.5 mm allowance.
- **New** `head_face_stack_gap` (`flange_1`–`injector_plate_1`), measured
  **0.400 mm** (0.2 below + 0.2 above the head face) → `[0.25, 1.0]`. This is
  the row that pins the flange's axial placement; the dropped flange above
  measures 3.400 mm here and reds.

### `asm_002_lid_on_base` — 1 pair, bounded; the ceiling is honest about what it can see
- `seat_gap` (`lid_1`–`base_1`), measured **0.100 mm** → `[0.05, 0.2]`.
- Measured lifts: 0.150 mm at Z = 30.5, 31.1 and 32.1, 0.180 mm at Z = 33.1,
  2.105 mm at Z = 35.1. The closest approach **saturates** at the lid lip's own
  0.15 mm radial clearance inside the cavity (`enclosure_lid.LIP_CLEARANCE`),
  so 0.2 mm reds a lid whose lip has left the cavity and every parked lid, and
  passes a lid floating within its lip engagement. A 0.14 mm ceiling would
  catch that too and was **rejected on authoring tolerance, not measurement**:
  the reading is exact and repeatable (0.10000000000000142 mm), but 0.14 mm
  leaves an agent only 0.04 mm around a seat height the prompt states as
  0.1 mm, so a candidate that reasons its way to a 0.15 mm seat reds a
  placement that is substantially right. Two instances give one pair, so this
  row is the whole of the placement grade here — stated in the rubric
  docstring, and its weight is called out in `docs/bench.md` (0.175 of the task
  total, about 0.035 of the category mean, under the gate's 0.05 epsilon).

### `asm_003_bolted_joint` — the awkward one: two pairs are *supposed* to touch
- **New** `clamp_seat` (`clamp_plate_1`–`tapped_plate_1`) and **new**
  `head_seat` (`cap_screw_1`–`clamp_plate_1`), both measured **0.000 mm**.
  `check_clearance` requires `min_mm > 0`, so both carry `TOUCHING = 1e-12` and
  a ceiling of **0.5 mm**. The worker's test is
  `distance >= min_mm - _slack(min_mm)` with `_slack(x) = max(1e-9, |x|·1e-9)`,
  so a floor cannot fail for **any** `min_mm` at or below the absolute slack
  floor of `1e-9`: that is a family, and `1e-9` is its **largest** member, not
  its smallest (`_positive` admits any positive float). Picking the boundary
  value would make the rows depend on the comparison landing on `0.0 >= 0.0`
  exactly, so the constant sits three orders inside the family. They are
  ceilings with a floor that cannot fail, declared that way on purpose: what
  these two pairs state is *how close*, never *how far*, and material sharing
  space stays `no_interference`'s row. Without them the clamp plate's placement
  was graded by nothing at all.
- `thread_clearance` (`cap_screw_1`–`tapped_plate_1`), measured **1.177 mm** →
  `[0.5, 2.0]`. Measured perturbations: bottomed 2 mm down → 0.000 mm (floor);
  8 mm up → 3.222 mm (ceiling); **1 mm up → still 1.177 mm**, because that
  approach is radial inside the counterbore. So this row cannot grade the
  screw's seating depth — `head_seat` does (1.000 mm, red, for that same 1 mm
  lift), which is the second reason it exists. The prompt says so in as many
  words — the graded distance there is the screw's thread flank against the
  4.5 mm counterbore wall (an M8 root radius of ≈ 3.32 mm gives the 1.18 mm),
  not the tip-to-hole-bottom depth — so a candidate reasoning about tip depth
  does not mis-model the joint to satisfy it.

### `asm_004_truss_node` — five of six pairs bounded
- `gusset_seat` (`gusset_1`–`base_plate_1`), measured **2.000 mm** → `[1.0,
  3.0]`. The approach is to the plate's 1 mm-deep column-footprint recess, not
  its top face, and it tracks the gusset's lower edge one for one (a gusset
  1 mm higher measures 3.000 mm), so the ceiling reds an edge above ≈ Z = 22.
- **New** `left_bracket_seat` / `right_bracket_seat` (`bracket_*`–
  `base_plate_1`), measured **0.500 mm** each → `[0.25, 1.0]`. The prompt
  already stated that 0.5 mm float and nothing measured it.
- `left_web_gap` / `right_web_gap` (`bracket_*`–`gusset_1`), measured
  **0.500 mm** each → `[0.25, 1.0]`.
- **Left unbounded:** `bracket_left`–`bracket_right`, measured 11.000 mm. The
  brackets neither seat on nor weld to each other and the prompt states no gap
  between them, so a window there would be a rubric invention; each bracket is
  already two-sidedly graded against the plate and the web.

### `asm_005_rod_and_piston` — six of ten pairs bounded
- `pin_bore_gap` (`wrist_pin_1`–`rod_1`), measured **0.125 mm** → `[0.05,
  0.25]`; `big_end_joint` (`rod_cap_1`–`rod_1`), measured **0.050 mm** →
  `[0.02, 0.15]` (3× — the crush height is a modelled 0.05 mm and a candidate
  who rounds it to 0.1 mm still passes).
- **New** `pin_boss_gap` (`wrist_pin_1`–`piston_1`), measured **0.100 mm** →
  `[0.05, 0.2]`; **new** `bolt_body_gap` (`rod_bolts_1`–`rod_1`), measured
  **0.550 mm** → `[0.3, 1.1]`; **new** `bolt_cap_gap` (`rod_bolts_1`–
  `rod_cap_1`), measured **0.200 mm** → `[0.1, 0.4]`; **new** `small_end_gap`
  (`piston_1`–`rod_1`), measured **2.000 mm** → `[1.0, 3.0]`.
- `small_end_gap` is a lateral approach (piston skirt to rod blade): a piston
  lifted 1 mm still measures 2.000 mm, so it grades "the piston is over the
  small end", not its height — the height is `pin_boss_gap`'s, which measures
  0.000 mm and reds for that same lifted piston. Both facts are in the rubric
  docstring rather than left for a reader to discover.
- **Left unbounded:** cap–piston (88.050 mm), cap–pin (102.898 mm),
  bolts–piston (78.085 mm), bolts–pin (93.176 mm) — opposite ends of the rod,
  no fit relates them, and each of those instances is already graded by a row
  that names it.

### Prompts and docs
- All five `prompt.md` bodies gain an explicit **graded-window** section
  stating each ceiling in words (the fairness bar: every graded row is stated
  in the prompt), and a reviewer-only HTML comment carrying the derivation
  table and the perturbation measurements. `prompt_text` strips those comments
  before the agent sees them, so no derivation reaches the model.
- `docs/bench.md`'s disclosed-limitation bullet is rewritten: placement is
  graded, the cheat's new scores are quoted, and the residual is named — a pair
  distance cannot see a part slid along a face it stays parallel to, spun about
  a symmetric axis, or lifted inside a saturating running clearance, and a pair
  that is supposed to touch cannot carry a real floor.

## Files
- `benchmarks/tasks/assemble_and_clear/asm_00{1,2,3,4,5}_*/specs/project.py` —
  the windows, and a docstring per bundle recording the measured value behind
  every bound, which side of each window bites, and what was left unbounded and
  why.
- `benchmarks/tasks/assemble_and_clear/asm_00{1,2,3,4,5}_*/prompt.md` — the
  graded windows in words plus the reviewer comment.
- `docs/bench.md` — **two** hunks under "What this does not guarantee": the
  `assemble_and_clear` bullet (this entry's work) and the
  *optimisation-category* bullet, which is the **held-over** rewrite belonging
  to changelog 0312's task. That rewrite was authored in the previous task but
  left uncommitted so a concurrent editor of the same file would not be
  clobbered; it landed in this commit because this commit is the next one to
  touch `docs/bench.md`. The commit message says so, and 0312's own entry
  carries the matching note. Nothing in the opt hunk is this task's design —
  read it against 0312.
- No `task.json` changed: `task_set` stays `bench-v1` and every `weights` block
  is byte-identical (`tests/test_bench_tasks.py`'s category-weight test still
  pins them).

## Notes
**Why the references did not have to move.** Every ceiling is derived from the
reference's own measurement, so AC1 ("the reference scores 1.0") is preserved
by construction rather than by luck — and it was re-proved per bundle, below.
The starters place no instances, so their clearance rows are `error` rows
(instance not in the assembly) and their `interference_free` row is the
`no_instances` skip the scorer counts as a failure: `specs` was 0 before and is
0 now, which is why five more rows changed no starter score.

**`asm_003`'s `TOUCHING` floor is a coupling, and it is declared as one.** It
depends on `core.specs._slack` keeping an absolute floor of at least `1e-12`
and on `BRepExtrema_DistShapeShape` returning exactly `0.0` for coincident
faces. If either goes, the two seated rows go red **on the reference itself**.
The tripwire is **manual**, and that is the part worth knowing: no test pins
the assembly references at 1.0 — `tests/test_bench_scoring.py`'s
reference-scores-one test covers the seed task (`mfd_001`) only — so a `_slack`
change surfaces as a red reference in a scored run (`agentcad bench score
benchmarks/tasks/assemble_and_clear/asm_003_bolted_joint/reference/project
--task assemble_and_clear/asm_003_bolted_joint`) and **not** in `make test`.
The per-bundle proof numbers below are what such a run is checked against. The
alternative was leaving the clamp plate's placement ungraded — a documented
full-marks cheat in the one bundle whose pairs are designed to touch — and that
was judged worse than a documented, measured coupling.

**Nothing else was touched.** No `agentcad/**` change, no test change
(`tests/test_bench_tasks_fix_asm.py`'s four asm contract tests — starter places
no instances, reference places ≥ 2, the rubric names only placed instances, the
prompt names every instance the rubric uses — pass unchanged, and the last one
is what keeps the new rows fair), and `AGENTS.md` does not name this limitation
so it is not edited. The one thing in this commit that is **not** this task's
work is `docs/bench.md`'s *optimisation* bullet, carried over as described
under Files; its design and its numbers belong to changelog 0312.

## Verification
Per bundle, scored with `uv run agentcad bench score <project> --task
assemble_and_clear/<id>` (`--json`, `total` / `specs`):

| bundle | reference | starter | park **all** 500 mm apart | worst single instance moved 500 mm |
|---|---|---|---|---|
| `asm_001_thrust_chamber` | 1.0 (specs 1.0, 4 rows) | 0.25 (specs 0.0) | **0.7375** (specs 0.25; 3 rows red) | 0.825 (specs 0.5; 2 rows red, any of the three) |
| `asm_002_lid_on_base` | 1.0 (specs 1.0, 2 rows) | 0.25 (specs 0.0) | **0.825** (specs 0.5; `seat_gap` red) | 0.825 (specs 0.5; either instance) |
| `asm_003_bolted_joint` | 1.0 (specs 1.0, 4 rows) | 0.25 (specs 0.0) | **0.7375** (specs 0.25; 3 rows red) | 0.825 (specs 0.5; 2 rows red, any of the three) |
| `asm_004_truss_node` | 1.0 (specs 1.0, 6 rows) | 0.25 (specs 0.0) | **0.708333** (specs 0.166667; 5 rows red) | 0.883333 (specs 0.666667; a bracket, 2 rows red) |
| `asm_005_rod_and_piston` | 1.0 (specs 1.0, 7 rows) | 0.25 (specs 0.0) | **0.7** (specs 0.142857; 6 rows red) | 0.9 (specs 0.714286; cap, bolts, piston or pin — 2 rows red each; `rod_1` reds 4) |

Before this change every cell in the last two columns was **1.0000**. The
`interference` subscore stays 1.0 in all of them, correctly: parked parts do
not overlap, which is exactly why the floors alone could not grade placement.

Which rows red per bundle for the park-them-all case:
`asm_001` `flange_bore_gap` + `head_face_stack_gap` + `injector_gasket_gap` ·
`asm_002` `seat_gap` · `asm_003` `clamp_seat` + `head_seat` +
`thread_clearance` · `asm_004` `gusset_seat` + `left_bracket_seat` +
`left_web_gap` + `right_bracket_seat` + `right_web_gap` · `asm_005`
`big_end_joint` + `bolt_body_gap` + `bolt_cap_gap` + `pin_bore_gap` +
`pin_boss_gap` + `small_end_gap`.

```
$ uv run pytest -q tests/test_bench_tasks.py tests/test_bench_scoring.py \
      tests/test_bench_tasks_fix_asm.py
81 passed in 5.64s

$ uv run pytest -q tests/test_bench_author.py tests/test_bench_cli.py \
      tests/test_bench_runner.py tests/test_bench_report.py \
      tests/test_bench_publish.py tests/test_prd024_acceptance.py
190 passed in 103.29s (0:01:43)

$ make test — <orchestrator fills>
```
