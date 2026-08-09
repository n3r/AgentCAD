# 0004 — Core: model, materials, project store, service layer, tool registry

- **Commit:** 6c8d416
- **Date:** 2026-08-08
- **Author:** Claude Fable 5

## Summary
Adds the application core between the kernel and the (future) HTTP/agent clients:
domain types, the material table, a filesystem-backed project store, the
orchestrating service with a content-hash rebuild cache and EventBus, and the
17-tool registry that MCP and the chat agent both render from. Also adds a kernel
`inspect` method so param specs can be read without a full build.

## Changes
- **Model** (`core/model.py`): dataclasses `ParamSpec`, `PartRecord`,
  `InstanceSpec` (with `to_manifest()`); `AppError` hierarchy `NotFoundError`/
  `ValidationError`/`ConflictError` (details dict); `ID_RE`
  (`[a-z][a-z0-9_]{0,39}`); `validate_id`/`validate_vec3` helpers.
- **Materials** (`core/materials.py`): frozen `Material` table of 10 entries
  (al6061 … douglas_fir) keyed by id, `DEFAULT_MATERIAL = "al6061"`, and
  `get_material` raising `ValidationError` with the known set on miss.
- **Project store** (`core/project.py`): `ProjectStore(root)` — list/create/open
  (external example dirs registered by resolved path), manifest read/save with
  schema-v1 defaults filled and unknown keys preserved, part CRUD
  (`add_part`/`remove_part`/`update_part_entry`/`read_script`/`write_script`),
  assembly `instances`/`set_instances` (validates unique ids + known part refs +
  vec3s), `cache_dir`/`exports_dir`. `remove_part` raises `ConflictError` while an
  instance references it; all writes are atomic (tmp+`os.replace`); corrupt/
  missing `project.json` raises `ValidationError`.
- **Service** (`core/service.py`): `EventBus` (bounded 256-deep per-subscriber
  queues, drops rather than blocks) and `AgentCADService` orchestrating store +
  kernel. Rebuild flow keys the cache by `sha256(script,sorted params,density,
  tolerance)` and persists `.cache/<key>.acm` + `<key>.metrics.json`; a broken
  script is saved and marked `error` while the last good mesh is retained;
  publishes `rebuild_started`/`finished`/`failed` and `project_changed`. Exposes
  the service methods each client needs (project/part CRUD, `set_params`,
  `ensure_mesh`, `mesh_summary`, `get_metrics`, export, assembly rollups with
  world bbox, `check_interference`, `part_template`), plus `_params_spec` backed
  by the kernel `inspect` call with a spec cache. `KernelErrorFromResult` re-
  raises a failed rebuild.
- **Templates** (`core/templates.py`): `DEFAULT_PART_SCRIPT` (parametric rounded
  plate) and `CHEATSHEET` — the contract + build123d idioms served to agents.
- **Tools** (`core/tools.py`): `Tool`/`ToolRegistry` with minimal JSON-Schema
  validation (required keys + primitive types) that returns `{"error": {...}}`
  payloads (converting `AppError`/`KernelError`) instead of raising;
  `build_registry(service)` registers the 17 tools; mutating tools attach a `hint`
  on failure via `_with_hint`.
- **Kernel** (`kernel/{protocol,worker}.py`): adds an `inspect` method/handler
  that validates the contract and returns normalized `params_spec` without
  building geometry.
- **Tests:** `tests/test_project.py`, `tests/test_service.py`,
  `tests/test_tools.py`.

## Files
- `agentcad/core/{model,materials,project,service,templates,tools}.py` — application core
- `agentcad/kernel/{protocol,worker}.py` — new `inspect` method/handler
- `tests/{test_project,test_service,test_tools}.py` — core coverage

## Notes
The service is the single writer of project files; every client (REST, MCP,
chat) is meant to go through it, and the ToolRegistry is the single source of
tool truth so MCP and chat surfaces cannot drift. Assembly world-bbox rollup uses
an intrinsic-XYZ Euler `_apply_transform` matching the kernel's placement.
