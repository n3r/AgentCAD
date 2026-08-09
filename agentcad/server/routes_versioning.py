"""Branch / version / merge routes: registry passthroughs.

    GET    /api/projects/{proj}/branches                 -> branch_list
    POST   /api/projects/{proj}/branches      {name, from}
    POST   /api/projects/{proj}/branches/switch {name}
    DELETE /api/projects/{proj}/branches/{name}   (name may contain '/')
    GET    /api/projects/{proj}/versions                 -> list_versions
    POST   /api/projects/{proj}/versions      {name, message}
    GET    /api/projects/{proj}/merge                    -> merge_status
    POST   /api/projects/{proj}/merge         {source, target, allow_invalid}
    POST   /api/projects/{proj}/merge/resolve {choices}
    POST   /api/projects/{proj}/merge/abort

Body keys are whitelisted per route (the registry rejects unknown arguments,
and ``null`` must read as "omitted", not as an argument). Ordinary failures
are re-raised as ``NotFoundError``/``ValidationError``/``ConflictError`` so the
app's handlers map them to 404/422/409 like every other REST route;
``merge_conflict`` is deliberately NOT one of those — it comes back as an
``{"error": …}`` body at HTTP 200, the way /api/tools/* passthroughs do, so a
UI can render the conflict list instead of an error page.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..core.model import ConflictError, NotFoundError, ValidationError

_RAISE = {
    "notfound_error": NotFoundError,
    "validation_error": ValidationError,
    "conflict_error": ConflictError,
}


def _result(payload: dict) -> dict:
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        cls = _RAISE.get(error.get("type"))
        if cls is not None:
            raise cls(error.get("message", ""), error.get("details"))
    return payload


def _body_keys(body: dict, *keys: str) -> dict:
    """Whitelisted, null-stripped forwarding — never ``**body``."""
    return {key: body[key] for key in keys
            if isinstance(body, dict) and body.get(key) is not None}


async def _json(request: Request) -> dict:
    if not int(request.headers.get("content-length") or 0):
        return {}
    body = await request.json()
    return body if isinstance(body, dict) else {}


def build_router(service, registry) -> APIRouter:
    router = APIRouter()
    if registry.get("branch_list") is None:
        return router  # no git: the tool pack registered nothing to route to

    @router.get("/projects/{proj}/branches")
    def list_branches(proj: str):
        return _result(registry.call("branch_list", {"project": proj}))

    @router.post("/projects/{proj}/branches")
    async def create_branch(proj: str, request: Request):
        body = await _json(request)
        args = {"project": proj, "name": body.get("name", ""),
                **_body_keys(body, "from")}
        return _result(registry.call("branch_create", args))

    @router.post("/projects/{proj}/branches/switch")
    async def switch_branch(proj: str, request: Request):
        body = await _json(request)
        return _result(registry.call(
            "branch_switch", {"project": proj, "name": body.get("name", "")}
        ))

    # ``{name:path}``: branch names may contain '/', so 'feat/x' is more than
    # one path segment. The name is whitelisted against the branch pattern by
    # BranchManager before it reaches git.
    @router.delete("/projects/{proj}/branches/{name:path}")
    def delete_branch(proj: str, name: str):
        return _result(registry.call(
            "branch_delete", {"project": proj, "name": name.strip("/")}
        ))

    @router.get("/projects/{proj}/versions")
    def list_versions(proj: str):
        return _result(registry.call("list_versions", {"project": proj}))

    @router.post("/projects/{proj}/versions")
    async def create_version(proj: str, request: Request):
        body = await _json(request)
        args = {"project": proj, "name": body.get("name", ""),
                **_body_keys(body, "message")}
        return _result(registry.call("version_tag", args))

    @router.get("/projects/{proj}/merge")
    def merge_status(proj: str):
        return _result(registry.call("merge_status", {"project": proj}))

    @router.post("/projects/{proj}/merge")
    async def merge_branch(proj: str, request: Request):
        body = await _json(request)
        args = {"project": proj, "source": body.get("source", ""),
                **_body_keys(body, "target", "allow_invalid")}
        return _result(registry.call("merge_branch", args))

    @router.post("/projects/{proj}/merge/resolve")
    async def resolve_merge(proj: str, request: Request):
        body = await _json(request)
        return _result(registry.call(
            "resolve_merge", {"project": proj, "choices": body.get("choices", {})}
        ))

    @router.post("/projects/{proj}/merge/abort")
    def abort_merge(proj: str):
        return _result(registry.call("merge_abort", {"project": proj}))

    return router
