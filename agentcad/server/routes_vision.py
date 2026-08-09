"""Render-to-PNG route (vision pack)."""

from __future__ import annotations

import base64

from fastapi import APIRouter, Request
from fastapi.responses import Response


def build_router(service, registry) -> APIRouter:
    router = APIRouter()

    @router.post("/projects/{proj}/render")
    async def render_project(proj: str, request: Request):
        body = await request.json() if int(request.headers.get("content-length") or 0) else {}
        result = registry.call("render_view", {
            "project": proj,
            "part_id": body.get("part_id"),
            "view": body.get("view", "iso"),
            "width": body.get("width", 800),
            "height": body.get("height", 600),
        })
        if "error" in result:
            return result
        png = base64.b64decode(result["png_base64"])
        return Response(content=png, media_type="image/png",
                        headers={"Cache-Control": "no-store"})

    return router
