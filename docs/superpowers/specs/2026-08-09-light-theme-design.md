# Light theme + theme switcher — design

**Date:** 2026-08-09 · **Status:** approved (autonomous goal session) · **Scope:** frontend only + CI bootstrap

## Goal

Give the browser UI a light theme to complement the existing dark "quiet
graphite" look, with a one-click switcher in the toolbar that persists across
sessions. Additionally bootstrap a GitHub Actions test workflow so pull
requests have a check to gate on (the repo currently has none).

## Approaches considered

1. **CSS custom-property override block keyed by a `data-theme` attribute**
   (chosen). `app.css` already routes nearly every color through `:root`
   tokens; a `:root[data-theme="light"]` block redefines them. One source of
   truth, no extra HTTP round trip, no flash on toggle.
2. A second stylesheet swapped at runtime — rejected: splits the palette from
   its usage, risks FOUC on swap, more files for no benefit.
3. `prefers-color-scheme` media query only — rejected: the user explicitly
   wants a manual switcher. A third "system" mode is YAGNI for a local
   single-user tool; two states with a sticky choice is simpler.

## Design

### CSS (frontend/css/app.css)

- **Tokenize the stragglers.** Colors currently hardcoded that must differ per
  theme move into `:root` tokens: CodeMirror syntax palette (`--syn-*`,
  `--code-*`), pop/modal shadows (`--shadow-pop`, `--shadow-modal`), the
  translucent HUD/placement overlays (`--overlay`, `--overlay-strong`), the
  modal scrim (`--scrim`), error prose (`--err-text`) and error borders
  (`--err-ring`), accent-tinted borders (`--accent-ring`), the slider thumb
  (`--thumb`, `--thumb-hover`), and the color-swatch ring (`--swatch-ring`).
  The two slightly-different error prose colors (banner body, error toast)
  unify onto `--err-text`.
- **Add `:root[data-theme="light"]`** overriding every token. Palette is the
  same design language mirrored: paper greys (`--panel #f5f6f8`, wells
  `#e9ebef`, raised `#ffffff`), darkened amber accent for AA contrast on
  light ground, darker red/green status colors. The drawing-preview
  checkerboard stays light in both themes (it represents paper).
- The dark palette stays on bare `:root` — absent/unknown attribute values
  keep today's exact look.

### Theme module (frontend/js/theme.js)

Owns the theme state. `localStorage["agentcad.theme"]` ∈ {"dark","light"},
default dark. Exposes `init()` (wires the toolbar button, applies the stored
theme) and `toggle()`. Applies the choice by setting
`document.documentElement.dataset.theme` and calling `viewport.setTheme(...)`
with the matching 3D scene palette (background, grid major/minor, edge
color). A 3-line inline script in `<head>` sets the attribute from
localStorage before first paint so a stored light theme never flashes dark.

### Viewport (frontend/js/viewport.js)

The scene hardcodes background `0x17181b`, grid `0x2c2f36`/`0x22242a`, and
edge lines `0x0d0e10`. New `setTheme(colors)` stores the palette in module
state and applies it to the live scene (background, shared edge material,
grid rebuild at the current size/divisions). `init()` reads the stored
palette so boot order doesn't matter. Lights, part colors, and the amber
selection emissive are theme-independent.

### Toolbar (frontend/index.html)

A `#theme-btn` icon button next to the connection dot: shows ☀ in dark mode
("switch to light"), ☾ in light mode, with a matching `title`/`aria-label`.

### Tests

Frontend has no JS test rig (static ES modules, no bundler), so coverage is a
server-level smoke test in `tests/test_server.py`: `GET /` contains
`id="theme-btn"`, `GET /css/app.css` contains the `[data-theme="light"]`
block, `GET /js/theme.js` serves the module. Real-browser verification
(screenshots of both themes, zero console errors, persistence across reload)
is part of the definition of done.

### CI bootstrap (.github/workflows/test.yml)

`pull_request` + `push` on main: ubuntu-latest, apt install of the mesa/X
libs OCP needs headlessly, `astral-sh/setup-uv` with caching, `uv sync
--frozen`, `uv run pytest -q`. The FEM extra stays uninstalled — the suite is
designed to skip those. Separate commit with its own changelog entry.

## Error handling

- localStorage unavailable (private mode): fall back to in-memory default
  dark; toggling still works for the session.
- Unknown stored value: treated as dark.

## Out of scope

Per-theme part colors, a "system" auto mode, theming the drawing SVG output,
favicon variants.
