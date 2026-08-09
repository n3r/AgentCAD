"""Tool pack: geometric analysis (tier 1) and optional FEM (tier 2:
linear-static, modal, thermal — all gated on the agentcad[fem] extra)."""

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
        def _material(project: str, material_id: str):
            """Full material record for property fallbacks (E, k). Uses the
            project-aware resolver when the materials-v2 pack is active."""
            resolve = getattr(service.materials, "resolve", None)
            if callable(resolve):
                return resolve(project, material_id)
            from .materials import get_material

            return get_material(material_id)

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

        def fem_modal(project: str, part_id: str, n_modes: int = 6,
                      fixed_face: dict | None = None, E_mpa: float | None = None,
                      nu: float | None = None) -> dict:
            if not 1 <= int(n_modes) <= 24:
                raise ValidationError("n_modes must be between 1 and 24")
            record = service.store.get_part(project, part_id)
            script = service.store.read_script(project, part_id)
            # Mass needs the real density — always the part material's.
            density = service.material_density(project, record.material)
            if E_mpa is None:
                material = _material(project, record.material)
                if material.E_gpa is None:
                    raise ValidationError(
                        f"material {record.material!r} has no Young's modulus; "
                        "pass E_mpa"
                    )
                E_mpa = material.E_gpa * 1000.0
            args = {
                "script": script, "params": record.params,
                "n_modes": int(n_modes), "E_mpa": E_mpa,
                "nu": 0.3 if nu is None else nu, "density_g_cm3": density,
            }
            if fixed_face is not None:
                args["fixed_face"] = fixed_face
            return service.kernel.request("fem_modal", args, timeout_s=600.0)

        registry.register(Tool(
            "fem_modal",
            "Modal FEM: natural frequencies (Hz) of a part, consistent-mass "
            "eigensolve with the part material's density (E defaults from the "
            "material too). Clamp an optional axis-aligned face "
            "({axis: x|y|z, side: min|max}); without one the free-free "
            "rigid-body modes are omitted from the result.",
            schema(
                {
                    "project": {"type": "string"},
                    "part_id": {"type": "string"},
                    "n_modes": {"type": "integer", "description": "modes to return (1..24, default 6)"},
                    "fixed_face": {"type": "object"},
                    "E_mpa": {"type": "number"},
                    "nu": {"type": "number"},
                },
                ["project", "part_id"],
            ),
            fem_modal,
        ))

        def fem_thermal(project: str, part_id: str, hot_face: dict,
                        cold_face: dict, t_hot_c: float, t_cold_c: float,
                        k_w_m_k: float | None = None) -> dict:
            record = service.store.get_part(project, part_id)
            script = service.store.read_script(project, part_id)
            if k_w_m_k is None:
                material = _material(project, record.material)
                if material.k_w_m_k is None:
                    raise ValidationError(
                        f"material {record.material!r} has no thermal "
                        "conductivity; pass k_w_m_k"
                    )
                k_w_m_k = material.k_w_m_k
            return service.kernel.request("fem_thermal", {
                "script": script, "params": record.params,
                "hot_face": hot_face, "cold_face": cold_face,
                "t_hot_c": t_hot_c, "t_cold_c": t_cold_c,
                "k_w_m_k": k_w_m_k,
            }, timeout_s=600.0)

        registry.register(Tool(
            "fem_thermal",
            "Thermal FEM: steady-state conduction with fixed temperatures on "
            "two axis-aligned faces ({axis: x|y|z, side: min|max}). Returns "
            "t_min/t_max (C) and the total heat flow through the hot face in "
            "W. k defaults from the part material's conductivity.",
            schema(
                {
                    "project": {"type": "string"},
                    "part_id": {"type": "string"},
                    "hot_face": {"type": "object"},
                    "cold_face": {"type": "object"},
                    "t_hot_c": {"type": "number"},
                    "t_cold_c": {"type": "number"},
                    "k_w_m_k": {"type": "number", "description": "thermal conductivity W/(m*K)"},
                },
                ["project", "part_id", "hot_face", "cold_face",
                 "t_hot_c", "t_cold_c"],
            ),
            fem_thermal,
        ))
