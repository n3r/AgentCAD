"""Single-instance transform route (gizmo write-back) and, since PRD-027, the
instance's navigation folder (the assembly tree's drag-move target)."""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..core.model import ConflictError, NotFoundError, validate_vec3
from ..core.navigation import normalize_folder
from .routes_configs import _json as _object_body


def build_router(service, registry) -> APIRouter:
    router = APIRouter()

    @router.patch("/projects/{proj}/assembly/instances/{instance_id}")
    async def patch_instance(proj: str, instance_id: str, request: Request):
        # Every branch below is a `"key" in body` test, and `in` on a JSON
        # STRING is a substring test — a body of `"position"` would take the
        # mate refusal, and `["folder"]` would subscript a list. The house's
        # strict body reader (seven routes already share it) refuses a
        # non-object with a 422 and reads an absent one as `{}`, which here is
        # an honest no-op because every write below is key-guarded. This route
        # predates the helper; PRD-027 §5 gave it a second writable key, which
        # is when a second opinion about body shape stops being affordable.
        body = await _object_body(request)
        instances = service.store.instances(proj)
        target = next((i for i in instances if i.id == instance_id), None)
        if target is None:
            raise NotFoundError(f"instance {instance_id!r} not found")
        # The refusal is about the TRANSFORM, not the instance: a mate owns
        # position/rotation, so setting one by hand would be overwritten on
        # the next resolve. Filing the same instance in a folder — or
        # recoloring it — touches nothing the mate computes (PRD-027 §5).
        if ("position" in body or "rotation_deg" in body) and target.mate:
            raise ConflictError(
                f"instance {instance_id!r} is mate-driven; clear its mate before "
                "setting an explicit transform"
            )
        if "position" in body:
            target.position = validate_vec3(body["position"], "position")
        if "rotation_deg" in body:
            target.rotation_deg = validate_vec3(body["rotation_deg"], "rotation_deg")
        if "color" in body:
            target.color = body["color"]
        if "folder" in body:
            # `null` (and "") is root. Validated here as well as in the store
            # so a bad path is a 422 naming it, before the whole-list write.
            target.folder = normalize_folder(body["folder"])
        service.store.set_instances(proj, instances)
        service.bus.publish({"type": "project_changed", "project": proj})
        return service.get_assembly(proj)

    return router
