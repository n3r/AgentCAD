# 0257 — PRD-014 Drawings v2, Slice 2: deterministic PDF backend

- **Commit:** pending
- **Date:** 2026-08-19
- **Author:** Nikita Fedorov

## Summary
Adds a pure-Python, dependency-free `PdfBackend` that renders the *same*
drawing display list the SVG backend renders to a byte-stable single-page
vector PDF (FR11), and wires `format: "pdf"` through the tool and a new route.
Determinism is the whole point (FR12): two renders of the same sheet produce
identical bytes.

## Changes
- **New `agentcad/kernel/handlers/_pdf.py`** — `PdfBackend.render(display_list,
  width_mm, height_mm) -> bytes`. Emits PDF content-stream operators directly:
  lines/polylines → `m`/`l`/`S` (filled arrowheads → `B`), circles → 4 cubic
  Béziers (fixed kappa), arcs → ≤90° Bézier segments, text → `BT/Tf/Tm/Tj/ET`
  with base-14 Helvetica (WinAnsiEncoding, no embedding), rects and the hatch
  fallback as paths. No OCP/build123d, no new dependency.
- **Determinism measures:** every coordinate/width/size through `fmt()`; fixed
  5-object numbering/order (catalog, pages, page, font, contents); **no**
  `/CreationDate`, **no** `/ID`, **no** `Info` dict; uncompressed content
  stream (no filter). No `datetime`/`time`/`uuid`/`random` anywhere on the
  path.
- **mm→pt:** `K = 72/25.4`; coordinates transformed in Python (`x_pt = x·K`,
  `y_pt = (H − y)·K`) so text is upright (no y-flipping CTM). Line widths, dash
  arrays and font sizes pre-scaled by `K`.
- **Style→PDF:** the shared `_STROKE` map drives colour (grays via `G`/`g`,
  else `RG`/`rg`), width (`w`), dash (`d`), cap (`J`) — the same values the SVG
  backend uses.
- **`drawing.py` refactor:** extracted `_build_display_list(...)` — the single
  composition both backends consume; `_build_svg`/`_build_pdf` are thin
  wrappers. The `drawing()` handler now dispatches `svg`/`pdf` through one
  measured-table branch (PMI + dim table render on both; DXF still ignores
  them).
- **Raw/dim-table wrinkle (preferred path):** `_dim_table` now emits typed
  `Rect`/`Text` primitives instead of byte-locked SVG strings, so BOTH backends
  render the table from one list; the `Raw` primitive and the dead
  `_text`/`_TXT`/`_BOX`/`_esc` helpers are removed from `drawing.py`. Labels are
  handed through unescaped (the SVG backend escapes on output).
- **`tools_drawing.py`:** accepts `format: "pdf"`; dim-table measurement now
  runs for `svg`/`pdf` (not DXF); tool description/schema updated.
- **`routes_drawing.py`:** new `GET …/parts/{part_id}/drawing.pdf` mirroring the
  `.svg` route — regenerate server-side, stream `application/pdf` through
  `_drawing_result`, with the same `CONFIG_RE.fullmatch` gate on `config`.

## Files
- `agentcad/kernel/handlers/_pdf.py` — new PDF backend (pure Python).
- `agentcad/kernel/handlers/drawing.py` — `_build_display_list` extraction,
  `_build_pdf`, pdf dispatch, dim-table → primitives, dead-helper removal.
- `agentcad/core/tools_drawing.py` — accept/route the `pdf` format.
- `agentcad/server/routes_drawing.py` — new `drawing.pdf` route.
- `tests/test_drawings_pdf.py` — new: determinism (SVG+PDF sha256), structural
  parse + MediaBox (AC6 machine half), git-guarded restore reproduces bytes
  (AC2), PMI-in-PDF, and the route.
- `tests/test_configs_drawing.py` — re-baselined the two direct `_dim_table`
  tests to assert on typed primitives (same intent: em-dash cell count;
  config/X/Z headers present), and the format-error message now names `pdf`.

## Notes
- **Text glyphs:** the base-14 Helvetica path encodes WinAnsi/Latin-1; glyphs
  outside it (⌀ diameter sign, GD&T symbols, ↧ depth arrow) become `?` in PDF.
  Dimension VALUES and tolerances are ASCII and render fully; SVG keeps
  full-fidelity glyphs. A documented v1 limitation of the no-embedding path.
- **Signature deviation:** the design sketched `render(display_list,
  sheet_template, meta)`. `PdfBackend.render` instead mirrors
  `SvgBackend.render(display_list, width_mm, height_mm)` — the dispatch has the
  sheet size in hand and no clock-derived metadata is used (CreationDate/ID are
  omitted entirely), so there is nothing else to pass.
- Verified `pdfinfo` reads the output as a valid 1-page A3 PDF (1190.55 ×
  841.89 pts). OCP boundary re-checked clean (`tools_drawing` imports no
  OCP/build123d). Targeted suites green: test_drawings, test_drawing_holes,
  test_configs_drawing, test_drawings_v2, test_drawings_pdf (77), plus
  test_pmi/test_prd012_acceptance/test_examples_golden (32).

`make test` — **4474 passed, 30 skipped** (clean run; the full suite measured
4465 passed with the 9 `*_cites_a_make_test_count` guards reading this entry's
own count before it was filled — self-referential, green once this number
lands; suite grew 4464→4474 with slice 2's PDF tests).
