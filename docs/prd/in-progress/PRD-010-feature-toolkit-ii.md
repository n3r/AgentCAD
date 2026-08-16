# PRD-010 — Feature toolkit II: patterns, hole wizard, sheet-metal v2

- **Status:** implemented — in review on `prd-010-feature-toolkit-ii` (AC1–AC8 + AC7b verified)
  see [As built](#as-built) for the verification table and the divergences
- **Phase:** v5 — daily-driver depth
- **Created:** 2026-08-09
- **Design:** [`docs/superpowers/specs/2026-08-13-feature-toolkit-ii-design.md`](../../superpowers/specs/2026-08-13-feature-toolkit-ii-design.md)
  · **Plan:** [`docs/superpowers/plans/2026-08-13-feature-toolkit-ii.md`](../../superpowers/plans/2026-08-13-feature-toolkit-ii.md)
  · **Changelogs:** 0147–0160
- **Origin:** competitive analysis (Aug 2026) + sheet-metal v1 residuals
- **Depends on:** — (none hard)
- **Related:** PRD-009 (profiles feed features), PRD-011 (fastener packages pair with holes), PRD-014 (hole metadata → callouts and hole tables), PRD-016 (hosts the UI actions), PRD-021 (hole/pattern metadata → DFM checks)

## Problem & motivation

The high-frequency feature vocabulary is missing or manual. Patterns exist
only as raw build123d idioms (`PolarLocations`, `GridLocations` — the
bundled examples use them by hand). Holes have no standards awareness: an M5
clearance hole is a table lookup and trigonometry, a tapped hole is a
`threads.tapped_hole_thread` dance the cheat-sheet has to explain. Sheet
metal v1 (`SheetPart` in `agentcad/toolkit/sheetmetal.py`) does full-edge
flanges only — no bend relief, no partial-width flanges, no hems, no corner
treatments — the explicitly recorded v1 residual.

The competitive evidence: SolidWorks' Hole Wizard + Toolbox is daily-use
muscle memory and part of what defines "professional depth"; configurations,
Toolbox, and template depth are what leavers say they lose
(market_research.md, "The desktop incumbents: Fusion, SolidWorks, Creo,
NX"). The Gap matrix rates "Patterns / hole wizard / standard features —
partial (toolkit) — SolidWorks — **build**". For the target users (robotics,
rocketry, construction) hole standards are the single highest-leverage gap:
every plate they make is a bolt pattern.

The agent-native angle makes this more than parity. Standards tables become
part of the agent's vocabulary — "M5 clearance holes on a 40 mm bolt
circle" is one call, not arithmetic. Every helper returns the same honest
warnings the `safe_*` family does. And because helpers know *intent*
(tapped M5, not "a ⌀4.2 cylinder"), they can emit machine-readable hole
metadata that flows downstream: `generate_drawing` today *detects* holes
from geometry; with metadata it can state designations, and PRD-014 hole
tables and PRD-021 DFM checks consume the same records.

## Users & jobs

- **Part author (human or agent):** place standard holes by designation,
  pattern features and bodies, add ribs/bosses/draft — without OCCT fights
  or table lookups.
- **Sheet-metal designer:** real brackets — partial flanges with relief,
  hems, closed corners — that still round-trip fold/unfold from one spec.
- **Drawing pipeline (PRD-014):** correct thread designations and hole
  tables from metadata instead of geometric guessing.
- **DFM checker (PRD-021):** hole intent (tapped M5 vs drilled ⌀4.2) to
  check against process rules.
- **UI user (PRD-016):** click a face, fill a hole dialog, get a script
  edit — direct manipulation that stays reviewable.

## Goals

- G1. `patterns` toolkit: linear / polar / mirror of features and bodies,
  robust fusion, honest warnings.
- G2. `holes` toolkit: clearance / tapped / counterbore / countersink from
  ISO and ANSI tables, placed on faces or points, one call per intent.
- G3. Machine-readable hole metadata harvested at build time, persisted
  with part state, consumed by drawings (PRD-014) and DFM (PRD-021).
- G4. Rib / boss / draft helpers under the `safe_*` warning contract.
- G5. Sheet-metal v2 in `SheetPart`: bend relief, partial-width flanges,
  hems, corner treatments — folded solid and flat pattern staying
  consistent by construction.
- G6. UI actions (hole on face-click, pattern dialog) emit visible script
  calls — the script stays the source of truth (PRD-016 hosts the UI).

## Non-goals

- Weldments/frames with cut lists — high target-fit but a feature of its
  own (the Gap matrix tracks it separately); not this PRD.
- Fastener geometry — the threads toolkit and PRD-011 packages supply the
  screws; this PRD supplies the holes they need.
- Full Hole Wizard catalog parity (slotted holes, legacy types) — the four
  families first; the data format leaves room.
- The direct-modeling UI surface itself — PRD-016; this PRD defines the
  script calls its dialogs emit.

## Experience

**Agent path.** The `part_template` cheat-sheet shows the new imports. A
script reads:

```python
from agentcad.toolkit import holes, patterns

part, hole_recs, warn = holes.clearance(
    part, face="top", points=patterns.bolt_circle(r=20, n=8),
    size="M5", fit="medium")
```

Tapped holes couple to the existing threads data (`holes.tapped(...,
size="M5", depth=12)` drills the tap-drill bore and records the thread
spec). Warnings return like `safe_fillet`'s — surfaced, not swallowed.
After rebuild, `generate_drawing` callouts read "8× M5 - 6H ⌀4.2 ↧12"
from metadata instead of "⌀4.2 (8×)" from detection. An agent that needs a
table value without building calls `hole_standards {size: "M5",
family: "clearance"}`.

**Human path (with PRD-016).** Click a face → hole dialog (family,
standard, size, fit/depth) → preview → Apply appends the call to the script
(the `push_pull` precedent: visible, editable, composable) and rebuilds.
Pattern dialog likewise. Sheet-metal: the flange controls gain width/start
and relief kind; the `flat_pattern` export shows relief cuts and hems.

**Handoff.** Everything either party does is ordinary script lines both can
read, review (PRD-002), and edit.

## Functional requirements

**Patterns**
- FR1. `patterns.linear(part_or_feature, direction, count, spacing)`,
  `patterns.polar(part_or_feature, axis, count, radius?, span_deg?)`,
  `patterns.mirror(part_or_feature, plane)` — each accepts a body (Shape)
  or a feature callable, fuses via `safe_bool`, and returns
  `(part, warning?)`; overlap and degenerate spacing produce warnings,
  never silent geometry.
- FR2. Helper `patterns.bolt_circle(r, n, start_deg?)` returns point sets
  usable by `holes.*` and plain build123d.
- FR3. A pattern of a wizard hole replicates metadata as one hole *group*
  (count + positions), not N unrelated records.

**Hole wizard**
- FR4. `holes.clearance(part, face/plane, points, size, fit=close|medium|
  loose, std=iso|ansi)`, `holes.tapped(part, …, size, pitch?, depth?,
  thread_class?)` (tap-drill from the same data the threads toolkit uses),
  `holes.counterbore(part, …, size, fastener=iso4762|…)`,
  `holes.countersink(part, …, size, angle=90)`. Each returns
  `(part, records, warning?)`.
- FR5. Standards data ships as versioned data files in the toolkit
  (ISO 273 clearance fits; ISO 262 pitches + tap drills; counterbore/
  countersink dims for standard fastener heads; ANSI/ASME equivalents
  including number/letter drills); designation strings are generated per
  standard's symbology.
- FR6. Every wizard hole records `{id, family, standard, designation, size,
  positions, axis, depth|thru, tap {pitch, class}?, cbore {d, depth}?,
  csk {d, angle}?}`; the worker harvests records after `build(p)` and
  returns them with the build result; they persist with part state and
  appear in `get_part`.
- FR7. Guards: a hole placed off the target face, depth beyond stock, or
  overlapping an existing wizard hole yields an honest warning; impossible
  geometry fails as a normal structured script error.

**Ribs, bosses, draft**
- FR8. `features.rib(part, profile, thickness, draft_deg?)`,
  `features.boss(part, at, d, h, hole=M-size?, draft_deg?)` (screw-boss
  variant pairs with `holes.tapped`), `features.draft(part, faces,
  angle_deg, neutral_plane)` — each returns `(part, warning?)`; draft
  falls back to the largest achievable angle with a warning naming it
  (the `safe_fillet` binary-search pattern).

**Sheet-metal v2**
- FR9. `SheetPart.flange(edge, angle_deg, length, inner_radius=None,
  start=0, width=None)` — partial-width flanges; multiple non-overlapping
  flanges per edge allowed (v1's one-per-edge restriction lifted).
- FR10. Automatic bend relief (rect | round | tear, sized from thickness
  rules) wherever a partial flange meets remaining material; per-flange
  override; reliefs appear in both fold and flat pattern.
- FR11. `SheetPart.hem(edge, kind=open|closed|teardrop, length)` and corner
  treatments (`close | gap | rip`) where two flanges meet.
- FR12. `fold()` / `unfold()` / `flat_outline()` / `bend_lines()` stay
  one-spec-consistent for every v2 feature — bend allowance math extended,
  `k_factor` honored, `sp.warnings` records any fusion fallback.

**Downstream & docs**
- FR13. `generate_drawing` prefers harvested hole metadata for callouts
  (designation, count, depth symbol) and falls back to today's geometric
  detection for non-wizard holes; `detected` reports which source it used.
  Hole-*table* rendering belongs to PRD-014.
- FR14. UI dialogs (hole on face-click, pattern dialog — hosted by
  PRD-016's shell) emit the script calls appended visibly; normal rebuild
  events fire.
- FR15. CHEATSHEET (`agentcad/core/templates.py`) and
  `docs/part-authoring.md` document every helper; the construction
  example's bolt patterns are rewritten to the helpers (validated
  identical on a copy first — see AC1).

## Agent surface

- New tool: `hole_standards {family?, size?, std?}` → table data
  (clearance diameters per fit, tap drills, cbore/csk dims, designation
  strings) so agents and UI dialogs query standards instead of embedding
  tables in prompts. Pure data — registered unconditionally.
- Changed: build results and `get_part` gain `holes` (the metadata
  records); `generate_drawing`'s `detected` gains `from_metadata: true`
  designations where wizard records exist.
- No new geometry tools — deliberately: helpers are *script* vocabulary
  and the agent's hands remain `update_part_script`/`set_params`; warnings
  ride the existing rebuild-result `warnings` channel.
- Error types: unchanged (script errors, `contract_error` for bad specs).

## Technical approach

- **Toolkit modules:** `agentcad/toolkit/patterns.py`, `holes.py`,
  `features.py`; `sheetmetal.py` extended (`_Flange` gains start/width/
  relief; the flat-outline walker learns tabs-with-gaps and hem geometry).
- **Metadata harvest:** helpers append to a per-build registry in
  `toolkit.holes`; the kernel worker drains it into the build result after
  `build(p)` returns. Subtlety: the script namespace is fresh per rebuild
  but `agentcad.toolkit` is a warm import in the worker — the worker must
  explicitly reset the registry per request (regression-tested), and the
  reset lives in the build pipeline, not in scripts.
- **Standards data:** `agentcad/toolkit/data/*.json`, versioned with the
  package, loaders cached. The `hole_standards` tool pack
  (`agentcad/core/tools_holes.py`) reads the same loaders server-side —
  data only, no OCP import, honoring the kernel-only-OCP rule.
- **Persistence:** hole records ride the part's stored state next to
  metrics (additive model/`project.json` field); the drawing handler pack
  (`agentcad/kernel/handlers/` drawing path) consumes them for callouts.
- **Tests:** golden geometry (AC1's identical-metrics rewrite), table
  spot-checks against published values, harvest/reset regressions,
  sheet-metal fold/unfold round-trips with reliefs and hems, drawing
  designation tests.

## MVP & phasing

- **MVP:** `patterns` (linear/polar/mirror + bolt_circle);
  `holes.clearance`/`holes.tapped` with ISO tables, metadata harvest, and
  drawing designations; `SheetPart` partial-width flanges + automatic bend
  relief; `hole_standards` tool; CHEATSHEET sections.
- **Phase 2:** counterbore/countersink + ANSI tables; hems + corner
  treatments; ribs/bosses; pattern metadata shaped for PRD-021 DFM.
- **Phase 3:** draft helper (the hardest OCCT surface — ships when its
  warning contract is honest); UI dialogs with PRD-016; config-aware hole
  tables with PRD-012/PRD-014.

## Acceptance criteria

- AC1. The construction example's bolt patterns rewritten with
  `patterns`/`holes` helpers produce identical geometry — same metrics and
  the same content-hash mesh-cache entries (test on a copy).
- AC2. A tapped M5×12 hole yields a drawing callout with the correct
  designation sourced from metadata, not detection (test asserts the SVG
  text and the `from_metadata` flag).
- AC3. `hole_standards {size: "M5", family: "clearance"}` returns
  close/medium/loose diameters matching published ISO 273 values
  (spot-check test; same for one tap-drill and one cbore row).
- AC4. A bracket with a partial-width flange gets automatic bend relief:
  `fold()` is valid, `unfold()`/`flat_pattern` round-trips with relief
  cuts and correct bend lines (test + one visually verified SVG).
- AC5. A polar pattern of one tapped hole produces a single hole-group
  record with `count: n`, and the callout reads "n× …" (test).
- AC6. Misuse is honest: hole off the face and overlapping flanges return
  warnings; impossible geometry returns a structured script error with the
  failing line (tests).
- AC7. Metadata registry resets between rebuilds — two consecutive builds
  of different parts on one warm worker never cross-contaminate records
  (regression test).
- AC8. Full suite green; examples repo state untouched by default (the
  rewrite lands only after AC1 proves identity).

## Risks & open questions

- **Warm-worker registry discipline:** the harvest design concentrates
  risk in one reset point; the AC7 regression is mandatory, and the design
  spec should consider passing records via return contract instead if the
  registry proves fragile.
- **Table provenance:** ISO/ANSI values are facts but transcription errors
  are real; spot-check against two independent published sources and
  version the data files so corrections are diffable.
- **Draft angle in OCCT** (`BRepOffsetAPI_DraftAngle`) fails readily on
  real parts — hence phase 3 and the fallback-with-warning contract;
  shipping rib/boss first is deliberate.
- **Metadata persistence shape** (manifest field vs sidecar): manifest
  keeps atomicity with part state but grows `project.json`; decide in
  design with a size measurement on a 50-hole part.
- **Flat-outline complexity:** multiple tabs, gaps, hems, and reliefs per
  edge multiply corner cases in the outline walker; property-based tests
  (outline is closed, CCW, area matches unfold) before feature growth.
- **Designation symbology variants** (ISO vs ASME depth/cbore symbols):
  emit per the hole's declared standard; document the mapping.

## As built

Fourteen slices, changelogs **0147–0160**. Every slice whose feasibility rested
on OCCT behaviour opened with a spike that measured it through the **kernel
worker** on bundled example parts; four of those measurements contradicted a
statement in this document or in the design spec, and each one is written down
below with the number that settled it rather than quietly worked around.

### Verification — one named test per criterion

`tests/test_prd010_acceptance.py` is the contract layer; the depth is in the
nine modules named beside it.

| AC | What it asks | Proving test | Verdict |
|---|---|---|---|
| **AC1** | the rewritten construction example is identical | `test_ac1_the_rewritten_construction_parts_are_byte_identical` · `tests/test_examples_golden.py::test_bundled_part_matches_golden` | **met, restated** — metrics at `rel=1e-9` **and a byte-identical `.acm`** on all three parts (0153). The cache-key half is impossible: `test_ac1_the_cache_key_necessarily_moved_and_the_prd_says_so` |
| **AC2** | tapped callout from metadata, not detection | `test_ac2_a_tapped_hole_reaches_the_drawing_as_its_designation` · `tests/test_drawing_holes.py::test_ac2_a_tapped_hole_prints_its_iso_designation` | **met** — the SVG carries `M5×0.8 - 6H ↧12` and `from_metadata: True` |
| **AC3** | `hole_standards` matches published ISO 273 | `test_ac3_hole_standards_returns_the_published_iso_273_diameters` · `tests/test_hole_standards.py` | **met** — M5 → 5.3 / 5.5 / 5.8, plus a tap-drill and a counterbore row, every row from two independent sources (0148) |
| **AC4** | partial flange → relief, fold/unfold, one verified SVG | `test_ac4_a_partial_flange_bracket_gets_relief_and_a_flat_pattern` · **`tests/test_sheetmetal_v2.py::test_ac4_partial_flange_bracket_exports_a_flat_pattern_with_reliefs`** (the SVG was rasterised and looked at — 0157) | **met** — `fold()` one valid solid at 6920.854 mm³, the two round reliefs remove 56.14 mm³, the bend line spans the 30 mm tab |
| **AC5** | a polar pattern is one group, callout reads `n× …` | `test_ac5_a_polar_pattern_of_one_tapped_hole_is_one_group` · `tests/test_drawing_holes.py::test_ac5_a_pattern_renders_one_callout_for_the_whole_group` | **met** — one record, `count: 8`, SVG `8× M5×0.8 - 6H ↧10` |
| **AC6** | misuse is honest | `test_ac6_a_hole_that_misses_the_part_warns_and_names_the_instance` · `test_ac6_impossible_geometry_is_a_script_error_with_the_failing_line` | **met** — the warning names the *index*; overlapping flanges and an unknown size are `script_error` with `details.line` |
| **AC7** | no cross-contamination on a warm worker | `test_ac7_two_parts_on_one_warm_worker_do_not_cross_contaminate` · `tests/test_hole_metadata.py` | **met, and now structural** — there is no shared mutable state to contaminate |
| **AC7b** | *(added by the design)* the same part twice is identical | `test_ac7b_the_same_part_twice_on_a_warm_worker_is_identical` | **met** — and the test asserts the second call really took the `_SHAPE_CACHE` path (`measured is False`) |
| **AC8** | full suite green; `examples/` untouched | `test_ac8_the_examples_tree_is_untouched_by_a_rebuild` · `test_ac8_the_full_suite_count_is_cited` | **met** — see changelog 0160 for the cited `make test` count |
| **FR14** | the UI emits a visible script call | `test_fr14_the_face_card_hole_controls_are_wired_into_the_shipped_frontend` · `tests/test_tools_holes.py` | **met, rescoped** — the existing face card, not PRD-016's shell (see below). Driven in a real browser in slice 13 (0159) |

### What could not be delivered as written

Six items. Four were found by the design spec before code was written; two by
a slice's spike. None is a refusal — each is a restatement with the
measurement that forced it.

1. **AC1's "the same content-hash mesh-cache entries" is impossible.** The
   cache key is `sha256({content, params, density, tolerance, format})` where
   `content` **is the script text** (`core/service.py`), so *any* rewrite mints
   a new `.cache/<key>.acm` filename by construction. And "identical geometry"
   was ambiguous: measured (0147), `gusset_plate` cut with
   `part - Compound(cylinders)` instead of `Locations` + `Hole` gives the same
   face count and a volume differing by a relative **2e-16** — and a different
   mesh (`56b50449…` vs `a00ec644…`). **Restated:** (a) every metric equal to
   the pre-rewrite golden at `rel=1e-9`, (b) a byte-identical `.acm` payload
   under the new key. Both hold, 3 of 3 parts, with no degraded half (0153).
2. **FR6's "helpers append to a per-build registry the worker drains" is
   wrong, not merely fragile.** Two caches skip `build(p)`: the worker's
   16-entry `_SHAPE_CACHE`, and the service's `.metrics.json` fast path, which
   makes **0 kernel calls** at all (measured, 0150). A drained registry would
   return nothing for every unchanged part — which is most of them — and the
   failure is *silence*: the drawing falls back to geometric detection and
   nobody sees a warning. **Replaced by:** records ride the returned shape
   (`holes.ATTR`), harvested by a `hole_records` handler and persisted in a
   `.cache/<key>.holes.json` sidecar. Measured through the worker: the
   attribute survives a cache hit (the same object), LRU eviction and rebuild,
   and `handle_build`'s tessellation. **AC7b** was added because the original
   AC7 cannot observe the regression this replaces.
   *Sub-divergence, measured in the same slice:* the design claimed records
   "compose along the helper chain". They do not — `safe_fillet`, `safe_bool`
   (both directions), a raw `part - tool` and even the helpers' own re-entered
   `BuildPart()` + `add(part)` all return an object with **none** of the
   original's attributes. Composition is something the code does:
   `fillet.py`/`shell.py`/`boolean.py` gained a `@holes.carries_records`
   decorator, a deliberate departure from the plan's file list.
   *Second sub-divergence (0151):* the seam harvests **before** the build, not
   after. With the harvest second it is always a shape-cache hit, its delta is
   always 0, and the drop warning is dead code — measured `measured: false` on
   three parts, every time. Harvest-first costs +3.0% on
   `prototyping/enclosure_base`, −0.2% on `engine/intake_manifold` and +17.5%
   on `construction/gusset_plate` (13 ms of fixed round trip on a small part).
3. **FR14's UI depends on PRD-016, which is unbuilt.** The hole dialog ships on
   the **existing face card** (`frontend/js/main.js`, the push/pull host):
   family / standard / size / fit / depth / positions and an Apply that calls
   the new `add_holes` tool, which resolves the picked ordinal into a literal
   `Plane(origin=…, x_dir=…, z_dir=…)` at edit time with the renumbering caveat
   written into the generated block. **The pattern dialog is deferred to
   PRD-016** — a pattern needs a direction/axis picker, which is what PRD-016
   is for. The `patterns.*` script vocabulary shipped without it, which is what
   the agent path needs.
4. **FR11's `teardrop` hem is refused, with numbers.** Spike S8 (0158) found
   the profile face itself is fine at every wrap angle — the self-intersection
   is with the **base plate**, and it is silent: at 225° with a 4t leaf the
   fuse returns one valid solid and **144.59 mm³** of declared material is
   simply gone. The longest non-penetrating leaf is 2.41·R at 225°, 1.00·R at
   270°, while a hem leaf needs ≥ 4t. `kind="teardrop"` raises a `ValueError`
   carrying those numbers. Likewise a *true* zero-radius closed hem: at
   `R = 0` the fold is still one valid solid of exactly the right volume but
   with **8 faces instead of 10** — the seam is gone and nothing distinguishes
   a closed hem from 2t of solid stock — so `inner_radius=0` is refused and the
   shipped radii (`R = t` open, `R = t/2` closed) are named as **shop
   defaults, not OCCT limits**.
5. **FR13's callouts are top-view only, inherited.** `_detect_circles` reads
   the top view, so a hole on a side face has a perfect record and no callout.
   Rather than partially patching it, the record is **named** in
   `detected.hole_warnings` — visible on a real part today
   (`construction/angle_bracket`'s vertical-leg group, 0153). Fixing it is
   PRD-014's job and it is documented in three places.
6. **Draft's ceilings are far below what the design's synthetics suggested,
   and its dominant failure does not raise.** Spike S6 (0156) swept 27 angles
   on eight shapes through the worker: monotone on **all eight**, no islands
   (so the binary search is sound), but `prototyping/enclosure_base` caps at
   **0.25°** — not the 2° the synthetic shelled box predicted — and
   `rocketry/nozzle` and `construction/angle_bracket` refuse **every** angle.
   Worse, only the extreme angles raise; most failing angles **return a shape**
   with `is_valid False` and a plausible volume. So `features.draft` validates
   every attempt, and when nothing works down to `min_angle` it returns the
   part **unchanged** with a warning — a rung the design did not have.

Two smaller ones, recorded so they are not rediscovered:

- **`holes.drill` is not in this PRD's FR list and had to exist** (0153).
  `gusset_plate` drills 18 mm for an M16 (EN 1090 structural clearance — none
  of ISO 273's 17.0/17.5/18.5) and its diameter is a swept *parameter*.
  Forcing it onto `holes.clearance` would have meant deleting a parameter from
  a bundled example or printing a standard's name on a number no standard
  supplied. `drill` takes millimetres and its record carries **no `size` and no
  provenance**.
- **The published counterbore charts disagree**, by up to 0.75 mm on one M8
  (0148). `hole_standards.cbore` therefore returns corroborated *head*
  geometry — the standard part of the answer — and applies a **named shop
  rule** to it for the bore, with the rule travelling in the record and in the
  tool's answer. It is not presented as a standard.

### The gotchas this PRD added to `AGENTS.md`

Every one is traceable to a measurement in 0147–0160: a misplaced OCCT cut is
a **silent no-op**; a floating rib is a *success* whose volume delta is exactly
right; draft's dominant failure returns `is_valid False` rather than raising;
records ride the shape because `_SHAPE_CACHE` skips `build(p)`; importing
worker **state** from a handler pack gets a second, empty copy (the worker runs
as `__main__`); sliding a workplane along a through-hole's axis is byte-free
while rotating about it is not; a 180° hem's air gap is `2R`; a >180° leaf is
swallowed silently. See **PRD-010 gotchas** in `AGENTS.md`.

## Competitive references

SolidWorks Hole Wizard + Toolbox define the expectation and the muscle
memory; template and standards depth is exactly what leavers report losing
(market_research.md, "The desktop incumbents"). Onshape ships Standard
Content; FreeCAD's fastener tooling is community-grade ("Open-source CAD").
Gap matrix verdict: **build**. We differ in three ways: helpers are script
vocabulary with the `safe_*` honest-warning contract rather than opaque
feature-tree nodes; the standards tables are themselves an agent-queryable
tool; and hole *intent* becomes machine-readable metadata that drawings
(PRD-014) and DFM (PRD-021) consume — incumbents know a hole's intent
inside the feature tree and lose it everywhere else; ours travels with the
reviewable script.
