"""Tool pack: assembly structure (PRD-013) — patterns, sub-assemblies, the
exported interface, and the SINGLE expansion point wired into the service.

This pack installs the assembly-v2 expansion by WRAPPING two service seams
(the sanctioned "wrapper, not a service.py edit" idiom — cf. ``tools_holes``
wrapping ``service.get_part``):

* ``service._resolved_instances`` — grows its trigger from "any mate" to "any
  mate OR pattern OR assembly" and runs ``mates.expand`` (flatten) THEN the
  mate pass. Every consumer already reads through this method, so mass
  roll-ups, interference, export, stackup, specs, checks and the packet all see
  N members from ONE expansion.
* ``service.get_assembly`` — adds a ``tree`` view (patterns/sub-assemblies as
  one node each, for the sidebar and reviewable diffs) and the resolved-pass
  ``warnings`` (polar off-axis, DOF clamp) beside the existing flattened view.

Load order: ``tools_structure`` sorts after ``tools_solids``/``tools_specs``/
``tools_stackup`` and before ``tools_undo``/``tools_versioning``. It reads NO
cross-pack seam (``service.branches``/``store.write_guard``) at registration —
the wrappers and tools touch only the store, under the ordinary
``project_changed`` publish. It installs no merge gate.
"""

from __future__ import annotations

import functools
import math

from . import mates
from .model import InstanceSpec, NotFoundError, ValidationError, validate_vec3
from .project import _validate_pattern
from .service import _apply_transform, _bbox_corners
from .tools import Tool, schema

_WRAPPED = "_agentcad_structure_wrapped"


def _owner(service, proj, inst):
    """The project a (possibly cross-project) member's geometry is built from."""
    return getattr(inst, "origin_project", None) or proj


def _cross_project_assembly(service, proj, instances):
    """The core get_assembly loop, but each member builds from its SOURCE
    project (`origin_project`) — used only when a sub-assembly is present."""
    detail = []
    total_mass = 0.0
    bounds_min = [math.inf] * 3
    bounds_max = [-math.inf] * 3
    for inst in instances:
        owner = _owner(service, proj, inst)
        entry = inst.to_manifest()
        built = (service._ensure_config_built(owner, inst.part, inst.config)
                 if inst.config else service._ensure_built(owner, inst.part))
        if built["ok"]:
            metrics = built["metrics"]
            entry["mass_g"] = metrics["mass_g"]
            entry["state"] = "ok"
            entry["mesh_key"] = built["cache_key"]
            total_mass += metrics["mass_g"]
            for corner in _bbox_corners(metrics["bbox"]):
                world = _apply_transform(corner, inst.position, inst.rotation_deg)
                for axis in range(3):
                    bounds_min[axis] = min(bounds_min[axis], world[axis])
                    bounds_max[axis] = max(bounds_max[axis], world[axis])
        else:
            entry["state"] = "error"
            entry["error"] = built["error"]
        detail.append(entry)
    bbox = ({"min": bounds_min, "max": bounds_max}
            if math.isfinite(bounds_min[0]) else None)
    return {"instances": detail, "total_mass_g": total_mass, "bbox": bbox}


def _tree_view(service, proj: str) -> list[dict]:
    """The raw (un-expanded) instance list as sidebar nodes: a pattern is ONE
    node with a ``count`` badge, a sub-assembly ONE node naming its source."""
    nodes = []
    for inst in service.store.instances(proj):
        if inst.assembly is not None:
            node = {"id": inst.id, "kind": "assembly",
                    "project": inst.assembly.get("project")}
            if inst.pattern is not None:
                node["count"] = int(inst.pattern["count"])
                node["pattern_kind"] = inst.pattern["kind"]
        elif inst.pattern is not None:
            node = {"id": inst.id, "kind": inst.pattern["kind"],
                    "count": int(inst.pattern["count"]), "part": inst.part}
        else:
            node = {"id": inst.id, "kind": "part", "part": inst.part}
        nodes.append(node)
    return nodes


def _install_expansion(service) -> None:
    if getattr(service, "_assembly_warnings", None) is None:
        service._assembly_warnings = {}

    resolved = service._resolved_instances
    if not getattr(resolved, _WRAPPED, False):

        def _resolved_instances(proj, timeout_s=None):
            # The single expansion point: flatten patterns + sub-assemblies,
            # then run the mate pass (mates.resolve_project). A flat,
            # single-level project with no mates/patterns/sub-assemblies passes
            # through untouched — byte-identical v1 fast path (AC8).
            flat, warns = mates.resolve_project(service, proj,
                                                timeout_s=timeout_s)
            service._assembly_warnings[proj] = warns
            return flat

        setattr(_resolved_instances, _WRAPPED, True)
        service._resolved_instances = _resolved_instances

    set_assembly = service.set_assembly
    if not getattr(set_assembly, _WRAPPED, False):

        @functools.wraps(set_assembly)
        def _set_assembly(proj, instances):
            # The core set_assembly predates PRD-013 and drops `pattern`/
            # `assembly`; rebuild the specs so a full-replace carries them.
            specs = [
                InstanceSpec(
                    id=item.get("id", ""),
                    part=item.get("part", ""),
                    position=validate_vec3(item.get("position", [0, 0, 0]),
                                           "position"),
                    rotation_deg=validate_vec3(
                        item.get("rotation_deg", [0, 0, 0]), "rotation_deg"),
                    color=item.get("color"),
                    mate=item.get("mate"),
                    config=item.get("config"),
                    pattern=item.get("pattern"),
                    assembly=item.get("assembly"),
                )
                for item in instances
            ]
            with service._lock:
                service.store.set_instances(proj, specs)
            service.bus.publish({"type": "project_changed", "project": proj})
            return service.get_assembly(proj)

        setattr(_set_assembly, _WRAPPED, True)
        service.set_assembly = _set_assembly

    get_assembly = service.get_assembly
    if not getattr(get_assembly, _WRAPPED, False):

        @functools.wraps(get_assembly)
        def _get_assembly(proj):
            # Same-project assemblies delegate to the CORE get_assembly (via
            # dynamic class dispatch, so a test's class-level monkeypatch is
            # still honoured); only a cross-project sub-assembly needs the
            # per-origin build loop the core version cannot do (it hardcodes
            # `proj`). Either way, add the tree + warnings views.
            instances = service._resolved_instances(proj)
            if any(_owner(service, proj, i) != proj for i in instances):
                result = _cross_project_assembly(service, proj, instances)
            else:
                result = type(service).get_assembly(service, proj)
            if isinstance(result, dict):
                result["tree"] = _tree_view(service, proj)
                result["warnings"] = list(
                    service._assembly_warnings.get(proj) or [])
            return result

        setattr(_get_assembly, _WRAPPED, True)
        service.get_assembly = _get_assembly

    check_interference = service.check_interference
    if not getattr(check_interference, _WRAPPED, False):

        @functools.wraps(check_interference)
        def _check_interference(proj, min_volume=0.001, timeout_s=None):
            resolved = service._resolved_instances(proj, timeout_s=timeout_s)
            items = []
            for inst in resolved:
                owner = _owner(service, proj, inst)
                record = service._record_for(owner, inst.part, inst.config)
                item = service._shape_item(owner, record, inst)
                item["name"] = inst.id
                items.append(item)
            if len(items) < 2:
                return {"pairs": [], "checked": len(items)}
            result = service.kernel.request(
                "interference", {"items": items, "min_volume": min_volume},
                timeout_s=600.0 if timeout_s is None else timeout_s,
            )
            out = {"pairs": result["pairs"], "checked": len(items)}
            if result.get("skipped_mesh"):
                out["skipped_mesh"] = result["skipped_mesh"]
            return out

        setattr(_check_interference, _WRAPPED, True)
        service.check_interference = _check_interference

    export_assembly = service.export_assembly
    if not getattr(export_assembly, _WRAPPED, False):

        @functools.wraps(export_assembly)
        def _export_assembly(proj, fmt):
            if fmt not in ("step", "stl"):
                raise ValidationError(
                    "assembly export supports formats: step, stl")
            items = []
            for inst in service._resolved_instances(proj):
                owner = _owner(service, proj, inst)
                record = service._record_for(owner, inst.part, inst.config)
                items.append(service._shape_item(owner, record, inst))
            if not items:
                raise ValidationError("assembly has no instances to export")
            out = service.store.exports_dir(proj) / f"assembly.{fmt}"
            return service.kernel.request(
                "export_assembly",
                {"items": items, "format": fmt, "out_path": str(out)},
                timeout_s=300.0,
            )

        setattr(_export_assembly, _WRAPPED, True)
        service.export_assembly = _export_assembly


def register(registry, service) -> None:
    _install_expansion(service)

    def set_pattern(project: str, instance: str, pattern=None) -> dict:
        if pattern is not None:
            _validate_pattern(instance, pattern)
        instances = service.store.instances(project)
        found = False
        for inst in instances:
            if inst.id == instance:
                inst.pattern = pattern
                found = True
        if not found:
            raise NotFoundError(f"instance {instance!r} not found")
        service.store.set_instances(project, instances)
        service.bus.publish({"type": "project_changed", "project": project})
        return service.get_assembly(project)

    registry.register(Tool(
        "set_pattern",
        "Attach (or clear) a repeat pattern on an assembly instance. "
        "pattern is {kind: 'linear'|'polar', count>=1, step_mm (linear), "
        "angle_step_deg (polar), axis? [[px,py,pz],[dx,dy,dz]], center?}; pass "
        "pattern: null to clear it. The instance is replaced everywhere by "
        "count concrete members <id>[0..count-1] — mass, interference and the "
        "flattened view all recount from this one edit.",
        schema(
            {
                "project": {"type": "string"},
                "instance": {"type": "string"},
                "pattern": {"type": "object",
                            "description": "pattern spec, or null to clear"},
            },
            ["project", "instance"],
        ),
        set_pattern,
    ))

    def add_subassembly(project: str, id: str, source: str,
                        position=None, rotation_deg=None) -> dict:
        instances = service.store.instances(project)
        if any(i.id == id for i in instances):
            raise ValidationError(f"instance id {id!r} already exists")
        instances.append(InstanceSpec(
            id=id, assembly={"project": source},
            position=validate_vec3(position or [0, 0, 0], "position"),
            rotation_deg=validate_vec3(rotation_deg or [0, 0, 0],
                                       "rotation_deg"),
        ))
        service.store.set_instances(project, instances)
        service.bus.publish({"type": "project_changed", "project": project})
        return service.get_assembly(project)

    registry.register(Tool(
        "add_subassembly",
        "Instance another project as a sub-assembly (FR1). Its resolved members "
        "are flattened into this assembly under ids <id>/<member>, rigidly "
        "placed by position/rotation_deg. The source is read-only — resolving "
        "it never writes to or rebuilds its authored state.",
        schema(
            {"project": {"type": "string"},
             "id": {"type": "string", "description": "instance id for the unit"},
             "source": {"type": "string",
                        "description": "source project name or absolute path"},
             "position": {"type": "array"},
             "rotation_deg": {"type": "array"}},
            ["project", "id", "source"],
        ),
        add_subassembly,
    ))

    def set_assembly_interface(project: str, exports: dict) -> dict:
        service.store.set_assembly_interface(project, exports)
        service.bus.publish({"type": "project_changed", "project": project})
        return {"interface": service.store.assembly_interface(project)}

    registry.register(Tool(
        "set_assembly_interface",
        "Declare which of a project's connectors are exported for mating from a "
        "parent assembly (FR3). exports is a map name -> {instance, connector}; "
        "only exported connectors are reachable when this project is instanced "
        "as a sub-assembly. Pass {} to clear.",
        schema(
            {"project": {"type": "string"},
             "exports": {"type": "object",
                         "description": "name -> {instance, connector}"}},
            ["project", "exports"],
        ),
        set_assembly_interface,
    ))
