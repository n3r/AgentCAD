# 0025 — v2: analysis tier-1 + optional FEM

- **Commit:** 269a1d6
- **Date:** 2026-08-09
- **Author:** Claude Fable 5

## Summary
Adds geometric analysis on shipped deps (section area, min wall thickness,
inertia tensor, projected area) plus an optional linear-static FEM tier that
runs on gmsh + scikit-fem and registers only when the `agentcad[fem]` extra is
installed.

## Changes
- **Tier-1 handler** (`kernel/handlers/analysis.py`, `analyze`): `section`
  (cross-section area on XY/XZ/YZ via `b3d.section`), `wall` (min wall thickness
  by casting inward rays from a UV grid on every face using
  `IntCurvesFace_ShapeIntersector`, optional `min_required` → `ok`), `inertia`
  (volume, center of mass, and inertia tensor from GProp scaled by density),
  and `projected_area` (silhouette area by a ray grid along X/Y/Z). Uses only
  build123d + OCP.
- **FEM implementation** (`kernel/_fem_impl.py`, `run_fem_static`): part → STEP
  → gmsh tet mesh → scikit-fem P2 vector elasticity; axis-aligned face
  selectors clamp `fixed_face` and apply a uniform traction over `load_face`
  (total `load_N` in `load_dir`); returns max displacement, max von Mises, and
  node/tet counts. Validated 0.03% vs the analytic cantilever.
- **FEM handler + gating** (`kernel/handlers/fem.py`): `fem_available()` checks
  for gmsh/skfem/meshio; `fem_static` raises a clear "install agentcad[fem]"
  error otherwise. Deferred import keeps FEM cost off the common path.
- **New tools** (`core/tools_analysis.py`): `analyze_part` (always registered;
  looks up material density for inertia) and `fem_static` (registered only when
  `fem_available()`, so agents never see an unrunnable tool).
- **New routes** (`server/routes_analysis.py`): `POST .../parts/{id}/analyze`
  and `POST .../parts/{id}/fem`; the FEM route returns HTTP 501 (FEMUnavailable)
  when the tool is not registered.

## Files
- `agentcad/kernel/handlers/analysis.py` — section/wall/inertia/projected_area
- `agentcad/kernel/_fem_impl.py` — gmsh mesh + scikit-fem P2 elasticity pipeline
- `agentcad/kernel/handlers/fem.py` — availability gate + `fem_static` handler
- `agentcad/core/tools_analysis.py` — `analyze_part` + conditional `fem_static`
- `agentcad/server/routes_analysis.py` — analyze + fem routes (501 when unavailable)
- `tests/test_analysis.py` — wall probe (2.5 mm shell), inertia vs analytic, section/projected area, FEM cantilever (skipped without the extra)

## Notes
FEM is subprocess/GPL-isolatable (gmsh) and behind an optional extra; tier-1
analysis ships by default. Stresses near clamps show singularities (noted in the
result).
