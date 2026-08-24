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
  rather than on truthiness, because ``null`` there genuinely means *unbind* —
  and for the same reason it **requires** the key: absence cannot be read as
  "nothing to change" when the tool's default for it is the destructive verb.

``_BODY_ERRORS`` is **empty**, and the rule ``_result`` enforces is not "no
error dict is ever a 200 body" — it is **a refusal raises; a build post-state
is a 200 whatever its `ok`**. The two are distinguishable by shape:
``ToolRegistry.call`` emits exactly ``{"error": …}`` for a refusal (no ``ok``
key), while a rebuild always carries one. ``set_active_config`` merges its
rebuild at the top level, so a switch that lands and then fails to build is a
200 with ``ok: false`` — the same answer ``PATCH …/params`` has always given on
the identical failure. Answering 422 there threw away a post-state the manifest
had already committed, which is a client model no retry fixes. A red matrix row
is payload the same way (``build_configs`` returns ``ok: false`` per row and
never raises), so a project whose members fail to build is an ordinary 200.

**On a write path, a pre-build refusal is a build post-state too**, and that
qualifier is what makes the ``PATCH …/params`` precedent above true for *every*
failure class rather than only for a kernel one. The five tools that write and
then rebuild call ``service.rebuild_after_write``, which converts an
``AppError`` raised before the kernel is reached — the script file is gone, the
entry is gone, the material is unknown, a resolver refused — into the same
``{ok: false, error}`` post-state ``_build_with`` produces for a
``KernelError``, plus its own ``hint`` (which is what stops ``with_hint`` from
decorating it with the script-failure one). It had to: those refusals fire
*after* the manifest write, so leaving them to propagate answered 4xx for a
change ``project.json`` already held.

``service._rebuild`` itself is **not** total and must not be made so: it is
also the build every READ path runs (``_ensure_built`` ← ``get_metrics`` /
``mesh_info`` / ``ensure_mesh`` / ``mesh_summary`` / ``get_assembly``, plus
``packet``, ``checks`` and ``merge``), and those callers re-raise an
``ok: false`` as a ``KernelError`` — a 502. A missing script file is permanent
and client-side; it stays a 404 there.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ..core.authz import PermissionDeniedError
from ..core.model import AuthError, ConflictError, NotFoundError, ValidationError
# One grammar for a tier suffix, not a second copy of it: a lod names a cache
# sidecar file, so it must stay a plain token no matter what a query hands us.
from ..core.service import _LOD_SUFFIX_RE as _LOD_RE

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

#: A cache key is 32 lowercase hex characters (`service._cache_key`). The gate
#: is what keeps this route from becoming a path-traversal read of the project,
#: and it is applied with ``fullmatch``: ``$`` also matches *before* a trailing
#: newline, so an anchored ``.match`` would accept `"<key>\n"` and look for a
#: file whose name carries it.
_KEY_RE = re.compile(r"[0-9a-f]{32}")


def _result(payload: dict) -> dict:
    """Raise a refusal; return a build post-state.

    ``"ok" not in payload`` is the whole test (see the module docstring): a
    refusal envelope is exactly ``{"error": …}``, a post-state always carries
    ``ok``. Without it a landed write — manifest saved, ``project_changed``
    published — was served as a 422, and the browser's ``catch`` repainted the
    switcher from stale state while ``project.json`` already held the new
    configuration.
    """
    error = payload.get("error") if isinstance(payload, dict) else None
    if (isinstance(error, dict) and "ok" not in payload
            and error.get("type") not in _BODY_ERRORS):
        cls = _RAISE.get(error.get("type"), ValidationError)
        raise cls(error.get("message", ""), error.get("details"))
    return payload


def _body_keys(body: dict, *keys: str) -> dict:
    """Whitelisted, null-stripped forwarding — never ``**body``."""
    return {key: body[key] for key in keys
            if isinstance(body, dict) and body.get(key) is not None}


async def _json(request: Request) -> dict:
    """The body as an OBJECT, or ``{}`` when there is genuinely none.

    Read the BYTES, not the header: a chunked request carries no
    ``content-length``, and trusting the header turns its body into "no
    arguments at all".

    A body that parses to anything else is a **refusal**, not an empty one.
    Folding ``[]`` / ``"bad"`` / ``3`` into ``{}`` made malformed input
    indistinguishable from an absent body, and on the instance ``PATCH`` that
    difference is destructive: the tool's ``config: str | None = None`` default
    means *unbind*, so garbage silently cleared a live binding. It is also the
    one guard the house's other body-reading routes want (``app.py``'s export /
    ``PUT assembly`` / ``PATCH …/params`` and ``routes_drawing``'s POST import
    it from here), where the same shape was a 500.
    """
    if not await request.body():
        return {}
    try:
        body = await request.json()
    except RecursionError as exc:  # not a ValueError — the packages/_json.py trap
        raise ValidationError("body is nested too deeply to be a request",
                              {"error": "RecursionError"}) from exc
    if not isinstance(body, dict):
        raise ValidationError("body must be a JSON object",
                              {"got": type(body).__name__})
    return body


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
        # The key is REQUIRED, because its absence cannot mean "nothing to
        # change" here: the tool's default for `config` is *unbind*, so a PATCH
        # with nothing in it would be the most destructive call on the route.
        # `{"config": null}` is the one way to say it — the same shape the
        # sibling `PUT active-config` already enforces from the other side.
        if "config" not in body:
            raise ValidationError(
                'config is required; send {"config": null} to unbind')
        args = {"project": proj, "instance": instance_id,
                "config": body["config"]}   # null unbinds — forward it
        return _result(registry.call("set_instance_config", args))

    @router.get("/projects/{proj}/meshes/{key}")
    def get_mesh_by_key(proj: str, key: str, lod: str | None = None):
        """A built mesh by CACHE KEY — the assembly's mesh addressing.

        One identity for a mesh, so there is no ``?config=`` here: an instance
        bound to a configuration publishes its ``mesh_key`` and the browser
        fetches that. This route **never builds** (the browser cannot storm the
        kernel through it): a key with nothing on disk is a 404.
        """
        if not _KEY_RE.fullmatch(key):
            raise NotFoundError(f"{key!r} is not a mesh cache key")
        cache = service.store.cache_dir(proj)   # 404s an unknown project
        path = cache / f"{key}.acm"
        served = None
        # `fullmatch` for the same trailing-newline reason as the key gate.
        if lod and _LOD_RE.fullmatch(lod):
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
