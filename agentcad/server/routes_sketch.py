"""Constraint-sketch solve route (project-independent)."""

from __future__ import annotations

from fastapi import APIRouter, Body

from ..core import sketch_emit


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
            # The plane a sketch-on-face was solved in. The solver is 2D and
            # ignores it; emission writes it into the script, because
            # sketch-on-face coordinates without their basis are arbitrary.
            "plane": body.get("plane"),
            # The round-trip block's name (FR10). `false`/`""` is "do not
            # persist", like `emit`.
            "persist": body.get("persist") or None,
        })

    @router.post("/sketch/blocks")
    def sketch_blocks(body: dict = Body(default_factory=dict)):
        """Read the round-trip sketch blocks out of a part script (FR10).

        **Not a registry call, deliberately.** `parse_blocks` is a pure text
        function: no project, no store, no kernel, nothing for the tool layer
        to mediate — and the PRD is explicit that the *solver* surface grows
        keys rather than sprouting sibling tools. The route-pack precedent for
        calling straight into core is `routes_undo.py` / `routes_presence.py`.
        An agent that wants the same answer reads the one-line JSON comment
        itself; `agentcad.core.sketch_emit.parse_blocks` is the reference
        implementation and is importable.

        Sync `def` for the same reason `/sketch/solve` is one.
        """
        script = body.get("script") or ""
        return {"blocks": sketch_emit.parse_blocks(script),
                "next_name": sketch_emit.next_name(script)}

    return router
