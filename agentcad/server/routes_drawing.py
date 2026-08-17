"""Drawing generation + SVG preview routes."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response


def build_router(service, registry) -> APIRouter:
    router = APIRouter()

    @router.post("/projects/{proj}/parts/{part_id}/drawing")
    async def make_drawing(proj: str, part_id: str, request: Request):
        body = await request.json() if int(request.headers.get("content-length") or 0) else {}
        return registry.call("generate_drawing", {
            "project": proj, "part_id": part_id,
            "views": body.get("views"), "format": body.get("format", "svg"),
            "config": body.get("config"),
            "dim_table": body.get("dim_table", False),
        })

    @router.get("/projects/{proj}/parts/{part_id}/drawing.svg")
    def get_drawing_svg(proj: str, part_id: str, config: str | None = None,
                        dim_table: bool = False):
        # `dim_table` is a query flag so the browser preview can ask for the
        # tabulated sheet without a POST — FastAPI parses `?dim_table=1` and
        # `?dim_table=true` alike. It is ignored for a part with no family.
        result = registry.call("generate_drawing", {
            "project": proj, "part_id": part_id, "format": "svg",
            "config": config, "dim_table": dim_table})
        if "error" in result:
            return result
        # The same suffix the tool wrote, derived the same way: a configuration
        # drawing lands beside the base one rather than overwriting it, so
        # reading the unsuffixed file back would serve the wrong sheet.
        suffix = f"_{config}" if config else ""
        svg = (service.store.exports_dir(proj) /
               f"{part_id}{suffix}_drawing.svg").read_bytes()
        return Response(content=svg, media_type="image/svg+xml",
                        headers={"Cache-Control": "no-store"})

    return router
