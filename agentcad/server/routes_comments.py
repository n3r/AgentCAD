"""Review-thread routes: the browser's half of PRD-008.

    GET    /api/projects/{proj}/comments   ?part_id=&state=&kind=&branch=
                                           &proposal=&anchor_status=
                                           &resolve_anchors=
    POST   /api/projects/{proj}/comments   {anchor|thread, body, attachments}
    GET    /api/projects/{proj}/comments/{tid}
    POST   /api/projects/{proj}/comments/{tid}/resolve
    POST   /api/projects/{proj}/comments/{tid}/reopen
    PATCH  /api/projects/{proj}/comments/{tid}/comments/{cid}   {body}
    DELETE /api/projects/{proj}/comments/{tid}/comments/{cid}
    GET    /api/projects/{proj}/comments/{tid}/audit
    GET    /api/projects/{proj}/notifications                   ?unread=
    POST   /api/projects/{proj}/notifications/read              {ids?}

The first four verbs and ``GET …/notifications`` are registry passthroughs,
because they are exactly the agent surface (``list_comments`` /
``add_comment`` / ``resolve_thread`` / ``reopen_thread`` /
``list_notifications``) and one implementation must serve both. The rest are
**not tools on purpose**: reading one thread, editing or deleting your own
comment, reading an audit log and moving a read cursor are panel affordances,
and FR7 freezes the agent surface at five tools. They call ``service.comments``
directly, whose structured errors the app's ``AppError`` handler maps
identically.

Both notification routes answer for **the identity of the request** — the
``X-Agent-Id`` the app's middleware bound — and never take one as an argument:
a client asking for somebody else's inbox would be asking a question this
server has no way to authorize.

Body keys are whitelisted per route (the registry rejects unknown arguments,
and ``null`` must read as "omitted", not as an argument) — never ``**body``.
That is also what keeps a client from posting ``state`` or ``author`` into a
thread document: those are the server's to stamp.

Ordinary failures are re-raised as ``NotFoundError``/``ValidationError``/
``ConflictError`` so the app's handlers map them to 404/422/409 like every
other REST route, and any OTHER error type (``invalid_arguments``, …) is a 422
rather than a 200 body nobody inspects. ``_BODY_ERRORS`` is **empty** and that
is the feature's philosophy rather than an omission: an ``orphaned`` or
``unverified`` anchor is *payload* — the honest answer to "where does this
point now" — and never an exception. Only "there is no such project, thread or
comment", "that anchor is invalid" and "that attachment is outside exports/"
are HTTP errors.

``GET`` with ``resolve_anchors=false`` is the cheapest listing: no resolution
work at all, for a panel that only needs counts and bodies.
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
    ``content-length``, and trusting the header would turn its body into "no
    arguments at all" — here, a comment with no text rather than a 422.
    """
    if not await request.body():
        return {}
    body = await request.json()
    return body if isinstance(body, dict) else {}


def build_router(service, registry) -> APIRouter:
    router = APIRouter()

    def manager():
        # `service.comments` is installed by the tool pack, which always loads
        # before the app is built (and never self-disables — comments need no
        # git). The guard is for a hand-built registry (a test, an embedder)
        # rather than for a real server.
        found = getattr(service, "comments", None)
        if found is None:
            raise NotFoundError("comments are not available on this service")
        return found

    @router.get("/projects/{proj}/comments")
    def list_comments(proj: str, part_id: str | None = None,
                      state: str | None = None, kind: str | None = None,
                      branch: str | None = None,
                      anchor_status: str | None = None,
                      proposal: str | None = None,
                      resolve_anchors: bool = True):
        args = {"project": proj, "resolve_anchors": resolve_anchors}
        for key, value in (("part_id", part_id), ("state", state),
                           ("kind", kind), ("branch", branch),
                           ("proposal", proposal),
                           ("anchor_status", anchor_status)):
            if value is not None:
                args[key] = value
        return _result(registry.call("list_comments", args))

    @router.post("/projects/{proj}/comments")
    async def add_comment(proj: str, request: Request):
        body = await _json(request)
        args = {"project": proj, "body": body.get("body", ""),
                **_body_keys(body, "anchor", "thread", "attachments")}
        return _result(registry.call("add_comment", args))

    @router.get("/projects/{proj}/comments/{tid}")
    def get_comment_thread(proj: str, tid: str):
        return {"thread": manager().get(proj, tid)}

    @router.post("/projects/{proj}/comments/{tid}/resolve")
    def resolve_thread(proj: str, tid: str):
        return _result(registry.call("resolve_thread",
                                     {"project": proj, "thread": tid}))

    @router.post("/projects/{proj}/comments/{tid}/reopen")
    def reopen_thread(proj: str, tid: str):
        return _result(registry.call("reopen_thread",
                                     {"project": proj, "thread": tid}))

    @router.patch("/projects/{proj}/comments/{tid}/comments/{cid}")
    async def edit_comment(proj: str, tid: str, cid: str, request: Request):
        body = await _json(request)
        return {"thread": manager().edit_comment(
            proj, tid, cid, body.get("body"))}

    @router.delete("/projects/{proj}/comments/{tid}/comments/{cid}")
    def delete_comment(proj: str, tid: str, cid: str):
        return {"thread": manager().delete_comment(proj, tid, cid)}

    @router.get("/projects/{proj}/notifications")
    def list_notifications(proj: str, unread: bool = False):
        # Passthrough: the drawer and an agent read the same list, for the
        # identity the X-Agent-Id middleware bound to this request.
        return _result(registry.call("list_notifications",
                                     {"project": proj, "unread": unread}))

    @router.post("/projects/{proj}/notifications/read")
    async def read_notifications(proj: str, request: Request):
        # Deliberately a route and not a tool (design Decision 11): FR7
        # freezes the agent surface at five tools, and a read cursor is a
        # drawer affordance. Omitting ``ids`` marks every unread one.
        body = await _json(request)
        return manager().mark_read(proj, body.get("ids"))

    @router.get("/projects/{proj}/comments/{tid}/audit")
    def thread_audit(proj: str, tid: str):
        # The append-only log of who did what, in order — the record the
        # lifecycle deliberately keeps out of the thread document.
        return {"thread": tid, "audit": manager().audit(proj, tid)}

    return router
