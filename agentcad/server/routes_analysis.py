"""Analysis + FEM routes."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse


def build_router(service, registry) -> APIRouter:
    router = APIRouter()

    @router.post("/projects/{proj}/parts/{part_id}/analyze")
    async def analyze(proj: str, part_id: str, request: Request):
        body = await request.json()
        args = {
            "project": proj, "part_id": part_id,
            "kind": body.get("kind", "inertia"),
            "plane": body.get("plane", "XY"),
            "axis": body.get("axis", "Z"),
        }
        # Only forward min_required when set — the tool's number-typed arg
        # rejects null.
        if body.get("min_required") is not None:
            args["min_required"] = body["min_required"]
        return registry.call("analyze_part", args)

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
        args = {"project": proj, "part_id": part_id}
        # Whitelist documented keys, forwarding only those present (the tool's
        # typed args reject nulls / unexpected keys).
        for key in ("fixed_face", "load_face", "load_N", "load_dir",
                    "E_mpa", "nu", "mesh_size_mm"):
            if body.get(key) is not None:
                args[key] = body[key]
        return registry.call("fem_static", args)

    return router
