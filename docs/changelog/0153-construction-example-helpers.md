# 0153 — 2026-08-13 — PRD-010 slice 7: the construction example rewritten onto the toolkit (AC1)

- **Commit:** pending
- **Date:** 2026-08-13
- **Author:** Claude (PRD-010 slice 7)

## Summary

The one slice that edits **committed** example scripts. All three
`examples/construction` parts now go through `toolkit.patterns` and
`toolkit.holes` instead of hand-rolled loops and bare `Locations`/`Hole`
blocks, and slice 1's golden harness — written before any of this existed
precisely so this edit could not be graded by the person making it — passes
**unchanged**: every metric equal at `rel=1e-9` and a **byte-identical `.acm`
payload** on all three parts. That is restated-AC1 (a) and (b) in full, with
no degraded half and no re-baselined golden.

The rewrite is not cosmetic. Every bolt hole in the example now carries a
machine-readable record, so `generate_drawing` prints `12× ⌀18` on the gusset
plate where it previously printed nothing, and a new test fails if anyone
reverts a script to a hand-cut hole.

## Byte identity, per part

Measured through `service._rebuild` on a copy of the example, at the params
each bundled `project.json` stores (`tests/test_examples_golden.py`):

| part | metrics diff | `.acm` bytes | sha256 before | sha256 after |
|---|---|---:|---|---|
| `construction/gusset_plate` | none | 72044 | `56b50449ac4e6bc1…` | `56b50449ac4e6bc1…` |
| `construction/base_plate` | none | 34676 | `238c8ceb7d3222e6…` | `238c8ceb7d3222e6…` |
| `construction/angle_bracket` | none | 26336 | `5a0eda299d710091…` | `5a0eda299d710091…` |

"none" is the harness's nine compared keys — `volume_mm3`, `area_mm2`,
`mass_g`, `bbox`, `center_of_mass`, `n_faces`, `n_edges`, `n_solids`,
`is_valid` — at `rel=1e-9`/`abs=1e-9`, plus the exact byte length. **3 of 3
identical.** The two control parts (`rocketry/flange`,
`prototyping/enclosure_lid`) are untouched and still match, which is what says
the harness itself did not move.

## The measurement that shaped the rewrite

The first `angle_bracket` rewrite used the **named** planes for both bolt
groups — `plane="top"` for the horizontal leg and `plane="left"` for the
vertical one. Every metric matched and the `.acm` was the same length, and the
sha moved: `5a0eda299d710091…` → `c00a36ab9fc7f3af…`. Isolating the two
changes one at a time:

| variant | horizontal leg | vertical leg | sha256 |
|---|---|---|---|
| shipped (before) | `Locations((hx, y, 0))` | `Locations(Plane(origin=(0, y, hz), z_dir=+X))` | `5a0eda299d710091…` |
| A | `plane="top"` | `plane="left"` | **`c00a36ab9fc7f3af…`** |
| B | `plane=Plane.XY` | explicit `Plane(origin=(0, 0, hz), z_dir=+X)` | `5a0eda299d710091…` |
| C (shipped now) | `plane="top"` | explicit `Plane(origin=(0, 0, hz), z_dir=+X)` | `5a0eda299d710091…` |

So the byte change was entirely the **`"left"`** half, and the finding
generalises:

> **For a through hole, sliding the workplane ALONG the hole axis is
> byte-free; rotating it ABOUT the axis is not.** `plane="top"` moves the
> tool's origin from z=0 to z=90 and changes nothing (slice 4 measured the same
> thing on a plate and asserted it as a test). The named `"left"` face carries
> its own frame — `x_dir = -Y`, `z_dir = -X` against build123d's own
> `x_dir = +Z` for a bare `z_dir = +X` — and a cutting cylinder rotated about
> its own axis re-tessellates the resulting cylindrical face.

That is why the vertical leg ships with an explicit `Plane` and a comment
saying why, rather than with the prettier named face. The alternative was to
re-baseline a golden, which is the exact failure this slice exists to prevent.

## Changes

- **`examples/construction/parts/gusset_plate.py`** — the two hand-rolled
  hole loops become `patterns.grid` calls and one `holes.drill`. The chord
  group is `patterns.grid(n, 2, pitch, gauge_c)` translated onto the work
  point; each diagonal group is the **same** grid laid out in the member's own
  (along, across) frame and then rotated onto the diagonal axis. Building it in
  the local frame is deliberate: `patterns.grid` rounds its output to 9
  decimals (`POSITION_DECIMALS`), the local coordinates are exact multiples of
  the pitch and the gauge, and the rotated coordinates are irrational and must
  not be rounded.
- **`examples/construction/parts/angle_bracket.py`** — the two `Locations` +
  `Hole` blocks become two `holes.drill` calls, one per leg, outside the
  builder. See the table above for why one names its face and the other spells
  its plane out.
- **`examples/construction/parts/base_plate.py`** — the four anchor-slot
  centres come from `patterns.grid(2, 2, 2*cx, 2*cy)`. These are **slots, not
  holes**: the point helper feeds a plain `Locations` block exactly as it feeds
  `holes.*`, and the part carries no hole record because it has no holes. The
  grid's row-major order differs from the hand-written corner order and the
  mesh is unchanged, which is the design's §M1 finding (instance order inside
  one block does not matter) holding on a real part and on a *sketch* block
  rather than a hole block.
- **`agentcad/toolkit/holes.py` gains `drill(part, points, diameter, …)`** —
  a table-free bore. **This is a deviation from the plan's slice-7 file list**
  and it is what made the slice possible: `gusset_plate`'s `hole_d` is a
  parameter with a 12–24 mm range whose default 18.0 is *not* an ISO 273 M16
  value (17.0/17.5/18.5), and `angle_bracket`'s 14.0 is not an ISO row either.
  Forcing them onto `holes.clearance` would have meant deleting a parameter
  from a bundled example — changing the part's meaning, which the slice
  explicitly may not do — or transcribing a false provenance claim onto a
  number no standard supplied. `drill` records `family: "drilled"`, a `⌀18`
  designation, and **no `size` and no standard row**. It shares `_drill`, so it
  shares the guard, the records and the byte-faithful `BuildPart` route.
- **`tests/test_examples_golden.py`** — a new
  `test_the_rewritten_construction_parts_carry_hole_records`. The goldens are a
  byte-identity gate and an edit that changes nothing passes them trivially;
  this asserts the rewrite's actual product (`⌀18 × 12` on the gusset, two
  `⌀14 × 2` groups on the bracket, nothing on the base plate) and fails if a
  script is reverted to a hand-cut hole.

## What the drawings now say

Generated from the rewritten example (`scratchpad/slice7_drawing.py`, run
against a copy):

```
gusset_plate   hole_groups: ⌀18, count 12, from_metadata True, family drilled
               SVG: 12× ⌀18                      hole_warnings: []
angle_bracket  hole_groups: ⌀14, count 2,  from_metadata True, family drilled
               SVG: 2× ⌀14
               hole_warnings: ["hole record 'h1' (⌀14, 2 instance(s)) has no
                 matching circle in the top view … a hole on another face has a
                 record and no callout (PRD-014)"]
base_plate     hole_groups: []                   hole_warnings: []
```

The bracket's second group is on the **vertical leg**, i.e. a side face, and
`generate_drawing` reads the top view only. That warning is slice 6's
documented, inherited limitation announcing itself correctly on a real part —
not a regression, and PRD-014's job to fix.

## Files

- `examples/construction/parts/gusset_plate.py` — `patterns.grid` + `holes.drill`
- `examples/construction/parts/angle_bracket.py` — two `holes.drill` groups
- `examples/construction/parts/base_plate.py` — `patterns.grid` slot centres
- `agentcad/toolkit/holes.py` — `drill`, `_check_diameter`
- `agentcad/toolkit/hole_standards.py` — `check_std` made public (a caller that
  wants only the *symbology* still has to validate `std`)
- `tests/test_examples_golden.py` — the records test
- `tests/test_holes.py` — `drill`: diameter, blind depth, shared guard, raises
- `docs/changelog/0153-construction-example-helpers.md` — this entry

## Notes

- **The cache key necessarily changed, and that half of the PRD's AC1 was
  never achievable.** `service._cache_key` hashes the **script text**, so any
  rewrite whatsoever mints a new `.cache/<key>.acm` filename. The design spec
  restated AC1 for exactly this reason; what is verifiable is that the *bytes*
  under the new key are the old bytes, and they are. The `.holes.json` sidecar
  is content-addressed on the same key, so it invalidates itself.
- **`patterns.grid` rounds to 9 decimals and that is load-bearing here.** It is
  why the diagonal groups are built in the member's local frame and rotated
  afterwards rather than being asked to emit rotated coordinates: a rounded
  `cos(45°) × 88` is a different number, and the golden would have caught it as
  a metric drift rather than as the design mistake it would have been.
- `agentcad check --project examples/construction` is green: 10 passed, 0
  failed, 0 skipped, 0 errors of 10 in 6.1 s (build 3, assembly 4, drawings 3;
  specs `skip`/`not_declared`, as before).
- Deliberately not here: `holes.clearance` on the examples (no bundled
  construction part uses a fastener-table hole), and the `patterns.linear` /
  `polar` / `mirror` shape patterns (no bundled construction part patterns a
  *solid*).

## Verification

```
$ .venv/bin/python -m pytest -q tests/test_examples_golden.py
8 passed in 3.7s
$ .venv/bin/python -m pytest -q tests/test_examples.py -k construction
4 passed, 16 deselected in 5.44s
$ .venv/bin/agentcad check --project examples/construction --work-dir <tmp>
check: green — construction · 10 passed, 0 failed, 0 skipped, 0 errors of 10
```

The full-suite count for this slice and slice 8 together is stated in
changelog 0154, which lands in the same series.
