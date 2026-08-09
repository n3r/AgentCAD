# 0034 — Engine example v1: dressed 90° V4 (visual fidelity level)

- **Commit:** 36bfae9
- **Date:** 2026-08-09
- **Author:** Nikita Fedorov / Claude

## Summary
Adds a fifth bundled example, `examples/engine`: a 90° V4 engine with 13
parametric parts and 24 instances, the first example to use the v2
declarative-mates system. Committed on the `engine-example` branch as a
parked baseline: the parts are display-level solids and the next iteration
rebuilds them around real assemblable joints.

## Changes
- New example project `examples/engine` (project.json schema v2, README):
  block, crankshaft, pistons ×4, rods ×4, heads ×2, flywheel, oil pan,
  intake manifold, exhaust manifolds ×2, timing cover, damper pulley, oil
  filter, ignition coils ×4.
- Mates showcase: crank on a revolute `crank_axis` connector (`angle = 90 +
  crank angle`), flywheel chained to the rotating crank flange, heads and
  pan on block-derived seats; bank B's seat carries a 180° turn about the
  deck normal (the V4 layout's R_z(180) symmetry) so both intakes face the
  valley.
- Pistons/rods posed by explicit slider-crank kinematics at a 20° crank
  angle; clearances engineered (0.3 mm piston/bore, 0.25 mm big-end/pin,
  0.4 mm gaskets) and verified interference-free over all 276 pairs.
- Example lists updated in `README.md`, `docs/user-guide.md`,
  `docs/part-authoring.md`.

## Files
- `examples/engine/**` — the new example (14 files)
- `README.md`, `docs/user-guide.md`, `docs/part-authoring.md` — example
  lists now name five examples

## Notes
Known limitation, acknowledged in the commit message: parts are fused
primitive compositions with no fastener joints, split lines (main caps, rod
caps), or mating hole patterns — a 3D-printed set could not be assembled.
The follow-up on this branch rebuilds the example joint-first.
