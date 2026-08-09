# Light theme + switcher — implementation plan

Spec: `docs/superpowers/specs/2026-08-09-light-theme-design.md`

## Task 1 — CSS tokens + light palette (`frontend/css/app.css`)

1. Move hardcoded per-theme colors into `:root` tokens: `--syn-*`/`--code-*`
   (CodeMirror), `--shadow-pop`/`--shadow-modal`, `--overlay`/
   `--overlay-strong`, `--scrim`, `--err-text`, `--err-ring`,
   `--accent-ring`, `--thumb`/`--thumb-hover`, `--swatch-ring`. Replace the
   literal values at every use site.
2. Append a `:root[data-theme="light"]` block overriding all tokens with the
   paper palette (see spec). Dark stays the bare-`:root` default.

Verify: dark theme renders pixel-identical (spot-check in browser).

## Task 2 — switcher + scene theming

1. `frontend/js/theme.js`: `SCENE_THEMES` (dark/light 3D palettes), storage
   key `agentcad.theme`, `init()` + `toggle()`, button glyph/title update.
2. `frontend/js/viewport.js`: module-level `sceneTheme` (dark defaults),
   `setTheme(colors)` applying background + edge material + grid rebuild;
   `setGrid` reads `sceneTheme`; remember last grid size/divisions.
3. `frontend/index.html`: pre-paint inline script setting
   `document.documentElement.dataset.theme` from localStorage; `#theme-btn`
   in the toolbar before `#conn-dot`.
4. `frontend/js/main.js`: import + `theme.init()` during boot.

## Task 3 — tests

`tests/test_server.py::test_frontend_theme_assets`: `GET /` has
`id="theme-btn"`; `GET /css/app.css` has `[data-theme="light"]`;
`GET /js/theme.js` returns 200. Run `make test`, record the count.

## Task 4 — browser verification

`run` skill → drive http://127.0.0.1:8630 with Chrome tools: screenshot dark,
toggle, screenshot light, reload (light persists), toggle back; console clean.

## Task 5 — docs + changelog + commit 1

Update `docs/user-guide.md` (toolbar section + shortcut table row if any).
`docs/changelog/0034-light-theme-switcher.md`. Commit spec+plan+code+tests+
docs with the trailer.

## Task 6 — CI workflow + changelog + commit 2

`.github/workflows/test.yml` (apt mesa/X libs, setup-uv cache,
`uv sync --frozen`, `uv run pytest -q`, timeout 40 min).
`docs/changelog/0035-ci-test-workflow.md`. Commit.

## Task 7 — PR → green → merge

Push `light-ui`, `gh pr create` → watch `gh pr checks --watch` → squash-merge
(repo default) into `main`, confirm merged state.
