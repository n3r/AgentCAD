# 0034 — v3 roadmap execution plan

- **Commit:** pending
- **Date:** 2026-08-09
- **Author:** Claude (with Nikita Fedorov)

## Summary

Adds the implementation plan for building out every remaining item in
`docs/roadmap.md` (v3): 18 slices across five waves, each landing as additive
packs behind the v2 extension points, plus a close-out docs sweep.

## Changes

- New plan document covering: non-numeric PARAMS, per-solid semantics, sheet
  metal + flat pattern, PMI/GD&T + stack-ups, motion from mates, surfacing +
  curvature, FEM modal/thermal tiers, turn-locking + multi-agent sessions,
  git-backed history, mesh LOD, vision feedback rendering, direct-manipulation
  gap closure, GUI sketcher + push/pull, sandbox-exec confinement, Win/Linux
  CI, single-binary packaging, and the final roadmap refresh.
- Binding interfaces (schemas, tool names, return shapes) are specified per
  slice so implementers cannot drift; frozen v1/v2 surfaces are called out.

## Files

- `docs/superpowers/plans/2026-08-09-agentcad-v3-roadmap.md` — the plan.

## Notes

Ordering minimizes shared-file contention (worker param validation and the
inspector params pane change once, early). ACM1 stays frozen; mesh-adjacent
slices use sidecar files. CI proof on Windows/Linux requires a push to GitHub;
the workflow is written to be correct on all three OSes.
