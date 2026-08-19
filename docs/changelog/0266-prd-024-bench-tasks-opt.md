# 0266 — PRD-024: the five `optimize_under_constraints` bench tasks

- **Commit:** pending
- **Date:** 2026-08-19
- **Author:** Claude (Task 10 of the AgentCAD-Bench plan)

## Summary

Authors the whole `optimize_under_constraints` category — five task bundles
under `benchmarks/tasks/optimize_under_constraints/`. No code changes: these
are data the existing loader, scorer and `agentcad bench score` already read.
Every reference scores exactly **1.0**, every starter well under 0.95, and a
hand-made half-way project lands strictly between the two on all five, which is
the evidence that an optimisation objective is graded here rather than a cliff.

## Changes

- **`opt_001_lightest_bracket`** (derived `construction/angle_bracket`, and the
  category's one `fast`-set task). Objective: minimise `mass_g`. Reference
  `thk` 6.0 → **631.4818 g / 80443.5403 mm³**; the objective ships in *both*
  units at the same ratios (`objective_mass` ≤ 663.06 g and `objective_volume`
  ≤ 84465.72 mm³, plus relaxed rungs at 1.20). Constraints:
  `check_wall(min_mm=4.0, grid=4)`, the two Ø14 holes per leg counted as
  circular edges, the 90 × 80 × 90 mm footprint held from both sides, and a
  `material_density` row (A36 steel, 0.00785 g/mm³ ± 1%).
- **`opt_002_stiffest_gusset`** (derived `construction/gusset_plate`).
  Objective: maximise `bbox_z_mm` (plate thickness). Reference — outline
  trimmed to `chord_w` 50 / `diag_w` 40 / `edge_dist` 27, then thickened until
  the budget binds — **17.0 mm at 2917.0270 g** (18 mm reads 3088.6168 g and
  breaks the 3000 g `check_mass` row). Constraints: the mass budget, its
  density-invariant twin `check_volume(max_mm3=382165.6)` (= 3000 g / 0.00785),
  a `material_density` row, a 234.5 × 142.2 mm reach floor (the code end
  distance restated as something a bounding box can see), and a
  273.2 × 176.8 × 25.2 mm envelope.
- **`opt_003_thinnest_lid`** (derived `prototyping/enclosure_lid`). Objective:
  minimise `volume_mm3`. Reference `lid_t` 2.0 / `lip_h` 1.5 / `lip_t` 1.6 →
  **12207.5183 mm³**. Constraints: the 100 × 60 mm footprint, the lip still
  1.5 mm below the plate underside, the plate still 2.0 mm thick, the four Ø3
  screw holes counted as circular edges.
- **`opt_004_most_bolts`** (derived `rocketry/flange`, its `SPECS` block and
  `check_wall` import stripped). Objective: maximise `n_faces`, which on this
  part *is* the bolt count — measured across five builds, `n_faces = 8 +
  n_bolts` exactly. Reference `n_bolts` 24 → **32 faces**. Constraints: the
  flange's own INT-003 ligament at a 3 mm floor, the Ø87 bore counted as
  circular edges, Ø140 × 14 held from both sides.
- **`opt_005_shortest_screw`** (derived `fasteners`, three parts). Objective:
  minimise the screw's `bbox_z_mm` (head + shank). Reference `length` 11.0 →
  **19.0 mm**. Constraints live in `specs/project.py`
  (`check_interference_free` + `check_clearance("cap_screw_1",
  "tapped_plate_1", 1.0)`) plus a part-scope reach row.
- Every bundle carries the category weight row **0.10 / 0.05 / 0.45 / 0.00 /
  0.00 / 0.40** (design §7.6) with no override, `budgets` `wall_s` 900 /
  `turns` 30, `sets` `["core"]` (`opt_001` adds `"fast"`), and
  `authored_against` `0.1.0`.

## Files

- `benchmarks/tasks/optimize_under_constraints/opt_00{1..5}_*/` — `task.json`,
  `prompt.md`, `starter/` (a complete project at the example's parameters),
  `reference/project/` (the optimised solution), `reference/metrics.json`,
  `specs/parts/<part>.py` (and `specs/project.py` for `opt_005`).
- `docs/changelog/0266-prd-024-bench-tasks-opt.md` — this entry.

## Notes

**Why `geometry` is `not_applicable` for the whole category.** An optimisation
has no unique correct shape: two different answers can both be right, and one
can be *better* than the reference. Scoring IoU against a reference solid would
turn "find the lightest bracket that still bolts up" into "reproduce this
bracket", and would punish a candidate that beat the reference. So the
`geometry` weight is 0.00, which is the only way the design lets a subscore
read `not_applicable` — the task declares it, never a run (design D5). The
loader only demands `reference.steps` entries while the geometry weight is
above zero, so every bundle ships `"steps": {}` and **no** `reference/steps/`
directory. `"steps"` cannot be *omitted*: `task_problems` requires the key to
be an object ("reference.steps must be an object of part id -> STEP path"), so
an empty object is the honest spelling and the task brief's "omit it entirely"
is not something the loader accepts. Verified: `task_problems` returns `[]` for
all five.

**How each objective window was derived, and why there are two rungs.** The
objective is a one-sided window on the reference's *measured* value with a
stated slack (design §7.5) — `max = 1.05 × measured` for a minimisation,
`min = measured / 1.05` for a maximisation — so the reference still scores 1.0
and a better-than-reference answer passes. Each task ships that window **plus a
second, looser rung** at 1.20 (`objective_*_relaxed`). The reason is
mechanical: `metrics` is scored as the fraction of windows satisfied, so a
single objective window makes the whole objective pass/fail — a candidate that
cut the bracket's mass by a third would score exactly what a candidate that
changed nothing scores. The ladder is what makes the half-way proof possible,
and every rung's arithmetic is written out in an HTML comment at the top of the
task's `prompt.md` so a reviewer can reproduce it.

**Proof (per task: reference / starter / half-way).**

| task | reference | starter | half-way |
|---|---|---|---|
| `opt_001_lightest_bracket` | **1.0** | 0.8 (`thk` 10, 1024.1152 g) | 0.9 (`thk` 7, 731.5241 g) |
| `opt_002_stiffest_gusset` | **1.0** | 0.84 (t 10, 80/60/30) | 0.92 (t 15, trimmed) |
| `opt_003_thinnest_lid` | **1.0** | 0.84 (3.0/3.0/2.0, 19086.9343 mm³) | 0.92 (2.0/3.0/2.0, 13122.9343 mm³) |
| `opt_004_most_bolts` | **1.0** | 0.866667 (`n_bolts` 8, 16 faces) | 0.933333 (`n_bolts` 21, 29 faces) |
| `opt_005_shortest_screw` | **1.0** | 0.641667 (`length` 20) | 0.933333 (`length` 13) |

Every reference also reports `subscores.geometry.status == "not_applicable"`
and `subscores.interference.status == "not_applicable"`.

The material-swap exploit, scored (fix round 1 — see below):

| project | total |
|---|---|
| `opt_001` starter re-materialled to `al6061`, geometry untouched | **0.825** |
| `opt_002` starter re-materialled to `al6061`, geometry untouched | **0.765** |
| `opt_002` reference re-materialled to `al6061` and taken to `plate_t` 25 | **0.85** | The half-way projects
are scratch artefacts (the starter with the obvious first improvement applied)
and are not shipped; the parameters are recorded in each `prompt.md`'s comment
block so the numbers can be reproduced.

**Three deviations, each argued in the task's own `prompt.md`:**

1. `opt_002`'s design-table constraint `plate_t <= 12` is replaced by the
   parameter's own declared maximum plus the table's other half, the mass
   budget. With a starter at 10 mm and a ceiling at 12 the objective has a
   1.2× dynamic range and no multiplicative window can separate the starter
   from the reference. The trade the task measures — thickness bought by
   trimming the outline — is unchanged.
2. `opt_003` ships **no** `check_wall`. Measured at grid=4 the sampler reports
   **0.200 mm on every variant in range**: it lands on the part's 0.2 mm
   `BOSS_RELIEF` recess, not on a wall, so the design table's
   `check_wall(min_mm=1.6)` would be red on the reference itself. The 1.6 mm
   lip rule is stated in the prompt as a design rule and is worth 228 mm³
   (1.9%) over its whole range — inside the objective's own 5% slack, so it
   cannot move the score. Same class of finding as `mts_002`/`mts_004`.
3. `opt_005`'s starter is `length` 20.0, not the example's shipped 13.0. The
   example's screw is 2 mm off the constrained optimum and passes every
   constraint; a starter that is already the answer measures nothing. 20 mm is
   the wrong state the constraint exists to catch (the shank overlaps the
   tapped thread by 0.00549 mm³ and the clearance reads 0.0 mm), and it is
   inside the parameter's declared 8..40 range. The example's 13.0 is the
   half-way project.

**`opt_005` and the zero `interference` weight.** The category row puts 0.00 on
`interference`, so the assembly requirement is carried by `specs/project.py`,
whose rows the `specs` subscore owns. That is deliberate rather than a
workaround: `interference` measures clean instance *pairs*, while this task
needs a named minimum clearance between two named instances, which only
`check_clearance` states.

**`check_that` predicates run in the kernel worker and may import build123d.**
Four of the five rubrics count a feature directly — Ø14 bolt-hole edges, Ø3
screw-hole edges, the Ø87 bore's edges — instead of trusting a parameter, and
each was proved to *fail* on the obvious wrong answer (`hole_d` 10 reads zero
matching edges; `screw_d` 5 reads zero; `inner_d` 100 reads zero). The
predicate body runs inside the confined worker (`handlers/specs._eval_that`),
which is the only place `OCP`/build123d is importable, and it must return a
real `bool`.

**`test_the_shipped_set_is_five_per_category`** is added to
`tests/test_bench_tasks.py` in fix round 1, now that all 25 bundles are on
disk. It asserts equality — five per `CATEGORIES` entry and 25 in total — and
goes through `load_tasks()` rather than a glob, so a bundle that globs but does
not load fails here too. It was deliberately withheld from the first pass
because `assemble_and_clear` was still being authored in a parallel slice.

**Verification.**

```
uv run agentcad bench score benchmarks/tasks/optimize_under_constraints/<id>/reference/project \
  --task optimize_under_constraints/<id> --json
optimize_under_constraints/opt_001_lightest_bracket 1.0
optimize_under_constraints/opt_002_stiffest_gusset 1.0
optimize_under_constraints/opt_003_thinnest_lid 1.0
optimize_under_constraints/opt_004_most_bolts 1.0
optimize_under_constraints/opt_005_shortest_screw 1.0

uv run pytest tests/test_bench_tasks.py -q   ->  19 passed in 0.21s
load_tasks() -> 20 tasks; fast set = one per shipped category
```

`make test` — 4702 passed, 36 skipped (measured at branch tip 1ae80d1, all slices landed)

---

## Fix round 1 (review of `b33a36c`)

**Critical — the mass-based objectives were defeated by changing the
material.** `opt_001`'s objective rungs are on `mass_g` and `opt_002`'s binding
constraint was `check_mass(max_g=3000)`. `mass_g = volume × density` and the
density comes from `project.json`'s material, which `update_part_script(
material=…)` / `set_project_materials` change in one call; nothing in either
bundle measured it, and `DEFAULT_MATERIAL = "al6061"` makes the swap reachable
by accident on a delete + `create_part`. Measured before the fix: the `opt_001`
starter re-materialled to `al6061` weighs **352.24 g**, clears both mass rungs
with all five spec rows green, and scores **1.0** for a submission that changed
no geometry. Fixed with both halves of the belt-and-braces the reviewer asked
for, because either alone leaves a hole:

- **Density-invariant twins of the measurement.** `opt_001` gains
  `objective_volume` (`max = 1.05 × 80443.5403 = 84465.72 mm³`) and
  `objective_volume_relaxed` (`1.20 × = 96532.25`) beside the two mass rungs —
  same reference, same ratios, a unit no material can move. `opt_002` gains
  `check_volume(max_mm3=382165.6)` (`= 3000 g / 0.00785 g/mm³`) beside
  `check_mass(max_g=3000.0)`; it binds identically on steel (17 mm reads
  371595.7903 mm³ and passes, 18 mm reads 393454.3662 mm³ and is red) and it
  keeps binding when the material changes.
- **A `material_density` spec row in both rubrics** — a `check_that` over
  `metrics["mass_g"] / metrics["volume_mm3"]` against A36 steel's
  0.00785 g/mm³ with a 1% tolerance, guarded against a zero volume.
- **Both prompts name the material** in the opening sentence and list "the
  material stays A36 steel" under the graded constraints; both comment blocks
  carry the derivation and the 352.2 g measurement.

Re-proved (`uv run agentcad bench score … --json`):

```
opt_001  reference 1.0 · starter 0.8   · half-way 0.9   · al6061 starter 0.825
opt_002  reference 1.0 · starter 0.84  · half-way 0.92  · al6061 starter 0.765
                                       · al6061 reference at plate_t 25: 0.85
```

`opt_001`'s starter and half-way moved (0.866667 → 0.8, 0.933333 → 0.9) because
the metrics denominator went from 6 windows to 8; the ordering the task needs —
reference 1.0, starter well under 0.95, half-way strictly between — is
unchanged. The `al6061` starter loses `angle_bracket:material_density` and both
`objective_volume` rungs. The `al6061` gusset at `plate_t` 25 — the exploit the
new rows exist for, which clears *every* objective window — loses
`gusset_plate:material_budget` and `gusset_plate:material_density` and lands at
0.85.

`opt_003` (`volume_mm3`), `opt_004` (`n_faces`) and `opt_005` (`bbox_z_mm`)
were already density-invariant in their objectives and needed no change;
`opt_005`'s two `mass_g` windows are *"this part was not touched"* rows, which
a material swap is supposed to fail.

**Important — `opt_002`'s objective metric departs from §7.5** ("maximise the
throat section area (`check_that` on metrics; window on `volume_mm3`)"; shipped:
a window on `bbox_z_mm`). The `prompt.md` DEVIATION block now names it as
deviation (1) and argues it: the throat section is `chord_w × plate_t`, and
`chord_w` is not recoverable from either argument a `check_that` predicate is
given — the plate's Y extent is the convex hull of the chord band *and* both
diagonal strips, so it moves when the diagonals move, and sectioning the solid
would need a boolean inside a spec predicate, which the kernel's spec tier does
not do. `plate_t` is the other factor, is the one that actually drives buckling
stiffness, and is directly measurable as `bbox_z_mm`. A `volume_mm3` objective
was rejected on a second ground: the table pairs it with `mass_g <= Y`, and on
a single material those are the same axis — a contradiction, not a trade.
`volume_mm3` is still measured, as `material_budget`, in the role that works.
The old `plate_t <= 12` argument is now deviation (2).

**Minors, all taken:**

1. **Unmeasured bullets moved out of "all of them graded".** `opt_001`'s R6
   fillet and `opt_002`'s Ø18 holes / two rows / 45° diagonals now sit under a
   "Not graded, but part of the design:" heading, as `opt_003` already did.
   `opt_003`'s "on the base's boss centres" moved there too — the rubric counts
   the holes, it does not locate them.
2. **`opt_003`'s envelope deviation is now named.** `(100.3, 60.3, 6.3)` vs the
   design's `[100, 60, 10]`: X and Y are the same footprint with 0.3 mm of
   measurement slack (a bbox reading exactly 100.000 against a 100.0 ceiling is
   a row decided by floating point), and Z is tightened because 6.0 mm is the
   thickest lid in the declared parameter range — a 10 mm ceiling could not
   fail any candidate.
3. **`test_the_shipped_set_is_five_per_category` added** to
   `tests/test_bench_tasks.py` (see above).
4. **`opt_005`'s prompt now pins the instance ids** — `tapped_plate_1`,
   `clamp_plate_1`, `cap_screw_1` — because `specs/project.py`'s
   `check_clearance` names two of them, and renaming one is the same as
   deleting the check.

**Fix-round verification.**

```
uv run pytest tests/test_bench_tasks.py -q          ->  20 passed in 0.24s
task_problems([]) for all five bundles; 20 .py files parse;
the six rubric files are the only ones declares_specs accepts;
no check_fem_static, no 'SPECS +=', no __pycache__ under benchmarks/.

reference / starter / half-way, all five:
  opt_001  1.0 / 0.8       / 0.9
  opt_002  1.0 / 0.84      / 0.92
  opt_003  1.0 / 0.84      / 0.92
  opt_004  1.0 / 0.866667  / 0.933333
  opt_005  1.0 / 0.641667  / 0.933333
every reference: geometry and interference == "not_applicable".
```
