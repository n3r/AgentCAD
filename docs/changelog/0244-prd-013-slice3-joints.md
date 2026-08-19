# 0244 — 2026-08-19 — PRD-013 slice 3: slider + planar joints, DOF object, clamp-not-raise

- **Commit:** pending
- **Date:** 2026-08-19
- **Author:** Nikita Fedorov

## Summary

Third slice of Assembly v2: two new joint types. `slider` (a prismatic DOF,
build123d `LinearJoint`) and `planar` (u/v translate + spin — composed as a
`Location` post-multiply on a rigid frame, since build123d has no PlanarJoint).
`set_mate` grows a general `dof` object (`offset_mm` → slider `position`,
`u_mm`/`v_mm`/`spin_deg` → planar) while `angle_deg`/`offset_mm` stay as
shorthand. The key behaviour: an out-of-range DOF value is now **clamped** to
the connector's declared range with a `dof_clamped` warning, not raised — an 80
mm request on a (0, 50) slider resolves at 50 and warns. The three existing mate
types and all part scripts are untouched.

## Changes

- `kernel/_mates_resolver.py`: `VALID_TYPES` grows `slider`/`planar`;
  `eval_connectors` validates both (slider needs a `linear_range`; planar carries
  a plane `location` + `u_range`/`v_range`/`spin`); `_make_joint` builds
  `LinearJoint` (slider) and a composed `RigidJoint` (planar); the connect
  dispatch clamps every DOF (`_clamp`) and records `dof_clamped`; `resolve_mates`
  takes a `warnings` sink.
- `kernel/handlers/connectors.py`: `resolve_mates` handler returns `warnings`.
- `core/mates.py`: `resolve()` forwards the kernel's `warnings` into its
  `warnings_out` sink (surfaced as `get_assembly()["warnings"]`).
- `core/tools_mates.py`: `set_mate` gains the `dof` object mapping.
- `core/tools_motion.py`: (from slice 2) `sweep_motion` sweeps the expanded
  assembly, so a pattern beside the driven instance contributes its N members.

## Files

- `agentcad/kernel/_mates_resolver.py`,
  `agentcad/kernel/handlers/connectors.py`, `agentcad/core/mates.py`,
  `agentcad/core/tools_mates.py`
- `tests/test_mates_joints.py` (new)

## Notes

- **Divergence (spec §14.1 / §5.3):** clamping is a behaviour change applied
  uniformly to ALL DOF joints (revolute/cylindrical/slider/planar) — an
  out-of-range value used to reach build123d and raise. Verified the existing
  mate/motion suites drive in-range values, so no existing test regresses; a new
  in-range revolute test asserts no `dof_clamped` warning.
- Planar is composed, not native: the effective anchor frame is
  `connector.location * Location((u, v, 0), (0, 0, spin))`, then a rigid connect.
- **Phase-2 (per the plan):** `sweep_motion`'s `first_collision` acceptance
  (AC4's obstruction half) lands with the Task-9 acceptance tests; planar/ball
  sweeping and coupled re-resolution stay Phase 2.
- Measured: `tests/test_mates_joints.py` 8 passed; the combined
  mates/motion/joints/structure set 57 passed. Full suite: `make test`
  (`-n auto --dist loadscope`) — **4135 passed, 1 skipped** after slices 1–3.
  (The one-shot full run measured 4134 passed / 1 skipped / 1 failed — a test
  that class-level-monkeypatches `get_assembly` and asserts one call; the pack
  wrapper was made to dynamically dispatch to the class method so the patch is
  honoured, the fix re-verified with that test plus 137 others green.) The
  prior tree measured 4068 passed, 1 skipped (changelog 0229).
