# PRD-017 — Interop pack: neutral formats done right

- **Status:** in-progress
- **Phase:** v5 — daily-driver depth
- **Created:** 2026-08-09
- **Origin:** competitive analysis (Aug 2026)
- **Depends on:** — (extends existing import/export directly)
- **Related:** PRD-007 (glTF feeds share links/embeds), PRD-013 (deep import trees → sub-assemblies; URDF is the articulated export), PRD-014 (drawings travel with STEP in supplier packages), PRD-015 (part numbers/metadata stamped into exports), PRD-022 (3MF feeds the print pipeline), PRD-032 (native CAD formats — explicitly *not* this PRD)

## Problem & motivation

AgentCAD's exchange surface is functional but dated: `export_part` writes
STEP/STL/3MF (geometry only — no PMI, no metadata, no colors),
`export_assembly` writes STEP/STL, and `import_cad_file` ingests a
STEP/BREP/STL as **one** reference part no matter how many products the file
contains — a 40-component vendor assembly arrives as a single unnamed blob
with no instance tree. The GD&T a user modeled with `set_part_pmi` renders
on our drawings and then dies at the export boundary: the STEP a supplier
receives carries none of it.

The format landscape settled in 2025–26 and the evidence is specific
(market_research.md, "The workflow ring", formats bullet): **STEP is
authoritative, 3MF is print's native format (ISO-standardized June 2025;
the slicer lineage reads it natively), glTF is web review, USD is rising
(Core Spec 1.0, ISO track), STL is legacy.** The gap matrix row "Interop:
STEP AP242 PMI, 3MF, glTF, USD" is verdict **build** against the NX/HOOPS
ecosystem benchmark. AP242 PMI is what makes our tolerance model survive the
trip to suppliers — the credible manufacturing handoff the analysis settled
on is "clean STEP + PMI + standards-correct drawings + DFM-checked quotes"
("The workflow ring", CAM bullet), not toolpaths. And import fidelity is
agent leverage: a structured product tree gives agents real instances to
reason over and re-parametrize piece by piece (PRD-018's assist), where a
blob gives them nothing.

This PRD is **neutral formats only**. Reading proprietary native formats
(SLDPRT, F3D, CATPart, …) is a different problem with a different strategy —
that is PRD-032, explicitly.

## Users & jobs

- **Design engineer (human):** send a supplier one STEP that carries the
  tolerances; import a vendor assembly and see its real structure, not a
  blob.
- **Maker / print user (human):** export a 3MF the slicer opens with
  correct units, name, and colors — no STL scale roulette.
- **Reviewer / stakeholder (human, PRD-007):** view models on the web via
  glTF without a CAD install.
- **Design agent:** walk an imported product tree (`get_assembly` over real
  instances), measure and mate against named reference parts, and export
  artifacts whose metadata (part numbers, materials) it stamped itself.
- **Sim/twin toolchains (external, flagged):** consume USD where that
  ecosystem already lives.

## Goals

- G1. STEP AP242 export attaches the existing PMI model (dims, datums,
  FCFs) so GD&T survives to any AP242-aware consumer.
- G2. 3MF export carries metadata (title, designer, description, part
  number), explicit millimeter units, and per-instance/solid colors —
  slicer-native, ISO-conformant.
- G3. glTF/GLB export of parts and placed assemblies — the web-review
  artifact PRD-007 streams.
- G4. Structured assembly-STEP import: product tree → deduplicated
  reference parts + placed instances with names, transforms, and colors —
  replacing today's single-blob import.
- G5. USD export behind an optional extra for the digital-twin ecosystem,
  registered only when runnable (the FEM precedent).
- G6. Honesty about fidelity: every export/import states what survived
  (geometry, PMI, colors, metadata) and what cannot (parametric intent) —
  in results and docs, not fine print.

## Non-goals

- Native proprietary formats (read or write) — PRD-032, with its own
  strategy and risks.
- STEP AP242 *kinematics* (exporting mates as STEP kinematic pairs) — URDF
  is our articulated handoff (PRD-013); revisit only on real demand.
- PMI *import* from STEP — v1 imports geometry + structure + colors;
  reading foreign semantic PMI is a later slice (noted in phasing).
- glTF import, USD import — export-only in this PRD.
- Drawing-format work (DXF/SVG/PDF) — PRD-014's territory; already ship.
- Mesh repair / reverse engineering of imported geometry — PRD-018/PRD-022
  assist paths.

## Experience

**Human path.** The Export menu grows: Part → STEP (with a "include PMI"
default-on checkbox), 3MF, glTF; Assembly → STEP (structured product tree),
3MF, glTF/GLB; USD appears only when the extra is installed. Import is where
the feel changes: uploading a multi-product STEP now shows an import preview
— the product tree with counts ("14 products, 41 occurrences") and a prefix
field — and lands as N reference parts plus placed instances in the Assembly
tree, named from the STEP labels, colored as authored. The old behavior (one
blob) remains available as an "import flat" toggle for genuinely monolithic
files.

**Agent path.** `export_part {format: "step", pmi: true}` /
`{format: "gltf"}` / `{format: "3mf", metadata: {...}}`;
`export_assembly {format: "step", structured: true}`.
`import_cad_file {structured: true}` returns the mapped tree —
`{parts: [...], instances: [...], tree, warnings}` — so the agent
immediately knows what exists and can `set_mate`/`measure` against it.
Every result carries a `fidelity` block naming what was written or dropped
(e.g. `{"pmi": "attached", "colors": "per_instance", "parametric": "none"}`).

**Handoff.** An agent imports the vendor STEP structured, reports the tree;
the human eyeballs the assembly; the agent mates our parts to named vendor
instances; the release bundle (PRD-015) exports AP242+PMI back out.

## Functional requirements

**STEP AP242 + PMI export**
- FR1. `export_part {format: "step"}` writes AP242 with the part's PMI
  section attached as semantic representation via OCCT XCAF
  (dimensions with plus/minus, datums, feature control frames — the same
  normalized model `set_part_pmi` validates); `pmi: false` opts out.
  Parts without PMI export exactly as today.
- FR2. `export_assembly {format: "step", structured: true}` writes a real
  product structure: one product per part, occurrences with transforms,
  instance names and colors from the manifest — not the current
  fused/flat compound. `structured: false` preserves today's behavior.
- FR3. What cannot attach is reported, never dropped silently: PMI entries
  that fail to map (e.g. an FCF type without an AP242 representation in our
  writer) are listed in `fidelity.pmi_skipped` with reasons.

**3MF v2**
- FR4. 3MF export writes core-spec metadata (`Title`, `Designer`,
  `Description`, `CreationDate` from the version date for determinism,
  part number from PRD-015 fields when present), explicit millimeter units,
  and colors: per-instance for assemblies, per-solid (from
  `set_solid_materials`-driven categories) for multi-solid parts.
- FR5. The output is a conformant OPC package — validated structurally
  (OPC parts + core-namespace XML asserts, in tests; an XSD validator would
  be a new dependency) and re-read via lib3mf, the 3MF Consortium's own
  reader — and opens in the PrusaSlicer/Bambu/Orca lineage with correct
  scale and names (manual verification once per release).

**glTF / GLB**
- FR6. `export_part`/`export_assembly` gain `gltf` (JSON+bin) and `glb`
  formats: meshes from the existing ACM cache (server-side conversion — no
  kernel involvement), Z-up→Y-up conversion stated in the asset metadata,
  per-instance nodes with transforms and colors, PBR materials derived from
  material categories. Output passes glTF 2.0 validation and loads in the
  vendored Three.js loader.
- FR7. Determinism: same version ⇒ byte-identical glTF/GLB (stable node
  ordering, fixed float formatting) — share links (PRD-007) and CI
  (PRD-004) may cache by content hash.

**Structured assembly-STEP import**
- FR8. `import_cad_file {structured: true}` (default when the file contains
  more than one product occurrence) reads the XCAF document: each unique
  product → one reference part (deduplicated — 8 occurrences of one screw
  yield 1 part), each occurrence → one assembly instance with its transform
  and color; names sanitized to part-id rules with a `prefix?` arg,
  collisions suffixed; the original labels kept in part metadata.
- FR9. The result reports the mapping: `{parts, instances, tree, warnings,
  fidelity}`; `structured: false` forces today's single-blob behavior.
  Existing single-product files behave exactly as today with either
  setting.
- FR10. Deep trees flatten to one instance level in v1 (transforms
  composed); once PRD-013 lands, `structured: "nested"` maps sub-trees to
  sub-assembly sources instead. Imported instances are placeable, matable
  as anchors are not (reference parts declare no connectors — unchanged
  rule), boolean/interference-capable per existing STEP semantics.

**USD (flagged)**
- FR11. `agentcad[usd]` extra enables `usd` export (assembly → one stage;
  meshes, transforms, colors, mm-to-meter scaling stated); the tool and
  format enum entry register only when the dependency imports — absent
  otherwise, per the FEM precedent (agents never see a tool that cannot
  run).

**Cross-cutting**
- FR12. Every import/export result carries `fidelity` (FR3/FR9 shape);
  documentation states the translation matrix plainly: exact B-rep — STEP
  only; PMI — STEP AP242 only; tessellation + colors — 3MF/glTF/USD;
  model metadata (title, designer, part number, creation date) — **3MF
  only**, glTF/USD carry no metadata beyond a generator/up-axis breadcrumb;
  parametric intent (PARAMS, scripts, constraints, specs) — survives in
  **no** neutral format, by nature of the formats.

## Agent surface

Changed: `export_part {project, part_id, format: step|stl|3mf|gltf|glb|usd,
tolerance?, pmi?, metadata?}` · `export_assembly {project, format:
step|stl|3mf|gltf|glb|usd, structured?}` · `import_cad_file {project,
source, part_id?, label?, material?, structured?, prefix?}` (`part_id`
optional when structured — ids derive from product names; required for
flat).
No new tools; the surface stays small and the formats ride existing verbs.
Errors: `validation_error` for malformed/unreadable files with
`details.stage` (parse vs map); oversized imports keep the existing 100 MB
guard. Events: imports publish the existing `project_changed`.

## Technical approach

- **Kernel handler packs** (only the kernel imports OCP): the `reference`
  pack and `refload.py` grow an XCAF path — `STEPCAFControl_Reader` for
  structure/names/colors on import (keeping the content-addressed LRU;
  cache key gains the structured flag), `STEPCAFControl_Writer` +
  `XCAFDoc_DimTolTool` for AP242 PMI and product-structure export. A new
  `handlers/interop.py` carries the export kinds; 3MF metadata rides the
  existing 3MF path with an OPC post-step if OCCT's writer can't stamp
  fields directly (decide in design with a spike — risk below).
- **Server-side glTF:** `agentcad/core/gltf.py` converts cached ACM1 meshes
  + manifest transforms/colors to glTF/GLB — pure Python, no OCP, cheap to
  make deterministic (FR7). USD similarly via `usd-core` in
  `core/usd_export.py`, guarded by the extra.
- **Tool/route packs:** `tools_import.py` and `tools.py`'s export tools are
  extended in their packs where they live today (`tools_import.py` is a
  pack; the core export tools gain enum members via a small
  `tools_interop.py` pack that re-registers extended schemas rather than
  editing the core); `routes_import.py` gains the tree-preview endpoint the
  import dialog uses.
- **Manifest:** reference parts gain `source_label` (original STEP name)
  and imported-instance provenance; PRD-015's `part_number` and PRD-001's
  version date feed metadata stamps. Schema-tolerant: old manifests load.
- **Frontend:** import dialog with tree preview (`tree.js` + a small
  modal); Export menu entries; nothing else.
- **Tests:** golden multi-product STEP fixtures (authored via build123d in
  the suite, so no binary blobs in-repo); PMI self-round-trip (export
  AP242 → re-import via our XCAF reader → assert dim/datum/FCF survival);
  glTF validation; 3MF schema validation; determinism sha-tests.

## MVP & phasing

- **MVP:** structured assembly-STEP import (FR8–FR9), glTF/GLB export with
  determinism (FR6–FR7), AP242 PMI export for dims/datums (FR1, FCF
  coverage grows), `fidelity` reporting (FR12).
- **Phase 2:** structured STEP assembly *export* (FR2), 3MF metadata/colors
  + conformance (FR4–FR5), FCF completeness (FR3 shrinks).
- **Phase 3:** USD behind the flag (FR11), nested-tree import onto PRD-013
  sub-assemblies (FR10), PMI *import* exploration.

## Acceptance criteria

- AC1. A toleranced part (dims + datum + flatness FCF via `set_part_pmi`)
  exports to STEP AP242; our own XCAF reader re-imports it and every PMI
  entity survives (automated round-trip test); the file shows PMI in
  FreeCAD's AP242 viewer (manual screenshot per release).
- AC2. Importing a 14-product/41-occurrence STEP fixture with
  `structured: true` yields exactly the deduplicated part set and 41 placed
  instances with correct composed transforms (spot-checked against known
  poses) and names derived from product labels; `structured: false` still
  yields today's single blob (tests).
- AC3. The rocketry assembly exports GLB that passes glTF 2.0 validation
  and renders in the vendored Three.js loader with instance colors and
  poses matching `get_assembly` (test + browser check); two exports at the
  same state are byte-identical (test).
- AC4. A 3MF export is validated structurally (OPC parts + core-namespace
  XML asserts) and re-read via lib3mf, declares millimeter units, and
  carries title/part-number metadata; a multi-solid part carries per-solid
  colors (tests); it opens correctly scaled in PrusaSlicer (manual, once
  per release).
- AC5. With `agentcad[usd]` installed, `usd` appears in the format enums
  and exports a stage that opens and composes via `Usd.Stage.Open` with
  structural asserts (`usd-core` ships no `ComplianceChecker`, so there is
  no `usdchecker`-equivalent to run); without the extra the format is
  absent from `GET /api/tools` schemas (tests, FEM-gating pattern).
- AC6. Every interop result carries a `fidelity` block; the PMI-skip path
  (an FCF type the writer can't map) is exercised and reported, not
  dropped (test).
- AC7. Full suite green; existing flat imports, STEP/STL/3MF exports, and
  the 100 MB/extension guards behave exactly as today.

## Risks & open questions

- **OCCT's AP242 PMI writer depth** is the load-bearing unknown: XCAF
  covers dimensions/datums/geometric tolerances, but coverage per FCF type
  and per-viewer readability varies. De-risk with a spike against FreeCAD
  and one commercial viewer before committing FR1's scope; `fidelity`
  reporting (FR3) is the honesty valve either way.
- **3MF metadata via OCCT:** if the pinned OCCT writer can't stamp
  metadata/colors, the OPC post-processing step must edit the package
  without breaking conformance — schema validation in CI is the guard.
- **Name sanitization collisions** on import (two products mapping to one
  part-id) must stay deterministic and reported; the original-label
  metadata field is the escape hatch.
- **Import scale:** a 40-product structured import triggers 40 reference
  registrations and mesh builds; batch through the kernel pool with
  progress events, and measure against the 100 MB guard before raising it.
- **Z-up vs Y-up** (glTF/USD conventions) is a classic silent-corruption
  spot; one conversion, stated in asset metadata, covered by a pose test
  (AC3) — never per-caller flags.
- **USD dependency weight** justifies the extra-gating; revisit bundling
  only if PRD-030/twin demand materializes.

## Competitive references

The NX/HOOPS ecosystem is the interop benchmark (market_research.md, gap
matrix "Interop"); the format verdict — STEP authoritative, 3MF for print,
glTF for web, USD rising, STL legacy — is the settled 2025–26 landscape
("The workflow ring"). Incumbents translate well but their exports drift
from their models between saves; AI-native rivals barely export at all (Zoo:
no drawings or PMI to carry — "AI-native CAD"). We differ by: PMI that
survives to suppliers because the tolerance model is structured data, not
drawing annotations; exports that are deterministic compiled artifacts of a
version (cacheable, CI-diffable); imports that produce an agent-legible
product tree; and plain-spoken fidelity reporting — geometry and PMI
travel, parametric intent does not, and the product says so. Native-format
ingestion is deliberately excluded here and owned by PRD-032.
