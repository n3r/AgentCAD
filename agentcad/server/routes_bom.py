"""BOM routes: registry passthroughs, plus the CSV/JSON export streams.

    GET   /api/projects/{proj}/bom              ?structure=&config=&ref=
    GET   /api/projects/{proj}/bom.csv          ?structure=&config=&ref=
    GET   /api/projects/{proj}/bom.json         ?structure=&config=&ref=
    PATCH /api/projects/{proj}/parts/{part_id}/bom  {part_number, unit_cost_usd,
                                                      supplier, url, config}

Body/query keys are whitelisted (the registry rejects unknown arguments, and
``null``/absence must read as "omitted"). ``get_bom``/``export_bom`` are
zero-kernel (PRD-015 Decision 1) so the only errors they raise are the three
ordinary house types — this pack reuses ``routes_configs``'s strict ``_json``/
``_result``/``_body_keys`` rather than growing a second copy of the split (the
``routes_drawing`` precedent).

The two export routes render the download IN-MEMORY from ``get_bom`` via the
pure ``bom.to_csv``/``to_json`` (byte-identical to what the ``export_bom`` tool
writes), streamed with the right content-type and ``Cache-Control: no-store``.
They deliberately do NOT re-read the tool's shared ``exports/bom.<ext>`` file —
two concurrent downloads with different params would clobber/half-read it
(review MED-3). The ``export_bom`` tool still writes that one canonical file for
agents.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ..core import bom as _bom
from .routes_configs import _body_keys, _json, _result


def _query_args(project: str, structure: str | None, config: str | None,
                ref: str | None) -> dict:
    """The shared ``{project, structure?, config?, ref?}`` args for
    ``get_bom``/``export_bom`` — an absent query parameter is omitted so the
    tool's own defaults (``structure: flat``) apply."""
    args = {"project": project}
    if structure is not None:
        args["structure"] = structure
    if config is not None:
        args["config"] = config
    if ref is not None:
        args["ref"] = ref
    return args


def build_router(service, registry) -> APIRouter:
    router = APIRouter()

    @router.get("/projects/{proj}/bom")
    def get_bom(proj: str, structure: str | None = None,
               config: str | None = None, ref: str | None = None):
        return _result(registry.call(
            "get_bom", _query_args(proj, structure, config, ref)))

    def _export(proj: str, fmt: str, structure: str | None, config: str | None,
               ref: str | None) -> Response:
        # Render the download IN-MEMORY from `get_bom` (review MED-3): the
        # `export_bom` tool writes the ONE canonical `exports/bom.<fmt>` for
        # agents, so reading that shared file back would let two concurrent
        # downloads with different params (`?structure=flat` vs `indented`, a
        # `?ref=` pin vs the live tree) clobber or half-read each other's bytes.
        # The pure `to_csv`/`to_json` renderers over the `get_bom` result are
        # byte-identical to what the tool writes, with no shared file.
        bom = _result(registry.call("get_bom",
                                    _query_args(proj, structure, config, ref)))
        if fmt == "csv":
            content, content_type = _bom.to_csv(bom), "text/csv"
        else:
            content, content_type = _bom.to_json(bom), "application/json"
        return Response(
            content=content,
            media_type=content_type,
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/projects/{proj}/bom.csv")
    def get_bom_csv(proj: str, structure: str | None = None,
                    config: str | None = None, ref: str | None = None):
        return _export(proj, "csv", structure, config, ref)

    @router.get("/projects/{proj}/bom.json")
    def get_bom_json(proj: str, structure: str | None = None,
                     config: str | None = None, ref: str | None = None):
        return _export(proj, "json", structure, config, ref)

    @router.patch("/projects/{proj}/parts/{part_id}/bom")
    async def patch_bom(proj: str, part_id: str, request: Request):
        body = await _json(request)
        args = {"project": proj, "part_id": part_id,
                **_body_keys(body, "part_number", "unit_cost_usd", "supplier",
                            "url", "config")}
        return _result(registry.call("set_bom_fields", args))

    return router
