"""Assembly-structure route pack (PRD-013): a thin, whitelisted HTTP surface
over the structure tools.

    POST /api/projects/{proj}/assembly/instances/{iid}/pattern -> set_pattern
    POST /api/projects/{proj}/assembly/subassemblies           -> add_subassembly
    PUT  /api/projects/{proj}/assembly/interface               -> set_assembly_interface
    POST /api/projects/{proj}/export/urdf                      -> export_urdf

This is a route pack (no `app.py` edit): `_mount_route_packs` discovers it by
name and mounts its `router` under `/api`. The name `routes_assembly2` is taken
(PRD-011/012's single-instance transform PATCH), so this pack owns the *new*
structure verbs. Bodies are whitelisted per route — never `**body`; the shared
helpers (`_result`, `_body_keys`, `_json`) come from `routes_configs`, so the
refusal-raises / build-post-state-is-200 discipline is one implementation.

The one subtlety, shared with `set_instance_config`: `set_pattern`'s default for
`pattern` is *clear it*, so `null` there is a real value, not "omitted". The
route therefore requires the key present and forwards it directly rather than
routing it through `_body_keys` (which strips `null`).
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..core.model import ValidationError
from .routes_configs import _body_keys, _json, _result


def build_router(service, registry) -> APIRouter:
    router = APIRouter()

    @router.post("/projects/{proj}/assembly/instances/{instance_id}/pattern")
    async def set_pattern(proj: str, instance_id: str, request: Request):
        body = await _json(request)
        # `null` clears the pattern (the tool default), so the key is REQUIRED
        # and forwarded verbatim — _body_keys would drop it.
        if "pattern" not in body:
            raise ValidationError(
                'pattern is required; send {"pattern": null} to clear it')
        return _result(registry.call("set_pattern", {
            "project": proj, "instance": instance_id,
            "pattern": body["pattern"]}))

    @router.post("/projects/{proj}/assembly/subassemblies")
    async def add_subassembly(proj: str, request: Request):
        body = await _json(request)
        args = {"project": proj,
                **_body_keys(body, "id", "source", "position", "rotation_deg")}
        return _result(registry.call("add_subassembly", args))

    @router.put("/projects/{proj}/assembly/interface")
    async def set_assembly_interface(proj: str, request: Request):
        body = await _json(request)
        # `exports` is required and `{}` legitimately clears the interface, so
        # it rides _body_keys (test `is not None`); a missing key becomes the
        # registry's own invalid_arguments 422.
        args = {"project": proj, **_body_keys(body, "exports")}
        return _result(registry.call("set_assembly_interface", args))

    @router.post("/projects/{proj}/export/urdf")
    async def export_urdf(proj: str, request: Request):
        body = await _json(request)
        args = {"project": proj, **_body_keys(body, "name", "mesh_format")}
        return _result(registry.call("export_urdf", args))

    return router
