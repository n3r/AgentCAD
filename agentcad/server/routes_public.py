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

from fastapi import APIRouter, Response
from fastapi.responses import FileResponse

from ..core.model import NotFoundError, ValidationError
from ..core.packages import format as pkgformat

#: Five minutes, on every response including the 404s. Design Decision 9
#: accepts that the anonymous surface is unmetered in 005-lite precisely
#: because a CDN or reverse proxy can absorb a flood — which is only true if
#: the responses say they are cacheable.
CACHE_CONTROL = "public, max-age=300"

#: What a preview may be, re-checked here as well as resolved inside the
#: version directory — the same two-part containment `routes_packages.py`
#: applies (`_PREVIEW_SUFFIX` there).
PREVIEW_SUFFIX = ".png"

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
    """Indexes an anonymous caller may read.

    `routes_packages.py`'s search and preview walk EVERY configured index,
    including a user's `scope: "private"` git index — exposing them would leak
    it. The scope property is PRD-011's, already load-bearing at publish time
    (`indexes.py:490-498` refuses non-redistributable vendor content into a
    public index); this route pack is the only consumer that filters on it for
    *access* rather than for policy.

    A `scope` that is anything but the literal ``"public"`` — including an
    index kind that has none at all — is refused, so the failure direction of
    a future index type is invisible rather than exposed.
    """
    return [index for index in service.packages.indexes
            if getattr(index, "scope", None) == "public"]


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
    raise NotFoundError(NO_SUCH_PACKAGE)


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

    @router.get("/public/packages/{name}")
    def public_package(name: str, response: Response):
        response.headers["cache-control"] = CACHE_CONTROL
        index, versions = _find(service, name)
        latest = pkgformat.resolve(versions, "*")
        if latest is None:
            raise NotFoundError(NO_SUCH_PACKAGE)
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
            raise NotFoundError(NO_SUCH_PACKAGE)
        return _document(index, name, version, entry)

    @router.get("/public/packages/{name}/versions/{version}/preview")
    def public_preview(name: str, version: str, path: str):
        """A shipped PNG, resolved inside the version's own directory.

        `path` is caller data (it comes back to us from a listing), so it gets
        the same two-part containment `routes_packages.py` applies: the `.png`
        suffix, then `content.resolve_within`, which refuses an absolute path,
        a `..` segment and anything a symlink resolves outside the root.
        """
        from ..core.packages import content

        index, versions = _find(service, name)
        if not isinstance(versions.get(version), dict):
            raise NotFoundError(NO_SUCH_PACKAGE)
        if not str(path).lower().endswith(PREVIEW_SUFFIX):
            raise ValidationError(
                f"a preview must be a {PREVIEW_SUFFIX} file, got {path!r}")
        root = index.fetch(name, version)
        resolved = content.resolve_within(Path(root), path, what="preview")
        if not resolved.is_file():
            raise NotFoundError(NO_SUCH_PACKAGE)
        return FileResponse(resolved, media_type="image/png",
                            headers={"cache-control": CACHE_CONTROL})

    return router
