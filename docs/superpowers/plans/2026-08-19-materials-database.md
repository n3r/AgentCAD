# PRD-028 Materials database — implementation plan

Design: `docs/superpowers/specs/2026-08-19-materials-database-design.md`. TDD per
slice; the controller (not subagents) runs `make test` and commits, one changelog
per commit. Slice 1 is the foundation; 2 and 3 touch disjoint files and run
**concurrently** after it; 4 (curation, eight agents on eight disjoint files) and
5 (frontend) run concurrently after 2; 6 closes.

## Slice 1 — schema v2, data loader, migration of the 30, lint + CLI (FR1, FR2, FR3, FR8, FR9)
- `materials.py`: `Property` (value|range, unit, basis, source, T_c, table,
  as_of) with `.point`, `.at(T_c) -> (value, interpolated, clamped)`;
  `PROPERTY_UNITS` (15 closed keys), `BASES`, `CATEGORIES` (7), `SUBCATEGORIES`,
  `PROCESS_*` vocab; `Material` keeps every existing field + new defaulted ones
  (`subcategory, condition, standards, process, links, properties, warnings,
  poisson_ratio, cp_j_kg_k, shear_modulus_gpa, compressive_mpa, bending_mpa,
  E_perp_gpa, library_version`); `to_payload(full=False)`; `normalize_entry(id,
  entry, source) -> Material` accepting a v1 flat entry **or** a v2 card (the
  `properties` key decides; mixing is a `ValidationError`), keeping every v1
  rejection; `validate_material_entry`/`validate_materials_dict` keep their
  signatures and route through it.
- `materials_data/`: `_library.json` (`library_version: "2.0.0"`), one file per
  family holding the **30 legacy records as v2 cards** — values byte-identical,
  re-attributed to primary sources (no MatWeb in any `source`), `basis`
  honest (A36/A992 ys/uts → `minimum`; EN/FPL → `characteristic`/`typical`);
  loader builds `MATERIALS` at import via the library lint profile and raises a
  clear error on a bad card. Delete the `_m(...)` rows. `LIBRARY_VERSION` exported.
- `materials_lint.py`: `lint_card/lint_file/lint_catalog` with `library` and
  `user` profiles, `Finding` rows, `ENVELOPES` per category (warnings),
  `disallowed_source` (MatWeb/MakeItFrom/Prospector/Granta) error,
  `missing_citation` naming the property, `density_must_be_point`, unit
  mismatch, table monotonic + point-in-envelope, `cost_usd_kg` in one place only.
- `cli.py`: `agentcad materials lint <path>… [--profile] [--json]` (file, dir,
  or `project.json` → `user` profile), exit 0/1/2.
- `service.create_project` writes `manifest["materials_library"]`;
  `tools_materials.set_project_materials` refreshes it; `list_materials` reports
  `library_version`, `project_library_version`, `count`, and the
  `library_version_newer_than_shipped` warning. `service._materials_map` tolerates
  the new fields. Check that no golden manifest/test breaks on the new key.
- Tests: 30 ids + densities pinned (all 30, not 10); payload compat (flat keys
  present, `basis`/`uncited` added); v1 flat entry still validates and reads
  `basis: typical`/uncited; card validation refusals (unknown prop, bad unit,
  range lo>hi, mixed v1/v2); `Property.at` interpolation/clamping; lint AC5;
  shipped library lints clean; CLI exit codes; `materials_library` written on
  create; full suite green (cache keys unchanged ⇒ existing mesh/cache tests).
- **Opus.**

## Slice 2 — query engine + tools + routes (FR6, G5) — after slice 1
- `materials_query.py`: `parse_constraints`, `qualifies(material, constraints)`
  (range lower/upper-bound rule, missing property fails, `process` mapping,
  `basis` restriction), `rank(materials, prefer)`, `nearest_relaxation`,
  `row(material, constraints)`.
- `tools_materials.py`: `find_materials {require?, prefer?, category?, limit?,
  project?}` (zero results → `ValidationError` with `nearest_relaxation`),
  `get_material {id, project?}` (full payload), `list_materials` gains
  `category/subcategory/filter`.
- `routes_materials.py`: `GET /api/materials?category&subcategory&filter=<json>`,
  `GET /api/materials/{id}`, `POST /api/materials/find`; invalid JSON filter → 422.
- Tests: AC2 (the PRD's exact query, every member satisfies, each constraining
  value carries `source`); range bound rule; `process: cnc`; `prefer` ordering;
  unknown key refusal lists grammar; nearest relaxation named; routes 200/404/422;
  hosted: the new routes are member-gated (not in the anonymous frozenset).
- **Sonnet** (Opus if the ranking logic needs judgment).

## Slice 3 — FEM temperature resolution + basis in results (FR4, G3, AC3) — after slice 1, concurrent with 2
- `tools_analysis.py`: `_resolve_property(project, material_id, key, T_c)` via
  `service.materials.resolve(...).prop(key).at(T_c)`; `fem_thermal` k at
  `(t_hot+t_cold)/2` unless `k_w_m_k` passed; `fem_static`/`fem_modal` gain
  `temperature_c`, `E_mpa`/`nu` default from the material (historical fallbacks
  kept and recorded); every FEM result gains `material_basis` and the
  `temperature_out_of_table_range` warning when clamped.
- `specs._youngs_mpa` reads E through the same resolver at 20 °C (key unchanged
  for point-only materials — test).
- Tests (fake kernel: patch `fem_available` → True before `build_registry`,
  capture `service.kernel.request`): synthetic k(T) table in a project material →
  interpolated k inside the table, clamped + warning outside; ν from
  `poisson_ratio`; fallbacks recorded; `importorskip` real-solver variants.
- **Opus.**

## Slice 4 — curation fan-out (G1, FR2, FR3, AC4, AC7) — after slice 1 lands; eight concurrent agents, one file each
- Files: `metal_aluminum_light.json` (Al/Mg/Zn), `metal_steel.json` (carbon/
  alloy/tool/cast iron), `metal_stainless_ni_ti_cu.json`, `polymer_commodity_
  engineering.json`, `polymer_high_performance_thermoset_elastomer_foam.json`,
  `composite_ceramic_other.json`, `wood.json`, `masonry.json` — each agent owns
  its file, merges the legacy records of its family (values identical), lints at
  `--profile library` with `.venv/bin/python -m agentcad.cli materials lint`,
  and returns the card count per subcategory. Rules of §11 in the spec quoted
  verbatim in every prompt; "omit when unsure"; ≥5 per example-touched leaf.
- Then one **QA agent**: 20 random records spot-checked against their sources
  (fixes or omissions applied), `materials_data/PROVENANCE.md` (per-file sources,
  AC7 attestation), `docs/materials.md` QA checklist section.
- **Opus** for curation and QA.

## Slice 5 — frontend browser (FR7) — after slice 2; concurrent with 3/4
- `frontend/js/materials.js`: overlay view (market.js pattern), toolbar
  `Materials` button, inspector **Browse…** (assign mode → `api.updatePart`),
  `#materials` hash; tree + filters + table + compare (2–4) + detail (basis
  badges, sources, tables, process, links, uncited, warnings). `api.js` gains
  `getMaterial`, `findMaterials`, `listMaterials(filter)`. CSS in `app.css`.
- Verified in a real browser (screenshot) if available, else evidence-graded
  against the HTTP contracts.
- **Sonnet.**

## Slice 6 — acceptance tests + docs + close-out (AC1–AC7)
- `tests/test_prd028_acceptance.py` per spec §12 (AC6 on a copy of
  `examples/construction` with `timber_c24` + `concrete_c30_37`).
- Docs: `docs/materials.md` (new), `docs/agent-api.md`, `docs/user-guide.md`,
  `AGENTS.md` traps, `CLAUDE.md` condensed traps, `docs/roadmap.md` (in progress
  → completed at merge), PRD status.
- **Sonnet** (tests + docs).

## Non-negotiables carried into every slice
- Only `agentcad/kernel/` imports OCP/build123d; no kernel changes in this PRD.
- The 30 legacy densities and ids are immutable; `_cache_key`'s payload is untouched.
- Closed property keys; `unit` canonical; every library value cited; no
  aggregator names in any `source`.
- Subagents run tests with `.venv/bin/python -m pytest` (never `uv sync`/`uv pip`)
  and never run git; the controller commits with a changelog citing
  `make test — N passed`.
