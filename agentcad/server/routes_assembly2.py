"""Single-instance transform route (gizmo write-back)."""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..core.model import ConflictError, NotFoundError, validate_vec3


def build_router(service, registry) -> APIRouter:
    router = APIRouter()

    @router.patch("/projects/{proj}/assembly/instances/{instance_id}")
    async def patch_instance(proj: str, instance_id: str, request: Request):
        body = await request.json()
        instances = service.store.instances(proj)
        target = next((i for i in instances if i.id == instance_id), None)
        if target is None:
            raise NotFoundError(f"instance {instance_id!r} not found")
        if target.mate:
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
        service.history.checkpoint(proj, f"Move {instance_id}")
        service.store.set_instances(proj, instances)
        service.bus.publish({"type": "project_changed", "project": proj})
        return service.get_assembly(proj)

    return router
