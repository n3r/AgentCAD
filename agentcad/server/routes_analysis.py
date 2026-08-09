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

    def _fem_unavailable():
        return JSONResponse(
            status_code=501,
            content={"error": {"type": "FEMUnavailable",
                               "message": "FEM requires: pip install 'agentcad[fem]'",
                               "details": {}}},
        )

    async def _fem_call(tool: str, keys: tuple, proj: str, part_id: str,
                        request: Request):
        if registry.get(tool) is None:
            return _fem_unavailable()
        body = await request.json()
        args = {"project": proj, "part_id": part_id}
        # Whitelist documented keys, forwarding only those present (the tool's
        # typed args reject nulls / unexpected keys).
        for key in keys:
            if body.get(key) is not None:
                args[key] = body[key]
        return registry.call(tool, args)

    @router.post("/projects/{proj}/parts/{part_id}/fem")
    async def fem(proj: str, part_id: str, request: Request):
        return await _fem_call(
            "fem_static",
            ("fixed_face", "load_face", "load_N", "load_dir",
             "E_mpa", "nu", "mesh_size_mm"),
            proj, part_id, request,
        )

    @router.post("/projects/{proj}/parts/{part_id}/fem/modal")
    async def fem_modal(proj: str, part_id: str, request: Request):
        return await _fem_call(
            "fem_modal",
            ("n_modes", "fixed_face", "E_mpa", "nu"),
            proj, part_id, request,
        )

    @router.post("/projects/{proj}/parts/{part_id}/fem/thermal")
    async def fem_thermal(proj: str, part_id: str, request: Request):
        return await _fem_call(
            "fem_thermal",
            ("hot_face", "cold_face", "t_hot_c", "t_cold_c", "k_w_m_k"),
            proj, part_id, request,
        )

    return router
