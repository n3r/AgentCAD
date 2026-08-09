"""Undo/redo routes over the service history (spec 2026-08-09-undo-redo)."""

from __future__ import annotations

from fastapi import APIRouter


def build_router(service, registry) -> APIRouter:
    router = APIRouter()

    @router.post("/projects/{proj}/undo")
    def undo(proj: str):
        info = service.history.undo(proj)
        return {
            "undone": info["label"],
            "history": {"undo": info["undo"], "redo": info["redo"]},
            "project": service.get_project(proj),
        }

    @router.post("/projects/{proj}/redo")
    def redo(proj: str):
        info = service.history.redo(proj)
        return {
            "redone": info["label"],
            "history": {"undo": info["undo"], "redo": info["redo"]},
            "project": service.get_project(proj),
        }

    @router.get("/projects/{proj}/history")
    def history(proj: str):
        return service.history.status(proj)

    return router
