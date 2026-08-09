"""Analysis + FEM routes."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse


def build_router(service, registry) -> APIRouter:
    router = APIRouter()

    @router.post("/projects/{proj}/parts/{part_id}/analyze")
    async def analyze(proj: str, part_id: str, request: Request):
        body = await request.json()
        return registry.call("analyze_part", {
            "project": proj, "part_id": part_id,
            "kind": body.get("kind", "inertia"),
            "plane": body.get("plane", "XY"),
            "axis": body.get("axis", "Z"),
            "min_required": body.get("min_required"),
        })

    @router.post("/projects/{proj}/parts/{part_id}/fem")
    async def fem(proj: str, part_id: str, request: Request):
        if registry.get("fem_static") is None:
            return JSONResponse(
                status_code=501,
                content={"error": {"type": "FEMUnavailable",
                                   "message": "FEM requires: pip install 'agentcad[fem]'",
                                   "details": {}}},
            )
        body = await request.json()
        return registry.call("fem_static", {"project": proj, "part_id": part_id, **body})

    return router
