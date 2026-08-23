# 0311 — PRD-017 slice 4: glTF/GLB export, color map, tools_xchange pack

- **Commit:** pending
- **Date:** 2026-08-23
- **Author:** Claude (Opus subagent) / Nikita Fedorov

## Summary
Pure-Python glTF 2.0/GLB export from cached ACM1 meshes, one deterministic
material-category color map, and the `tools_xchange.py` pack that extends
`export_part`/`export_assembly` (formats, PMI routing, fidelity) without
touching any core. FR6–FR7/FR12 of PRD-017, plus FR1's tool wiring.

## Changes
- `core/gltf.py` (OCP-free, no new dependency): ACM1→glTF with meshes
  deduplicated by `mesh_key`, one −90°X root node for Z-up→Y-up
  (`asset.extras` states the conversion), per-instance nodes sorted by id,
  quaternions from intrinsic-XYZ Euler (numerically tested against the
  house convention), linear-space PBR `baseColorFactor`, `round(x, 6)`
  floats + `sort_keys` — **byte-identical across builds and across
  processes** (cross-`PYTHONHASHSEED` sha match). `.gltf` defaults to a
  self-contained data-URI buffer so an export is one file.
- `core/interop_colors.py`: `color_for` precedence (explicit color →
  category map → `#98a2ad`), `CATEGORY_COLORS`/`METAL_SUBCATEGORY_COLORS`
  closed over `materials.CATEGORIES`/`SUBCATEGORIES` (asserted at import),
  `srgb_to_linear`. Unknown material ids resolve neutral, never raise
  mid-export.
- `core/tools_xchange.py` (name = load order: must wrap the FINAL
  `export_assembly` after `tools_structure`'s replacement): wraps
  `service.export_part`/`export_assembly` (`_WRAPPED` sentinels), routes
  `gltf`/`glb` server-side and `step`-with-PMI to the slice-1
  `export_step_pmi` handler (`pmi: false` opts out), delegates
  `stl`/`3mf`/flat-`step` untouched (stl byte-identity asserted; STEP
  headers carry a timestamp, 3MF mints UUIDs — shape-asserted), attaches
  `fidelity` to every export (axes absent when the format can't carry
  them; `parametric: "none"` always). Registered schemas mutated in place
  (enums + `pmi?`/`metadata?`) **and the handler rebound** — the old
  lambda would `TypeError` on the new args; both halves are tested via
  `GET /api/tools`. Assembly `3mf`/`structured` deliberately not yet
  advertised (slice 5).
- Assembly items come from `service.get_assembly` (the expanded public
  result — expansion not re-implemented); a cross-project member whose
  mesh is uncached becomes a `fidelity.instances_skipped` row, never a
  silent drop.

## Files
- `agentcad/core/gltf.py`, `agentcad/core/interop_colors.py`,
  `agentcad/core/tools_xchange.py` — new
- `tests/test_interop_gltf.py` (30), `tests/test_xchange_pack.py` (26) — new

## Notes
Behavior change, intended (FR1): every `format=step` export of a part with
stored PMI — REST route and release bundles included — now writes AP242
unless `pmi: false`; `test_release_bundle` green. `mesh` entries key by
`(mesh_key, material)` because a glTF primitive carries its material.
`make test` — 5507 passed, 40 skipped in the recorded run (19:31, box shared with a concurrent session); the 7 non-passing items are the same known set as 0308/0309 — sheetmetal/supervisor load timeouts (22/22 pass in 57 s in isolation) and the pre-existing local-only prd028 AC6 real-solver timeout (skips on CI).
