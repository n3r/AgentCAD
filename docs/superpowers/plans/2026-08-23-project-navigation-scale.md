# PRD-027 Navigation at scale — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. One fresh subagent per slice (Opus for slices needing judgment, Sonnet for mechanical ones); the controller runs `make test`, writes the changelog count, and commits. Subagents never run mutating `git`, `uv sync`, or `uv pip install`.

**Goal:** folders/tags on parts (and folders on instances), a server search engine with a `field:value` query language, content-hash thumbnails, bulk operations as one undo step, a project dashboard, and a virtualized folder tree — PRD-027 MVP + Phase 2.

**Architecture:** additive manifest fields read/written through `ProjectStore` (single-RMW bulk writes = one `project_changed` publish = one undo step); `core/search.py` scans on demand with a stat-validated memo; `core/thumbnails.py` renders from existing ACM meshes with `render.render_acm` (server-side numpy), pre-warmed by a bus subscriber, served on demand — never building; three tools in `tools_navigation.py`, routes in `routes_navigation.py` + `routes_thumbnails.py`; the frontend is pure `*_model.js` modules (node-tested) + a rewritten `tree.js`, `bulk.js`, `dashboard.js`, `shell/contextmenu.js`.

**Tech stack:** Python 3.12, FastAPI, numpy (render), vanilla ES modules, node-in-pytest harnesses, Playwright (`uv run --no-project --with playwright`, `channel="chrome"`) for browser evidence.

**Spec:** `docs/superpowers/specs/2026-08-23-project-navigation-scale-design.md` — the plan argues from it; read both.

## Global constraints

- No kernel changes; `agentcad/kernel/` untouched. Thumbnails and search never call `ensure_mesh`/`_ensure_built` (kernel spies in tests).
- `_rebuild`/`get_part` signatures byte-for-byte; nothing enters `_cache_key`'s payload.
- `to_manifest` writes `folder`/`tags` **only when set** — an untouched project serializes byte-identically (test it).
- One `project_changed` publish per mutating call — N publishes are N undo steps.
- Tags: `^[a-z0-9][a-z0-9_.-]{0,31}$` after lowercasing/stripping; ≤ 32 per part. Folders: 1–8 segments of `^[A-Za-z0-9][A-Za-z0-9 _.-]{0,39}$`, no leading/trailing space per segment, stored verbatim, matched case-insensitively.
- Route gates use `fullmatch`, never `match`/`$`.
- New routes are member-only (default-deny); nothing joins `PUBLIC_PATHS`/`PUBLIC_PREFIXES`.
- Text I/O always names `encoding="utf-8"`.
- Tool count strings move **85 → 88** / **88 → 91** in the docs slice (seven files).
- Every commit: `docs/changelog/NNNN-<slug>.md` citing `make test — N passed`.

## Dependency map

```
S1 (metadata + set_part_meta)
 ├─► S2 (search engine + search_parts + fixture)  ─► S5 (frontend pure models)
 └─► S3 (thumbnails + routes_thumbnails)          ─┐
        S4 (bulk_part_op + dashboard) ◄────────────┘ (needs S1 + S3)
S6 (frontend DOM: tree/bulk/dashboard/wiring)  needs S4 + S5
S7 (acceptance + docs)                          needs S6
```
S2 ∥ S3 run concurrently (disjoint files except `tools_navigation.py`, where each Edits only inside its own anchored block). S4 ∥ S5 likewise.

---

## Slice 1 — manifest metadata, store writes, `set_part_meta` (FR1, §1, §5) — **Opus**

**Files:** modify `agentcad/core/model.py` (`PartRecord`, `InstanceSpec`), `agentcad/core/project.py` (`get_part`, `instances`, `set_instances` validation, new `update_part_meta`, `update_parts_meta`), `agentcad/core/service.py` (`get_project`, `get_part`, `set_assembly` passes `folder`), `agentcad/core/tools_structure.py:118-140` (`_set_assembly` passes `folder`), `agentcad/core/mates.py:50` (`_member` inherits `folder`), `agentcad/server/routes_assembly2.py` (accept `folder`; mate refusal only for transform keys); create `agentcad/core/navigation.py`, `agentcad/core/tools_navigation.py`; tests `tests/test_navigation_meta.py`.

**Produces (later slices rely on these exact names):**
```python
# agentcad/core/navigation.py
FOLDER_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,39}$")
TAG_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,31}$")
MAX_FOLDER_DEPTH = 8
MAX_TAGS = 32
def normalize_folder(value) -> str | None      # None/"" -> None; ValidationError("invalid folder ...", {"folder": value})
def normalize_tags(value) -> list[str]         # list[str] -> stripped, lowercased, de-duped (first seen), validated; ValidationError names the bad tag
def folder_matches(folder: str | None, query: str) -> bool   # case-insensitive segment-prefix: "a/b" matches query "a", "A/B", "a/b"; not "a/bc"
```
```python
# ProjectStore (agentcad/core/project.py)
def update_part_meta(self, proj, part_id, *, folder=_UNSET, tags=None) -> PartRecord
    # under locks.write_scope(part_id); folder=None clears; tags=[] clears; one save_manifest
def update_parts_meta(self, proj, edits: dict[str, dict]) -> list[PartRecord]
    # edits[part_id] = {"folder": str|None (key present = set), "tags": list|None, "material": str|None}
    # 1) every id must exist (NotFoundError listing the missing ids — validate before any write)
    # 2) for pid in edits: with locks.write_scope(pid): if self.write_guard: self.write_guard(proj)
    # 3) mutate entries in place (folder None -> pop key; tags [] -> pop key; material via _validate_material)
    # 4) ONE save_manifest(proj, manifest); return records in edits order
```
`PartRecord.folder: str | None = None`, `PartRecord.tags: list[str] = field(default_factory=list)`; `to_manifest` adds `"folder"` when truthy and `"tags"` when non-empty. `InstanceSpec.folder: str | None = None`, written when truthy; `instances()` reads `i.get("folder")`; `set_instances` validates each `folder` with `normalize_folder` (import locally to avoid a cycle if needed). `get_project` part dicts gain `"folder": entry.get("folder")`, `"tags": list(entry.get("tags") or [])` (no `thumb_key` yet — slice 3 adds it); `get_part` detail gains the same two.

`tools_navigation.py` (loads at `nav`; header docstring explains load order: after `materials`, before `packages`/`proposals`; registers no gate provider):
```python
def register(registry, service) -> None:
    def set_part_meta(project: str, part_id: str, folder=_UNSET, tags=None) -> dict:
        # folder omitted = unchanged; folder None = root; tags None = unchanged; tags [] = clear
        record = service.store.update_part_meta(project, part_id, folder=..., tags=...)
        service.bus.publish({"type": "project_changed", "project": project, "part": part_id, "reason": "meta"})
        service.bus.publish({"type": "parts_meta_changed", "project": project, "part_ids": [part_id], "fields": [...]})
        return {"id": record.id, "folder": record.folder, "tags": list(record.tags)}
    registry.register(Tool("set_part_meta", "...", schema({...}, ["project", "part_id"]), set_part_meta))
    # --- search (slice 2) ---
    # --- thumbnails (slice 3) ---
    # --- bulk (slice 4) ---
```
The schema for `folder` is `{"type": ["string", "null"]}` — check `ToolRegistry.call`'s validator accepts a type list (read `tools.py:80-98`); if it does not, declare `"type": "string"` and treat `""` as root, documenting it in the description. `parts_meta_changed` is published **after** `project_changed`.

`routes_assembly2.patch_instance`: `if "folder" in body: target.folder = normalize_folder(body["folder"])`; move the mate `ConflictError` under `if "position" in body or "rotation_deg" in body:`.

**Tests (`tests/test_navigation_meta.py`):** grammar tables for folder/tag (valid, invalid, normalization: `" Printed "` → `printed`, dedupe order); `to_manifest` byte-identity for a part with no meta (compare `json.dumps` of a fresh project's manifest before/after a no-op `update_part_meta(tags=[])`); `update_part_meta` round-trips through a fresh `ProjectStore` on the same dir; `get_project`/`get_part` expose them; `set_part_meta` tool publishes `project_changed` then `parts_meta_changed` (subscribe a queue, assert order) and is one undo step (`make_test_service` disables snapshots — use a service with history: see how `tests/test_undo*.py` builds one); `update_parts_meta` with a missing id writes nothing; the write guard is invoked once per id with the right `write_scope` (install a recording `store.write_guard` that reads `locks.write_part_var`); instance `folder` survives `set_assembly`, the gizmo PATCH, `tools_structure`'s `_set_assembly` and a pattern expansion (`mates._member` inherits it); PATCH with `folder` on a mate-driven instance succeeds, PATCH with `position` still refuses. Run `uv run pytest tests/test_navigation_meta.py tests/test_assembly*.py tests/test_structure*.py tests/test_tools.py -q` — controller runs the full suite.

## Slice 2 — search engine, `search_parts`, search route, the parity fixture (FR3, §2) — **Opus**

**Files:** create `agentcad/core/search.py`, `agentcad/server/routes_navigation.py`, `tests/fixtures/search_queries.json`, `tests/test_search.py`; modify `agentcad/core/tools_navigation.py` (inside `# --- search (slice 2) ---` only).

**Produces:**
```python
# agentcad/core/search.py
FIELDS = ("tag", "material", "state", "kind", "folder", "id", "label")
STATES = ("ok", "error", "unbuilt"); KINDS = ("script", "reference", "package")
GRAMMAR = "…one paragraph quoted into the tool description and the 422…"
@dataclass(frozen=True) class Term: field: str | None; value: str; negate: bool
@dataclass(frozen=True) class Query: terms: tuple[Term, ...]
def parse(query: str) -> Query          # ValidationError on unknown field / unknown state or kind value / unterminated quote; "" -> Query(())
class Engine:
    def __init__(self, service): ...
    def rows(self, proj) -> list[dict]   # memo keyed (lock_key, manifest mtime_ns, size): [{id,label,material,folder,tags,kind,state,script_path}]
    def script_text(self, proj, part_id) -> str   # memo keyed (path, mtime_ns, size); "" for reference parts / missing file
    def search(self, proj, query: str, *, filters: dict | None = None, limit: int = 50) -> dict
        # {"query", "total", "parts": [{id,label,material,folder,tags,state,kind,matched_on:[...],snippet?}]}
def matches(row: dict, script_text: str, query: Query) -> list[str] | None   # matched_on or None — PURE, shared with the fixture test
```
Semantics per spec §2 (`kind:package` = `packages.provenance` recognises the script's header — look at `provenance.parse`/its status-on-read entry point and use the cheapest "has a header" check it offers; `state` from `service._status.get(service._status_key(proj, pid))`; free text hits id/label/tags/material **and** script text, `snippet` = 120 chars around the first script hit when script is the only match). Ranking: id/label › tag › material › script, then manifest order. `limit` 1..500 (ValidationError outside). `filters` = `{tag?: [str], material?, state?, kind?, folder?}` ANDed as extra terms. `service.search = Engine(service)` is installed by `tools_navigation.register` (inside the slice-2 anchor block).

Tool `search_parts {project, query, filters?, limit?}` (required: `project`, `query`). Route `GET /api/projects/{proj}/search?q=&limit=` in `routes_navigation.build_router(service, registry)` → same payload; a `ValidationError` is the usual 422 envelope; unknown project 404.

**Fixture** `tests/fixtures/search_queries.json`: `{"parts": [ {id,label,material,folder,tags,kind,state,script} × ~12 ], "cases": [ {"query": "...", "expect": ["ids in rank order"]} × ~25 ]}` covering: plain text on label, id substring, `tag:`, `-tag:`, `material:`, `state:error`, `kind:package` (a part whose `script` carries a real provenance header — copy one from `tests/fixtures/packages` or generate with `provenance` helpers), `folder:` prefix (and the `a/bc` non-match), quoted phrase, `label:`, `id:`, two terms AND, script-text-only hit, empty query = all in manifest order, and three `"error": true` cases (unknown field, bad state, unterminated quote). `test_search.py` runs every case through `matches()` + the ranking (and the error cases through `parse`). Slice 5's node test consumes the same file.

**Latency test:** build a project with 1 000 parts via `store.add_part` (or write `project.json` + `parts/*.py` directly — never build them) with ~2 KB scripts that mention a unique token each; assert cold `search("token_0777")` `< 0.5 s` and a second identical call `< 0.1 s`; assert editing one script (`write_script`) invalidates only that memo entry (the hit changes). Print the measured numbers with `-s` so the controller can quote them.

## Slice 3 — thumbnails (FR4, §3) — **Opus**

**Files:** create `agentcad/core/thumbnails.py`, `agentcad/server/routes_thumbnails.py`, `tests/test_thumbnails.py`; modify `agentcad/core/service.py` (`rebuild_finished` gains `"cache_key": key` in both branches of `_build_with`; `get_project` parts gain `"thumb_key"`), `agentcad/core/project.py` (`_TRIMMABLE += (".thumb.png",)`), `agentcad/core/tools_navigation.py` (inside `# --- thumbnails (slice 3) ---`: `service.thumbnails = ThumbnailWarmer(service); service.thumbnails.start()` unless `os.environ.get("AGENTCAD_THUMBNAILS") == "off"`).

**Produces:**
```python
# agentcad/core/thumbnails.py
THUMB_SIZE = 192
def thumb_path(cache_dir: Path, key: str) -> Path                 # cache_dir / f"{key}.thumb.png"
def mesh_for_key(cache_dir: Path, key: str) -> Path | None        # prefers <key>.lod1.acm, else <key>.acm, else None
def render_part_thumb(cache_dir: Path, key: str) -> bytes | None  # None when no mesh or > render.MAX_TRIANGLES; atomic write; returns PNG bytes
def part_thumb(service, proj, part_id) -> tuple[bytes, str] | None   # (png, key) from the part's CURRENT _status key; None when unbuilt/no mesh — NEVER builds
def assembly_key(service, proj) -> str | None      # sha256 over sorted (mesh key of inst.part@config, position, rotation_deg, color); None if no instance has a mesh
def assembly_thumb(service, proj) -> tuple[bytes, str] | None   # composite like tools_vision (transforms, colours), cached at .cache/asm.<key>.thumb.png; falls back to first built part's thumb; None otherwise
def has_thumb(service, proj) -> bool               # cheap is_file checks only (an existing asm/part thumb OR a part mesh that could be rendered) — the dashboard's gate, never renders
class ThumbnailWarmer:
    def __init__(self, service, *, maxsize=256): ...
    def start(self) -> None            # daemon thread consuming service.bus.subscribe(); idempotent
    def stop(self) -> None
    def enqueue(self, proj, key) -> None   # coalesces; when full, drops the OLDEST
    def drain(self, timeout=10.0) -> None  # test seam: block until the queue is empty and no render in flight
    stats -> {"rendered": n, "skipped_exists": n, "skipped_missing": n, "skipped_too_large": n, "dropped": n}
```
The warmer reacts to `rebuild_finished` events carrying `cache_key` (and ignores `config`-tagged ones — matrix builds are not tree rows). One render at a time; a render failure is logged to stderr and counted, never raised.

Routes (`routes_thumbnails.build_router(service, registry)`):
- `GET /api/projects/{proj}/parts/{part_id}/thumb.png?k=` → `part_thumb`; 404 (`NotFoundError`) when `None`; `ETag: "<key>"`; `If-None-Match` equal → 304; `Cache-Control: private, max-age=31536000, immutable` iff `k` (gated `[0-9a-f]{32}` fullmatch; a malformed `k` is ignored, not a 422) equals the served key, else `Cache-Control: no-cache`. `media_type="image/png"`.
- `GET /api/projects/{proj}/thumb.png` → `assembly_thumb`, same header rules, ETag the composite key.
Both pass a `CountingKernel`-style spy test: zero kernel requests across a cold project, an unbuilt part (404), and a built part (200).

`get_project` part dict: `"thumb_key": status["cache_key"] if status and status["state"] == "ok" else None`.

**Tests:** key = build cache key (build a part, assert `.cache/<status cache_key>.thumb.png` after `warmer.drain()`); a script edit → new key → new file, old one still present until trimmed; LOD1 preferred when the sidecar exists (create a fake `<key>.lod1.acm` by copying a tiny mesh and assert `mesh_for_key` picks it); `render_part_thumb` returns None for a missing mesh; `MAX_TRIANGLES` guard (monkeypatch `render.MAX_TRIANGLES = 1`); warmer coalescing (enqueue the same key 5× → rendered 1), drop-oldest at `maxsize=2`, `AGENTCAD_THUMBNAILS=off` leaves `service.thumbnails` with no thread; on-demand route render when the warmer is off; 404 for unbuilt; ETag/304; immutable only on key match; `trim_cache` sweeps an unreferenced `<key>.thumb.png` (use `min_age_s=0`); assembly composite key changes when an instance moves; first-part fallback; `rebuild_finished` carries `cache_key` on both the cached and fresh path. PNG sanity: bytes start with `\x89PNG`, and `render.encode_png` output size is 192×192 (parse the IHDR).

## Slice 4 — `bulk_part_op` + dashboard (FR5, FR6, §4, §6) — **Opus**

**Files:** modify `agentcad/core/navigation.py` (add `BulkExecutor`, `dashboard`), `agentcad/core/project.py` (`remove_parts`), `agentcad/core/tools_navigation.py` (inside `# --- bulk (slice 4) ---`), `agentcad/server/routes_navigation.py` (add `GET /api/dashboard`); tests `tests/test_tools_navigation.py`, `tests/test_routes_navigation.py`.

**Produces:**
```python
# ProjectStore
def remove_parts(self, proj, part_ids: list[str], *, force: bool = False) -> dict
    # validates all first: missing -> per-item NotFound; used-by-instances -> per-item ConflictError(details.instances) unless force (then those instances are dropped in the same write)
    # returns {"removed": [...], "errors": {pid: error_payload}}; ONE save_manifest; unlinks scripts after the save
# agentcad/core/navigation.py
OPS = ("material", "tag", "untag", "folder", "export", "delete")
MAX_BULK = 500; MAX_BULK_EXPORT = 50
class BulkExecutor:
    def __init__(self, service): ...
    def run(self, proj, part_ids, op, args) -> dict
        # {"op", "ok": all_ok, "applied": n, "results": [{"id", "ok", "error"?, ...}], "undo_label": "bulk material ×6" | None}
def dashboard(service) -> dict   # {"projects": [{name, path, n_parts, n_instances, mass_g|None, failing, last_modified|None, thumb|None}]}
```
`run` rules: de-dup ids (order kept), 1..MAX_BULK else ValidationError; unknown op → ValidationError listing OPS; per-op arg validation **before any write** (`material` via the resolver: `service.material_density(proj, m)` or `store._validate_material` — whichever the single-part path uses; `tags` via `normalize_tags`; `folder` via `normalize_folder`; `export` format via `service._check_format`). Manifest ops: compute `edits` per existing id (missing ids become per-item errors), `store.update_parts_meta(...)` once, publish `project_changed {reason: undo_label}` once, then `parts_meta_changed {part_ids, fields}`; for `material` then call `service.rebuild_after_write(proj, pid)` per part (read its exact signature at `service.py:867`) and fold the result's `ok` into the item. `delete` → `store.remove_parts` + status eviction like `service.delete_part` (`service.py:441-452`) + one publish with `part` omitted. `export` → per-item `service.export_part(proj, pid, format, tolerance?)` catching `AppError`/`KernelError` per item, no publish, ≤ `MAX_BULK_EXPORT`.

`dashboard(service)`: iterate `service.list_projects()`; per project read the manifest (`store.manifest`) and in-memory `_status` only; `mass_g` = sum of `status["metrics"]["mass_g"]` when **every** part has an ok status with metrics, else `None`; `failing` = count of `state == "error"`; `last_modified` = ISO-8601 UTC of `project.json`'s mtime; `thumb` = `/api/projects/<p>/thumb.png` when `thumbnails.assembly_key`/first-part thumb would answer **from existing files without rendering** — add `thumbnails.has_thumb(service, proj) -> bool` (cheap `is_file` checks) for this. Never reads a script, never calls the kernel. Route `GET /api/dashboard` (member-only).

**Tests:** AC4 shape — six parts, `bulk_part_op material steel_a36` (use a material id that exists in the shipped library), `get_history` shows exactly one new step labelled with the `undo_label`, `undo` restores all six materials (build with a history-enabled service; see `tests/test_undo*.py`); partial success (one unknown id → `ok: false`, `applied: 5`, the five written); invalid material → refusal, nothing written; `tag`/`untag` idempotent and normalized; `folder` with `None` → root; `delete` refuses a used part with `details.instances`, `force` removes the instances in the same write (one undo step, instance gone); `export` bounded at 50 and returns paths; `parts_meta_changed` published after `project_changed` with the right `part_ids`; a claim-held part (install a `write_guard` that raises `ConflictError` for `pid == "b"`) refuses the whole bulk with nothing written. Dashboard: 20 synthetic projects → `< 0.5 s` (print it), `mass_g` None when one part unbuilt, `failing` counts, `thumb` null without files, the route is member-only (`hosted_client` 401 anonymous / 200 logged in), kernel spy zero.

## Slice 5 — frontend pure models + context menu primitive (§2 client half, §7) — **Opus**

**Files:** create `frontend/js/query_model.js`, `frontend/js/virtual_model.js`, `frontend/js/shell/contextmenu.js`, `tests/test_frontend_navigation.py`; modify `frontend/js/tree_model.js` (add functions, keep the existing three + `__treeModel__` seam extended).

**Produces (ES modules, no DOM access at import time):**
```js
// query_model.js  — a byte-equivalent port of core/search.py's grammar & matcher (minus script text)
export const FIELDS = ["tag","material","state","kind","folder","id","label"];
export function parse(query) → {terms:[{field, value, negate}]}   // throws Error(message) on the same inputs Python refuses
export function matches(part, query /* parsed */, {scriptText=""}={}) → string[]|null   // matched_on or null
export function hasFreeText(query) → boolean                      // true when any term has field === null (server call needed)
export const __queryModel__ = { parse, matches, hasFreeText, FIELDS };

// virtual_model.js
export function window({scrollTop, viewportHeight, rowHeight, total, overscan=8}) → {start, end, padTop, padBottom}
export const __virtualModel__ = { window };

// tree_model.js (additions)
export function folderTree(parts, {collapsed=[], emptyFolders=[]}={}) → rows
   // rows: [{kind:"folder", path, name, depth, count, collapsed}, {kind:"part", id, part, depth}]; folders first (case-insensitive alpha), parts in manifest order; descendants of a collapsed folder omitted; an emptyFolders path renders (count 0) unless a part already names it
export function filterRows(parts, query /* parsed */, opts) → {rows, total, shown}
   // applies matches(); ancestors of a hit are kept and FORCED OPEN; folders with no hit dropped
export function instanceTree(instances, {collapsed=[]}={}) → rows   // same shape over inst.folder; instanceRows() kept for the pattern/sub-assembly badges (a row carries `row.instance` = the instanceRows() descriptor)
export function selectionAfter(current /*Set*/, anchor, visibleIds, clickedId, {shift, meta}) → {selection:Set, anchor, primary}
export function persistTree(project, {collapsed, emptyFolders}) → string   // JSON for localStorage["agentcad.tree.<project>"]
export function readTree(json) → {collapsed:[], emptyFolders:[]}           // clamps: arrays of valid folder paths only, ≤ 500 entries
```
`shell/contextmenu.js`: `init(hostEl)`, `open({x, y, items:[{id, label, danger?, disabled?, run}], label?}) → Promise<void>` (resolves on close), `close()`, `isOpen()`; markup via `markup(items)` (exported, pure string: `<ul role="menu" aria-label>` + `<li role="menuitem" tabindex="-1" data-id>`), arrow keys/Home/End/Enter/Esc, outside click/scroll closes, positioned inside the viewport (flip when near the edge), registered on the dialogs overlay stack via `dialogs.attachLegacy`-style push/pop so `isModalOpen()` stays false (it is non-modal) but Esc routing is owned by the stack — read `dialogs.js:384-430` and follow the adopted-modal contract; uses `.menu`/`.menu-item` styles plus a `.ctx-menu` class.

**Tests (`tests/test_frontend_navigation.py`, node-in-pytest, skip without node):** parity — every case in `tests/fixtures/search_queries.json` through `query_model` gives the same ids (script-only cases pass `scriptText` from the fixture); error cases throw; `folderTree` ordering/collapse/empty folders; `filterRows` bubbling + forced-open; `selectionAfter` tables (click, Cmd-toggle, Shift-range over visible order, Shift with no anchor = click); `virtual_model.window` (10 000 rows × 28 px, viewport 600 → ≤ 30+2·overscan rows; pads sum to total height; clamps at the end); `readTree` clamping; `contextmenu.markup` a11y (`role="menu"`, every item `role="menuitem"`, danger class, disabled → `aria-disabled`). `node --check` every new file.

## Slice 6 — frontend DOM: tree rewrite, bulk bar, dashboard, wiring (FR2, FR5 UX, FR6 UX, FR7, FR8, §6, §7) — **Opus**

**Files:** rewrite `frontend/js/tree.js`; create `frontend/js/bulk.js`, `frontend/js/dashboard.js`; modify `frontend/js/main.js` (events `parts_meta_changed`, `rebuild_finished` thumb swap, actions `project.dashboard` / `part.bulk.*` / `tree.filter.focus`, `state.selection`, first-run dashboard), `frontend/js/state.js` (`selection: new Set()`, `selectionAnchor: null`, `treeFilter: ""`, `dashboardOpen: false`), `frontend/js/api.js` (`searchParts`, `setPartMeta`, `bulkPartOp`, `dashboard`, `partThumbUrl(project, id, key)`, `projectThumbUrl`, `patchInstance` gains `folder`), `frontend/index.html` (filter box, bulk bar host, `#dashboard` host, context-menu host), `frontend/css/app.css` (`.tree-*`, `.row-thumb`, `.bulk-bar`, `.dash-*`, `.ctx-menu`; token colours only; `prefers-reduced-motion`).

Behaviour per spec §6/§7: fixed 28 px rows; one `<ul role="tree">` per section with top/bottom spacers, window re-rendered on `scroll` via `requestAnimationFrame`; rows: twist / 24 px `<img class="row-thumb" loading="lazy">` (placeholder glyph span when `thumb_key` is null or the image errors) / label / state dot / existing badges (`ref`, `cfg`, presence, claim) / `⋯` button; the row `×` button is gone; click/Cmd/Shift selection through `selectionAfter`; `ArrowUp/Down` move focus (roving `tabIndex`), `ArrowLeft/Right` collapse/expand folders, `Enter` selects, `Space` toggles selection, `/` focuses the filter (when not in a field and no modal), `Esc` in the filter clears it and refocuses the tree; filter applies `filterRows` synchronously and, when `hasFreeText`, calls `api.searchParts` debounced 120 ms and unions the returned ids (rows matched only server-side get a small `script` badge); "n of N" count; HTML5 drag: parts (the selection when the dragged row is selected) onto folder rows / a root drop zone → `bulkPartOp folder` (or `setPartMeta` for one); instances onto instance folders → `patchInstance({folder})`; context menu items: Rename… (label, via `api.updatePart`), Tags…, Move to folder…, New folder… (prompt; adds to `emptyFolders`), Export…, Delete… (danger; uses the existing delete dialog for one, a bulk confirm naming N for many); collapse state persisted per project via `persistTree`/`readTree`; `parts_meta_changed` patches `state.project.parts` in place + re-render; `rebuild_finished` with `cache_key` updates that part's `thumb_key` and swaps the `<img>`.

`bulk.js`: strip under the filter box, visible when `state.selection.size > 1` — "N selected · Material · Tags · Folder · Export · Delete · ×"; each opens a `dialogs.form` and calls `api.bulkPartOp`; a result with any `ok: false` item opens the non-modal results dialog (`view: "bulk-results"`, table id/status/error, registered in the dialog registry); success → one toast with the undo label.

`dashboard.js`: `#dashboard` full-pane view (hidden by default) with a card grid from `api.dashboard()`; cards: hero `<img loading="lazy">` (placeholder when `thumb` null), name, `N parts · M instances`, `mass` (formatted; "—" when null), relative time, red `n failing` badge; click → `loadProject(name)` and hide; "New project…" / "Open by path…" cards run `actions.run("project.new")` / `("project.open-path")`; opened on first run (no `localStorage["agentcad.project"]`), by action `project.dashboard` (`Mod+Shift+O`, menu `file/15`, palette), and a project-menu row "All projects…"; `Esc` closes it when a project is open; registered as dialog view `dashboard` so `ui_open {view: "dashboard"}` works.

**Verification (required, in the brief):** `node --check` on every changed module; `uv run pytest tests/test_frontend_*.py tests/test_prd026_acceptance.py -q` (AC1 grep: no native `prompt/confirm/alert`; legacy-overlay invariants); a Playwright run (`uv run --no-project --with playwright python <script>`, `chromium.launch(channel="chrome", args=["--use-angle=swiftshader","--enable-unsafe-swiftshader"])`) against `uv run agentcad serve --port 8632 --projects-dir <scratch copy of examples/engine + a generated 1 000-part project>` with `localStorage["agentcad.project"]` pre-set: (a) type `err` with one broken part → only it remains, timing via `performance.now()` around the input event `< 100 ms`; (b) fix the script → the dot clears without reload; (c) the 1 000-part project: scroll the tree 50 steps, mean frame `< 16.7 ms` (measure with `requestAnimationFrame` deltas), DOM row count ≤ window size; (d) a thumbnail `<img>` has `naturalWidth === 192` for a built part; (e) the dashboard shows the cards with stats. Save screenshots to the scratchpad and quote the numbers in the report.

## Slice 7 — acceptance tests + docs (AC1–AC6) — **Opus**

**Files:** create `tests/test_prd027_acceptance.py`; modify `docs/agent-api.md` (three tools with the `GRAMMAR` paragraph, the `parts_meta_changed` event, `get_project` fields, `rebuild_finished.cache_key`), `docs/user-guide.md` (Sidebar → folders/tags/filter/selection/context menu/bulk bar; Dashboard; shortcut table rows `/` and `Mod+Shift+O`), `docs/architecture.md` (the three modules, two packs, the thumb cache + the `immutable` precedent), `AGENTS.md` + `CLAUDE.md` (a "Navigation" trap block: one-publish-per-bulk, thumbs never build, `_TRIMMABLE`, instance `folder` five writers, `kind:package` via provenance, the grammar constant, tool count), `README.md`; tool count **85 → 88 / 88 → 91** in `docs/agent-api.md:3`, `docs/architecture.md` (two places), `docs/user-guide.md`, `README.md` (two places), `AGENTS.md`, and `tests/test_prd012_acceptance.py`'s asserted string.

**Acceptance tests:** AC1 — copy `examples/engine` to tmp, open it, `set_part_meta` folders `block/pistons/fasteners` on real part ids + `tag:fastener` on the fastener parts, re-open the store from disk, `get_project` shows them, `search_parts {query: "tag:fastener"}` returns exactly that set; AC2 machine half — `search_parts state:error` returns the one broken part (the browser half is evidence-graded in the changelog); AC3 — build a part, thumb exists under key₁, edit the script, rebuild, thumb under key₂, `get_project.thumb_key == key₂`; AC4 — six parts → bulk material → one `get_history` step → `undo` restores six; AC5 model half — 1 000-part `folderTree` + `virtual_model.window` in node ≤ 60 rows (the browser frame time is evidence-graded); AC6 — every new tool name appears in `docs/agent-api.md`, every new route template appears in `flatten_routes(app)` and is **not** public (`is_public` false), the three tools are in `build_registry`, the user guide mentions "dashboard" and the filter shortcut, the newest changelog cites `make test … N passed` (the PRD-026 AC7 shape).

---

## Non-negotiables (repeat of the spec's rulings the implementer must not re-open)

- Bulk = one store RMW + one `project_changed` publish; never N publishes; never touch `history.in_restore`.
- Thumbnail code reads `.cache/<key>[.lod1].acm` off disk; `ensure_mesh` is forbidden in `thumbnails.py`, `routes_thumbnails.py`, `navigation.dashboard`.
- `_rebuild`/`get_part` signatures unchanged; `gate_providers` untouched; `tools_navigation` registers no gate provider.
- No new dependency; no `package.json`; pure models carry a `__x__` test seam.
- Subagents report file lists + test commands + outputs; the controller commits.
