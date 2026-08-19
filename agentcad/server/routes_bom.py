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

The two export routes mirror how ``routes_drawing`` streams a regenerated
drawing: call the tool (which writes ``exports/bom.<ext>``), then read those
exact bytes back off disk and stream them with the right content-type and
``Cache-Control: no-store`` — never the JSON envelope ``export_bom`` returns.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response

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
        args = _query_args(proj, structure, config, ref)
        args["format"] = fmt
        result = _result(registry.call("export_bom", args))
        # export_bom always lands the file in the REAL project's exports/ (a
        # ref is torn down with its throwaway worktree before this returns),
        # so `result["path"]` is a path this process can read right back.
        data = service.store.exports_dir(proj) / f"bom.{fmt}"
        content_type = "text/csv" if fmt == "csv" else "application/json"
        return Response(
            content=data.read_bytes(),
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
