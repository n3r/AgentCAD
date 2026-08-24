"""Package registry routes: registry passthroughs for the Library dialog.

    GET    /api/packages/search  ?query=&index=&limit=&keywords=&standards=
    GET    /api/projects/{proj}/packages
    POST   /api/projects/{proj}/packages            {name, version_req?, index?}
    DELETE /api/projects/{proj}/packages/{name}
    POST   /api/projects/{proj}/packages/{name}/use {part, part_id, preset?,
                                                     params?}
    GET    /api/packages/{name}/versions/{version}/preview?index=&path=

Body keys are whitelisted per route (the registry rejects unknown arguments,
and ``null`` must read as "omitted", not as an argument) — never ``**body``.

**No error type here is a legitimate HTTP 200 body**, and that is the
difference between this pack and `routes_checks`: a check *report* is evidence
even when it is red, but every failure a package operation can produce — an
unresolvable name, a tampered cache, a `part_id` that already exists, a
package in `packages` with no `packages_lock` entry — is a **refusal**. There
is nothing to render, so `_BODY_ERRORS` is empty and everything maps to
404/422/409.

`validate_package` and `publish` are deliberately **not** routed. The gate
builds every variant of a package on the shared kernel pool and is a
CLI-and-tool surface (`agentcad package validate`, the `validate_package`
tool); `--work-dir` cannot be widened from a running server anyway, because
the seatbelt profile is fixed at worker spawn. Publishing is CLI-only in this
feature (design spec, PRD divergence 6).

The publish gate is a **correctness** gate, not a security boundary: package
scripts run in your kernel worker with your privileges. The Library dialog
says so in visible text; these routes carry no affordance that could say it.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

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

#: What a preview may be. A preview path comes out of an index entry, which is
#: data from somewhere else, so it is re-checked here as well as resolved
#: inside the index root.
_PREVIEW_SUFFIX = ".png"


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
    ``content-length``, and trusting the header turns its body into "no
    arguments at all".
    """
    if not await request.body():
        return {}
    body = await request.json()
    return body if isinstance(body, dict) else {}


def _list_param(value: str | None) -> list | None:
    """A repeated-free comma list, because a `<select multiple>` is not what
    the dialog sends and `?keywords=a&keywords=b` would need a different
    signature for one filter."""
    if value is None:
        return None
    items = [part.strip() for part in value.split(",") if part.strip()]
    return items or None


def build_router(service, registry) -> APIRouter:
    router = APIRouter()

    @router.get("/packages/search")
    def search_packages(query: str | None = None, index: str | None = None,
                        keywords: str | None = None,
                        standards: str | None = None,
                        limit: int | None = None):
        args: dict = {}
        for key, value in (("query", query), ("index", index),
                           ("limit", limit)):
            if value is not None:
                args[key] = value
        for key, value in (("keywords", _list_param(keywords)),
                           ("standards", _list_param(standards))):
            if value is not None:
                args[key] = value
        return _result(registry.call("search_packages", args))

    @router.get("/projects/{proj}/packages")
    def list_packages(proj: str):
        return _result(registry.call("list_packages", {"project": proj}))

    @router.post("/projects/{proj}/packages")
    async def add_package(proj: str, request: Request):
        body = await _json(request)
        args = {"project": proj, **_body_keys(body, "name", "version_req",
                                              "index")}
        return _result(registry.call("add_package", args))

    @router.delete("/projects/{proj}/packages/{name}")
    def remove_package(proj: str, name: str):
        return _result(registry.call("remove_package",
                                     {"project": proj, "name": name}))

    @router.post("/projects/{proj}/packages/{name}/use")
    async def use_part(proj: str, name: str, request: Request):
        body = await _json(request)
        args = {"project": proj, "package": name,
                **_body_keys(body, "part", "part_id", "preset", "params")}
        return _result(registry.call("use_part", args))

    @router.get("/packages/{name}/versions/{version}/preview")
    def package_preview(name: str, version: str, path: str,
                        index: str | None = None):
        """Serve a preview image straight out of an index.

        A preview lives in the index, not in the project, so there is no
        project route that could serve it and no copy in the cache before the
        package is installed. The path is **caller data** — it comes back to
        us from a search hit — so it is resolved inside the version's own
        directory (`content.resolve_within`) and refused if it escapes, and it
        must be a `.png`. That is the same containment rule `LocalIndex.fetch`
        already applies to an entry's `path`.
        """
        from ..core.packages import content

        manager = service.packages
        candidates = ([manager.index_named(index)] if index
                      else list(manager.indexes))
        for candidate in candidates:
            try:
                root = candidate.fetch(name, version)
            except (NotFoundError, ValidationError):
                continue
            if not str(path).lower().endswith(_PREVIEW_SUFFIX):
                raise ValidationError(
                    f"a preview must be a {_PREVIEW_SUFFIX} file, got {path!r}")
            resolved = content.resolve_within(Path(root), path, what="preview")
            if not resolved.is_file():
                raise NotFoundError(
                    f"{name}@{version} has no preview at {path!r}")
            return FileResponse(resolved, media_type="image/png")
        raise NotFoundError(
            f"no configured index carries {name}@{version}"
            + (f" (asked {index!r})" if index else ""))

    return router
