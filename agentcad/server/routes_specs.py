"""Design-spec routes: registry passthroughs.

    GET    /api/projects/{proj}/specs               ?part_id=      -> list_specs
    POST   /api/projects/{proj}/specs/run    {part_id, ref}        -> run_specs
    GET    /api/projects/{proj}/specs/file                  -> get_project_specs
    PUT    /api/projects/{proj}/specs/file   {script}       -> set_project_specs

Body keys are whitelisted per route (the registry rejects unknown arguments,
and ``null`` must read as "omitted", not as an argument) — never ``**body``.
Ordinary failures are re-raised as ``NotFoundError``/``ValidationError``/
``ConflictError`` so the app's handlers map them to 404/422/409 like every
other REST route, and any OTHER error type (``invalid_arguments``, a kernel
error, …) is a 422 rather than a 200 body nobody inspects.

``_BODY_ERRORS`` is **empty**: this pack has no error type that is a
legitimate HTTP 200 body. Everything about a *check* — a failure, a skip, a
broken predicate, a specs.py that will not execute — is payload rather than an
error in the first place, so a red project is a perfectly ordinary 200.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..core.model import ConflictError, NotFoundError, ValidationError

_RAISE = {
    "notfound_error": NotFoundError,
    "validation_error": ValidationError,
    "conflict_error": ConflictError,
}

# No error type here is a legitimate 200 body (see the module docstring).
_BODY_ERRORS: set[str] = set()


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
    """The body, or ``{}`` when there is none.

    Read the BYTES, not the header: a chunked request carries no
    ``content-length``, and trusting the header turns its body into "no
    arguments at all" — here, a write with no script rather than a 422.
    """
    if not await request.body():
        return {}
    body = await request.json()
    return body if isinstance(body, dict) else {}


def build_router(service, registry) -> APIRouter:
    # Every route is a registry passthrough, so ``service`` is unused here (the
    # routes_analysis shape): the tool pack owns the seams.
    router = APIRouter()

    @router.get("/projects/{proj}/specs")
    def list_specs(proj: str, part_id: str | None = None):
        args = {"project": proj}
        if part_id is not None:
            args["part_id"] = part_id
        return _result(registry.call("list_specs", args))

    @router.post("/projects/{proj}/specs/run")
    async def run_specs(proj: str, request: Request):
        body = await _json(request)
        args = {"project": proj, **_body_keys(body, "part_id", "ref")}
        return _result(registry.call("run_specs", args))

    @router.get("/projects/{proj}/specs/file")
    def get_project_specs(proj: str):
        return _result(registry.call("get_project_specs", {"project": proj}))

    @router.put("/projects/{proj}/specs/file")
    async def set_project_specs(proj: str, request: Request):
        body = await _json(request)
        # 'script' is required, and "" is a legitimate value (it deletes the
        # file) — so it rides _body_keys, whose test is `is not None`, and a
        # missing key becomes an invalid_arguments 422 from the registry.
        args = {"project": proj, **_body_keys(body, "script")}
        return _result(registry.call("set_project_specs", args))

    return router
