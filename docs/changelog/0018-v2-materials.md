# 0018 — v2: materials system (30 engineering materials, layered user-defined)

- **Commit:** 9c0d9fc
- **Date:** 2026-08-09
- **Author:** Claude Fable 5

## Summary
Replaces the fixed 10-entry material table with a schema-v2 engineering library
of 30 curated materials plus a three-layer (builtin < global < project)
resolver, activated through the Wave-0 material seam. Adds tools and routes to
list resolved materials and define per-project overrides.

## Changes
- **Schema v2 `Material`:** `density_g_cm3` stays the only required numeric
  field (still the only property the kernel consumes); optional `E_gpa`,
  `yield_mpa`, `ultimate_mpa`, `elongation_pct`, `cte_um_m_k`, `k_w_m_k`,
  `max_service_temp_c`, `cost_usd_kg`, `category`, `notes`, plus a `source`
  provenance tag. `to_payload()` drops None fields. `ultimate >= yield` is
  deliberately not enforced (ductile polymers break below yield).
- **30-material builtin library** across metal/polymer/composite/wood/masonry,
  with sourced typical values. All 10 v1 ids and their exact densities are
  preserved (cache keys hash density, so a change would silently invalidate
  every existing mesh cache).
- **Validation:** `validate_material_entry`/`validate_materials_dict` reject bad
  ids, unknown fields (typo safety), missing/out-of-range density (0, 25],
  negative numerics, unknown category, non-string label/notes.
- **`MaterialLibrary` resolver:** builtin < `~/.agentcad/materials.json` <
  project `materials` section; whole-entry replacement (no field merging). The
  global file is re-read only on mtime change; a corrupt global file degrades to
  builtins with the reason recorded in `global_error` rather than breaking
  builds. `resolve()`/`effective()` return provenance-tagged materials.
- **Seam activation:** `tools_materials.register` swaps `service.materials` for
  a `ProjectMaterialResolver` (reads project overrides via the store), so mass
  metrics honor user-defined alloys.
- **New tools:** `list_materials(project?)` (returns catalog sorted by
  category/id, plus a not-design-allowables caveat and `global_error`) and
  `set_project_materials(project, materials)` (validates then writes the
  project's `materials` section, publishes `project_changed`).
- **Store:** `add_part`/`set_part` now validate materials against builtins **or**
  the project's `materials` section via `_validate_material` instead of only
  builtins.

## Files
- `agentcad/core/materials.py` — schema v2, 30-material library, validation, `MaterialLibrary` resolver
- `agentcad/core/tools_materials.py` — `ProjectMaterialResolver` (seam), `list_materials`/`set_project_materials`
- `agentcad/server/routes_materials.py` — `GET /materials`, `PUT /projects/{proj}/materials`
- `agentcad/core/project.py` — project-aware material validation in `add_part`/`set_part`
- `tests/test_materials.py` — v1 preservation, layered precedence, entry validation, custom-alloy mass

## Notes
Property values are typical room-temperature datasheet figures, not certified
design allowables — surfaced in every `list_materials` response caveat. Keeping
v1 densities byte-stable is what protects the on-disk mesh cache.
