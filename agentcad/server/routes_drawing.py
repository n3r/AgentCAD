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


def build_router(service, registry) -> APIRouter:
    router = APIRouter()

    @router.post("/projects/{proj}/parts/{part_id}/drawing")
    async def make_drawing(proj: str, part_id: str, request: Request):
        # `_json` reads the BYTES (a chunked request carries no
        # content-length) and refuses a non-object body, which used to be an
        # `AttributeError` 500 out of the `body.get(...)` below.
        body = await _json(request)
        return _drawing_result(registry.call("generate_drawing", {
            "project": proj, "part_id": part_id,
            "views": body.get("views"), "format": body.get("format", "svg"),
            "config": body.get("config"),
            "dim_table": body.get("dim_table", False),
        }))

    @router.get("/projects/{proj}/parts/{part_id}/drawing.svg")
    def get_drawing_svg(proj: str, part_id: str, config: str | None = None,
                        dim_table: bool = False):
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
        _drawing_result(registry.call("generate_drawing", {
            "project": proj, "part_id": part_id, "format": "svg",
            "config": config, "dim_table": dim_table}))
        # The same suffix the tool wrote, derived the same way: a configuration
        # drawing lands beside the base one rather than overwriting it, so
        # reading the unsuffixed file back would serve the wrong sheet.
        suffix = f"_{config}" if config else ""
        svg = (service.store.exports_dir(proj) /
               f"{part_id}{suffix}_drawing.svg").read_bytes()
        return Response(content=svg, media_type="image/svg+xml",
                        headers={"Cache-Control": "no-store"})

    return router
