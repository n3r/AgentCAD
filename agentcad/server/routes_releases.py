"""Release routes: registry passthroughs over the PRD-015 revision state
machine (FR6-9).

    GET  /api/projects/{proj}/releases                -> list_releases
    GET  /api/projects/{proj}/releases/{rev}           -> get_release
    POST /api/projects/{proj}/releases        {notes, waive} -> release_start
    POST /api/projects/{proj}/releases/{rev}/finalize -> release_finalize

Body keys are whitelisted (the registry rejects unknown arguments, and
``null``/absence must read as "omitted") via the ``routes_configs`` strict
``_json``/``_result``/``_body_keys`` split (the ``routes_drawing`` precedent).
A ``conflict_error`` — a finalized (``released``/``superseded``) record is
append-only (FR12), or an unapproved proposal at finalize — surfaces as the
house 409 the same way every other route pack answers ``ConflictError``.

Like ``routes_proposals``/``routes_versioning``, the whole pack self-disables
(an empty router) when the ``tools_releases`` pack registered nothing — no git
on PATH means no branches, so no proposals, so no releases.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from .routes_configs import _body_keys, _json, _result


def build_router(service, registry) -> APIRouter:
    router = APIRouter()
    if registry.get("release_start") is None:
        return router  # no git: the tool pack registered nothing to route to

    @router.get("/projects/{proj}/releases")
    def list_releases(proj: str):
        return _result(registry.call("list_releases", {"project": proj}))

    @router.get("/projects/{proj}/releases/{rev}")
    def get_release(proj: str, rev: str):
        return _result(registry.call(
            "get_release", {"project": proj, "rev": rev}))

    @router.post("/projects/{proj}/releases")
    async def start_release(proj: str, request: Request):
        body = await _json(request)
        args = {"project": proj, **_body_keys(body, "notes", "waive")}
        return _result(registry.call("release_start", args))

    @router.post("/projects/{proj}/releases/{rev}/finalize")
    def finalize_release(proj: str, rev: str):
        return _result(registry.call(
            "release_finalize", {"project": proj, "rev": rev}))

    return router
