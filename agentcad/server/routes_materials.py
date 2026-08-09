"""Materials v2 REST routes."""

from __future__ import annotations

from fastapi import APIRouter, Request


def build_router(service, registry) -> APIRouter:
    router = APIRouter()

    @router.get("/materials")
    def list_materials(project: str | None = None):
        return registry.call("list_materials", {"project": project} if project else {})

    @router.put("/projects/{proj}/materials")
    async def set_materials(proj: str, request: Request):
        body = await request.json()
        return registry.call(
            "set_project_materials",
            {"project": proj, "materials": body.get("materials", body)},
        )

    return router
