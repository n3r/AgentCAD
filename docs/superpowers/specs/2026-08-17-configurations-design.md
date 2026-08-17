# Configurations — design

- **PRD:** [`docs/prd/in-progress/PRD-012-configurations.md`](../../prd/in-progress/PRD-012-configurations.md)
- **Date:** 2026-08-17
- **Phase:** v5 — daily-driver depth
- **Depends on (all completed):** PRD-011 (the frozen configuration schema and
  its one validator, `packages.format.validate_configuration`), PRD-001 (the
  key-wise manifest merge driver this extends), PRD-003 (spec results per
  config ride the shape tier), PRD-004 (per-config CI build rows), PRD-002
  (packet renders and the assembly delta learn about instance bindings).
- **Plan:** [`docs/superpowers/plans/2026-08-17-configurations.md`](../../plans/2026-08-17-configurations.md)

---

## Problem

A part today has exactly one working state: `PARAMS` defaults plus one live
override map (`parts.<id>.params`, written by `set_params`). Exploring a size
means mutating that shared state, and nothing downstream — metrics, exports,
drawings, assembly instances, cache entries — has a per-variant identity.
PRD-012 adds **named, validated parameter sets** (configurations) to the part
record, resolves them into every geometry request without the kernel ever
learning the word, gives every derived artifact a per-config identity, lets an
assembly instance bind to one, and protects a bound configuration from being
removed. A part without configurations must behave byte-identically to today.

This spec records what the eight-seam codebase reading found, and the eleven
decisions it forced. Several of them contradict a sentence in the PRD; each
contradiction is listed in **PRD divergences to fold back** and has already
been folded into the PRD text.

## Architecture at a glance

```
project.json                                    (authored, branch-scoped)
  parts.<id>.params            explicit overrides         — unchanged
  parts.<id>.configs.<name>    {params, label?, description?}   NEW (frozen schema)
  parts.<id>.active_config     name | absent                    NEW
  assembly.instances.<id>.config   name | absent                NEW

PartRecord (model.py, pure)
  .params              explicit overrides (as stored)
  .configs             the map (or None)
  .active_config       name (or None)
  .config_params(name) -> pure config override map        (defaults < config)
  .effective_params    -> {**config_params(active), **params}  (defaults < config < overrides)
                          "defaults <" costs nothing: worker._resolve_params fills them.

service.py (one build path, two entry points)
  _record_for(proj, part_id, config)  -> the stored record (config=None) or a DERIVED record
                                         whose params are the pure config map
  _cache_key_for(proj, record)        -> hashes record.effective_params      (config-aware by construction)
  _rebuild(proj, part_id)             -> UNCHANGED signature (packs wrap it) — working state, _status
  _build_with(proj, record, *, affinity, status_key, config)  -> the extracted body both paths share
  _ensure_config_built(proj, part_id, config)  -> memo _config_status[(lock_key, part, config)], never _status
  mesh_info / ensure_mesh(..., config=)         -> {path, key, lod} for a pure config build
  export_part(..., config=)                    -> exports/<part>_<config>.<fmt>

tools_configs.py (pack)     set_part_configs · list_configs · build_configs · set_active_config · set_instance_config
routes_configs.py (pack)    the browser routes + GET /projects/{p}/meshes/{key} (content-addressed)
manifest_merge.py           parts.<id>.configs.<name>.params.<param> merges per parameter; config_problems()
handlers/drawing.py         dim_table: one measured row per configuration
frontend                    config bar (switcher + divergence chip) · param provenance marks · tree badge ·
                            placement picker · assembly meshes fetched by mesh_key · matrix modal
```

The kernel never sees a configuration. Every request it receives carries a
resolved override map exactly as today; the resolution happens in the
service on the way *into* a request, never on the way *out of* a record read
(resolving inside `ProjectStore.get_part` would make the next `set_params`
bake the config into the overrides — a data-corruption bug, not a shortcut).

## Decision 1 — configurations are manifest-resident fields on `PartRecord`

`PartRecord` gains `configs: dict[str, dict] | None = None` and
`active_config: str | None = None`; `InstanceSpec` gains
`config: str | None = None`. `to_manifest()` writes each **only when set**
(the `solid_materials` / `color` precedent), so a project without
configurations serializes byte-identically (G5, AC8). `ProjectStore.get_part`
and `ProjectStore.instances()` read them back — the instance half is
load-bearing, not cosmetic: `set_instances` rewrites the whole list from
`to_manifest()`, and `tools_mates._set_instance_mate` and
`routes_assembly2.patch_instance` both read-all/write-all, so a field the
dataclass does not carry is destroyed by the next mate edit or gizmo drag.

No schema bump, no migration: `SCHEMA_VERSION` is written and `setdefault`-ed
but never read to branch behaviour, PRD-011 added two top-level maps without
touching it, and three bundled examples still say `1`. The guarantee is a
test ("a manifest written before configurations existed loads and
round-trips unchanged"), not a number.

The map's **insertion order is the family order** the switcher and the
dimension table display. `set_part_configs` preserves the caller's order and
never sorts (the packages lockfile sorts for byte stability; a family is not
a lockfile).

Why manifest-resident rather than script-resident: clean tool CRUD, key-wise
merge under PRD-001, and one choke point for the removal conflict — at the
cost that a script alone no longer carries its family (PRD-011 packaging
exports configs as presets, which is the mitigation the PRD names).

## Decision 2 — the frozen schema, one validator, one grammar, normalized on write

A configuration is `{"params": {...}, "label"?: str, "description"?: str}`
— the object PRD-011 froze and nine catalog packages already publish. It is
validated by **`packages.format.validate_configuration(entry, params_spec)`**
and nothing else; the map-level loop (name grammar, per-entry problems
re-prefixed `configs.<name>.<field>`) lands as a new pure function
**`validate_configurations(configs, params_spec) -> list[problem]`** beside
`validate_presets`, so the tool pack and the presets validator cannot drift.

Four consequences the reading surfaced, each decided:

1. **Names are lowercase.** `CONFIG_RE = ^[a-z0-9][a-z0-9_-]{0,31}$` — the
   grammar nine published packages already obey and the index digest names
   `<part>.<config>` under. The PRD's own narrative wrote `"L"` and
   `flange_L.step`; that is folded back to `"l"` → `flange_l.step`, with
   `label` carrying the display name. Widening the grammar would break the
   one-spelling rule the format enforces on purpose.
2. **A declared configuration is range- and enum-strict.** `validate_configuration`
   refuses an out-of-range value; `set_params` stores it raw and the worker
   clamps with a warning. The publish gate already chose *refuse* for presets
   ("an out-of-range preset would otherwise publish quietly"), and a family
   is a published thing. So: refuse at `set_part_configs`; an explicit
   `set_params` on top keeps today's clamp semantics. Documented in the tool
   description and the user guide, because a user who reads one rule will hit
   the other.
3. **Values are normalized on write** through the same rules `set_params`
   uses (int/float coercion, enum canonicalization to the declared choice),
   exposed as a small public seam `service.normalize_params(spec, values)`
   wrapping the private `_normalize_param`. Without it `{"n": 3}` and
   `{"n": 3.0}` are two configurations and two cache keys for one geometry,
   and AC5 fails on a spelling difference.
4. **`params` is a full override set, not a delta.** `None` values are
   refused (`_is_json_scalar` excludes them) — the `set_params` meaning of
   `None` (remove) does not exist here. An empty `params` map is legal (a
   configuration that overrides nothing); it is not warned about.

The spec the validator needs is `service._params_spec(script)` — memoized on
`sha256(script)`, one kernel `inspect`, negative-cached as `None` for a
script that does not load. A `None` spec is refused with `set_params`' exact
message ("the part script does not currently load — fix the script first"),
never shape-checked-and-persisted. Reference parts (no PARAMS) are refused
with the gate's wording. Validation covers the **whole map before one byte is
written**, and a refusal lists every problem at once (`details.problems`).

## Decision 3 — resolution is a pure function of the record; the kernel fills defaults

Two pure members on `PartRecord`:

- `config_params(name) -> dict` — a copy of `configs[name]["params"]`. An
  unknown name raises `KeyError`; every tool boundary validates membership
  before reaching here. **Pure-config resolution** (defaults < config) is
  what matrix builds, per-config exports/renders/drawings and instance
  bindings use, so a variant's identity never depends on session state.
- `effective_params -> dict` — `{**config_params(active_config), **params}`
  when `active_config` names a declared configuration, else `params`. This
  is FR3's working state (defaults < active config < explicit overrides). An
  `active_config` that names a configuration the map no longer has (a merge
  or a hand edit) resolves as base — silently here, and loudly in the merge
  report (Decision 9).

"PARAMS defaults <" needs no code: `worker._resolve_params` already fills
every unset name from `PARAMS[name]["default"]`, so the service never learns
a default and never calls `inspect` to resolve. Writing it this way is what
keeps every existing cache key byte-identical.

Every geometry consumer of `record.params` switches to
`record.effective_params` — a mechanical rename at the ~20 sites the reading
enumerated: `service.py` (`export_part`, `_shape_item`, `_cache_key_for`,
`_rebuild` build params), `mates.py:64`, `tools_analysis.py` (four kernel
items), `specs.py:839/960`, `tools_drawing.py:25`, `tools_facemod.py:60/71`,
`tools_sketch.py:487`, `tools_sheetmetal.py:21`, `tools_holes.py:305/709`,
`packet.py:916`. `record.params` keeps meaning *explicit overrides* wherever
the manifest is read or written (`get_part.params`, the merge, `set_params`'
merge-into-overrides).

`_cache_key_for(proj, record)` therefore hashes `record.effective_params`
and **nothing new enters `_cache_key`'s payload**: two configurations with the
same override map share one entry (AC5), distinct maps get distinct entries,
and flipping `active_config` twice is a memo miss followed by an on-disk
`<key>.metrics.json` hit — zero new code, and `tests/test_solids.py`'s
pinned payload bytes stay green. AC5's "identical resolved params" is
defined as **identical override maps**: the service hashes overrides while
the worker hashes fully-resolved values, and making them agree would need an
`inspect` inside every key computation and would move every existing key.

**The derived record.** `service._record_for(proj, part_id, config)` returns
the stored record when `config is None`, otherwise
`dataclasses.replace(record, params=record.config_params(config), active_config=None)`.
Every record-driven function — `_cache_key_for`, `_content_signature`,
`_solid_densities`, `_shape_item` — then works unchanged on a pure-config
build, which is how FR4 and FR10 fall out of one helper instead of a
`config=` argument threaded through twenty functions.

## Decision 4 — one build path, two entry points; matrix state never enters `_status`

`service._rebuild(proj, part_id)` is a **monkey-patch seam**: `tools_specs`
and `tools_holes` rebind it with wrappers declaring exactly
`(proj: str, part_id: str)`, and three packs rebind `get_part` the same way.
It cannot grow a `config=` kwarg (a `TypeError` through the chain, and pack
load order — alphabetical, `configs` before `holes` before `specs` — would
make a config pack the innermost wrapper). So:

- The body of `_rebuild` moves into
  `_build_with(proj, record, *, affinity, status_key, config=None) -> dict`:
  same cache-key computation, same `<key>.metrics.json` read/write, same
  `rebuild_started` / `rebuild_finished` / `rebuild_failed` events, same
  return shape (`{ok, metrics, warnings, lods, cache_key}` or `{ok: False,
  error}`). `record.params` becomes `record.effective_params`; the four event
  dicts gain `**({"config": config} if config else {})` so a base rebuild's
  payload stays byte-identical (the frontend keys on `ev.part`).
- `_rebuild(proj, part_id)` keeps its signature and calls `_build_with` with
  the stored record, `affinity=part_id` and the existing 2-tuple
  `_status_key`. Working-state behaviour is unchanged.
- **`_ensure_config_built(proj, part_id, config) -> dict`** is the pure-config
  entry point: it memoizes in a **separate** `_config_status[(lock_key, part_id,
  config)]` (checked exactly like `_ensure_built`: recorded key equals the
  freshly computed pure key *and* the `.acm` exists), else calls
  `_build_with(proj, self._record_for(proj, part_id, config), affinity=part_id,
  status_key=None, config=config)` and memoizes the result. It never touches
  `_status`, so `get_project.parts[].state`, `get_part.status` and the tree
  badge keep meaning *the working state*, and the three tests plus one pack
  that index `_status` as a literal 2-tuple stay untouched. `_forget_status`
  and `delete_part` sweep both dicts by prefix.

The consequence, accepted explicitly: the specs and holes wrappers around
`_rebuild` do **not** decorate pure-config builds. Per-config spec results
are produced on purpose instead (Decision 8), and per-config hole metadata
is not a PRD-012 deliverable.

Why the memo matters (a livelock, not a perf note): with one slot per part,
two instances of one part bound to different configurations would miss the
memo on alternate `get_assembly` calls, re-enter `_rebuild`, publish
`rebuild_finished` even on a disk hit, and the browser's `rebuild_finished`
handler schedules an assembly refresh — a self-sustaining 400 ms loop with
full mesh re-downloads. `_config_status` is what makes a bound assembly quiet
after its first build.

`affinity` stays `part_id` for every build. The pool routes by
`hash(affinity) % size` (salted per process), and PRD-011 measured the
identical many-variants-of-one-part fan-out at 1.08×/1.40×/1.17× against a
pre-registered 1.5× bar and deleted it; the fan-out also let two configs that
share a cache key race the worker's fixed-name `.tmp` staging. **`build_configs`
is serial and de-duplicates by cache key** (compute every requested config's
pure key first, build each distinct key once, fan the row back out across
the names that share it). A fan-out may return only behind a fresh
pre-registered bar and a determinism test.

## Decision 5 — `set_active_config` loads a variant: it clears explicit overrides unless told not to

`active_config` is the working state's named layer. Switching to a
configuration is *loading that variant*, so
`set_active_config {project, part_id, config?, keep_overrides?}`:

- validates membership (`config` must be a declared name; `null`/omitted
  returns to base);
- **clears the explicit override map by default** — the response reports
  `cleared_overrides: {...}` and the change is one `project_changed` publish,
  hence one history snapshot and one undo step;
- with `keep_overrides: true` leaves `parts.<id>.params` alone, so the part
  is immediately "M — modified" (FR3's resolution order, made explicit);
- returns the rebuild result (`with_hint`) merged with
  `{part_id, active_config, diverged, diverged_params, cleared_overrides}` —
  a switch changes the visible geometry, so the post-state a caller needs
  next is the build.

Why clear by default: after `set_active_config m` an agent (and the
inspector) must see pure M — no hidden state, no "why is width 12". The
alternative (keep, and rely on the chip) makes S→M read as a bug on the
first click. Both are reachable; the default is the predictable one.

**Divergence** is semantic, not syntactic:
`diverged = active_config is set and effective_params != config_params(active)`;
`diverged_params` are the names whose effective value differs from the pure
configuration (including overrides of parameters the configuration does not
set). An override equal to the configuration's value is not divergence — the
geometry, and the cache key, are the pure configuration's. `get_part.status`
carries `diverged` and `diverged_params`; `get_part` and `get_project` part
rows carry `configs` (always present; `{}` when none) and `active_config`
(always present; `null` when base). `set_params` remains the way to add or
remove an override on top (`null` removes), so AC7's round trip is the
existing pinned behaviour.

## Decision 6 — the tool pack and the browser routes

`agentcad/core/tools_configs.py` — five tools, each validating before
writing, publishing `project_changed` exactly once after its write, and
returning post-state:

| Tool | Behaviour |
|---|---|
| `set_part_configs {project, part_id, configs}` | Full replace (the `set_project_materials` pattern). Refuse non-object, reference part, non-loading script; `validate_configurations` over the whole map; normalize values; **FR11**: a name that is removed (or absent from the new map) while an assembly instance is bound to it or it is the part's `active_config` → `ConflictError` naming every referrer (`details.instances: [ids]`, `details.active_config: bool`, `details.config`, `details.part`), the `remove_part` payload shape. Then write under `manifest_scope` + `locks.write_scope(part_id)` (an emptied map pops the key), publish once with `reason: "configs"`, rebuild only if the active configuration's params changed, return `{part_id, configs, active_config, rebuild?}`. Order preserved, never sorted. |
| `list_configs {project, part_id?}` | `{parts: [{part_id, configs, active_config, diverged, diverged_params, referrers: {name: [instance ids]}}]}` — `referrers` makes FR11 a lookup before it is a surprise. |
| `build_configs {project, part_id?, configs?}` | Serial, de-duplicated by cache key. Single part → `{part_id, configs: [row]}`; project-wide → `{parts: [{part_id, configs: [row]}]}`; a project with no configured parts → `{parts: [], warnings: ["no configured parts"]}` (never an empty list with no reason). Row = `{name, label, ok, cached, cache_key, metrics, warnings, error?, spec_results?}`; a failing configuration is a row with `ok: false` and `error`, never an exception; rows in family order. Unknown name in `configs` → `validation_error`. Kernel timeout per build as `_rebuild` (300 s). |
| `set_active_config {project, part_id, config?, keep_overrides?}` | Decision 5. |
| `set_instance_config {project, instance, config?}` | The narrow binding tool (`set_mate`/`clear_mate` are the precedent — `set_assembly` is a full-list replace and would silently unbind everything a caller forgot). `null` unbinds. Validated by the store (Decision 7); returns `get_assembly`. |

Changed core tools (unavoidable, small, listed): `export_part {config?}` (core
`tools.py` schema + `service.export_part(config=)` + the `app.py` export
route whitelist + `agentcad export --config`), `render_view {config?}`
(`tools_vision.py`), `generate_drawing {config?, dim_table?}`
(`tools_drawing.py`), `set_assembly` instances accept `config`,
`get_part`/`get_project`/`get_assembly` expose the state.

`agentcad/server/routes_configs.py` — the `routes_specs.py` template
(`_RAISE`, `_BODY_ERRORS = set()`, `_result`, `_body_keys`, `_json`):

```
GET    /projects/{p}/configs                                → list_configs
GET    /projects/{p}/parts/{id}/configs                     → list_configs {part_id}
PUT    /projects/{p}/parts/{id}/configs        {configs}    → set_part_configs
PUT    /projects/{p}/parts/{id}/active-config  {config, keep_overrides?} → set_active_config
DELETE /projects/{p}/parts/{id}/active-config               → set_active_config {config: null}
POST   /projects/{p}/configs/build             {part_id?, configs?}      → build_configs
PATCH  /projects/{p}/assembly/instances/{id}/config {config|null}         → set_instance_config
GET    /projects/{p}/meshes/{key}?lod=                      → the content-addressed mesh (Decision 7)
```

The `DELETE` exists because `_body_keys` strips `null` (a documented trap);
the `PATCH` forwards `config` on `"config" in body`, not truthiness. Every
error is a refusal (`_BODY_ERRORS` empty); a red matrix is a 200 payload.

Errors: `validation_error` (bad map, unknown config, reference part,
non-loading script), `conflict_error` (FR11), `notfound_error`. There is no
service-side `contract_error` — that is the kernel's own PARAMS verdict and
reaches a caller through a build result. Events: `project_changed {reason:
"configs" | "active_config" | "instance_config"}`; `rebuild_*` gain `config`
only when the build is a pure-config build.

## Decision 7 — assembly binding: the store validates, the record resolves, the mesh is content-addressed

**Validation lives in `ProjectStore.set_instances`**, beside the existing
unknown-part and dangling-mate refusals — three writers reach the store
(`service.set_assembly`, `tools_mates._set_instance_mate`,
`routes_assembly2.patch_instance`) and only the store sees all three. An
instance `config` must name a declared configuration of its part
(`ValidationError` naming the declared names) and the part must be a script
part (a reference part has no PARAMS; refused with the reason).
`config: null`/absent means **the part's live working state** (defaults <
active config < overrides) — the only reading that keeps AC8's byte-identical
behaviour; a bound instance uses **pure** resolution, so a part viewed with an
override on top of M legitimately differs from its own instance bound to M
(intended; the chip is a part-level concept and the doc says so).

**Resolution.** Every per-instance geometry site obtains
`service._record_for(proj, inst.part, inst.config)` and passes the derived
record to the existing record-driven function:
`_shape_item` (hence `check_interference` and `export_assembly`),
`mates.resolve` (`item["params"] = record.effective_params` — the kernel's
`conn_cache` is already per instance id, so nothing in the resolver changes),
`tools_motion.sweep_motion`, `specs._instance_item` and `specs._assembly_key`
(so an assembly-tier verdict measured at S is not reused at L — "a spec cache
key covers every input the check reads"), `tools_vision.render_view`'s
assembly path and `packet`'s assembly render (`ensure_mesh(config=inst.config)`).
`get_assembly` uses `_ensure_config_built` for bound instances and publishes
each built instance's cache key as **`mesh_key`** (always, for every built
instance). `packet.assembly_delta` treats `config` like `mate` (a rebinding
with unchanged mass is a change). `tolerance_stackup` emits a warning row for
a config-bound path instance ("tolerances are per part; the nominal is per
configuration"), because per-config PMI is a stated non-goal and a silently
mixed answer is worse than a named one.

**Mesh addressing.** The one-mesh-per-part assumption lives in exactly two
places: the server's `_status` (fixed by Decision 4) and the browser's
`meshBuffers: Map<partId, …>`. It is removed rather than patched: the browser
fetches assembly geometry by **`mesh_key`** through
`GET /api/projects/{p}/meshes/{key}?lod=` — a pack route that serves
`.cache/<key>.acm` (or `<key>.<lod>.acm`, with the same-shape tier fallback
the part route has) after a `^[0-9a-f]{32}$` gate, 404s when not built (it
never builds — the browser cannot storm the kernel through it), and echoes
`X-Mesh-Key`/`X-Mesh-Lod`. Part mode needs no mesh work at all: the
`active_config` resolves in the manifest path, so the existing part-keyed
route already serves the active configuration's geometry, `geomKey` is
config-distinct for free, and the `.faces.u32` sidecar (per cache key) stays
correct. The viewport's geometry cache is keyed by content hash and already
distinguishes sibling configurations; only its comments change. `app.py`'s
mesh routes stay byte-identical. There is no `?config=` query parameter —
one identity for a mesh, not two.

## Decision 8 — drawings, renders, specs and CI: per-config identity in every artifact

- **`export_part {config?}`** → `exports/<part>_<config>.<fmt>` (base naming
  unchanged); pure resolution; the config is echoed in the result. Names
  are dot-free and slash-free by grammar, so no extra sanitizing.
- **`render_view {config?}`** → `renders/<part>_<config>_<view>.png` via
  `ensure_mesh(config=)`; assembly renders take each instance's binding from
  the resolved instance (one render can mix configurations).
- **`generate_drawing {config?, dim_table?}`** → `<part>_<config>_drawing.<fmt>`
  when a config is named (mirrored in `routes_drawing.py` and
  `drawings.js`); `dim_table: true` on a configured part asks the worker to
  draw a boxed table — one row per configuration, columns = the configured
  parameters (union, first-seen order) plus overall X/Y/Z extents — with
  every value **measured from that configuration's built shape in the
  handler** (the module's contract: values come from geometry, not from
  parameters), every cell `_esc`'d (a `label` may contain `&`), anchored at
  the sheet's one clear rectangle (264,18)–(414,60) above the iso view,
  truncated with a warning beyond eight rows, echoed structurally as
  `detected.dim_table = {columns, rows: [{config, label, values, ok, error?}]}`,
  ignored by DXF like PMI is, and with the request timeout scaled
  `120 + 60·rows`. Deeper table formats and PMI-toleranced dims per
  configuration are PRD-014's.
- **Per-config spec results.** `SpecRunner._shape_tier` gains a keyword-only
  `record=` (a derived record): the sidecar key becomes that record's cache
  key and the `spec_eval` params its `effective_params`, so a config-keyed
  sidecar can never carry the base measurement. `build_configs` fills
  `spec_results` (shape tier only — the assembly tier is not per
  configuration) when the part declares SPECS and the specs pack is loaded.
- **CI (FR6).** `checks._stage_build` emits one extra `build` row per
  configuration of a configured part, subject `part@config` (the packages
  gate's own subject grammar), `details: {config, cache_key, cached}`,
  budget-checked before each — no new stage, no new item kind, so
  `STAGES`/`ITEM_KINDS` and their pinned tests stay untouched.

## Decision 9 — the merge reaches `configs.<name>.params.<param>`, and the hybrid is reported

Today `configs` would merge as one atomic value: two branches adding
*different* configurations conflict, and two branches editing different
parameters of one configuration conflict — FR12 violated in both directions
(measured on the real driver). Fixed with one descriptor and one merger, not
a fork of `_merge_entry`:

- `_PART_ENTRY_DICTS = {"configs": ("params",)}` beside `_PART_SUBDICTS`;
  in `_merge_entry`, `configs` (when keyed on all three sides) goes to a new
  `_merge_keyed_entries(prefix, base, ours, theirs, conflicts, subdicts=("params",))`
  which, **per name**, descends only when `_keyed(b, o, t)` (the map-level
  analogue of `_entry_list`'s guard — without it a hand-edited `"m": 5`
  merges cleanly to `{}`), else `_merge_atomic`. Inside a configuration
  present on all three sides, `params` merges per parameter and
  `label`/`description` merge as whole values; add/add of the same name and
  delete/modify conflict on the whole configuration.
- `_write_entry` learns the same shape (`_write_keyed_entry`) so
  `apply_choices` on a six-segment path writes into the map — today it
  writes the literal flat key `"configs.m.params.w"` beside the real map
  (measured).
- `parts.<id>.active_config` and `assembly.instances.<id>.config` are whole
  values and need no driver work.
- **`config_problems(manifest)`** beside `package_problems`, called by
  `merge.py`'s validation: an instance bound to a configuration the merged
  part no longer declares → `integrity` (blocking — the instance would
  resolve to nothing); a part whose `active_config` is gone → `warnings`
  (non-blocking; it resolves as base per Decision 3). A key-wise merge
  produces this hybrid clean, and the tool choke point cannot see it — the
  same reason `package_problems` exists.
- The docstring's key-space table gains the five rows, with the sentence
  that says *why* a configuration is not atomic while a lock entry is: a
  lock entry is content-determined and half of one verifies against nothing;
  a configuration is a set of independent parameter values, the argument
  that makes `params` per-key.

Docs that assert the key space (`docs/architecture.md`, `docs/packages.md`,
the PRD-001 design spec's table) are updated in the same change.

## Decision 10 — the browser: a config bar, provenance marks, and nothing that fights an open dropdown

- **Config bar** — a static host `<div id="config-bar" class="hidden">`
  between `#banner` and `#pane-params` (outside `paramsPane`, which every
  full rebuild wipes), rendered by `renderConfigBar(part)` at the tail of
  `inspector.render()`: a `<select>` (**base** + declared configurations,
  labels shown, names as values) rebuilt only when
  `[part.id, active_config, Object.keys(configs)]` changes (the
  `materialSig` idiom — a rebuild must not close an open dropdown), the
  divergence chip `<span class="cfg-chip diverged" title="…">M — modified</span>`
  with a **Reset to M** action (`set_active_config m`, which clears the
  overrides), and a **Matrix** button. Hidden entirely when the part has no
  configurations — G5 made visible.
- **Provenance marks** — `markConfigSources(part)` toggles
  `.param-from-config` / `.param-overridden` on each `.param` wrap, called
  immediately before `decorateParams()` (the one hook both the full rebuild
  and `syncParamValues` return to; **not** through `setParamDecorator`, whose
  single slot comments.js owns). Styled with existing accent tokens only
  (a left border, not a background — the thread badge stays readable; no
  new token, so light mode keeps working).
- **Tree** — a `.row-badge` on configured parts (active name, or a neutral
  glyph when base; title `N configurations · active: M`) from
  `get_project`'s new fields; instance rows print `part@config`.
- **Placement** — a configuration `<select>` **before** the mated
  early-return, `config` in the rebuild signature, written through
  `PATCH …/assembly/instances/{id}/config`.
- **Assembly meshes** — `loadAssembly` fetches by `inst.mesh_key` through
  `api.getMeshByKey`, keeps `instanceMeshes: Map<mesh_key, entry>`, and
  skips keys it already holds (the unconditional per-refresh refetch goes
  away as a side effect); `renderAssemblyFromCache` looks up by key; part
  mode's `meshBuffers` is untouched.
- **Events** — a config-tagged `rebuild_*` event is handed to the matrix
  panel and otherwise ignored (`state.rebuilding` is a Set of bare part ids;
  the first finished configuration would otherwise clear the part's dot).
- **Matrix modal** — `frontend/js/configs.js`, `.modal.wide` + `.prop-table`
  (the inspector is 326 px and `flex: none`), rows = configurations,
  columns = metrics (+ spec chips when present), rendered from the
  `build_configs` response held module-locally, per-row failures in place.
  Opened from the config bar for the selected part.
- **Export / drawing** — when a configuration is active, `runExport` passes
  `config` (through `api.callTool("export_part", …)` — the export route is
  core), the toast names the suffixed file; the drawings panel gains a
  *dimension table* checkbox for configured parts.

There is no browser harness in this repo; AC9 is a real session driven by
the `run` skill (headless Chrome needs `--use-gl=angle --use-angle=swiftshader`),
graded — as PRD-003/004/011 graded theirs — by a structural test over the
named surfaces plus a changelog session block containing `ERROR COUNT: 0`.

## Decision 11 — what is deliberately not in v1

Per-config PMI, materials and solid assignments (shared across the family);
assembly-level configurations (after PRD-013); per-instance overrides beyond
`config` (the derived-record seam makes it trivial later — `config` is the
only per-instance parameter channel for now, said out loud so the cache key,
spec sidecar and mesh addressing do not silently need a second dimension);
face picking / comment anchors on config-bound instances (assembly-mode
picking cannot resolve a face today); a `use_part` option that seeds
`configs` from a package's presets (the copy would live outside the
provenance header's coverage — decide with PRD-031); rejecting unknown
instance keys in `set_assembly` (out of scope; `config` is documented);
pruning `configs: {}` inside the pure merge (the tool path pops it).

## Surfaces

### Tools (5 new, 4 changed)

New: `set_part_configs` · `list_configs` · `build_configs` ·
`set_active_config` · `set_instance_config`. Changed: `export_part {config?}`
· `render_view {config?}` · `generate_drawing {config?, dim_table?}` ·
`set_assembly` (instances accept `config`). Exposing state: `get_part`
(`configs`, `active_config`, `status.diverged`, `status.diverged_params`),
`get_project` (per part `configs`, `active_config`), `get_assembly`
(per instance `config`, `mesh_key`).

### CLI

`agentcad export <project> <part> --format … [--config NAME]`.

### Routes

Decision 6's table, all in `routes_configs.py`.

### Events and errors

`project_changed` with `reason` ∈ {`configs`, `active_config`,
`instance_config`}; `rebuild_started`/`rebuild_finished`/`rebuild_failed`
gain `config` only for pure-config builds. `validation_error`,
`conflict_error` (with `details.instances`, `details.active_config`,
`details.config`, `details.part`), `notfound_error`.

## Data flow — the AC1 walk

1. `set_part_configs {flange, {s, m, l}}` → `_params_spec(script)` (memo) →
   `validate_configurations` (names, entries, ranges) → `normalize_params` →
   referrer diff (nothing bound) → one manifest write → `project_changed
   (configs)` → one snapshot.
2. `build_configs {flange}` → for each of s/m/l, `_record_for(flange, name)`
   → pure key via `_cache_key_for(derived)`; group by key → for each distinct
   key `_ensure_config_built` → `_build_with(derived, affinity="flange",
   status_key=None, config=name)` → kernel `build` with `params = config map`
   → `<key>.acm` + `<key>.metrics.json`; `rebuild_started/finished {config}`
   → row `{name, ok, metrics.mass_g, cache_key, cached: false}`; if the
   specs pack is loaded and the script declares SPECS, `_shape_tier(record=derived)`
   → `spec_results`. Three distinct masses, family order, one call.
3. `set_active_config {flange, "l"}` → overrides cleared, `active_config: l`
   → `project_changed (active_config)` → `_rebuild` → key equals l's matrix
   key → disk hit → `rebuild_finished {cached: true}` → the inspector shows
   L's geometry and metrics; `export_part {config: "l"}` → `flange_l.step`.

## Testing strategy

Fixtures: `FLANGE_SCRIPT` + `THREE_SIZE_CONFIGS` literals in
`tests/conftest.py` (every param with unit and description; the bundled
`examples/rocketry/parts/flange.py` is never edited — its mesh sha is a
golden). A module-scoped template project cloned per test for the heavy
family (the `test_specs.py` pattern).

Modules: `tests/test_configs.py` (model, resolution, normalization, cache
key, manifest round trip, byte-identical without configs, `_build_with` /
`_ensure_config_built`, events), `tests/test_configs_api.py` (the pack + the
router through `create_app(..., extra_allowed_hosts={"testserver"})` and
`TestClient(base_url="http://127.0.0.1")`, including `DELETE active-config`
and the meshes route), `tests/test_configs_assembly.py` (bindings, store
validation, mates, interference, mesh_key, packet delta, stackup warning),
`tests/test_manifest_merge.py` (a `# ---- PRD-012: the configs map` section:
add/add different names clean, different params of one config clean, same
param conflicts at the param key with the exact payload, add/add same name
and delete/modify conflict on the whole config, `apply_choices` writes into
the map not a flat key, non-dict entry survives, dotted-name resolution,
config-free manifest untouched, plus `KEY_CLASSES` rows and `config_problems`
cases), `tests/test_configs_drawing.py` (dim_table SVG + `detected`, `&` in a
label, DXF ignores it, base drawing byte-identical), `tests/test_configs_checks.py`
(per-config build rows, budget), and `tests/test_prd012_acceptance.py` — one
`test_acN_<claim>` per criterion with the `| AC | Test |` table, AC5 anchored
by the kernel-call counter, AC8 split into a golden check on a config-free
copy of `examples/rocketry` and the cited suite count in the close-out
changelog, AC9 graded as changelog evidence (`ERROR COUNT: 0`).

Heavy tests sit in `Test<Subject>` classes with ≥ 3 tests (loadscope's
refill watermark) and an explicit `pytest.mark.timeout`; the module-level
cheap tests form their own scheduling unit.

## Risks and open questions

- **`get_assembly` builds serially** — a `set_assembly` that binds five
  configurations pays five builds in one call. Accepted for v1 (first build
  is honest compute; the memo makes every later read free); revisit with a
  measured bar, never by re-adding the deleted fan-out.
- **`worker._atomic_write` stages through a fixed `.tmp`** — latent today
  because builds are serialized per key; the serial matrix keeps it latent.
  Recorded again as a follow-up (0184's note), not smuggled in.
- **A merge can land a state the tool would refuse** (a bound instance whose
  configuration one side deleted). It is reported by `config_problems`, and
  the repair is a tool call (`set_instance_config null`), not a read-time
  fallback that hides it.
- **The stored configuration is not re-validated when the script changes**
  (a renamed parameter or a lowered `max`). Compute-on-read is the house
  precedent; v1 reports it through `build_configs` (that row fails, in
  place) rather than a watcher.
- **`GEOM_CACHE_MAX = 32` in the viewport** counts `(part, config)` pairs
  on stage; a family-heavy assembly can exceed it. Measured in the AC9
  session with a 3-configuration assembly; the fix, if needed, is the LOD
  request in `loadAssembly`, which is independent of configurations.

## Naming traps (live collisions in this tree today)

- The object is a **configuration**; `preset` names only where one lives (a
  configuration a package publishes); the field is `configs`, the tools are
  `set_part_configs` / `set_active_config` / `set_instance_config`. Never
  `variant` (`Variant` is the gate's build-sweep namedtuple) and never
  `preset` for a manifest configuration.
- `config` already means `~/.agentcad/config.json` in `library.js` and
  `AGENTCAD_CONFIG` in tests; the library dialog's preset control keeps
  saying *preset*.
- `tools_configs` sorts at `con` — before `drawing`, `holes`, `packages`,
  `proposals`, `specs`, `vision`: at `register()` time `service.specs`,
  `service.packages`, `service.gate_providers` do not exist; read them inside
  handlers behind `getattr`, and never append to `gate_providers`
  (`tools_proposals` resets it unconditionally — the `tools_run_checks` trap).
- Config names are lowercase; `label` is the display name. `s`/`m`/`l`, not
  `S`/`M`/`L`.

## PRD divergences to fold back (all applied to the PRD text on 2026-08-17)

1. Config names in the Experience section and AC4: `"L"`/`flange_L.step` →
   `"l"`/`flange_l.step` (the frozen grammar is lowercase).
2. FR5 / Experience: `build_configs` is serial and de-duplicated by cache
   key, not "in parallel on the kernel pool" (evidence: PRD-011's deleted
   fan-out and the same-key race).
3. FR1: declared configurations are range/enum-strict; `set_params` on top
   clamps; values are normalized on write.
4. FR8: the v1 dimension table's columns are the configured parameters plus
   overall X/Y/Z; PMI-toleranced dims per configuration go to PRD-014.
5. Not text changes but decisions the PRD left open, recorded here:
   `set_active_config` clears explicit overrides unless `keep_overrides`;
   an unbound instance is the live working state; a narrow
   `set_instance_config` tool exists beside `set_assembly`; assembly meshes
   are content-addressed (`mesh_key` + `/meshes/{key}`) rather than
   `?config=`-addressed.
