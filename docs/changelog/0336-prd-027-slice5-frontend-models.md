# 0336 — 2026-08-23 — PRD-027 slice 5: `query_model` (the parity port), folder tree / filter / selection models, `virtual_model`, the context-menu primitive

- **Commit:** pending
- **Date:** 2026-08-23
- **Author:** Nikita Fedorov (orchestrated; Claude)

## Summary

The pure, node-tested half of the new sidebar: a byte-equivalent port of the
search grammar and matcher, the folder tree / filter / multi-selection /
persisted-collapse models, a virtual-window model, and a context-menu shell
primitive. No DOM module changes yet (slice 6). Design §2 (client half), §7.
Landed in one commit with slice 4 (0335).

## Changes

- **`frontend/js/query_model.js` (new)** — `parse`, `matches(part, query,
  {scriptText})`, `hasFreeText`, `asQuery` (a string is parsed, `null` is the
  empty query, anything else throws — never a silent match-all), `segments`,
  `isFolderPath`, `folderMatches`; the same `SOURCES` order, `RANKS`,
  `NO_EVIDENCE_RANK`, `scriptOnly` and `(rank, manifest index)` sort as
  `core/search.py`, including the refusals and their exact message strings.
  Whitespace is an explicit class (a `\s`-based tokenizer diverges from Python
  on U+FEFF and U+001C). **Parity** is driven through every case of
  `tests/fixtures/search_queries.json` exactly as `test_search.py` drives the
  Python half; the reviewer additionally probed 30 adversarial inputs (lone
  `-`, `-"quoted"`, `tag:` at end, internal quotes, `folder:/`, `http://x`,
  `C:/tmp`, Turkish/Greek/German case folds, BOM/C0 prefixes) — all agree
  byte-for-byte with Python, error strings included.
- **`frontend/js/tree_model.js`** — the three PRD-013 exports are
  byte-identical; added `folderTree(parts, {collapsed, emptyFolders})`
  (folders first, case-insensitive alpha, parts in manifest order, collapsed
  descendants omitted, empty folders at count 0), `filterRows(parts, query,
  {ids, scripts})` (a hit pulls its ancestors into view **forced open**;
  folders with no hit dropped; the server's `ids` are **unioned** with the
  client match and server-only rows carry `matchedOn: ["script"]` so the DOM
  can badge them — the first draft intersected, which would have rendered
  zero rows for every free-text search; an empty query is not a filter and
  ignores `ids`, so clearing the box restores the whole tree),
  `instanceTree`, `selectionAfter(current, anchor, visibleIds, clickedId,
  {shift, meta})` (plain = single, Cmd toggles, Shift ranges over the
  **visible** order, Shift without an anchor = click), `persistTree`/
  `readTree` (total over malformed JSON; valid folder paths only; ≤ 500).
  Total over a non-array parts list.
- **`frontend/js/virtual_model.js` (new)** — `window({scrollTop,
  viewportHeight, rowHeight, total, overscan=8}) → {start, end, padTop,
  padBottom}`; pads + window always sum to the full height; clamps at the
  end; degenerate inputs return an empty window, never `NaN`. Import it
  namespaced (`import * as virtual`) — a named `window` import shadows the
  browser global.
- **`frontend/js/shell/contextmenu.js` (new)** — `init(hostEl)`, `open({x,
  y, items, label}) → Promise`, `close()`, `isOpen()`, pure `markup(items)`
  (`role="menu"`, `role="menuitem"`, roving `tabindex="-1"`, `aria-disabled`,
  danger class); ↑/↓/Home/End/Enter/Esc, **Tab closes** (WAI-ARIA menu),
  outside click/scroll/resize close, viewport flip. A verb's failure is never
  lost: `runItem` reports a sync throw and an async rejection alike to the
  shell toast + `console.error`. **Design ruling:** the menu is *not* an
  entry on the dialogs overlay stack — `attachLegacy` hard-codes
  `modal: true` (which would switch off every global shortcut and the
  sketcher while a menu is open) and also stamps agent attribution and emits
  `dialog_opened` per open — so it owns Esc through a `window`-capture
  keydown listener installed only while open (the `palette.js` precedent;
  capture runs window → document, before `dialogs.js`'s listener). The one
  divergence from `escOwner` (Esc taken unconditionally rather than only
  while focus is inside) and the caller contract (focus the row before
  `open()` so focus restores somewhere useful) are in the module header.

## Files

- `frontend/js/query_model.js`, `frontend/js/virtual_model.js`, `frontend/js/shell/contextmenu.js`, `tests/test_frontend_navigation.py` (116 node-in-pytest tests) — new
- `frontend/js/tree_model.js`, `tests/test_frontend_tree.py` (docstring) — as above

## Notes

Review (Opus) found the union/intersection defect, the sync-only catch and the
string-query match-all; all fixed and re-reviewed clean. Deferred minors: a
mouse-invoked menu restores focus to `<body>` unless the caller focuses the
row first (documented contract); report count slips.

`make test` — **5891 passed, 50 skipped, 2 failed** (20m29s on this tree
with slices 4 and 5 together, under a concurrent full-suite run from another
worktree). The two reds are `test_supervisor.py::test_a_ballooning_script_…`
and `…::test_a_breach_on_one_pool_worker_…` (PRD-006's RSS supervisor): they
fail identically, in isolation, on a sibling worktree that carries **no
PRD-027 code** (`fea3105`, the PRD-017 branch) — a machine-state flake of
the measured-baseline memory cap on this Mac today, not this branch. Every
PRD-027 test file is green; CI is the arbiter for the supervisor pair. An
earlier run of the same tree before slice 5's fix round measured 5888 passed.
