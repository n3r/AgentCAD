# 0044 — FEM tiers: modal and steady-state thermal analysis

- **Commit:** pending
- **Date:** 2026-08-09
- **Author:** Claude (with Nikita Fedorov)

## Summary

The optional `[fem]` tier grows beyond single-part linear statics (roadmap
"Higher-fidelity FEM"): modal analysis (natural frequencies, clamped or
free-free) and steady-state thermal conduction, behind the same
extra/gating so the core install stays light and the suite stays green
without the dependencies.

## Changes

- **`_fem_impl.py`**: the validated STEP→gmsh(subprocess)→meshio→scikit-fem
  path factored into shared helpers (gmsh settings byte-identical;
  `run_fem_static` unchanged), plus:
  - `run_fem_modal` — P2 vector elasticity K + consistent mass M in the
    mm–N–MPa–tonne–s system; `eigsh` shift-invert (`sigma=0` clamped;
    free-free uses a small negative sigma scaled from K/M diagonals so
    ordering stays ascending, then drops rigid modes below `1e-6·λ_max`
    with a note). Returns ascending `frequencies_hz`.
  - `run_fem_thermal` — P2 scalar conduction with inhomogeneous Dirichlet
    BCs; `flux_w` from the reaction `K@T` summed over hot-face DOFs (the
    mm→m unit derivation is documented in-code).
- **Handlers**: `fem_modal`/`fem_thermal` behind the same `fem_available()`
  gate + install-hint error as `fem_static`.
- **Tools**: registered inside the existing availability block; E defaults
  from the material's `E_gpa`, density always from the material resolver,
  k from `k_w_m_k` (missing → ValidationError naming the fix); n_modes 1..24.
- **Routes**: `POST .../fem/modal` and `.../fem/thermal` sharing one
  501-fallback helper with the static route.
- Docs: agent-api FEM section (both rows + route list).

## Files

- `agentcad/kernel/_fem_impl.py`, `agentcad/kernel/handlers/fem.py`
- `agentcad/core/tools_analysis.py`, `agentcad/server/routes_analysis.py`
- `tests/test_analysis.py` — six new tests: registry gating both ways,
  501 shapes, cantilever modal vs Euler-Bernoulli (achieved −0.26% at the
  default 3 mm mesh; degenerate square-section pair split 0.000%),
  free-free rigid-mode omission, thermal bar flux vs kAΔT/L (exact — the
  linear field is in P2's span), missing-conductivity error
- `docs/agent-api.md`

## Notes

Validated in a scratch venv with the extra installed (11 passed there);
the base suite runs with the FEM tests skipped. Known softness: ARPACK
convergence for free-free on very soft/large parts is untested — a
retry-with-smaller-sigma fallback is a noted follow-up. Reaction-flux
counting is exact for opposite faces, approximate if hot/cold faces touch.
