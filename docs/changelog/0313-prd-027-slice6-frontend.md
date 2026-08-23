# 0313 — 2026-08-23 — PRD-027 slice 6: the virtualized folder tree, filter box, multi-select, context menu, bulk bar, dashboard

- **Commit:** pending
- **Date:** 2026-08-23
- **Author:** Nikita Fedorov (orchestrated; Claude)

## Summary

The sidebar becomes a real tree (FR2/FR7/FR8, G1/G3/G5/G6/G7): folders with
persisted collapse state, 24 px thumbnails and state dots on every row, a
pinned type-to-filter box with the shared query language, Finder-style
multi-selection, drag-to-organize, a context menu, a bulk-action bar, and a
project dashboard that replaces the bare switcher as the entry experience.
Verified in a real browser (Chrome via Playwright, SwiftShader). Design §6/§7.

## Changes

- **`frontend/js/tree.js` (rewritten on the slice-5 models)** — one `<ul
  role="tree">` per section with top/bottom spacers; fixed 28 px rows; the
  window from `virtual_model.window` re-rendered on `scroll` through one
  `requestAnimationFrame` (43 `<li>` for 1 009 rows). Row = twist (folders,
  PRD-013 groups) · `<img class="row-thumb" loading="lazy">` from
  `thumb_key` with a placeholder glyph · label · `script`/`ref`/`cfg` badges ·
  claim/presence chips · state dot · `⋯`. The row `×` delete button is gone
  (ruling 7). Keyboard: roving `tabIndex`, ↑/↓/Home/End, ←/→ collapse/expand,
  Enter, Space toggles selection, the ContextMenu key; `aria-level`,
  `aria-expanded`, `aria-selected`, sibling-relative `aria-setsize`/
  `aria-posinset`. Focus restore after a repaint uses `{preventScroll:
  true}` and only returns to the row that had it — review caught the first
  draft's bare `.focus()` tugging the list back toward an overscan row on
  every scroll once any row was focused (proven gone in the browser:
  `scrollTop` 2800 stays 2800 across three repaints).
- **Filter** (`/` focuses it outside a field; Esc clears and refocuses the
  tree): `filterRows` synchronously; a query with free text also calls
  `GET …/search` debounced 120 ms with a sequence guard and **unions** the
  ids (server-only rows carry a `script` badge and the snippet as a title);
  "n of N"; a grammar refusal shows under the box instead of blanking the
  list and survives unrelated repaints; clearing restores the persisted
  collapse; scroll returns to the top on a query change; a project switch
  clears the query, the server ids and the snippets.
- **Selection**: `state.selection` (Set) + `selectionAnchor` through
  `selectionAfter`; the scalars `selectedPart`/`selectedInstance` are
  untouched. **Drag-move**: the selection travels when the dragged row is in
  it; folder rows, part rows (= that part's folder), a root drop zone;
  ≥ 2 parts → one `bulk_part_op folder` (one undo step), one → `set_part_meta`,
  instances → the gizmo PATCH with `folder`; a no-op move writes nothing.
- **Context menu** (`shell/contextmenu.js`): Rename…, Tags…, Move to
  folder…, New folder… (client-side `emptyFolders`), Export…, Delete… — every
  verb applies to the selection with its count in the label; the row is
  focused before `open()`.
- **`frontend/js/bulk.js` (new)** — the strip at `selection.size > 1`:
  "N selected · Material · Tags · Folder · Export · Delete · ×"; the verbs
  are the same functions the context menu uses (one implementation each);
  per-item failures open the non-modal `bulk-results` dialog (a DOM table of
  id/status/error); a clean run is one toast carrying the undo label; a tool
  refusal and a thrown `ApiError` both toast once. `part.bulk.*` actions
  read `ctx.selectionSize` (one line added to `actions.context()`).
- **`frontend/js/dashboard.js` (new)** — a full-pane `#dashboard` view (not
  modal — shortcuts keep working) with a card grid from `GET /api/dashboard`:
  hero `<img loading="lazy">` or placeholder, name, `N parts · M instances`,
  mass or "—", relative time, a red `n failing` badge (exercised in the
  browser: "1 failing"); New project / Open by path cards run the existing
  PRD-026 actions. Opened on first run (no or stale `agentcad.project`), by
  `project.dashboard` (`Mod+Shift+O`, File menu, palette), the project
  menu's "All projects…", and as dialog view `dashboard` (so `ui_open
  {view: "dashboard"}` works); Esc closes it while focus is in the pane.
- **Live updates**: `rebuild_finished.cache_key` swaps the row's thumb
  (no refetch); `parts_meta_changed` patches `state.project.parts` in place
  for this client's own writes and falls back to the debounced refetch for a
  remote change (the event carries ids and fields, not values — recorded);
  `project_changed` keeps its debounced refetch.
- **`api.js`**: `searchParts`, `setPartMeta`, `bulkPartOp`, `dashboard`,
  `partThumbUrl`, `projectThumbUrl`, `patchInstance` with `folder`;
  **`state.js`**: `selection`, `selectionAnchor`, `treeFilter`,
  `dashboardOpen`; **`index.html`**: filter box, bulk-bar host, dashboard
  pane, context-menu host; **`app.css`**: `.tree-*`, `.row-thumb`,
  `.bulk-bar`, `.dash-*`, `.ctx-menu` — token colours only, light theme
  covered, `prefers-reduced-motion` drops the one transition.
- `docs/user-guide.md`: the two new chords (`/`, `Mod+Shift+O`) in the
  shortcut table (PRD-026's chord-table test requires it) and the
  dashboard's Esc scope; `tests/test_prd026_acceptance.py`'s chord list.

## Files

- `frontend/js/tree.js` (rewrite), `frontend/js/bulk.js`, `frontend/js/dashboard.js` (new), `frontend/js/{main,api,state}.js`, `frontend/js/shell/actions.js` (one line), `frontend/index.html`, `frontend/css/app.css`, `docs/user-guide.md`, `tests/test_prd026_acceptance.py`, `tests/test_frontend_navigation.py` (+22 DOM-module tests: markup helpers, result-row totality, sibling annotation, no-op move, 28 px row contract in JS and CSS)

## Notes

**Browser evidence** (Chrome channel via Playwright, `--use-angle=swiftshader`,
a scratch copy of `examples/engine` + a generated 1 000-part project; commands
and artifacts in the slice report): typing `err` → one row (`err_bracket`),
"1 of 34", **1.7 ms** around the input event (AC2); fixing the script → the
error dot clears with no reload; a built part's `row-thumb` has
`naturalWidth === 192` (AC3's visible half); the bulk bar shows "3 selected"
with the five verbs; the dashboard shows 8 cards and the failing badge.
**AC5, honestly:** 1 009 rows render **43 `<li>`** (the window bound); the
tree's own rAF-callback CPU is **0.49 ms/frame** (median of 5×50, stable
under load); wall-clock per scroll step with a row focused: **14.5 ms mean /
16.6 median / p95 24.9** with the WebGL viewport out of the loop, **26.0 /
25.0 / 34.3 ms** with the viewport re-rendering through SwiftShader (machine
load 9.6–11 from concurrent sessions). The 16.7 ms bar is met without the
software-rendered viewport and not with it; no 60 fps claim is made — not
measured on hardware GL. The virtualization itself is proven; the viewport's
per-frame cost is PRD-013/viewport territory.

Deferred for the final review: `parts_meta_changed` carries no values (remote
changes refetch); a stale `agentcad.project` opens the dashboard rather than
`projects[0]` (arguably better); the sidebar's two panes scroll separately;
the 50-id export cap is not pre-checked client-side.

`make test` — **5922 passed, 50 skipped** (11m16s on this tree; the run measured 5912 + the 10 self-referential count-guard tests that were red only on this entry's placeholder). The two `test_supervisor` memory-cap cases that failed on earlier runs today (an environmental flake reproduced on a PRD-017 tree) passed this run.
