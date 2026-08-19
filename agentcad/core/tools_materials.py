"""Materials v2 tool pack — also installs the project-aware material resolver.

Registering this pack swaps ``service.materials`` (the Wave-0 seam, default a
builtin-only resolver) for one backed by the layered MaterialLibrary
(builtin < ~/.agentcad/materials.json < project ``materials`` section), so
mass metrics honor user-defined alloys.
"""

from __future__ import annotations

from . import materials_query
from .materials import LIBRARY_VERSION, PROPERTY_UNITS, MaterialLibrary
from .model import ValidationError
from .tools import Tool, schema

CAVEAT = (
    "Property values are typical room-temperature datasheet figures (2-3 sig "
    "figs), NOT design allowables. Do not size safety-critical parts against "
    "them without a certified source and margins."
)

#: The constraint grammar, spelled out once so `find_materials`'s and
#: `list_materials`'s tool descriptions (which an agent reads to use them
#: correctly) and `get_material`'s error text stay in sync with
#: `materials_query.py` rather than drifting from a second copy.
_PROPERTY_KEYS = ", ".join(PROPERTY_UNITS)
_PROCESS_KEYS = ", ".join(materials_query.CONSTRAINT_PROCESSES)
_GRAMMAR = (
    f"require/filter keys: <property>_min/<property>_max for "
    f"{_PROPERTY_KEYS}; category; subcategory; process "
    f"({_PROCESS_KEYS}); basis (typical|minimum|characteristic). "
    "A range qualifies by its conservative bound (the whole range must clear "
    "the bar); a material missing the property never qualifies."
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

    def project_library_version(self, proj: str | None) -> str | None:
        """The library version the project pinned, or None (an old project,
        or no project at all)."""
        if not proj:
            return None
        try:
            return self.store.manifest(proj).get("materials_library")
        except Exception:  # noqa: BLE001 — unknown project → no pin
            return None

    def density(self, proj: str | None, material_id: str) -> float:
        return self.library.resolve(
            material_id, self._project_materials(proj)
        ).density_g_cm3

    def resolve(self, proj: str | None, material_id: str):
        return self.library.resolve(material_id, self._project_materials(proj))

    def effective(self, proj: str | None) -> dict:
        return self.library.effective(self._project_materials(proj))


def _version_tuple(version: str) -> tuple[int, ...]:
    """Dotted integers. A version we cannot read raises — the caller turns
    that into the ``library_version_unreadable`` warning rather than guessing
    an ordering (a hand-edited manifest is the usual cause)."""
    return tuple(int(part) for part in str(version).split("."))


def _pin_warnings(pinned: str | None) -> list[str]:
    if pinned is None:
        return []
    try:
        newer = _version_tuple(pinned) > _version_tuple(LIBRARY_VERSION)
    except (TypeError, ValueError):
        return ["library_version_unreadable"]
    return ["library_version_newer_than_shipped"] if newer else []


def register(registry, service) -> None:
    resolver = ProjectMaterialResolver(service.store)
    service.materials = resolver  # activate the seam

    def list_materials(project: str | None = None, category: str | None = None,
                       subcategory: str | None = None,
                       filter: dict | None = None) -> dict:
        catalog = resolver.effective(project)
        constraints = materials_query.normalize_constraints(
            filter, category=category, subcategory=subcategory)
        selected = [m for m in catalog.values()
                   if materials_query.qualifies(m, constraints) is not None]
        selected.sort(key=lambda m: (m.category, m.subcategory or "", m.id))
        materials = [m.to_payload() for m in selected]
        pinned = resolver.project_library_version(project)
        return {
            "materials": materials,
            "count": len(materials),
            "library_version": LIBRARY_VERSION,
            "project_library_version": pinned,
            "warnings": _pin_warnings(pinned),
            "caveat": CAVEAT,
            "global_error": resolver.library.global_error,
        }

    def find_materials(require: dict | None = None, prefer: dict | None = None,
                       category: str | None = None, limit: int = 10,
                       project: str | None = None) -> dict:
        catalog = resolver.effective(project)
        rows = materials_query.find(catalog, require=require, prefer=prefer,
                                    category=category, limit=limit)
        constraints = materials_query.normalize_constraints(require, category=category)
        return {
            "materials": rows,
            "count": len(rows),
            "constraints": constraints.to_dict(),
            "caveat": CAVEAT,
        }

    def get_material(id: str, project: str | None = None) -> dict:
        material = resolver.resolve(project, id)
        payload = material.to_payload(full=True)
        payload["caveat"] = CAVEAT
        return payload

    def set_project_materials(project: str, materials: dict) -> dict:
        if not isinstance(materials, dict):
            raise ValidationError("materials must be an object of id -> entry")
        # Validate before writing (raises ValidationError on any bad entry).
        from .materials import validate_materials_dict

        validate_materials_dict(materials, "project")
        manifest = service.store.manifest(project)
        manifest["materials"] = materials
        # Writing the section re-bases the project on the running library
        # (FR9): the entries were just validated against THIS schema, so the
        # pin has to say so — an older pin left behind would report a
        # provenance nobody could reproduce.
        manifest["materials_library"] = LIBRARY_VERSION
        service.store.save_manifest(project, manifest)
        service.bus.publish({"type": "project_changed", "project": project})
        return list_materials(project)

    registry.register(Tool(
        "list_materials",
        "List resolved materials (builtin + user-defined) with engineering "
        "properties, per-property basis, uncited keys and provenance, plus "
        "the shipped library_version and the project's pin. Values are "
        "typical, not design allowables. Optionally filter by category, "
        "subcategory, and/or the same constraint grammar find_materials uses "
        f"('filter', 'require' there — {_GRAMMAR}).",
        schema(
            {
                "project": {"type": "string", "description": "Project for overrides (optional)"},
                "category": {"type": "string", "description": "Exact category filter (optional)"},
                "subcategory": {"type": "string", "description": "Exact subcategory filter (optional)"},
                "filter": {"type": "object", "description": "Constraint grammar object (optional) — see the tool description"},
            },
            [],
        ),
        list_materials,
    ))
    registry.register(Tool(
        "find_materials",
        "Search the resolved catalog by engineering requirements and return "
        "the qualifying materials with the cited evidence that made each one "
        f"qualify. {_GRAMMAR} prefer: {{<property>: min|max}} — ranks the "
        "qualifying set (a material missing a preferred property ranks last). "
        "Zero matches is a validation_error naming the single constraint whose "
        "removal would admit the most records (details.nearest_relaxation).",
        schema(
            {
                "require": {"type": "object", "description": "Constraint grammar object (optional) — see the tool description"},
                "prefer": {"type": "object", "description": "{<property>: 'min'|'max'} ranking (optional)"},
                "category": {"type": "string", "description": "Exact category filter (optional)"},
                "limit": {"type": "integer", "description": "Max rows, 1-50 (default 10)"},
                "project": {"type": "string", "description": "Project for overrides (optional)"},
            },
            [],
        ),
        find_materials,
    ))
    registry.register(Tool(
        "get_material",
        "Get one resolved material's full record: every property with its "
        "unit/basis/source/table, process metadata, links, standards and "
        "warnings. Unknown id is a validation_error listing every known id.",
        schema(
            {
                "id": {"type": "string", "description": "Material id"},
                "project": {"type": "string", "description": "Project for overrides (optional)"},
            },
            ["id"],
        ),
        get_material,
    ))
    registry.register(Tool(
        "set_project_materials",
        "Define or override this project's materials (a map of id -> entry). "
        "An entry is either a flat {density_g_cm3 required, E_gpa, yield_mpa, "
        "... category, notes} or a v2 card with a 'properties' object whose "
        "values carry unit/basis/source. Replaces the project's materials "
        "section.",
        schema(
            {
                "project": {"type": "string"},
                "materials": {"type": "object", "description": "id -> material entry"},
            },
            ["project", "materials"],
        ),
        set_project_materials,
    ))
