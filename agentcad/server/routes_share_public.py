"""PRD-007 slice 3: the anonymous viewer surface (kernel-free).

Root-mounted (``PREFIX = ""``) so ``/s/<token>`` and ``/embed/<token>`` live at
the origin's root, where the capability token belongs in the *path* (never a
query param, so it never lands in an access log or a ``Referer``). Its
authenticated twin — ``share_create/list/revoke`` — is ``routes_share.py`` at
``/api``; a route pack carries one ``PREFIX``, hence two packs.

Every handler resolves the token against the store and answers **one
indistinguishable 404** for a revoked, expired, unknown or wrong-secret token —
no existence oracle over what was ever published (design Decision 5), with the
cache header ``routes_public._miss`` uses so a CDN absorbs a 404 flood.

The six routes here make **zero** kernel calls. ``/mesh/{key}`` serves the
``.acm`` bytes for a key **already in the variant cache** and 404s an absent one
— the ``routes_configs.get_mesh_by_key`` discipline, **never builds**; ``/model``
and ``/params`` are file reads of sidecars the publish pin cached. That is what
makes "a viewer link reaches zero kernel" true (AC7). The two customizer routes
that *do* reach ``exec()`` — ``/variant`` and ``/download`` — are a LATER slice;
they are enumerated in ``test_hosted_surface.py`` but not mounted here yet.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, Response

from .._resources import resource_root
from ..core.model import NotFoundError
from ..core.share_build import ensure_share
from . import security as sec

PREFIX = ""

#: Five minutes on the 404s, so a CDN absorbs a flood of dead/forged tokens
#: (design Decision 9, ``routes_public.CACHE_CONTROL``).
CACHE_CONTROL = "public, max-age=300"

#: **One message for every miss**, name-free: a revoked link, an expired link, an
#: unknown token and a wrong secret must be one answer.
NO_SUCH_LINK = "no such share link"

_SHARE_HTML = resource_root() / "frontend" / "share.html"


def _miss() -> NotFoundError:
    """The one 404, carrying the cache header (the ``routes_public._miss`` shape:
    set-then-raise loses the header, so it rides the exception)."""
    return NotFoundError(NO_SUCH_LINK, headers={"cache-control": CACHE_CONTROL})


def _shell(*, embed: bool) -> HTMLResponse:
    """The self-contained viewer page. Served from disk when the slim bundle
    exists (slice 5), else a minimal placeholder shell — no external assets
    either way, and **never** a cookie (the page is fully anonymous)."""
    if _SHARE_HTML.is_file():
        html = _SHARE_HTML.read_text(encoding="utf-8")
    else:
        html = (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<title>AgentCAD — shared model</title></head>"
            "<body data-embed='{embed}'>"
            "<div id='agentcad-share'>Loading shared model…</div>"
            "</body></html>"
        ).format(embed="1" if embed else "0")
    return HTMLResponse(content=html, headers={"Cache-Control": "no-store"})


def build_router(service, registry) -> APIRouter:
    router = APIRouter()

    def _live(token: str):
        """The live record for a token, or a 404. Also the seam that lazily
        installs the store — only ever from a server route, never from
        ``AgentCADService.__init__``."""
        builder = ensure_share(service)
        record = service.publications.resolve(token)
        if record is None:
            raise _miss()
        return record, builder

    @router.get("/s/{token}")
    def share_page(token: str):
        record, _ = _live(token)
        # View counting is the page load only (human-paced, one store write),
        # NOT every asset fetch — a mesh flood must not become a write flood.
        service.publications.bump(record["pub_id"], "views")
        response = _shell(embed=False)
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @router.get("/embed/{token}")
    def embed_page(token: str):
        record, _ = _live(token)
        service.publications.bump(record["pub_id"], "views")
        response = _shell(embed=True)
        # Any site may embed the public, auth-free customizer (the growth loop,
        # founder decision). This wins over the app's `frame-ancestors 'none'`
        # because the middleware applies that with `setdefault` and excludes
        # `/embed/`.
        response.headers["Content-Security-Policy"] = "frame-ancestors *"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @router.get("/s/{token}/model")
    def share_model(token: str):
        """Attribution, metrics and the default variant's mesh cache key — a
        JSON read of the sidecar the pin pre-warmed. No kernel."""
        record, builder = _live(token)
        key = record["default_variant_key"]
        sidecar = builder.metrics_for(record["script_sha"], key) or {}
        settings = record.get("settings") or {}
        return {
            "attribution": {
                "project": record["project"],
                "part_id": record["part_id"],
                "ref": record["ref"],
                "created_by": record["created_by"],
            },
            "settings": {
                "customizer": bool(settings.get("customizer")),
                "exports": settings.get("exports") or [],
                "show_script": bool(settings.get("show_script")),
            },
            "default_variant_key": key,
            "metrics": sidecar.get("metrics"),
            "warnings": sidecar.get("warnings", []),
            "lods": sidecar.get("lods", []),
        }

    @router.get("/s/{token}/mesh/{key}")
    def share_mesh(token: str, key: str):
        """The ``.acm`` bytes for a key **already in the cache**. 404-if-absent,
        **never builds** — the ``get_mesh_by_key`` contract."""
        record, builder = _live(token)
        path = builder.mesh_path(record["script_sha"], key)
        if path is None:
            raise _miss()
        return Response(
            content=path.read_bytes(),
            media_type="application/octet-stream",
            headers={"Cache-Control": "no-store", "X-Mesh-Key": key})

    @router.get("/s/{token}/params")
    def share_params(token: str):
        """The typed slider spec (bounds/choices), a cached JSON read. No kernel."""
        record, builder = _live(token)
        return {"params_spec": builder.params_spec(record["script_sha"]) or {}}

    @router.get("/s/{token}/script")
    def share_script(token: str):
        """The pinned script text iff ``show_script``; else the same 404 as any
        miss — an off flag is not a distinct answer."""
        record, builder = _live(token)
        if not (record.get("settings") or {}).get("show_script"):
            raise _miss()
        text = builder.script_text(record["script_sha"])
        if text is None:
            raise _miss()
        return Response(content=text,
                        media_type="text/plain; charset=utf-8",
                        headers={"Cache-Control": "no-store"})

    return router
