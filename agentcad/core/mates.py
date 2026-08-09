"""Server-side mate resolution — the seam AgentCADService._resolved_instances
calls when any instance carries a ``mate`` spec.

Geometry (build123d Joints) lives in the worker; this module marshals the
instance list to the worker's ``resolve_mates`` handler and writes the
resulting concrete transforms back onto copies of the instances, so the rest
of the service and the frontend see ordinary position/rotation_deg.
"""

from __future__ import annotations

import copy
from pathlib import Path

from ..kernel.client import KernelError
from .model import ValidationError


def resolve(service, proj: str, instances: list):
    ids = {inst.id for inst in instances}
    kinds = {inst.id: service.store.get_part(proj, inst.part).kind for inst in instances}
    items = []
    for inst in instances:
        record = service.store.get_part(proj, inst.part)
        # Reference/imported parts declare no connectors and cannot mate; give a
        # clear error instead of a cryptic KeyError deep in the resolver.
        if inst.mate:
            if record.kind != "script":
                raise ValidationError(
                    f"instance {inst.id!r}: reference/imported parts have no "
                    "connectors and cannot be mated"
                )
            target = inst.mate.get("to_instance")
            if target not in ids:
                raise ValidationError(
                    f"instance {inst.id!r}: mate.to_instance {target!r} not found"
                )
            if kinds.get(target) != "script":
                raise ValidationError(
                    f"instance {inst.id!r}: cannot mate to reference/imported "
                    f"instance {target!r} (it has no connectors)"
                )
        item = {
            "id": inst.id,
            "position": list(inst.position),
            "rotation_deg": list(inst.rotation_deg),
        }
        if inst.mate:
            item["mate"] = inst.mate
        # Scripts carry connectors; reference parts have none (script omitted).
        if record.kind == "script":
            item["script"] = service.store.read_script(proj, inst.part)
            item["params"] = record.params
        items.append(item)

    try:
        result = service.kernel.request(
            "resolve_mates", {"items": items}, timeout_s=120.0
        )
    except KernelError as exc:
        # Surface mate errors (unknown connector, cycle, range) as validation.
        raise ValidationError(f"mate resolution failed: {exc.message}",
                              exc.details) from exc

    transforms = result["transforms"]
    resolved = []
    for inst in instances:
        t = transforms.get(inst.id)
        new = copy.copy(inst)
        if t:
            new.position = [float(v) for v in t["position"]]
            new.rotation_deg = [float(v) for v in t["rotation_deg"]]
        resolved.append(new)
    return resolved
