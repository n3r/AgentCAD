"""PRD-005a slice 7: the anonymous catalog read.

    GET /api/public/packages
    GET /api/public/packages/{name}
    GET /api/public/packages/{name}/versions/{version}
    GET /api/public/packages/{name}/versions/{version}/preview?path=

These four routes plus `/`, `/api/health`, `/api/auth/login` and the two
`/api/auth/enrol/{token}` methods are the **entire** anonymous surface of a
hosted instance (`server/security.py`, design Decision 8). Everything here is
one file read of a pre-generated document; nothing reaches
``service.kernel``, ``service.store`` or the tool registry, and
``tests/test_hosted_surface.py`` proves that by counting at the kernel's own
door rather than by assertion (FR16 / AC7).

**Why this is a separate pack and not a flag on `routes_packages.py` — the
single most important detail of Decision 8.** That pack's
``GET /api/packages/search`` iterates ``manager.indexes`` and its preview route
loops over every configured index. A user's ``scope: "private"`` index would be
searchable and its previews served. This pack does its own filtering
(:func:`_public_indexes`) *before* it looks anything up, so a private index is
not consulted at all — not consulted-and-then-hidden.

**Not inert in local mode.** ``routes_auth.py`` answers ``404`` without a
``SecurityConfig`` because identity state must not follow a service into
``checks._ephemeral_service``. This pack has no such state: it reads public
catalog files and is strictly *narrower* than the
``/api/packages/search`` a loopback bind already serves to anybody. Running the
same code in both modes is what keeps the scope filter on one path that the
whole suite exercises.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Query, Response
from fastapi.responses import FileResponse, PlainTextResponse

from ..core.model import NotFoundError, ValidationError
from ..core.packages import format as pkgformat
from ..core.packages import search as pkgsearch

#: Five minutes, on every response including the 404s. Design Decision 9
#: accepts that the anonymous surface is unmetered in 005-lite precisely
#: because a CDN or reverse proxy can absorb a flood — which is only true if
#: the responses say they are cacheable.
CACHE_CONTROL = "public, max-age=300"

#: What a preview may be, re-checked here as well as resolved inside the
#: version directory — the same two-part containment `routes_packages.py`
#: applies (`_PREVIEW_SUFFIX` there).
PREVIEW_SUFFIX = ".png"

#: A part's read-only script is one file inside ``parts/`` — the same
#: two-part containment the preview gets (`content.resolve_within` + a fixed
#: suffix), so a crafted ``part`` cannot read outside the version directory.
SCRIPT_SUFFIX = ".py"

#: **One message for every miss**, and deliberately name-free.
#:
#: A package carried only by a private index and a package that does not exist
#: must be indistinguishable, so the body cannot echo what was asked for: two
#: 404s that differ by a single quoted string are an existence oracle over the
#: private index. `test_a_private_index_is_invisible_and_indistinguishable`
#: compares the two bodies for equality, which is what stops a helpful message
#: from being added back.
NO_SUCH_PACKAGE = "no such package in the public catalog"


def _public_indexes(service):
    """Indexes an anonymous caller may read: **both scopes must say public.**

    `routes_packages.py`'s search and preview walk EVERY configured index,
    including a user's `scope: "private"` git index — exposing them would leak
    it. So this pack filters, and it filters on two properties:

    * ``index.configured_scope`` — what the **operator** wrote in
      `~/.agentcad/config.json`. This is the authority. PRD-011's
      ``index.scope`` lets the index *document* win, which is correct for
      publish policy (`indexes.py` refuses non-redistributable vendor content
      into a public index) and was wrong the moment PRD-005a reused it for
      access control: for a git index the third party who authors `index.json`
      would then decide whether the operator's instance serves it to the
      internet, overriding a `scope: "private"` the operator had set (review
      finding M2).
    * ``index.scope`` — the effective, document-aware answer, kept as well so
      the refusal direction is preserved: a document that says "private" still
      hides itself even from an operator who configured it public.

    Requiring agreement means a disagreement serves nothing, which is the
    fail-closed direction. Anything but the literal ``"public"`` on either —
    including an index kind carrying neither property — is refused, so a
    future index type is invisible rather than exposed.
    """
    return [index for index in service.packages.indexes
            if getattr(index, "configured_scope", None) == "public"
            and getattr(index, "scope", None) == "public"]


def _entries(index) -> dict:
    """``index.entries()``, or an empty document.

    One broken `index.json` must not take down a route with no credential in
    front of it — `load_indexes` already refuses to let a broken index hide the
    others at *load* time, and this is the same rule on *read*.
    """
    try:
        return index.entries() or {}
    except (NotFoundError, ValidationError):
        return {}


def _versions(index, name: str) -> dict:
    record = (_entries(index).get("packages") or {}).get(name)
    versions = record.get("versions") if isinstance(record, dict) else None
    return versions if isinstance(versions, dict) else {}


def _find(service, name: str) -> tuple[object, dict]:
    """The first *public* index carrying ``name``, and its versions.

    Precedence order, but only across the filtered list: a private index that
    is configured first cannot shadow a public package, because it was never
    in the list to be first in.
    """
    for index in _public_indexes(service):
        versions = _versions(index, name)
        if versions:
            return index, versions
    raise _miss()


def _miss() -> NotFoundError:
    """The one miss, carrying the cache header.

    Setting `response.headers` and *then* raising loses the header: the
    `Response` the handler was given is discarded and the exception handler
    builds a fresh one. So the 404s — the majority of a flood, since a flood
    asks for names that do not exist — went out uncacheable, which is exactly
    the traffic Decision 9 says a CDN absorbs (review finding m1). The house
    error contract carries `headers` for this; `core/model.AppError` renders
    them in `server/app.py`'s handler.
    """
    return NotFoundError(NO_SUCH_PACKAGE,
                         headers={"cache-control": CACHE_CONTROL})


def _version_key(version: str):
    """Descending version; never raises on one this build cannot parse."""
    try:
        return tuple(-part for part in pkgformat.parse_version(version))
    except ValidationError:
        return (0, 0, 0)


def _document(index, name: str, version: str, entry: dict) -> dict:
    """One index entry as it is served: exactly what `index.json` ships, plus
    the three facts the caller cannot derive (`name`, `version`, `index`)."""
    return {"name": name, "version": version, "index": index.name, **entry}


def build_router(service, registry) -> APIRouter:
    router = APIRouter()

    @router.get("/public/packages")
    def list_public_packages(response: Response):
        """Every package in every `scope: "public"` index, latest version.

        Enough to render a catalog card without a second request. Indexes are
        walked in precedence order and the first one carrying a name wins, so
        the listing agrees with what `/api/public/packages/{name}` will serve.
        """
        response.headers["cache-control"] = CACHE_CONTROL
        packages: dict[str, dict] = {}
        for index in _public_indexes(service):
            for name, record in sorted((_entries(index).get("packages") or {}).items()):
                if name in packages:
                    continue
                versions = record.get("versions") if isinstance(record, dict) else None
                if not isinstance(versions, dict):
                    continue
                version = pkgformat.resolve(versions, "*")
                if version is None:        # every version yanked, or none parses
                    continue
                packages[name] = _document(index, name, version, versions[version])
        return {"packages": [packages[name] for name in sorted(packages)]}

    @router.get("/public/packages/search")
    def public_search(
            response: Response,
            q: str | None = None,
            keyword: list[str] | None = Query(default=None),
            standard: list[str] | None = Query(default=None),
            license: str | None = None,
            param: str | None = None,
            param_min: float | None = None,
            param_max: float | None = None,
            limit: int | None = None):
        """Anonymous, public-scoped, **refresh-free** catalog search (PRD-031a
        FR1).

        Declared **before** ``/{name}`` so Starlette does not bind
        ``{name} == "search"``. It reuses ``search.search`` — one scoring
        implementation, one ranking, one ``why`` — but over
        :func:`_public_indexes` (the dual ``scope: public`` filter, so a private
        index is never scored) and with ``refresh=False`` (PRD-005a forbids a
        network fetch on the anonymous path — the M2 discipline). ``keyword``
        and ``standard`` are repeatable AND filters; ``license`` is a single
        AND filter; ``param``/``param_min``/``param_max`` is the range-overlap
        facet. Zero kernel — ``search`` runs entirely over parsed ``index.json``
        documents.
        """
        response.headers["cache-control"] = CACHE_CONTROL
        facet = ({"name": param, "min": param_min, "max": param_max}
                 if param else None)
        return pkgsearch.search(
            _public_indexes(service), query=q, keywords=keyword,
            standards=standard, license=license, param=facet,
            limit=pkgsearch.DEFAULT_LIMIT if limit is None else limit,
            refresh=False)

    @router.get("/public/packages/{name}")
    def public_package(name: str, response: Response):
        response.headers["cache-control"] = CACHE_CONTROL
        index, versions = _find(service, name)
        latest = pkgformat.resolve(versions, "*")
        if latest is None:
            raise _miss()
        return {"name": name, "index": index.name, "latest": latest,
                "versions": sorted(versions, key=_version_key),
                **{key: versions[latest].get(key)
                   for key in ("summary", "license", "disclosure", "keywords",
                               "standards")
                   if key in versions[latest]}}

    @router.get("/public/packages/{name}/versions/{version}")
    def public_version(name: str, version: str, response: Response):
        response.headers["cache-control"] = CACHE_CONTROL
        index, versions = _find(service, name)
        entry = versions.get(version)
        if not isinstance(entry, dict):
            raise _miss()
        return _document(index, name, version, entry)

    @router.get("/public/packages/{name}/versions/{version}/preview")
    def public_preview(name: str, version: str, path: str | None = None):
        """A shipped PNG, resolved inside the version's own directory.

        `path` is caller data (it comes back to us from a listing), so it gets
        the same two-part containment `routes_packages.py` applies: the `.png`
        suffix, then `content.resolve_within`, which refuses an absolute path,
        a `..` segment and anything a symlink resolves outside the root.

        It is declared **optional and checked here** so that a missing `path`
        is the house `{"error": {...}}` envelope rather than FastAPI's native
        `{"detail": [...]}` 422 — the only route on the anonymous surface that
        answered in a second error dialect, which a client written against
        this API has no reason to parse (review finding m5).
        """
        from ..core.packages import content

        index, versions = _find(service, name)
        if not isinstance(versions.get(version), dict):
            raise _miss()
        if not isinstance(path, str) or not path.strip():
            raise ValidationError(
                "a preview needs a 'path' query parameter naming a "
                f"{PREVIEW_SUFFIX} file shipped with this version",
                {"parameter": "path"})
        if not str(path).lower().endswith(PREVIEW_SUFFIX):
            raise ValidationError(
                f"a preview must be a {PREVIEW_SUFFIX} file, got {path!r}")
        root = index.fetch(name, version)
        resolved = content.resolve_within(Path(root), path, what="preview")
        if not resolved.is_file():
            raise _miss()
        return FileResponse(resolved, media_type="image/png",
                            headers={"cache-control": CACHE_CONTROL})

    def _part_or_miss(name: str, version: str, part: str) -> dict:
        """The digest of one declared part at a version, or the one name-free
        404. ``_find`` enforces the dual ``scope: public`` filter, so a private
        or nonexistent package is one indistinguishable miss (no oracle)."""
        _index, versions = _find(service, name)
        entry = versions.get(version)
        if not isinstance(entry, dict):
            raise _miss()
        parts = entry.get("parts")
        digest = parts.get(part) if isinstance(parts, dict) else None
        if not isinstance(digest, dict):
            raise _miss()
        return digest

    @router.get("/public/packages/{name}/versions/{version}/script/{part}")
    def public_script(name: str, version: str, part: str):
        """The read-only ``.py`` text of a declared part (PRD-031a FR2 — "the
        code, read-only with syntax highlight"), a kernel-free file read.

        ``_part_or_miss`` confirms the part is declared (dual-scope filtered),
        then the bytes are resolved with the preview route's containment —
        ``index.fetch`` (a **local** read; the anonymous path never
        ``refresh()``es) plus ``content.resolve_within`` with a fixed ``.py``
        suffix — so a crafted ``part`` cannot escape the version directory.
        The provenance header PRD-011 writes inside each script travels with
        the text; no separate call.
        """
        from ..core.packages import content

        index, _versions = _find(service, name)
        _part_or_miss(name, version, part)
        root = index.fetch(name, version)
        resolved = content.resolve_within(
            Path(root), f"parts/{part}{SCRIPT_SUFFIX}", what="script")
        if not resolved.is_file():
            raise _miss()
        return PlainTextResponse(
            resolved.read_text(encoding="utf-8"),
            media_type="text/plain; charset=utf-8",
            headers={"cache-control": CACHE_CONTROL})

    @router.get("/public/packages/{name}/versions/{version}/params/{part}")
    def public_params(name: str, version: str, part: str, response: Response):
        """The pre-generated param spec for a declared part (PRD-031a FR2 /
        Decision 3): the ``parts[part].params`` digest list, straight from
        ``index.json``. **Zero kernel** — no ``inspect`` — which is what keeps
        the browse surface kernel-free while a variant is exactly one kernel
        call. The customizer feeds this same list to ``normalize_params`` and
        ``_clamp_params`` (the market variant route), so param parity holds
        from pre-generated data.
        """
        response.headers["cache-control"] = CACHE_CONTROL
        digest = _part_or_miss(name, version, part)
        return {"name": name, "version": version, "part": part,
                "params": list(digest.get("params") or [])}

    return router
