# PRD-014 — Drawings v2: the standards wrapper

- **Status:** pending
- **Phase:** v5 — daily-driver depth
- **Created:** 2026-08-09
- **Origin:** competitive analysis (Aug 2026)
- **Depends on:** PRD-015 (hard — BOM lines feed tables/balloons) · PRD-010 (soft — hole-wizard metadata feeds hole tables) · PRD-012 (soft — config dimension tables) · PRD-001 (soft — version-keyed determinism fields)
- **Related:** PRD-002 (drawings as proposal diff content), PRD-004 (drawings as CI artifacts), PRD-013 (assembly structure behind assembly views), PRD-023 (docs reuse sheet output)

## Problem & motivation

Today's drawings are correct but naked: `generate_drawing` projects
front/top/right/iso views with detected overall dimensions and hole callouts,
renders PMI (tolerance suffixes, boxed datum flags, feature control frames)
on the SVG, and writes `exports/<part>_drawing.svg|.dxf` — with only the
minimal title strip the drawing handler already draws. There are no sheet
formats, no real title block with project/revision fields, no assembly
drawings, no sections or details, no centerlines, no hole tables, and no PDF.
A machine shop receiving one sees a projection, not a drawing — and "shops
reject drawings that don't look standard" is the reason this feature exists.

The competitive evidence: SolidWorks' decades of drawing-template depth is a
stated moat, and the incumbents are raising the automation baseline — SW2026
ships one-click AI drawing generation, Solid Edge 2026 auto-generates ~80% of
views/dimensions (market_research.md, "The desktop incumbents"). Onshape
proves demand and the cost of getting it wrong: "drawings so slow they are
unusable" is a real forum thread title ("Cloud-native CAD: Onshape"). The gap
matrix rows "Assembly drawings, BOM, balloons, title blocks" and part of
"Interop" are verdict **build**. None of the incumbents regenerate drawings
*deterministically from the model on every change* — their drawings are
documents that drift. Ours can be compiled artifacts: same version ⇒ same
bytes, which makes drawings CI-checkable (PRD-004) and diffable in proposals
(PRD-002). That inversion — drawing as build output, not document — is the
differentiated half.

## Users & jobs

- **Design engineer (human):** produce a shop-submittable sheet (title
  block, revision, sections, hole table) without a drafting pass in another
  tool.
- **Machine shop / supplier (external human):** receive an A3/A-size sheet
  that reads as standard — frames, title block, balloons matching the BOM —
  as PDF.
- **Release manager (human, PRD-015):** trust that the drawing in a release
  bundle matches the tagged geometry exactly, because it was regenerated
  from it.
- **Design agent:** regenerate drawings after every change as part of the
  loop; a proposal (PRD-002) carries the drawing diff as evidence.
- **CI (automation, PRD-004):** treat drawings as build artifacts — assert
  byte-stability at a version and flag silent geometry changes.

## Goals

- G1. Sheets: ISO (A4–A0) and ASME (A–D) formats with frame and title block
  populated from project fields, part data, and version/revision identity.
- G2. Assembly drawings: projected assembly views with balloons keyed to a
  BOM table on the sheet, fed by PRD-015's BOM lines.
- G3. Section and detail views with hatching, labeled A-A/B, built on the
  kernel's existing cross-section machinery.
- G4. Drafting furniture that shops expect: centerlines and center marks on
  detected/known holes; hole tables driven by PRD-010 hole metadata.
- G5. Config dimension tables (PRD-012): one tabulated drawing for a family.
- G6. PDF export alongside SVG/DXF, and byte-stable regeneration at a fixed
  version so drawings are CI artifacts and proposal diff content.

## Non-goals

- An interactive drawing editor (drag dimensions, add annotations by hand) —
  drawings stay derived; annotation intent enters via PMI (`set_part_pmi`)
  and drawing options, not a 2D canvas.
- Full ASME Y14.5 auto-dimensioning coverage — v1 dimensions what it can
  detect plus PMI-declared dims; completeness grows by evidence, not by
  checkbox.
- DXF parity with SVG/PDF — DXF stays the geometry-exchange output (it
  already ignores PMI, v1); title blocks and tables target SVG/PDF.
- Weldment cut lists — with frames/weldments when that feature lands.
- Print/plot management, sheet scales per viewport dragged by mouse — out.

## Experience

**Human path.** The drawing preview (`drawings.js`, today's in-app SVG
preview) grows a header: sheet format picker (A4/A3/ANSI-A/…), view
checkboxes plus "Section…" (pick plane XY/XZ/YZ + offset, or a face from the
viewport selection) and "Detail…" (pick a center + radius), and an Export
menu with PDF next to SVG/DXF. Project-level title-block fields (company,
project code, author, approval) are edited once in a small "Drawing fields…"
dialog and stored in the manifest. An assembly drawing appears when the
Assembly node is selected: views of the placed assembly, balloons, and the
BOM table. Revision and version cells fill themselves from PRD-015/PRD-001
state — "-" and the working-state hash before any tag exists.

**Agent path.** `generate_drawing` keeps its name and grows: omit `part_id`
for the assembly sheet (the same convention `render_view` uses), pass
`sheet`, `sections`, `details`, `format: "pdf"`. `set_drawing_fields` writes
the title-block fields. The tool returns what it drew — views, detected and
PMI dims, balloon↔BOM mapping, hole-table rows — so an agent can verify
coverage ("is every bolt ballooned?") without reading pixels.

**Handoff.** An agent regenerates the sheet after a geometry change; the
human eyeballs the preview; PRD-002 shows the SVG diff in review; PRD-015
freezes the PDF into the release bundle.

## Functional requirements

**Sheets & title blocks**
- FR1. `sheet` selects `iso_a4|iso_a3|iso_a2|iso_a1|iso_a0|ansi_a|ansi_b|
  ansi_c|ansi_d` (landscape), drawing frame included; default `iso_a3`.
  Views auto-scale to a standard scale (1:1, 1:2, 1:5, 2:1, …) chosen to fit;
  the chosen scale prints in the title block.
- FR2. The title block renders: part id + label, project name, material,
  mass (from cached metrics), units (mm), scale, sheet size, author/company/
  project-code fields from the manifest's `drawing` section, revision (from
  PRD-015; "-" before first release), version ref (PRD-001 tag name, else
  short state hash), and date = the *version's* commit date, never
  wall-clock (determinism).
- FR3. A revision block (top-right, ISO style) lists released revisions with
  date and description from PRD-015 release records; empty before the first
  release.

**Assembly drawings**
- FR4. With `part_id` omitted, views project the placed assembly (mate-
  resolved transforms, sub-assemblies flattened per PRD-013); hidden-line
  removal at the assembly level; per-view `explode?: factor` uses PRD-013's
  exploded offsets.
- FR5. Balloons: one numbered balloon per BOM line (not per instance),
  leader to a representative instance silhouette; numbers match the sheet's
  BOM table rows exactly. The BOM table (item, qty, part number, name,
  material) comes from PRD-015's `get_bom` for the drawn config.

**Sections & details**
- FR6. `sections: [{plane: xy|xz|yz, offset_mm, label?}]` produces section
  views: the solid cut by the plane (the analysis pack's section machinery
  extended to return section outlines, not just area), cut faces hatched at
  45°, view labeled `A-A` with cutting-plane arrows drawn on the parent
  view. Multi-solid/assembly sections hatch per body with alternating
  angles.
- FR7. `details: [{view, center_mm: [x, y], radius_mm, scale}]` draws a
  labeled circle on the parent view and a magnified detail view at the given
  scale.

**Centerlines, hole tables, config tables**
- FR8. Detected circular features (today's `detected.diameters_mm` path) and
  PRD-010 hole-metadata features get center marks; coaxial hole runs in side
  views get centerlines (thin chain linetype).
- FR9. When PRD-010 hole metadata exists, a hole table renders: tag (A1,
  A2…), X/Y from the view datum corner, and the standard designation
  (e.g. `M5x0.8 - 6H ⌴ Ø9.5×5.4`); tags print at each hole. Without
  metadata the table falls back to detected diameter groups, marked as
  detected.
- FR10. For a part with PRD-012 configurations, `tabulate: true` renders
  PMI-declared dims as letter variables (A, B, …) and a config table
  (config × variable values, per-config mass); the drawn views use the
  active config.

**Formats & determinism**
- FR11. `format: pdf` produces vector PDF of the same sheet (one page per
  sheet); SVG and DXF keep working; PMI renders in SVG and PDF, DXF ignores
  it (unchanged v1 behavior).
- FR12. Regeneration is byte-stable: two runs at the same project state
  produce identical SVG and PDF bytes (fixed float formatting, no
  wall-clock, no random ids; PDF object ordering and `CreationDate` derived
  from the version date). This is a tested guarantee, not an aspiration.
- FR13. Tool results are machine-readable: `{path, size_bytes, sheet, scale,
  views, sections, detected, pmi_rendered?, balloons?: [{item, instance,
  part}], hole_table?: rows, config_table?: rows, warnings}`.

## Agent surface

Changed: `generate_drawing {project, part_id?, views?, sections?, details?,
sheet?, scale?, tabulate?, explode?, format?}` — part_id omitted = assembly
sheet; result per FR13.
New: `set_drawing_fields {project, fields}` (validated whitelist: company,
author, project_code, approved_by, notes) · `get_drawing_fields {project}`.
Routes: the existing preview route gains the assembly form
(`GET /api/projects/<proj>/drawing.svg`) and a `?format=pdf` download; the
part preview route is unchanged.
Errors: house contract — `validation_error` for bad sheet/section specs
(details name the offending entry); a section plane missing the solid
entirely returns a warning and an empty view, never a silent blank sheet.

## Technical approach

- **Worker handler pack:** `handlers/drawing.py` (already a pack) is the
  center of gravity — sheet templating, balloons, tables, and section/detail
  composition extend `_build_svg`; section geometry comes from a new
  `section_outline` handler in the analysis pack (the `kind=section` plumbing
  grown to return outline polylines + per-body loops for hatching). Assembly
  projection reuses the polymorphic per-instance shape loading the
  interference/export handlers already do.
- **Sheet templates as data:** frame + title-block geometry per format in a
  versioned Python/JSON table inside the pack — not user-editable files in
  v1 (custom templates are an open question below).
- **PDF backend:** SVG-structure → PDF via a small deterministic writer (the
  drawing pack already owns all geometry as primitives; emitting PDF
  operators directly avoids an SVG→PDF dependency with nondeterministic
  output). Pinned, pure-Python; `CreationDate` from the version timestamp.
- **Tool/route packs:** `tools_drawing.py` and `routes_drawing.py` extended
  in place (they are packs); BOM lines come from PRD-015's service seam, hole
  metadata from PRD-010's manifest section, config sets from PRD-012 —
  each degrades gracefully when the upstream PRD hasn't landed (FR9
  fallback; FR5 assembly drawings require PRD-015).
- **Manifest:** a `drawing` section (fields per FR2); merged key-wise by
  PRD-001's driver.
- **Frontend:** `drawings.js` grows the sheet/section/detail controls and
  PDF download; no new modules.
- **Determinism:** shared float-formatting helper; a regression test
  regenerates twice and compares sha256 for SVG and PDF (the same check
  PRD-004 runs in CI).

## MVP & phasing

- **MVP:** ISO A4/A3 + ANSI A/B sheets with title block and fields
  (FR1–FR2), section views on parts (FR6), centerlines/center marks (FR8),
  PDF export (FR11), determinism guarantee + test (FR12), machine-readable
  results (FR13).
- **Phase 2 (with PRD-015):** assembly drawings with balloons + BOM table
  (FR4–FR5), revision block (FR3).
- **Phase 3 (with PRD-010/PRD-012):** hole tables (FR9), config tabulation
  (FR10), detail views (FR7), exploded assembly views.

## Acceptance criteria

- AC1. The construction gusset produces an A3 ISO sheet: frame, populated
  title block (material, mass, scale, version ref), a sectioned view labeled
  A-A with hatching, and center marks on the bolt holes — verified by test
  asserting the SVG contains each element, plus a browser-preview
  screenshot.
- AC2. Byte-stability: regenerating that sheet twice at the same project
  state yields identical sha256 for SVG and PDF; mutating a param then
  restoring the snapshot (PRD-001 `project_restore`) reproduces the original
  bytes (test).
- AC3. The rocketry assembly sheet shows one balloon per BOM line with
  numbers matching the on-sheet BOM table rows one-to-one, and the tool
  result's `balloons` maps every BOM item (test, once PRD-015 lands).
- AC4. With PRD-010 metadata on a tapped hole, the hole table prints the
  correct designation and per-hole tags; without metadata the table appears
  in detected-fallback form with diameters only (two tests).
- AC5. A three-config flange (PRD-012) with `tabulate: true` renders letter
  dims and a config table whose values match `get_bom`/`build_configs`
  outputs (test, once PRD-012 lands).
- AC6. PDF opens in standard viewers (CI: a strict PDF parse of structure +
  page count; manual: Preview/Acrobat screenshot); SVG preview in the
  browser shows the same sheet with zero console errors.
- AC7. Full suite green; existing `generate_drawing` calls without new
  arguments produce the same output as today plus the default sheet wrapper
  (golden files updated once, then stable).

## Risks & open questions

- **Assembly hidden-line removal cost:** HLR over many instances is the
  classic slow path (Onshape's forum thread is the cautionary tale). Budget
  it (per-call timeout, simplified-rep option from PRD-013), cache per
  version, and measure on the rocketry example before promising more.
- **PDF determinism** is easy to lose (library upgrades, locale float
  formatting). Owning a minimal writer keeps it in our hands but costs
  maintenance; the determinism test is the tripwire either way.
- **Standard-scale auto-selection** may pick a scale that crowds dims;
  expose `scale` override and report the chosen scale so agents can retry.
- **Custom title-block templates** (per-company layouts) are a real demand
  we defer; the fields whitelist plus data-driven templates leaves the door
  open. Decide the user-template format when demanded.
- **Section hatching of imported reference parts** (no script, possibly
  dirty solids): hatch what sections cleanly, warn on the rest —
  `skipped_mesh` semantics carried over for STL.
- **Balloon placement quality** (leader crossings) is a layout problem;
  v1 uses a deterministic greedy placement and accepts imperfection —
  aesthetics iterate behind the stable data contract.

## Competitive references

SolidWorks: decades of drawing templates and shop trust — the depth moat we
wrap with standards-correct sheets rather than replicate feature-by-feature;
SW2026 and Solid Edge 2026 ship AI-generated drawings, raising the baseline
(market_research.md, "The desktop incumbents"). Onshape: drawings exist but
"so slow they are unusable" threads persist ("Cloud-native CAD: Onshape").
Nobody regenerates deterministically from the model — their drawings drift
from geometry and get re-checked by hand (Onshape Labs is *promising* an AI
drawing checker). We differ by: drawings as compiled, byte-stable artifacts
of the version — diffed in proposals (PRD-002), gated in CI (PRD-004), and
frozen into releases (PRD-015) — with the drawing checker role reduced to a
spec.
