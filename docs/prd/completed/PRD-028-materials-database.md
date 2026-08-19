# PRD-028 — Materials database expansion

- **Status:** completed — merged to main in PR #27 (4bd8f44); MVP + Phase 2 (434 cited cards, schema v2, find_materials, FEM temperature resolution, lint, browser); community repo / package distribution / FreeCAD import / 600+ records deferred (docs/materials.md)
- **Phase:** v5 — daily-driver depth
- **Created:** 2026-08-09
- **Origin:** founder idea #2 (Aug 2026), engineering-reviewed; grounded by dedicated research (market_research.md, "Materials data")
- **Depends on:** — (extends the existing materials seam)
- **Related:** PRD-021 (process suitability feeds DFM/costing), PRD-003 (specs consume properties), PRD-031 (community material cards), PRD-025 (Test/Produce tabs display properties)

## Problem & motivation

The library ships 30 curated materials. Real projects need the next thousand
questions answered: "6061-T6 or 7075-T651?", "which PA12 grade survives
120 °C?", "C24 timber for this beam?", "what does this cost per kg?" —
and every downstream calculation (mass, FEM, thermal, stack-ups via CTE,
costing, DFM) is only as good as the property data underneath.

Founder idea #2 asks for "almost all possible" materials. The research
(market_research.md, "Materials data") reframes that goal in two important
ways. First, **size is the wrong target**: Ansys Granta — the industry's
reference — ships ~4,000 *generic* records with property ranges, and
SolidWorks ~500; MatWeb's 115k supplier datasheets is a licensing business,
not a library shape. A curated 300–1,000 generic materials with honest
ranges serves calculations better than 45k grades. Second, **sourcing is a
legal minefield with a clean path through it**: MatWeb/MakeItFrom/UL
Prospector contractually prohibit ingestion (and EU database rights bind
regardless of US facts doctrine), while NIST data (public domain), standards
values (EN 338 timber classes, EN 206/ACI concrete, ASTM/EN alloy grade
minima), and hand-collected per-value-cited facts are unencumbered. FreeCAD's
Supplemental-Materials repo (CC-BY-SA data, PR-gated) is the direct OSS
precedent for community contribution.

## Users & jobs

- **Engineer:** pick a real material with confidence, see the basis of every
  number (typical vs minimum vs allowable), and get a defensible mass/FEM/
  thermal/cost answer.
- **Maker:** browse by plain-language category ("strong printable plastic")
  with process suitability visible.
- **Agent:** select materials programmatically against requirements ("yield
  ≥ 240 MPa, service ≥ 150 °C, machinable, < $8/kg") and cite the property
  basis when reporting margins.
- **Contributor:** add a validated material card once, benefit everyone
  (community repo, PRD-031 distribution).

## Goals

- G1. 300–1,000 generic engineering materials across metals/alloys,
  plastics/polymers, elastomers, composites (laminate-level generics), wood
  (strength classes), concrete (strength classes), ceramics/glass — each
  record citing a source per value.
- G2. A property schema that calculations can trust: value-or-range, unit,
  basis (`typical | minimum | allowable-linked`), temperature point or
  table, source citation — superset-compatible with today's `Material`
  schema (all 30 existing entries migrate losslessly).
- G3. Temperature dependence where it matters: optional `(T, value)` tables
  for E, k, c_p with linear interpolation and out-of-range warnings — the
  SolidWorks/Ansys convention — consumed by FEM tools.
- G4. Process metadata feeding PRD-021: machinability class, weldability,
  printability per process (FDM/SLA/SLS/MJF), sheet bend data (K-factor
  ranges, min bend radius multiples), IM suitability.
- G5. Selection as a query: humans filter/compare in UI; agents call
  `find_materials` with property constraints.
- G6. Community contribution with validation gates (schema lint, unit/range
  sanity, mandatory citations) and clean licensing (CC-BY-SA data repo),
  FreeCAD-card interop where practical.
- G7. Honesty everywhere: the existing "typical datasheet figures, not
  design allowables" caveat becomes per-value basis labels surfaced in UI,
  tool results, and exports; aerospace allowables (MMPDS) are linked, never
  ingested.

## Non-goals

- Bulk import of any licensed database (MatWeb, MakeItFrom, Prospector,
  Granta) — legally excluded; the strategy is curation, not scraping.
- Supplier-specific grade catalogs at launch (generic-grade records first;
  specific grades arrive on demand and via community cards).
- Statistical allowables computation (A/B-basis) — out of scope entirely.
- Cost feeds/live pricing (static ranges with dates; live pricing is a
  later connector).

## Experience

**Human path.** A Materials browser (reachable from the inspector's material
dropdown → "Browse…", and the ⌘K palette): category tree on the left,
filterable table (density, E, yield, service temp, cost class, process
chips), compare view (pin 2–4 materials side by side), detail page per
material showing every property with basis badge and source link. Assigning
works as today (part material / per-solid materials). In the Test workspace
(PRD-025), FEM setup shows which properties came from tables vs points.

**Agent path.** `find_materials {require: {yield_mpa_min: 240,
max_service_temp_c_min: 150, process: "cnc"}, prefer: {cost}, limit: 5}` →
ranked candidates with the constraining values and their bases.
`get_material {id}` returns the full record including tables and citations
— so an agent's margin report can say "yield 240 MPa (minimum, EN 755-2)".

**Contributor path.** A material card is a JSON/YAML file in a public
`agentcad-materials` repo (CC-BY-SA-4.0 data license); PRs run the
validation gate in CI (schema, units, range sanity vs category envelope,
citation presence); merged cards ship in the next library release and are
installable as a package (PRD-011 mechanics; PRD-031 distribution).

## Functional requirements

- FR1. Schema v2 for materials: per-property object `{value | range:
  [lo, hi], unit, basis, T_c?, table?: [[T, v]…], source}`; record-level
  `category` (taxonomy below), `condition` (e.g. T6, annealed, C24),
  `standards: [refs]`, `process: {machinability?, weldability?,
  printable?: {fdm|sla|sls|mjf: rating}, sheet?: {k_factor_range,
  min_bend_radius_t}, im?: rating}`, `cost_usd_kg?: range + as_of`.
  Backward compatible: today's flat fields read as `basis: typical` points;
  the three-layer resolver (builtin < user file < project) is unchanged.
- FR2. Taxonomy: metals (steel/stainless/aluminum/titanium/copper/nickel/
  magnesium/zinc…), polymers (commodity/engineering/high-performance,
  thermosets, elastomers), composites (generic laminates), wood (softwood/
  hardwood strength classes per EN 338), concrete (EN 206/ACI classes),
  ceramics/glass, other. Every category populated at launch (G1 floor:
  ≥300 records total, ≥5 per leaf category actually used by examples).
- FR3. Sourcing rules enforced editorially and in CONTRIBUTING: allowed —
  standards-defined values, NIST/public-domain data, manufacturer
  datasheets cited fact-by-fact, community measurements labeled as such;
  disallowed — extraction from licensed aggregators. Every value carries
  its citation; records without citations fail validation.
- FR4. FEM integration: `fem_static/modal/thermal` resolve E/ν/k/c_p at
  the analysis temperature when tables exist (linear interpolation;
  structured warning outside table range); default behavior without
  tables unchanged.
- FR5. Mass/metrics unchanged in behavior (density point or range midpoint
  with a warning when a range is used for mass).
- FR6. `find_materials` (query), `get_material` (full record),
  `list_materials` extended with category/filter args — all registry
  tools; palette/browser use the same query engine.
- FR7. Browser UI with category tree, filters, compare, detail-with-
  citations; per-value basis badges (typical/min); MMPDS/Prospector
  linked out on relevant detail pages, never mirrored.
- FR8. Validation gate as a reusable checker (`agentcad materials lint`)
  used by the community repo CI and by project-level custom materials.
- FR9. Library versioning: the builtin set carries a version; projects
  record the library version used (manifest additive key) so old projects
  re-resolve identically (determinism: density feeds cache keys today —
  version pinning preserves byte-stable rebuilds).

## Agent surface

New tools: `find_materials {require?, prefer?, category?, limit?}` ·
`get_material {id}`. Changed: `list_materials` gains `category?`/`filter?`;
FEM tools' results note the property basis and interpolation used. Errors:
`validation_error` for impossible constraint sets (with the nearest
relaxation named — agent-actionable).

## Technical approach

- **Data:** builtin library moves from a Python table to versioned data
  files (JSON) under `agentcad/core/materials_data/` compiled into the
  existing `MaterialLibrary` resolver at startup; the public community repo
  mirrors the same card format. FreeCAD card interop via a one-way import
  script where mappings are clean (their `.FCMat` → our schema).
- **Seam, not fork:** `service.materials` resolver API extended (typed
  property access with basis/temperature), keeping the three-layer
  precedence; per-solid materials and cache-key wiring untouched.
- **Packs:** `tools_materials.py` extended (query tools); `routes_materials`
  extended for browser endpoints; FEM handler pack reads tables via the
  toolbox.
- **Curation pipeline:** initial 300+ records authored via agent-assisted
  curation (agents draft cards from cited standards/datasheets; human spot
  review; the validation gate enforces schema/citations) — the same
  economics argument as PRD-011's registry seeding.
- No kernel changes beyond FEM property resolution.

## MVP & phasing

- **MVP:** schema v2 + resolver + migration of the 30; ~150 records across
  metals/polymers/wood/concrete (standards-first, fastest-to-cite);
  `find_materials`/`get_material`; browser v1 (tree, filter, detail with
  citations); basis badges; materials lint.
- **Phase 2:** temperature tables + FEM integration; process metadata
  blocks feeding PRD-021; compare view; 300+ records.
- **Phase 3:** community repo + CI gate + package distribution (011/031);
  FreeCAD card import; 600+ records; cost ranges refreshed with dates.

## Acceptance criteria

- AC1. All 30 existing materials migrate losslessly; full suite green with
  byte-identical meshes for the examples (density unchanged ⇒ cache keys
  unchanged, test).
- AC2. `find_materials {require: {yield_mpa_min: 240, max_service_temp_c_min:
  150}}` returns a ranked set whose every member satisfies the constraints,
  each with cited values (test).
- AC3. A thermal FEM run on a material with a k(T) table interpolates at
  the case temperature and warns outside range (test with a synthetic
  table).
- AC4. The browser shows ≥300 materials with per-value citations; a
  spot-checked sample of 20 records traces every value to its named
  source (editorial QA checklist in the PR).
- AC5. `agentcad materials lint` rejects a card missing citations, with
  the failing property named (test).
- AC6. A wood (C24) and a concrete (C30/37) record drive a plausible
  mass + static FEM on the construction example (integration test).
- AC7. No record in the shipped library derives from a licensed aggregator
  (provenance audit documented in the data repo).

## Risks & open questions

- **Curation quality at speed:** agent-assisted drafting can propagate a
  bad citation; mitigation — the lint gate + per-category envelope checks
  + human spot review ratio defined in the design spec.
- **Range vs point ergonomics** for downstream math (mass from a density
  range?) — MVP rule: points preferred for density; ranges allowed for
  strength/cost with explicit midpoint-plus-warning semantics. Revisit
  with usage.
- **FreeCAD interop drift** — treat as one-way import convenience, not a
  compatibility promise.
- **Anisotropy** (wood, composites): MVP stores directional values as
  separate properties (E_parallel/E_perp) without tensor support; full
  orthotropic FEM is out until the solver tier needs it (noted in
  PRD-030/sim-burst territory).

## Competitive references

Granta's generic-record shape and SolidWorks' ~500-material library set
the practical bar; MatWeb's license wall and the EU database right define
the sourcing constraints; FreeCAD Supplemental-Materials proves the
community model (market_research.md, "Materials data"). We differ: per-value
provenance and basis labels as first-class data (nobody surfaces this at
CAD level), agent-queryable selection, and a community pipeline whose
validation gate is the same lint agents and CI run.
