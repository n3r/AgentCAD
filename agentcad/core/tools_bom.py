"""BOM tool pack (PRD-015 FR1-3): ``get_bom`` and ``set_bom_fields``.

Registers no gate provider (a BOM read/edit is not a proposal gate), so its
load order is irrelevant. ``core/bom.py`` does the zero-kernel work; this pack
is the agent surface and the manifest write.
"""

from __future__ import annotations

from .bom import build_bom
from .model import NotFoundError, ValidationError
from .tools import Tool, schema

_MAX_FIELD_LEN = 200
#: The string fields set_bom_fields writes into parts[i]["bom"].
_STRING_FIELDS = ("part_number", "supplier", "url")


def _clean_str(name: str, value) -> str:
    """A bounded, control-char-free string, or ``ValidationError`` (the
    tools_drawing precedent)."""
    if not isinstance(value, str):
        raise ValidationError(f"{name} must be a string")
    if len(value) > _MAX_FIELD_LEN:
        raise ValidationError(
            f"{name} is {len(value)} characters; the cap is {_MAX_FIELD_LEN}")
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in value):
        raise ValidationError(f"{name} contains a control character")
    return value


def register(registry, service) -> None:

    def get_bom(project: str, config: str | None = None,
                structure: str = "flat", ref: str | None = None) -> dict:
        # `ref` (a reproducible ref-pinned BOM) is a Slice 2 feature — accepted
        # here so the tool signature is stable, ignored until the tag-capable
        # ephemeral service lands.
        return build_bom(service, project, structure=structure, config=config)

    def set_bom_fields(project: str, part_id: str,
                       part_number: str | None = None,
                       unit_cost_usd: float | None = None,
                       supplier: str | None = None, url: str | None = None,
                       config: str | None = None) -> dict:
        # `config` is accepted but v1 stores a SINGLE per-part bom
        # (config-agnostic part numbers are the common case); a per-config
        # override map is a documented follow-up.
        manifest = service.store.manifest(project)
        entry = next((e for e in manifest.get("parts") or []
                      if isinstance(e, dict) and e.get("id") == part_id), None)
        if entry is None:
            raise NotFoundError(
                f"project {project!r} has no part {part_id!r}")

        bom = dict(entry.get("bom") or {})
        for name, value in (("part_number", part_number),
                            ("supplier", supplier), ("url", url)):
            if value is not None:
                bom[name] = _clean_str(name, value)
        if unit_cost_usd is not None:
            if isinstance(unit_cost_usd, bool) or \
                    not isinstance(unit_cost_usd, (int, float)):
                raise ValidationError("unit_cost_usd must be a number")
            if unit_cost_usd < 0:
                raise ValidationError("unit_cost_usd must be >= 0")
            bom["unit_cost_usd"] = float(unit_cost_usd)

        entry["bom"] = bom
        service.store.save_manifest(project, manifest)
        service.bus.publish(
            {"type": "project_changed", "project": project, "part": part_id})
        return {"ok": True, "part_id": part_id, "bom": bom}

    registry.register(Tool(
        "get_bom",
        "Roll up the project's bill of materials from the assembly — one line "
        "per part with qty, part_number, label, config, material, unit/extended "
        "mass and cost, and a source. Patterns count by their `count` and "
        "sub-assemblies roll their members up under the source project; the "
        "build makes NO kernel calls, so an unbuilt part reports "
        "mass_source: unbuilt with a warning rather than triggering a build. "
        "cost_source is manual (a set_bom_fields override), material_estimate "
        "(unit_mass * the material's cost_usd_kg) or none. structure: flat "
        "(grouped, default) or indented (per-occurrence tree with a level). "
        "config applies a configuration assembly-wide where an instance binds "
        "none.",
        schema(
            {
                "project": {"type": "string", "description": "Project name"},
                "structure": {"type": "string",
                              "description": "flat (default) or indented"},
                "config": {"type": "string",
                           "description": "Assembly-wide configuration to apply "
                                          "where an instance binds none"},
                "ref": {"type": "string",
                        "description": "Branch or tag for a reproducible BOM "
                                       "(reserved; not yet honored)"},
            },
            ["project"],
        ),
        get_bom,
    ))

    registry.register(Tool(
        "set_bom_fields",
        "Set a part's BOM inputs, stored at parts[i].bom and read by get_bom: "
        "part_number, unit_cost_usd (a non-negative number; a manual price that "
        "overrides the material estimate), supplier and url (bounded strings, "
        "no control characters). Only the fields you pass are updated; unknown "
        "keys are refused. config is accepted but v1 stores a single per-part "
        "bom.",
        schema(
            {
                "project": {"type": "string", "description": "Project name"},
                "part_id": {"type": "string", "description": "Part id"},
                "part_number": {"type": "string",
                                "description": "Manufacturer/part number"},
                "unit_cost_usd": {"type": "number",
                                  "description": "Manual unit cost (USD, >= 0)"},
                "supplier": {"type": "string", "description": "Supplier name"},
                "url": {"type": "string", "description": "Datasheet/source URL"},
                "config": {"type": "string",
                           "description": "Reserved (v1 stores one per-part "
                                          "bom)"},
            },
            ["project", "part_id"],
        ),
        set_bom_fields,
    ))
