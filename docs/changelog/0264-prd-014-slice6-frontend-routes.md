# 0264 — 2026-08-19 — PRD-014 slice 6: drawing UI controls + the HTTP surface they need

## Summary

Slice 6 of Drawings v2 — the browser controls (sheet format, view subset,
section, PDF download) — plus the route fix that makes them actually take effect:
the SVG/PDF preview routes now forward the full drawing surface, not just
`config`/`dim_table`.

## Changes

- **`frontend/index.html`** — the `#drawing-modal` header grows a sheet-format
  `<select>` (9 formats, default `iso_a3`), a view-checkbox group (top/front/
  right/iso), a `Section…` mini-form (plane xy/xz/yz + offset, apply/clear), and
  a `Download PDF` button beside SVG/DXF.
- **`frontend/js/drawings.js`** — session-persisted `sheet`/`viewChecks`/
  `section` state, a `drawingArgs()` normalizer (`views` omitted when all/none
  checked; `sections` a one-element array), and `savePdf()` mirroring `saveDxf`.
  Every control reuses the existing `previewSeq` stale-response guard and the
  zero-extra-request `configOf` pattern.
- **`frontend/js/api.js`** — `drawingPdfUrl` (the PDF twin of `drawingSvgUrl`).
- **`frontend/css/app.css`** — `.modal-actions` wraps; small additive styles for
  the new controls (reusing `.tb-btn`'s box model).
- **`agentcad/server/routes_drawing.py`** — **the gap this slice exposed:** the
  SVG/PDF preview GET routes forwarded only `config`/`dim_table`, and the POST
  only `views`/`format`/`config`/`dim_table` — so the sheet select, view
  checkboxes, and section control sent correct requests the route silently
  dropped (and the GET step, which re-renders the shown bytes, ignored them).
  Both GET routes now accept `sheet`, `views` (comma-separated), `sections`/
  `details` (JSON, malformed → 422 via `_json_query`), `scale`, and `hole_table`;
  the POST forwards them from the body. One `_view_args` helper builds the tool
  call for both; `None` values are dropped so a bare `?config=` request is
  unchanged and the tool's own defaults apply.
- **`tests/test_drawings_sections.py`** — a route test
  (`test_the_get_routes_forward_sheet_views_and_sections`) asserting a different
  sheet + a section ride the GET through to the rendered bytes (`ansi_a` width,
  an `A-A` view), a malformed `sections` JSON is a 422, the PDF twin honors
  `sheet`, and a bare GET is unchanged.

## Notes

The detail control was deferred for v1 (sheet/views/section/PDF cover the
Experience); `savePdf`/`drawingPdfUrl`/the section JSON contract are written to
the full tool shape, so wiring a detail control later is JS-only. Browser
rendering is **evidence-graded** for now (verified via `TestClient` HTTP
contracts + `node --check`; a real-browser screenshot is the AC6 visual half —
graded as evidence where the extension is unavailable, per the 005a/031a
precedent). No OCP crosses into the server; the route change is pure request
plumbing.

`make test` — **4496 passed, 30 skipped** (clean run on the committed tree; the
full suite measured 4487 with the 9 `*_cites_a_make_test_count` guards reading
these entries' own counts before they were filled — green once they land; suite
grew 4486→4496 with slice 4's + this slice's tests).
