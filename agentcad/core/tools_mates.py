"""Tool pack: declarative assembly mates."""

from __future__ import annotations

from .model import InstanceSpec, NotFoundError, ValidationError
from .tools import Tool, schema


def _set_instance_mate(service, project, instance_id, mate):
    instances = service.store.instances(project)
    found = False
    for inst in instances:
        if inst.id == instance_id:
            inst.mate = mate
            found = True
    if not found:
        raise NotFoundError(f"instance {instance_id!r} not found")
    service.store.set_instances(project, instances)
    service.bus.publish({"type": "project_changed", "project": project})
    return service.get_assembly(project)


def register(registry, service) -> None:
    def set_mate(project: str, instance: str, connector: str,
                 to_instance: str, to_connector: str,
                 angle_deg: float | None = None,
                 offset_mm: float | None = None) -> dict:
        params = {}
        if angle_deg is not None:
            params["angle"] = float(angle_deg)
        if offset_mm is not None:
            params["position"] = float(offset_mm)
        mate = {
            "connector": connector,
            "to_instance": to_instance,
            "to_connector": to_connector,
        }
        if params:
            mate["params"] = params
        return _set_instance_mate(service, project, instance, mate)

    def clear_mate(project: str, instance: str) -> dict:
        return _set_instance_mate(service, project, instance, None)

    registry.register(Tool(
        "set_mate",
        "Constrain an assembly instance to another via named connectors "
        "(declared by a part script's connectors(p, part) function). The "
        "moving instance's connector must be rigid; the anchor connector may "
        "be rigid/revolute/cylindrical (angle_deg/offset_mm drive the DOF). "
        "Position/rotation are then derived automatically.",
        schema(
            {
                "project": {"type": "string"},
                "instance": {"type": "string", "description": "instance to move"},
                "connector": {"type": "string", "description": "rigid connector on the moving instance"},
                "to_instance": {"type": "string", "description": "anchor instance"},
                "to_connector": {"type": "string", "description": "connector on the anchor"},
                "angle_deg": {"type": "number", "description": "revolute/cylindrical angle"},
                "offset_mm": {"type": "number", "description": "cylindrical slide"},
            },
            ["project", "instance", "connector", "to_instance", "to_connector"],
        ),
        set_mate,
    ))
    registry.register(Tool(
        "clear_mate",
        "Remove an instance's mate; it reverts to its explicit position/rotation.",
        schema({"project": {"type": "string"}, "instance": {"type": "string"}},
               ["project", "instance"]),
        clear_mate,
    ))
