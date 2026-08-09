"""Tool pack: generate 2D engineering drawings from a part."""

from __future__ import annotations

from .model import ValidationError
from .tools import Tool, schema


def register(registry, service) -> None:
    def generate_drawing(project: str, part_id: str, views: list | None = None,
                         format: str = "svg") -> dict:
        if format not in ("svg", "dxf"):
            raise ValidationError("drawing format must be svg or dxf")
        record = service.store.get_part(project, part_id)
        if record.kind != "script":
            raise ValidationError("drawings are supported for script parts only")
        script = service.store.read_script(project, part_id)
        out = service.store.exports_dir(project) / f"{part_id}_drawing.{format}"
        result = service.kernel.request("drawing", {
            "script": script, "params": record.params,
            "views": views, "format": format,
            "out_path": str(out), "label": f"{project} / {part_id}",
        }, timeout_s=120.0)
        return result

    registry.register(Tool(
        "generate_drawing",
        "Generate a 2D engineering drawing (projected front/top/right/iso views "
        "with overall dimensions and hole callouts detected from geometry). "
        "Formats: svg, dxf. Writes to exports/<part>_drawing.<ext>.",
        schema(
            {
                "project": {"type": "string"},
                "part_id": {"type": "string"},
                "views": {"type": "array", "description":
                          "subset of [top, front, right, iso]; default all"},
                "format": {"type": "string", "description": "svg | dxf"},
            },
            ["project", "part_id"],
        ),
        generate_drawing,
    ))
