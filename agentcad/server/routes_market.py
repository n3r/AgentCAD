"""PRD-031a slice 2: the listing customizer — the ONE anonymous kernel path.

``routes_public.py`` promises **zero kernel**, so the two routes that legitimately
reach ``exec()`` in the worker live here, in an isolated, separately-reviewable
pack (design Decision 4):

    GET /api/public/packages/{name}/versions/{version}/parts/{part}/variant
    GET /api/public/packages/{name}/versions/{version}/parts/{part}/download/{fmt}
    GET /api/public/packages/{name}/versions/{version}/parts/{part}/mesh/{key}

The first two reach the kernel; the third — ``/mesh/{key}`` — is **kernel-free**
(PRD-031a slice 4). It serves the ``.acm`` bytes for a variant **already in the
build cache** and 404s an absent one, **never building** — the exact
``routes_share_public.py`` ``/s/{token}/mesh/{key}`` (and ``routes_configs``
``get_mesh_by_key``) discipline. It lives here, beside the ``/variant`` route it
completes, precisely as ``/s/``'s kernel-free mesh read sits beside ``/s/``'s
customizer routes; it closes the one functional gap slice 2 flagged — the browser
viewport needs the rebuilt mesh bytes a ``mesh_key`` names.

The whole risk of the slice is here, and it is closed by construction: this pack
opens **no second set of limits**. It reuses PRD-007's containment *verbatim* —

- ``_public_indexes`` + ``_find`` from ``routes_public`` (the dual ``scope:
  public`` filter, so a private listing is one name-free ``_miss``; **no
  ``refresh()``** on the anonymous path);
- ``require_customizer_capacity()`` — the ``pool_size − 1`` worker reservation,
  a 503 naming ``AGENTCAD_KERNEL_POOL_SIZE`` on a single-worker pool;
- ``service.customizer_guard`` — the per-IP ``TokenBucket`` + hourly login gate
  **shared with ``/s/``**, so a visitor cannot double their allowance across the
  two anonymous kernel paths (a per-*listing* bucket stays route-local here);
- ``ShareBuilder.build_catalog_variant`` / ``export_catalog_variant`` — the
  ``normalize_params`` parity, the ``paramclamp`` clamp *before* the variant
  cache key, the content-addressed variant cache and the process-global
  in-flight semaphore, all reached through the SAME ``_variant``/``_export`` tail
  the ``/s/`` path runs.

The catalog version's ``content_id`` (in ``index.json``) is already the immutable
pin, so there is **no ``Publication``, no share token**: the part's script bytes
are pinned by ``ensure_catalog_pin`` and the param spec is the pre-generated
index digest (Decision 3), so a variant is exactly one kernel call — the build.
Downloads are gated by a **fixed** export set ``{step, stl, 3mf}`` (founder
decision): a format outside it 404s **before** the builder.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, Response

from ..core import share_build
from ..core.materials import DEFAULT_MATERIAL
from ..core.model import RateLimitedError
from ..core.packages import content
from ..core.ratelimit import TokenBucket
from ..core.service import EXPORT_FORMATS
from ..core.share_build import ensure_share, require_customizer_capacity
from . import security as sec
from .routes_public import SCRIPT_SUFFIX, _find, _miss

PREFIX = "/api"

#: The fixed export mask for every listing (design Decision 6 / founder
#: decision): a catalog listing has no owner to carry a per-link mask, so every
#: listing offers the same set — exactly the ``EXPORT_FORMATS`` the kernel ships.
#: A format outside it 404s before the builder.
ALLOWED_EXPORTS = frozenset(EXPORT_FORMATS)

#: The catalog part's pinned material. The digest carries no per-part material,
#: so the customizer builds at the default density (a preview-fidelity choice —
#: geometry is exact; a mass metric uses the default material). Noted as a
#: PRD-031a residual, the same fallback ``ShareBuilder._ensure_project`` already
#: makes for a project-custom material.
PIN_MATERIAL = DEFAULT_MATERIAL


def build_router(service, registry) -> APIRouter:
    router = APIRouter()

    # The per-LISTING bucket is route-local (a popular listing is a different
    # subject from a popular share link); the per-IP bucket + login gate are the
    # SHARED `service.customizer_guard`. Never module-level: a second app in the
    # same worker must not inherit a drained bucket.
    listing_rate = TokenBucket(rate=share_build.SHARE_RATE_PER_S,
                               burst=share_build.SHARE_BURST)

    def _resolve_catalog_part(name: str, version: str, part: str):
        """``(spec, script_bytes)`` for a declared, customizable catalog part —
        or the one name-free ``_miss``.

        ``_find`` enforces the dual ``scope: public`` filter, so a private or
        nonexistent package is one indistinguishable 404 (no oracle). A part with
        **no declared params** has no customizer — the ``customizer: false``
        analogue, a ``_miss`` *before* the builder. The script bytes are a
        **local** read (``index.fetch`` + ``content.resolve_within`` with the
        fixed ``.py`` suffix — the anonymous path never ``refresh()``es)."""
        index, versions = _find(service, name)
        entry = versions.get(version)
        if not isinstance(entry, dict):
            raise _miss()
        parts = entry.get("parts")
        digest = parts.get(part) if isinstance(parts, dict) else None
        if not isinstance(digest, dict):
            raise _miss()
        spec = {p["name"]: p for p in (digest.get("params") or [])
                if isinstance(p, dict) and p.get("name")}
        if not spec:                         # no params → no customizer → 404
            raise _miss()
        root = index.fetch(name, version)
        script = content.resolve_within(
            Path(root), f"parts/{part}{SCRIPT_SUFFIX}", what="script")
        if not script.is_file():
            raise _miss()
        return spec, script.read_bytes()

    def _throttle(name: str, version: str, part: str, addr: str) -> None:
        """The per-LISTING bucket (route-local) AND the SHARED per-IP bucket —
        the same object ``/s/`` uses, so a visitor's per-address allowance is not
        doubled across the two surfaces."""
        if not listing_rate.take(f"catalog:{name}@{version}/{part}"):
            raise RateLimitedError(
                "too many customizer rebuilds; the page will retry shortly",
                {"retry_after_s": share_build.SHARE_RETRY_AFTER_S})
        service.customizer_guard.throttle(addr)

    def _guarded(request: Request, name: str, version: str, part: str):
        """The shared containment order for both kernel routes: refuse if no
        worker can be spared (503), then the shared login gate + the per-listing
        and shared per-IP buckets. Returns the resolved address."""
        require_customizer_capacity()
        guard = service.customizer_guard
        addr = guard.client_host(request)
        guard.gate(addr, authenticated=sec.current_principal() is not None)
        _throttle(name, version, part, addr)
        return addr

    @router.get(
        "/public/packages/{name}/versions/{version}/parts/{part}/variant")
    def market_variant(name: str, version: str, part: str, request: Request):
        """THE market kernel path: a logged-out visitor's bounded rebuild of a
        seeded catalog part.

        Order IS the containment: resolve the listing (dual-scope 404), confirm
        the part is customizable *before* the builder, then the shared
        containment (503 / gate / buckets), then pin the bytes and reach
        ``build_catalog_variant`` — which validates params to authoring parity,
        serves a repeat from the content-addressed variant cache with zero kernel
        calls, and caps a fresh build under the global in-flight semaphore."""
        builder = ensure_share(service)
        spec, script_bytes = _resolve_catalog_part(name, version, part)
        _guarded(request, name, version, part)
        pin = builder.ensure_catalog_pin(script_bytes, PIN_MATERIAL)
        result = builder.build_catalog_variant(
            pin["script_sha"], PIN_MATERIAL, spec, dict(request.query_params))
        return {"mesh_key": result["mesh_key"],
                "metrics": result.get("metrics"),
                "warnings": result.get("warnings", []),
                "lods": result.get("lods", []),
                "cached": bool(result.get("cached"))}

    @router.get(
        "/public/packages/{name}/versions/{version}/parts/{part}/download/{fmt}")
    def market_download(name: str, version: str, part: str, fmt: str,
                        request: Request):
        """A variant export honouring the fixed mask. A format outside
        ``{step, stl, 3mf}`` is a ``_miss`` **before** the builder (and before
        the listing is even resolved, so a bad format is one 404 regardless of
        whether the listing exists — no oracle). The export is content-addressed,
        so a repeat is a disk read."""
        builder = ensure_share(service)
        if fmt not in ALLOWED_EXPORTS:
            raise _miss()                    # export mask: 404 before the builder
        spec, script_bytes = _resolve_catalog_part(name, version, part)
        _guarded(request, name, version, part)
        pin = builder.ensure_catalog_pin(script_bytes, PIN_MATERIAL)
        path = builder.export_catalog_variant(
            pin["script_sha"], PIN_MATERIAL, spec, dict(request.query_params),
            fmt)
        filename = f"{part}_{path.stem[:8]}.{fmt}"
        return FileResponse(
            path, filename=filename, media_type="application/octet-stream",
            headers={"Cache-Control": "no-store"})

    @router.get(
        "/public/packages/{name}/versions/{version}/parts/{part}/mesh/{key}")
    def market_mesh(name: str, version: str, part: str, key: str):
        """The rebuilt mesh bytes the browser viewport fetches after a
        ``/variant`` returns a ``mesh_key`` — **kernel-free**, closing the one
        functional gap slice 2 flagged.

        It serves the ``.acm`` for a variant **already in the cache** and 404s
        an absent one; it **never builds** (the ``/s/{token}/mesh/{key}`` /
        ``get_mesh_by_key`` contract), so it is not guarded or rate-limited — a
        pure disk read. The listing is resolved by the SAME dual-``scope:
        public`` ``_resolve_catalog_part`` the variant route uses (so a private
        or nonexistent listing, or a non-customizable part, is one
        indistinguishable ``_miss``), and the ``.acm`` is located from the
        pinned ``script_sha`` alone — computed without registering a build —
        with ``ShareBuilder.mesh_path`` hex-gating ``key`` (``_is_cache_key``)
        against any path-traversal before it is joined to a directory."""
        builder = ensure_share(service)
        _spec, script_bytes = _resolve_catalog_part(name, version, part)
        path = builder.mesh_path(share_build.script_sha_for(script_bytes), key)
        if path is None:
            raise _miss()                    # absent key: 404, never a build
        return Response(
            content=path.read_bytes(),
            media_type="application/octet-stream",
            headers={"Cache-Control": "no-store", "X-Mesh-Key": key})

    return router
