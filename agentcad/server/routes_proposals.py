"""Change-proposal routes: registry passthroughs.

    GET    /api/projects/{proj}/proposals                 ?state=
    POST   /api/projects/{proj}/proposals      {source, target, title,
                                                description, draft}
    GET    /api/projects/{proj}/proposals/{pid}
    PATCH  /api/projects/{proj}/proposals/{pid}       {title, description, state}
    GET    /api/projects/{proj}/proposals/{pid}/packet          ?regenerate=1
    POST   /api/projects/{proj}/proposals/{pid}/review {verdict, summary}
    POST   /api/projects/{proj}/proposals/{pid}/merge  {allow_invalid}
    GET    /api/projects/{proj}/proposals/{pid}/render/{side}/{part}  ?view=iso
    GET    /api/projects/{proj}/proposals/{pid}/render/{side}         ?view=iso
    GET    /api/projects/{proj}/proposals/{pid}/diff/{gen}/{part}/{kind}.acm

Body keys are whitelisted per route (the registry rejects unknown arguments,
and ``null`` must read as "omitted", not as an argument). Ordinary failures are
re-raised as ``NotFoundError``/``ValidationError``/``ConflictError`` so the
app's handlers map them to 404/422/409 like every other REST route, and any
OTHER error type (``invalid_arguments``, a kernel error, …) is a 422 rather
than a 200 body nobody inspects. ``merge_conflict`` is the single deliberate
exception — it comes back as an ``{"error": …}`` body at HTTP 200, exactly as
it does for ``POST …/merge``, so the UI can render the conflict list with its
existing modal instead of an error page.

The two asset routes are the exception to the passthrough rule: they answer
with raw bytes rather than JSON — ``image/png`` decoded from the render tool's
``png_base64`` (like ``routes_vision``) and the ``application/octet-stream``
ACM1 diff mesh straight off disk (like the mesh route in ``app.py``), both
``no-store`` because a packet regenerates in place.
"""

from __future__ import annotations

import base64

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ..core.model import ConflictError, NotFoundError, ValidationError

_RAISE = {
    "notfound_error": NotFoundError,
    "validation_error": ValidationError,
    "conflict_error": ConflictError,
}

# The ONE error type that is a legitimate HTTP 200 body: a UI renders the
# conflict list rather than an error page.
_BODY_ERRORS = {"merge_conflict"}


def _result(payload: dict) -> dict:
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict) and error.get("type") not in _BODY_ERRORS:
        cls = _RAISE.get(error.get("type"), ValidationError)
        raise cls(error.get("message", ""), error.get("details"))
    return payload


def _body_keys(body: dict, *keys: str) -> dict:
    """Whitelisted, null-stripped forwarding — never ``**body``."""
    return {key: body[key] for key in keys
            if isinstance(body, dict) and body.get(key) is not None}


def _png(registry, args: dict) -> Response:
    result = _result(registry.call("proposal_render", args))
    return Response(
        content=base64.b64decode(result["png_base64"]),
        media_type="image/png",
        headers={"Cache-Control": "no-store"},
    )


async def _json(request: Request) -> dict:
    """The body, or ``{}`` when there is none.

    Read the BYTES, not the header: a chunked request carries no
    ``content-length``, and trusting the header turned its body into "no
    arguments at all" — a review with no verdict rather than a 422.
    """
    if not await request.body():
        return {}
    body = await request.json()
    return body if isinstance(body, dict) else {}


def build_router(service, registry) -> APIRouter:
    router = APIRouter()
    if registry.get("proposal_list") is None:
        return router  # no git: the tool pack registered nothing to route to

    # Route packs are mounted after every tool pack, so this is the earliest
    # point at which service.branches exists — install the branch-delete guard
    # here too, so a server that has only ever *read* proposals since boot
    # still refuses to delete a branch one of them names (FR2).
    service.proposals.ensure_branch_guard()

    @router.get("/projects/{proj}/proposals")
    def list_proposals(proj: str, state: str | None = None):
        args = {"project": proj}
        if state is not None:
            args["state"] = state
        return _result(registry.call("proposal_list", args))

    @router.post("/projects/{proj}/proposals")
    async def create_proposal(proj: str, request: Request):
        body = await _json(request)
        args = {"project": proj, "source": body.get("source", ""),
                "title": body.get("title", ""),
                **_body_keys(body, "target", "description", "draft")}
        return _result(registry.call("proposal_create", args))

    @router.get("/projects/{proj}/proposals/{pid}")
    def get_proposal(proj: str, pid: str):
        return _result(registry.call("proposal_get",
                                     {"project": proj, "id": pid}))

    @router.patch("/projects/{proj}/proposals/{pid}")
    async def update_proposal(proj: str, pid: str, request: Request):
        body = await _json(request)
        args = {"project": proj, "id": pid,
                **_body_keys(body, "title", "description", "state")}
        return _result(registry.call("proposal_update", args))

    @router.get("/projects/{proj}/proposals/{pid}/packet")
    def get_packet(proj: str, pid: str, regenerate: bool = False):
        return _result(registry.call(
            "proposal_packet",
            {"project": proj, "id": pid, "regenerate": regenerate},
        ))

    @router.get("/projects/{proj}/proposals/{pid}/render/{side}/{part}")
    def render_part(proj: str, pid: str, side: str, part: str,
                    view: str = "iso"):
        return _png(registry, {"project": proj, "id": pid, "side": side,
                               "part": part, "view": view})

    @router.get("/projects/{proj}/proposals/{pid}/render/{side}")
    def render_assembly(proj: str, pid: str, side: str, view: str = "iso"):
        return _png(registry, {"project": proj, "id": pid, "side": side,
                               "view": view})

    # ``{kind}.acm``: the packet publishes the extension so the URL reads as a
    # file, and the generation/part/kind triple is whitelisted by the builder
    # before it touches the filesystem. ``{gen}`` is the build the packet was
    # published with: a URL from a packet whose assets are gone reads as a 404
    # rather than as another build's geometry.
    @router.get("/projects/{proj}/proposals/{pid}/diff/{gen}/{part}/{kind}.acm")
    def get_diff_mesh(proj: str, pid: str, gen: str, part: str, kind: str):
        path = service.packets.diff_mesh_path(proj, pid, gen, part, kind)
        try:
            content = path.read_bytes()
        except OSError as exc:
            # A regeneration unlinked it between the check and the read: the
            # asset is gone, which is a 404 — never a 500.
            raise NotFoundError(
                f"proposal {pid} has no {kind} geometry for part {part!r}",
                {"id": pid, "part": part, "kind": kind},
            ) from exc
        return Response(
            content=content,
            media_type="application/octet-stream",
            headers={"Cache-Control": "no-store"},
        )

    @router.post("/projects/{proj}/proposals/{pid}/review")
    async def review_proposal(proj: str, pid: str, request: Request):
        body = await _json(request)
        args = {"project": proj, "id": pid, "verdict": body.get("verdict", ""),
                **_body_keys(body, "summary")}
        return _result(registry.call("proposal_review", args))

    @router.post("/projects/{proj}/proposals/{pid}/merge")
    async def merge_proposal(proj: str, pid: str, request: Request):
        body = await _json(request)
        args = {"project": proj, "id": pid,
                **_body_keys(body, "allow_invalid")}
        return _result(registry.call("proposal_merge", args))

    return router
