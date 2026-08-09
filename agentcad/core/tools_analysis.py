"""Tool pack: geometric analysis (tier 1) and optional FEM (tier 2)."""

from __future__ import annotations

from .model import ValidationError
from .tools import Tool, schema


def register(registry, service) -> None:
    def analyze_part(project: str, part_id: str, kind: str = "inertia",
                     plane: str = "XY", axis: str = "Z",
                     min_required: float | None = None) -> dict:
        record = service.store.get_part(project, part_id)
        if record.kind != "script":
            raise ValidationError("analysis is supported for script parts only")
        script = service.store.read_script(project, part_id)
        density = service.material_density(project, record.material)
        return service.kernel.request("analyze", {
            "script": script, "params": record.params, "kind": kind,
            "plane": plane, "axis": axis, "min_required": min_required,
            "density_g_cm3": density,
        }, timeout_s=120.0)

    registry.register(Tool(
        "analyze_part",
        "Geometric analysis of a part. kind=section (cross-section area on a "
        "plane), wall (min wall thickness, optionally vs min_required), "
        "inertia (mass-properties tensor), projected_area (silhouette area "
        "along an axis).",
        schema(
            {
                "project": {"type": "string"},
                "part_id": {"type": "string"},
                "kind": {"type": "string", "description": "section|wall|inertia|projected_area"},
                "plane": {"type": "string", "description": "section plane: XY|XZ|YZ"},
                "axis": {"type": "string", "description": "projected_area axis: X|Y|Z"},
                "min_required": {"type": "number", "description": "wall: min acceptable mm"},
            },
            ["project", "part_id", "kind"],
        ),
        analyze_part,
    ))

    # FEM: register only when the optional extra is importable, so agents never
    # see a tool that cannot run.
    from ..kernel.handlers.fem import fem_available

    if fem_available():
        def fem_static(project: str, part_id: str, fixed_face: dict,
                       load_face: dict, load_N: float = 100.0,
                       load_dir: list | None = None, E_mpa: float = 210000.0,
                       nu: float = 0.3, mesh_size_mm: float = 3.0) -> dict:
            record = service.store.get_part(project, part_id)
            script = service.store.read_script(project, part_id)
            return service.kernel.request("fem_static", {
                "script": script, "params": record.params,
                "fixed_face": fixed_face, "load_face": load_face,
                "load_N": load_N, "load_dir": load_dir or [0, 0, -1],
                "E_mpa": E_mpa, "nu": nu, "mesh_size_mm": mesh_size_mm,
            }, timeout_s=600.0)

        registry.register(Tool(
            "fem_static",
            "Linear-static FEM: clamp one axis-aligned face, load another, "
            "return max displacement and max von Mises. fixed_face/load_face = "
            "{axis: x|y|z, side: min|max}.",
            schema(
                {
                    "project": {"type": "string"},
                    "part_id": {"type": "string"},
                    "fixed_face": {"type": "object"},
                    "load_face": {"type": "object"},
                    "load_N": {"type": "number"},
                    "load_dir": {"type": "array"},
                    "E_mpa": {"type": "number"},
                    "nu": {"type": "number"},
                    "mesh_size_mm": {"type": "number"},
                },
                ["project", "part_id", "fixed_face", "load_face"],
            ),
            fem_static,
        ))
