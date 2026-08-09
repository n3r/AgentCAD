"""Project history routes (git-backed undo): registry passthroughs.

GET  /api/projects/{proj}/history?limit=  -> project_history payload
POST /api/projects/{proj}/restore {commit} -> project_restore payload

Both return the tool's JSON verbatim (expected failures arrive as
``{"error": {...}}`` payloads with HTTP 200, like /api/tools/*).
"""

from __future__ import annotations

from fastapi import APIRouter, Request


def build_router(service, registry) -> APIRouter:
    router = APIRouter()

    @router.get("/projects/{proj}/history")
    def get_history(proj: str, limit: int = 20):
        return registry.call("project_history", {"project": proj, "limit": limit})

    @router.post("/projects/{proj}/restore")
    async def restore_project(proj: str, request: Request):
        body = await request.json() if int(request.headers.get("content-length") or 0) else {}
        return registry.call(
            "project_restore", {"project": proj, "commit": body.get("commit", "")}
        )

    return router
