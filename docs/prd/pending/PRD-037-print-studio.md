# PRD-037 — Print studio: in-app slicing

- **Status:** pending
- **Phase:** v6 — manufacturing (the Produce mode's print route)
- **Created:** 2026-08-25
- **Origin:** founder direction from the Aug-2026 UX study ("slice
  them, place on different plates, modify — full parity with
  BambuStudio and others")
- **Depends on:** PRD-017 (3MF v2 export — completed) — soft: PRD-036
  (routing), PRD-022 (send-to-printer/quotes), PRD-021 (printability
  DFM), PRD-025 (mode frame)
- **Related:** PRD-013 (instances on plates), PRD-028 (materials)

## Problem & motivation

For the maker half of the audience, the design is not done until it is
sliced: oriented, arranged on a plate, given a process profile, and
turned into a printable job with a believable time/material estimate.
Today that means exporting 3MF/STL and re-importing into Bambu Studio,
Orca, or Prusa — losing parametrics, material context, and the checks
loop at the exact moment physical reality bites. The UX study's Produce
mode reproduced the Bambu Studio grammar (printer/filament/process
columns, plates, slice→preview with a layer slider and line-type
breakdown) and review confirmed the pattern reads as native. The
engineering honesty behind "full parity": we do not write a slicer.
Mature open engines (PrusaSlicer, OrcaSlicer, Bambu Studio's CLI) are
AGPL — bundling or linking them is excluded by the same licensing
posture that declined a GPL solver in PRD-009 — but **orchestrating an
external engine as a separate process over files is not linkage**. The
studio owns the UX, the project context, and the honesty rules; the
engine owns slicing.

## Users & jobs

- **Maker:** place, orient, profile, slice, and export a job without
  leaving the project — with estimates that update when the part does.
- **Engineer:** validate printability (min wall vs nozzle, overhangs,
  bed fit) as checks, before committing to a route (PRD-036).
- **Agent:** prepare a job ("slice the housing at 0.2 mm, strong
  infill"), read estimates, and cite them in the production plan.

## Goals

- G1. A plate workspace: parts placed/rotated/arranged on one or more
  named plates, persisted per project; multi-part and multi-plate.
- G2. Profiles: printer (bed size, nozzle), filament (material,
  diameter — linked to PRD-028 materials where sensible), process
  (layer height, walls, infill, supports…) — as named presets with
  overrides, stored as data, editable by tools.
- G3. Slice via a pluggable external engine (CLI, separate process,
  file exchange: project 3MF in, G-code + metadata out); engine
  presence is a capability — absent engine, absent tools, designed
  empty state (the `[fem]`/ODA precedent).
- G4. Preview from the engine's actual output: layer scrubbing,
  line-type breakdown (walls/infill/travel with time and filament per
  type), totals (time, grams, cost from filament price) — parsed from
  G-code/engine reports, never invented.
- G5. The staleness invariant (UX-study review): a sliced job records
  its input hash (geometry, placement, profiles); any drift gates the
  preview and export behind "re-slice needed". No stale job is
  printable-looking.
- G6. Printability checks (bed fit, min feature vs nozzle, overhang
  share) run pre-slice as advisory rows; deepen into PRD-021 DFM packs
  when installed.

## Non-goals

- Writing or forking a slicing engine; vendoring/linking AGPL code into
  the Apache-2.0 tree (process boundary + file exchange only).
- Printer fleet control, live printing, camera monitoring — PRD-022
  connector territory ("Print plate" hands off or exports).
- Multi-material painting/AMS mapping UX in v1 (single filament per
  plate first; the data model must not preclude it).
- Guaranteeing estimate parity with any vendor's closed slicer.

## Experience

From Produce (or a part's "Print…" action): the plate view — Prepare
tab: parts as top-down silhouettes on the configured bed, drag/rotate/
auto-arrange, out-of-bed parts flagged red and blocking slice (review
rule: never slice what cannot print); left column Printer / Filament /
Process panels with preset dropdowns and an Advanced toggle, exactly
the grammar Bambu users know. Slice runs the engine with progress;
Preview tab: layer slider with z-readout, line-type legend with
per-type time/percent/filament, totals strip. Edit the part in Design
and the job flips to "re-slice needed" everywhere; Export writes
G-code + a job 3MF; "Send to printer" appears only when a PRD-022
connector is configured. Agent path mirrors it: `prepare_print_job`,
`slice_job`, `get_print_job` — the production plan (PRD-036) reads the
same job object.

## Functional requirements

- FR1. Job model persisted per project: plates (name, printer preset),
  placements (part/config instance, position, rotation), filament +
  process profiles (preset id + overrides), last slice (engine id +
  version, input hash, estimates, artifact paths).
- FR2. Engine adapter contract: `slice(job_3mf, profiles) → {gcode,
  report}` over a subprocess with the PRD-006 confinement posture
  (work-dir-only writes); engines register like toolkit packs;
  reference adapter for one open engine (PrusaSlicer or Orca CLI —
  chosen in design by CLI stability), engine binaries user-installed
  or fetched with consent (the PRD-032 ODA precedent).
- FR3. Input assembly: plate 3MF generated from the existing PRD-017
  exporter (placements as transforms); config-bound instances resolve
  exactly as assemblies do (PRD-012/013 rules).
- FR4. Preview data parsed from engine output (G-code + report):
  per-layer polylines or bounds, per-line-type time/filament, totals.
  Parsing failures degrade to totals-only with an honest note — never
  fabricated layers.
- FR5. Staleness: input hash covers geometry cache keys, placements,
  and profile values; mismatch gates Preview/Export/Print behind
  re-slice (UI + tools both refuse with `job_stale`).
- FR6. Pre-slice checks: bed fit (bounds vs printer), nozzle-vs-min-
  feature (from geometry metrics), overhang share (mesh normal scan)
  as advisory rows; with PRD-021 installed the print DFM pack replaces
  the built-ins row-for-row.
- FR7. Estimates carry basis ("engine report") and cost from filament
  price metadata; no engine → no numbers (capability rule).
- FR8. Tools: `prepare_print_job` (create/update placements +
  profiles) · `slice_job` (async-safe, budget-honest) · `get_print_job`
  · `export_print_job {format: gcode|3mf}`; events `print_job_changed`.
- FR9. Multi-plate: parts assignable across plates; per-plate slice
  state and estimates; plate operations are undo steps.
- FR10. Determinism honesty: G-code and 3MF outputs are not
  byte-stable across engine versions; record engine id+version in the
  job and exclude these artifacts from byte-determinism claims (the
  DXF precedent).

## Agent surface

The four tools above, registered only when an engine adapter is
present; structured errors (`engine_absent`, `job_stale`,
`part_exceeds_bed {part_id, overhang_mm}`, `slice_failed` carrying the
engine's stderr tail). Post-state returns include estimates with basis.

## Technical approach

Service: `core/printing.py` (job model, hashing, adapter registry,
G-code report parsing) + `tools_print.py` + routes pack for the studio
UI; subprocess execution follows the confinement/work-dir rules from
PRD-006 (engine writes only in the job work dir). Frontend: plate
canvas (2D top-down; silhouettes from existing mesh projections),
profile panels, preview renderer over parsed layer data. No kernel
changes; the 3MF path is PRD-017's exporter.

## MVP & phasing

- **MVP:** one engine adapter, single plate, place/rotate/arrange, the
  three profile panels with presets, slice → totals + per-type table +
  layer scrubbing (bounds-level), staleness gating, export G-code/3MF,
  the four tools.
- **Phase 2:** multi-plate, richer preview (extrusion polylines),
  built-in printability checks, PRD-036 integration as the print
  route's artifact.
- **Phase 3 (with 021/022):** DFM pack swap-in, send-to-printer
  connector, quote-a-print.

## Acceptance criteria

- AC1. Browser: place two parts, slice with the reference engine,
  scrub layers, read the per-type table; totals match the engine
  report (parity test against the report file).
- AC2. Edit a placed part's parameter → the job shows "re-slice
  needed" everywhere; `export_print_job` refuses with `job_stale`;
  re-slice clears it (live).
- AC3. A part larger than the bed flags red and `slice_job` refuses
  with the structured error naming the overhang.
- AC4. Without an engine installed the tools are absent and the studio
  shows the designed empty state (both directions tested).
- AC5. Engine subprocess writes outside its work dir are blocked
  (confinement test, Linux).
- AC6. Full suite green without any engine installed (the suite never
  requires one — engine tests gate on its presence like `[fem]`).

## Risks & open questions

- **Engine CLI drift** across versions: pin per-adapter version
  ranges; record engine version in the job; adapter tests run only
  where the engine exists.
- **Preview fidelity vs cost:** full toolpath rendering can be heavy;
  the phased preview (bounds → polylines) keeps honesty ("preview
  simplified") ahead of spectacle.
- **Profile licensing:** vendor printer profiles may not be
  redistributable; ship generic profiles + import-your-own, decide
  per-vendor in design.
- **Which engine first** — PrusaSlicer's CLI is the most stable;
  Orca's inherits Bambu grammar. Decide in design by CLI contract
  quality, not brand.

## Competitive references

Bambu Studio set the grammar the study borrowed (Prepare/Preview,
profile columns, line-type table) — market_research.md's consumer-loop
analysis. Onshape/Fusion stop at export; slicers know nothing about
parametrics or checks. The differentiator is the loop the incumbents
structurally lack: a parametric change re-estimates the job, staleness
is honest, printability is a check beside every other check, and an
agent can run the whole path — while the engine itself stays a
swappable, properly-licensed subprocess.
