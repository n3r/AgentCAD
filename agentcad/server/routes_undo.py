"""Undo/redo routes over the history cursor (GET history lives in
routes_history as the durable snapshot log)."""

from __future__ import annotations

from fastapi import APIRouter


def build_router(service, registry) -> APIRouter:
    router = APIRouter()

    @router.post("/projects/{proj}/undo")
    def undo(proj: str):
        info = service.undo_cursor.undo(proj)
        return {
            "undone": info["label"],
            "history": service.undo_cursor.status(proj),
            "project": service.get_project(proj),
        }

    @router.post("/projects/{proj}/redo")
    def redo(proj: str):
        info = service.undo_cursor.redo(proj)
        return {
            "redone": info["label"],
            "history": service.undo_cursor.status(proj),
            "project": service.get_project(proj),
        }

    return router
