# 0217 — PRD-007 slice 5: the slim viewer, customizer, embed, and owner dialog

- **Commit:** pending
- **Date:** 2026-08-18
- **Author:** Nikita Fedorov

## Summary
The browser surface for share links: a self-contained `share.html` served at
`/s/<token>` and `/embed/<token>`, a slim `share.js` controller that reuses the
editor's WebGL viewport (`parseACM`/`showPart`) to render the pinned model and
drive `/variant` sliders (degrading to view-only on a 429), and an owner-side
"Share…" dialog + Links panel in the main app.

## Changes
- **New `frontend/share.html`** — the anonymous shell: an importmap for the
  vendored `three`, inline theme-aware CSS, a `#share-viewport` canvas host and a
  `#share-controls` panel. No external asset (CSP-clean); the embed page hides
  the chrome via a body class the controller sets from the path.
- **New `frontend/js/share-viewport.js`** — the explicit reuse seam:
  re-exports `init`/`showPart`/`fit`/`onFrame`/`setTheme`/`clear`/`parseACM`
  from `viewport.js`, and NOTHING editor-only (no `TransformControls` gizmo, no
  CodeMirror), so the stranger's bundle stays small (design Decision 7).
- **New `frontend/js/share.js`** — reads `/model` + `/params`, renders the
  viewport and a typed param panel (number/int→slider, bool→checkbox,
  enum→select, string→text with `max_len`), calls `/variant?<params>` on input
  with a local debounce, streams the returned mesh key from `/mesh/<key>`, and
  shows metrics + clamp warnings. A 429 degrades the panel to **view-only** with
  a retry banner; a 401 (login gate) and a 422 surface plainly. `Download <fmt>`
  navigates to `/download/<fmt>`. The token stays in the URL — it IS the
  shareable artifact (unlike the one-time enrolment token 005a strips).
- **New `frontend/js/share-links.js`** — the owner dialog (hosted-mode only):
  version picker, customizer toggle, export mask, show-script, expiry →
  `api.shareCreate`; the URL is shown **once** with a copy button; a Links panel
  lists live links with coarse counters and a revoke button.
- **`frontend/js/api.js`** — `shareCreate` / `shareList` / `shareRevoke`.
- **`frontend/index.html`** — a hidden `#share-btn` (unhidden in hosted mode)
  and a `#share-modal`. **`frontend/js/main.js`** imports `setupShare` and calls
  it in boot with the resolved `identity`. **`frontend/css/app.css`** — the
  share dialog styles.

## Files
- `frontend/share.html` — new: the self-contained anonymous shell
- `frontend/js/share.js` — new: the viewer/customizer controller
- `frontend/js/share-viewport.js` — new: the slim reuse of `viewport.js`
- `frontend/js/share-links.js` — new: the owner Share dialog + Links panel
- `frontend/js/api.js` — the three `share_*` calls
- `frontend/index.html` — the Share button + modal
- `frontend/js/main.js` — wires `setupShare(identity)`
- `frontend/css/app.css` — share dialog styles
- `tests/test_share_frontend.py` — new: the shell serves for both pages, is
  external-asset-free, reuses `parseACM`, and the owner bundle calls the API

## Notes
Verification: `pytest tests/test_share_frontend.py` → **4 passed** (2026-08-18);
`node --check` clean on the three new modules.

**Browser ACs graded as evidence (no Chrome extension available — the PRD-005a
AC3 precedent).** `mcp__claude-in-chrome__list_connected_browsers` returned `[]`,
so no visual pass was made; the criteria are NOT weakened, they are graded on the
same evidence 005a used:
- **AC1** (a logged-out visitor opens the viewer, no auth cookie on any
  response): `tests/test_share_viewer.py::test_the_page_opens_for_a_logged_out_visitor`
  and `test_share_frontend.py::test_the_share_shell_is_served_for_both_pages`
  assert the 200 shell with **no `Set-Cookie`**; `/model` renders metrics +
  attribution with zero kernel calls.
- **AC10** (the embed frames on a second origin): the embed response carries
  `Content-Security-Policy: frame-ancestors *`
  (`test_share_viewer.py::test_the_embed_page_is_framable_and_the_main_app_is_not`),
  and the main app carries `frame-ancestors 'none'` — so a second-origin iframe
  is permitted by construction while the authenticated app refuses to frame.
