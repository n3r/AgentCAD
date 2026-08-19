# 0262 — 2026-08-19 — PRD-014 slice 3: section views and detail views

## Summary

Slice 3 of Drawings v2 — section views (FR6) and detail views (FR7). The
drawing handler now cuts the built part into labeled, hatched section views with
cutting-plane arrows on the parent view, and magnifies a chosen region into a
scaled detail view — both deterministic, in SVG and PDF.

## Changes

- **`agentcad/kernel/handlers/drawing.py`** — the section/detail engine
  (kernel-side, no second round-trip: the handler already holds the built part,
  so it sections it directly and `affinity=part_id` holds trivially). For each
  `sections` spec it cuts **each solid body separately** with
  `b3d.section(solid, section_by=Plane.<XY|XZ|YZ>.offset(offset_mm))`, traces
  every face's outer + inner wires in connectivity order, and projects them to
  plane-local 2D loops. Composition: closed `Polyline(VIS)` outlines + one
  `Hatch` per body with **alternating 45°/135°** angles, a `A-A`/`B-B` label,
  and cutting-plane marks (a `CHAIN` line + arrowheads + letter) on the parent
  view (`xy→front`, `xz→top`, `yz→front`). A plane that misses the solid → a
  `warnings` entry + a labeled **empty** view, never a blank sheet. `details`
  clip the parent view's already-projected edges to a circle (pure 2D, no
  rebuild), re-center and scale them into a slot labeled `A (2:1)`, and mark the
  source circle on the parent. Extra views land in a deterministic row of slots.
- **`agentcad/kernel/handlers/_draw_primitives.py`** — `hatch_line_segments`
  (shared deterministic scanline hatch with even-odd holes, anchored at integer
  multiples of `pitch`); `SvgBackend._hatch` now renders real parallel hatch
  lines (Slice 1/2 only had the outline fallback — this slice is the first
  `Hatch` producer).
- **`agentcad/kernel/handlers/_pdf.py`** — `PdfBackend._hatch` renders the
  *same* segments as PDF strokes, so SVG and PDF hatching are identical geometry.
- **`agentcad/core/tools_drawing.py`** — `sections`/`details` tool args + schema,
  `_validate_sections`/`_validate_details` (index-naming refusals for a bad
  plane / non-number offset / bad view / bad center-radius-scale), request wiring
  (SVG/PDF only — DXF drops specs like PMI/dim-table), and a per-section timeout
  scaling (`_SECTION_TIMEOUT_S`).
- **`tests/test_drawings_sections.py`** (new, 12): a box section hatches with one
  body; two bodies alternate 45°/135°; a missed plane warns + empties; malformed
  section/detail specs name the entry; sequential `A-A`/`B-B`; a detail magnifies
  with an `A (2:1)` label + a source circle; and a section+detail sheet is
  byte-stable in **both** SVG and PDF.

## Determinism

Loops and bodies are sorted by a geometric key (rounded bbox + point count), not
by OCCT iteration order; hatch scanlines anchor at integer `pitch` multiples;
every coordinate goes through `fmt()`. Two runs → identical SVG and PDF bytes,
and the geometry-CI determinism stage stays green.

## Notes

Deviation from the design's Decision 6: section geometry lives in the drawing
handler rather than a separate `section_outline` kernel handler — deliberately,
to avoid a second kernel round-trip (the handler already has the built part).
OCP boundary re-checked clean (`tools_drawing` imports no OCP/build123d). DXF
renders neither sections nor details (both `[]` there), matching how it drops
PMI and the dim table.

`make test` — **4486 passed, 30 skipped** (clean run; the full suite measured
4477 passed with the 9 `*_cites_a_make_test_count` guards reading this entry's
own count before it was filled — self-referential, green once it lands; suite
grew 4474→4486 with slice 3's tests).
