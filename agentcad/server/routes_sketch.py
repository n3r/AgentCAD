"""Constraint-sketch solve route (project-independent)."""

from __future__ import annotations

from fastapi import APIRouter, Body


def build_router(service, registry) -> APIRouter:
    router = APIRouter()

    @router.post("/sketch/solve")
    def solve_sketch(body: dict = Body(default_factory=dict)):
        # **A sync `def` on purpose** (design Decision 9). FastAPI runs a sync
        # handler in the threadpool; an `async def` would run this synchronous
        # solver directly on the event loop, where one long solve blocks the
        # `/ws` event channel and every other request. The body is declared as
        # a parameter rather than read from `Request`, because a sync handler
        # cannot await `request.json()`.
        #
        # Explicit keys, never **body: the registry rejects unknown arguments,
        # so a key that is not whitelisted here simply never reaches the
        # solver — which is how `initial` was dead until PRD-009 slice 4.
        # Entity *kinds* (`arcs`, `splines`, `slots`) travel inside `entities`
        # and are whitelisted in `core/tools_sketch.py`, which is what unpacks
        # that dict; only new top-level keys belong here.
        return registry.call("solve_sketch", {
            "entities": body.get("entities", {}),
            "constraints": body.get("constraints", []),
            "initial": body.get("initial"),
            "drag": body.get("drag"),
            "diagnostics": body.get("diagnostics"),
            # `false` is the GUI's "do not emit"; the tool schema types this
            # key as a string, so the bool never reaches the registry.
            "emit": body.get("emit") or None,
        })

    return router
