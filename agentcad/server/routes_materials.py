"""Materials v2 REST routes.

    GET  /api/materials?project&category&subcategory&filter=<json>  -> list_materials
    GET  /api/materials/{id}?project                                -> get_material
    POST /api/materials/find                                        -> find_materials
    PUT  /projects/{proj}/materials                                 -> set_project_materials

The GET/POST routes go through ``routes_configs._result`` (imported the way
``app.py`` already imports its body reader from there): a tool refusal is
exactly ``{"error": …}`` with no ``ok`` key, and ``_result`` raises the mapped
``AppError`` for it (``validation_error`` -> ``ValidationError`` -> 422 per
``app.py``'s ``_ERROR_STATUS``) rather than returning it as a 200 body. That
is the house convention for a read/search route — ``list_configs`` and every
other ``_result``-wrapped GET follow it — so ``GET /materials/{id}`` on an
unknown id and ``POST /materials/find`` with zero qualifying records both
answer **422**, not 404: the underlying refusal (``MaterialLibrary.resolve``,
``materials_query.find``) is a ``ValidationError``, and there is no
``NotFoundError`` in this pack to map to 404 instead.

``PUT /projects/{proj}/materials`` is UNCHANGED (slice 1): it returns
``registry.call(...)`` directly, so a validation error there is still a 200
``{"error": …}`` body — an existing inconsistency this slice does not touch.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Request

from ..core.model import ValidationError
from .routes_configs import _json as _object_body
from .routes_configs import _result


def _parse_filter(raw: str | None) -> dict | None:
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ValidationError("filter must be valid JSON", {"error": str(exc)}) from exc
    if not isinstance(parsed, dict):
        raise ValidationError("filter must be a JSON object",
                              {"got": type(parsed).__name__})
    return parsed


def build_router(service, registry) -> APIRouter:
    router = APIRouter()

    @router.get("/materials")
    def list_materials(project: str | None = None, category: str | None = None,
                       subcategory: str | None = None, filter: str | None = None):
        args = {"project": project, "category": category,
                "subcategory": subcategory, "filter": _parse_filter(filter)}
        args = {k: v for k, v in args.items() if v is not None}
        return _result(registry.call("list_materials", args))

    @router.get("/materials/{material_id}")
    def get_material(material_id: str, project: str | None = None):
        args = {"id": material_id}
        if project is not None:
            args["project"] = project
        return _result(registry.call("get_material", args))

    @router.post("/materials/find")
    async def find_materials(request: Request):
        body = await _object_body(request)
        return _result(registry.call("find_materials", body))

    @router.put("/projects/{proj}/materials")
    async def set_materials(proj: str, request: Request):
        body = await request.json()
        return registry.call(
            "set_project_materials",
            {"project": proj, "materials": body.get("materials", body)},
        )

    return router
