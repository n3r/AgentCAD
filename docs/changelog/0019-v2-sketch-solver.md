# 0019 — v2: 2D constraint-solved sketches (scipy solver + solve_sketch tool)

- **Commit:** 4a6a845
- **Date:** 2026-08-09
- **Author:** Claude Fable 5

## Summary
Adds a first-party 2D sketch constraint solver (scipy least-squares) exposed as
both a toolkit module and a `solve_sketch` agent tool + route, so agents can
resolve a constrained sketch to exact coordinates and feed them into a normal
build123d `BuildLine`/`BuildSketch`.

## Changes
- **Solver `agentcad/toolkit/sketch.py`:** `Sketch` builds a residual system
  over free point (x, y) and circle radius parameters and solves via
  `scipy.optimize.least_squares` (`lm` when overdetermined, else `trf`). Points
  can be fixed; circle radii can be fixed.
- **Constraint vocabulary:** fixed, coincident, distance, distance_x,
  distance_y, horizontal, vertical, parallel, perpendicular, angle,
  point_on_line, point_on_circle, radius, equal_radius, midpoint,
  tangent_line_circle (optional explicit tangency point), tangent_circles
  (external/internal).
- **Result:** returns `ok` (success AND max residual < 1e-7), `max_residual`,
  `n_params`/`n_residuals`/`dof`, `nfev`, `solve_ms`, and solved `points`/
  `circles` — so callers can detect over/under-constraint.
- **JSON front end `solve_sketch(spec)`** builds a `Sketch` from
  points/lines/circles/constraints and dispatches by constraint `type`
  (unknown type → `SketchError`).
- **Tool pack `core/tools_sketch.py`:** registers `solve_sketch`; wraps solver
  errors and non-convergence in `ValidationError` with usage guidance,
  including the mirror-solution caveat (the solver converges to the nearest
  solution, so a mirrored initial guess yields a mirrored result).
- **Route `server/routes_sketch.py`:** `POST /sketch/solve` (project-
  independent) forwarding entities+constraints to the tool.

## Files
- `agentcad/toolkit/sketch.py` — new scipy least-squares constraint solver + JSON front end
- `agentcad/core/tools_sketch.py` — `solve_sketch` tool with usage/mirror-guard docs
- `agentcad/server/routes_sketch.py` — `POST /sketch/solve`
- `tests/test_sketch.py` — rectangle dims, two-circle tangent line, solved-coords→build123d face area, contradictory-constraint non-convergence

## Notes
Chosen over python-solvespace / py-slvs specifically to avoid their GPLv3
license. The solver is geometry-only (no build123d dependency); it emits
coordinates the agent uses in a part script rather than producing a shape.
