# 0264 — PRD-024: nine bench tasks (model_from_drawing 2–5, modify_to_spec 1–5) and `author.py drawing`

- **Commit:** pending
- **Date:** 2026-08-19
- **Author:** Claude (Task 8)

## Summary

Authors the four remaining `model_from_drawing` tasks and all five
`modify_to_spec` tasks — the shipped roster goes from 1 task to **10** — and
adds the `drawing` subcommand to the authoring helper, so a derived task's SVG
asset is rendered by the product's own `generate_drawing` path rather than
drawn by hand. Every reference scores exactly **1.0** (AC1) and every
`modify_to_spec` starter scores well under 0.95, which is what makes those
rubrics measurements rather than formalities.

## Changes

### `agentcad/bench/author.py` — the `drawing` subcommand

- `render_drawing(task_dir, part_id, *, service, views=None, out=None) -> Path`
  stages `reference/project` through the existing `_stage_reference` copy (the
  bundle is never opened in place — a build writes `.cache/` and the confined
  worker cannot write into the repo anyway), builds the tool registry and calls
  `generate_drawing` with `format="svg"` and `views=("top", "front", "right")`.
  `ToolRegistry.call` answers a refusal as an `{"error": …}` **payload** rather
  than by raising, so the result is checked before anything is copied; a
  `part_id` outside `target.parts` is refused up front.
- `compact_svg` / `_compact_path` — an **iterative** Douglas-Peucker at
  `PATH_EPSILON = 0.002` SVG units, run on the way out.
  `handlers/drawing._edge_svg` discretizes *every* non-circular edge, a
  dead-straight 90 mm line included, into up to 256 points; the raw three-view
  sheets were **156 KB** (angle bracket) and **226 KB** (flange), and an asset
  is attached to the prompt as text *verbatim* (design §8.4), so those bytes
  are the agent's context window. The epsilon is twice the file's own printed
  resolution (three decimals), so the sheet renders identically and the task is
  neither easier nor harder — only cheaper: **17.5 KB** and **28.9 KB** after,
  an 8.9x and 7.8x reduction. Nothing but `d="M … L …"` attributes is touched.
- `main` gains `drawing` with `--part` (required), `--views` and `--out`.

### Nine new task bundles

`model_from_drawing` (weights are the §7.6 category default
0.15/0.10/0.10/0.50/0.00/0.15; `budgets` 600 s / 24 tool calls; `sets: ["core"]`):

| id | source | reference | datum |
|---|---|---|---|
| `mfd_002_angle_bracket` | derived `construction/angle_bracket` (defaults) | 90x90 L, 80 wide, 10 thick, R6 inner fillet, 2xØ14 per leg on a 56 gauge at 53 from the opposite face | inside corner at the origin; horizontal leg +X, vertical leg +Z, 80 mm width along **-Y** |
| `mfd_003_head_flange` | derived `rocketry/flange` (defaults, SPECS stripped) | Ø140 ring, Ø87 bore, 14 thick, 8xØ9 on a Ø118 circle, chamfered | bottom face on Z = 0, bore axis = Z, centred |
| `mfd_004_shaft_collar` | authored (revolve + cut) | Ø40/Ø20 x 15 collar, 3 mm clamp slit through the +X wall, Ø5 pinch hole along Y at X = 15 | bottom face on Z = 0, bore axis = Z |
| `mfd_005_vee_block` | authored | 60x60x40 block, 90° vee 15 deep the full length along X, 2xØ8 through along Y at X = ±20, Z = 12 | base on Z = 0, centred in X and Y |

`mfd_002`/`mfd_003` carry generated drawings; `mfd_004`/`mfd_005` carry
hand-authored three-view SVGs (7.4 KB and 7.7 KB).

`modify_to_spec` (weights are the §7.6 default 0.10/0.05/0.40/0.30/0.00/0.15
except `mts_005`; each has a `starter/` at the shipped parameters and a
`reference/project/` that is the **same script** at the target parameters):

| id | source | starter → reference | what the prompt asks |
|---|---|---|---|
| `mts_001_thin_the_nozzle` | `rocketry/nozzle` (SPECS stripped) | `wall` 3.0 → **2.5** | under 900 g without breaching the measured 0.8 mm wall floor |
| `mts_002_bigger_pcb` | `prototyping/enclosure_base` | 100x60 → **140x90** | a 134x84 cavity at wall 2.5, in whole 10 mm steps |
| `mts_003_gusset_pattern` | `construction/gusset_plate` | hole 18 / pitch 45 / 2 rows / edge 30 → **22 / 60 / 4 / 33** | 4 rows at 60 pitch, Ø22, end distance exactly 1.5·d |
| `mts_004_lighter_flywheel` | `engine/flywheel` | thickness 22 → **19** | ≤ 4.2 kg, Ø200 OD and the 6-bolt Ø56 circle kept |
| `mts_005_m10_clamp` | `fasteners/{clamp_plate,tapped_plate}` | 40x40x8 / Ø9 → **64x64x10 / Ø11** | M10 clearance, still clear of the tapped plate |

- **`mts_001`'s reference is `wall = 2.5`, not the plan's 2.0.** Measured:
  wall 3.0 → 1078.08 g and a 1.020 mm grid-4 wall reading; 2.5 → 892.08 g /
  0.867 mm; **2.0 → 708.61 g / 0.707 mm, which is red against the example's own
  0.8 mm ENG-014 floor.** The task is only interesting because the cheap answer
  breaks a spec, so 2.5 is the unique 0.5 mm step that satisfies both.
- **`mts_004`'s reference is `thickness = 19`, not 18.** thickness 20 → 4218.02 g
  is over the 4200 g budget and 19 → 3984.97 g is under it, so 19 is the
  thickest whole millimetre that meets the budget, which is what the prompt asks
  for (inertia is the point of a flywheel).
- **`mts_005` is the one v1 weight override**, argued in an HTML comment at the
  top of its `prompt.md` as the checklist requires: `interference` 0.10 taken
  out of `geometry` (0.30 → 0.20), because its project is an assembly and "still
  stacks without interfering" is half the requirement. Its bundle deliberately
  omits `cap_screw`: the screw head sat on an 8 mm plate, and a 10 mm plate
  would swallow it — the reference would fail its own interference check.
- Every derived bundle **copies the example script in** with a provenance header
  and strips any `SPECS` the example shipped (`nozzle`, `flange`); the runner
  registers no examples, so a run can never read the answer.
- `sets`: `mfd_001` and `mts_002_bigger_pcb` carry `"fast"` — one per category.
- `reference/metrics.json` was seeded by `author metrics` and then **hand-tightened**
  to five windows per task (three bbox extents, mass at ±2%, `n_solids`
  exact), each checked against the measured reference value and against the
  value the wrong answer produces. Two windows are load-bearing rather than
  decorative: `mfd_004`'s `slit_opens_the_rim` is `bbox_x_mm ∈ [39.85, 39.99]`,
  because `sqrt(20² - 1.5²) = 19.9437` is what a real 3 mm slit through a Ø40
  rim measures and an unsplit collar reads 40.0; `mts_001`'s
  `barrel_outside_diameter` pins `wall` to 2.5 from the outside.

### `tests/test_bench_author.py` (new)

Twelve tests, all on `tmp_path` copies — nothing writes into `benchmarks/`:
`compact_svg` collapses a 200-point straight run to its two endpoints, keeps a
real R40 arc within `PATH_EPSILON` at every original vertex, touches no
`<circle>`/`<text>`/`<line>`, leaves a two-point path alone and survives a
4000-point monotone staircase (the recursion the iterative implementation
exists for); `render_drawing` writes a three-view sheet with no `ISO` view and
no path over 64 points, refuses a part outside `target.parts`, and honours
`--out`/`--views`; plus two roster invariants — every shipped SVG asset is
under 40 KB, and every derived task's reference scripts live inside the bundle.

## Files

- `agentcad/bench/author.py` — `DEFAULT_VIEWS`, `PATH_EPSILON`, `_PATH_RE`,
  `_compact_path`, `compact_svg`, `render_drawing`; `main` gains `drawing`,
  `--part`, `--views`, `--out`.
- `benchmarks/tasks/model_from_drawing/mfd_00{2,3,4,5}_*/` — nine files each:
  `task.json`, `prompt.md`, `assets/drawing.svg`, `reference/project/{project.json,parts/*.py}`,
  `reference/steps/*.step`, `reference/metrics.json`, `specs/parts/*.py`.
- `benchmarks/tasks/modify_to_spec/mts_00{1,2,3,4,5}_*/` — the same plus
  `starter/{project.json,parts/*.py}` and no `assets/`.
- `tests/test_bench_author.py` — new.

## Proof

AC1, every shipped reference (`uv run agentcad bench score <bundle>/reference/project --task <id> --json`):

```
model_from_drawing/mfd_001_spacer_plate 1.0
model_from_drawing/mfd_002_angle_bracket 1.0
model_from_drawing/mfd_003_head_flange 1.0
model_from_drawing/mfd_004_shaft_collar 1.0
model_from_drawing/mfd_005_vee_block 1.0
modify_to_spec/mts_001_thin_the_nozzle 1.0
modify_to_spec/mts_002_bigger_pcb 1.0
modify_to_spec/mts_003_gusset_pattern 1.0
modify_to_spec/mts_004_lighter_flywheel 1.0
modify_to_spec/mts_005_m10_clamp 1.0
```

The rubric discriminates — every `modify_to_spec` **starter**, scored under its
own task:

```
mts_001_thin_the_nozzle   0.758196  specs 0.75 (mass_budget red) · metrics 0.4 · iou 0.8273
mts_002_bigger_pcb        0.460772  specs 0.50 (cavity_length, cavity_width) · metrics 0.4 · iou 0.1692
mts_003_gusset_pattern    0.496679  specs 0.50 (chord_reach, diagonal_reach) · metrics 0.4 · iou 0.2889
mts_004_lighter_flywheel  0.795222  specs 0.75 (mass_budget red) · metrics 0.6 · iou 0.8507
mts_005_m10_clamp         0.539465  specs 0.50 (footprint, plate_thickness) · metrics 0.2 · iou 0.2973
```

Targeted suites:

```
uv run pytest -q tests/test_bench_tasks.py tests/test_bench_author.py \
    tests/test_bench_report.py tests/test_bench_kernel_iou.py
77 passed in 3.65s
```

`make test` — <orchestrator fills>

## Notes

- **The generated drawings dimension only the overall extents plus a hole
  callout.** `generate_drawing` detects those and nothing else, so `prompt.md`
  carries the full dimension set in words for every task — exactly as the seed
  does. The SVG is corroboration, not the specification; a reviewer must read
  the prompt against `frame.datum` and the reference script, and the drawing
  against both.
- `mts_004`'s reference STEP is **860 KB** — the flywheel's 36 ring-gear teeth
  make it 207 faces and 590 edges. That is above the design's "20-200 KB per
  part" estimate; the ten shipped datums total about 1.6 MB, still inside "25
  tasks are a few MB".
- `compact_svg` is deliberately in `author.py` and not in
  `kernel/handlers/drawing.py`: changing the handler would change every drawing
  the product emits, which is a product decision and not this task's. The
  underlying finding — that a 90 mm straight line ships as 256 points — is worth
  raising separately.
- `mts_003`'s starter parameters differ from the script's own defaults on
  purpose (manifest `pitch` 45 / `n_rows` 2 vs script defaults 50 / 3), and the
  prompt says so: the agent has to change the **stored** parameters, not the
  script's defaults.
- No `check_wall` in `mts_002` or `mts_004`: the grid-4 sampler reports
  0.029 mm on the vented, filleted enclosure shell (a sampling artefact at a
  slot corner) and a flat 4.0 mm on the flywheel at every thickness in range.
  A floor drawn under either would be a row that can neither fail nor
  discriminate; both rubrics say so in a comment.
