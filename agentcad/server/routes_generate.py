"""Task-to-part generation routes (PRD-018 §7).

Deliberately minimal. Running ``generate_part`` is just the tool over
``POST /api/tools/generate_part`` and progress rides the existing event bus /
WebSocket, so there is no streaming endpoint and no second dispatch path. What
a dedicated route adds is one honest thing the generic tool route cannot: when
generation is NOT configured (no ``ANTHROPIC_API_KEY`` at startup), the pack
registers no ``generate_part`` tool at all, so ``POST /api/tools/generate_part``
would 404 — an opaque "no such tool". This route answers the browser's Generate
panel with the SAME ``generation_unavailable`` 422 the chat dock gets from
``/api/chat`` (message + fix hint), so the UI can tell the user how to enable it.

Uploads reuse ``POST /api/projects/{proj}/imports`` (``routes_import``); the
filenames are then passed to ``generate_part`` as ``images``/``files``.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..core.tools_generate import GenerationUnavailable


def build_router(service, registry) -> APIRouter:
    router = APIRouter()

    @router.post("/projects/{proj}/generate")
    async def generate(proj: str, request: Request):
        """Run a generation. A thin convenience over the generate_part tool
        that turns the 'tool absent because unconfigured' case into the honest
        generation_unavailable 422 (the ChatUnavailable shape)."""
        if registry.get("generate_part") is None:
            raise GenerationUnavailable()
        body = await request.json() if int(
            request.headers.get("content-length") or 0) else {}
        args = dict(body)
        args["project"] = proj
        # registry.call is report-honest: a tool refusal comes back as a 200
        # with an {"error": ...} payload, exactly like POST /api/tools/{name}.
        return registry.call("generate_part", args)

    return router
