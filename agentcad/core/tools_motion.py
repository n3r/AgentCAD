"""Tool pack: motion from mates — sweep a mated instance's driven DOF.

``sweep_motion`` drives one mate parameter (revolute/cylindrical ``angle`` or
cylindrical ``position``) across an inclusive [start, end] range, re-resolving
the mate graph and checking pairwise interference at each sample in the
kernel. Connector-type validation is left to the mate resolver: an angle sweep
against a rigid anchor (or an offset sweep against a revolute one) surfaces
the resolver's contract error rather than costing an extra kernel round-trip.
"""

from __future__ import annotations

from .model import NotFoundError, ValidationError
from .tools import Tool, schema

MAX_SAMPLES = 60


def register(registry, service) -> None:
    def sweep_motion(project: str, instance: str,
                     angle_range: list | None = None,
                     offset_range: list | None = None,
                     samples: int = 12, min_volume: float = 0.001) -> dict:
        if (angle_range is None) == (offset_range is None):
            raise ValidationError(
                "provide exactly one of angle_range or offset_range"
            )
        rng = angle_range if angle_range is not None else offset_range
        which = "angle_range" if angle_range is not None else "offset_range"
        if (
            not isinstance(rng, (list, tuple))
            or len(rng) != 2
            or any(
                isinstance(v, bool) or not isinstance(v, (int, float))
                for v in rng
            )
        ):
            raise ValidationError(f"{which} must be [start, end] numbers")
        if (
            isinstance(samples, bool)
            or not isinstance(samples, int)
            or not 2 <= samples <= MAX_SAMPLES
        ):
            raise ValidationError(
                f"samples must be an integer in 2..{MAX_SAMPLES}"
            )

        instances = service.store.instances(project)
        driven = next((i for i in instances if i.id == instance), None)
        if driven is None:
            raise NotFoundError(f"instance {instance!r} not found")
        if not driven.mate:
            raise ValidationError(
                f"instance {instance!r} has no mate to drive — set_mate first"
            )

        param = "angle" if angle_range is not None else "position"
        start, end = float(rng[0]), float(rng[1])
        values = [
            start + (end - start) * i / (samples - 1) for i in range(samples)
        ]

        items = []
        for inst in instances:
            # A bound instance sweeps at its configuration's size — the
            # kernel re-resolves the mate graph from these items, so the
            # derived record is what puts the connector where it belongs.
            record = service._record_for(project, inst.part, inst.config)
            item = service._shape_item(project, record, inst)
            item["id"] = inst.id
            if inst.mate:
                item["mate"] = inst.mate
            items.append(item)

        result = service.kernel.request(
            "motion_sweep",
            {
                "items": items,
                "driven": {"instance": instance, "param": param,
                           "values": values},
                "min_volume": float(min_volume),
            },
            timeout_s=300.0,
            affinity=project,
        )
        return {**result, "instance": instance, "param": param,
                "values": values}

    registry.register(Tool(
        "sweep_motion",
        "Sweep a mated instance's driven DOF (revolute/cylindrical angle_deg "
        "or cylindrical offset_mm) across [start, end], re-resolving mates "
        "and boolean-checking every instance pair at each sample. Returns "
        "per-sample overlap pairs, per-instance transform frames (for "
        "animation), clear (true when nothing collides) and first_collision "
        "(the first swept value that overlaps; null when clear). Imported "
        "STL instances cannot be boolean-checked and land in skipped_mesh.",
        schema(
            {
                "project": {"type": "string", "description": "Project name"},
                "instance": {"type": "string",
                             "description": "Mated instance whose DOF is driven"},
                "angle_range": {"type": "array",
                                "description": "[start, end] angle in degrees"},
                "offset_range": {"type": "array",
                                 "description": "[start, end] slide in mm (cylindrical mates)"},
                "samples": {"type": "integer",
                            "description": f"Sample count across the range, 2..{MAX_SAMPLES} (default 12)"},
                "min_volume": {"type": "number",
                               "description": "Overlap threshold in mm^3 (default 0.001)"},
            },
            ["project", "instance"],
        ),
        sweep_motion,
    ))
