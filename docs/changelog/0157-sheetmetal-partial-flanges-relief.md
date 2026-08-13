# 0157 — 2026-08-13 — PRD-010 slice 11: sheet-metal v2a — partial flanges, bend relief, outline-from-unfold

- **Commit:** pending
- **Date:** 2026-08-13
- **Author:** Claude (PRD-010 slice 11)

## Summary

FR9, FR10 and the structural half of FR12, plus **AC4**. `SheetPart` learns
partial-width flanges (`start` / `width`, several per edge as long as their
spans do not overlap), automatic bend relief (`rect | round | tear`) cut from
**one computation applied to both `fold()` and `unfold()`**, and a
`flat_outline()` that is now a discretization of `unfold()`'s own top face
rather than a parallel walker — design Decision 9, which turns FR12's
"one-spec-consistent" from an invariant somebody has to maintain into a fact
about where the numbers come from.

Spike S9 measured the model before any of it was written, and it produced the
one number this design rests on: fold and unfold disagree by **exactly** the
k-factor's own neutral-fibre offset, and by nothing else.

## Spike S9 — does the fold/unfold model survive partial tabs?

`scratchpad/spike_sheet_v2.py`, through the kernel worker (`worker_probe.py`),
on the AC4 bracket: a 60 × 40 × 2 plate with **one** 90° flange spanning
x ∈ [−15, +15] of the 60 mm front edge, R = 3, leaf 30 — so the flange stops in
the middle of the edge at *both* ends and earns two reliefs.

**(a) `fold()` is one valid solid, for every relief kind.**

| relief | ms | `is_valid` | solids | fold vol | unfold `is_valid` | solids | unfold vol | cuts |
|---|---:|---|---:|---:|---|---:|---:|---:|
| rect | 48.4 | True | 1 | 6916.9911 | True | 1 | 6905.6814 | 2 |
| round | 68.2 | True | 1 | 6920.8540 | True | 1 | 6909.5442 | 2 |
| tear | 16.9 | True | 1 | 6976.9911 | True | 1 | 6965.6814 | 0 |

Two non-overlapping flanges on one edge (spans [4,22) and [38,56) of the same
60 mm edge): valid, **1 solid**, 7412.3893 mm³.

**(b) The tolerance, and where it comes from.** The solid model puts the
neutral fibre at `t/2` (a bend sector of volume `angle·t·(R + t/2)·span`); the
flat model puts it at `k·t` (`BA = angle·(R + k·t)`). So for every bend

```
fold().volume − unfold().volume = angle_rad · (0.5 − k) · t² · span
```

and for nothing else. Measured:

```
fold   6916.991118 mm^3
unfold 6905.681385 mm^3
diff     11.309734 mm^3
predicted by the k-factor  11.309734 mm^3
residual                   -0.0        (below 1e-9)
diff as a fraction of fold  0.001635
```

That is the bend-allowance model's own tolerance. It is **not** an error, it
does not accumulate with feature count, and it is now asserted directly in
`test_partial_flange_unfold_matches_fold_within_the_k_factor_gap` rather than
hidden behind a `rel=0.01`.

**(c) The outline derived from `unfold()`'s own top face.**

| relief | ms | points | edge kinds | shoelace area | CCW | top-face area | error |
|---|---:|---:|---|---:|---|---:|---:|
| rect | 4.44 | 16 | 16 × LINE | 3452.8407 | yes | 3452.8407 | **0.0** |
| round | 4.23 | 28 | 14 × LINE, 2 × CIRCLE | 3455.0070 | yes | 3454.7721 | 0.2349 |

Exact for a straight-edged blank; for a round relief the chord polygon
*over*-states the blank (the arc bulges into removed material) by 0.007 % at a
0.05 mm chord tolerance. `flat_outline_edges()` returns the two arcs exactly.

**(d) Reliefs appear in both, because they are the same solids.**

| relief | removed from `fold()` | removed from `unfold()` | one slot |
|---|---:|---:|---:|
| rect | **60.0** | **60.0** | 30.0 (= 1.5t × (R+t) × t) |
| round | **56.1372** | **56.1372** | 28.0686 |
| tear | **0.0** | **0.0** | — |

A flange flush to a plate corner gets **one** relief, not two (30.0 mm³
removed) — a relief exists where material remains beside the flange, and at a
plate corner none does.

**(e) The sliver that was not there.** The design worried that a relief narrower
than the mesh tolerance would turn the fold into a sliver. Swept t = 2.0, 1.0,
0.5, 0.25, 0.1, **0.05** mm (relief width 1.5t = 3.0 … **0.075** mm): valid,
**1 solid**, every time, for both fold and unfold. The reason is structural —
the relief is sized *from the thickness*, so it never becomes narrow relative
to its own sheet. No guard rung was needed.

## What shipped

- `_Flange` gains `start`, `width`, `relief` and `hem`; the per-edge uniqueness
  rule becomes a **non-overlap** rule over `[start, start + width)`, with the
  error naming both spans. `start` is measured from the edge's
  low-coordinate end (X− for `front`/`back`, Y− for `left`/`right`) and
  `width=None` keeps v1's full-edge meaning.
- **The v1 corpus is the gate and it passes unchanged.** `_flange_solid` takes
  the v1 `extrude(amount=edge_w/2, both=True)` branch verbatim when the span is
  the whole edge, and the asymmetric `extrude` + `translate` branch otherwise;
  `_tab_solid` reduces to v1's `Box` + `translate` for a full-edge tab. A test
  asserts `width=None` and `start=0, width=<edge>` agree to `rel=1e-12` in
  volume and produce identical outlines. Measured independently in the spike:
  the asymmetric path reproduces v1's two-flange fold volume to the last bit
  (10056.6371).
- Relief kinds `rect | round | tear`, `"auto"` → `rect`, or an explicit
  `{"kind", "width", "depth"}`. Sizing lives in `RELIEF_WIDTH_FACTOR = 1.5` and
  `RELIEF_DEPTH_EXTRA = 1.0` with the rule written beside them and **"no
  standard governs this"** stated in the docstring and in
  `docs/part-authoring.md`. `tear` removes nothing and says so in
  `sp.warnings` at declaration time (so it cannot be duplicated by repeated
  `fold()` calls).
- `flat_outline(tolerance=0.05)` is derived from `unfold()`'s top face:
  `order_edges()` for ordering, per-arc sampling for a sagitta ≤ *tolerance*,
  shoelace sign for CCW, and a rotation so the list starts at the vertex
  nearest the (−width/2, −depth/2) corner. `flat_outline_edges()` returns the
  exact lines and arcs. Both are memoized per tolerance and invalidated by any
  declaration.
- `bend_lines()` spans the flange's own extent, not the whole edge.
- `fold()`/`unfold()` end in `_checked()`, which warns when `is_valid` is False
  **or** the solid count is not 1 — the standing fact that OCCT's "success" is
  not evidence.

## AC4 — verified, and looked at

`tests/test_sheetmetal_v2.py::test_ac4_partial_flange_bracket_exports_a_flat_pattern_with_reliefs`
drives a bundled-shape part script through the real `flat_pattern` tool. The
rendered SVG (`scratchpad/ac4_flat.svg`, 27137 bytes, 60.00 × 76.09 mm) was
rasterised and **inspected**: the blank is the 60 × 40 plate with one 30 mm tab
hanging off the front edge, the two **round reliefs** are keyhole notches
straddling the ends of the bend line at the plate/tab junction, and the dashed
`BEND` line labelled `90° R3` spans the 30 mm tab only — not the 60 mm edge.
The fold's volume through `get_part` is 6920.854 mm³, the spike's `fold_round`
number for the same parameters.

## Files

- `agentcad/toolkit/sheetmetal.py` — rewritten around the v2 model
  (`_Flange`, `_add_flange`, `_relief_spec`, `_declared_span`,
  `_effective_span`, `_flange_solid`, `_tab_solid`, `_relief_cuts`,
  `_outline`, `_checked`, and the `_xy` / `_signed_area` / `_arc_samples` /
  `_arc_is_ccw` free helpers)
- `tests/test_sheetmetal_v2.py` — **new**
- `docs/part-authoring.md` — the sheet-metal section rewritten for v2
- `docs/changelog/0157-sheetmetal-partial-flanges-relief.md`

## Verification

- `.venv/bin/python -m pytest -q tests/test_sheetmetal.py` — **12 passed**, the
  v1 corpus unchanged and untouched.
- `.venv/bin/python -m pytest -q tests/test_sheetmetal.py
  tests/test_sheetmetal_v2.py` — see 0158 for the combined count; slices 11 and
  12 land together in one file and one test module.
- The full-suite numbers are in 0158.

## Notes

- The blank's relief notch is *open*, not an interior hole: at the flange end
  the removed slot straddles the bend line, and the half of it that lies beyond
  the tab reaches the plate's own boundary. That is why `outer_wire()` sees it
  and the outline area is exact.
- `flat_outline()` now costs an `unfold()` (11–70 ms) on a cold cache. It was
  free in v1 because it was a separate model — which is exactly the thing this
  slice removed.
- Not done here (slice 14 owns them): the CHEATSHEET section and `AGENTS.md`.
