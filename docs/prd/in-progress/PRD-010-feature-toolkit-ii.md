# PRD-010 — Feature toolkit II: patterns, hole wizard, sheet-metal v2

- **Status:** pending
- **Phase:** v5 — daily-driver depth
- **Created:** 2026-08-09
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
