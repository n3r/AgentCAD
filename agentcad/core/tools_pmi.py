"""Tool pack: PMI / GD&T annotations on parts.

``set_part_pmi`` validates (core/pmi.py) and persists a part's tolerance
model into its manifest entry; ``get_part_pmi`` reads it back. PMI is
annotation, not geometry, so it applies to script AND reference parts.
``generate_drawing`` picks the stored section up and renders callouts.
"""

from __future__ import annotations

from .model import NotFoundError
from .pmi import validate_pmi
from .tools import Tool, schema


def _empty_pmi() -> dict:
    return {"dims": [], "datums": [], "fcf": []}


def register(registry, service) -> None:
    def _entry(manifest: dict, project: str, part_id: str) -> dict:
        for entry in manifest["parts"]:
            if entry["id"] == part_id:
                return entry
        raise NotFoundError(f"part {part_id!r} not found in project {project!r}")

    def get_part_pmi(project: str, part_id: str) -> dict:
        manifest = service.store.manifest(project)
        stored = _entry(manifest, project, part_id).get("pmi") or {}
        pmi = _empty_pmi()
        for key in pmi:
            pmi[key] = list(stored.get(key, []))
        return {"part_id": part_id, "pmi": pmi}

    def set_part_pmi(project: str, part_id: str, pmi: dict) -> dict:
        normalized = validate_pmi(pmi)
        manifest = service.store.manifest(project)
        entry = _entry(manifest, project, part_id)
        if any(normalized.values()):
            entry["pmi"] = normalized
        else:
            entry.pop("pmi", None)  # empty dict clears PMI
        service.store.save_manifest(project, manifest)
        service.bus.publish({"type": "project_changed", "project": project})
        return {"part_id": part_id, "pmi": normalized}

    registry.register(Tool(
        "set_part_pmi",
        "Set a part's PMI / GD&T tolerance model (replaces the whole section; "
        "an empty object clears it). Shape: {dims: [{id, kind: linear|diameter, "
        "target: width|height|depth or nominal hole dia mm, plus, minus, "
        "note?}], datums: [{id: 'A'..'Z', face: top|bottom|left|right|front|"
        "back}], fcf: [{id, type: flatness|position|perpendicularity|"
        "parallelism|cylindricity, tol_mm, datums: [letters], note?}]}. "
        "Rendered as callouts by generate_drawing (SVG). Works for script and "
        "reference parts.",
        schema(
            {
                "project": {"type": "string"},
                "part_id": {"type": "string"},
                "pmi": {"type": "object",
                        "description": "PMI section ({} clears)"},
            },
            ["project", "part_id", "pmi"],
        ),
        set_part_pmi,
    ))
    registry.register(Tool(
        "get_part_pmi",
        "Get a part's PMI / GD&T tolerance model (empty sections when unset).",
        schema(
            {"project": {"type": "string"}, "part_id": {"type": "string"}},
            ["project", "part_id"],
        ),
        get_part_pmi,
    ))
