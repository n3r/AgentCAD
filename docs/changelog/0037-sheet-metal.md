# 0037 — Sheet metal: SheetPart toolkit + flat-pattern export

- **Commit:** pending
- **Date:** 2026-08-09
- **Author:** Claude (with Nikita Fedorov)

## Summary

First-party sheet-metal semantics (roadmap "Sheet metal"): a declarative
`SheetPart` builder in the toolkit that yields both the folded solid and the
manufacturing flat pattern from one spec, plus a `flat_pattern` kernel
handler + tool exporting the unfolded blank with bend lines (SVG/DXF).

## Changes

- **`agentcad/toolkit/sheetmetal.py`** (new): `SheetPart(thickness,
  k_factor=0.44)` with `.base(width, depth)`, `.flange(edge, angle_deg,
  length, inner_radius=None)` (full-edge flanges, one per edge, bending +Z),
  `.fold()` (base + cylindrical bend sector + leaf fused into one valid
  solid; volume matches the analytic `w·d·t + Σ[α·t·(R + t/2) + L·t]·edge_w`
  to ~1e-9 rel), `.unfold()` (flat blank; each flange adds
  `BA + length` where `BA = α·(R + K·t)`), `.flat_outline()`, and
  `.bend_lines()` (bend midlines in flat coordinates). Fusion goes through
  `safe_bool`, with fallback warnings collected on `sp.warnings`. Registered
  lazily in `toolkit/__init__` (server import stays build123d-free — pinned
  by a subprocess test).
- **Kernel handler** `handlers/sheetmetal.py` (new): method `flat_pattern` —
  executes the script's optional `flat_pattern(p)` contract function (a flat
  Part or `(part, bend_lines)`), projects the top view (reusing the drawing
  pack's SVG primitives), and emits fit-to-content 1:1 SVG (dashed bend lines
  with `angle° R…` callouts in a `<g id="BEND">` group) or DXF with
  `OUTLINE`/`BEND` layers. Returns `{path, size_bytes, flat_bbox_mm,
  n_bend_lines}`.
- **Tool** `flat_pattern(project, part_id, format)` (`tools_sheetmetal.py`
  pack): script parts only (references rejected pre-kernel), writes
  `exports/<part_id>_flat.<ext>`.
- CHEATSHEET gains a SHEET METAL section; part-authoring gains a toolkit
  subsection; agent-api documents the tool.

## Files

- `agentcad/toolkit/sheetmetal.py`, `agentcad/toolkit/__init__.py`
- `agentcad/kernel/handlers/sheetmetal.py`
- `agentcad/core/tools_sheetmetal.py`
- `agentcad/core/templates.py` — CHEATSHEET section
- `tests/test_sheetmetal.py` — 12 tests: analytic fold volume (1%), unfold
  flat length vs bend allowance (1e-6), bend-line coordinates, two-flange
  cases, validation errors, handler SVG/DXF structure, tool + reference
  rejection, toolkit lazy-import discipline
- `docs/agent-api.md`, `docs/part-authoring.md`

## Notes

The flat pattern SVG is true-scale (1:1 mm, fit-to-content) rather than the
A3 sheet used by `generate_drawing` — it is a manufacturing artifact. The
top-view projection was empirically verified to be an identity on model XY,
which is what justifies overlaying flat-coordinate bend lines on the
projected outline. Bend-relief and partial-width flanges are explicitly out
of scope for this v1 (full-edge flanges only).
