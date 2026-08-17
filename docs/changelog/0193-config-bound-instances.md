# 0193 — PRD-012 slice 4: an assembly instance bound to a configuration, end to end

- **Commit:** pending
- **Date:** 2026-08-17
- **Author:** Claude

## Summary
Design Decision 7's resolution half: every per-instance geometry site now
obtains `service._record_for(proj, inst.part, inst.config)` and hands that
derived record to the existing record-driven helper, so two instances of one
part can be two sizes in one assembly — with two masses, two cache keys and two
meshes. `get_assembly` builds a bound instance through `_ensure_config_built`
and publishes every built instance's `mesh_key`; `render_view` grows `config`;
the packet's assembly delta reports rebindings; `tolerance_stackup` names the
part/configuration mismatch it cannot resolve.

## Changes
- `service.get_assembly`: a bound instance is built with
  `_ensure_config_built(proj, inst.part, inst.config)` (memoized per
  configuration, silent on a hit — one `_status` slot per part would miss on
  alternate reads and republish `rebuild_finished` forever), an unbound one
  still with `_ensure_built`. Every **built** instance entry gains
  `mesh_key = built["cache_key"]`, bound or not: that is the content-addressed
  handle the browser fetches assembly geometry with (slice 3's
  `GET /projects/{p}/meshes/{key}`). An unbound instance's `mesh_key` is
  exactly `mesh_info(proj, part)["key"]`.
- `service.check_interference` and `service.export_assembly` build their
  `_shape_item` from `_record_for(proj, inst.part, inst.config)` instead of
  `store.get_part`, so an overlap and an export are measured at the size each
  instance actually is.
- `mates.resolve`: same substitution — a connector position routinely rides a
  parameter, so a bound instance resolves its connectors from its
  configuration's geometry. `item["params"]` is already
  `record.effective_params`, which is now the pure configuration map. The
  kernel's `conn_cache` is keyed by instance id, so nothing in the resolver
  changes.
- `tools_motion.sweep_motion`: same substitution, so a sweep re-resolves the
  mate graph at the bound sizes.
- `specs._instance_item` and `specs._project_key` (the assembly-tier key):
  both go through `_record_for` with `getattr(instance, "config", None)`. "A
  spec cache key covers every input the check reads" — an assembly verdict
  measured at S is no longer reused at L. Nothing new enters the key payload
  (the instance's cache key already hashes `effective_params`), so a
  configuration-free project's key is byte-identical and two configurations
  with the same override map legitimately share it.
- `tools_stackup.compute_stackup`: one warning per config-bound path instance —
  `instance <id> (part <p>) is bound to configuration '<c>': tolerances are per
  part, the nominal is per configuration`. Per-configuration PMI is a stated
  non-goal, so the mixed answer is named rather than silently produced; the
  tool description says so too.
- `tools_vision.render_view` gains `config` (moved here from slice 6): the part
  path validates through `_record_for` **before** any build, reads
  `ensure_mesh(project, part_id, config=config)`, writes
  `renders/<part>_<config>_<view>.png` (base naming unchanged) and echoes
  `config` in the result. The assembly path renders each instance at its own
  binding (`ensure_mesh(config=inst.config)`), so one image can mix sizes; the
  `skipped` semantics are unchanged. `config` without `part_id` is refused —
  an assembly render takes each instance's own binding, and silently ignoring
  the argument would hand back a different picture than the one asked for.
  `config` is added to the tool schema.
- `packet._render_assembly` renders each instance with
  `ensure_mesh(config=inst.config)`. `packet.assembly_delta` gains
  `configs_changed: [{id, old, new}]` (plain `!=` on a name-or-absent field)
  and counts it towards `changed`, so a rebinding whose mass happens not to
  move is still reported as a change — `config` is treated exactly like `mate`.
  `_render_part` is deliberately untouched: a part render is the working state,
  where `active_config` already resolves through the manifest.

## Files
- `agentcad/core/service.py` — `get_assembly` (config builds + `mesh_key`),
  `check_interference`, `export_assembly`
- `agentcad/core/mates.py` — `resolve` builds its items from the derived record
- `agentcad/core/tools_motion.py` — `sweep_motion` items from the derived record
- `agentcad/core/specs.py` — `_instance_item`, `_project_key` (assembly key)
- `agentcad/core/tools_stackup.py` — the configuration warning row, docstring,
  tool description
- `agentcad/core/tools_vision.py` — `render_view {config?}`, assembly path per
  binding, filename, schema, module docstring
- `agentcad/core/packet.py` — assembly render per binding, `configs_changed`
- `tests/test_configs_assembly.py` — new: four kernel-free tests (spec instance
  item, assembly spec key, stack-up warning, assembly delta) plus
  `TestBoundAssembly`, which builds one flange size family and one mated hinge
  for real

## Notes
- The interference test is discriminating in both directions: the two S
  instances are 10 mm apart at S and would overlap by 30 mm at the default
  size, so dropping the binding turns one reported pair into two. Before the
  fix it reported `[{s1, s2}, {l1, l2}]`.
- The mate test's anchor carries a revolute connector at `t / 2 + 1`, a
  *configured* parameter, so rebinding the anchor lifts the mated flap by
  exactly `(40 - 10) / 2 = 15 mm` — a resolution that ignored the binding could
  not produce that number.
- `get_assembly` lets a `ValidationError` from an inconsistent binding (a
  configuration the part no longer declares) propagate, exactly as it already
  does for an instance naming an unknown part: the store refuses both on write,
  and inventing a per-entry error shape for a manifest the store cannot produce
  would be a second meaning for `state: "error"`.
- Focused suite: `uv run pytest tests/test_configs_assembly.py tests/test_mates.py
  tests/test_motion.py tests/test_packet.py tests/test_specs.py
  tests/test_service.py tests/test_analysis.py tests/test_render.py -q` —
  175 passed, 7 skipped. `tests/test_configs_assembly.py` alone: 15 passed.
- Full suite: `make test` — 3472 passed, 7 skipped (run by the controller over slices 4 and 6 together, with slice 7's frontend edits already in the tree; the one red row was `tests/test_presence.py::test_the_browser_mints_and_sends_a_per_profile_identity`, the hand-rolled-fetch `X-Agent-Id` count pin that slice 7's `getMeshByKey` bumps from 5 to 6 — slice 7 updates the pin).

## Fix round 1 (review of slice 4)
- `render_view`: dropped the redundant `_record_for` pre-call on the part path
  (`_ensure_config_built` resolves the record before any build, so
  `ensure_mesh(config=)` already raises the same refusal); widened the assembly
  loop's `except` to `(KernelError, AppError)` so an instance bound to a
  configuration its part no longer declares is *skipped* like any other
  unbuildable one instead of failing the whole image — the pair
  `packet._render_assembly` already catches.
- `specs._project_key`: `ValidationError` joins the inner
  `except (NotFoundError, OSError)`. A stale binding now degrades that one row
  to `part_key = "missing"`; escaping reached `_project_block`'s bare
  `except Exception` and evaluated the whole project's assembly tier uncached.
- `specs._project_key`, `specs._instance_item`, `tools_stackup.compute_stackup`:
  `instance.config` / `by_id[iid].config` instead of `getattr(…, "config",
  None)` — `InstanceSpec` always carries the field, and every other call site
  reads it directly.
- Two covering tests in `tests/test_configs_assembly.py`
  (`test_a_stale_binding_degrades_one_assembly_key_row`,
  `TestBoundAssembly::test_render_view_skips_an_instance_whose_binding_went_stale`)
  with a `_stale_binding` helper that writes the binding straight to the
  manifest, because `set_instances` refuses it.
- Not changed: `packet._summary` ignoring a rebinding-only assembly change is
  pre-existing and deferred by the reviewer.
