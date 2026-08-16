# 0161 — PRD-010 review round: 21 findings fixed (red branch, persisted RecursionError, lying callouts, false warnings, unit errors)

- **Commit:** pending
- **Date:** 2026-08-16
- **Author:** Nikita Fedorov

## Summary

An independent review of `prd-010-feature-toolkit-ii` returned
CHANGES-REQUIRED with 21 verified findings. This entry lands all of them. Two
were blocking: the branch was **red at HEAD** (three acceptance tests pinned
the PRD to a directory it only moves to at merge), and `add_holes` and
`push_pull` minted the **same module global** with independent counters, so
any combination of the two left the part permanently unbuildable with a
persisted `RecursionError` and only a toast to say so.

Every fix is TDD: the regression test was written and observed failing for the
stated reason before the code changed. The measured before/after numbers are
recorded below because several of these defects were invisible to the previous
tests — the sheet-metal blank test passed with the wrong blank, and the FR14
frontend test was a substring grep its own hard-coded literal satisfied.

## Changes

### R1 — the branch was red at HEAD (blocking)

`tests/test_prd010_acceptance.py` hard-coded
`docs/prd/completed/PRD-010-feature-toolkit-ii.md`. A PRD moves to
`completed/` at **merge**, not when the build finishes, so the file was in
`in-progress/` (where `docs/roadmap.md:81` correctly links it) and three tests
failed deterministically:
`test_ac1_the_cache_key_necessarily_moved_and_the_prd_says_so`,
`test_the_prd_records_every_divergence_the_design_measured`,
`test_the_roadmap_points_at_the_moved_prd`.

**The tests were wrong, not the PRD's location.** `_find_prd()` now locates the
PRD wherever it lives, and the roadmap test — renamed
`test_the_roadmap_link_resolves_to_the_prd_where_it_actually_lives` — asserts
what is gradeable *before* merge: the link resolves, it points at the file that
actually exists, and the PRD is filed exactly once. It no longer asserts a
`completed` status that is only true on the far side of an event this tree
cannot observe. The PRD did not move.

### R2 — `add_holes` and `push_pull` minted the same global (blocking)

`tools_holes` emitted `_agentcad_prev_build_{n}` with
`n = script.count(ADD_HOLES_MARKER)`; `tools_facemod` emitted the identical
name counted off `PUSH_PULL_MARKER`. Both start at 0. The name is resolved as
a **module global at call time**, so the second block rebound it to the first
block's wrapper and `build` recursed into itself. Order-symmetric, and also
reachable from `add_holes` alone: the marker says "edit or remove freely", so
deleting a middle block walks the count backwards onto a live name.

New `agentcad/core/script_blocks.py` owns the naming. `next_build_alias(script)`
derives the name from **the bindings the script actually has** — a textual scan
for every `_agentcad_prev_build_*` already present, whoever wrote it, in
whatever order, after whatever deletions — and returns one that is absent. The
scan is textual rather than an AST walk on purpose: the script may not parse
(that is the state a user repairing one is in), and a name appearing anywhere
at all is a name not worth reusing.

**`update_part` still does not roll back, and should not.** A human editing
their own script must be able to save a broken state — that is how they repair
it, and `get_part.status` exists to report it. Source *nobody typed* is the
other case, so the two packs now go through
`script_blocks.apply_generated_block`, which reverts its own append when the
rebuild fails and returns `rolled_back` / `restored` alongside the error. The
call still reports failure; what changes is that the user is not left holding
an unbuildable part they did not author.

### R3 — drawing callouts printed intent, not what was matched

`_match_record` already computed the set of circles a record actually landed
on and **discarded it**; the callout printed `record["count"]`.

| repro | before | after |
|---|---|---|
| drill 4× M5, weld 2 shut (`holes.carry` across the fill) | SVG says `4× ⌀5.5` over **2** circles, no warning | `2× ⌀5.5` + a warning naming record, claim and match |
| 2 holes then `patterns.mirror` | SVG says `2×` while `hole_groups` says `count: 4`, no warning | `2× ⌀5.5` + "accounts for 2 of the 4 … no record claims them" |

The mirrored case deliberately still reads `2×`: the two unmatched circles are
**not** swept into the callout, because a second feature that merely shared a
diameter would then be mislabelled — the exact mistake `_match_record` demands
centre agreement to avoid. What changed is that the shortfall is stated.

### R6 — `_extent()` returned 0 on the flagship's own plane

A workplane says *where* to drill; which way is "into the part" is a fact about
the part. The bundled `angle_bracket` drills its vertical leg through
`Plane(origin=(0, 0, hz), z_dir=(1, 0, 0))` — chosen deliberately, because
sliding a workplane along the hole axis is free while the named `left` face
would rotate the tool and re-tessellate the part — and that normal points
**into** the material. `_extent` measured only along `-z_dir` and returned
**0.0 mm** of stock.

build123d's `Hole` does not care (a thru hole is cut both ways), which is why
the geometry was always right and only everything *derived* from the frame was
wrong. Measured on a 40 mm slab with such a plane:

| | before | after |
|---|---|---|
| `_extent` | `0.0` | `40.0` |
| `verify="exact"` status | `flush`, `engaged_mm3 0.0` + a false warning | `engaged`, `engaged_mm3 3141.59`, no warning |
| recorded `axis` | `[-1, 0, 0]` (backwards) | `[1, 0, 0]` |
| `safe_bool` fallback | zero-height cylinder — **raises** `safe_bool: cut failed` | cuts 3141.59 mm³ |

New `_tool_frame()` returns the workplane oriented so `-z_dir` faces the
material, used only for measuring stock, building tools and placing fused
threads. **Placement is untouched** — the points are `(u, v)` in the caller's
plane, so re-basing them would move the holes. On the real `angle_bracket`,
both vertical-leg instances now report `engaged` at 1539.38 mm³ each where they
previously reported `flush, engaged 0.0`; the geometry and the goldens are
byte-identical.

### R7 — `exact` warned on every correct rib, boss and pattern instance

A helper-built feature sits **on** its seat plane, so its interpenetration with
the part is 0 by construction — and `engagement` called that `flush` and
warned. The strong tier fired on the happy path, which is how a reader learns
to ignore it. Meanwhile the default `bbox` tier cannot see a real miss into
existing void, because the boxes genuinely do overlap.

`part & tool` cannot tell these apart: for a face-to-face seat, an edge
tangency **and** a shape adrift in an existing pocket it is an empty Compound —
volume 0 and area 0 in all three (measured). The discriminator is the shared
face area, from the identity
`(part.area + tool.area - (part + tool).area) / 2`:

| case | interpenetration | contact_mm2 | status before | status after |
|---|---|---|---|---|
| rib/boss on its seat | 0 | 400.0 | `flush` + warning | `flush`, **no warning** |
| instance over an existing through-pocket | 0 | 0.0 | `flush` (and `engaged` under `bbox`) | `missed` + warning |
| corner-to-corner tangency | 0 | 0.0 | `flush` + warning | `missed` + warning |
| genuine overlap | 800.0 | 480.0 | `engaged` | `engaged` |

`engagement` now reports `contact_mm2` under `exact`, and the extra fuse runs
only on instances that already measured zero interpenetration. `holes` still
warns on `flush`, correctly: for a *cut*, zero engaged volume means nothing was
removed.

### R10 — `add_holes` accepted a zero or negative drilled diameter

`depth` had a `> 0` guard and `size` did not, so the block was written and only
*then* failed in the kernel. Now refused up front as a `validation_error`, and
the script on disk is untouched. (With R2's rollback this is belt and braces —
the write is undone either way — but a request the tables can answer "no" to
should never reach the disk.)

### R18 — ribs and bosses skipped the material-conservation check

`_fuse_warnings` grows a second check when it is given the `seed`: what the
instances *contain* against what actually *arrived*. `patterns` passed it;
`features.rib` and `features.boss` passed `1` and no seed, so a feature landing
half inside material it overlaps fused in silence — the per-instance probe
cannot see it, because the instance genuinely *does* engage the part. Both now
pass `seed=solid`.

### R4 — the mitre was cut from `fold()` and never from `unfold()`

The flat blank had a **square corner** where the model has a mitre, so it could
not be bent into the part. Worse than the volume gap: both tabs ran the full
`rho = R + k·t` past the corner and both claimed the same `rho × rho` square,
so the fuse swallowed **30.108800 mm³** of declared sheet in silence.

| | before | after |
|---|---|---|
| `fold()` | 10441.970395 | 10441.970395 (byte-identical) |
| `unfold()` | 10393.818734 | 10376.632742 |
| gap | 48.151660 | 65.337653 |
| tabs declared vs blank | 10423.927534 vs 10393.818734 — **30.1088 claimed twice** | agree |
| `flat_outline()` at the corner | straight through `(33.88, -23.88)` | V-notch apex at the plate corner `(30.0, -20.0)` |

The existing test (`close.unfold().volume > rip.unfold().volume`) passed with
the wrong blank; it is now the exact closed form, joined by
`test_the_flat_blank_carries_the_mitre_notch_not_a_square_corner` and
`test_the_close_corner_bend_line_stays_inside_the_blank`.

**Two measured findings worth keeping.** First, the review's predicted 41.4690
is not reachable by any correct model — it plugs the mitre-extended spans into
`α(0.5−k)t²·span` while ignoring that the mitre cuts that extension away; the
fold is right (its declared material and measured volume agree to the last
digit), so the honest gap is *larger*. Second, cutting the blank with the same
45° bisector would have been **invisible** — the two half-spaces tile the corner
square exactly, so the union, its volume and its outline are unchanged, and a
total-volume identity would have passed. The blank's mitre is therefore the
**chord of the unrolled bisector** (slope `sin(a)/a`): the exact unrolled
boundary `v = ρ·sin(u/ρ)` is neither line nor arc, and sine is concave on
(0, 90°], so the chord is the steepest straight mitre that never over-runs the
bisector. The blank comes out small (3.231 mm²/tab, gap peaking 0.815 mm inside
the bend zone, zero at both ends) and **never large** — over-running would fold
two leaves into one space, which is the failure R5 exists to catch.
`bend_lines()` now stops a mitred midline at `ρ·sin(a)/2`.

### R5 — `fold()` had no material-conservation check

`base(60,40).hem("front","open",length=50).flange("back",90,25)` declared
15496.460033 mm³ and measured 15256.460033 — **240.000000 mm³ gone**, with
`is_valid True`, one solid, and `warnings == []`. Only `is_valid` and the solid
count were checked.

`_conserved()` now uses the closed form already in the `fold()` docstring
(`_declared_flange_volume`), credits every cut with what it *measurably*
removed (`_cut_measured`), and warns through the existing `self._warn` sink.
Applied to `unfold()` too — which is what proves R4's blank honest. Volumes go
through a new `_volume()` that sums `shape.solids()` (the nested-`Compound`
trap).

### R19 — the outline and its edge list picked start indices separately

Measured on a bracket with an oversized round relief: `flat_outline()[0] =
(1.634902, -6.809857)`, an arc *sample* 34.2746 mm from the base corner, while
`flat_outline_edges()[0]["a"] = (-30, -56.094690)`, 36.0947 mm away. Same loop,
different starting edge — a reader walking them together (a DXF writer, a
bend-line overlay) is off by an edge. `_outline` now keeps each edge's interior
samples in a parallel list so one rotation moves both.

### R8/R9/R12 — the units contract

The module documents millimetres and the ASME path returned inches under the
documented keys.

| | before | after |
|---|---|---|
| `lookup("clearance","1/4",std="ansi")["fits"]["normal"]` | `0.281` (in) — vs `clearance(...)["d"] == 7.1374` | `7.1374` mm; inches under `fits_native` |
| `lookup(family="tapped")` `tap_drill` | `5.1054` mm at top level, `0.201`/`0.213` **in** inside `pitches` | every pitch row rebuilt: `tap_drill` mm, `tap_drill_native` |
| `_mm(1.0, {"units": "inch"})` | `1.0` — silently unconverted | `25.4` |

`_UNIT_FACTORS` makes the unit lookup **total**: `mm/millimetre(s)/
millimeter(s)/in/inch/inches` are accepted case- and space-insensitively and
anything else raises naming the field, validated at load by `table()`.
`clearance_fits()` moved to mm as well (its bare name carried inches too), with
`clearance_fits_native()` alongside.

### R11 — provenance was the file's source list stapled to every row

`iso_cbore_csk.json` names four sources: two back the ISO 4762 column, one
backs the whole ISO 10642 column alone, and one was consulted for the
counterbore convention and **deliberately not transcribed**. Stapling all four
onto every answer claimed corroboration nine rows do not have and named a
source backing no row at all.

Each file now carries a `provenance` block (a `default` plus `/`-joined scope
overrides indexing that file's `sources`), validated on load. Answers report
the sources backing **that row**, plus `corroborated` and `conflicts`.
Measured: `csk("M5")` went from 4 sources to 1 with `corroborated: false`;
`cbore("M5")` reports 2 and `corroborated: true`; `clearance("#8",
fit="normal", std="ansi")` surfaces its recorded resolved conflict while its
neighbours do not.

**The transcription itself is clean.** The reviewer spot-checked every named
row across all six tables against the published standards and found no numeric
error, and the JSON diff for this entry contains **zero data rows**.

### R13 — the flat +1.5 mm counterbore rule below M5

`cbore("M2")` bored **5.3** where DIN 974-1 gives 4.3 — a flat 1.5 mm on a
3.8 mm head is a third of the whole hole. The guard is on the **head diameter**
(`CBORE_DIA_FLAT_MIN_HEAD = 8.5`, the ISO 4762 M5 head; `0.375 in` on the inch
side), because the rule is a statement about the head and the inch tables index
sizes whose nominal diameter they do not carry. Below it the clearance is the
same *proportion* of the head it has at the threshold — continuous, so M5 stays
10.0 and a 1/4 in head still gets 7/16, and M2 becomes **4.4706**, still erring
large rather than small. The depth clearance is deliberately left flat: no
published small-size depth was measured, and inventing a second guard would be
the invented number this module exists to avoid.

### R14 — the "internal checks" were false

Measured: `dk = 2.24·d` holds M3–M12 and fails above (M16 33.6 vs 35.84, M20
40.32 vs 44.8). `H max = d` holds throughout ASME B18.3, but `A max = 1.5·d`
holds only on the fractional sizes — **all nine numbered sizes are above it**
(#0 0.096 vs 0.090, #10 0.312 vs 0.285). **The data is right; the corroborating
rule was the false part.** Each claim is now restricted to the range where it
holds, in the JSON `notes`, in two new tests, and in dated "Correction
(review)" blocks in changelogs 0148 and 0154.

### R15/R16/R17 — the hole-on-face card

- **R15** switching family to Drilled carried `M5` into a numeric input. The
  size is now normalized at render to what *this* control means, and the family
  `onChange` clears it when the `drilled` boundary is crossed.
- **R16** `holeForm` was module-level and reset by nothing, so a depth typed on
  part A applied to part B. `syncHoleFormPart()` is subscribed to the existing
  `selectedPart` signal and **compares** rather than firing, because `setState`
  re-announces a key whether or not the value moved.
- **R17** Enter fired `applyAddHoles` with no `disabled` check. A disabled
  `<button>` never fires `click`, so the attribute *is* the click path's guard;
  the keydown path now honours it, which also makes it the in-flight guard.

**User-visible:** the table-family default size is now the table's first row
rather than a hard-coded `M5`. Removing that literal is what makes "every size
the picker offers came from `hole_standards`" true without exception; a nicer
default has to come from the tool, not from a literal in JS.

### R21 — two tests that could not fail

The FR14 test was a substring grep its own hard-coded `"M5"` satisfied. It is
now positional — assertions run against the extracted bodies of
`renderFaceCard` / `renderHoleControls` / `applyAddHoles`, with comments
stripped first (the first rewrite still passed with the render call commented
out) — and the shipped `main.js` is *executed* in node against a stub DOM, so
R15/R16/R17 are graded as behaviour. `tests/test_hole_standards.py`'s
structural invariants read only the ISO files, leaving **ANSI with no
structural coverage at all**; they are now parametrised over all six.

### R20 — the tool count was wrong in three docs, three ways

Measured off a live registry: **73 tools, 76 with the optional `[fem]` extra**
(`fem_static`, `fem_modal`, `fem_thermal` register only when their deps are
importable). `README.md` said 72/75, `docs/architecture.md` said 76,
`docs/agent-api.md` said 76/79. All three now state base and with-fem in one
phrasing. A repo sweep found and fixed six more stale claims (65/68, 42/45,
25) in `AGENTS.md`, `docs/user-guide.md`, `docs/market_research.md`,
`PRD-018`, `PRD-024`, and stale comments in `tests/test_tools.py` /
`tests/test_mcp.py`. `docs/roadmap.md`'s v3 snapshot is now marked as
historical rather than reading as current. The `docs/agent-api.md` tool tables
were already correct — set-diffed against the live registry, empty both ways;
only the header number was wrong.

## Files

- `agentcad/core/script_blocks.py` — **new**: `next_build_alias`,
  `existing_build_aliases`, `apply_generated_block`
- `agentcad/core/tools_holes.py` — alias allocation, `> 0` size guard, rollback
- `agentcad/core/tools_facemod.py` — alias allocation, rollback
- `agentcad/kernel/handlers/drawing.py` — callout counts matched circles;
  two new divergence warnings
- `agentcad/toolkit/holes.py` — `_stock_sides`, `_tool_frame`, `_extent`;
  frame threaded through `_drill`, `counterbore`, `countersink`
- `agentcad/toolkit/patterns.py` — `_contact_area`, `_AREA_TOL`, `contact_mm2`,
  seat/miss split; `_fuse_warnings` no longer warns on a seat
- `agentcad/toolkit/features.py` — `seed=solid` at both `_fuse_warnings` calls
- `agentcad/toolkit/sheetmetal.py` — `_mitre_cuts(flat=True)`, `_cut_measured`,
  `_declared_flange_volume`, `_conserved`, `_volume`; one outline start index
- `agentcad/toolkit/hole_standards.py` — `_UNIT_FACTORS`/`_unit_factor`/
  `_is_inch`, per-row `_provenance`/`_prov_scope`/`_check_provenance`,
  `clearance_fits_native`, the counterbore head guard
- `agentcad/toolkit/data/*.json` — a `provenance` block per file, corrected
  `notes` (**no data row changed**)
- `frontend/js/main.js` — size normalization, `syncHoleFormPart`, Enter guard
- `tests/test_prd010_acceptance.py` — `_find_prd`, roadmap test rewritten
- `tests/test_tools_holes.py`, `tests/test_holes.py`, `tests/test_patterns.py`,
  `tests/test_features.py`, `tests/test_drawing_holes.py` — the regressions
- docs: `README.md`, `docs/architecture.md`, `docs/agent-api.md`, `AGENTS.md`,
  `docs/user-guide.md`, `docs/market_research.md`, `docs/roadmap.md`,
  `docs/prd/pending/PRD-018-*.md`, `docs/prd/pending/PRD-024-*.md`

## Verification

`make test-fast`:

```
$ .venv/bin/python -m pytest -q -n 2 --dist loadscope -m "not slow"
2047 passed, 1 skipped in 304.98s (0:05:04)
```

`make test` — the full suite, in the two chunks this machine runs so the
examples build does not exceed the sandbox's foreground time cap:

```
$ .venv/bin/python -m pytest -q -n 4 --dist loadscope tests/ --ignore=tests/test_examples.py
2360 passed, 1 skipped in 326.71s (0:05:26)
$ .venv/bin/python -m pytest -q -n 2 tests/test_examples.py
20 passed in 943.06s (0:15:43)
```

**`make test`: 2380 passed, 1 skipped.** The one skip is the long-standing
`[fem]`-extra skip. This is the first green full-suite measurement on this
branch: HEAD had **three deterministic failures** (R1), so 0160's cited 2329
was never reproducible — that entry now carries a correction saying so.

**One load-sensitive test, named rather than left to surprise someone.**
`tests/test_sketch_diagnostics.py::test_the_full_budget_completes_the_same_analysis`
asserts `analysis_complete` under a **wall-clock** budget
(`ANALYSIS_BUDGET_MS`). It failed once here when `-m "not slow"` was run
concurrently with the examples chunk, and passed in every run on an otherwise
idle machine. Pre-existing and untouched by this work (`toolkit/sketch.py` is
not in this diff); recorded because a chunked `make test` invites exactly that
overlap.

## Notes

- **A reverted append still leaves two history snapshots.** `update_part`
  snapshots on every `project_changed`, and the rollback is a second
  `update_part`, so git history records the broken state and its revert. That
  is what actually happened, and the revert has to go through `update_part` so
  the rebuild, status and events return to the good state — but a Cmd+Z from
  there steps into the bad script.
- **The reviewer verified the transcription itself is clean** — every named row
  across all six tables was spot-checked against the published standards with
  no numeric error found. Every hole-standards fix below is in the machinery
  around the data, not the data.
