"""Navigation routes: part search and the project dashboard (PRD-027 FR3/FR6).

    GET /api/projects/{proj}/search   ?q=&limit=
    GET /api/dashboard

A thin passthrough to the `search_parts` tool — the browser's filter box and
an agent must get the same answer to the same question, and the way to
guarantee that is for there to be one implementation with one caller shape,
not a route that reaches into `service.search` on its own terms.

Errors go through ``routes_configs._result`` (the `routes_bom`/`routes_drawing`
precedent): `search_parts` is zero-kernel and returns no ``ok`` post-state, so
every ``{"error": …}`` it can yield is a refusal — an unknown project is the
house 404, a bad query or an out-of-range ``limit`` the house 422 carrying the
grammar in its message.

The dashboard is the other shape: not a passthrough, because it is **not** an
agent verb (the tool count stays at three for this PRD) and there is nothing
for a tool to add — `navigation.dashboard` already is the one implementation.
It answers a listing built from manifests and the service's in-memory
``_status`` alone: no kernel call, no build, no render, no part script. Any
refusal it could raise is an `AppError`, which `app.py`'s handler already maps.

**Member-only**, by default-deny: this module is not in ``PUBLIC_PATHS`` /
``PUBLIC_PREFIXES``, and nothing here adds it. A search reads part ids, labels,
materials, folders, tags and — through the snippet — script text, which is
exactly the project content a member is allowed to see and an anonymous
visitor is not; the dashboard enumerates every project on the server, names
its path on disk and says how much of it is failing to build, which is if
anything the more sensitive of the two.
"""

from __future__ import annotations

from fastapi import APIRouter

from ..core.navigation import dashboard
from .routes_configs import _result


def build_router(service, registry) -> APIRouter:
    router = APIRouter()

    @router.get("/projects/{proj}/search")
    def search_parts(proj: str, q: str = "", limit: int | None = None):
        # `q` defaults to the empty string, which the grammar defines as "every
        # part in manifest order" — so a filter box that has just been cleared
        # is a listing, not a 422. `limit` is omitted rather than defaulted
        # here: the ONE default lives in `search.DEFAULT_LIMIT`, and FastAPI
        # has already refused a non-integer before the tool sees it.
        args = {"project": proj, "query": q}
        if limit is not None:
            args["limit"] = limit
        return _result(registry.call("search_parts", args))

    @router.get("/dashboard")
    def project_dashboard():
        # No `_result`: `dashboard` returns a plain payload and raises its own
        # refusals, so there is no `{"error": ...}` envelope to unwrap.
        return dashboard(service)

    return router
