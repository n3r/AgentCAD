# PRD-027 Navigation at scale — design spec (folders, tags, search, thumbnails, bulk ops, dashboard, virtualized tree)

Grounded in a survey of `main` at `95fdcc3` (PRD-026 shell + PRD-015 landed):
`core/model.py` (`PartRecord`/`InstanceSpec` dataclasses, conditional
`to_manifest`), `core/project.py` (`ProjectStore`: `manifest`/`save_manifest`,
`update_part_entry` under `locks.write_scope`, `trim_cache` + `_TRIMMABLE`),
`core/service.py` (`get_project`, `_build_with`'s `rebuild_*` events,
`EventBus.on_publish` = `_snapshot_on_event`, `export_part`), `core/history.py`
(`UndoCursor.on_snapshot` — **one `project_changed` publish = one undo step**),
`core/render.py` (`render_acm` — numpy + stdlib PNG, no OCP, importable from the
server), `core/tools_vision.py` / `core/packet.py` (the render call shape),
`frontend/js/tree.js` + `tree_model.js` (pure-model/DOM split, node-tested),
`frontend/js/shell/*` (dialogs, actions, menu, toast, palette), `main.js
handleEvent`, `server/security.py` (default-deny, `PUBLIC_PATHS` equality
test). This spec records the decisions, rejected alternatives, and the rulings
the orchestrator made where the PRD left room; the slice plan is the sibling
`docs/superpowers/plans/2026-08-23-project-navigation-scale.md`.

## Scope (what this PRD builds now)

**MVP plus Phase 2** of the PRD:

- **Build now:** FR1 manifest metadata (part `folder`/`tags`, instance
  `folder`), FR2 folder tree with thumbnails + drag-move + persisted collapse,
  FR3 search (server engine + the `field:value` query language + the pinned
  filter box), FR4 content-hash thumbnails (post-build, budget-capped, never
  on the rebuild path), FR5 bulk ops (material / tag / untag / folder / export
  / delete) as **one undo step**, FR6 dashboard (cards, stats, badges), FR7
  virtualized tree, FR8 live updates from the existing events; the three
  tools `search_parts` · `set_part_meta` · `bulk_part_op`, the
  `parts_meta_changed` event, `get_project` carrying `folder`/`tags`.
- **Defer (Phase 3, with PRD-013's Phase 2):** sub-assembly *nesting* inside
  folders (a sub-assembly stays the one read-only row PRD-013 gave it),
  instance-pattern member rows inside folders, and the 1k-*instance*
  certification (PRD-013's synthetic is one pattern row; AC5 here is a 1k-
  **part** synthetic). Also out: saved smart-filter views, cross-project
  search, folder ACLs, a feature-history tree (PRD non-goals).

Non-goals unchanged: no framework/bundler, no kernel change, scripts stay
flat in `parts/` (folders are manifest metadata — see Risk 1).

## 0. Shape of the change (Decision 0)

Three new Python modules and two packs, one new frontend module family, no
core-file rewrites:

| Layer | New | Touched (additively) |
|---|---|---|
| model/store | — | `core/model.py` (`folder`, `tags` on `PartRecord`; `folder` on `InstanceSpec`; conditional `to_manifest`), `core/project.py` (`get_part` reads them; `update_part_meta`, `update_parts_meta` bulk RMW; `.thumb.png` in `_TRIMMABLE`) |
| engine | `core/search.py` (query grammar + matcher + per-project memo), `core/thumbnails.py` (render + cache + background warmer), `core/navigation.py` (bulk-op executor, dashboard stats) | `core/service.py` (`get_project` emits `folder`/`tags`/`thumb_key`; `rebuild_finished` carries `cache_key`) |
| packs | `core/tools_navigation.py` (`search_parts`, `set_part_meta`, `bulk_part_op`), `server/routes_navigation.py` (search, dashboard), `server/routes_thumbnails.py` (part + assembly thumbs — its own pack so the thumbnail slice and the search slice can run concurrently) | — |
| frontend | `js/tree_model.js` (+folder tree/flatten/selection), `js/virtual_model.js`, `js/query_model.js`, `js/shell/contextmenu.js`, `js/dashboard.js`, `js/bulk.js` | `js/tree.js` (rewrite on the new models), `js/main.js` (wiring, events), `js/api.js`, `index.html`, `css/app.css` |
| docs/tests | sections in `docs/user-guide.md` + `docs/agent-api.md` + `docs/architecture.md`; `tests/test_search.py`, `test_thumbnails.py`, `test_tools_navigation.py`, `test_routes_navigation.py`, `test_frontend_navigation.py`, `test_prd027_acceptance.py` | `tests/test_frontend_tree.py` |

## 1. Manifest metadata (Decision 1 — FR1)

- `PartRecord.folder: str | None = None`, `PartRecord.tags: list[str] =
  field(default_factory=list)`; `InstanceSpec.folder: str | None = None`.
  `to_manifest` writes each **only when set** (the `solid_materials`/`configs`
  precedent) so an untouched project serializes byte-identically.
- **Folder grammar** (`navigation.FOLDER_SEGMENT_RE`): a `/`-joined path of 1–8
  segments, each `^[A-Za-z0-9][A-Za-z0-9 _.-]{0,39}$` with no leading/trailing
  space (so "Pistons", "chassis/left side" are fine; `..`, empty segments,
  leading `/`, `\`, control chars are refused). Stored verbatim (case kept —
  a display name); matched case-insensitively in search. `None`/`""` = root.
- **Tag grammar** (`navigation.TAG_RE`): `^[a-z0-9][a-z0-9_.-]{0,31}$`;
  **normalized on write** — stripped, lowercased, de-duplicated preserving
  first-seen order; ≤ 32 tags per part. A tag that is still invalid after
  normalization (spaces inside, `#`, `:` …) is a `validation_error` naming it.
- `store.update_part_meta(proj, part_id, *, folder=_UNSET, tags=None)` under
  `locks.write_scope(part_id)` (the claim guard sees the part) and
  `store.update_parts_meta(proj, edits: dict[str, dict])` — **one manifest RMW
  for N parts** (this is what makes a bulk op one snapshot). The write guard
  is invoked by `save_manifest` with whatever `write_scope` is current, so the
  bulk method runs `self.write_guard(proj)` **once per part id inside
  `locks.write_scope(pid)` before mutating anything**, then mutates and saves
  once — a part another human holds (PRD-008 claim) refuses the whole bulk
  with the usual `ConflictError`, which is the right UX: partial success is
  for *per-item validity*, not for stepping on a colleague.
- `get_project` (`service.py:248` — a hand-written whitelist) parts gain
  `"folder"`, `"tags"`, `"thumb_key"` (§3); `get_part` (`service.py:317`, the
  second whitelist) gains `folder`/`tags`. **Instances are the asymmetric
  half:** `ProjectStore.instances()` rebuilds `InstanceSpec` from a fixed key
  list and `set_instances` writes `[i.to_manifest()]` (a full replace), so an
  unknown instance key evaporates on the next gizmo drag. `folder` therefore
  goes on the dataclass, into `instances()`'s read, into `to_manifest`, and
  through every place that constructs an `InstanceSpec` from a dict
  (`service.set_assembly`, `tools_mates`, `tools_structure`'s pattern/sub-
  assembly writers, `routes_assembly2`) — the implementer greps
  `InstanceSpec(` and passes `folder=` (validated) at each site, and a test
  proves a gizmo PATCH keeps an instance's folder.
- `manifest_merge` (PRD-002/001): `folder` is a scalar and `tags` a list, and
  a field outside `_PART_ENTRY_DICTS` merges **atomically** — a both-sides
  edit of one part's `tags` is a whole-list conflict, like `packages`. Ruled
  acceptable for metadata (the alternative, set-union, would silently keep a
  tag one side deliberately removed); recorded, no new problem class.

Rejected: folders as directories under `parts/` (breaks `script_path`,
git-diff stability, package materialisation, every `parts/<id>.py` assumption
in the packs); a separate `navigation.json` sidecar (not in the snapshot =
organizing would not undo; not in proposals = a proposal could not move a
part). The manifest is already the one file every seam snapshots.

## 2. Search engine and query language (Decision 2 — FR3, G2)

`core/search.py`:

- **Grammar** (`search.parse(query) → Query`): whitespace-separated terms;
  `field:value` with `field ∈ {tag, material, state, kind, folder, id,
  label}`; a leading `-` negates a term; double quotes group a phrase
  (`"m5 boss"` or `folder:"left side"`); anything else is free text. Unknown
  `field:` → `validation_error` listing the fields (an agent typo must not
  silently become free text). Terms AND together; a repeated `tag:` ANDs
  (use `tag:a tag:b` for both, `-tag:a` to exclude).
- **Semantics:** `tag` exact (normalized); `material` exact on the id;
  `state ∈ {ok, error, unbuilt}` from `_status` (the server has no
  "building" state — that is a client notion); `kind ∈ {script, reference,
  package}` where **package** = a script part whose script carries the
  package provenance header (`packages.provenance.parse` recognises it —
  the manifest's `packages_lock` alone cannot say which *part* came from
  where); `folder` = the folder **or any sub-folder** (prefix on segments,
  case-insensitive); `id`/`label` substring, case-insensitive. Free text
  matches id, label, tags, material id, **and script text** (substring,
  case-insensitive); a hit reports `matched_on: ["label"|"id"|"tag"|"script"|…]`
  and a `snippet` (≤ 120 chars around the first script hit) when the only
  match is in the script.
- **Ranking:** id/label match › tag › material › script; ties by manifest
  order. `limit` default 50, max 500; the response carries `total`.
- **Index strategy — on-demand scan with a per-file memo, no maintained
  inverted index.** The PRD asked for the benchmark; the numbers at our scale
  decide it: a 1 000-part manifest is ~300 KB of JSON (≈3 ms to load), and
  1 000 scripts of ~2 KB are ≈2 MB of text (≈15–30 ms to read cold from the
  page cache, ≈1 ms warm). `search.Engine` memoizes each script's lowered
  text keyed on `(path, mtime_ns, size)` and the manifest-derived rows keyed
  on the manifest's `(mtime_ns, size)` per `lock_key` (a branch checkout
  changes the file, so the key changes). **No EventBus plumbing**: the bus's
  one pre-fan-out hook is `_snapshot_on_event` and a subscriber queue would
  add a thread for a cache that stat() validates in microseconds. AC: the
  acceptance test builds a 1 000-part synthetic project (scripts never built
  — search is kernel-free) and asserts a warm query `< 100 ms` and a cold
  one `< 500 ms` (CI headroom; the measured local number goes in the
  changelog).
- The grammar string is **one module constant** (`search.GRAMMAR`) quoted
  into the tool description and the route's 422 message, the
  `materials_query` precedent — docs cannot drift from the parser.
- **Tool** `search_parts {project, query, filters?, limit?}`: `filters` is an
  optional object `{tag?: [..], material?, state?, kind?, folder?}` ANDed
  with the query (so an agent can pass structured filters without quoting).
  Result: `{query, total, parts: [{id, label, material, folder, tags, state,
  kind, matched_on, snippet?}]}`.
- **Route** `GET /api/projects/{p}/search?q=…&limit=…` → the same payload
  (member-only). The UI calls it debounced **only when the query has free
  text** (script content lives server-side); every metadata-only filter is
  answered client-side by `query_model.js`, a **byte-equivalent port of the
  grammar** — parity is enforced by one shared fixture,
  `tests/fixtures/search_queries.json` (`[{query, parts, expect_ids}]`),
  consumed by the Python test and by the node test of `query_model.js`.

Rejected: a maintained inverted index (a second source of truth invalidated
by events we would have to route; wins nothing under ~10 k parts); SQLite
FTS (a dependency and a file next to the manifest for a grep); full-text on
the client (the scripts are not in the browser and must not be shipped
wholesale to it).

## 3. Thumbnails (Decision 3 — FR4, G3)

`core/thumbnails.py`:

- **Path and key:** `.cache/<cache_key>.thumb.png`, 192×192 iso, rendered by
  `render.render_acm` from the part's mesh — the **LOD1 sidecar when one
  exists** (big parts render faster at 192² from fewer triangles; the visual
  difference at that size is nil), else the full `.acm`. Content-addressed by
  the **build's cache key** (script + params + density + tolerance already
  hash into it), so a script change refreshes the thumb by construction and
  two parts with one key share one file. `.thumb.png` joins `_TRIMMABLE` so
  the janitor sweeps a trimmed key's thumb with its mesh.
- **Never on the rebuild path:** `ThumbnailWarmer` owns a daemon thread and a
  bounded queue (256, coalesced by `(proj, key)`), subscribed to the bus
  (`bus.subscribe()` — the `/ws` precedent) and reacting to `rebuild_finished`
  (which therefore **carries `cache_key`** — two additive lines in
  `_build_with`, both the cached and the fresh branch). A full queue drops the
  *oldest* entry (freshness catches up on the next read); one render in
  flight; a render is skipped when the file exists, when the mesh is gone,
  or when the mesh exceeds `render.MAX_TRIANGLES` after LOD selection
  (`reason: too_large` in the warmer's stats). `warmer.drain(timeout)` is the
  test seam; `AGENTCAD_THUMBNAILS=off` disables the thread (CI for the
  geometry checks and the bench never want it).
- **On demand is the fallback, not the warmer:** `GET
  /api/projects/{p}/parts/{id}/thumb.png[?k=<key>]` answers from the cache or
  **renders synchronously** when the part's current mesh exists but its thumb
  does not (≈5–20 ms at 192² — the warmer is a pre-warm, never a dependency).
  A part with no mesh (unbuilt, or error with no prior build) → **404** with
  the usual envelope; the route **never builds** (no kernel call — it is a
  static-asset path in spirit and must stay cheap under a dashboard burst).
  Headers: `ETag: "<key>"`; when `k` equals the current key →
  `Cache-Control: private, max-age=31536000, immutable`; otherwise
  `no-cache` (the browser revalidates with `If-None-Match` → 304). **This is
  the first non-`no-store` binary response in the codebase** (every mesh/
  render/drawing route is `no-store`): it is safe exactly because the
  immutable answer is keyed by the content hash the client named, never by
  the part id alone — a `thumb.png` URL without a matching `k` is revalidated.
- **Assembly thumbnail** `GET /api/projects/{p}/thumb.png`: the resolved
  instances' meshes composed as `render_view` does (per-instance transform
  and colour; unbuildable instances skipped), cached at
  `.cache/asm.<sha256 of the sorted (part_key, config, position, rotation,
  color) tuples>.thumb.png`; a project with no instances falls back to its
  first built part's thumb; nothing built → 404. **Kernel-free like the part
  route**: it uses only meshes that already exist (`mesh_info`, never
  `ensure_mesh`), so a dashboard of 20 projects cannot trigger 20 builds.
- `get_project` parts carry `thumb_key` (= the `_status` cache key when the
  state is `ok`, else `null`); the UI builds `…/thumb.png?k=<thumb_key>` and
  a `rebuild_finished` event (now carrying `cache_key`) swaps the row's
  `<img>` to the new key — no project refetch.

Rejected: rendering thumbnails in the kernel worker at build time (puts PNG
work on the confined, metered, pooled path the PRD says must stay clear);
`exports/renders/` (that is the user's export space, not derived cache);
a sprite sheet (premature).

## 4. Bulk operations and grouped undo (Decision 4 — FR5, AC4)

`core/navigation.py: BulkExecutor`:

- There is no `set_material` service method — material is
  `service.update_part(material=)` → `store.update_part_entry` per part. The
  bulk path does **not** call it N times (N publishes = N undo steps); it
  extends the single-RMW `store.update_parts_meta` with an optional
  `material` per edit (validated once through `_validate_material`).
- `bulk_part_op {project, part_ids, op, args}` with `op ∈ {material, tag,
  untag, folder, export, delete}`; `args`: `material {material}`, `tag/untag
  {tags: [..]}`, `folder {folder: str|null}`, `export {format, tolerance?}`,
  `delete {force?: bool}`. `part_ids` 1..500, de-duplicated, order kept.
- **Per-item validity, partial success:** each id is validated first
  (unknown part → `{id, ok: false, error: {type: "NotFoundError", …}}`,
  invalid material once for the whole op → a `validation_error` refusal
  *before any write*). The result is `{op, ok: <all items ok>, applied: n,
  results: [{id, ok, error?, …}], undo_label}`.
- **One undo step:** the manifest ops (`material`, `tag`, `untag`, `folder`,
  `delete`) are applied in **one** `store.update_parts_meta` / one
  `remove_parts` RMW, followed by **one** `project_changed` publish
  (`reason: "bulk material ×6"` — the snapshot message becomes the undo
  label, so "Undo bulk material ×6" reads right) — `_snapshot_on_event`
  makes that one commit, `UndoCursor` one entry. Rebuilds that follow a
  material change go through `service.rebuild_after_write` **per part
  after the publish** and publish only `rebuild_*` (never `project_changed`),
  so they add no undo entries. AC4's test: six parts → material → `undo` →
  all six back, `get_history` shows one step.
- **Delete with dependency checks:** an item whose part is referenced by an
  assembly instance (or a pattern) is refused per-item (`error.type:
  "ConflictError"`, `details.instances: [...]`) unless `force: true`, in
  which case the instances are removed in the same manifest write (one undo
  step still). Parts referenced by another project's sub-assembly cannot be
  known here and are not checked (recorded).
- **Export:** not a mutation — per-part `service.export_part` (kernel), each
  item returns `{id, ok, path}`; errors per item (kernel-class payload);
  no undo entry, no `project_changed`. Bounded: ≤ 50 ids per export call
  (each is a kernel round trip).
- `parts_meta_changed {project, part_ids, fields}` is published **after**
  `project_changed` on every metadata write (`set_part_meta`, bulk
  `tag`/`untag`/`folder`/`material`), so a browser can patch rows in place
  without waiting for the debounced project refetch.

Rejected: composing per-part `set_material` calls and "squashing" history
(the snapshot hook is per publish — N publishes are N commits; squashing
git history violates the linear-history contract of PRD-001); a
`history.group()` context manager (would have to suppress the hook for
nested publishes and then publish once — that *is* what the single-RMW
design does without a new global flag next to `in_restore`).

## 5. `set_part_meta` and the instance side (Decision 5)

- `set_part_meta {project, part_id, folder?, tags?}`: omitted = unchanged,
  `folder: null` = root, `tags: []` = clear. Returns the part's
  `{id, folder, tags}`. One `project_changed` + `parts_meta_changed`.
- Instances: `folder` rides the existing instance write paths (`set_assembly`
  / `PUT /assembly`, whose `InstanceSpec` round-trips it) — no new instance
  tool. `PATCH /projects/{p}/assembly/instances/{id}` (`routes_assembly2`)
  accepts a `folder` key (validated with the folder grammar, `null` = root);
  its mate-driven refusal now applies only when `position`/`rotation_deg` is
  in the body (organizing a mated instance is not a transform). The tree's
  instance-folder drag uses that PATCH.

## 6. Dashboard (Decision 6 — FR6, G6)

- `GET /api/dashboard` → `{projects: [{name, path, n_parts, n_instances,
  mass_g|null, failing: n, last_modified: iso|null, thumb: url|null}]}`.
  **Kernel-free and cache-free by contract:** `mass_g` sums `_status`
  metrics for built parts only and is `null` when any part has no metrics in
  memory (an honest "unknown", never a build); `failing` counts `_status`
  error states; `last_modified` is the manifest's mtime; `thumb` is the
  assembly/part thumb URL when a cached thumb or a renderable mesh **already
  exists** (the route checks the cache dir, it does not render here). The
  AC is 20 projects < 500 ms with warm thumbs; the test measures it on 20
  synthetic projects (stat + JSON only).
- Frontend `dashboard.js`: a full-pane `#dashboard` view (over the viewport,
  below the toolbar) with a card grid (hero `<img loading="lazy">`, name,
  `N parts · M instances`, mass, relative last-modified, a red `n failing`
  badge), a "New project…" card and an "Open by path…" card that run the
  existing `project.new` / `project.open-path` actions (PRD-026 dialogs).
  Shown on first run (no `localStorage["agentcad.project"]` and no `?project=`),
  via the palette/menu action `project.dashboard` (`Mod+Shift+O`), and from the
  project menu's "All projects…" row. The old switcher dropdown **stays** as
  the quick path (one click); the dashboard is the entry experience.

## 7. The tree (Decision 7 — FR2, FR7, FR8, G1, G7)

Pure models (node-tested) + one DOM module:

- `tree_model.js` gains `folderTree(parts, {collapsed, emptyFolders}) → rows`
  (a flattened list of `{kind: "folder"|"part", id, path, depth, count,
  collapsed}` in display order: folders first, alphabetical
  case-insensitive, then parts in manifest order), `filterRows(rows, query,
  ctx)` (applies `query_model` with a **match-bubbling** rule — a folder stays
  when any descendant matches, and matching **expands** collapsed ancestors
  while a filter is active; clearing restores the persisted collapse state),
  `instanceTree(instances, …)` (same flatten over instance `folder`, with the
  PRD-013 pattern/sub-assembly rows kept as one row each), and
  `selectionAfter(sel, visibleIds, clickedId, {shift, meta, anchor})` (the
  Finder rules: plain click = single, Cmd toggles, Shift ranges from the
  anchor over the *visible* order).
- `virtual_model.js`: `window({scrollTop, viewportHeight, rowHeight, total,
  overscan}) → {start, end, padTop, padBottom}` — fixed 28 px rows (thumb
  24 px + padding), rendered into one `<ul role="tree">` with spacer
  elements; `tree.js` re-renders the window on scroll via
  `requestAnimationFrame` and keeps the focused row's `tabIndex` roving
  (`aria-level`, `aria-expanded`, `aria-selected`, `aria-setsize/posinset`).
  The PRD's 60 fps/1k-row claim is measured in the browser on a 1 000-part
  synthetic project (Playwright: scroll 50 frames, mean frame < 16.7 ms on
  the SwiftShader Chrome; the number goes in the changelog), and the *model*
  half is asserted in node (a 10 000-row tree renders a ≤ 60-row window).
- **Selection state:** a new `state.selection` (`Set` of part ids, plus
  `selectionAnchor`) beside the untouched scalars `selectedPart`/
  `selectedInstance`, which stay "the primary" — `inspector`, `viewport`,
  `comments`, `presence.focus`, `actions.context()` and the shortcut `when`
  predicates all read the scalars and none of them changes. A plain click sets
  both; a modifier click grows the set and leaves the primary alone.
- **Row:** twist (folders), 24 px thumb `<img>` (parts; a neutral placeholder
  glyph while unbuilt/404), label, state dot (building/error; the existing
  dots), the existing badges (`ref`, `cfg`, presence, claim), and a `⋯` button
  that opens the context menu. The `×` delete button is **removed from the
  row** (it is in the context menu and the bulk bar; a 1k-row list with a
  delete button per row is a misclick farm) — `actions.deletePart` stays.
- **Filter box** pinned above the parts list (`/` focuses it when not in a
  field; `Esc` clears and returns focus to the tree); a live "n of N" count;
  the input is debounced 120 ms for the server free-text call and applies the
  client filter synchronously.
- **Drag-move** (HTML5 DnD on rows, `dragover` highlights folder rows and a
  root drop zone at the list head): dropping parts (the selection, if the
  dragged row is in it) on a folder → `bulk_part_op folder` (one undo step);
  a single part → `set_part_meta`. `Alt`-less: dropping on a *part* row means
  "that part's folder". Reordering *within* a folder is **not** a feature
  (parts stay in manifest order — the PRD's "drag-reorderable" is met by the
  folder move, and a manual order would be a fourth sort key nobody asked for;
  recorded as a ruling).
- **Collapse state** persists per project under
  `localStorage["agentcad.tree.<project>"]` = `{collapsed: [paths],
  emptyFolders: [paths]}` (`tree_model.persistTree/readTree` with clamping on
  read). **Folders are implicit** (a folder exists iff a part names it); "New
  folder…" creates an `emptyFolders` entry (shown until a part lands in it or
  the page reloads with it still empty — then it is dropped, and the user is
  told so in the dialog's help text).
- **Context menu** (`shell/contextmenu.js`, new PRD-026 primitive: `open({x,
  y, items:[{id, label, danger?, disabled?, run}]})`, `role="menu"`, arrow
  keys, Esc, outside-click, reuses the `.menu/.menu-item` styles; registered on
  the dialog overlay stack so `isModalOpen()` stays honest): Rename…, Tags…,
  Move to folder…, Export…, Delete… — each a `dialogs.*` form that calls the
  tool; with a multi-selection every verb applies to the selection.
- **Bulk bar** (`bulk.js`): a strip under the filter box visible when
  `selection.size > 1` — "6 selected · Material · Tag · Folder · Export ·
  Delete · ×"; each opens the same dialog as the context menu. **Per-item
  failures open a results dialog** (`view: "bulk-results"`, a table of
  id/status/error, non-modal) — not a toast per item (Risk 4).
- **Live updates (FR8):** `rebuild_finished` → state dot + thumb swap;
  `parts_meta_changed` → patch `state.project.parts[*].folder/tags` in place
  and re-render (no fetch); `project_changed` keeps its debounced refetch.

Rejected: a tree library (no dependencies by house rule); a DOM node per
row without virtualization (1k rows × 7 children = 7k nodes per render —
the current module rebuilds the whole list on every state change);
`content-visibility: auto` alone (keeps the DOM cost, only skips paint).

## 8. Security and hosting (Decision 8)

`tools_navigation.py` registers unconditionally and is member-only by
default-deny (the tool count moves **85 → 88** (88 → 91 with `[fem]`), a
string `tests/test_prd012_acceptance.py` asserts and that `docs/agent-api.md`,
`docs/architecture.md` (twice), `docs/user-guide.md`, `README.md` (twice) and
`AGENTS.md` repeat — seven coordinated edits in the docs slice); `routes_navigation.py` is **not** in `PUBLIC_PATHS`/
`PUBLIC_PREFIXES` (the equality test stays untouched). The thumb routes
serve bytes the member could already fetch as a mesh; the dashboard lists
only projects the store already lists. `bulk_part_op export` is bounded (≤ 50
ids) because each item is a kernel round trip; `search_parts` reads scripts
the service already owns and caps `limit` at 500. Pack load order:
`tools_navigation` sorts at `nav` — after `mat`/`mar`, before `pac`
(`tools_packages`'s `gate_providers` rule is untouched; this pack registers
no gate provider and reads `service.specs` inside handlers only).

## 9. Testing (Decision 9)

- Python: `test_search.py` (grammar incl. refusals, semantics per field,
  negation, phrases, ranking, `kind:package`, memo invalidation on script
  edit, the 1k-part latency AC with the shared fixture), `test_thumbnails.py`
  (key = cache key, LOD1 preference, warmer coalescing/drop/disable,
  on-demand render, 404 for unbuilt, ETag/304, immutable only on key match,
  `_TRIMMABLE` sweep, assembly composite + first-part fallback, no kernel
  call on the routes — asserted with a kernel spy), `test_tools_navigation.py`
  (the three tools: validation, normalization, events, partial success, one
  undo step (AC4), delete dependency refusal + force, export bounds),
  `test_routes_navigation.py` (search route, dashboard < 500 ms on 20
  synthetic projects, member-only via `flatten_routes`),
  `test_prd027_acceptance.py` (AC1 on a copy of the engine example — **33
  parts / 65 instances** on `main`, not the PRD's 32/63: folders + tags
  persist across a store re-open and `search_parts tag:fastener` returns
  exactly the tagged set; AC3 hash-keyed refresh; AC4; AC5's model half over a
  **manifest-only 1 000-part fixture that never builds** (the PRD-013
  synthetic is 1 000 *instances* of one part — one tree row); registry count;
  docs mention every new tool/route).
- Node-in-pytest (`test_frontend_navigation.py`): `query_model` parity with
  the shared fixture, `folderTree`/`filterRows` (bubbling, auto-expand,
  ordering), `selectionAfter`, `virtual_model.window`, persisted collapse
  clamping, context-menu markup a11y (`role="menu"`/`menuitem`).
- Browser (AC2/AC3/AC5 visual halves): a Playwright script against
  `agentcad serve` on a scratch port with a copied engine example and a
  generated 1k-part project — the filter latency, the live dot, the thumb
  swap after a script edit, and the scroll frame time; results quoted in the
  changelog with the command used.

## 10. Rulings ledger (made by the orchestrator, no human in the loop)

1. Phase 2 is in (server search + grammar, virtualization, bulk delete/move
   with dependency handling, grouped undo); Phase 3 (sub-assembly nesting,
   pattern member rows, 1k-instance certification) stays out.
2. Search is an on-demand scan with a stat-validated memo, not an inverted
   index; the 1k-part latency AC is the proof.
3. Thumbnails are keyed by the build cache key and live in `.cache/`;
   `rebuild_finished` gains `cache_key`; the warmer is a pre-warm and the
   route renders on demand from an existing mesh — never builds.
4. A bulk manifest op is one store RMW + one `project_changed` publish = one
   undo step; export is not a mutation and has no undo entry.
5. A claimed part (PRD-008 human claim) refuses the *whole* bulk op; per-item
   partial success covers validity errors only.
6. Folders are implicit metadata; no in-folder manual ordering; empty
   folders are a client-side pending entry.
7. The row's `×` delete button is replaced by the context menu / bulk bar.
8. The dashboard is kernel-free and never renders on the listing call;
   `mass_g` is `null` when any part is unbuilt rather than a partial sum.
9. Tags are lowercased on write; folders keep their case and match
   case-insensitively.
10. No new instance tool; instance `folder` rides the existing assembly
    write paths.
11. Context menu is a new shell primitive (`shell/contextmenu.js`) rather
    than a per-module popup.
