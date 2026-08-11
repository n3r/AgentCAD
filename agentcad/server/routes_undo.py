"""Undo/redo routes over the history cursor (GET history lives in
routes_history as the durable snapshot log).

Both verbs take an optional ``{scope}`` body — ``"any"`` (the default, and
what a body-less POST still means, so the browser's existing calls are
unchanged) or ``"mine"``, which takes back only the calling client's own most
recent edit. See ``core/history.py``'s ``UndoCursor`` for why the stacks are
shared rather than per client.
"""

from __future__ import annotations

from fastapi import APIRouter, Request


async def _scope(request: Request) -> str:
    """The body's ``scope``, or ``"any"``. Read the BYTES, not the header: a
    beacon-style or chunked POST carries no ``content-length``. Whitelisted —
    never ``**body`` — and an unknown value is left for the cursor to reject
    as a validation_error, so the tool and the route refuse identically."""
    if not await request.body():
        return "any"
    body = await request.json()
    if not isinstance(body, dict):
        return "any"
    found = body.get("scope")
    return found if isinstance(found, str) else "any"


def build_router(service, registry) -> APIRouter:
    router = APIRouter()

    @router.post("/projects/{proj}/undo")
    async def undo(proj: str, request: Request):
        info = service.undo_cursor.undo(proj, await _scope(request))
        return {
            "undone": info["label"],
            "history": service.undo_cursor.status(proj),
            "project": service.get_project(proj),
        }

    @router.post("/projects/{proj}/redo")
    async def redo(proj: str, request: Request):
        info = service.undo_cursor.redo(proj, await _scope(request))
        return {
            "redone": info["label"],
            "history": service.undo_cursor.status(proj),
            "project": service.get_project(proj),
        }

    return router
