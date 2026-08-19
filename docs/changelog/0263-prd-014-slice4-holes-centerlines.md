# 0263 — 2026-08-19 — PRD-014 slice 4: center marks, coaxial centerlines, hole tables

## Summary

Slice 4 of Drawings v2 — the shop drafting furniture: center marks in **every**
view (fixing today's top-view-only detection), coaxial-run centerlines in side
views (FR8), and an opt-in hole table with per-hole tags (FR9, from PRD-010
metadata, with a detected-diameter fallback).

## Changes

- **`agentcad/kernel/handlers/drawing.py`** —
  - **FR8 (automatic):** `_detect_circles` now runs on **each** rendered view's
    own projected edges; a `_center_mark` (two crossing `THIN` lines) is drawn
    at every detected circle center and PRD-010 hole center, deduped and sorted
    by rounded `(x, y)`. A coaxial hole run (a record with ≥2 centers + an axis)
    seen edge-on in a side view gets a `CHAIN` centerline spanning the run
    (`_project_to_view` derived analytically from `_VIEW_DIRS`; `_edge_on` tests
    axis ⊥ view direction). `iso` yields none (circles project to ellipses).
  - **FR9 (opt-in `hole_table=True`):** a table in `table_zone`, stacked below
    the dim table — tag (`A1, A2…`), X, Y from the top view's bbox lower-left
    datum, and the standard designation (`hole_standards.designation_for_record`,
    the final degraded callout text as drawn). Without metadata it falls back to
    detected diameter groups, one row each, `detected: true`, diameters only —
    no fabricated designation. Tags print at each hole. Overflow past the zone
    caps rows + a `warnings` entry, never a silent truncation.
- **`agentcad/core/tools_drawing.py`** — a `hole_table` bool arg + schema, wired
  SVG/PDF only (like `dim_table`), and surfaced at top-level `result["hole_table"]
  = {rows, from_metadata, datum, warnings?}`.
- **`tests/test_drawings_holes_table.py`** (new, 9): AC4 with metadata
  (designation + tags, `from_metadata: true`) and the detected fallback
  (diameters only); the datum; center marks beyond the top view; a coaxial
  centerline in a side view; a hole-table sheet byte-stable in SVG **and** PDF;
  and unit tests for the projection/edge-on/center-mark helpers.

## Notes

Deviation: FR9's hole table is **opt-in** (`hole_table=True`), not automatic —
an always-on designation table would print callout text on every default sheet
and break the existing `test_drawing_holes.py::test_ac5` ⌀-count assertion. FR8
center marks/centerlines stay automatic (they add no conflicting text). All
coordinates through `fmt()`, sorted order — the geometry-CI determinism stage
stays green; OCP boundary clean.

`make test` — **4496 passed, 30 skipped** (measured on the combined slice-4 +
slice-6 working tree in one run — this commit lands slice 4, the route/frontend
work follows in 0264; the run showed 4487 passed with the 9 self-referential
`*_cites_a_make_test_count` guards, green once the counts land).
