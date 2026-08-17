# 0189 — PRD-012 slice 1: the configuration model, the store, and resolution

- **Commit:** pending
- **Date:** 2026-08-17
- **Author:** Claude

## Summary
The foundation slice of PRD-012 (Configurations): a part record gains a
manifest-resident `configs` map and an `active_config`, an assembly instance
gains a `config` binding, resolution lands as two pure members on
`PartRecord`, and every geometry consumer of `record.params` now reads
`record.effective_params`. No tool, route or UI is wired up yet (slices 2–7),
and no fixture declares a configuration — so `effective_params == params`
everywhere and a project without configurations is byte-identical to a
pre-PRD-012 one.

## Changes
- **`PartRecord` (model.py)** gains `configs: dict[str, dict] | None` (the
  frozen `{name: {params, label?, description?}}` schema PRD-011 published,
  insertion order = family order, never sorted) and
  `active_config: str | None`. `to_manifest()` writes each **only when set**
  (the `solid_materials` precedent), so nothing changes on disk for a project
  that has no family. No `SCHEMA_VERSION` bump.
- **Resolution** — two pure members, no I/O and no kernel call:
  `config_params(name)` returns a **copy** of one configuration's params
  (pure-config resolution, ignoring `active_config` and the overrides;
  `KeyError` for an unknown name, because every tool boundary validates
  membership first), and the `effective_params` property returns
  `{**config_params(active), **params}` (defaults < active config < explicit
  overrides). An `active_config` the map no longer declares resolves as
  **base**, never as a `KeyError` out of a geometry read. PARAMS defaults need
  no code: `worker._resolve_params` already fills every unset name.
- **`InstanceSpec` (model.py)** gains `config: str | None`, written by
  `to_manifest()` only when bound. Load-bearing rather than cosmetic:
  `set_instances` rewrites the whole list from `to_manifest()`, and
  `tools_mates` and the gizmo drag both read-all/write-all, so a field the
  dataclass did not carry would be destroyed by the next mate edit.
- **`ProjectStore`** reads all three fields back as stored — resolution stays
  the record's job, because a store that resolved would let the next
  `set_params` bake the active configuration into the overrides.
  `update_part_entry` / `_update_part_entry` gain keyword-only
  `configs: dict | None = None` (a non-empty map replaces, an **empty** map
  pops the key, `None` means "not passed") and `active_config` with a new
  module-level `_UNSET` sentinel (`None` is a real value here — "return to
  base" — so it cannot double as "leave it alone").
- **`ProjectStore.set_instances`** validates a binding beside the existing
  unknown-part and dangling-mate refusals, because three writers reach the
  store and only the store sees all three: a reference/imported part cannot
  bind one (no PARAMS), and an undeclared name is a `ValidationError` carrying
  `details.declared`.
- **`packages/format.validate_configurations(configs, params_spec)`** — the
  new pure map-level loop beside `validate_presets`: `CONFIG_RE` per key, then
  `validate_configuration` per entry with every field re-prefixed
  `configs.<name>.<field>`. The presets loop and this one now share the rules
  by construction, so the manifest and a published `presets.json` cannot drift
  on what a configuration is. The module stays OCP-free (covered by the
  existing `tests/test_packages_ocp_free.py` probe).
- **`service.normalize_params(spec, values)`** — the public seam over the
  private `_normalize_param`: int for an int parameter, the declared choice for
  an enum, float for a number, `ValidationError` (with `unknown`/`known`
  details) for an unknown name. Without it `{"n": 3}` and `{"n": 3.0}` would be
  two configurations and two cache keys for one geometry. Ranges are
  deliberately not checked here — a declared configuration is refused by the
  validator, while an explicit override keeps today's store-raw/worker-clamps
  semantics.
- **`record.params` → `record.effective_params` at every geometry consumer**
  (19 sites): `service.py`'s `_cache_key_for`, `_rebuild` build params,
  `export_part` and `_shape_item`, plus `mates.py`, `tools_analysis.py` (×4),
  `specs.py` (×2), `tools_drawing.py`, `tools_facemod.py` (×2),
  `tools_sketch.py`, `tools_sheetmetal.py`, `tools_holes.py` (×2) and
  `packet.py`. `record.params` keeps meaning *explicit overrides* at the two
  manifest-facing sites that are not geometry (`get_part`'s payload and
  `set_params`' merge).
- **Cache keys are config-aware by construction and nothing new enters the
  hashed payload**: `_cache_key_for` hashes `record.effective_params`, so two
  configurations with the same override map share one entry, a declared family
  nobody activated changes no key, and every cache entry written before
  PRD-012 still hits. `tests/test_solids.py`'s pinned payload bytes are
  untouched.

## Files
- `agentcad/core/model.py` — `PartRecord.configs`/`active_config`,
  conditional `to_manifest` keys, `config_params()`, `effective_params`;
  `InstanceSpec.config`
- `agentcad/core/project.py` — `_UNSET`; `get_part` and `instances()` read the
  new fields; `update_part_entry`/`_update_part_entry` keywords;
  `set_instances` binding validation
- `agentcad/core/packages/format.py` — `validate_configurations`
- `agentcad/core/service.py` — `normalize_params`; four `effective_params`
  renames (`_cache_key_for`, `_rebuild`, `export_part`, `_shape_item`)
- `agentcad/core/mates.py`, `tools_analysis.py`, `specs.py`,
  `tools_drawing.py`, `tools_facemod.py`, `tools_sketch.py`,
  `tools_sheetmetal.py`, `tools_holes.py`, `packet.py` — the mechanical
  `record.effective_params` rename (geometry requests only)
- `tests/conftest.py` — `FLANGE_SCRIPT` (a flange with six unit-and-description
  parameters) and `THREE_SIZE_CONFIGS` (an s/m/l family in family order)
- `tests/test_configs.py` — **new**, 24 tests: the model (serialization,
  layering, a dangling `active_config`, the copy, the `KeyError`), the store
  (byte-identical round trip, configs surviving a params write, popping,
  instance round trip, the two refusals), `normalize_params` (coercion, enum
  canonicalization, unknown names, no range check) and the two cache-key
  claims. Kernel-free by construction (`kernel=None`).
- `tests/test_packages_format.py` — 5 tests for `validate_configurations`
  (a good map, the name grammar at `configs.M`, a flat entry re-prefixed under
  `configs.s`, a bad parameter at `configs.m.params.width`, a non-object map
  as one `wrong_type`)

## Notes
- **Verification:** `make test` — 3339 passed, 7 skipped in 758 s (12:38, 8
  xdist workers). Focused first:
  `uv run pytest tests/test_configs.py tests/test_packages_format.py
  tests/test_service.py tests/test_solids.py tests/test_manifest_merge.py -q`
  — 277 passed. Written test-first: the 27 new tests failed as
  `AttributeError`/`TypeError` before the implementation landed.
- `_rebuild(proj, part_id)` and `get_part(proj, part_id)` keep their exact
  signatures — three packs monkey-patch them with two-positional wrappers.
  This slice only swaps `record.params` inside `_rebuild`; the `_build_with` /
  `_ensure_config_built` split arrives in slice 2.
- The store does **not** shape-check `configs`: validation is the tool
  boundary's job (`validate_configurations` + `normalize_params` over the whole
  map before one byte is written), landing with `set_part_configs` in slice 2.
- `manifest_merge.py` is deliberately untouched — the key-wise merge of
  `parts.<id>.configs.<name>.params.<param>` and `config_problems()` belong to
  slice 5.
