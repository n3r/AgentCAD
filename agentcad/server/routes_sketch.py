"""Constraint-sketch solve route (project-independent)."""

from __future__ import annotations

from fastapi import APIRouter, Request


def build_router(service, registry) -> APIRouter:
    router = APIRouter()

    @router.post("/sketch/solve")
    async def solve_sketch(request: Request):
        body = await request.json()
        # Explicit keys, never **body: the registry rejects unknown arguments,
        # so a key that is not whitelisted here simply never reaches the
        # solver — which is how `initial` was dead until PRD-009 slice 4.
        return registry.call("solve_sketch", {
            "entities": body.get("entities", {}),
            "constraints": body.get("constraints", []),
            "initial": body.get("initial"),
        })

    return router
