# 0038 — Per-solid part semantics: labels, metrics, and per-solid materials

- **Commit:** pending
- **Date:** 2026-08-09
- **Author:** Claude (with Nikita Fedorov)

## Summary

Multi-solid parts (a `build(p)` returning a Compound) now report per-solid
metrics and can assign a material per solid, with the aggregate mass rolled up
from the per-solid masses (roadmap "Per-solid part semantics").

## Changes

- **Worker** (`worker.py`): `_metrics(shape, density, densities=None,
  labels=None)` emits an index-ordered `"solids"` list (`{label, volume_mm3,
  mass_g, bbox, center_of_mass}`) for multi-solid shapes; a solid's density
  resolves label match > index match > part default, and the aggregate
  `mass_g` becomes the sum of per-solid masses. `handle_build` switched to
  `build_shape_ns` to read the new optional script contract addition
  `SOLID_LABELS` (list of strings, applied by index; anything else is a
  contract_error; extra labels warn). Handler packs calling the 2-arg
  `metrics(shape, density)` toolbox entry are unchanged — imported multi-solid
  STEP references get uniform-density per-solid metrics automatically.
- **Manifest/model**: part entries gain optional `"solid_materials"`
  (`{label-or-index: material_id}`); `PartRecord.solid_materials` +
  store read/write; no migration needed (the store round-trips unknown keys).
- **Service**: `_solid_densities` resolves the map via the material resolver;
  build params carry `"densities"` when present; the cache key gains a
  `densities` component **only when non-empty**, so every pre-feature cache
  key stays byte-identical (pinned by a test). `get_part` surfaces
  `solid_materials`.
- **New tool** `set_solid_materials(project, part_id, materials)`
  (`tools_solids.py` pack): validates script-kind part, material ids
  (builtin + project), persists, publishes `project_changed`, rebuilds, and
  returns the rebuild result plus post-state.
- **Inspector**: the Metrics tab renders a compact per-solid sub-table
  (label · volume · mass) for multi-solid parts.
- Docs: agent-api rows (`get_metrics`, `set_solid_materials`),
  part-authoring "Multi-solid parts and SOLID_LABELS" section, CHEATSHEET
  contract addition.

## Files

- `agentcad/kernel/worker.py` — per-solid metrics, SOLID_LABELS validation
- `agentcad/core/model.py`, `agentcad/core/project.py` — solid_materials field
- `agentcad/core/service.py` — density map, cache key, detail surface
- `agentcad/core/tools_solids.py` — new tool pack
- `frontend/js/inspector.js` — per-solid metrics sub-table
- `tests/test_solids.py` — 15 tests incl. byte-exact pre-feature cache key pin
- `docs/agent-api.md`, `docs/part-authoring.md`, `agentcad/core/templates.py`

## Notes

Known asymmetry: the tool validates material ids against builtin + project
layers only, while the density resolver also reads the user-global materials
file — a global-only id is rejected at assignment despite being resolvable.
Single-solid parts keep exactly the old math and emit no `solids` key.
Implemented in an isolated worktree branched before 0037 (typed PARAMS); cherry-picked with
one trivial add/add conflict in `worker.py` resolved by keeping both helpers.
