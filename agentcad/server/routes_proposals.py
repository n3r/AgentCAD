"""Change-proposal routes: registry passthroughs.

    GET    /api/projects/{proj}/proposals                 ?state=
    POST   /api/projects/{proj}/proposals      {source, target, title,
                                                description, draft}
    GET    /api/projects/{proj}/proposals/{pid}
    PATCH  /api/projects/{proj}/proposals/{pid}       {title, description, state}
    POST   /api/projects/{proj}/proposals/{pid}/review {verdict, summary}
    POST   /api/projects/{proj}/proposals/{pid}/merge  {allow_invalid}

Body keys are whitelisted per route (the registry rejects unknown arguments,
and ``null`` must read as "omitted", not as an argument). Ordinary failures are
re-raised as ``NotFoundError``/``ValidationError``/``ConflictError`` so the
app's handlers map them to 404/422/409 like every other REST route, and any
OTHER error type (``invalid_arguments``, a kernel error, …) is a 422 rather
than a 200 body nobody inspects. ``merge_conflict`` is the single deliberate
exception — it comes back as an ``{"error": …}`` body at HTTP 200, exactly as
it does for ``POST …/merge``, so the UI can render the conflict list with its
existing modal instead of an error page.

The packet, render and diff routes are slice 4.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

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


async def _json(request: Request) -> dict:
    if not int(request.headers.get("content-length") or 0):
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
