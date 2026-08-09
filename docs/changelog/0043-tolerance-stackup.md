# 0043 — Tolerance stack-ups over the mate graph (worst-case + RSS)

- **Commit:** pending
- **Date:** 2026-08-09
- **Author:** Claude (with Nikita Fedorov)

## Summary

The other half of the PMI story (roadmap "Tolerance stack-ups"): a
`tolerance_stackup` tool computes 1-D worst-case and statistical (RSS) stacks
along a world axis across the unique mate-forest path between two assembly
instances, sourcing tolerances from the parts' PMI linear dims.

## Changes

- **Tool** `tolerance_stackup(project, axis, from_instance, to_instance)`
  (`tools_stackup.py` pack, pure server-side math): walks each endpoint's
  mate chain to its root (cycle/dangling guards), finds the lowest common
  ancestor, and builds the up-then-down path (endpoints included;
  `from == to` analyzes that instance alone). Each path instance contributes
  its part's linear PMI dims matching the axis (x=`width`, y=`depth`,
  z=`height`); worst case sums plus/minus linearly, RSS is root-sum-of-squares
  **per dim** (statistically correct when a part carries several dims).
  `nominal_mm` is the resolved axis distance (mates honored via
  `_resolved_instances`). Path instances without matching dims contribute
  zero with a warning. Disconnected instances → validation error carrying
  both chains.

## Files

- `agentcad/core/tools_stackup.py` — the tool pack
- `tests/test_stackup.py` — 8 tests: 3-chain worst/RSS totals, no-dims axis
  (warnings), direction symmetry, self-stack, disconnection, unknown
  instance, and a branched-forest LCA case with asymmetric ±0.2 dims
  (worst 0.5, RSS √0.07, nominal cross-checked against get_assembly)
- `docs/agent-api.md` — tool row

## Notes

Nominal distances were verified against build123d's rigid-rigid `connect_to`
semantics (no flip). The tool reads the `service._resolved_instances` seam —
the same access pattern the service uses internally for assembly reads.
