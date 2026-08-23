# 0307 — Exact drawing view bounds; lines and arcs stop being tessellated

- **Commit:** pending
- **Date:** 2026-08-23
- **Author:** Claude (Opus 5) for Nikita Fedorov

## Summary

Two product bugs that AgentCAD-Bench (PRD-024) *fenced* rather than fixed, both
in the drawing/flat-pattern projection path. `_view_bounds` sampled six points
per edge — exact for a line, wrong for a circle — so a Ø140 flange's plan view
was auto-scaled, placed and **dimensioned** 132.641. And `_edge_prim`
special-cased only the closed circle, so every other projected edge, a
dead-straight 90 mm line included, was discretised into up to 256 points. View
bounds now come from each edge's own bounding box, and a `LINE`/`CIRCLE` edge
is drawn as the exact thing it is in every backend (SVG, PDF, DXF).

## Changes

- **`_view_bounds` is exact.** Per-edge `e.bounding_box()` instead of six
  `position_at` samples. The `(0, 0, 1, 1)` empty fallback is unchanged and no
  caller changed: the same tuple still feeds `_choose_scale`, the per-view
  placements, the overall-dimension text, and sheetmetal's public
  `flat_bbox_mm` (which is therefore now exact too). It is also cheaper than
  the sampler it replaces.
- **New `drawing._arc_angles(e, *, y_down=True)`.** `(start_deg, end_deg)` for
  an open circular edge, ordered so that sweeping from `start` to `end` in the
  direction of **increasing** angle passes through `position_at(0.5)` — the
  contract `SvgBackend._arc` (sweep-flag 1), `PdfBackend._arc` (the Bézier
  chain) and ezdxf's `add_arc` (CCW) all already assumed. `y_down=True` measures
  in the sheet plane, where projected Y is negated, so the ordering is computed
  *after* the mirror; `y_down=False` is the model-plane twin the DXF writer
  wants. A sweep within `_ARC_FULL_TOL_DEG` (1e-3°) of 0 or 360 returns
  `(None, None)` and the caller draws a full circle.
- **`_edge_prim` branches by geometry.** `LINE` → a two-point `Polyline`
  (deliberately the polyline, not the `Line` primitive, so an outline edge stays
  an SVG `<path>`); closed `CIRCLE` → `Circle` as before; open `CIRCLE` → the
  `Arc` primitive, which until now was vocabulary with **no producer**; anything
  else (ELLIPSE, BSPLINE) keeps the sampler unchanged. `Arc`'s "kept in the
  vocabulary" comment in `_draw_primitives.py` is rewritten accordingly.
- **`_clip_edges_to_circle` clips straight edges analytically.** New
  `_clip_segment_to_circle` solves the segment/circle quadratic in `t` and
  clamps the root interval to `[0, 1]`; a tangent touch and a zero-length
  segment are "no run". Curved edges keep the sampler — clipping a curve to a
  circle needs samples, and the run it yields *is* a polyline.
- **`_build_dxf` writes native entities.** `LINE` → `add_line`, open `CIRCLE` →
  `add_arc` (model-plane angles, CCW), closed → `add_circle`, else the
  lwpolyline sampler. A DXF is read by other CAD, and a LINE/ARC survives the
  round trip as the exact thing it is.
- **`sheetmetal.py` mirrors both.** `_edge_svg` gets the same branches, emitted
  as strings with this module's own local `:.3f` contract (not the display
  list's `fmt`); an arc stays a `<path … A …>` element, so the outline is still
  "paths plus circles" for anything counting them. `_dxf_flat` mirrors
  `_build_dxf` on the OUTLINE layer. `_arc_angles` is imported beside the
  existing `_VIEW_DIRS`/`_view_bounds`.
- **Bench side.** `test_render_drawing_refuses_a_sheet_that_contradicts_the_part`
  is **deleted** — its own docstring instructed exactly that once the bug was
  fixed — and replaced by
  `test_render_drawing_dimensions_the_curved_part_exactly`, which renders
  mfd_003's flange with `check_dims=True` and asserts `overall_dim_problems ==
  []`, three `140`s among the dimension texts, and no `132.64`.
  `mfd_003_head_flange`'s shipped asset is re-rendered from the author CLI (it
  was a hand-corrected sheet from a pre-PRD-014 renderer; the new one is an
  honest machine render, 28 850 → 30 051 B, still under the 40 kB asset cap, and
  its plan view now reads 140 × 140). `compact_svg` **stays** — it still guards
  the ellipse/bspline sampler — with its docstring and the stale comment above
  `test_render_drawing_writes_a_three_view_svg`'s `0 < longest < 64` assertion
  updated to say why.
- **Prose.** The `_view_bounds` sentences in `docs/bench.md` and `AGENTS.md` now
  say "fixed (changelog 0307); `check_dims` stays as a live guard". The
  swept-solid half of the same bullets is untouched here (see 0309).

## Files

- `agentcad/kernel/handlers/drawing.py` — exact `_view_bounds`; new
  `_arc_angles`, `_ARC_FULL_TOL_DEG`, `_clip_segment_to_circle`; branched
  `_edge_prim`, `_clip_edges_to_circle`, `_build_dxf`; `Arc` imported
- `agentcad/kernel/handlers/_draw_primitives.py` — `Arc`'s comment: it has a
  producer now, and the sweep direction is stated as a contract
- `agentcad/kernel/handlers/sheetmetal.py` — `import math`, `_arc_angles`
  import, branched `_edge_svg` and `_dxf_flat`
- `agentcad/bench/author.py` — `compact_svg`, `overall_dim_problems` and
  `render_drawing` docstrings: the defect is fixed, the guard is live
- `benchmarks/tasks/model_from_drawing/mfd_003_head_flange/assets/drawing.svg`
  — re-rendered
- `tests/test_drawings_v2.py` — six new tests (see below)
- `tests/test_bench_author.py` — tripwire deleted, replacement added, stale
  comment rewritten
- `AGENTS.md`, `docs/bench.md` — the drawing half of the "two product findings"
  bullets

## Verification

Written test-first: all four of the originally planned product tests failed
before the change (`132.641`/`133.148` dimension texts, a 54.266 mm plan
extent on a 55 mm part, `['LWPOLYLINE']` as the only DXF entity type, and
multi-`L` outline paths on a box) and pass after.

New tests in `tests/test_drawings_v2.py`:

- `test_a_curved_silhouette_is_dimensioned_at_its_true_extent` — the Ø140
  flange's plan view reads `140` twice; no `132.64`/`133.14` anywhere.
- `test_a_prismatic_part_draws_two_point_paths_not_tessellated_lines` — every
  outline path on a box sheet is exactly `M x y L x y`.
- `test_a_partial_arc_silhouette_renders_as_an_arc_the_right_way_round` — a
  60 × 40 block with an R15 lug on its +Y edge: the plan view is dimensioned
  60 × 55 (was 60 × 54.266) and the half-round renders as an `A` segment whose
  **rendered endpoints** are asserted — same sheet y, a full diameter apart,
  left endpoint first, `large-arc 0 sweep 1`. That is the arc-orientation pin:
  flip either the y-negation or the midpoint ordering in `_arc_angles` and this
  pair changes.
- `test_dxf_carries_native_lines_and_arcs` — a box exports LINEs and **no**
  LWPOLYLINE; the lug exports both ARC and LINE.
- `test_a_detail_view_clips_a_straight_edge_analytically` — a detail circle over
  a box corner yields two-point runs.
- `test_clip_segment_to_circle_is_exact` — the pure quadratic: chord through the
  centre, one endpoint inside, wholly inside, miss, tangent, zero-length,
  wholly-outside-but-collinear.

`test_dxf_carries_native_lines_and_arcs` also pins the **model-plane** angle twin
(`_arc_angles(y_down=False)`), which the SVG test cannot reach: the lug's rim is
`center (0, 20), R15, start 0deg, end 180deg`, and ezdxf sweeps CCW, so that is
the half bulging to +Y. The reversed twin reads `(180, 360)` and would otherwise
pass on `"ARC" in types` alone. Both this and the SVG endpoint pin were
mutation-checked: swapping `y_down=False` for `y_down=True` in `_build_dxf`
fails the DXF assertion with `(-180, -0)`.

In `tests/test_bench_author.py`,
`test_render_drawing_refuses_before_writing_when_check_dims_objects` covers the
`check_dims` refusal branch, which the deleted tripwire was the only end-to-end
exercise of — three docstrings and two docs now claim the guard "stays live", so
it needs a test that does not depend on a part being broken.
`overall_dim_problems` is monkeypatched to report one problem (that function IS
the judgement; everything downstream of it is the real path): `render_drawing`
raises `ValidationError`, its `details` name the part and the problems, and
**nothing is written** to the target. Mutation-checked: `if problems:` ->
`if False:` fails it with `DID NOT RAISE`.

Targeted suites, all green:

```
uv run pytest -q tests/test_drawings.py tests/test_drawings_v2.py \
  tests/test_drawings_pdf.py tests/test_drawings_sections.py \
  tests/test_drawings_holes_table.py tests/test_drawing_holes.py \
  tests/test_drawings_tabulate.py tests/test_configs_drawing.py \
  tests/test_sheetmetal.py tests/test_sheetmetal_v2.py \
  tests/test_bench_author.py tests/test_prd014_acceptance.py \
  tests/test_bench_tasks.py
275 passed, 2 skipped
```

`make test` — <orchestrator fills>

## Notes

**Accepted visible changes.** No golden SVG is pinned anywhere and every
determinism test is a render-twice self-comparison, so none of this breaks a
byte contract — but the pixels do move:

- Auto-scale may drop a ladder notch on a curved part, because the fit is now
  computed from the true silhouette. (The A3 flange kept 1:2.)
- Annotation and view positions shift on curved sheets, for the same reason.
- Sheet bytes shrink: the four-view flange went 105 590 → 95 579 B (-9.5 %), and
  a purely prismatic part shrinks far more.
- The DXF entity mix changes (LINE/ARC where there used to be LWPOLYLINE). DXF
  is never byte-compared — `core/checks.py` excludes it by name because ezdxf
  stamps `$TDCREATE` — so this is a quality gain, not a determinism risk.
- `flat_bbox_mm` becomes exact. `test_sheetmetal_v2`'s extremes are straight
  edges, so its `abs=0.1` assertions are unaffected.

**Gotcha for a future reader.** The arc ordering has to be computed in the frame
it will be *rendered* in. The sheet frame negates Y, and a mirror reverses the
sense of every angle, so taking the angles in model coordinates and then
negating them gives an arc that bulges the wrong way. That is what
`_arc_angles`' `y_down` flag is for, and why the DXF writer passes
`y_down=False`.

**Not fixed here.** `compact_svg` is still needed: an iso view of a turned part
is full of ELLIPSE/BSPLINE edges and those still sample. Emitting real ellipse
arcs is a separate change with its own backend work.
