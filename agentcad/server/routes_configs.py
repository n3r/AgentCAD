"""Configuration routes: registry passthroughs, plus the content-addressed mesh.

    GET    /api/projects/{proj}/configs                          -> list_configs
    GET    /api/projects/{proj}/parts/{part_id}/configs           -> list_configs
    PUT    /api/projects/{proj}/parts/{part_id}/configs           -> set_part_configs
    PUT    /api/projects/{proj}/parts/{part_id}/active-config     -> set_active_config
    DELETE /api/projects/{proj}/parts/{part_id}/active-config     -> set_active_config (base)
    POST   /api/projects/{proj}/configs/build                     -> build_configs
    PATCH  /api/projects/{proj}/assembly/instances/{iid}/config   -> set_instance_config
    GET    /api/projects/{proj}/meshes/{key}?lod=                 -> the built mesh

Body keys are whitelisted per route (the registry rejects unknown arguments,
and ``null`` must read as "omitted", not as an argument) — never ``**body``.

Two consequences of that whitelist are load-bearing here:

* ``_body_keys`` strips ``null``, so ``PUT active-config {"config": null}``
  cannot express "return to base" — it would arrive as *no argument at all* and
  quietly do the right thing for the wrong reason. The **DELETE** is that verb,
  and the PUT refuses a null body key instead of guessing.
* the instance ``PATCH`` therefore forwards ``config`` on ``"config" in body``
  rather than on truthiness, because ``null`` there genuinely means *unbind*.

``_BODY_ERRORS`` is **empty**: nothing about a configuration is a legitimate
HTTP 200 error body. A red matrix row is payload (``build_configs`` returns
``ok: false`` per row and never raises), so a project whose members fail to
build is an ordinary 200.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ..core.model import ConflictError, NotFoundError, ValidationError
# One grammar for a tier suffix, not a second copy of it: a lod names a cache
# sidecar file, so it must stay a plain token no matter what a query hands us.
from ..core.service import _LOD_SUFFIX_RE as _LOD_RE

_RAISE = {
    "notfound_error": NotFoundError,
    "validation_error": ValidationError,
    "conflict_error": ConflictError,
}

# No error type here is a legitimate 200 body (see the module docstring).
_BODY_ERRORS: set[str] = set()

#: A cache key is 32 lowercase hex characters (`service._cache_key`). The gate
#: is what keeps this route from becoming a path-traversal read of the project.
_KEY_RE = re.compile(r"^[0-9a-f]{32}$")


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


def build_router(service, registry) -> APIRouter:
    router = APIRouter()

    @router.get("/projects/{proj}/configs")
    def list_configs(proj: str):
        return _result(registry.call("list_configs", {"project": proj}))

    @router.get("/projects/{proj}/parts/{part_id}/configs")
    def list_part_configs(proj: str, part_id: str):
        return _result(registry.call("list_configs",
                                     {"project": proj, "part_id": part_id}))

    @router.put("/projects/{proj}/parts/{part_id}/configs")
    async def set_part_configs(proj: str, part_id: str, request: Request):
        body = await _json(request)
        # 'configs' is required and {} is a legitimate value (it clears the
        # family), so it rides _body_keys (whose test is `is not None`) and a
        # missing key becomes an invalid_arguments 422 from the registry.
        args = {"project": proj, "part_id": part_id,
                **_body_keys(body, "configs")}
        return _result(registry.call("set_part_configs", args))

    @router.put("/projects/{proj}/parts/{part_id}/active-config")
    async def set_active_config(proj: str, part_id: str, request: Request):
        body = await _json(request)
        if body.get("config") is None:
            raise ValidationError(
                "active-config requires a configuration name; DELETE this "
                "path to return the part to base"
            )
        args = {"project": proj, "part_id": part_id,
                **_body_keys(body, "config", "keep_overrides")}
        return _result(registry.call("set_active_config", args))

    @router.delete("/projects/{proj}/parts/{part_id}/active-config")
    def clear_active_config(proj: str, part_id: str):
        # Omitting `config` IS "return to base" (the tool's default).
        return _result(registry.call("set_active_config",
                                     {"project": proj, "part_id": part_id}))

    @router.post("/projects/{proj}/configs/build")
    async def build_configs(proj: str, request: Request):
        body = await _json(request)
        args = {"project": proj, **_body_keys(body, "part_id", "configs")}
        return _result(registry.call("build_configs", args))

    @router.patch("/projects/{proj}/assembly/instances/{instance_id}/config")
    async def set_instance_config(proj: str, instance_id: str,
                                  request: Request):
        body = await _json(request)
        args: dict = {"project": proj, "instance": instance_id}
        if "config" in body:
            args["config"] = body["config"]   # null unbinds — forward it
        return _result(registry.call("set_instance_config", args))

    @router.get("/projects/{proj}/meshes/{key}")
    def get_mesh_by_key(proj: str, key: str, lod: str | None = None):
        """A built mesh by CACHE KEY — the assembly's mesh addressing.

        One identity for a mesh, so there is no ``?config=`` here: an instance
        bound to a configuration publishes its ``mesh_key`` and the browser
        fetches that. This route **never builds** (the browser cannot storm the
        kernel through it): a key with nothing on disk is a 404.
        """
        if not _KEY_RE.match(key):
            raise NotFoundError(f"{key!r} is not a mesh cache key")
        cache = service.store.cache_dir(proj)   # 404s an unknown project
        path = cache / f"{key}.acm"
        served = None
        if lod and _LOD_RE.match(lod):
            tier = cache / f"{key}.{lod}.acm"
            if tier.is_file():
                path, served = tier, lod
        if not path.is_file():
            # Small parts have no tier, so a missing tier already fell back to
            # the full mesh above; reaching here means nothing is built.
            raise NotFoundError(f"mesh {key} is not built")
        return Response(
            content=path.read_bytes(),
            media_type="application/octet-stream",
            headers={
                "Cache-Control": "no-store",
                "X-Mesh-Key": key,
                "X-Mesh-Lod": served or "full",
            },
        )

    return router
