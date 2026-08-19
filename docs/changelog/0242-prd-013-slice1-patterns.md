# 0242 — 2026-08-19 — PRD-013 slice 1: instance patterns + the single expansion point

- **Commit:** pending
- **Date:** 2026-08-19
- **Author:** Nikita Fedorov

## Summary

First slice of Assembly v2 (PRD-013 MVP). Adds the additive manifest schema for
instance `pattern`/`assembly` (old files load unchanged, `SCHEMA_VERSION`
stays 2), the key-wise `assembly.interface`/`couplings` merge with a
`structure_problems` referential backstop, and — the load-bearing piece —
`mates.expand`: the ONE server-side point that flattens a patterned base
instance into `count` concrete members `<id>[0..count-1]` (replace-not-add).
Because every consumer already reads `service._resolved_instances`, mass
roll-ups, interference candidates and the flattened `get_assembly` view all
recount N members from that one expansion; changing `count` updates all three.

## Changes

- `core/model.py`: `InstanceSpec` gains `pattern`/`assembly` (both optional);
  `part` now defaults to `""` for a sub-assembly reference; `to_manifest` emits
  the new keys only when truthy and drops `part` for an `assembly` instance.
- `core/project.py`: `instances()` reads the new keys; `set_instances`
  validates the pattern spec and the "part XOR assembly" rule; new
  `_validate_pattern`/`_validate_assembly_ref` helpers; new
  `assembly_interface`/`set_assembly_interface` store accessors (referential
  check at write time).
- `core/manifest_merge.py`: `_ASSEMBLY_ENTRY_DICTS = ("interface","couplings")`
  routed through `_merge_entry_dict` (per-name atomic) in `_merge_assembly`, plus
  the matching `_write_path` branch; new `structure_problems(manifest)` reporting
  `dangling_interface`/`dangling_coupling` (both warnings).
- `core/merge.py`: `structure_problems` wired next to `config_problems`, its
  messages appended to `report["warnings"]`.
- `core/mates.py`: new `expand(service, proj, instances) -> (flat, warnings)`
  (linear composed server-side, polar via the kernel); `resolve()` grows an
  optional `warnings_out` sink (backward compatible).
- `kernel/_mates_resolver.py`: `resolve_assembly(operators)` + `_rigid_place` —
  shape-free `Location` composition (linear/polar/rigid) in the one Euler
  convention.
- `kernel/handlers/connectors.py`: exposes the `resolve_assembly` handler.
- `core/tools_structure.py` (**new** pack): installs the expansion by wrapping
  `service._resolved_instances` (trigger grows to mate OR pattern OR assembly),
  `service.set_assembly` (carry `pattern`/`assembly`), and `service.get_assembly`
  (adds `tree` + `warnings`) — the sanctioned "wrapper, not a service.py edit"
  idiom. New tools `set_pattern`, `set_assembly_interface`.

## Files

- `agentcad/core/model.py`, `agentcad/core/project.py`,
  `agentcad/core/manifest_merge.py`, `agentcad/core/merge.py`,
  `agentcad/core/mates.py`, `agentcad/core/tools_structure.py`,
  `agentcad/kernel/_mates_resolver.py`,
  `agentcad/kernel/handlers/connectors.py`
- `tests/test_structure_schema.py`, `tests/test_structure_patterns.py` (new)

## Notes

- **Divergence (spec §14.3):** `set_pattern` is added as a focused verb so a
  `count` edit is one call, not a full-list resend.
- **Divergence (spec §14.2):** the interface/couplings merge needed the small
  key-wise code change; instance `pattern`/`assembly` merge whole-value per id
  with no merge change.
- The core `set_assembly`/`get_assembly`/`_resolved_instances` are extended by
  pack wrappers (like `tools_holes` wraps `service.get_part`), NOT by editing
  `service.py`. The wrappers install when any pack is registered
  (`build_registry`); a bare `make_test_service` keeps v1 behaviour.
- Polar members re-aim (rotation about the axis composed onto the base pose). A
  polar pattern on a *mated* base cannot re-solve the mate per member, so it
  falls back to the rigid image and emits `pattern_polar_offaxis` (spec §2.4).
- Measured: `tests/test_structure_patterns.py` 9 passed,
  `tests/test_structure_schema.py` 15 passed; the mates/motion/configs/merge
  regression set 205 passed. Prior tree measured 4068 passed, 1 skipped
  (changelog 0229).
