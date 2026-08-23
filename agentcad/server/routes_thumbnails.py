"""Thumbnail routes (PRD-027 FR4) — the part and assembly preview images.

    GET /api/projects/{proj}/parts/{part_id}/thumb.png[?k=<key>]
    GET /api/projects/{proj}/thumb.png[?k=<key>]

Its own pack rather than lines in `routes_configs`, so the thumbnail slice and
the search slice could be built side by side (design Decision 0), and because
what these two routes share is a caching contract, not a subject.

**This is the first non-`no-store` binary response in the codebase.** Every
mesh, render and drawing route answers `Cache-Control: no-store`, deliberately:
they are addressed by *part id*, so a cached copy would go stale the moment the
part rebuilt. These are addressed by **content hash** — `k` is the cache key the
client already learned from `get_project`'s `thumb_key` (or from a
`rebuild_finished` event, which now carries `cache_key`). So:

* `k` equals the key we are about to serve → the client named this exact
  content, and the answer can be `immutable` for a year. A rebuild mints a new
  key, so the browser asks for a different URL; nothing can be stale.
* `k` absent, malformed or stale → `no-cache`. The response is still returned in
  full and the browser revalidates next time with `If-None-Match`, which is a
  cheap 304 here because the ETag is the key and computing it needs no render.

A malformed `k` is **ignored, not refused**: it can only cost the client the
immutable answer, and a 422 on a cache hint would break an `<img>` tag for a
typo. Both routes are member-only by default-deny — nothing here joins
`PUBLIC_PATHS`.

Neither route ever builds. `thumbnails.part_thumb` / `assembly_thumb` render
only from meshes already on disk, so a dashboard opening twenty projects walks
twenty cache directories and starts zero kernel requests.
"""

from __future__ import annotations

import os
import re

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ..core import thumbnails
from ..core.model import NotFoundError

#: A build cache key (and, by construction, an assembly composite key): 32 hex
#: characters. `fullmatch`, never `match`/`$` — `$` also matches before a
#: trailing newline, which is the codebase's standing gate rule.
_KEY_RE = re.compile(r"[0-9a-f]{32}")

_IMMUTABLE = "private, max-age=31536000, immutable"
_REVALIDATE = "no-cache"


def _if_none_match(header: str | None, etag: str) -> bool:
    """Does the client already hold this exact entity?

    RFC 9110 allows a comma-separated list and a weak `W/` prefix; both are
    normalized away before comparing, and `*` matches anything we would serve.
    """
    if not header:
        return False
    for candidate in header.split(","):
        candidate = candidate.strip()
        if candidate == "*":
            return True
        if candidate.startswith("W/"):
            candidate = candidate[2:]
        if candidate == etag:
            return True
    return False


def _headers(key: str, k: str | None) -> dict:
    cacheable = bool(k) and _KEY_RE.fullmatch(k or "") is not None and k == key
    return {
        "ETag": f'"{key}"',
        "Cache-Control": _IMMUTABLE if cacheable else _REVALIDATE,
        "X-Thumb-Key": key,
    }


def _not_modified(key: str, k: str | None, request: Request) -> Response | None:
    """The 304, decided from the KEY ALONE — before anything is rendered.

    This is what makes the docstring's "cheap 304" true: resolving the key is a
    script hash (or an `_status` lookup), and the render or the disk read that
    would produce the body is never reached. It is also honest when the mesh
    has since been trimmed and we could no longer *produce* that PNG: the
    thumbnail is content-addressed, so the bytes the client holds for this key
    are the bytes for this key, permanently.
    """
    if _if_none_match(request.headers.get("if-none-match"), f'"{key}"'):
        return Response(status_code=304, headers=_headers(key, k))
    return None


def _image(png: bytes, key: str, k: str | None) -> Response:
    return Response(content=png, media_type="image/png",
                    headers=_headers(key, k))


def build_router(service, registry) -> APIRouter:
    router = APIRouter()

    # The warmer's thread starts HERE, not in `tools_navigation`. Route packs
    # are mounted only by `create_app`, so the background pre-warm exists in
    # exactly the process that serves these two routes — never in `agentcad
    # check`, the package gate, `bench run`, `share_build` or the MCP server,
    # all of which build a registry and would otherwise strand a daemon thread
    # (and could re-create a deleted check cell with a late render).
    # `start()` is idempotent, so a second `create_app` on one service reuses
    # the running thread; `AGENTCAD_THUMBNAILS=off` is honoured here because
    # this is now the only place that could start it.
    warmer = getattr(service, "thumbnails", None)
    if not isinstance(warmer, thumbnails.ThumbnailWarmer):
        warmer = service.thumbnails = thumbnails.ThumbnailWarmer(service)
    if os.environ.get("AGENTCAD_THUMBNAILS") != "off":
        warmer.start()

    @router.get("/projects/{proj}/parts/{part_id}/thumb.png")
    def part_thumb_png(proj: str, part_id: str, request: Request,
                       k: str | None = None):
        # Key first, so a revalidation costs no render and no file read. An
        # unknown part raises NotFoundError out of the record lookup here.
        key = thumbnails.part_key(service, proj, part_id)
        early = _not_modified(key, k, request)
        if early is not None:
            return early
        got = thumbnails.part_thumb(service, proj, part_id)
        if got is None:
            # The part exists (the key resolved) and has no mesh on disk.
            raise NotFoundError(
                f"part {part_id!r} has no built geometry to preview")
        png, served = got
        return _image(png, served, k)

    @router.get("/projects/{proj}/thumb.png")
    def assembly_thumb_png(proj: str, request: Request, k: str | None = None):
        # `assembly_key` walks the manifest and stats the cache; it renders
        # nothing and reads no PNG. It is None when no placed instance is
        # built, in which case only the part fallback can name the key.
        key = thumbnails.assembly_key(service, proj)
        if key is not None:
            early = _not_modified(key, k, request)
            if early is not None:
                return early
        got = thumbnails.assembly_thumb(service, proj)
        if got is None:
            raise NotFoundError(
                f"project {proj!r} has no built geometry to preview")
        png, served = got
        early = _not_modified(served, k, request)
        return early if early is not None else _image(png, served, k)

    return router
