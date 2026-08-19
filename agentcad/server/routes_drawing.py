"""Drawing generation + SVG preview routes.

``generate_drawing`` returns **no** ``ok`` post-state — the kernel's
``{path, size_bytes, detected, …}`` on success — so every ``{"error": …}`` it
yields is a failure, in exactly one of two classes, and both routes answer
them through one helper (`_drawing_result`):

* an **`AppError`-class refusal** (unsupported format, unknown part, undeclared
  configuration, reference part) raises through `_result`: 404 or 422 with the
  house error type. The POST used to serve these as ``200 {"error": …}``, a
  success a caller had to sniff to disbelieve.
* a **kernel-class failure** (``script_error``, ``contract_error``,
  ``kernel_error``, ``timeout``, ``kernel_crash``) re-raises as a
  ``KernelError``, which `app.py` answers as **502** with the kernel's own type
  and ``details.traceback`` intact. The GET used to push these through
  `_RAISE`'s default too, answering ``422 ValidationError`` — "your request was
  invalid, do not retry" — for a worker timeout or crash, which is the opposite
  of the truth for the two retryable failures, and which destroyed the only
  place the kernel type reached HTTP.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ..core.model import ValidationError
# One grammar for a configuration name, not a second copy of it — the same
# module the manifest validator uses.
from ..core.packages.format import CONFIG_RE
from ..kernel.client import KernelError
# The constants themselves, never a retyped list: there is no `"crash"`, and
# `script_error`/`kernel_error` belong in it.
from ..kernel.protocol import (
    ERROR_CONTRACT,
    ERROR_CRASH,
    ERROR_KERNEL,
    ERROR_SCRIPT,
    ERROR_TIMEOUT,
)
# The house's strict body reader and refusal/post-state split live next door;
# importing them is what keeps a malformed body a 422 here too.
from .routes_configs import _json, _result

#: A kernel failure is not a bad request: `_RAISE`'s default would answer 422
#: and rename it "ValidationError"; the house answer for a KernelError is
#: app.py's 502 with the kernel's own type intact.
_KERNEL_TYPES = {ERROR_SCRIPT, ERROR_CONTRACT, ERROR_KERNEL, ERROR_TIMEOUT,
                 ERROR_CRASH}


def _drawing_result(payload: dict) -> dict:
    """`_result`, plus the kernel-class split (see the module docstring)."""
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict) and error.get("type") in _KERNEL_TYPES:
        raise KernelError(error["type"], error.get("message", ""),
                          error.get("details"))
    return _result(payload)


def _json_query(value: str | None, name: str):
    """Parse a JSON-encoded query parameter (the browser preview sends
    ``sections``/``details`` as ``JSON.stringify(...)``). A malformed value is a
    422 here, not a 500 in the tool — and the tool's own structural validator
    (``_validate_sections``/``_validate_details``) is the second gate on shape.
    """
    if value is None or value == "":
        return None
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        raise ValidationError(f"{name} must be a JSON value")


def _view_args(sheet, views, sections, details, scale, config, dim_table,
               hole_table=False):
    """The generate_drawing argument dict shared by the two GET preview routes.

    ``views`` arrives comma-separated (``?views=top,front``); ``sections`` and
    ``details`` as JSON; all optional so a bare ``?config=`` request is
    unchanged. ``None`` values are dropped so the tool's own defaults apply
    (a missing ``sheet`` stays ``iso_a3``, missing ``views`` stays all four).
    """
    args = {"config": config, "dim_table": dim_table, "hole_table": hole_table}
    if sheet:
        args["sheet"] = sheet
    if views:
        args["views"] = [v for v in views.split(",") if v]
    if scale is not None:
        args["scale"] = scale
    parsed_sections = _json_query(sections, "sections")
    if parsed_sections is not None:
        args["sections"] = parsed_sections
    parsed_details = _json_query(details, "details")
    if parsed_details is not None:
        args["details"] = parsed_details
    return args


def build_router(service, registry) -> APIRouter:
    router = APIRouter()

    @router.post("/projects/{proj}/parts/{part_id}/drawing")
    async def make_drawing(proj: str, part_id: str, request: Request):
        # `_json` reads the BYTES (a chunked request carries no
        # content-length) and refuses a non-object body, which used to be an
        # `AttributeError` 500 out of the `body.get(...)` below.
        body = await _json(request)
        # Forward the full drawing surface (PRD-014): sheet/sections/details/
        # scale ride the POST body the browser preview and agents send. Absent
        # keys are dropped so the tool's defaults apply; the tool is the one
        # validator for each (unknown sheet, malformed section, bad scale).
        args = {
            "project": proj, "part_id": part_id,
            "format": body.get("format", "svg"),
            "views": body.get("views"),
            "config": body.get("config"),
            "dim_table": body.get("dim_table", False),
        }
        for key in ("sheet", "sections", "details", "scale", "hole_table"):
            if body.get(key) is not None:
                args[key] = body[key]
        return _drawing_result(registry.call("generate_drawing", args))

    @router.get("/projects/{proj}/parts/{part_id}/drawing.svg")
    def get_drawing_svg(proj: str, part_id: str, config: str | None = None,
                        dim_table: bool = False, hole_table: bool = False,
                        sheet: str | None = None,
                        views: str | None = None, sections: str | None = None,
                        details: str | None = None, scale: float | None = None):
        # `dim_table` is a query flag so the browser preview can ask for the
        # tabulated sheet without a POST — FastAPI parses `?dim_table=1` and
        # `?dim_table=true` alike. It is ignored for a part with no family.
        #
        # The name is gated HERE and not only in the tool: this route joins it
        # into a filename it then reads, and `generate_drawing` refusing an
        # undeclared name first is an ordering three modules away. Applied with
        # `fullmatch` for the `_KEY_RE` reason — `$` also matches before a
        # trailing newline.
        if config is not None and not CONFIG_RE.fullmatch(config):
            raise ValidationError(
                f"configuration name {config!r} must match {CONFIG_RE.pattern}")
        # A refusal is an HTTP error, not a 200: this endpoint is declared to
        # serve SVG, so answering `content-type: application/json` at 200 is a
        # success the caller has to sniff to disbelieve.
        args = _view_args(sheet, views, sections, details, scale,
                          config, dim_table, hole_table)
        args.update({"project": proj, "part_id": part_id, "format": "svg"})
        _drawing_result(registry.call("generate_drawing", args))
        # The same suffix the tool wrote, derived the same way: a configuration
        # drawing lands beside the base one rather than overwriting it, so
        # reading the unsuffixed file back would serve the wrong sheet.
        suffix = f"_{config}" if config else ""
        svg = (service.store.exports_dir(proj) /
               f"{part_id}{suffix}_drawing.svg").read_bytes()
        return Response(content=svg, media_type="image/svg+xml",
                        headers={"Cache-Control": "no-store"})

    @router.get("/projects/{proj}/parts/{part_id}/drawing.pdf")
    def get_drawing_pdf(proj: str, part_id: str, config: str | None = None,
                        dim_table: bool = False, hole_table: bool = False,
                        sheet: str | None = None,
                        views: str | None = None, sections: str | None = None,
                        details: str | None = None, scale: float | None = None):
        # Mirrors the .svg route (PRD-014 Slice 2, FR11): regenerate the sheet
        # server-side through the SAME tool, then stream the deterministic PDF
        # bytes. The configuration name is gated HERE with `fullmatch` for the
        # same reason as the SVG route — this endpoint joins it into a filename
        # it then reads, and must not depend on the tool's ordering three
        # modules away — and a refusal is an HTTP error, not a 200 whose body
        # is JSON masquerading as a PDF.
        if config is not None and not CONFIG_RE.fullmatch(config):
            raise ValidationError(
                f"configuration name {config!r} must match {CONFIG_RE.pattern}")
        args = _view_args(sheet, views, sections, details, scale,
                          config, dim_table, hole_table)
        args.update({"project": proj, "part_id": part_id, "format": "pdf"})
        _drawing_result(registry.call("generate_drawing", args))
        suffix = f"_{config}" if config else ""
        pdf = (service.store.exports_dir(proj) /
               f"{part_id}{suffix}_drawing.pdf").read_bytes()
        return Response(content=pdf, media_type="application/pdf",
                        headers={"Cache-Control": "no-store"})

    return router
