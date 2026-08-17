"""Tool pack: render the built mesh to a PNG so agents can see the geometry.

``config`` (PRD-012) renders one declared configuration of ``part_id`` — pure
resolution through ``ensure_mesh(config=)``, written to
``renders/<part>_<config>_<view>.png``. On the assembly path there is no
top-level configuration: every instance renders at its OWN binding, so one
image can mix two sizes of one part.
"""

from __future__ import annotations

import base64

from ..kernel import acm
from ..kernel.client import KernelError
from .model import AppError, ValidationError
from .project import ProjectStore
from .render import VIEWS, render_acm
from .tools import Tool, schema


def register(registry, service) -> None:
    def render_view(project: str, part_id: str | None = None, view: str = "iso",
                    width: int = 800, height: int = 600,
                    config: str | None = None) -> dict:
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
            # An undeclared name is refused by `ensure_mesh` itself
            # (`_ensure_config_built` resolves the record before any build), so
            # a caller asking for a size that does not exist is never handed a
            # different one.
            mesh = acm.read(service.ensure_mesh(project, part_id,
                                                config=config))
            meshes.append({
                "positions": mesh["positions"], "normals": mesh["normals"],
                "indices": mesh["indices"], "transform": None, "color": None,
            })
        else:
            if config is not None:
                raise ValidationError(
                    "config renders one part: pass part_id with it — an "
                    "assembly render takes each instance's own binding"
                )
            instances = service._resolved_instances(project)
            if not instances:
                raise ValidationError(
                    "assembly has no instances to render; "
                    "pass part_id to render a single part"
                )
            for inst in instances:
                try:
                    # Each instance renders at its own binding, so one image
                    # can legitimately mix configurations of one part.
                    mesh = acm.read(service.ensure_mesh(project, inst.part,
                                                        config=inst.config))
                # AppError, not just NotFoundError: an instance bound to a
                # configuration its part no longer declares (a key-wise merge
                # can produce that) is skipped like any other unbuildable one,
                # never fatal to the whole image — `packet._render_assembly`
                # catches exactly this pair.
                except (KernelError, AppError):
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
        # Base naming unchanged: the configuration is a middle segment, and
        # names are dot- and slash-free by grammar, so nothing needs escaping.
        subject = part_id or "assembly"
        if part_id is not None and config is not None:
            subject = f"{part_id}_{config}"
        out = service.store.exports_dir(project) / "renders" / f"{subject}_{view}.png"
        ProjectStore._atomic_write(out, png)
        result = {
            "path": str(out),
            "width": width,
            "height": height,
            "view": view,
            "png_base64": base64.b64encode(png).decode("ascii"),
        }
        if config is not None:
            result["config"] = config
        if skipped:
            result["skipped"] = skipped
        return result

    registry.register(Tool(
        "render_view",
        "Render built geometry to a shaded PNG image (server-side orthographic "
        "render) so you can SEE the shape, not just measure it. Give part_id "
        "for a single part, or omit it to render the whole placed assembly "
        "(instance transforms and colors honored; each instance renders at the "
        "configuration it is bound to, so one image can mix sizes; unbuildable "
        "instances are skipped and listed). config renders ONE declared "
        "configuration of part_id instead of its working state. Views: iso, "
        "front, top, right. Writes "
        "exports/renders/<part|assembly>[_<config>]_<view>.png and returns "
        "the image.",
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
                "config": {"type": "string",
                           "description": "Declared configuration of part_id to render (default: its working state)"},
            },
            ["project"],
        ),
        render_view,
    ))
