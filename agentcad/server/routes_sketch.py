"""Constraint-sketch solve route (project-independent)."""

from __future__ import annotations

from fastapi import APIRouter, Request


def build_router(service, registry) -> APIRouter:
    router = APIRouter()

    @router.post("/sketch/solve")
    async def solve_sketch(request: Request):
        body = await request.json()
        return registry.call("solve_sketch", {
            "entities": body.get("entities", {}),
            "constraints": body.get("constraints", []),
        })

    return router
