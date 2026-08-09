# 0009 — Fix assembly bbox rollup to intrinsic XYZ Euler

- **Commit:** 1a86867
- **Date:** 2026-08-08
- **Author:** Claude Fable 5

## Summary
Corrects the rotation order used when rolling up an assembly's bounding box so it
matches the kernel (build123d `Location`) and the viewer (`THREE.Euler` "XYZ").
The previous order transformed bboxes differently than parts were actually placed,
so multi-axis rotations produced a wrong assembly bbox.

## Changes
- `_apply_transform` in `agentcad/core/service.py` now applies intrinsic XYZ Euler
  rotation as `R = Rx · Ry · Rz` — the Z rotation hits the vector first, then Y,
  then X — before translating. Previously the code rotated about X first, then Y,
  then Z, which is the opposite (extrinsic/reversed) composition and disagreed with
  how build123d and Three.js interpret `rotation_deg`.
- Behavior change is confined to the bbox rollup math; no API/schema change. The
  fix only affects instances with rotation about more than one axis (single-axis
  cases were already order-independent).

## Files
- `agentcad/core/service.py` — reordered the three axis rotations in
  `_apply_transform`; docstring now states `R = Rx · Ry · Rz`
- `tests/test_service.py` — new `test_assembly_rollup_multi_axis_rotation_intrinsic_xyz`
  places an X-elongated box with `rotation_deg=[90,0,90]` and asserts the rolled-up
  extents land on Z (20mm) / X (4mm) / Y (2mm), matching the kernel

## Notes
The new regression test is the guard: it exercises a rotation whose result differs
between the two orderings, so a future reversion would fail. The existing
single-axis (45° about Z) test kept passing throughout, which is why the bug
survived until now.
