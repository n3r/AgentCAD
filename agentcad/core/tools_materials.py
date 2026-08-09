"""Materials v2 tool pack — also installs the project-aware material resolver.

Registering this pack swaps ``service.materials`` (the Wave-0 seam, default a
builtin-only resolver) for one backed by the layered MaterialLibrary
(builtin < ~/.agentcad/materials.json < project ``materials`` section), so
mass metrics honor user-defined alloys.
"""

from __future__ import annotations

from .materials import MaterialLibrary
from .model import ValidationError
from .tools import Tool, schema

CAVEAT = (
    "Property values are typical room-temperature datasheet figures (2-3 sig "
    "figs), NOT design allowables. Do not size safety-critical parts against "
    "them without a certified source and margins."
)


class ProjectMaterialResolver:
    """Resolves materials with project overrides. Exposes ``density(proj, id)``
    (the seam the service calls) plus full-record helpers for the tools."""

    def __init__(self, store):
        self.store = store
        self.library = MaterialLibrary()

    def _project_materials(self, proj: str | None) -> dict | None:
        if not proj:
            return None
        try:
            return self.store.manifest(proj).get("materials")
        except Exception:  # noqa: BLE001 — unknown project → no overrides
            return None

    def density(self, proj: str | None, material_id: str) -> float:
        return self.library.resolve(
            material_id, self._project_materials(proj)
        ).density_g_cm3

    def resolve(self, proj: str | None, material_id: str):
        return self.library.resolve(material_id, self._project_materials(proj))

    def effective(self, proj: str | None) -> dict:
        return self.library.effective(self._project_materials(proj))


def register(registry, service) -> None:
    resolver = ProjectMaterialResolver(service.store)
    service.materials = resolver  # activate the seam

    def list_materials(project: str | None = None) -> dict:
        catalog = resolver.effective(project)
        return {
            "materials": [m.to_payload() for m in sorted(
                catalog.values(), key=lambda m: (m.category, m.id))],
            "caveat": CAVEAT,
            "global_error": resolver.library.global_error,
        }

    def set_project_materials(project: str, materials: dict) -> dict:
        if not isinstance(materials, dict):
            raise ValidationError("materials must be an object of id -> entry")
        # Validate before writing (raises ValidationError on any bad entry).
        from .materials import validate_materials_dict

        validate_materials_dict(materials, "project")
        manifest = service.store.manifest(project)
        manifest["materials"] = materials
        service.store.save_manifest(project, manifest)
        service.bus.publish({"type": "project_changed", "project": project})
        return list_materials(project)

    registry.register(Tool(
        "list_materials",
        "List resolved materials (builtin + user-defined) with engineering "
        "properties and provenance. Values are typical, not design allowables.",
        schema({"project": {"type": "string", "description": "Project for overrides (optional)"}}, []),
        list_materials,
    ))
    registry.register(Tool(
        "set_project_materials",
        "Define or override this project's materials (a map of id -> "
        "{density_g_cm3 required, E_gpa, yield_mpa, ... category, notes}). "
        "Replaces the project's materials section.",
        schema(
            {
                "project": {"type": "string"},
                "materials": {"type": "object", "description": "id -> material entry"},
            },
            ["project", "materials"],
        ),
        set_project_materials,
    ))
