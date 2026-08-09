# 0024 — v2: 2D engineering drawings (HLR views, SVG/DXF)

- **Commit:** f18d307
- **Date:** 2026-08-09
- **Author:** Claude Fable 5

## Summary
Generates 2D engineering drawings from a script part: OCCT hidden-line-removal
projections (front/top/right/iso) rendered to a hand-rolled dimensioned SVG
sheet, plus a DXF export. Dimensions and hole callouts are measured from the
projected geometry, not copied from parameters.

## Changes
- **Worker handler** (`kernel/handlers/drawing.py`, `drawing`): projects the
  part via `project_to_viewport` (HLR) for each requested view, laying visible
  edges (solid) and hidden edges (dashed, non-iso) into an A3 SVG with a border
  and title block. Circles render as `<circle>`, other edges as sampled paths.
- **Annotation layer**: `_linear_dim` draws overall width/height dimensions
  (with extension lines, arrowheads, rotated text) on front/top from the
  measured view bounds; top-view circles are grouped by radius into
  `diameters_mm` and `hole_groups` (count ≥ 3) hole callouts.
- **DXF export** (`_build_dxf` via ezdxf): top-view visible edges written as
  CIRCLE / LWPOLYLINE entities to modelspace.
- **New tool** (`core/tools_drawing.py`, `generate_drawing`): validates format
  (`svg`/`dxf`) and script-part-only, reads the script, and writes to
  `exports/<part>_drawing.<ext>`; returns path, size, and detected features.
- **New routes** (`server/routes_drawing.py`): `POST .../parts/{id}/drawing`
  (regenerate, honoring `views`/`format`) and `GET .../parts/{id}/drawing.svg`
  (regenerate + stream the SVG bytes with `Cache-Control: no-store`).

## Files
- `agentcad/kernel/handlers/drawing.py` — HLR projection, SVG/dimension rendering, DXF export
- `agentcad/core/tools_drawing.py` — `generate_drawing` tool
- `agentcad/server/routes_drawing.py` — drawing POST + SVG-preview GET
- `tests/test_drawings.py` — SVG view labels + detected bolt group, DXF round-trip via ezdxf, views subset

## Notes
Drawings are script-part only (references are rejected). The SVG is
print-oriented (fixed black/blue ink, third-angle, mm). Values are geometry-
derived, so a flange's OD/bore and 8-hole bolt circle are detected, not
declared.
