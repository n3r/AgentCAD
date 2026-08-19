# 0273 — PRD-026 slice 4: the menu bar and the layout manager

- **Commit:** pending
- **Date:** 2026-08-20
- **Author:** Nikita Fedorov (with Claude)

## Summary

PRD-026 spec §4–§5: a File · Edit · View · Model · Help menu bar generated
from the action registry (every row is the same action object the palette and
shortcuts use — no second command list), and resizable/collapsible sidebar,
inspector and chat dock with per-workspace persistence and keyboard toggles.

## Changes

- **`shell/menu_model.js` (pure)** — `tree(actionList, platform)` (fixed
  menu order, rows sorted by the numeric part of `menu:`, `separatorBefore`
  when the gap from the previous order is ≥ 10, empty menus omitted,
  shortcut labels via `shortcuts_model.label`), `markup(tree)`
  (`.menu-wrap` children with `role="menu"`/`menuitem`, `aria-disabled`,
  `aria-keyshortcuts`, escaped titles; the single `role="menubar"` lives on
  the static `<nav id="menubar">` host).
- **`shell/menu.js`** — renders `#menubar`, re-renders on `actions.onChange`
  and on open (so `enabled`/`when` are fresh); absorbs `main.js`'s
  `setupMenus()` as `attach(wrapEl)` with the shared listeners installed once
  and `.menu-wrap` queried live (the old "snapshot at boot" caveat is gone —
  the project/branch/export dropdowns keep working through it); `←/→` between
  menubar menus only, `↑/↓` roving, Enter/Space runs via `actions.run(id, ctx,
  {source: "menu"})`, Esc/outside-click close and **reset the open-menu
  state** (the task review caught a second click being needed to reopen a
  menu after running a row); the wrap set is pruned of disconnected nodes.
- **`shell/layout_model.js` (pure)** — `LIMITS` (sidebar 160–480/216,
  inspector 240–640/326, chat 120–60 vh/264), `clamp` (`null`/`NaN` → the
  default, never the minimum), `serialize`/`deserialize` (unknown keys
  dropped, clamped on read), `toggle`, `responsiveDefaults(width)` (1100 /
  800), `key(workspace)` → `agentcad.layout.<workspace>`.
- **`shell/layout.js`** — `.resize-handle` separators (`role="separator"`,
  `aria-orientation`, `aria-valuenow/min/max`, `tabindex=0`,
  `aria-controls`), pointer drag with `setPointerCapture` (guarded), 16 px
  keyboard nudges, double-click/Enter collapse (inline size cleared so the
  `.collapsed` CSS owns the geometry; expand restores the persisted size),
  persistence on change, responsive auto-collapse applied but never
  persisted, the three actions `view.sidebar.toggle` (`Mod+B`, view/30),
  `view.inspector.toggle` (`Shift+Mod+B`, view/31), `view.chat.toggle`
  (`Mod+J`, view/32); `agentcad.chat.open` migrated once **and persisted
  immediately** (the review caught the preference being lost on the next
  reload); `chat.js`'s collapse button goes through `layout.toggle("chat")`.
- **`index.html`** — `<nav id="menubar" role="menubar">` inside `#toolbar`
  after the brand. **`app.css`** — `#menubar`, `.menu-kbd`, `.resize-handle`
  (+ hover/focus/dragging), `#sidebar.collapsed`/`#inspector.collapsed`.

## Files

- `frontend/js/shell/{menu_model,menu,layout_model,layout}.js` — new
- `frontend/js/{main,chat}.js`, `frontend/index.html`, `frontend/css/app.css`
- `tests/test_frontend_shell.py` — menu tree/markup a11y, layout model,
  and DOM-stub tests for the reopen bug and the migration persistence

## Notes

Reviewed (Sonnet task review → Needs fixes for the two criticals above; fix
round 1 → scoped re-review). Not verified in a browser (no Chrome reachable):
drag, nudge, collapse, roving focus and both themes are evidence-graded on the
node tests that drive the shipped modules against a DOM stub.

`make test` — **4750 passed, 44 skipped** on the combined slice 3 + slice 4 tree (the run reported `12 failed, 4741 passed`: nine `*_count_is_cited` guards read the newest changelog before its count was filled, `test_checks_pipeline` asserts a clean tree while the slices were uncommitted, `test_sketch_diagnostics::test_the_full_budget_completes_the_same_analysis` passed on an isolated re-run, and `test_checks_cli`'s 1 ms `--budget` race lost again at load average 17–65 — CI is authoritative).
