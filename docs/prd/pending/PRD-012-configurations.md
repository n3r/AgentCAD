# PRD-012 — Configurations

- **Status:** pending
- **Phase:** v5 — daily-driver depth
- **Created:** 2026-08-09
- **Origin:** competitive analysis (Aug 2026)
- **Depends on:** — (none hard)
- **Related:** PRD-003 (specs evaluate per config), PRD-004 (CI builds all configs), PRD-007 (customizer exposes configs), PRD-011 (package presets share this schema), PRD-013 (assembly-level variants later), PRD-014 (config dimension tables), PRD-015 (per-config BOM identity)

## Problem & motivation

Every real product line is a family, not a part: S/M/L enclosures,
left/right brackets, 3- and 5-bolt flanges. AgentCAD's only variant
mechanism today is the single live override set (`set_params` on the current
state) — exploring a size means mutating shared state, and there is no
per-variant identity for exports, drawings, BOM lines, or assembly
instances. "Configurations / design tables — typed PARAMS only" is a Gap
matrix row with the verdict **build** (market_research.md, "Gap matrix").

The competitive evidence is unusually direct: configurations and design
tables are a named top loss for anyone leaving SolidWorks
(market_research.md, "The desktop incumbents: Fusion, SolidWorks, Creo, NX"
— the "what you lose" analysis), and mature configurations are on Onshape's
2025–26 breadth list ("Cloud-native CAD: Onshape (the collaboration
benchmark)"). Fusion's weak configuration depth is a documented complaint.
A daily driver without families forces users back to their old tool the
first time a product comes in three sizes.

The agent-native angle: a configuration sweep is just data. `build_configs`
returns the whole family's metrics (and spec results, with PRD-003) in one
call, so "which sizes violate the mass budget?" is one tool call and one
comparison — and geometry CI (PRD-004) builds every config on every
proposal, so a change that breaks only size XL is caught before merge, not
at the machine shop.

## Users & jobs

- **Product designer (human):** declare S/M/L once; switch, inspect, and
  export variants without touching override state by hand.
- **Manufacturing/release engineer:** per-config exports and drawings with
  tabulated dimensions; per-config BOM identity (PRD-015).
- **Design agent:** build the config matrix in one call; reason across the
  family; fix the one failing size.
- **Assembly author (human or agent):** instance a specific config and be
  protected from its deletion.
- **Customizer visitor (PRD-007):** pick a curated config instead of
  wandering raw sliders.

## Goals

- G1. Named parameter sets as first-class, validated, reviewable variants.
- G2. Per-config identity wherever derived artifacts exist: metrics,
  exports, renders, drawings, BOM lines, cache entries.
- G3. One-call family builds: the config matrix with per-config metrics and
  (with PRD-003) spec results.
- G4. Assemblies bind instances to configs, with referential integrity —
  deleting a referenced config is a conflict, not a dangling instance.
- G5. Zero cost when unused: a part without configs behaves byte-identically
  to today.

## Non-goals

- Spreadsheet design tables with feature suppression — v1 configures
  *parameters*; feature variation is already expressible in script logic
  off an enum param, which configs can set.
- Assembly-level configurations (named variant instance sets) — deferred to
  after PRD-013 lands sub-assembly structure; v1 configures parts and
  instance bindings.
- Per-config PMI, materials, or solid assignments — later slices; v1 keeps
  those shared across the family.
- Optimization across the family — PRD-019 studies own sweeps beyond
  declared configs.

## Experience

**Human path.** The inspector gains a config switcher: "base" plus the
declared configs. Selecting "M" shows M's resolved params (overridden ones
visually distinct), M's metrics, M's geometry in the viewport. Editing a
param on top of an active config flags divergence ("M — modified") so
nobody ships an unnamed variant unknowingly. Exports and drawings made
while a config is active carry its name. A matrix view (config × metrics /
spec status table) summarizes the family.

**Agent path.** `set_part_configs {project, part_id, configs: {"S": {...},
"M": {...}, "L": {...}}}` declares the family (validated like `set_params`
— typed, clamped numerics with warnings, enum membership).
`build_configs {project, part_id}` returns per-config
`{name, ok, metrics, warnings}` in one call, built in parallel on the
kernel pool. `export_part {…, config: "L"}` writes `flange_L.step`.
`set_assembly` instances accept `config`; `get_assembly` resolves each
instance with its config's geometry and mass. Removing a config that an
instance references returns `conflict_error` naming the referrers.

**Handoff.** An agent proposes adding an XL config; the manifest diff is
key-wise reviewable (PRD-002 packet shows the new override set and its
metrics); the human flips the switcher to XL and looks.

## Functional requirements

**Declaration & resolution**
- FR1. Part records in `project.json` gain `configs: {name → {param:
  value}}`; names match `[a-z0-9][a-z0-9_-]{0,31}`. Override sets validate
  against the part's PARAMS exactly as `set_params` does: typed values,
  numeric clamping to min/max with warnings, enum membership enforced,
  unknown names rejected before anything is written.
- FR2. `set_part_configs {project, part_id, configs}` replaces the map
  (the `set_project_materials` full-replace pattern); `get_part` and
  `get_project` expose `configs` and `active_config`; parts without
  configs return an empty map — behavior unchanged.
- FR3. Per-part `active_config` (manifest field) drives the working state.
  Resolution order: PARAMS defaults < active config < explicit `set_params`
  overrides — explicit overrides on top flag the part as diverged from the
  config (status + inspector chip). Pure-config resolution (defaults <
  config only) is what matrix builds, per-config exports, and instance
  bindings use, so a variant's identity never depends on session state.
- FR4. Caching is config-aware by construction: config resolution feeds
  the existing content-hash key (`sha256(content, params, density,
  tolerance)`). Two configs with identical resolved params share one cache
  entry; switching `active_config` back and forth is cache-hit fast.

**Family builds**
- FR5. `build_configs {project, part_id?, configs?}` builds each named
  config (default: all configs of the part; omitting `part_id` covers
  every configured part in the project) through the kernel pool in
  parallel (per-config `affinity`), returning `{configs: [{name, ok,
  metrics, warnings, spec_results?}]}`. Per-config failures are reported
  in place; the matrix never aborts on the first failure.
- FR6. With PRD-003, specs evaluate per config and `spec_results` appears
  in the matrix; with PRD-004, CI builds all configs of changed parts
  (that PRD's requirement — restated here as the forward contract).

**Identity in artifacts**
- FR7. `export_part` and `render_view` gain `config?`; export filenames
  gain the config suffix (`<part_id>_<config>.<fmt>`); base-state naming
  is unchanged.
- FR8. `generate_drawing` gains `config?` and, for configured parts, an
  optional tabulated dimension table (per-config values of the overall and
  PMI-toleranced dims); full drawing-table depth (formats, placement)
  belongs to PRD-014.
- FR9. Forward BOM contract (PRD-015): a `(part, config)` pair is a
  distinct BOM line identity; `get_assembly` mass roll-ups use each
  instance's bound config.

**Assembly binding & integrity**
- FR10. `set_assembly` instances accept `config?`; resolution places that
  config's pure-resolved geometry; `get_assembly` returns the binding;
  `check_interference`, `sweep_motion`, and `tolerance_stackup` operate on
  config-resolved geometry.
- FR11. Referential integrity at the tool choke point: `set_part_configs`
  that removes or renames a config referenced by any assembly instance (or
  set as the part's `active_config`) fails with `conflict_error` naming
  every referrer; clearing the references first makes it succeed.
  `delete_part`'s existing instance-reference conflict is unchanged.
- FR12. The PRD-001 manifest merge driver merges `configs` key-wise per
  config name: concurrent additions of different configs merge clean;
  divergent edits of the same config conflict explicitly.
- FR13. Rebuild events carry the config being built (`rebuild_started`/
  `rebuild_finished` gain optional `config`) so the UI can show matrix
  progress.

## Agent surface

New tools: `set_part_configs {project, part_id, configs}` ·
`list_configs {project, part_id?}` ·
`build_configs {project, part_id?, configs?}` ·
`set_active_config {project, part_id, config?}` (null returns to base).
Changed: `export_part {…, config?}` · `render_view {…, config?}` ·
`generate_drawing {…, config?, dim_table?}` · `set_assembly` instances
accept `config?` · `get_part`/`get_project`/`get_assembly` expose config
state and bindings.
Errors: `conflict_error` (removing/renaming a referenced config, with
referrers in details) · `validation_error`/`contract_error` (bad override
sets) — house contract, post-state returns throughout.
Events: `project_changed` on config edits; rebuild events gain `config`.

## Technical approach

- **Manifest schema (additive):** `configs` and `active_config` on part
  records; `config` on assembly instances. Old manifests load unchanged;
  nothing is written for parts without configs.
- **Service:** config resolution slots into the existing param-resolution
  path as one layer ahead of explicit overrides (the service already
  resolves params into the build request and the cache key — one function
  grows one input). Assembly resolution (`mates.resolve` seam) uses
  pure-config resolution for bound instances.
- **Mesh addressing:** instances of one part bound to different configs
  carry different geometry, which breaks the one-mesh-per-part assumption
  in the mesh pipeline and viewport. Mesh endpoints and the frontend keyed
  by part gain a config dimension — the content-hash cache already stores
  distinct entries, so this is addressing, not recomputation. This is the
  main pre-existing-code implication of the PRD (see Risks).
- **Tool pack** `agentcad/core/tools_configs.py` + **route pack**
  `agentcad/server/routes_configs.py` per the extension-point contract;
  `build_configs` fans out on the pool with `affinity=f"{part}@{config}"`.
- **Frontend:** inspector config switcher + divergence chip; tree badge on
  configured parts; placement dialog config picker; matrix table
  (phase 2).
- **Drawings:** the dimension-table rendering lands in the drawing handler
  path reading per-config resolved dims; PRD-014 deepens it.
- Kernel untouched — configs are resolved to params before any kernel
  request, exactly like overrides today.

## MVP & phasing

- **MVP (roadmap):** manifest configs + `set_part_configs`/`list_configs`
  with full validation; `active_config` + inspector switcher + divergence
  flag; config-aware resolution and caching; `build_configs` matrix with
  per-config metrics; per-config export naming; instance config binding
  with the delete-conflict guarantee.
- **Phase 2:** drawing dimension tables (with PRD-014); `spec_results` in
  the matrix (with PRD-003); rebuild-event config field + matrix UI;
  `render_view` config support.
- **Phase 3:** customizer config exposure (PRD-007); per-config BOM lines
  (PRD-015); CI all-configs gating (PRD-004); package-preset schema
  alignment (PRD-011).

## Acceptance criteria

- AC1. A three-size flange family (S/M/L) builds as a matrix in one
  `build_configs` call returning per-config mass, with correct distinct
  values (test) — the roadmap's done-when.
- AC2. The flange drawing with `dim_table` shows a tabulated dimension
  table with one row per config (test asserting SVG content + one browser
  check).
- AC3. `set_part_configs` removing a config bound to an assembly instance
  returns `conflict_error` naming the instance; after clearing the binding
  the removal succeeds (test) — the roadmap's conflict done-when.
- AC4. `export_part {config: "L"}` writes `flange_L.step`; base export
  naming is unchanged (test).
- AC5. Two configs with identical resolved params share one cache entry;
  distinct configs get distinct entries; toggling `active_config` twice
  rebuilds from cache (test).
- AC6. Two instances of one part bound to different configs report
  different masses in `get_assembly`, and `check_interference` uses each
  instance's config geometry (test).
- AC7. Explicit `set_params` on top of an active config flags divergence
  in `get_part` status; clearing the override returns to the pure config
  (test).
- AC8. A project with no configs behaves byte-identically (existing tests
  untouched); full suite green.
- AC9. Browser session: switch configs in the inspector, watch viewport
  and metrics update live, see the divergence chip appear on a manual
  param edit — zero console errors.

## Risks & open questions

- **Mesh/viewport config dimension:** per-instance config geometry is the
  one place this PRD touches load-bearing pipeline assumptions
  (one-mesh-per-part). Contain it by reusing content-hash addressing end
  to end; benchmark the viewport with a 3-config assembly before MVP
  freeze.
- **Config-vs-override UX:** if the divergence flag is subtle, users ship
  "M but modified" unknowingly — borrow the visual weight SolidWorks gives
  overridden dims; test the chip in the browser session.
- **Manifest-resident vs script-resident configs:** manifest is chosen
  (clean tool CRUD, key-wise merge under PRD-001, conflict-on-delete at
  one choke point) at the cost that a part script alone no longer carries
  its family. Mitigated by PRD-011 packaging exporting configs as presets
  and release bundles including them; revisit if script portability
  suffers in practice.
- **Matrix cost** on large families of heavy parts: first build is honest
  compute; pool parallelism + caching make re-runs cheap; `build_configs`
  accepts subsets and PRD-004 budgets CI. No speculative pre-building.
- **Assembly-level configurations** will be demanded once PRD-013
  sub-assemblies land; the `configs` map shape is generic enough to extend
  — keep the schema forward-compatible, build nothing yet.

## Competitive references

SolidWorks configurations/design tables are the named top loss for leavers
(market_research.md, "The desktop incumbents"); Onshape's configurations
are a mature strength ("Cloud-native CAD: Onshape"); Fusion's weakness
here is a documented complaint. Gap matrix verdict: **build**. We differ:
a configuration is transparent data in a reviewable manifest — diffed
key-wise in proposals (PRD-002) instead of buried in a binary feature
tree; the whole family builds as one tool call any agent can reason over;
and per-config validation (specs in PRD-003, CI in PRD-004) makes "every
size stays green" enforceable — incumbents validate only the configuration
you happen to be looking at.
