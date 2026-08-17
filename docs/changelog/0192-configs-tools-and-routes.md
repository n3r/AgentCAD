# 0192 — PRD-012 slice 3: the configuration tool pack and the routes

- **Commit:** pending
- **Date:** 2026-08-17
- **Author:** Claude

## Summary
Slice 3 of PRD-012 puts a surface on the configuration model slices 1–2 built:
a tool pack (`set_part_configs`, `list_configs`, `build_configs`,
`set_active_config`, `set_instance_config`) and a route pack for the browser,
including the content-addressed mesh route (`GET /projects/{p}/meshes/{key}`)
that removes the one-mesh-per-part assumption from the server side. Every
mutating tool validates before it writes, writes under the manifest lock,
publishes `project_changed` exactly once after the write with a `reason`, and
returns post-state.

## Changes
- **New `agentcad/core/tools_configs.py`** (sorts at `con`, so it reads
  `service.specs` / `service.packages` nowhere at `register()` and never
  touches `gate_providers`):
  - `set_part_configs {project, part_id, configs}` — a **full replace**.
    Refuses a non-object, a reference/imported part, and a script that does not
    currently load (with `set_params`' wording, because a user who meets one
    meets both); runs `packages.format.validate_configurations` over the
    **whole map** and reports every problem at once in `details.problems`;
    normalizes values through `service.normalize_params` (so `{"n": 3}` and
    `{"n": 3.0}` are one configuration and one cache key) while preserving the
    caller's order; then **FR11**: a removed name that an assembly instance
    binds, or that is the part's `active_config`, is a `ConflictError` carrying
    `details.{part, configs, instances, active_config}`. Writes under
    `packages.manager.manifest_scope` + `locks.write_scope(part_id)` (an
    emptied map pops the key), publishes once with `reason: "configs"`, and
    rebuilds **only** when the ACTIVE configuration's params changed (the
    result rides back as `rebuild`).
  - `list_configs {project, part_id?}` — `{parts: [{part_id, configs,
    active_config, diverged, diverged_params, referrers}]}`; project-wide it
    lists the configured parts and only those. `referrers` (`{name: [instance
    ids]}`) makes FR11 a lookup before it is a surprise.
  - `build_configs {project, part_id?, configs?}` — **serial and
    de-duplicated by cache key**: every requested member's pure key is computed
    first via `_cache_key_for(_record_for(...))`, each distinct key is built
    once through `_ensure_config_built`, and the row is fanned back out across
    the names that share it with `cached: True`. Rows are in family order,
    `{name, label, ok, cached, cache_key, metrics, warnings, error?}`, and a
    member that cannot build is a row with `ok: false`, never an exception.
    `cached` is measured (the key's `.acm` + `.metrics.json` existed before the
    ask), not assumed. Single part → `{part_id, configs: [rows]}`;
    project-wide → `{parts: [{part_id, configs}]}`; no configured part →
    `{parts: [], warnings: ["no configured parts"]}`. `spec_results` is
    deliberately absent (slice 6).
  - `set_active_config {project, part_id, config?, keep_overrides?}` — loading
    a variant, so it **clears the explicit overrides by default** and reports
    `cleared_overrides`; `keep_overrides: true` layers them on top and the
    response says `diverged`. Returns `with_hint(_rebuild(...))` merged with
    `{part_id, active_config, diverged, diverged_params, cleared_overrides}`;
    omitting `config` returns the part to base.
  - `set_instance_config {project, instance, config?}` — the narrow binding
    tool beside `set_assembly` (a full-list replace would silently unbind
    everything a caller forgot). `null` unbinds; the store validates the
    binding; publishes `reason: "instance_config"`; returns `get_assembly`.
- **New `agentcad/server/routes_configs.py`** — the `routes_specs.py` template
  (`_RAISE`, `_BODY_ERRORS = set()`, `_result`, `_body_keys`, `_json`) with the
  eight routes in the design's table. Two whitelist consequences are encoded:
  `PUT …/active-config` **refuses** a null/absent `config` and names the
  `DELETE` (because `_body_keys` strips `null`, the PUT cannot express "base"),
  while `PATCH …/assembly/instances/{id}/config` forwards `config` on
  `"config" in body` so `null` genuinely unbinds. `GET
  /projects/{p}/meshes/{key}?lod=` serves `.cache/<key>.acm` behind a
  `^[0-9a-f]{32}$` gate (or `<key>.<lod>.acm` when the tier grammar matches and
  the file exists, else the full mesh), **never builds** — an unbuilt key is a
  404 — and echoes `Cache-Control: no-store`, `X-Mesh-Key`, `X-Mesh-Lod`.
- **`agentcad/core/service.py`** — `set_assembly` now reads
  `config=item.get("config")` into each `InstanceSpec`, so the manifest's
  instance binding survives the full-list write path (the store validates it).
- **Fix round 1 (review findings):** `set_part_configs` and
  `set_active_config` now hold `manifest_scope` across the whole
  read-modify-write — the FR11 referential check (part entry **and** instance
  list) and `cleared_overrides` were read outside the lock and were therefore
  TOCTOU; `set_instance_config` additionally takes `service._lock`, the lock
  `service.set_assembly` serializes the identical read-all/write-all on (order:
  `manifest_scope` outer, `_lock` inner; both reentrant).
  `set_active_config` now clears the explicit overrides **only when the active
  configuration actually changes**, so a `DELETE …/active-config` on a part
  already at base (or a re-selection of the active name) no longer drops
  `set_params` values — the tool description says so. `build_configs` and
  `list_configs` share one `_configured()` definition (configured **script**
  parts), an empty matrix always carries a `warnings` reason (nothing declared /
  nothing requested / none of the requested names declared by that part), and
  project-wide `list_configs` returns the `"no configured parts"` warning. The
  mesh route's key gate uses `fullmatch` (`$` also matches before a trailing
  newline, so an anchored `.match` accepted `"<key>\n"`), as does the lod
  grammar; the rebuild decision guards a non-dict `configs` entry the way
  `_rows` does; and the redundant `locks.write_scope` calls say why they are
  belt and braces.

## Files
- `agentcad/core/tools_configs.py` — new tool pack (five tools).
- `agentcad/server/routes_configs.py` — new route pack (eight routes).
- `agentcad/core/service.py` — one field: `set_assembly` reads `config`.
- `tests/test_configs_api.py` — new: 39 tests in three sections (registration,
  the tools against a real three-member flange family, the routes through
  `create_app` + `TestClient`).

## Notes
- The `cached` flag on a `build_configs` row is measured from the cache
  directory before each group's build, so a second `build_configs` on an
  unchanged family reports `cached: True` everywhere — deliberately more honest
  than "the de-dup copies are cached".
- `set_active_config` writes `params={}` rather than popping the key: a part
  entry always carries `params` (`PartRecord.to_manifest`), so this matches a
  freshly created part byte for byte.
- `set_instance_config` takes `manifest_scope` **and** `service._lock` (it is a
  read-all/write-all of the instance list, and `service.set_assembly`
  serializes the identical one on `_lock`) but **no** `locks.write_scope`: a
  claim is a *part* claim, and the store's whole-manifest writes deliberately
  have no scope. **Deferred follow-up, deliberately not fixed here:**
  `tools_mates._set_instance_mate` and `routes_assembly2.patch_instance` do the
  same read-all/write-all with **no** lock at all. That is pre-existing and
  outside PRD-012's scope, so those two files are untouched; the third writer
  being serialized does not make the other two safe against each other.
- **Behaviour change from the fix round, with a consequence for the browser:**
  because `set_active_config` now clears overrides only on a real change of the
  active configuration, the design's "Reset to M" chip action (Decision 10)
  cannot be `set_active_config m` while `m` is already active — it must remove
  the overrides the pinned way, `set_params` with `null` per diverged
  parameter. Slice 7 should wire the chip that way.
- `list_configs` project-wide filters to configured parts; a part with no
  family is `configs: {}` on `get_part`, not a row here.
- The `set_assembly` tool *description* in `agentcad/core/tools.py` still does
  not mention the instance `config` key (that file belongs to another slice);
  the discoverable path is `set_instance_config`.
- Focused runs (this slice does not run the full suite — a parallel fix round
  shares the tree): `uv run pytest tests/test_configs_api.py
  tests/test_configs.py tests/test_server.py tests/test_mcp.py -q` — **99
  passed**; `tests/test_configs_api.py` alone — **39 passed**; regression
  batches `tests/test_service.py tests/test_solids.py tests/test_mates.py
  tests/test_packages_ocp_free.py tests/test_locks.py
  tests/test_undo_authors.py` — **86 passed**, `tests/test_packet.py
  tests/test_reference.py tests/test_specs_api.py tests/test_history.py` —
  **98 passed**, `tests/test_tools.py tests/test_prd011_acceptance.py` — **19
  passed**.
- Fix-round runs: `uv run pytest tests/test_configs_api.py
  tests/test_configs.py tests/test_server.py -q` — **104 passed**
  (`tests/test_configs_api.py` alone is now **45 passed**, six new tests);
  regression `tests/test_mcp.py tests/test_locks.py tests/test_mates.py
  tests/test_service.py tests/test_packages_tools.py -q` — **97 passed**.
- Full suite (run by the controller after this slice landed): `make test` — 3428 passed, 7 skipped in 9:10 on 8 workers. The fix round adds six tests and
  changes no other module's behaviour; the controller re-runs it.
