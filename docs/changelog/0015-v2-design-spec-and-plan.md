# 0015 — v2 design spec and implementation plan (spike-validated decisions)

- **Commit:** d451c89
- **Date:** 2026-08-09
- **Author:** Claude Fable 5

## Summary
Adds the two governing documents for AgentCAD v2 — a design specification of
per-limitation decisions (each proven by a running spike before adoption) and
a wave-based implementation plan that keeps v1 frozen and lands v2 as additive
vertical modules. Docs only; no code changes.

## Changes
- New spec `2026-08-09-agentcad-v2-design.md`: one spike-validated decision per
  triaged limitation — reference imports (STEP/BREP/STL with STL boolean ban),
  a first-party scipy sketch solver (rejecting GPL solvespace/py-slvs),
  `bd_warehouse` threads, HLR+SVG drawings, robustness toolkit + Error Doctor,
  declarative mates, a kernel pool, TransformControls gizmos, materials v2, and
  tier-1 analysis + optional FEM behind `agentcad[fem]`.
- Records two upstream bugs found by spikes and fixed in our layer: nested-
  `Compound.volume` undercount (sum `shape.solids()`) and re-export-after-
  Compound-adoption failure (hand out moved copies).
- Defines binding contracts: manifest schema_version 2 (part `kind`/`source`,
  instance `mate`, project `materials`), new HTTP surface (imports upload,
  instance PATCH, materials GET/PUT, drawing/analyze/fem/sketch endpoints), the
  tool registry growing 17 → 27, and optional `connectors()` script contract.
- New plan `2026-08-09-agentcad-v2-implementation.md`: Wave 0 orchestrator
  scaffolding, Wave 1 nine parallel backend verticals with disjoint file
  ownership, Wave 2 frontend/docs/examples, Wave 3 adversarial review + final
  verification; each wave ends green with one commit.

## Files
- `docs/superpowers/specs/2026-08-09-agentcad-v2-design.md` — v2 design decisions, contracts, testing bar
- `docs/superpowers/plans/2026-08-09-agentcad-v2-implementation.md` — wave plan, per-agent scope matrix

## Notes
Establishes the extension-point architecture (handler/tool/route packs, three
service seams) that the subsequent commits implement. The spec's tool/route
names are the authoritative contract the Wave-1 packs must match verbatim.
