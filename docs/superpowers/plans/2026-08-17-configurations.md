# Configurations — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Named, validated parameter sets (configurations) on part records —
resolved into every geometry request without the kernel learning the word,
with per-config identity for cache entries, exports, renders, drawings and
assembly instances, one-call family builds, referential integrity for bound
configurations, and a browser switcher — while a part without configurations
stays byte-identical to today (PRD-012, all nine acceptance criteria).

**Architecture (one paragraph):** `PartRecord` gains `configs` and
`active_config`, `InstanceSpec` gains `config`; the record resolves itself
(`config_params(name)` pure, `effective_params` = config < explicit
overrides — the worker fills defaults). Every geometry consumer switches from
`record.params` to `record.effective_params`, so `_cache_key_for` is
config-aware with no new key field. Pure-config builds go through a derived
record (`_record_for`) and an extracted `_build_with` shared with the
unchanged `_rebuild`, memoized in a separate `_config_status`. A tool pack
(`tools_configs.py`, five tools) and a route pack (`routes_configs.py`,
including a content-addressed mesh route) carry the surface; the merge
driver reaches `configs.<name>.params.<param>`; drawings draw a measured
per-config dimension table; CI emits per-config build rows; the browser gets
a config bar, provenance marks, a tree badge, a placement picker and fetches
assembly meshes by `mesh_key`.

**Tech stack:** Python 3.12, FastAPI, build123d/OCCT (kernel only), vanilla
ES modules + THREE.js, pytest + xdist (`make test`).

**Spec:** [`docs/superpowers/specs/2026-08-17-configurations-design.md`](../specs/2026-08-17-configurations-design.md)
— every decision number below refers to it. Read it first.

## Global constraints (encode these in every slice)

- **Only `agentcad/kernel/` may import `OCP`/build123d.** Nothing in
  `core/` or `server/` does. `tests/test_packages_ocp_free.py` is the probe
  pattern for a new pure module.
- **The kernel never sees a configuration.** Every kernel request carries a
  resolved override map exactly as today (`params`). Resolution happens on
  the way into a request, never inside `ProjectStore.get_part`.
- **`_rebuild(proj, part_id)` and `get_part(proj, part_id)` keep their
  signatures byte-for-byte** — `tools_specs`, `tools_holes`, `tools_packages`
  wrap them with two-positional wrappers. Config builds go through
  `_build_with` / `_ensure_config_built` (Decision 4). `_status` stays a
  2-tuple-keyed dict; matrix/instance builds never write it.
- **Nothing new enters `_cache_key`'s payload.** Config-awareness comes from
  hashing `record.effective_params`. `tests/test_solids.py::…cache_key…` pins
  the bytes; it must not change.
- **Byte-identical without configurations (G5/AC8):** `to_manifest` writes
  `configs`/`active_config`/`config` only when set; base `rebuild_*` events
  gain no key; `set_part_configs` with `{}` pops the key; no `SCHEMA_VERSION`
  bump; existing tests are not edited (a shape-only addition like
  `mesh_key` on `get_assembly` instances is allowed — no test pins that
  dict exactly).
- **Config names match `packages.format.CONFIG_RE`** (`^[a-z0-9][a-z0-9_-]{0,31}$`,
  lowercase). Display names live in `label`. Never `variant`, never `preset`
  for a manifest configuration (Naming traps).
- **One publish per mutating tool call**, after the write, with a `reason`;
  every write of a part's manifest entry runs under
  `packages.manager.manifest_scope(store, proj)` (RMW serialization) and
  `locks.write_scope(part_id)` (the claim guard sees the part).
- **`tools_configs` sorts at `con`** — before `drawing`, `holes`, `packages`,
  `proposals`, `specs`, `vision`. Read `service.specs`, `service.packages`
  etc. inside handlers behind `getattr(service, …, None)`; never touch
  `service.gate_providers`.
- **`build_configs` is serial and de-duplicated by cache key.** No thread
  pool, no `--jobs`. `affinity=part_id` everywhere.
- **Every commit stages a changelog entry** `docs/changelog/NNNN-<slug>.md`
  written from the diff (numbers below), and each entry cites the `make
  test` count (`tests/test_prd011_acceptance.py::test_ac8_the_full_suite_count_is_cited`
  reads the newest entry). Commits end with
  `Co-Authored-By: Claude <noreply@anthropic.com>`.
- **Do not edit `examples/rocketry/parts/flange.py`** (its mesh sha is a
  golden). Do not reword FR1 of the PRD (a PRD-011 test pins its needles).
- **Subagents do not run `git` and do not `uv sync`/`uv pip install`.** Run
  tests with `uv run pytest …`; the orchestrator commits.
- Tests: session-scoped `kernel` fixture; `make_test_service` /
  `clone_test_service` from `tests/conftest.py`; server tests use
  `create_app(service, registry, extra_allowed_hosts={"testserver"})` and
  `TestClient(app, base_url="http://127.0.0.1")`; heavy family tests live in
  `Test<Subject>` classes with ≥ 3 tests and an explicit
  `@pytest.mark.timeout(600)`.

## Slice map

| # | Slice | Lands | Changelog |
|---|---|---|---|
| 0 | Design: spec, plan, PRD → in-progress (this commit) | docs only | 0188 |
| 1 | Model, store, validator, resolution: `PartRecord`/`InstanceSpec` fields and methods, `validate_configurations`, `ProjectStore` read/write/validate, `service.normalize_params`, every consumer → `effective_params`, config-aware cache key | Decisions 1, 2, 3 | 0189 |
| 2 | Build path: `_record_for`, `_build_with`, `_ensure_config_built`, `_config_status`, events with `config`, `mesh_info`/`ensure_mesh(config=)`, `export_part(config=)` (+ tool schema, route whitelist, CLI), `get_part`/`get_project` config fields + divergence | Decisions 3, 4, 5 (state), 8 (export) | 0190 |
| 3 | The tool pack and the routes: `tools_configs.py` (5 tools), `routes_configs.py` (7 routes + `/meshes/{key}`) | Decision 6, 7 (mesh route) | 0191 |
| 4 | Assembly binding end to end: `get_assembly` (config builds, `mesh_key`), `_shape_item` callers via `_record_for`, `mates.resolve`, `sweep_motion`, specs instance/assembly key, stackup warning, `render_view` assembly + packet renders, `assembly_delta` | Decision 7 | 0192 |
| 5 | Merge: `configs.<name>.params.<param>` granularity, `apply_choices` write path, `config_problems`, `merge.py` integration, docstring table | Decision 9 | 0193 |
| 6 | Artifacts: `render_view {config}`, `generate_drawing {config, dim_table}` (tool + handler + route/UI filename), `SpecRunner._shape_tier(record=)` + `spec_results`, CI per-config build rows | Decision 8 | 0194 |
| 7 | Frontend: config bar, provenance marks, tree badge, placement picker, assembly meshes by key, event guard, matrix modal, export/drawing config; AC9 session | Decision 10 | 0195 |
| 8 | Docs, AGENTS/CLAUDE gotchas, acceptance tests `tests/test_prd012_acceptance.py`, roadmap row | — | 0196 |

Slices 1 → 2 → 3 are strictly ordered. 4, 5, 6 depend on 3 (4 and 6 also on
2) and are independent of each other. 7 depends on 3, 4, 6. 8 is last.

---

## Slice 1 — model, store, validator, resolution

**Changelog:** `docs/changelog/0189-configs-model-and-resolution.md`

### Files

- Modify: `agentcad/core/model.py` (`PartRecord`, `InstanceSpec`)
- Modify: `agentcad/core/project.py` (`ProjectStore.get_part`, `instances`,
  `set_instances`, `_update_part_entry` gains `configs=`/`active_config=`)
- Modify: `agentcad/core/packages/format.py` (`validate_configurations`)
- Modify: `agentcad/core/service.py` (`normalize_params` public seam;
  `_cache_key_for`, `_rebuild` build params, `export_part`, `_shape_item` →
  `effective_params`)
- Modify (mechanical `record.params` → `record.effective_params`):
  `agentcad/core/mates.py:64`, `agentcad/core/tools_analysis.py:20,67,113,156`,
  `agentcad/core/specs.py:839,960`, `agentcad/core/tools_drawing.py:25`,
  `agentcad/core/tools_facemod.py:60,71`, `agentcad/core/tools_sketch.py:487`,
  `agentcad/core/tools_sheetmetal.py:21`, `agentcad/core/tools_holes.py:305,709`,
  `agentcad/core/packet.py:916`
- Create: `tests/test_configs.py`
- Modify: `tests/conftest.py` (`FLANGE_SCRIPT`, `THREE_SIZE_CONFIGS`)
- Modify: `tests/test_packages_format.py` (`validate_configurations` cases)

### The shapes

```python
# model.py
@dataclass
class PartRecord:
    id: str
    label: str
    material: str
    params: dict[str, float | int | bool | str] = field(default_factory=dict)
    kind: str = "script"
    source: str | None = None
    solid_materials: dict[str, str] | None = None
    configs: dict[str, dict] | None = None        # NEW — {name: {params, label?, description?}}
    active_config: str | None = None              # NEW

    def to_manifest(self) -> dict:
        ...  # existing keys, then:
        if self.configs:
            data["configs"] = self.configs
        if self.active_config:
            data["active_config"] = self.active_config
        return data

    def config_params(self, name: str) -> dict:
        """Pure-config resolution (defaults < config): a COPY of the declared
        configuration's params. KeyError for an unknown name — every tool
        boundary validates membership before reaching here."""
        return dict((self.configs or {})[name]["params"])

    @property
    def effective_params(self) -> dict:
        """The working state (defaults < active config < explicit overrides).
        An `active_config` the map no longer declares resolves as base."""
        base = {}
        if self.active_config and self.configs \
                and self.active_config in self.configs:
            base = dict(self.configs[self.active_config].get("params") or {})
        base.update(self.params)
        return base

@dataclass
class InstanceSpec:
    ...
    config: str | None = None                     # NEW — a declared config of `part`, or None = live state
    # to_manifest: `if self.config: data["config"] = self.config`
```

```python
# packages/format.py
def validate_configurations(configs, params_spec) -> list[dict]:
    """Problems with a whole `configs` map (parts.<id>.configs, PRD-012).
    Name grammar (CONFIG_RE) per key, then validate_configuration per entry
    with fields re-prefixed `configs.<name>.<field>` — the presets loop
    (validate_presets) and this share the rules by construction."""
    if not isinstance(configs, dict):
        return [problem("wrong_type", "configs must be an object of name -> configuration")]
    out = []
    for name, entry in configs.items():
        cfield = f"configs.{name}"
        if not CONFIG_RE.match(str(name)):
            out.append(problem("bad_value", f"configuration name {name!r} must match {CONFIG_RE.pattern}", field=cfield))
        for item in validate_configuration(entry, params_spec):
            item = dict(item)
            item["field"] = f"{cfield}.{item['field']}" if item.get("field") else cfield
            out.append(item)
    return out
```

```python
# service.py (public seam; wraps the private _normalize_param)
def normalize_params(self, spec: dict, values: dict) -> dict:
    """Coerce/canonicalize `values` against a kernel-normalized PARAMS spec the
    way set_params does (int for int params, declared enum choice, float for
    numbers). Raises ValidationError; unknown names raise too."""
    out = {}
    unknown = sorted(set(values) - set(spec))
    if unknown:
        raise ValidationError(f"unknown parameter(s): {', '.join(unknown)}",
                              {"unknown": unknown, "known": sorted(spec)})
    for name, value in values.items():
        out[name] = _normalize_param(name, spec[name], value)
    return out
```

`ProjectStore.get_part` reads `configs=entry.get("configs")`,
`active_config=entry.get("active_config")`; `ProjectStore.instances` reads
`config=i.get("config")`. `_update_part_entry` gains keyword-only
`configs: dict | None = None` (an empty dict **pops** the key; a non-empty
dict replaces it) and `active_config: str | None | _UNSET` (a sentinel so
"set to None" is expressible; `None` pops the key). `set_instances`
validates, beside the unknown-part refusal:

```python
if inst.config is not None:
    part_entry = next(p for p in manifest["parts"] if p["id"] == inst.part)
    if part_entry.get("kind", "script") != "script":
        raise ValidationError(f"instance {inst.id!r}: reference/imported parts have no parameters and cannot bind a configuration")
    declared = part_entry.get("configs") or {}
    if inst.config not in declared:
        raise ValidationError(f"instance {inst.id!r}: part {inst.part!r} declares no configuration {inst.config!r} (declares {sorted(declared)})", {"declared": sorted(declared)})
```

### Tasks

- [ ] **Task 1 — fixtures.** Add `FLANGE_SCRIPT` to `tests/conftest.py`
  (copy `tests/test_drawings.py`'s `FLANGE`; every param already carries
  unit + description) and
  `THREE_SIZE_CONFIGS = {"s": {"params": {"outer_d": 100.0, "bore_d": 50.0, "bc_d": 80.0}, "label": "Small"}, "m": {"params": {"outer_d": 140.0, "bore_d": 80.0, "bc_d": 118.0}, "label": "Medium"}, "l": {"params": {"outer_d": 200.0, "bore_d": 120.0, "bc_d": 170.0}, "label": "Large"}}`.
- [ ] **Task 2 — `validate_configurations`** in `packages/format.py` (shape
  above), tests first in `tests/test_packages_format.py`: a good map is `[]`;
  an uppercase name is a `bad_value` at `configs.M`; a flat entry's problem
  is re-prefixed `configs.s`; a bad param is at `configs.m.params.width`;
  a non-dict map is one `wrong_type`. Keep the module OCP-free (probe test).
- [ ] **Task 3 — `PartRecord`/`InstanceSpec`** fields, `to_manifest`
  conditionals, `config_params`, `effective_params`. Tests: round trip via
  `to_manifest()` omits the keys when unset; `effective_params` order
  (config < overrides); dangling `active_config` resolves as base;
  `config_params` copies (mutating the result does not mutate the record).
- [ ] **Task 4 — `ProjectStore`**: read the fields; `_update_part_entry`
  keyword args; `set_instances` validation (unknown config, reference part).
  Tests: a manifest written without the keys loads and saves byte-identical
  (`json.dumps(sort_keys=True)` before/after a `set_params` and a
  `set_instances`); a manifest with `configs` survives `set_params` (in-place
  entry edit); an instance `config` survives `set_instances` round trip
  (`tools_mates`-style read-all/write-all); unknown config refused with
  `declared` in details.
- [ ] **Task 5 — `service.normalize_params`** + tests (int coercion, enum
  canonicalization, unknown name).
- [ ] **Task 6 — the mechanical rename.** Every listed `record.params` →
  `record.effective_params` (service.py: `_cache_key_for`, `_rebuild` build
  params, `export_part`, `_shape_item`; the eleven pack sites). Then run the
  full suite: `uv run pytest -q -x` must stay green — no configuration exists
  yet, so `effective_params == params` everywhere.
- [ ] **Task 7 — cache-key tests.** With `configs` set on a record by hand
  (`store.update_part_entry(..., configs=…)`) and `active_config`, assert
  `_cache_key_for` equals the key of a record whose `params` are the config's
  params (identity), and that a part with no configs has the same key as
  before (compare against a hand-computed `sha256` of the pinned payload the
  way `tests/test_solids.py` does).

### Tests (`tests/test_configs.py`, `tests/test_packages_format.py`)

Module-level functions (cheap): the model, store, normalize, validator,
cache-key cases above — at least 14 tests.

### Verification

`uv run pytest tests/test_configs.py tests/test_packages_format.py tests/test_service.py tests/test_solids.py tests/test_manifest_merge.py -q` green, then
`make test` green (cite the count in `0189-…`).

---

## Slice 2 — the build path

**Changelog:** `docs/changelog/0190-config-build-path.md`

### Files

- Modify: `agentcad/core/service.py`
- Modify: `agentcad/core/tools.py:202-216` (`export_part` schema + lambda)
- Modify: `agentcad/server/app.py:265-270` (`export` route whitelist `config`)
- Modify: `agentcad/cli.py` (`export --config`)
- Modify: `tests/test_configs.py` (a `TestFlangeFamily` class)

### The shapes

```python
_UNSET = object()

def _record_for(self, proj: str, part_id: str, config: str | None = None):
    """The stored record (config=None: the working state) or a DERIVED record
    whose params are the pure configuration map (defaults < config)."""
    record = self.store.get_part(proj, part_id)
    if config is None:
        return record
    if record.kind != "script":
        raise ValidationError(f"part {part_id!r} is a reference part and has no configurations")
    if not record.configs or config not in record.configs:
        raise ValidationError(f"part {part_id!r} declares no configuration {config!r}",
                              {"declared": sorted(record.configs or {})})
    return dataclasses.replace(record, params=record.config_params(config), active_config=None)

def _build_with(self, proj: str, record, *, affinity: str, status_key: tuple | None,
                config: str | None = None) -> dict:
    """The body of today's _rebuild (lines 625-746), parameterized: key from
    _cache_key_for(record) (which hashes effective_params); build params =
    record.effective_params; events gain **({"config": config} if config else {});
    _status written only when status_key is not None. Same sidecar
    read/write, same return shape."""

def _rebuild(self, proj: str, part_id: str) -> dict:          # signature unchanged
    record = self.store.get_part(proj, part_id)
    return self._build_with(proj, record, affinity=part_id,
                            status_key=self._status_key(proj, part_id))

def _config_status_key(self, proj, part_id, config) -> tuple[str, str, str]:
    return (self.store.lock_key(proj), part_id, config)

def _ensure_config_built(self, proj: str, part_id: str, config: str) -> dict:
    """_ensure_built for a pure configuration: memo in _config_status, never
    _status; a hit publishes nothing (this is what keeps a bound assembly
    quiet — Decision 4)."""
    record = self._record_for(proj, part_id, config)
    key = self._cache_key_for(proj, record)
    memo = self._config_status.get(self._config_status_key(proj, part_id, config))
    if memo is not None and memo["cache_key"] == key:
        if memo["state"] == "ok" and (self.store.cache_dir(proj) / f"{key}.acm").is_file():
            return {"ok": True, "metrics": memo["metrics"], "warnings": memo["warnings"],
                    "lods": memo["lods"], "cache_key": key}
        if memo["state"] == "error":
            return {"ok": False, "error": memo["error"]}
    result = self._build_with(proj, record, affinity=part_id, status_key=None, config=config)
    self._config_status[self._config_status_key(proj, part_id, config)] = {
        "state": "ok" if result["ok"] else "error", "cache_key": key,
        "metrics": result.get("metrics"), "warnings": result.get("warnings", []),
        "lods": result.get("lods", []), "error": result.get("error")}
    return result

def mesh_info(self, proj, part_id, lod=None, *, config: str | None = None) -> dict:
    # config given → _ensure_config_built; else unchanged. Same {path, key, lod}.
def ensure_mesh(self, proj, part_id, *, config: str | None = None) -> Path

def export_part(self, proj, part_id, format, tolerance=EXPORT_TOLERANCE, *, config: str | None = None) -> dict:
    record = self._record_for(proj, part_id, config)
    out = self.store.exports_dir(proj) / (f"{part_id}.{format}" if config is None else f"{part_id}_{config}.{format}")
    ... params = record.effective_params ...; result["config"] = config (only when given)
```

`get_part` adds top-level `configs` (`record.configs or {}`), `active_config`
(`record.active_config`), and inside `status`: `diverged` and
`diverged_params` computed by a helper:

```python
def _divergence(record) -> tuple[bool, list[str]]:
    if not record.active_config or not record.configs or record.active_config not in record.configs:
        return False, []
    pure = record.config_params(record.active_config)
    eff = record.effective_params
    names = sorted(k for k in set(pure) | set(eff) if pure.get(k, _UNSET) != eff.get(k, _UNSET))
    return bool(names), names
```

`get_project` part rows add `configs` and `active_config`. `_forget_status`
and `delete_part` also sweep `_config_status` (prefix `(lock_key, part_id)`).

### Tasks

- [ ] **Task 1 — extract `_build_with`**; `_rebuild` becomes the thin
  shell. Run `uv run pytest tests/test_service.py tests/test_specs.py tests/test_holes.py -q`
  green (the wrappers still see two positionals).
- [ ] **Task 2 — `_record_for` + `_ensure_config_built` + `_config_status`
  + key + sweeps.** Tests (class `TestFlangeFamily`, module-scoped template
  project with `FLANGE_SCRIPT` and `THREE_SIZE_CONFIGS` written via
  `store.update_part_entry(configs=…)`, cloned per test): three configs
  build with three distinct `mass_g`; a second `_ensure_config_built` call
  publishes no `rebuild_*` event (subscribe to the bus queue); `_status` has
  no 3-tuple keys after a config build; a config build's `rebuild_started`
  carries `"config": "m"` and a base rebuild's does not have the key; a
  config sharing `m`'s params (`"m2"`) returns `m`'s `cache_key` and issues
  no kernel `build` (the `_counting` monkeypatch pattern from
  `tests/test_specs_api.py`).
- [ ] **Task 3 — `mesh_info`/`ensure_mesh(config=)`** + test that the key
  differs per config and matches `_ensure_config_built`'s.
- [ ] **Task 4 — `export_part(config=)`** + tool schema (`"config":
  {"type": "string", "description": "Configuration to export (pure config
  resolution); omit for the current state"}`) + `app.py` route forwards
  `body.get("config")` + `cli.py` `--config`. Tests: base export
  `flange.step` unchanged; `config="l"` → `flange_l.step`, `size_bytes > 500`,
  result carries `"config": "l"`; unknown config → `ValidationError`.
- [ ] **Task 5 — `get_part`/`get_project` fields + `_divergence`.** Tests:
  fields present (`configs: {}`, `active_config: None`) for a plain part;
  with `active_config="m"` and an override `thick=20` → `status.diverged`
  is True and `diverged_params == ["thick"]`; an override equal to the
  configuration's value is not divergence; `set_params(None)` returns to
  `diverged: False` and to `m`'s cache key.

### Verification

`uv run pytest tests/test_configs.py -q` (≥ 12 new tests) then `make test`
green; count cited in `0190-…`.

---

## Slice 3 — the tool pack and the routes

**Changelog:** `docs/changelog/0191-configs-tools-and-routes.md`

### Files

- Create: `agentcad/core/tools_configs.py`
- Create: `agentcad/server/routes_configs.py`
- Create: `tests/test_configs_api.py`
- Modify: `agentcad/core/service.py` (`set_assembly` reads `config` from
  each item; `set_instance_config`-shaped store write lives in the pack)

### The shapes

```python
# tools_configs.py
"""Tool pack: configurations (PRD-012) — set_part_configs, list_configs,
build_configs, set_active_config, set_instance_config. Loads at `con`: read
service.specs/service.packages inside handlers, never at register()."""
from . import locks
from .model import ConflictError, NotFoundError, ValidationError
from .packages import format as pkgformat
from .packages.manager import manifest_scope
from .tools import Tool, schema, with_hint

def register(registry, service) -> None:
    def _spec_for(project, part_id):
        record = service.store.get_part(project, part_id)
        if record.kind != "script":
            raise ValidationError(f"part {part_id!r} is a reference/imported part and has no parameters to configure")
        script = service.store.read_script(project, part_id)
        spec = service._params_spec(script)
        if spec is None:
            raise ValidationError("cannot set configurations: the part script does not currently load — fix the script first (see get_part.status)")
        return record, spec

    def _referrers(project, part_id, names: set[str]) -> dict[str, list[str]]:
        """{config name: [instance ids]} for names bound by assembly instances."""

    def set_part_configs(project, part_id, configs) -> dict:
        if not isinstance(configs, dict): raise ValidationError("configs must be an object of name -> configuration")
        record, spec = _spec_for(project, part_id)
        problems = pkgformat.validate_configurations(configs, spec)
        if problems:
            raise ValidationError("invalid configurations: " + "; ".join(p["message"] for p in problems),
                                  {"problems": problems, "part_id": part_id})
        normalized = {name: {**entry, "params": service.normalize_params(spec, entry["params"])}
                      for name, entry in configs.items()}          # preserves order
        removed = set(record.configs or {}) - set(normalized)
        bound = _referrers(project, part_id, removed)
        active_hit = record.active_config in removed
        if bound or active_hit:
            names = sorted(removed & (set(bound) | ({record.active_config} if active_hit else set())))
            raise ConflictError(
                f"configuration(s) {', '.join(names)} of part {part_id!r} are referenced: "
                + "; ".join(f"{n} by instance(s) {', '.join(ids)}" for n, ids in sorted(bound.items()))
                + ("; active_config" if active_hit else ""),
                {"part": part_id, "configs": names, "instances": sorted({i for ids in bound.values() for i in ids}),
                 "active_config": active_hit})
        with manifest_scope(service.store, project), locks.write_scope(part_id):
            service.store.update_part_entry(project, part_id, configs=normalized)   # {} pops the key
        service.bus.publish({"type": "project_changed", "project": project, "part": part_id, "reason": "configs"})
        out = {"part_id": part_id, "configs": normalized, "active_config": record.active_config}
        active = record.active_config
        if active and active in normalized and normalized[active]["params"] != (record.configs or {}).get(active, {}).get("params"):
            out["rebuild"] = with_hint(service._rebuild(project, part_id))
        return out

    def list_configs(project, part_id=None) -> dict:  # {"parts": [{part_id, configs, active_config, diverged, diverged_params, referrers}]}
    def build_configs(project, part_id=None, configs=None) -> dict:
        # rows in family order; group by _cache_key_for(derived) → build each key once via
        # service._ensure_config_built(project, pid, name) for the FIRST name of a group and
        # copy the result (cached: True) to the others; row = {name, label, ok, cached, cache_key,
        # metrics, warnings, error?}; spec_results filled in Slice 6.
        # single part → {"part_id", "configs": rows}; project-wide → {"parts": [{part_id, configs}]},
        # empty → {"parts": [], "warnings": ["no configured parts"]}
    def set_active_config(project, part_id, config=None, keep_overrides=False) -> dict:
        # validate membership; under scopes: update_part_entry(active_config=config or None,
        # params={} unless keep_overrides); publish once (reason "active_config");
        # return {**with_hint(service._rebuild(project, part_id)), "part_id", "active_config",
        #         "diverged", "diverged_params", "cleared_overrides": {...}}
    def set_instance_config(project, instance, config=None) -> dict:
        # read store.instances, set inst.config (None unbinds), store.set_instances (validates),
        # publish once (reason "instance_config"), return service.get_assembly(project)
```

`set_assembly` (service.py:465-481) reads `config=item.get("config")` into
`InstanceSpec`.

```python
# routes_configs.py — copy _RAISE/_BODY_ERRORS/_result/_body_keys/_json from routes_specs.py
GET    /projects/{proj}/configs                          -> registry.call("list_configs", {"project"})
GET    /projects/{proj}/parts/{part_id}/configs          -> list_configs {project, part_id}
PUT    /projects/{proj}/parts/{part_id}/configs          -> set_part_configs {project, part_id, configs: body["configs"]}
PUT    /projects/{proj}/parts/{part_id}/active-config    -> set_active_config {..., **_body_keys(body, "config", "keep_overrides")}
DELETE /projects/{proj}/parts/{part_id}/active-config    -> set_active_config {project, part_id}   (back to base)
POST   /projects/{proj}/configs/build                    -> build_configs {project, **_body_keys(body, "part_id", "configs")}
PATCH  /projects/{proj}/assembly/instances/{iid}/config  -> set_instance_config {project, instance, "config": body.get("config")}  # forwarded when "config" in body, null unbinds
GET    /projects/{proj}/meshes/{key}?lod=                -> the content-addressed mesh:
        if not re.fullmatch(r"[0-9a-f]{32}", key): raise NotFoundError
        cache = service.store.cache_dir(proj); path = cache / f"{key}.{lod}.acm" if lod and _LOD_RE.match(lod) and exists else cache / f"{key}.acm"
        404 NotFoundError("mesh not built") if missing; Response(bytes, application/octet-stream,
        headers Cache-Control: no-store, X-Mesh-Key: key, X-Mesh-Lod: lod|full)
```

### Tasks

- [ ] **Task 1 — pack skeleton + `set_part_configs`** (validate → normalize
  → referrers → write → publish → return). Tests through `build_registry`:
  a good family is stored in order and returned; uppercase name →
  `validation_error` with `problems`; out-of-range → `validation_error`;
  reference part refused; a broken script refused with the `set_params`
  wording; `configs: {}` pops the key (raw `project.json` read); exactly one
  `project_changed` per call (bus subscription); rebuild key present only
  when the active config's params changed.
- [ ] **Task 2 — FR11 conflict.** Bind an instance to `l` via
  `set_assembly` (`{"id": "f1", "part": "flange", "config": "l"}`), then
  `set_part_configs` without `l` → `conflict_error`, `details.instances ==
  ["f1"]`, `details.configs == ["l"]`; set `active_config="l"` and remove
  → conflict with `details.active_config is True`; unbind, then removal
  succeeds and `get_part` no longer lists `l`.
- [ ] **Task 3 — `set_active_config`.** Tests: switching clears overrides
  (`params == {}` in the manifest, `cleared_overrides` echoed) and returns
  the rebuild; `keep_overrides=True` keeps them and reports `diverged`;
  `config=None` returns to base; unknown name → `validation_error` naming
  the declared names.
- [ ] **Task 4 — `list_configs`, `build_configs`.** Tests: `list_configs`
  reports `referrers` for a bound name; `build_configs {part_id}` returns
  three rows in family order with distinct masses; a family with a config
  that cannot build (`outer_d` < `bore_d` — the script's Cylinder subtract
  still builds, so use a config on a second part whose script raises on a
  param, e.g. `if p.thick > 50: raise ValueError`) returns `ok: false` +
  `error` in place beside `ok: true` rows; a config with `m`'s params is
  `cached: True` with `m`'s key and no extra kernel `build`; project-wide
  shape; empty project → the warning.
- [ ] **Task 5 — `set_instance_config`** + `set_assembly` reads `config`.
  Tests: bind, `get_assembly` shows `config`; unknown config → validation
  error from the store; unbind with `None`.
- [ ] **Task 6 — routes** (`tests/test_configs_api.py` with the
  `create_app`/`TestClient` rig): each route round-trips; `DELETE
  active-config` returns to base; `PUT` with `{"config": null}` is a 422
  (documented — use DELETE); `PATCH …/config {"config": null}` unbinds;
  `GET /meshes/{key}` serves bytes with `X-Mesh-Key`, 404 for an unbuilt or
  malformed key, `?lod=lod1` falls back to full with `X-Mesh-Lod: full` when
  no tier exists.

### Verification

`uv run pytest tests/test_configs.py tests/test_configs_api.py -q` (≥ 20
new tests) then `make test`; count in `0191-…`. Also
`uv run agentcad mcp` unaffected: `registry.tools()` lists the five names
(one assertion in the api test).

---

## Slice 4 — assembly binding end to end

**Changelog:** `docs/changelog/0192-config-bound-instances.md`

### Files

- Modify: `agentcad/core/service.py` (`get_assembly`, `check_interference`,
  `export_assembly` → `_record_for(inst.config)`)
- Modify: `agentcad/core/mates.py:54-65`
- Modify: `agentcad/core/tools_motion.py:64-66`
- Modify: `agentcad/core/specs.py` (`_instance_item`, `_assembly_key`)
- Modify: `agentcad/core/tools_stackup.py` (warning row)
- Modify: `agentcad/core/tools_vision.py` (assembly path `ensure_mesh(config=inst.config)`)
- Modify: `agentcad/core/packet.py` (assembly render `ensure_mesh(config=)`;
  `assembly_delta` `configs_changed`)
- Create: `tests/test_configs_assembly.py`

### The shapes

- `get_assembly`: for each resolved instance,
  `built = self._ensure_config_built(proj, inst.part, inst.config) if inst.config else self._ensure_built(proj, inst.part)`;
  `entry["mesh_key"] = built["cache_key"]` when `built["ok"]`; `entry`
  already carries `config` via `to_manifest()`.
- `_shape_item(proj, record, resolved)` unchanged; callers pass
  `self._record_for(proj, inst.part, inst.config)`.
- `mates.resolve`: `record = service._record_for(proj, inst.part, inst.config)`;
  `item["params"] = record.effective_params`.
- `specs._assembly_key`: `record = self.service._record_for(proj, instance.part, getattr(instance, "config", None))`.
- `tools_stackup`: for each path instance with `config`, append
  `f"instance {iid} (part {part_id}) is bound to configuration {config!r}: tolerances are per part, the nominal is per configuration"`.
- `packet.assembly_delta`: `configs = [{"id", "old", "new"}]` where
  `before.get("config") != after.get("config")`; include in `changed` and
  return as `"configs_changed": configs`.

### Tasks

- [ ] **Task 1 — `get_assembly` + `mesh_key`** (AC6 half 1): two instances
  of `flange` bound to `s` and `l` → different `mass_g`, `total_mass_g` is
  their sum, each entry has `mesh_key`, the two keys differ; an unbound
  instance's `mesh_key` equals `mesh_info(...)["key"]`; a second
  `get_assembly` publishes no `rebuild_*` (the livelock test).
- [ ] **Task 2 — `check_interference` / `export_assembly` / `sweep_motion`
  / specs items via `_record_for`** (AC6 half 2): place `s` and `l`
  instances so only the L pair overlaps (two `l` instances at the same
  position, one `s` far away) → `check_interference` reports exactly the L
  pair; `sweep_motion` on a mated config-bound instance runs (smoke);
  `specs._assembly_key` differs when an instance is rebound (unit test on
  the key string).
- [ ] **Task 3 — mates.** A mated instance bound to a configuration
  resolves its connector from the config's geometry (use a script whose
  connector position depends on a configured param; assert the resolved
  position moves between `s` and `l`).
- [ ] **Task 4 — renders + packet + stackup.** `render_view` (assembly)
  with bound instances returns a PNG (smoke; and `ensure_mesh` was called
  with `config=`, via monkeypatch spy); `assembly_delta` reports
  `configs_changed` for a rebinding with unchanged mass and `changed is
  True`; `tolerance_stackup` over a bound path emits the warning row.

### Verification

`uv run pytest tests/test_configs_assembly.py tests/test_mates.py tests/test_motion.py tests/test_packet.py tests/test_specs.py -q` then `make test`; count in `0192-…`.

---

## Slice 5 — the merge

**Changelog:** `docs/changelog/0193-configs-merge-granularity.md`

### Files

- Modify: `agentcad/core/manifest_merge.py` (`_PART_ENTRY_DICTS`,
  `_merge_keyed_entries`, `_write_keyed_entry`, `config_problems`, docstring
  table)
- Modify: `agentcad/core/merge.py:620` (`+ manifest_merge.config_problems(merged)`
  split into integrity vs warnings) and `_summarize`
- Modify: `tests/test_manifest_merge.py` (new section + `KEY_CLASSES` rows)
- Modify: `docs/architecture.md`, `docs/packages.md`,
  `docs/superpowers/specs/2026-08-09-branching-version-control-design.md`
  key-space tables (one row block each)

### The shapes

A verified prototype of the driver half exists at
`/private/tmp/claude-501/-Users-nfedorov--supacode-repos-cad-claude-parallel/763899f3-3439-4251-a572-2dce24917b58/scratchpad/proto/agentcad/core/manifest_merge.py`
(all 82 existing tests pass against it); diff it against the tree and adopt:

```python
_PART_ENTRY_DICTS = {"configs": ("params",)}   # beside _PART_SUBDICTS; _write_entry knows the same set

# in _merge_entry's field loop:
elif field in _PART_ENTRY_DICTS and _keyed(b, o, t):
    value = _merge_keyed_entries((*segs, field), b, o, t, conflicts, subdicts=_PART_ENTRY_DICTS[field])

def _merge_keyed_entries(prefix, base, ours, theirs, conflicts, *, subdicts):
    result = {}
    for name in _key_order(_as_dict(ours), _as_dict(theirs)):
        b, o, t = _get(base, name), _get(ours, name), _get(theirs, name)
        value = (_merge_entry((*prefix, name), b, o, t, conflicts, subdicts)
                 if _keyed(b, o, t) else _merge_atomic((*prefix, name), b, o, t, conflicts))
        if value is not _MISSING:
            result[name] = value
    return result

# in _write_entry:
elif len(rest) >= 2 and rest[0] in _PART_ENTRY_DICTS:
    _write_keyed_entry(entry.setdefault(rest[0], {}), rest[1], rest[2:], value, present, _PART_ENTRY_DICTS[rest[0]], key)

def _write_keyed_entry(container, name, rest, value, present, subdicts, key):
    if not rest: _write_slot(container, name, value, present); return
    inner = container.get(name)
    if not isinstance(inner, dict):
        raise ValidationError(f"cannot resolve {key!r}: configuration {name!r} is not present")
    if len(rest) == 2 and rest[0] in subdicts: _write_slot(inner.setdefault(rest[0], {}), rest[1], value, present)
    else: _write_slot(inner, rest[0] if len(rest) == 1 else ".".join(rest), value, present)

def config_problems(manifest: dict) -> list[dict]:
    """Post-merge damage the key-wise merge cannot see (Decision 9):
    {"kind": "dangling_instance_config", "instance", "part", "config", "message"} — BLOCKING
    {"kind": "dangling_active_config", "part", "config", "message"}             — a warning
    Silent on a config-free project, on `{}`, on a healthy family; skips instances whose
    part is missing (dangling_instance already says so)."""
```

`merge.py`: `problems = manifest_merge.config_problems(merged)`;
`report["integrity"] += [p for p in problems if p["kind"] == "dangling_instance_config"]`;
`report["warnings"] += [p["message"] for p in problems if p["kind"] == "dangling_active_config"]`
(add `report["warnings"]` if the report has no such list yet — check
`merge._validate`'s report shape first and follow it).

### Tasks

- [ ] **Task 1 — tests first**, section `# ---- PRD-012: the configs map` in
  `tests/test_manifest_merge.py`: (1) two branches adding different names
  merge clean; (2) different params of one config merge clean; (3) the same
  param at two values conflicts at
  `parts.flange.configs.m.params.bolt_d` with the exact payload and key
  order `CONFLICT_KEYS`; (4) add/add same name divergent → whole-config
  conflict, `'base' not in conflict`; (5) delete/modify → whole-config
  conflict, `apply_choices take theirs` restores / `take ours` removes;
  (6) resolving a param conflict writes into the map (`[k for k in entry
  if '.' in k] == []`); (7) label and params merge independently; (8) a
  non-dict entry (`{"m": 5}`, `{"m": None}`) survives whole; (9) a dotted
  config name still resolves through `path` (X13 sibling); (10) a manifest
  without configs is untouched; (11) `active_config` and instance `config`
  merge as whole values; plus two `KEY_CLASSES` rows
  (`parts.flange.configs.m.params.bolt_d`, `parts.flange.configs.m.label`)
  with `sample()` carrying a `configs` map. Run: all red for the new
  behaviour, the 82 old ones green.
- [ ] **Task 2 — the driver** (adopt the prototype), rerun: green.
- [ ] **Task 3 — `config_problems`** + four tests (dangling active, dangling
  instance binding, silent on healthy, silent on config-free/`{}`).
- [ ] **Task 4 — `merge.py` integration** + one orchestrator test driving
  two real branches through `merge_branch` (the
  `tests/test_packages_index.py::test_a_real_merge_blocks_on_the_package_hybrid`
  shape) asserting the dangling binding lands in `validation.integrity`
  and blocks, and the dangling active config is a warning that does not.
- [ ] **Task 5 — docstring table + the three docs' key-space tables.**

### Verification

`uv run pytest tests/test_manifest_merge.py tests/test_merge.py tests/test_branches.py -q` then `make test`; count in `0193-…`.

---

## Slice 6 — artifacts: renders, drawings, specs, CI

**Changelog:** `docs/changelog/0194-config-artifacts-drawings-specs-ci.md`

### Files

- Modify: `agentcad/core/tools_vision.py` (`render_view {config?}`)
- Modify: `agentcad/core/tools_drawing.py` (`config?`, `dim_table?`,
  filename, timeout)
- Modify: `agentcad/kernel/handlers/drawing.py` (`_build_svg(dim_table=)`,
  `_dim_table` renderer, per-row measurement in `drawing()`)
- Modify: `agentcad/server/routes_drawing.py:26` (filename mirror; accept
  `config`/`dim_table` body keys on the POST)
- Modify: `agentcad/core/specs.py` (`_shape_tier(..., *, record=None)`)
- Modify: `agentcad/core/tools_configs.py` (`spec_results` rows)
- Modify: `agentcad/core/checks.py` (`_stage_build` per-config rows)
- Create: `tests/test_configs_drawing.py`, `tests/test_configs_checks.py`
- Modify: `tests/test_configs.py` (spec_results)

### The shapes

```python
# tools_drawing.generate_drawing(project, part_id, views=None, format="svg", config=None, dim_table=False)
record = service._record_for(project, part_id, config)      # ValidationError for unknown
suffix = f"_{config}" if config else ""
out = exports_dir / f"{part_id}{suffix}_drawing.{format}"
request = {..., "params": record.effective_params, ...}
if dim_table and record.configs:                             # a part with no configs: no table, no error
    names = list(record.configs)                             # family order
    columns = []                                             # configured params, union, first-seen order
    for entry in record.configs.values():
        for k in entry["params"]:
            if k not in columns: columns.append(k)
    request["dim_table"] = {"rows": [{"config": n, "label": record.configs[n].get("label") or n,
                                      "params": record.config_params(n)} for n in names],
                            "columns": columns}
    timeout = 120.0 + 60.0 * len(names)
result["config"] = config (when given); result["dim_table"] echoed from detected
```

Handler (`drawing()`): when `params.get("dim_table")`, for each row build
`shape_i = build_shape(script, row["params"])`, measure the overall extents
`X, Y, Z` from `shape_i.bounding_box()` (the same numbers the front/top
overall dims print are the projected bbox; use the world bbox — say so in
`detected`), record `values = {**row["params"], "X": x, "Y": y, "Z": z}`,
`ok`/`error` per row (a row that fails to build is `ok: false` and the table
prints `—`), truncate beyond 8 rows with a warning. `_build_svg(...,
dim_table=None)` calls `_dim_table(rows, columns, x=264.0, y_top=18.0,
row_h=4.5)`: header row (`config`, then columns, then `X`, `Y`, `Z`), one
boxed cell per value (`<rect … {_BOX}/>` + `_text(...)`), **every string
through `_esc`**, col width `max(14, 2.2*len(longest)+4)` capped so the table
fits 150 mm (drop trailing columns with a warning if not). `detected["dim_table"] = {"columns": [...], "rows": [{"config", "label", "values", "ok", "error"?}], "placement": "right-column", "warnings": [...]}`.
DXF ignores it.

`SpecRunner._shape_tier(self, proj, part_id, cache_key=None, deadline=None, refresh=False, *, record=None)`:
`record = record or store.get_part(...)`; `key = cache_key or _cache_key_for(proj, record)`;
`"params": record.effective_params`. `build_configs` row:
`specs = getattr(service, "specs", None)`; if specs and `declares_specs(script)`:
`payload, cached, _ = specs._shape_tier(project, pid, record=derived)` →
`row["spec_results"] = {"checks": payload["checks"], "cached": cached}` (a
`KernelError` → `row["spec_results"] = {"error": payload}`).

`checks._stage_build`: after each part's row, for `name in (entry.get("configs") or {})`:
budget check → `self._budget_item("build", "part", f"{part_id}@{name}", …)`
else `_config_item(proj, part_id, name, …)` calling
`self.service._ensure_config_built(proj, part_id, name)` and making
`make_item("build", "part", f"{part_id}@{name}", "pass"|"fail", message,
details={"config": name, "cache_key": …, "cached": …})` (mirror
`_build_item`'s branches; `_is_cached` has a config variant reading the
memo).

### Tasks

- [ ] **Task 1 — `render_view {config?}`**: validate via `_record_for`,
  `ensure_mesh(config=)`, filename `renders/<part>_<config>_<view>.png`.
  Test: PNG returned, path suffix, unknown config refused.
- [ ] **Task 2 — drawing tool + handler** (AC2): tests in
  `tests/test_configs_drawing.py` — `generate_drawing {config: "l"}` writes
  `flange_l_drawing.svg` and `detected` reflects L's extents; `dim_table:
  true` on the family → SVG contains each label exactly once and each
  config's `outer_d` value, `detected.dim_table.rows` has three `ok` rows
  in family order with `X/Y/Z` present; a label with `&` yields a
  well-formed SVG (`xml.etree.ElementTree.fromstring` parses); DXF with
  `dim_table` writes the same file as without; a part with no configs and
  `dim_table: true` renders the base drawing byte-identical to a plain call
  (compare bytes); `routes_drawing` `POST` forwards `config`/`dim_table`
  and the SVG GET reads the suffixed file when `?config=` is passed.
- [ ] **Task 3 — `_shape_tier(record=)` + `spec_results`**: a family whose
  script declares `SPECS` (add `SPECS = [check_wall(min_mm=3.0)]`-style
  declaration to a second fixture script) → each `build_configs` row has
  `spec_results.checks`, and a config-keyed sidecar's `measured` differs
  from base (a silent params fall-through is the failure to catch).
- [ ] **Task 4 — CI rows**: `tests/test_configs_checks.py` with the
  `tests/test_checks_pipeline.py` `stack` fixture: a configured part
  yields `build` rows `flange`, `flange@s`, `flange@m`, `flange@l`;
  `--budget` too small → the config rows are `skip`/`budget_exceeded`;
  `validate_report` accepts the report; a config-free project's report is
  unchanged (row set equality against a pre-recorded list).

### Verification

`uv run pytest tests/test_configs_drawing.py tests/test_configs_checks.py tests/test_configs.py tests/test_drawings.py tests/test_checks*.py tests/test_specs*.py -q` then `make test`; count in `0194-…`.

---

## Slice 7 — the frontend

**Changelog:** `docs/changelog/0195-configs-ui.md` (must contain the AC9
session block with `ERROR COUNT: 0`)

### Files

- Modify: `frontend/index.html` (`#config-bar` host; matrix modal markup;
  drawings panel checkbox)
- Modify: `frontend/js/inspector.js` (`renderConfigBar`, `setActiveConfig`,
  `markConfigSources`, matrix button hook)
- Modify: `frontend/js/tree.js` (badge; `part@config` on instance rows)
- Modify: `frontend/js/placement.js` (config picker; `config` in `sig`)
- Modify: `frontend/js/api.js` (`listConfigs`, `setActiveConfig`,
  `clearActiveConfig`, `buildConfigs`, `setInstanceConfig`, `getMeshByKey`)
- Modify: `frontend/js/main.js` (`instanceMeshes` by `mesh_key`;
  `loadAssembly`/`renderAssemblyFromCache`/`reloadMesh`; `handleEvent`
  guard; `runExport` passes `config`; `configs.init(actions)` in boot)
- Create: `frontend/js/configs.js` (matrix modal)
- Modify: `frontend/js/drawings.js` (`config`/`dim_table` in preview + save)
- Modify: `frontend/css/app.css` (`#config-bar`, `.cfg-chip`,
  `.param-from-config`, `.param-overridden`; existing tokens only)
- Modify: `tests/test_prd012_acceptance.py` structural half is in Slice 8;
  this slice adds nothing to tests beyond keeping `tests/test_server.py`
  green

### The shapes (functions, by file)

- `inspector.js`: `let configBarEl, configSig;`
  `function renderConfigBar(part)` (hidden when `!part.configs || !Object.keys(part.configs).length`;
  `<select>` rebuilt only when `JSON.stringify([part.id, part.active_config, Object.keys(part.configs)])` changes;
  option `base` + one per config showing `label || name`; chip
  `<span class="cfg-chip diverged" title="overrides: thick, bore_d">M — modified</span>`
  when `part.status.diverged`; a `Reset to M` button calling
  `setActiveConfig(part.active_config)`; a `Matrix` button calling
  `configs.open(part.id)`), `async function setActiveConfig(name)` (the
  `setMaterial` shape with the `handleWriteConflict` retry; `name === "base"`
  → `api.clearActiveConfig`), `function markConfigSources(part)` (toggle
  `param-from-config` when `part.active_config && part.configs[active].params` has the name;
  `param-overridden` when `part.params` has the name) called right before
  `decorateParams()`.
- `tree.js`: after the `ref` badge block, `if (part.configs && Object.keys(part.configs).length)` → `.row-badge` text `part.active_config || "cfg"`, title `${n} configurations · active: ${active || "base"}`; instance rows: `ref.textContent = inst.config ? `${inst.part}@${inst.config}` : inst.part`.
- `placement.js`: `sig` includes `inst.config`; a `.placement-seg`-style row
  with a `<select>` (base + `state.project.parts.find(p => p.id === inst.part).configs`)
  BEFORE the `if (mated)` return; on change → `api.setInstanceConfig(proj, inst.id, value || null)`.
- `api.js`: `listConfigs(proj, id?)`, `setActiveConfig(proj, id, config, keepOverrides)` (PUT), `clearActiveConfig(proj, id)` (DELETE), `buildConfigs(proj, body)` (POST), `setInstanceConfig(proj, iid, config)` (PATCH, sends `{config}` even when null), `getMeshByKey(proj, key, lod)` (like `getMesh`, URL `/api/projects/${enc(proj)}/meshes/${enc(key)}`).
- `main.js`: `const instanceMeshes = new Map(); // mesh_key -> {buffer, key, lod}`;
  `loadAssembly` fetches `[...new Set(asm.instances.map(i => i.mesh_key).filter(Boolean))]` that are not already in the map; `renderAssemblyFromCache` looks up `instanceMeshes.get(inst.mesh_key)`; `reloadMesh(partId)` in assembly mode → `scheduleAssemblyRefresh()`; clear the map where `meshBuffers` is cleared; `handleEvent`: `if (ev.config) { configs.onRebuildEvent(ev); return; }` at the top of the three `rebuild_*` cases (after the project check); `runExport`: `const cfg = state.part && state.part.active_config; result = cfg ? await api.callTool("export_part", {project, part_id, format, config: cfg}) : await api.exportPart(...)`, toast names `result.path`.
- `configs.js`: `export function init(actions)`, `export function open(partId)`, `export function onRebuildEvent(ev)`; a `.modal.wide` with a `.prop-table` (rows = configs, columns = mass_g, volume_mm3, bbox X/Y/Z, spec chips when `spec_results`), a per-row `building…` state driven by `onRebuildEvent`, module-local `lastMatrix`.
- `drawings.js`: when the selected part has configs, a `dim table` checkbox; preview/save pass `config: part.active_config || undefined, dim_table: checked` and read `…/drawing.svg?config=` accordingly.

### Tasks

- [ ] **Task 1 — api.js + main.js mesh-by-key** (assembly renders bound
  instances with distinct geometry; part mode untouched). Manual check via
  the run skill.
- [ ] **Task 2 — config bar + provenance marks + chip + CSS.**
- [ ] **Task 3 — tree badge + instance row + placement picker.**
- [ ] **Task 4 — event guard + matrix modal + export/drawing config.**
- [ ] **Task 5 — the AC9 session** with the `run` skill: `make run` (port
  8630); create a project, a flange part with `FLANGE_SCRIPT`, declare
  s/m/l through `PUT …/configs`; in the browser: switch base→m→l and watch
  the viewport + metrics update; edit a param on M and see the chip; Reset
  to M clears it; place two instances bound to s and l and see two sizes;
  open the matrix; preview the drawing with the dim table. Record in the
  changelog: `ERROR COUNT: 0` and `FAILED REQUESTS: 0` (from the console
  and network panels; headless Chrome needs
  `--use-gl=angle --use-angle=swiftshader`), plus the 3-config assembly
  frame-time note the spec's risk item asks for.

### Verification

`uv run pytest tests/test_server.py -q` green (theme/asset tests), the
session block written, `make test`; count in `0195-…`.

---

## Slice 8 — docs, gotchas, acceptance

**Changelog:** `docs/changelog/0196-prd-012-docs-and-acceptance.md`

### Files

- Modify: `docs/agent-api.md` (tool count line 3: 73/76 → 78/81; a
  `### Configurations` table with the five tools, bold required args, prose
  Returns; amend `export_part`, `render_view`, `generate_drawing`,
  `set_assembly`, `get_part`, `get_project`, `get_assembly` rows; a short
  narrative subsection: same frozen object as a package preset, same
  validator; declared = strict, override = clamp; `build_configs` red rows
  are a 200 payload; `set_active_config` clears overrides unless
  `keep_overrides`)
- Modify: `docs/user-guide.md` (Inspector → Parameters: the switcher, the
  chip, provenance marks; Sidebar → Parts badge; Placement picker; a
  `## Configurations` section; Drawings: dimension table)
- Modify: `docs/architecture.md` (a `## Configurations` section after
  Packages: the resolution layer, `_build_with`/`_config_status`, the
  content-addressed mesh route; the key-space table already updated in
  Slice 5)
- Modify: `docs/packages.md` (cross-link at `presets.json — configurations`)
- Modify: `docs/geometry-ci.md` (per-config build rows `part@config`)
- Modify: `AGENTS.md` (`## Configuration gotchas (PRD-012 — read before touching configs, the build path or the merge)` in the house style, plus the tool count / cache-key sentence at 1447)
- Modify: `CLAUDE.md` (a condensed traps line: config names lowercase ·
  `_rebuild`/`get_part` signatures pinned → `_build_with` · nothing new in
  `_cache_key` · matrix serial+dedupe · `_status` 2-tuple, `_config_status`
  separate · `set_active_config` clears overrides · meshes by `mesh_key`)
- Modify: `docs/roadmap.md` (index row 012 → in progress link; the
  completion commit later flips it)
- Create: `tests/test_prd012_acceptance.py`

### Tasks

- [ ] **Task 1 — `tests/test_prd012_acceptance.py`**: module docstring with
  the `| AC | Test |` table; `test_ac1_…` (matrix, three distinct masses,
  one mass cross-checked against `set_active_config` + `get_metrics`),
  `test_ac2_…` (dim table SVG + `detected` + the changelog `ERROR COUNT: 0`
  evidence check for the browser half), `test_ac3_…` (conflict then
  success), `test_ac4_…` (`flange.step` unchanged, `flange_l.step`),
  `test_ac5_…` (kernel-call counter: identical maps share a key and issue
  no `build`; toggling `active_config` twice issues no `build`),
  `test_ac6_…` (two bound instances, two masses, interference on the L
  pair only), `test_ac7_…` (divergence + null-removes round trip),
  `test_ac8_a_project_without_configs_is_byte_identical` (`measure_part`
  on a copy of `examples/rocketry` matches `GOLDENS[("rocketry","flange")]`;
  raw manifest has no `configs`/`active_config`),
  `test_ac8_the_full_suite_count_is_cited` (newest changelog contains
  `make test` and `passed`), `test_ac9_the_ui_surfaces_exist_and_the_session_was_clean`
  (structural grep over `inspector.js`/`tree.js`/`placement.js`/`api.js`/
  `configs.js`/`index.html` for `renderConfigBar(`, `markConfigSources(`,
  `config-bar`, `setActiveConfig`, `cfg-chip`, `getMeshByKey`,
  `setInstanceConfig`; routes present in `routes_configs.py`; changelog
  0195 contains `ERROR COUNT: 0`), plus the house meta-test that the
  roadmap row for `[012]` links to the folder the PRD lives in.
- [ ] **Task 2 — docs** as listed; grep every new tool name appears in
  `docs/agent-api.md` (one assertion in the acceptance module, the PRD-011
  precedent).
- [ ] **Task 3 — AGENTS.md / CLAUDE.md gotchas.**

### Verification

`make test` green (cite the count in `0196-…`), `uv run pytest tests/test_prd012_acceptance.py -q -m ""` green.

## Rollback / landing notes

Each slice is one commit on `prd-012-configurations`; the branch lands as
one PR after two independent reviews (Opus subagent; Codex GPT-5.6 xhigh) and
an adversarial verification pass, then the roadmap/PRD move commit on main
(the PRD-011 close-out shape, changelog `0197-prd-012-completed.md`). Nothing
here migrates data: a checkout of an older commit reads a manifest with
`configs` and ignores it (in-place entry edits keep the key; a whole-list
`set_assembly` on an old build drops instance bindings — the one lossy path,
noted in the changelog).
