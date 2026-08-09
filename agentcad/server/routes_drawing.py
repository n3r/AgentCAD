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
        })

    @router.get("/projects/{proj}/parts/{part_id}/drawing.svg")
    def get_drawing_svg(proj: str, part_id: str):
        result = registry.call("generate_drawing", {
            "project": proj, "part_id": part_id, "format": "svg"})
        if "error" in result:
            return result
        svg = (service.store.exports_dir(proj) / f"{part_id}_drawing.svg").read_bytes()
        return Response(content=svg, media_type="image/svg+xml",
                        headers={"Cache-Control": "no-store"})

    return router
