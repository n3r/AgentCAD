# 0048 — Class-A surfacing toolkit + curvature analysis

- **Commit:** pending
- **Date:** 2026-08-09
- **Author:** Claude (with Nikita Fedorov)

## Summary

Continuity-controlled freeform surfacing (roadmap "Class-A surfacing"):
`smooth_loft` and `blend_surface` (G0/G1/G2 face blends via OCCT plate
filling) in the toolkit, plus a `curvature` analysis kind so agents can
verify continuity intent numerically — the curvature-analysis half the
roadmap called out.

## Changes

- **`agentcad/toolkit/surfacing.py`** (new): `smooth_loft(profiles,
  ruled=False)` (spline loft → ruled fallback with warning → RuntimeError)
  and `blend_surface(face_a, face_b, continuity="G0|G1|G2")` — plate filling
  (`BRepOffsetAPI_MakeFilling`) between the faces' nearest edges with
  support-face continuity constraints, straight G0 rails closing open
  boundaries, and a degradation ladder G2 → G1 → G0 with honest warnings.
  Two root-caused findings baked in: (1) nearest-edge selection ranks by
  mean sampled distance (plain min-distance ties on rectangle corners and
  picks a side edge → collinear-boundary crash); (2) **literal `GeomAbs_G2`
  can never work in OCCT 7.x plate filling** — the enum value (3) is passed
  straight through as the integer Tang order (valid 0..2), so "G2" maps to
  `GeomAbs_C1` (which lands as Tang=2, a true curvature constraint; verified
  against OCCT V7_7_2 sources). Curvature-constrained plates balloon on
  non-coplanar supports while reporting success (measured 2.98×–338× area),
  so G2 results are gated against the G1 reference (ratio 1.5) and degrade
  with a warning.
- **Curvature analysis**: `analyze` kind `"curvature"` — per-face gaussian
  and mean curvature via `BRepAdaptor_Surface` + `BRepLProp_SLProps` on a
  UV grid (clamped 4..16), `IsCurvatureDefined`-guarded. Validated: cylinder
  |H| = 1/(2r) and |K| ≈ 0; sphere K = 1/r²; box ≈ 0.
- Inspector Analysis tab gains a Curvature card (worst |K|, per-face ranges
  capped at 8); `analyze_part` kind description extended; toolkit lazy
  re-export updated; CHEATSHEET line added.

## Files

- `agentcad/toolkit/surfacing.py`, `agentcad/toolkit/__init__.py`
- `agentcad/kernel/handlers/analysis.py`, `agentcad/core/tools_analysis.py`
- `frontend/js/inspector.js`
- `tests/test_surfacing.py` — 14 tests incl. the G2-degradation and
  ballooning-gate paths
- `docs/agent-api.md`, `docs/part-authoring.md`, `agentcad/core/templates.py`

## Notes

The G2 stability gate is an area-ratio proxy against the G1 reference — it
cleanly separates the observed converged (1.00) vs ballooned (≥2.98) cases;
a seam-curvature check would be stricter if ever needed. Add/add conflict in
`toolkit/__init__.py` with 0039 resolved by keeping both submodules.
