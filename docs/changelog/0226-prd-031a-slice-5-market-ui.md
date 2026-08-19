# 0226 — PRD-031a slice 5: the Market UI (browse + listing pages)

- **Commit:** pending
- **Date:** 2026-08-18
- **Author:** Nikita Fedorov

## Summary
The marketplace frontend: a full-page browse grid + search and per-listing pages
(metadata, previews, spec/param tables, provenance/license read-only, a versions
selector, the read-only script, and the PRD-007 customizer viewport with sliders
+ viewport + download), plus an authenticated "Add to library" button. It is
entered on the `#market` hash **before the auth gate**, so a logged-out hosted
visitor can browse, customize and download; add-to-library needs a session.

## Changes
- `frontend/js/market.js`: **new** self-contained controller. Browse
  (`api.marketList`/`marketSearch`, cards with license/disclosure/validated
  badges + preview thumbnails), listing (`api.marketPackage`/`marketVersion`,
  provenance block, versions/part selectors, read-only script), and the
  customizer — the slider panel + debounced rebuild + 429 degrade-to-view-only
  modelled on `share.js`, reusing `share-viewport.js` (`parseACM`/`showPart`/
  `fit`, the PRD-007 viewport) and the slice-4 mesh route. Add-to-library calls
  the existing authenticated `add_package` + `use_part`.
- `frontend/js/api.js`: a `market*` client — `marketList`, `marketSearch`,
  `marketPackage`, `marketVersion`, `marketParams`, `marketPreviewUrl`,
  `marketScript` (text), `marketVariant` (JSON), `marketMesh` (binary),
  `marketDownloadUrl`. No new authenticated methods — add-to-library reuses
  `addPackage`/`usePackagePart`.
- `frontend/index.html`: a `#market-view` container and a `Market` toolbar
  button.
- `frontend/js/main.js`: imports `market.js`; `boot()` enters the market view on
  the `#market` hash BEFORE the auth gate (anonymous browse), so the view takes
  the whole page and the viewport singleton is its alone; the Market button
  navigates via a full reload into `#market` (the "reload rather than re-run
  boot()" discipline).
- `frontend/css/app.css`: the `.mkt-*` styles, routed entirely through the
  existing workbench tokens so the market follows the theme.

## Files
- `agentcad/../frontend/js/market.js` — new controller
- `frontend/js/api.js` — the `market*` client
- `frontend/index.html` — `#market-view` + the Market button
- `frontend/js/main.js` — import + boot wiring + the button handler
- `frontend/css/app.css` — market styles

## Notes
- **Browser verification: graded as evidence.** `list_connected_browsers`
  returned `[]` (Chrome has been unavailable for many sessions), so the pages
  were **never rendered by a browser** and no slider was dragged in one. The API
  half is the machine-checked backstop (AC1–AC8 in
  `tests/test_prd031a_acceptance.py`), the JS is `node --check`-clean, and the
  view + its wiring + the routes it calls are asserted statically (AC9's contract
  half). The visual pass is deferred to a session with a browser; delete the
  `test_ac9_browser_half_is_recorded_as_unverified` assertion and update the PRD
  in the same commit when it lands.
- **No new anonymous surface.** The market view is served by the existing `/`
  (index.html) and the existing `/js`,`/css` static mounts; it adds **no** server
  route, so `EXPECTED_PUBLIC` is unchanged from slice 4.
