"""Tool pack: render the built mesh to a PNG so agents can see the geometry."""

from __future__ import annotations

import base64

from ..kernel import acm
from ..kernel.client import KernelError
from .model import NotFoundError, ValidationError
from .project import ProjectStore
from .render import VIEWS, render_acm
from .tools import Tool, schema


def register(registry, service) -> None:
    def render_view(project: str, part_id: str | None = None, view: str = "iso",
                    width: int = 800, height: int = 600) -> dict:
        if view not in VIEWS:
            raise ValidationError(f"view must be one of: {', '.join(VIEWS)}")
        for name, value in (("width", width), ("height", height)):
            if (isinstance(value, bool) or not isinstance(value, int)
                    or not 64 <= value <= 2048):
                raise ValidationError(
                    f"{name} must be an integer between 64 and 2048"
                )

        meshes = []
        skipped: list[str] = []
        if part_id is not None:
            mesh = acm.read(service.ensure_mesh(project, part_id))
            meshes.append({
                "positions": mesh["positions"], "normals": mesh["normals"],
                "indices": mesh["indices"], "transform": None, "color": None,
            })
        else:
            instances = service._resolved_instances(project)
            if not instances:
                raise ValidationError(
                    "assembly has no instances to render; "
                    "pass part_id to render a single part"
                )
            for inst in instances:
                try:
                    mesh = acm.read(service.ensure_mesh(project, inst.part))
                except (KernelError, NotFoundError):
                    skipped.append(inst.id)
                    continue
                meshes.append({
                    "positions": mesh["positions"], "normals": mesh["normals"],
                    "indices": mesh["indices"],
                    "transform": (inst.position, inst.rotation_deg),
                    "color": inst.color,
                })
            if not meshes:
                raise ValidationError(
                    "no assembly instance could be built",
                    {"skipped": skipped},
                )

        png = render_acm(meshes, view=view, width=width, height=height)
        out = (service.store.exports_dir(project) / "renders"
               / f"{part_id or 'assembly'}_{view}.png")
        ProjectStore._atomic_write(out, png)
        result = {
            "path": str(out),
            "width": width,
            "height": height,
            "view": view,
            "png_base64": base64.b64encode(png).decode("ascii"),
        }
        if skipped:
            result["skipped"] = skipped
        return result

    registry.register(Tool(
        "render_view",
        "Render built geometry to a shaded PNG image (server-side orthographic "
        "render) so you can SEE the shape, not just measure it. Give part_id "
        "for a single part, or omit it to render the whole placed assembly "
        "(instance transforms and colors honored; unbuildable instances are "
        "skipped and listed). Views: iso, front, top, right. Writes "
        "exports/renders/<part|assembly>_<view>.png and returns the image.",
        schema(
            {
                "project": {"type": "string", "description": "Project name"},
                "part_id": {"type": "string",
                            "description": "Part to render; omit for the whole assembly"},
                "view": {"type": "string",
                         "description": "iso | front | top | right (default iso)"},
                "width": {"type": "integer",
                          "description": "Image width in px, 64..2048 (default 800)"},
                "height": {"type": "integer",
                           "description": "Image height in px, 64..2048 (default 600)"},
            },
            ["project"],
        ),
        render_view,
    ))
