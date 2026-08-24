"""Geometry CI routes: run a check, read the last one.

    POST   /api/projects/{proj}/checks   {ref, stages, strict, budget,
                                          proposal}          -> run_checks
    GET    /api/projects/{proj}/checks                 -> the last run's report
    GET    /api/projects/{proj}/checks?proposal=<id>   -> the report POSTED to
                                                          that proposal

Body keys are whitelisted per route (the registry rejects unknown arguments,
and ``null`` must read as "omitted", not as an argument) — never ``**body``.
Ordinary failures are re-raised as ``NotFoundError``/``ValidationError``/
``ConflictError`` so the app's handlers map them to 404/422/409 like every
other REST route, and any OTHER error type (``invalid_arguments``, a kernel
error, …) is a 422 rather than a 200 body nobody inspects.

``_BODY_ERRORS`` is **empty**, and here that is the whole philosophy of the
feature rather than an accident: everything a check *measured* — a part that
will not build, an interfering pair, a failing spec, a drawing that will not
generate — is payload, so a **red project is an ordinary 200**. Only "we could
not produce a verdict at all" (an unknown project, a ``ref`` on a project with
no git, an unknown stage name) is an HTTP error.

The GET is served from ``service.checks.last``, an in-memory, per-process cache
of the last report per project: it is what makes a browser tab that reconnects
after a ``check_finished`` event cheap. It is deliberately **not** persisted —
the durable copy of a report belongs to a proposal, not to a process.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..core.authz import PermissionDeniedError
from ..core.model import AuthError, ConflictError, NotFoundError, ValidationError

_RAISE = {
    "notfound_error": NotFoundError,
    "validation_error": ValidationError,
    "conflict_error": ConflictError,
    # FR6: a tenancy refusal keeps its own status (see routes_comments).
    "permission_error": PermissionDeniedError,
    "auth_error": AuthError,
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
    ``content-length``, and trusting the header would turn its body into "no
    arguments at all" — here, a full check where the caller asked for one
    stage.
    """
    if not await request.body():
        return {}
    body = await request.json()
    return body if isinstance(body, dict) else {}


def build_router(service, registry) -> APIRouter:
    router = APIRouter()

    @router.post("/projects/{proj}/checks")
    async def run_checks(proj: str, request: Request):
        body = await _json(request)
        args = {"project": proj,
                **_body_keys(body, "ref", "stages", "strict", "budget",
                             "proposal")}
        return _result(registry.call("run_checks", args))

    @router.get("/projects/{proj}/checks")
    def last_check(proj: str, proposal: str | None = None):
        # `service.checks` is installed by the tool pack, which is always
        # loaded before the app is built; the guard is for a hand-built
        # registry (a test, an embedder) rather than for a real server.
        runner = getattr(service, "checks", None)
        if runner is None:
            raise NotFoundError(
                "checks are not available on this service", {"project": proj})
        if proposal:
            # The DURABLE copy — the record posted to that proposal, which
            # outlives this process and is what its `checks` gate reads. 404
            # when nothing was posted, which is not the same answer as a green.
            return runner.posted_report(proj, proposal)
        return runner.last_report(proj)

    return router
