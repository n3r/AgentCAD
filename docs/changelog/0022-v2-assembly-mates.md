# 0022 — v2: assembly mates (connectors + declarative Joints)

- **Commit:** 64e37e5
- **Date:** 2026-08-09
- **Author:** Claude Fable 5

## Summary
Adds declarative assembly constraints: part scripts declare named connector
frames, instances carry a `mate` spec, and the worker resolves the mate graph
to concrete per-instance transforms via build123d Joints. The rest of the
service and the frontend keep consuming plain position/rotation_deg.

## Changes
- **Script contract addition** (`_mates_resolver.eval_connectors`): an optional
  `connectors(p, part)` function returns named frames typed `rigid` /
  `revolute` / `cylindrical`; `location` coerces from Location/Plane/(x,y,z)/
  ((pos),(rot)) and `axis` from Axis/((point),(dir)). Backward compatible —
  scripts without it declare no connectors.
- **Worker resolution** (`_mates_resolver.resolve_mates` + `order_mates`):
  validates the graph (duplicate ids, unknown/self `to_instance`, cycles with
  the cycle path), resolves in topological order (unmated instances are roots),
  and connects Joints on fresh location-proxies so cached shapes are never
  mutated. Moving-side connector must be rigid; anchor connector type carries
  the DOF (`angle`/`position` params); range violations become WorkerErrors.
- **New worker handlers** (`kernel/handlers/connectors.py`): `resolve_mates`
  (items → transforms) and `connectors` (introspect a script's connector names/
  types). Worker gains `build_shape_ns` (returns the script namespace) and
  exposes it in `WORKER_TOOLBOX`.
- **Service seam** (`core/mates.py`): `resolve()` marshals instances to the
  worker, forwards each script part's source/params, and writes resolved
  transforms back onto instance copies; KernelError → ValidationError.
  `service.set_assembly` now round-trips the `mate` field.
- **New tools** (`core/tools_mates.py`): `set_mate` (connector/to_instance/
  to_connector + optional angle_deg/offset_mm) and `clear_mate`; both persist
  via `set_instances` and publish `project_changed`.
- **New route** (`server/routes_assembly2.py`): `PATCH /projects/{proj}/
  assembly/instances/{id}` for single-instance transform/color write-back;
  returns 409 (ConflictError) when the instance is mate-driven.

## Files
- `agentcad/kernel/_mates_resolver.py` — connector coercion, mate-graph ordering, Joint resolution
- `agentcad/kernel/handlers/connectors.py` — `resolve_mates`/`connectors` worker handlers
- `agentcad/kernel/worker.py` — `build_shape_ns`, toolbox entry
- `agentcad/core/mates.py` — service-side resolve seam
- `agentcad/core/tools_mates.py` — `set_mate`/`clear_mate` tools
- `agentcad/core/service.py` — `mate` field on instance ingest
- `agentcad/server/routes_assembly2.py` — single-instance PATCH route
- `tests/test_mates.py` — connectors, rigid/chain resolution, cycle rejection, 409 on mate-driven PATCH

## Notes
Geometry stays isolated in the worker; the service/frontend never touch Joints.
Cycles are caught at write time because `set_assembly` resolves to return state.
