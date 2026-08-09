# 0034 — Light theme + toolbar light/dark switcher

- **Commit:** pending
- **Date:** 2026-08-09
- **Author:** Claude (goal session for Nikita Fedorov)

## Summary
Adds a light theme to the browser UI and a toolbar button to switch between
it and the existing dark "quiet graphite" look. The choice persists in
localStorage and is restored before first paint. The 3D viewport (scene
background, grid, B-rep edge overlay) and the CodeMirror editor follow the
theme; the drawing-preview checkerboard intentionally stays light in both
(it represents paper).

## Changes
- `app.css`: colors that were hardcoded per-theme now route through new
  `:root` tokens — CodeMirror syntax palette (`--syn-*`, `--code-*`),
  pop/modal shadows (`--shadow-pop`, `--shadow-modal`), viewport overlays
  (`--overlay`, `--overlay-strong`), modal scrim (`--scrim`), error prose
  and borders (`--err-text`, `--err-ring`), accent-tinted borders
  (`--accent-ring`), slider thumb (`--thumb`, `--thumb-hover`), swatch ring
  (`--swatch-ring`). A `:root[data-theme="light"]` block overrides the full
  token set with a paper palette; the amber accent and status colors deepen
  to hold WCAG AA on light ground. Bare `:root` (dark) stays the default.
  Minor consolidations while tokenizing: accent-tinted borders that were
  0.3/0.4/0.5 alpha unify on `--accent-ring` (0.4), the two error prose
  colors (`#d8b4b0` banner / `#eab6b1` toast) unify on `--err-text`, the
  row-delete hover tint reuses `--err-soft` (was a 0.15-alpha one-off), and
  the toast shadow reuses `--shadow-pop`.
- `theme.js` (new): owns theme state (`localStorage["agentcad.theme"]`,
  default dark), wires the toolbar button, sets
  `document.documentElement.dataset.theme`, and pushes the matching 3D
  palette into the viewport. Falls back to in-memory state when
  localStorage is unavailable.
- `viewport.js`: scene background, grid colors, and the shared edge
  material now come from a module-level `sceneTheme` palette; new
  `setTheme(colors)` export applies a palette live (grid rebuilds at its
  current size/divisions, which are now remembered). Safe to call before
  `init()`.
- `index.html`: pre-paint inline script restores a stored light theme
  before the stylesheet applies (no dark flash); new `#theme-btn` (☀/☾)
  in the toolbar next to the connection dot.
- `main.js`: `theme.init()` runs first in `boot()` so the scene is created
  with the stored palette.
- `tests/test_server.py::test_frontend_theme_assets`: smoke test that the
  served `index.html` contains the button and the pre-paint restore, that
  `app.css` contains the light-theme block, and that `theme.js` is served.
- `docs/user-guide.md`: toolbar section documents the switcher.
- Spec and plan under `docs/superpowers/specs|plans/2026-08-09-light-theme-*`.

## Files
- `frontend/css/app.css` — tokenization + light palette
- `frontend/js/theme.js` — new theme module
- `frontend/js/viewport.js` — themeable 3D palette, `setTheme` export
- `frontend/index.html` — pre-paint restore script, toolbar button
- `frontend/js/main.js` — boot wiring
- `tests/test_server.py` — theme asset smoke test
- `docs/user-guide.md` — toolbar docs
- `docs/superpowers/specs/2026-08-09-light-theme-design.md` — design spec
- `docs/superpowers/plans/2026-08-09-light-theme-implementation.md` — plan

## Notes
Verified in headless Chrome against a live server: default dark, toggle to
light, persistence across reload, ☾ glyph in light mode, toggle back, zero
console errors; screenshots of both themes with built geometry (1,796-tri
gusset plate) and of the light CodeMirror palette. `make test`: 139 passed,
1 skipped (FEM extra absent). Part colors and the amber selection emissive
are deliberately theme-independent; a "system" auto mode was considered
and dropped as YAGNI for a local single-user tool.
