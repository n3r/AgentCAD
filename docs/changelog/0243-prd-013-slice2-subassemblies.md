# 0243 — 2026-08-19 — PRD-013 slice 2: cross-project sub-assemblies (read-only)

- **Commit:** pending
- **Date:** 2026-08-19
- **Author:** Nikita Fedorov

## Summary

Second slice of Assembly v2: an instance can reference ANOTHER project as a
sub-assembly. Resolution is depth-first, READ-ONLY, and rigid: the source is
opened read-only, its own patterns/sub-assemblies/mates are resolved into
source-local members, each member is rigid-placed at `parent * member_local`
(kernel `Location`) and namespaced `<parent_id>/<member_local_id>`, so two
nesting levels read `stand/engine/piston[0]`. The sharpest invariant —
`write_guard` never fires against a source and no authored state is written —
is structural (only read accessors touch a source) and asserted by a store-spy.
Cross-project cycles and mates to a non-exported connector are validation
errors carrying `details.cycle` / `details.interface`.

## Changes

- `core/mates.py`: `resolve_project(service, proj, _stack)` — the recursion
  primitive (expand + native mate pass, splitting native vs foreign members);
  `_expand_subassembly` (open source read-only, cycle detect by canonical path,
  interface referential check, recurse, namespace + rigid-place via a `rigid`
  op); `_source_name` resolves a name or an absolute path (read-only `open`).
- `core/model.py`: `InstanceSpec.origin_project` — a **transient** field (never
  persisted) naming the project a cross-project member's geometry builds from.
- `kernel/_mates_resolver.py`: `_rigid_place` grows a `rigid` operator
  (`parent * local`).
- `core/tools_structure.py`: `_resolved_instances` now delegates to
  `mates.resolve_project`; `get_assembly`/`check_interference`/`export_assembly`
  reimplemented in the wrapper to build each member from `origin_project or proj`
  (the core versions hardcode `proj`). New `add_subassembly` tool.
- `core/tools_motion.py`: `sweep_motion` reads the expanded assembly.

## Files

- `agentcad/core/mates.py`, `agentcad/core/model.py`,
  `agentcad/core/tools_structure.py`, `agentcad/core/tools_motion.py`,
  `agentcad/kernel/_mates_resolver.py`
- `tests/test_structure_subassembly.py` (new)

## Notes

- **Read-only safety (Decision 3.4):** the only write a source resolution can
  trigger is a derived, content-addressed `.cache/<key>.acm` when a source part
  is built for its mesh — never authored state, never through `write_guard`.
  Two tests prove it: a store-spy asserting zero `save_manifest`/`write_script`
  against `engine`/`stand`, and a guard installed to RAISE for the sources that
  never fires.
- Members are namespaced by the parent INSTANCE id, not the source name (two
  instances of one source stay distinct).
- **Deferred (documented):** the geometric resolution of an interface MATE
  (placing a sub-assembly unit by mating its exported connector) — MVP validates
  the exported-connector reference and places the unit by its explicit
  transform; the `src:<source>:<part>` build affinity is a warmth optimization
  only (the content-addressed cache is the correctness layer) and is not yet
  set precisely.
- Measured: `tests/test_structure_subassembly.py` 8 passed; the
  mates/motion/configs/patterns/subassembly set 51 passed.
