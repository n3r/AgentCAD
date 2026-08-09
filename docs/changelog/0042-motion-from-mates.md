# 0042 — Motion from mates: DOF sweeps with moving-body interference

- **Commit:** pending
- **Date:** 2026-08-09
- **Author:** Claude (with Nikita Fedorov)

## Summary

Mates now drive motion, not just placement (roadmap "Motion from mates"): a
`motion_sweep` kernel method re-resolves the mate graph at each sampled DOF
value and boolean-checks every instance pair, surfaced as the `sweep_motion`
tool and a Motion row in the placement card that animates the sweep in the
viewport.

## Changes

- **Kernel handler** `handlers/motion.py` (new): method `motion_sweep` —
  `{items, driven: {instance, param: angle|position, values}, min_volume}`.
  Builds every distinct shape once per request (shared with the mate
  resolver), overrides the driven mate param per value, resolves via the
  existing `resolve_mates`, records a frame (every instance's resolved
  transform) and runs the pairwise `&` interference (mesh-kind items diverted
  to `skipped_mesh` once — the STL/OCCT segfault guard). Returns
  `{samples, frames, clear, first_collision, skipped_mesh}`; values capped
  at 60.
- **Tool** `sweep_motion(project, instance, angle_range|offset_range,
  samples=12, min_volume)` (`tools_motion.py` pack): exactly-one-of range
  validation, samples 2..60, instance-has-mate check; items built via
  `service._shape_item` with mates attached; inclusive linspace;
  `timeout_s=300`, affinity=project.
- **Viewport/placement UI**: `viewport.setInstanceTransform` export; mated
  instances get a Motion row (from/to inputs + Sweep) that calls the tool,
  animates `frames` at 30 ms/frame, restores the real pose, and toasts
  "Motion clear through range" or "Collision at N°". Re-entrancy guarded.
- Docs: agent-api `sweep_motion` row; user-guide "Motion from mates" section.

## Files

- `agentcad/kernel/handlers/motion.py`, `agentcad/core/tools_motion.py`
- `frontend/js/placement.js`, `frontend/js/viewport.js`, `frontend/css/app.css`
- `tests/test_motion.py` — 10 tests around an analytic hinged-flap-vs-wall
  scenario (first contact at θ* = 43.216°, bracketing pinned by its own test)
- `docs/agent-api.md`, `docs/user-guide.md`

## Notes

Connector-type validation is delegated to the resolver (a kernel `connectors`
round-trip per call wasn't worth it): sweeping a rigid mate surfaces the
resolver's own contract error. `first_collision` is first-in-values order, so
a descending range reports the first collision along the motion direction.
The pairwise loop has no bbox prefilter (parity with `check_interference`);
the 60-sample cap and 300 s timeout bound the cost.
