"""Tool pack: generate 2D engineering drawings from a part.

``config`` and ``dim_table`` are PRD-012's half. A named configuration is
resolved **purely** (defaults < config, the part's own overrides never reach
it) by :meth:`service._record_for`, and its sheet is written beside the base
one as ``<part>_<config>_drawing.<ext>`` — so a family's drawings do not
overwrite each other.

``dim_table: true`` asks the worker for a *dimension table*: one row per
declared configuration, its configured parameters, and the overall X/Y/Z
extents **measured from that configuration's built shape**. The measurement
happens in the handler because that is this module's whole contract — a
drawing prints what the geometry is, never what a parameter claimed — and it
is why the request's timeout is scaled by the number of rows: each one is a
build.
"""

from __future__ import annotations

from .model import ValidationError
from .tools import Tool, schema

#: Per-configuration build allowance folded into the drawing request's timeout.
#: The base 120 s covers the projection and the SVG; every table row is one
#: more ``build_shape`` in the same call.
_ROW_TIMEOUT_S = 60.0


def register(registry, service) -> None:
    def generate_drawing(project: str, part_id: str, views: list | None = None,
                         format: str = "svg", config: str | None = None,
                         dim_table: bool = False) -> dict:
        if format not in ("svg", "dxf"):
            raise ValidationError("drawing format must be svg or dxf")
        # `_record_for` is the one validator: it refuses a reference part, a
        # non-string name and an undeclared configuration, and returns a
        # DERIVED record whose params are the pure configuration map.
        record = service._record_for(project, part_id, config)
        if record.kind != "script":
            raise ValidationError("drawings are supported for script parts only")
        script = service.store.read_script(project, part_id)
        # Forward the part's stored PMI section (tolerances/datums/FCF) so the
        # handler can render callouts. SVG only — DXF output ignores PMI (v1).
        pmi = next((entry.get("pmi")
                    for entry in service.store.manifest(project)["parts"]
                    if entry["id"] == part_id), None)
        suffix = f"_{config}" if config else ""
        out = service.store.exports_dir(project) / \
            f"{part_id}{suffix}_drawing.{format}"
        request = {
            "script": script, "params": record.effective_params,
            "views": views, "format": format,
            "out_path": str(out), "label": f"{project} / {part_id}",
            "pmi": pmi,
        }
        timeout = 120.0
        declared = record.configs or {}
        # Two ways this is a question rather than a fault, and neither costs
        # the kernel anything: a part with no configurations (the request
        # carries no table and the sheet is byte-identical to a plain call),
        # and a DXF request (DXF discards the table exactly as it discards
        # PMI, so measuring the family for it would buy a minute of builds per
        # eight members and throw every one of them away).
        if dim_table and declared and format == "svg":
            names = list(declared)                       # family order
            columns: list[str] = []                      # union, first-seen
            for name in names:
                # Through the same accessor the ROWS use, so the two cannot
                # disagree about what a member holds — and `config_params` is
                # total (a merged or hand-edited member that is not an object
                # with a params map resolves as an empty configuration), so
                # this needs no shape guard of its own.
                for param in record.config_params(name):
                    if param not in columns:
                        columns.append(param)
            request["dim_table"] = {
                "rows": [{"config": name,
                          "label": (declared[name].get("label")
                                    if isinstance(declared[name], dict)
                                    else None) or name,
                          "params": record.config_params(name)}
                         for name in names],
                "columns": columns,
            }
            timeout += _ROW_TIMEOUT_S * len(names)
        # Pinned to the part's worker, the house rule everywhere that issues
        # repeated builds of one part (`tools_holes` cites 11 354 ms -> 1 ms):
        # `dim_table` makes this request up to eight builds, and the browser
        # preview issues it twice (the POST, then the regenerating GET).
        result = service.kernel.request("drawing", request, timeout_s=timeout,
                                        affinity=part_id)
        if config:
            result["config"] = config
        table = (result.get("detected") or {}).get("dim_table")
        if table is not None:
            result["dim_table"] = table
        return result

    registry.register(Tool(
        "generate_drawing",
        "Generate a 2D engineering drawing (projected front/top/right/iso views "
        "with overall dimensions and hole callouts detected from geometry). "
        "Renders the part's PMI section (set_part_pmi) as toleranced dims, "
        "datum flags, and feature control frames — SVG only; DXF ignores PMI. "
        "With config, draws that configuration (pure resolution) and writes "
        "exports/<part>_<config>_drawing.<ext>. With dim_table, adds a boxed "
        "table of every configuration — its configured parameters and the "
        "overall X/Y/Z extents measured from each built shape (SVG only, up to "
        "8 rows). Formats: svg, dxf. Writes to exports/<part>_drawing.<ext>.",
        schema(
            {
                "project": {"type": "string"},
                "part_id": {"type": "string"},
                "views": {"type": "array", "description":
                          "subset of [top, front, right, iso]; default all"},
                "format": {"type": "string", "description": "svg | dxf"},
                "config": {"type": "string", "description":
                           "Configuration to draw (pure resolution); omit for "
                           "the current state"},
                "dim_table": {"type": "boolean", "description":
                              "Draw a per-configuration dimension table (SVG "
                              "only; ignored when the part has no "
                              "configurations)"},
            },
            ["project", "part_id"],
        ),
        generate_drawing,
    ))
