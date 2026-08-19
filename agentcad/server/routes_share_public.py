"""PRD-007 slice 3: the anonymous viewer surface (kernel-free).

Root-mounted (``PREFIX = ""``) so ``/s/<token>`` and ``/embed/<token>`` live at
the origin's root. The capability token rides in the *path*, not a query param:
the page sends ``Referrer-Policy: no-referrer`` so a shared model never leaks
its token through a ``Referer`` to an embedded/linked third party. **The token
is NOT log-invisible, though:** a reverse proxy (nginx/Caddy) logs the request
path by default, so the token lands in the proxy's access log — treat those logs
as containing secrets. The mitigations that make a path token acceptable are
elsewhere: immediate revocation and expiry (``publications.resolve``), and a
value that is 256 bits of ``secrets.token_urlsafe``. (uvicorn's own access log
is off by default in the hosted run; it is the proxy tier that logs.) Its
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

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, Response

from .._resources import resource_root
from ..core import share_build
from ..core.model import NotFoundError, RateLimitedError
from ..core.ratelimit import TokenBucket
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

# ----------------------------------------------------- customizer rate limits
#
# Two token buckets guard ``/variant`` and ``/download`` (design Decision 4):
# one keyed per LINK (``share:<pub_id>``) so a single popular link cannot flood
# the kernel — that one is ROUTE-LOCAL below — and one keyed per ADDRESS so one
# visitor cannot. The per-ADDRESS bucket AND the hourly login gate now live in
# ``share_build.CustomizerGuard`` (``service.customizer_guard``), SHARED with
# PRD-031a's ``/market`` customizer so a visitor cannot double their per-address
# allowance across the two anonymous kernel paths (PRD-031a AC4). The rate
# (``0.5/s`` burst ``15``), the login-gate knob
# (``AGENTCAD_SHARE_REQUIRE_LOGIN_ABOVE``) and the ``request.client.host`` M3
# discipline are all defined once in ``share_build``.
#
# **The address is only honest behind the documented reverse proxy.** It is
# ``request.client.host``, which uvicorn resolves from a BOUNDED
# ``X-Forwarded-For`` when ``forwarded_allow_ips`` names the trusted proxy
# (``appmode.trusted_proxy``, ``docs/deployment.md``). A visitor who could forge
# ``X-Forwarded-For`` would mint a fresh bucket per request and the per-IP cap
# would be a fiction — the PRD-005a review finding M3, kept from being repeated.


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

    # The per-LINK bucket is ROUTE-LOCAL (never module-level): a second app
    # built later in the same process — or the next test in a worker — must not
    # inherit a bucket a prior one drained. The per-ADDRESS bucket and the login
    # gate live in `service.customizer_guard` (installed by `ensure_share`),
    # SHARED with `/market`. The GLOBAL cap that actually bounds kernel
    # concurrency is `share_build.inflight_semaphore`; these only shape rate.
    link_rate = TokenBucket(rate=share_build.SHARE_RATE_PER_S,
                            burst=share_build.SHARE_BURST)

    def _live(token: str):
        """The live record for a token, or a 404. Also the seam that lazily
        installs the store — only ever from a server route, never from
        ``AgentCADService.__init__``."""
        builder = ensure_share(service)
        record = service.publications.resolve(token)
        if record is None:
            raise _miss()
        return record, builder

    def _client_host(request: Request) -> str:
        """The resolved address, via the shared guard (the M3 discipline)."""
        ensure_share(service)
        return service.customizer_guard.client_host(request)

    def _gate(request: Request, addr: str) -> None:
        """The optional login-above-N backstop, via the SHARED guard so ``/s/``
        and ``/market`` share one hourly counter. Off unless the knob is set; a
        signed-in member is never gated."""
        ensure_share(service)
        service.customizer_guard.gate(
            addr, authenticated=sec.current_principal() is not None)

    def _throttle(pub_id: str, addr: str) -> None:
        """The per-LINK bucket (route-local) AND the SHARED per-IP bucket; either
        over-limit is a ``quota_exceeded`` the page degrades to view-only on. The
        per-link check runs first, unchanged, so ``/s/`` behaviour is preserved;
        the per-IP check is now the same object ``/market`` uses."""
        if not link_rate.take(f"share:{pub_id}"):
            raise RateLimitedError(
                "too many customizer rebuilds; the page will retry shortly",
                {"retry_after_s": share_build.SHARE_RETRY_AFTER_S})
        ensure_share(service)
        service.customizer_guard.throttle(addr)

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

    # ------------------------------------------------- the customizer (exec)

    @router.get("/s/{token}/variant")
    def share_variant(token: str, request: Request):
        """THE kernel path: a logged-out visitor's bounded rebuild.

        The order IS the containment: resolve the token (404), refuse a
        non-customizer link **structurally before the builder** (the escalation
        boundary — the bit is owner-written, not in the request), apply the
        login-gate knob, take the per-link + per-IP buckets, and only then reach
        ``build_variant`` — which validates params to authoring parity, serves a
        repeat from the content-addressed cache with zero kernel calls, and caps
        a fresh build under the global in-flight semaphore."""
        record, builder = _live(token)
        if not (record.get("settings") or {}).get("customizer"):
            raise _miss()                       # customizer:false → 404, no build
        addr = _client_host(request)
        _gate(request, addr)
        _throttle(record["pub_id"], addr)
        result = builder.build_variant(record["pub_id"],
                                       dict(request.query_params))
        service.publications.bump(record["pub_id"], "rebuilds")
        return {"mesh_key": result["mesh_key"],
                "metrics": result.get("metrics"),
                "warnings": result.get("warnings", []),
                "lods": result.get("lods", []),
                "cached": bool(result.get("cached"))}

    @router.get("/s/{token}/download/{fmt}")
    def share_download(token: str, fmt: str, request: Request):
        """A variant export honouring the mask. A format absent from the mask —
        or a link with the customizer off — is a 404 **before** the builder;
        the export is content-addressed, so a repeat is a disk read."""
        record, builder = _live(token)
        settings = record.get("settings") or {}
        # A download carries visitor params, so it IS a rebuild: it requires the
        # customizer AND the format in the mask. Either failing is one 404,
        # structural, before any build — closing a params-via-download escalation.
        if not settings.get("customizer") or fmt not in (
                settings.get("exports") or []):
            raise _miss()
        addr = _client_host(request)
        _gate(request, addr)
        _throttle(record["pub_id"], addr)
        path = builder.export_variant(record["pub_id"],
                                      dict(request.query_params), fmt)
        service.publications.bump(record["pub_id"], "downloads")
        part = record.get("part_id") or "part"
        filename = f"{part}_{path.stem[:8]}.{fmt}"
        return FileResponse(
            path, filename=filename, media_type="application/octet-stream",
            headers={"Cache-Control": "no-store"})

    return router
