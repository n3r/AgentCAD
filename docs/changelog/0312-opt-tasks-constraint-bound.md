# 0312 — `opt_001`/`opt_003`/`opt_004` are constraint-bound: the engineering row binds, not the `PARAMS` range

- **Commit:** pending
- **Date:** 2026-08-23
- **Author:** Claude (Opus 5) with Nikita Fedorov

## Summary

Three of the five `optimize_under_constraints` bundles had their optimum sitting
exactly on the parameter's own declared bound — `opt_001`'s `thk` at `PARAMS`
min 6.0, `opt_003`'s `lid_t`/`lip_h` at their minima, `opt_004`'s `n_bolts` at
max 24 — so the winning play was "read `PARAMS`, type the extreme" and the task
measured slider-reading rather than engineering judgement. `docs/bench.md`
disclosed exactly this under "What this does not guarantee". This retunes all
three so a **declared engineering rubric row** binds strictly inside the range,
which is the shape `opt_002` (mass budget) and `opt_005` (thread clearance)
already had. Every reference still scores exactly 1.0, every starter is still
well under 0.95, and typing the range extreme is now measurably worse than
reasoning.

**`task_set` stays `bench-v1` and no `version` is bumped**, deliberately: no
results are published (`benchmarks/baseline.json` is null and the leaderboard
has no rows), so there is no number in the world that a retune would silently
re-scale. A shipped rubric is retuned in place exactly while that is true; the
moment a score is published this becomes a `bench-v2` change.

## Per task: what binds now, and the proof numbers

All numbers measured through `agentcad bench score` / the real `Scorer` on the
pinned build123d, on this machine, against the bundles as shipped in this diff.

### `opt_001_lightest_bracket` — the 4 mm wall floor binds at `thk` 4.0

`PARAMS["thk"]` min widened **6.0 → 3.0** (both scripts, identically), and the
reference moved from `thk` 6.0 to **`thk` 4.0** — the lightest bracket whose
wall is the 4 mm `check_wall(min_mm=4.0, grid=4)` asks for, a whole millimetre
inside the declared range. Measured wall across the range (grid=4 reads about
0.2 mm over the leg thickness itself):

| `thk` | 3.0 | 3.5 | 3.8 | 3.9 | 4.0 | 4.5 | 6.0 |
|---|---|---|---|---|---|---|---|
| `leg_thickness` | 3.176 ✗ | 3.686 ✗ | 3.992 ✗ | 4.094 ✓ | **4.196 ✓** | 4.706 ✓ | 6.235 ✓ |

Reference measures **427.6291 g / 54475.0446 mm³** (was 631.4818 / 80443.5403),
and the four objective rungs were re-derived from it at the unchanged 1.05/1.20
ratios: 449.01 g / 513.15 g / 57198.80 mm³ / 65370.05 mm³.

| candidate | total |
|---|---|
| reference (`thk` 4.0) | **1.0** |
| starter (`thk` 10.0) | 0.8 |
| half-way (`thk` 4.5) | 0.9 |
| **old range floor (`thk` 6.0)** | **0.8** (all six rubric rows green, all four objective rungs missed) |
| new range floor (`thk` 3.0) | 0.925 (every metric window green, `leg_thickness` red at 3.176) |
| starter re-materialled `al6061` | 0.825 (unchanged; the density twin still binds) |

The R6-fillet claim was re-measured at the new reference thickness: R2 reads
423.3165 g against R6's 427.6291 g — 4.3126 g, 1.0%, still inside the
objective's 5% slack, so the one ungraded rule still cannot move the score.

### `opt_003_thinnest_lid` — the fit floors bind, and the countersink is why

`PARAMS` widened **`lid_t` min 2.0 → 1.0** and **`lip_h` min 1.5 → 0.8** (two
and four layers of a 0.4 mm nozzle — what the script prints), and the reference
is **unchanged** at 2.0 / 1.5 / 1.6, because the rubric's own `plate_thickness`
(2.0 mm above the underside) and `lip_depth` (1.5 mm below it) are the
requirement and they now bind a millimetre inside the range. `reference/metrics.json`
is therefore untouched.

The previous round's finding stands and is why no new `check_wall` was added:
grid=4 reads **0.200 mm on every variant**, landing on the 0.2 mm `BOSS_RELIEF`
recess, so a wall row here can neither pass nor discriminate. What the widened
range exposed instead is a real manufacturing consequence — under a 2 mm plate
the 90° countersink (csk_r 3.0, 1.5 mm deep) **breaks through**, the Ø3
cylindrical hole is gone (48 faces → 44) and the existing `screw_holes` row goes
red with the other two.

| candidate | total |
|---|---|
| reference (2.0 / 1.5 / 1.6) | **1.0** |
| starter (3.0 / 3.0 / 2.0) | 0.84 |
| half-way (2.0 / 3.0 / 2.0) | 0.92 |
| **new range floor (1.0 / 0.8)** | **0.775** — every objective window green, three of six rubric rows red (`plate_thickness`, `lip_depth`, `screw_holes`) |
| 1.5 / 1.2 | 0.775 (same three rows) |

The old range floor **is** the reference here, so the "old cheat scores below
the reference" line reads differently for this task: the extreme moved rather
than the answer, and typing it is now a 0.775 instead of a 1.0. `lip_t` keeps
its declared min of 1.0 on purpose — widening a range that nothing measures
would only widen an ungraded rule.

### `opt_004_most_bolts` — two ligaments bind at 32 bolts on Ø123.5

`PARAMS["n_bolts"]` max widened **24 → 48**, and the reference moved from
24 @ Ø118 to **32 @ Ø123.5** (40 faces). Raising the ceiling alone was **not
enough and would have made the task dishonest**: measured, the grid-4 wall
sampler does not see the neighbour ligament at all — 42 holes on a Ø124 circle
leave 0.27 mm between neighbours and `check_wall(3.0, grid=4)` reports **4.064
and passes**; 41 holes on the shipped Ø118 circle (0.04 mm ligament) scored a
clean 1.0. A finer grid does not fix it either: grid=16 reads **0.022 mm on
every variant, reference included** (it samples the rim chamfer), exactly as the
bundle's own comment warned. So the rubric gained two `check_that` rows:

- **`bolt_spacing`** — the neighbour ligament, measured on the built part's hole
  centres: Ø9 holes 3 mm apart are 12 mm centre-to-centre (row written at 11.95,
  0.05 mm of measurement slack). Centres come from `edge.arc_center`, never
  `edge.center()`: a merged hole survives as a trimmed arc whose centre of mass
  is a point *on* the arc, which would make a merged pattern look well spaced.
- **`bolt_pattern`** — at least four Ø9 holes and `n_faces == 8 + holes`, which
  is what keeps the `n_faces` objective honest now that the ceiling is high
  enough for the proxy to be gamed.

What binds is now a genuine two-sided squeeze: outward by
`bolt_circle_ligament` (measured at n=32: Ø122.5 → 4.154, Ø123.5 → 3.447,
Ø124 → 3.094, Ø124.5 → 2.740 ✗, Ø125 → 2.387 ✗ — the sampler walks out through
the 1.5 mm rim chamfer and reads ~0.4 mm under the nominal rim ligament), and
inward by `bolt_spacing` (a 33rd bolt needs Ø126.05, which the rim row reads at
1.998 and refuses). Objective rungs re-derived from 40 faces at the unchanged
ratios: 38.10 (≥ 31 bolts) and 33.33 (≥ 26 bolts).

| candidate | total |
|---|---|
| reference (32 @ Ø123.5) | **1.0** |
| starter (8 @ Ø118) | 0.866667 |
| half-way (28 @ Ø118) | 0.933333 |
| **old range ceiling (24 @ Ø118)** | **0.866667** — every rubric row green, both rungs missed |
| new range ceiling (48 @ Ø118) | 0.804762 — holes merge into a slot: two solids, `bolt_spacing` + `bolt_pattern` red |
| 33 @ Ø123.5 | 0.935714 (`bolt_spacing` red) |
| 33 @ Ø126.1 | 0.935714 (`bolt_circle_ligament` red at 1.998) |
| 41 holes at **Ø5** @ Ø118 | 0.871429 — 49 faces, and without `bolt_pattern` this was a 1.0 |
| 31 @ Ø120 | 1.0 (a near-optimal answer inside the 5% rung, full credit by design) |

## Changes

- Widened one `PARAMS` range per task in **both** the starter and the reference
  script, keeping the two byte-identical below their header comments (the
  bundle convention: starter and reference differ by stored manifest params,
  never by script edits).
- Moved two reference manifests (`opt_001` `thk` 6.0 → 4.0; `opt_004` `n_bolts`
  24 → 32, `bolt_circle_d` 118.0 → 123.5). `opt_003`'s reference is unchanged.
- Re-derived the objective windows in `opt_001` (four rungs, mass + the
  density-invariant volume twins) and `opt_004` (two `n_faces` rungs) from the
  newly measured references at the unchanged 1.05/1.20 ratios. `opt_003`'s
  `metrics.json` is unchanged.
- Added `bolt_spacing` and `bolt_pattern` to `opt_004`'s rubric and stated both
  in its prompt (fairness bar: an obedient agent cannot fail an unstated check).
- Rewrote each prompt's body to say plainly that the parameter range is not the
  limit, and each `prompt.md` HTML comment + rubric comment with the new
  derivation and its measured provenance. Rationale stays inside the HTML
  comment, which `strip_reviewer_comments` removes before the model sees it.
- Rewrote the `docs/bench.md` "What this does not guarantee" bullet: the
  category is constraint-bound now, carries the authoring rule for new tasks,
  and discloses what actually remains (per-row measurement slack and the 5%
  rung's full credit — neither of which lets a candidate outscore the reference,
  because the objective windows are one-sided).

## Files

- `benchmarks/tasks/optimize_under_constraints/opt_001_lightest_bracket/{starter,reference/project}/parts/angle_bracket.py` — `thk` min 6.0 → 3.0
- `.../opt_001_lightest_bracket/reference/project/project.json` — `thk` 6.0 → 4.0
- `.../opt_001_lightest_bracket/reference/metrics.json` — four objective rungs re-derived
- `.../opt_001_lightest_bracket/prompt.md` — "what binds" paragraph, new provenance block
- `.../opt_001_lightest_bracket/specs/parts/angle_bracket.py` — `leg_thickness` comment inverted (the row now binds inside the range)
- `.../opt_003_thinnest_lid/{starter,reference/project}/parts/enclosure_lid.py` — `lid_t` min 2.0 → 1.0, `lip_h` min 1.5 → 0.8
- `.../opt_003_thinnest_lid/prompt.md` — "what binds", the countersink argument, deviation (3)
- `.../opt_003_thinnest_lid/specs/parts/enclosure_lid.py` — comment: the fit rows bind inside the range, and why `screw_holes` goes red with them
- `.../opt_004_most_bolts/{starter,reference/project}/parts/flange.py` — `n_bolts` max 24 → 48
- `.../opt_004_most_bolts/reference/project/project.json` — 32 bolts on Ø123.5
- `.../opt_004_most_bolts/reference/metrics.json` — `n_faces` rungs 30.48/26.67 → 38.10/33.33
- `.../opt_004_most_bolts/specs/parts/flange.py` — `bolt_spacing` + `bolt_pattern` rows, `_bench_bolt_centres`, and the comment explaining which row sees which ligament
- `.../opt_004_most_bolts/prompt.md` — both new rows stated, new provenance block
- `docs/bench.md` — the optimisation caveat bullet, rewritten

## Verification

```
uv run pytest -q tests/test_bench_tasks.py tests/test_bench_author.py   # 44 passed
uv run pytest -q tests/test_bench_cli.py tests/test_bench_scoring.py    # 76 passed
uv run pytest -q "tests/test_prd024_acceptance.py::test_ac1_every_shipped_reference_scores_one[optimize_under_constraints/opt_00{1,3,4}_*]"   # 3 passed
uv run agentcad bench score .../opt_001_lightest_bracket/reference/project --task optimize_under_constraints/opt_001_lightest_bracket  # 1.0000
uv run agentcad bench score .../opt_003_thinnest_lid/reference/project    --task optimize_under_constraints/opt_003_thinnest_lid       # 1.0000
uv run agentcad bench score .../opt_004_most_bolts/reference/project      --task optimize_under_constraints/opt_004_most_bolts         # 1.0000
uv run agentcad bench score .../opt_001_lightest_bracket/starter … 0.8000 · opt_003 starter 0.8400 · opt_004 starter 0.8667
```

`make test` — <orchestrator fills>

## Notes

- **No weight changed.** `task.json` is byte-identical in all three bundles;
  `tests/test_bench_tasks.py`'s weight map, five-per-category count and prompt
  contracts are untouched, and no pinned expectation had to move.
- **Why widening a range is honest.** The `PARAMS` range is the part author's
  claim about what the *script* builds; the rubric is the task's claim about
  what the *application* allows. When the two are the same number the task has
  no engineering content. Every widened bound was measured to build a valid
  single solid (`opt_003`'s 1.0 mm lid and `opt_004`'s 48 holes both build; the
  latter builds *two* solids, which is exactly what the `solids` window is for).
- **`opt_004`'s rubric grew by two rows, which moves its `specs` denominator**
  from 5 to 7. That is a rubric change, not a weight change, and it is the
  reason a failing row there now costs 0.0643 instead of 0.09.
- The `n_faces` proxy is still a proxy — `bolt_pattern` pins it to the ring's
  own eight faces plus one per Ø9 hole, so a candidate that legitimately adds a
  chamfer of its own would lose the row. The prompt states that constraint in
  words, which is the fairness bar this task set holds itself to.
