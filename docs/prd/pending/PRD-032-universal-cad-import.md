# PRD-032 — Universal CAD import: native formats, tiered honestly

- **Status:** pending
- **Phase:** v6 — moats (Tier 1 additions can ride v5 with PRD-017)
- **Created:** 2026-08-09
- **Origin:** founder idea #7 (Aug 2026), engineering-reviewed; grounded by dedicated research (market_research.md, "Native CAD import")
- **Depends on:** PRD-017 (neutral-format pack — hard; this PRD extends it) · PRD-005 (org-level connector keys for cloud translation — soft) · PRD-022 (connector extension point — soft)
- **Related:** PRD-002 (re-import diffing as change review), PRD-011 (imported vendor parts as private packages)

## Problem & motivation

Founder idea #7: "support import from all existing CAD systems like
SolidWorks, Fusion, etc." Adoption depends on it — nobody rebuilds their
parts library to try a new tool, and every team has a folder of `.sldprt`
and vendor STEP files.

The research (market_research.md, "Native CAD import") defines what
"support" can honestly mean. Feature-history translation is impossible for
*everyone*: Onshape — with a Parasolid kernel and licensed translators —
imports SolidWorks files as featureless solids and says so; Fusion's
breadth is cloud translation to dumb solids too. Native B-rep access is
license-walled (SLDPRT's geometry is an embedded Parasolid stream; the
practical JT toolkit is gated; HOOPS Exchange starts ≥$50k/yr and cannot
ship in open source). Meanwhile OCCT already reads STEP AP203/214/242
(with names/colors/some PMI), IGES, BREP, glTF, OBJ, STL, VRML, PLY — and
per-conversion cloud services (Autodesk APS Model Derivative at ~0.1 token
per job; CAD Exchanger Cloud) cover the native long tail as an *optional,
clearly-labeled* connector. So: three tiers, no lies.

## Users & jobs

- **Adopting engineer:** bring the existing zoo of files in, see exactly
  what survived, and keep working.
- **Team with vendor data:** import supplier STEP/native files as
  reference parts and mate-ready private packages (PRD-011).
- **Agent:** ingest a customer file, report the import fidelity, and
  propose next steps (re-parametrize critical parts via PRD-022's scan/
  mesh assist; wrap as package; or use as-is).
- **Reviewer:** when a vendor re-sends a revised file, see a geometric
  diff against the previous import (PRD-002 machinery), not a guess.

## Goals

- G1. **Tier 1 — built-in, OSS-clean (extends PRD-017):** deep STEP
  AP203/214/242 (assembly structure, names, colors, PMI where present —
  semantic vs graphical reported), IGES, BREP; mesh formats OBJ, PLY,
  glTF (+ existing STL, 3MF via lib3mf); DXF profiles (ezdxf) as sketch/
  drawing references.
- G2. **Tier 2 — external gratis binary, opt-in:** DWG⇄DXF via a
  user-installed ODA File Converter subprocess (the FreeCAD pattern);
  LibreDWG documented as the fully-free fallback.
- G3. **Tier 3 — hosted conversion connectors, opt-in per org:** SLDPRT/
  SLDASM, CATPart/CATProduct, NX/Creo .prt, IPT/IAM, JT, 3DM via a
  configured cloud converter (APS Model Derivative or CAD Exchanger
  Cloud), with unmistakable "this file leaves your machine" consent,
  per-file cost surfaced before conversion.
- G4. **An import fidelity report** on every import, machine- and
  human-readable: bodies found, structure preserved, names/colors, PMI
  (semantic/graphical/none), units, what was dropped — the honesty
  contract.
- G5. **Re-import as change:** importing a new revision of a previously
  imported file produces a geometric diff (volume deltas, moved/added/
  removed bodies) reviewable as a proposal (PRD-002).
- G6. The explainer shipped in docs and UI: why feature history cannot
  cross tools (nobody's can), and the recommended "export STEP AP242"
  path with its PMI caveats.

## Non-goals

- Bundling proprietary SDKs (HOOPS/Parasolid/JT Open) in the open-source
  distribution — licensing excludes it.
- Feature-tree/sketch/constraint translation from any native format —
  impossible in the general case; scan-assist re-parametrization
  (PRD-022) is the offered path where parametric editing is required.
- 2D drawing *sheet* reconstruction from DWG/DXF (import as reference
  geometry only at this stage).
- Point clouds (later; belongs with scan workflows).

## Experience

**Human path.** The import dialog (PRD-026) accepts the full extension
list; Tier-3 extensions show the consent panel (destination service, cost
estimate, privacy note) if a connector is configured — or an explainer
("SolidWorks native files need a conversion connector — or export STEP
AP242 from SolidWorks; here's how") if not. After import, the fidelity
report renders as a panel: green rows (12 bodies, names, colors), amber
(graphical-only PMI), gray (feature history — never translates, link to
explainer). The assembly arrives structured (PRD-017), parts land as
reference parts exactly like today's STEP path.

**Agent path.** `import_cad_file` grows: accepted formats by tier,
`{consent: true}` required for Tier 3 (never implicit), and the fidelity
report in the result. `reimport_part {part_id, source}` produces the
geometric diff and (optionally) opens a proposal. The agent narrates
fidelity honestly: "imported 12 solids; PMI was graphical-only — 
tolerances need re-entry (or set_part_pmi from the drawing PDF)."

## Functional requirements

- FR1. Tier-1 format matrix lands in the reference loader: OBJ/PLY/glTF
  readers (OCCT toolkits) join STL as mesh-kind; DXF (ezdxf) imports as
  2D reference geometry attachable to sketches; STEP structure/PMI depth
  per PRD-017. Every format gains a loader test fixture.
- FR2. Fidelity report schema `{format, bodies, structure: kept|flattened,
  names, colors, units, pmi: semantic|graphical|none, dropped: […]}`
  returned by `import_cad_file` and persisted with the reference part
  (manifest additive key), rendered in UI.
- FR3. Tier 2: ODA File Converter discovery (configured path), subprocess
  isolation with timeout, DWG→DXF→import pipeline; absence = actionable
  error with install pointer (capability-honest).
- FR4. Tier 3 connector interface (PRD-022's extension point): configured
  per org (PRD-005 keys) or locally (env); explicit consent parameter +
  UI consent panel; cost estimate surfaced pre-flight when the API
  provides it; converted intermediate (STEP) cached so re-opens don't
  re-convert (content-hash keyed).
- FR5. Consent and audit: Tier-3 conversions logged (who, file hash,
  destination, when) in the audit trail (PRD-005); never auto-triggered
  by agents without the consent parameter, which agents must surface to
  the human (etiquette enforced by tool description + review).
- FR6. Re-import: `reimport_part` matches bodies between old and new
  (geometric matching — centroid/volume/hash heuristics), computes
  per-body added/removed volumes (the PRD-002 boolean diff), and updates
  the reference part atomically; result includes the diff summary.
- FR7. Mesh-kind rules preserved: imported meshes stay measure/display
  (no booleans — the OCCT segfault rule); the report says so.
- FR8. Size/complexity budgets: existing 100 MB upload cap revisited per
  tier (Tier-3 outputs can be large); import runs under worker timeouts
  with the standard structured errors.

## Agent surface

Changed: `import_cad_file {project, source, part_id, label?, material?,
consent?}` — extended formats, consent gate, fidelity report in result.
New: `reimport_part {project, part_id, source, propose?}` ·
`list_import_connectors {}` (configured Tier-3 services + formats).
Errors: `connector_required {format, how_to_configure}` ·
`consent_required`.

## Technical approach

- **Kernel:** `refload.py` gains the Tier-1 readers (OCCT RWObj/RWPly/
  RWGltf toolkits; ezdxf parsing server-side into edge geometry passed to
  the kernel); the LRU and mesh-kind discipline unchanged.
- **Server:** Tier-2 subprocess wrapper + Tier-3 connectors live
  server-side (network is server-tier by design — workers stay
  networkless per PRD-006); conversion cache under the project's
  `imports/.converted/` keyed by source hash.
- **Packs:** extends the existing import tool/route packs; connector
  configs ride PRD-022's extension point; fidelity persistence is a
  manifest additive key.
- **Diffing:** body matching + boolean deltas reuse the PRD-002 geometric
  diff kind in the analysis handler pack.
- **Docs:** the honest explainer page; per-source-CAD "how to export STEP
  AP242 well" guides (SolidWorks/Fusion/Creo/NX specifics from research).

## MVP & phasing

- **MVP (can ship with PRD-017):** Tier-1 additions (OBJ/PLY/glTF/DXF) +
  fidelity report + explainer docs.
- **Phase 2:** Tier 2 (ODA converter path); re-import diffing.
- **Phase 3:** Tier 3 connectors (one service first — APS or CAD
  Exchanger, chosen by cost/coverage in design), org keys + consent +
  audit; private-package wrapping of imported vendor parts (PRD-011).

## Acceptance criteria

- AC1. An OBJ and a glTF part import as mesh-kind references with correct
  units/scale; a PLY scan displays and measures; all excluded from
  booleans with the standard skip reporting (tests + fixtures).
- AC2. A STEP AP242 file with semantic PMI reports `pmi: semantic`; the
  same geometry exported from a tool that drops to graphical annotations
  reports `pmi: graphical` (fixture pair test).
- AC3. With a mock Tier-3 connector: `.sldprt` import without `consent`
  fails with `consent_required`; with consent, the converted STEP lands,
  the fidelity report renders, and the audit row exists (integration
  test with a stub service).
- AC4. `reimport_part` on a revised fixture reports one grown body with
  its added volume and updates the reference part; with `propose: true`
  a PRD-002 proposal carries the diff (test, staged for when 002 lands).
- AC5. Without ODA configured, DWG import returns the actionable
  `connector_required` error naming the install path (test).
- AC6. The import dialog shows tier-appropriate consent/explainer states
  (browser-verified); docs explainer published.
- AC7. Full suite green; existing STEP/BREP/STL import behavior
  unchanged (regression tests).

## Risks & open questions

- **Tier-3 vendor choice** (APS vs CAD Exchanger Cloud): decide on
  format coverage, per-conversion cost, ToS compatibility with our
  proxying, and EU data residency; the connector interface keeps it
  swappable.
- **DXF scope creep** (entities beyond profiles): constrain MVP to
  closed polylines/arcs/circles as sketch references; log the rest in
  the fidelity report.
- **Body-matching heuristics** for re-import can mismatch on mirrored/
  duplicated bodies — expose match confidence, allow manual pairing in
  the diff UI.
- **User expectation management** remains the real risk: the fidelity
  report and explainer must land *before* Tier 3, or "import worked but
  my features are gone" becomes a trust bug. Sequencing enforces this.

## Competitive references

Onshape's licensed server-side translators (broadest, still featureless
imports, honestly documented); Fusion's cloud translation; HOOPS/ODA/
CAD Exchanger/Datakit as the tooling market; STEP AP242 as the neutral
best target (market_research.md, "Native CAD import"). We differ: an
explicit fidelity contract on every import, open Tier-1 breadth by
default, consent-gated cloud conversion instead of silent uploads, and
re-import as a reviewable geometric change — plus the only honest
explainer in the industry about what import can never do.
