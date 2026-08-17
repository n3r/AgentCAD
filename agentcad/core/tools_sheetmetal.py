"""Tool pack: export a sheet-metal part's flat pattern (SVG/DXF)."""

from __future__ import annotations

from .model import ValidationError
from .tools import Tool, schema


def register(registry, service) -> None:
    def flat_pattern(project: str, part_id: str, format: str = "svg") -> dict:
        if format not in ("svg", "dxf"):
            raise ValidationError("flat pattern format must be svg or dxf")
        record = service.store.get_part(project, part_id)
        if record.kind != "script":
            raise ValidationError(
                "flat patterns are supported for script parts only "
                "(reference parts have no flat_pattern(p) contract)")
        script = service.store.read_script(project, part_id)
        out = service.store.exports_dir(project) / f"{part_id}_flat.{format}"
        return service.kernel.request("flat_pattern", {
            "script": script, "params": record.effective_params,
            "format": format, "out_path": str(out),
            "label": f"{project} / {part_id}",
        }, timeout_s=120.0)

    registry.register(Tool(
        "flat_pattern",
        "Export a sheet-metal part's flat pattern (unfolded blank outline plus "
        "dashed bend lines with angle/radius callouts). The part script must "
        "define flat_pattern(p) returning a flat part or (part, bend_lines) — "
        "SheetPart from agentcad.toolkit.sheetmetal provides both. Formats: "
        "svg, dxf (layers OUTLINE and BEND). Writes to exports/<part>_flat.<ext>.",
        schema(
            {
                "project": {"type": "string"},
                "part_id": {"type": "string"},
                "format": {"type": "string", "description": "svg | dxf"},
            },
            ["project", "part_id"],
        ),
        flat_pattern,
    ))
