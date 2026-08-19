# 0275 — PRD-028 slice 5: the materials database browser (frontend)

- **Commit:** pending
- **Date:** 2026-08-20
- **Author:** Nikita Fedorov (orchestrated with Claude)

## Summary
The frontend half of FR7 (Decision 10): a materials database browser —
tree/filters/sortable table/compare/detail — reachable from a `Materials`
toolbar button, the inspector's material block ("Browse…", assign mode), and
the `#materials` hash. Per this slice's ruling it is a **modal inside the
workbench** (`.modal-overlay`/`.modal` chrome, the library/proposals/configs
pattern), not the design spec §10's `#market`-style full-page takeover —
assign mode needs the workbench, and the part being assigned to, alive
underneath it.

## Changes
- `frontend/js/materials_model.js` (new) — pure data model, no DOM/imports
  (same discipline as `tree_model.js`): `filterToQuery` (UI filter snapshot ->
  `{category?, subcategory?, filter}`, numeric fields -> `_min`/`_max` keys,
  blank/non-finite omitted), `treeCounts` (summary rows -> category/subcategory
  counts), `compareRows` (full records + property keys -> side-by-side
  columns, ranges as `lo–hi`, missing as `—`), `basisBadge` (typical/minimum/
  characteristic, anything else incl. `null` -> `uncited`), `formatProperty`
  (value/range + unit, `T_c` shown only when ≠ 20), `sortRows` (stable,
  numbers or strings, missing always last regardless of direction). Copies
  `PROPERTY_UNITS`/labels and the `process` key vocabulary from
  `agentcad/core/materials.py`/`materials_query.py` rather than inventing a
  second source of truth.
- `frontend/js/materials.js` (new) — the modal view. Tree (left, counts read
  from ONE unfiltered `GET /api/materials` per open — not re-fetched per
  filter change, so "one GET per filter change" stays literally true); filter
  bar (min/max density, min E, min yield, min max-service-temp, max cost —
  debounced 250 ms; process chips and the basis select are discrete, refresh
  immediately; tree clicks too); a sortable table (click a `<button>` inside
  each `<th scope=col>`, a checkbox pins up to 4 rows); a Compare toggle that
  swaps the table for `compareRows`' side-by-side view; a detail pane (label/
  condition/standards, every property with its basis badge — uncited wins
  over a real basis when the property carries no source — the source text,
  a per-property temperature table when present, the process block as
  chips, `links` as `<a target=_blank rel=noopener>`, record `warnings`, the
  `caveat`, and a **Use for `<part>`** button in assign mode). Esc closes,
  backdrop click closes, `role="dialog"`/`aria-label` on the modal, initial
  focus on Close — the same shape `library.js`'s Escape/backdrop/initial-focus
  wiring uses (no modal in this app implements a real Tab focus trap; this one
  doesn't either, on purpose, to stay consistent).
- `frontend/js/api.js` — `listMaterials(proj, opts)`: `opts` is optional and
  `listMaterials(proj)` alone is byte-compatible with every existing caller;
  `opts.filter` is JSON-encoded and omitted entirely when it has no keys (an
  empty constraint object and no `filter` param mean the same thing
  server-side). New `getMaterial(id, proj)` and `findMaterials(body)`.
- `frontend/js/inspector.js` — a **Browse…** button in `materialBlock`
  (`.mat-browse`), assign mode's entry point, calling
  `actions.openMaterials({assignTo: part.id})`. `setMaterial` (the `<select>`'s
  change handler) is refactored, not rewritten, into an exported
  `setPartMaterial(partId, id)` that both the `<select>` and the modal's "Use
  for part" button call — the identical `api.updatePart` + state-update path
  (`state.part`, the sidebar's project entry, `applyRebuildResult`, the toast).
- `frontend/js/main.js` — imports `materials.js`; `actions.openMaterials`/
  `actions.assignMaterial` route inspector.js <-> materials.js through the
  shared actions object rather than a direct cross-import (the same idiom
  `openProposal` already uses for comments.js -> proposals.js — inspector.js
  and materials.js never import each other). `materials.init(actions)` beside
  the other panel inits; a `materials-btn` click opens the modal (no
  navigation, unlike `market-btn`'s hash + reload); the `#materials` hash
  opens the modal at the END of `boot()`, after the initial project load
  settles (unlike `#market`, which is entered BEFORE the auth gate and owns
  the whole page, `#materials` is an overlay on the workbench `boot()` just
  built).
- `frontend/index.html` — the `Materials` toolbar button next to `Market`;
  the `#materials-modal` skeleton (tree/filter-bar/table-wrap/compare
  containers as static markup, `role="dialog"` `aria-modal` `aria-label`) on
  the library/proposals/configs `.modal-overlay` pattern.
- `frontend/css/app.css` — a new `mat-*`-prefixed block (tree rows, filter
  inputs, chips, sortable table, badges incl. `mat-badge-{typical,minimum,
  characteristic,uncited}`, detail pane, `mat-use`/`mat-browse` buttons) plus
  `.mat-browse` for the inspector's new button — every color an existing
  token, no new ones; the inspector's own `.mat-block`/`.mat-head`/… classes
  are untouched (this slice's classes are a disjoint set, chosen to avoid
  colliding with them).
- `tests/test_frontend_materials.py` (new) — a node harness over
  `materials_model.js` (same shape as `test_frontend_tree.py`, skipped when
  `node` is missing): `filterToQuery` (min/max + process/basis keys, blank/
  non-finite omission, empty input), `treeCounts` (grouping, empty), `compareRows`
  (ranges, missing, zero records), `basisBadge` (all three known bases, plus
  `null`/`"uncited"`/an unknown string), `sortRows` (numeric asc/desc, missing
  always last both directions, stability on ties). Plus two Python tests
  against the real HTTP surface (`_local_client`, the same fixture shape as
  `test_materials_tools.py`): `GET /api/materials?category=&filter=<json
  yield_mpa_min>` returns only qualifying rows, `GET /api/materials/{id}`
  carries `properties`.
- `docs/user-guide.md` — a "Materials browser" subsection (opening it, the
  tree, filters, sortable table, compare, detail badges, assign mode) between
  "Browsing the catalog (the Marketplace)" and "Working with the bundled
  examples". Re-read the file immediately before editing; no other section
  touched.

## Files
- `frontend/js/materials_model.js` — new (pure model).
- `frontend/js/materials.js` — new (the modal view).
- `frontend/js/api.js` — `listMaterials` extended, `getMaterial`/
  `findMaterials` added.
- `frontend/js/inspector.js` — Browse… button; `setMaterial` refactored into
  exported `setPartMaterial`.
- `frontend/js/main.js` — `materials.js` import, `actions.openMaterials`/
  `assignMaterial`, `materials.init`, `materials-btn` handler, `#materials`
  hash-open.
- `frontend/index.html` — `Materials` toolbar button, `#materials-modal`.
- `frontend/css/app.css` — the materials-browser CSS block, `.mat-browse`.
- `tests/test_frontend_materials.py` — new.
- `docs/user-guide.md` — new "Materials browser" subsection.

## Verification
- `.venv/bin/python -m pytest -q tests/test_frontend_materials.py
  tests/test_frontend_tree.py tests/test_materials_tools.py` → **40 passed**
  (18 new node-model tests + 2 new live-HTTP tests in
  `test_frontend_materials.py`, 5 unchanged `test_frontend_tree.py`, 15
  unchanged `test_materials_tools.py`).
- `node --check` on every touched/added `.js` file: clean.
- HTML sanity: no duplicate ids in `frontend/index.html`; a stack-based
  tag-balance check (Python's `html.parser`) reports zero unclosed/mismatched
  tags.
- **Real-browser verification**: the Claude-in-Chrome extension was NOT
  connected in this environment (`tabs_context_mcp` reported "Browser
  extension is not connected"), so per the brief's fallback this was
  evidence-graded instead:
  - Started `.venv/bin/agentcad serve --port 8639 --projects-dir <scratch>`
    (never `uv run`), created a scratch project + part (material `al6061`),
    drove the three contract routes with `curl`: `GET /api/materials?category=
    metal&filter={"yield_mpa_min":200}` (117/117 rows all `category=metal` and
    `yield_mpa>=200`), `GET /api/materials/al6061` (9 properties present),
    `POST /api/materials/find` (200, cited constraining evidence). Confirmed
    `GET /` serves the new toolbar button and modal markup, and that
    `/js/materials.js`/`/js/materials_model.js` are byte-identical to the
    files in this diff (no build/transform step for this static frontend).
  - Additionally imported the REAL, unmodified `frontend/js/api.js` and
    `frontend/js/materials_model.js` into node (stubbing only `localStorage`/
    `window`, and forwarding `fetch` to the live server) and called
    `api.listMaterials` (both the legacy no-arg form and with
    `materials_model.filterToQuery`'s output), `api.getMaterial` and
    `api.findMaterials` — the exact code path the browser would run,
    round-tripping against the live server and matching the `curl` evidence
    (117 metal/yield≥200 rows both ways). Killed the server and confirmed
    port 8639 is free afterward.
- Last full `make test`-equivalent run on this branch (entry 0271): 4565
  passed, 44 skipped; the controller's full-suite run over the finished
  branch is cited in the close-out entry.

## Notes
**Found, not fixed (out of scope — `agentcad/core/materials_query.py` is a
concurrent agent's file):** every `process` filter value except `"sheet"`
currently matches **zero** materials, in this slice's own live-server
testing and confirmed in-process. `_process_ok`'s `isinstance(node, dict)`
checks are always `False` for `Material.process`, which is a
`types.MappingProxyType` (not a `dict` subclass) — so `cnc`/`weld`/`fdm`/
`sla`/`sls`/`mjf`/`dmls`/`im`/`casting` all short-circuit to "no match" while
`sheet` (which reads `process.get("sheet")` directly, no `isinstance` check)
works. The frontend's process-chip filter is wired correctly to the
documented grammar (`materials_model.filterToQuery` -> `filter.process`,
proven against the live route) and needs no change when this is fixed
upstream — the chip UI will start working the moment `_process_ok` does.
Flagged here rather than touching `materials_query.py`, per this slice's
scope — **fixed in this same commit by the orchestrator**: `_process_ok` now
tests `collections.abc.Mapping`, and
`tests/test_materials_query.py::test_process_filter_works_on_the_real_catalog_not_only_on_dict_doubles`
pins `cnc` → `al6061` and `sls` → `nylon_pa12` against the shipped library.

The detail pane shows exactly one badge per property: `uncited` when the
property has no `source` (from the record's `uncited` list), otherwise the
real basis. This is a deliberate design choice for `basisBadge`'s two-value
contract (not literally specified by the brief beyond "typical/minimum/
characteristic; uncited -> uncited") — an uncited number's missing source is
judged the more important thing to surface than which basis it claims,
since the basis can't be verified without one either.

Tree category/subcategory counts are read once per modal open (an unfiltered
`GET /api/materials`) rather than recomputed per filter change — keeps
"debounced 250 ms -> one GET per change" literally true for the table's own
query, at the cost of the tree not narrowing itself as a facet count when
other filters are applied (it stays a fixed map of the whole catalog; only
the table narrows).
