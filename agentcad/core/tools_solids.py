"""Tool pack: per-solid part semantics (solid_materials assignment).

A multi-solid script part reports per-solid metrics (``metrics.solids``,
labeled via the script's optional ``SOLID_LABELS``). This pack lets agents
assign a material per solid — the kernel then uses each solid's density for
its mass, and the aggregate mass is the sum.
"""

from __future__ import annotations

from .model import ValidationError
from .project import ProjectStore
from .tools import Tool, schema


def register(registry, service) -> None:
    def set_solid_materials(project: str, part_id: str, materials: dict) -> dict:
        record = service.store.get_part(project, part_id)
        if record.kind != "script":
            raise ValidationError(
                "solid materials are supported for script parts only"
            )
        manifest = service.store.manifest(project)
        for key, material_id in materials.items():
            if not isinstance(key, str) or not key:
                raise ValidationError(
                    f"solid_materials key {key!r} must be a non-empty string "
                    "(a solid label or an index string like '0')"
                )
            if not isinstance(material_id, str):
                raise ValidationError(
                    f"solid_materials[{key!r}] must be a material id string"
                )
            ProjectStore._validate_material(manifest, material_id)
        for entry in manifest["parts"]:
            if entry["id"] == part_id:
                if materials:
                    entry["solid_materials"] = materials
                else:
                    entry.pop("solid_materials", None)  # empty dict clears
                break
        service.store.save_manifest(project, manifest)
        service.bus.publish({"type": "project_changed", "project": project})
        result = service._rebuild(project, part_id)
        return {
            **result,
            "solid_materials": service.store.get_part(
                project, part_id
            ).solid_materials,
        }

    registry.register(Tool(
        "set_solid_materials",
        "Assign a material per solid of a multi-solid script part. First read "
        "get_metrics(...).solids to learn the solid labels (from the script's "
        "SOLID_LABELS, else solid_0, solid_1, ...). 'materials' maps a solid "
        "label or index string ('0', '1', ...) to a material id; unmatched "
        "keys build with a warning. Per-solid mass and the aggregate mass "
        "then use these densities; unmapped solids keep the part material. "
        "An empty object clears per-solid materials. Rebuilds and returns "
        "the result.",
        schema(
            {
                "project": {"type": "string", "description": "Project name"},
                "part_id": {"type": "string", "description": "Part id"},
                "materials": {
                    "type": "object",
                    "description": "Map of solid label or index string to "
                                   "material id; {} clears",
                },
            },
            ["project", "part_id", "materials"],
        ),
        set_solid_materials,
    ))
