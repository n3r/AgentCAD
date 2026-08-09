# 0029 — Add v2 UI + drawing assets (inspector screenshot; flange drawing SVG)

- **Commit:** 551f4fc
- **Date:** 2026-08-09
- **Author:** Claude Fable 5

## Summary
Adds two static documentation assets under `docs/assets/`: a screenshot of the
v2 inspector panel (materials/analysis) and a hand-generated 2D flange
engineering drawing in SVG. No code, tests, or behavior change — assets only.

## Changes
- Adds `docs/assets/flange-drawing.svg` (293 lines): a self-contained,
  dependency-free vector drawing on an A3 sheet (`width="420mm"
  height="297mm"`, `viewBox="0 0 420 297"`) with a white sheet fill and a
  0.5mm inner border frame. The drawing is a flange with a bolt-circle pattern:
  concentric pitch/OD circles plus eight bolt-hole circles arrayed on the bolt
  circle, drawn with dashed `#777` centerlines/hidden lines
  (`stroke-dasharray="2.4 1.2"`). Composition is 24 `<circle>`, 66 `<path>`,
  6 `<line>`, 4 `<polygon>` arrowheads for dimension terminators, 2 `<rect>`
  (sheet + frame), and 3 `<text>` annotations (dimension values `132.64`,
  `133.15` at font-size 3.5 and a `TOP` view label at font-size 4). Uses a
  Helvetica/Arial `font-family`.
- Adds `docs/assets/inspector-v2.png` (~346 KB binary): a screenshot of the v2
  inspector UI surface showing the materials and analysis panes, for use in
  docs/README references.

## Files
- `docs/assets/flange-drawing.svg` — new A3 vector flange drawing (bolt-circle
  view with centerlines, dimensions, and title text)
- `docs/assets/inspector-v2.png` — new screenshot of the v2 inspector
  (materials/analysis)

## Notes
Documentation assets only — nothing imports or renders these at runtime. The
SVG is fully inline (no external fonts or images), so it renders standalone.
The flange geometry is illustrative for docs, not produced by the kernel.
