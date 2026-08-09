# 0016 — v2 Wave 0: extension-point scaffolding

- **Commit:** bbd4c9f
- **Date:** 2026-08-09
- **Author:** Claude Fable 5

## Summary
Pre-wires the extension points and service seams that let v2 features land as
additive packs without editing the core dispatch/tool/route files. All seams
default to v1 behavior, so the change is behavior-preserving on its own.

## Changes
- **Pack discovery:** `worker._load_handler_packs()` merges `HANDLERS`/
  `register(toolbox)` from `kernel/handlers/*.py` (shadowing a builtin is
  refused with a warning); `tools._load_tool_packs()` calls `register(registry,
  service)` on every `core/tools_*.py`; `app._mount_route_packs()` mounts each
  `server/routes_*.py` router under `/api`. A shared `WORKER_TOOLBOX` (build,
  metrics, place, export, tessellate, error types) is handed to handler packs.
- **Error Doctor hook:** `worker._dispatch` now routes every WorkerError and
  raw exception through `_diagnose`, which enriches `details.hint` from an
  optional `error_doctor` module (no-op when absent or a hint already exists).
- **Nested-Compound volume fix:** new `worker._shape_volume` sums `shape.solids()`
  (build123d 0.11 `Compound.volume` undercounts nested compounds), falling back
  to `.volume` for non-solids (e.g. STL Face); `_metrics` uses it. Regression
  test added (two disjoint cubes → 2000 mm³, n_solids 2).
- **Schema v2:** `SCHEMA_VERSION = 2`; `PartRecord` gains `kind`/`source`,
  `InstanceSpec` gains `mate`, all round-tripped through the manifest and
  `ProjectStore` (readers still accept v1). Reference parts skip script-file
  writes; `imports_dir()` added.
- **Reference/mate dispatch:** service `_shape_item` builds either a `script` or
  `source` worker item; `check_interference`/`export_assembly` use it and
  surface `skipped_mesh`; `create_part` accepts `kind`/`source` with validation;
  cache keys now hash a content signature (script text, or file path+mtime+size
  for references) via `_cache_key_for`; rebuild dispatches `build` vs
  `build_reference` with `affinity=part_id`.
- **Seams:** `_DefaultMaterialResolver` (builtin density) behind
  `service.materials`; `_resolved_instances` calls `mates.resolve()` when
  present; `KernelClient.request` gains an ignored `affinity` kwarg.
- **Reference loader:** new `kernel/refload.py` — content-addressed LRU keyed by
  (realpath, mtime_ns, size), STEP/BREP → solid, STL → mesh (boolean-banned).
- **Deps:** build123d bumped to ≥0.11.1, `bd_warehouse`/`ezdxf` added, optional
  `[fem]` extra (gmsh/scikit-fem/meshio); `uv.lock` updated. Examples tests now
  copytree to a temp dir so the committed examples are never mutated.

## Files
- `agentcad/kernel/worker.py` — pack loading, Error Doctor hook, volume fix, `_item_shape`, toolbox
- `agentcad/kernel/refload.py` — new content-addressed reference loader
- `agentcad/kernel/handlers/__init__.py` — new handler-pack package doc
- `agentcad/kernel/client.py` — `affinity` kwarg on `request()`
- `agentcad/core/service.py` — material/mate/reference seams, cache-key refactor, rebuild dispatch
- `agentcad/core/model.py` — `PartRecord.kind/source`, `InstanceSpec.mate` + manifest round-trip
- `agentcad/core/project.py` — schema v2, `add_part` kind/source, `imports_dir`
- `agentcad/core/tools.py` — tool-pack discovery, `schema`/`with_hint` public aliases
- `agentcad/server/app.py` — route-pack mounting
- `pyproject.toml`, `uv.lock` — deps + `[fem]` extra
- `tests/test_kernel.py`, `tests/test_service.py`, `tests/test_examples.py` — volume regression, `affinity` signature, copy-on-open

## Notes
Full suite reported 81 passed. Nothing here changes user-visible behavior yet;
it is the load-bearing substrate for commits 0017-0021 and the rest of Wave 1.
