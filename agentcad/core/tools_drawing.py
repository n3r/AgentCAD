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

import hashlib
import json

# `_sheets` is pure data (no OCP/build123d import — verified), so the server
# process may import it to validate the `sheet` argument against the one
# authoritative table.
from ..kernel.handlers._sheets import DEFAULT_SHEET, SHEETS
from .model import ValidationError
from .tools import Tool, schema

#: Per-configuration build allowance folded into the drawing request's timeout.
#: The base 120 s covers the projection and the SVG; every table row is one
#: more ``build_shape`` in the same call.
_ROW_TIMEOUT_S = 60.0

#: Per-section allowance. Each section cuts the built shape (a ``b3d.section``
#: per solid body) in the same pinned worker, so it is geometry work exactly as
#: a dim-table row is — the timeout scales by it, like ``_ROW_TIMEOUT_S``.
#: Details are a pure 2D clip of an already-computed projection, so they add
#: nothing.
_SECTION_TIMEOUT_S = 30.0

#: The section planes and the views a detail can magnify, named for the caller.
_SECTION_PLANES = ("xy", "xz", "yz")
_DETAIL_VIEWS = ("top", "front", "right", "iso")


def _is_number(value) -> bool:
    """A JSON number, excluding ``bool`` (which is an ``int`` in Python)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_sections(sections) -> list:
    """Validate + normalize the ``sections`` spec (FR6). Each entry names a
    plane, an ``offset_mm`` and an optional ``label``. A malformed entry raises
    ``ValidationError`` whose message NAMES the offending index (house
    contract)."""
    if not isinstance(sections, list):
        raise ValidationError("sections must be an array of "
                              "{plane, offset_mm, label?} objects")
    out: list = []
    for i, entry in enumerate(sections):
        if not isinstance(entry, dict):
            raise ValidationError(
                f"section[{i}] must be an object with a plane and an offset_mm")
        plane = entry.get("plane")
        if plane not in _SECTION_PLANES:
            raise ValidationError(
                f"section[{i}] plane must be one of "
                f"{', '.join(_SECTION_PLANES)} (got {plane!r})")
        offset = entry.get("offset_mm", 0.0)
        if not _is_number(offset):
            raise ValidationError(
                f"section[{i}] offset_mm must be a number (got {offset!r})")
        label = entry.get("label")
        if label is not None and not isinstance(label, str):
            raise ValidationError(f"section[{i}] label must be a string")
        out.append({"plane": plane, "offset_mm": float(offset), "label": label})
    return out


def _validate_details(details) -> list:
    """Validate + normalize the ``details`` spec (FR7). Each entry names a parent
    ``view``, a two-element ``center_mm``, a positive ``radius_mm`` and a
    positive ``scale``. A malformed entry raises ``ValidationError`` naming the
    offending index."""
    if not isinstance(details, list):
        raise ValidationError("details must be an array of "
                              "{view, center_mm, radius_mm, scale} objects")
    out: list = []
    for i, entry in enumerate(details):
        if not isinstance(entry, dict):
            raise ValidationError(f"detail[{i}] must be an object")
        view = entry.get("view")
        if view not in _DETAIL_VIEWS:
            raise ValidationError(
                f"detail[{i}] view must be one of {', '.join(_DETAIL_VIEWS)} "
                f"(got {view!r})")
        center = entry.get("center_mm")
        if (not isinstance(center, (list, tuple)) or len(center) != 2
                or not all(_is_number(c) for c in center)):
            raise ValidationError(
                f"detail[{i}] center_mm must be a two-element [x, y] of numbers")
        radius = entry.get("radius_mm")
        if not _is_number(radius) or radius <= 0:
            raise ValidationError(
                f"detail[{i}] radius_mm must be a positive number")
        scale = entry.get("scale")
        if not _is_number(scale) or scale <= 0:
            raise ValidationError(
                f"detail[{i}] scale must be a positive number")
        out.append({"view": view,
                    "center_mm": [float(center[0]), float(center[1])],
                    "radius_mm": float(radius), "scale": float(scale)})
    return out

#: The whitelisted title-block fields (Decision 4). Strings only, length-capped,
#: control chars refused; an empty string clears a field; unknown keys refused.
_DRAWING_FIELDS = ("company", "author", "project_code", "approved_by", "notes")
_MAX_FIELD_LEN = 200


def _filled(section: dict) -> dict:
    """The drawing section with every whitelisted field present (empty when
    unset) — a stable shape for round-tripping through get/set."""
    return {name: (section or {}).get(name, "") for name in _DRAWING_FIELDS}


def _validate_drawing_fields(fields) -> dict:
    """Validate a ``{field: value}`` map against the whitelist. Returns the
    cleaned map (unchanged values); raises ``ValidationError`` on an unknown
    key, a non-string value, an over-long value, or a control character."""
    if not isinstance(fields, dict):
        raise ValidationError("fields must be an object of drawing fields")
    unknown = sorted(k for k in fields if k not in _DRAWING_FIELDS)
    if unknown:
        raise ValidationError(
            f"unknown drawing field(s): {', '.join(unknown)}; "
            f"allowed: {', '.join(_DRAWING_FIELDS)}",
            details={"unknown": unknown, "allowed": list(_DRAWING_FIELDS)})
    clean: dict = {}
    for key, value in fields.items():
        if not isinstance(value, str):
            raise ValidationError(f"drawing field {key!r} must be a string")
        if len(value) > _MAX_FIELD_LEN:
            raise ValidationError(
                f"drawing field {key!r} is {len(value)} characters; the cap is "
                f"{_MAX_FIELD_LEN}")
        if any(ord(c) < 0x20 or ord(c) == 0x7F for c in value):
            raise ValidationError(
                f"drawing field {key!r} contains a control character")
        clean[key] = value
    return clean


def _content_hash(service, project: str) -> str:
    """A stable digest of the project's authored state (manifest + every part
    script). Same content ⇒ same digest, so the working-tree version ref is
    reproducible with no repo."""
    h = hashlib.sha256()
    manifest = service.store.manifest(project)
    h.update(json.dumps(manifest, sort_keys=True,
                        ensure_ascii=False).encode("utf-8"))
    for part_id in service.store.part_ids(project):
        try:
            script = service.store.read_script(project, part_id)
        except Exception:                                      # noqa: BLE001
            continue  # a reference part has no script — skip, don't fail
        h.update(b"\x00" + part_id.encode("utf-8") + b"\x00")
        h.update(script.encode("utf-8"))
    return h.hexdigest()


def _drawing_version(service, project: str) -> dict:
    """``{"ref", "date"}`` for the title block, computed service-side (FR2/FR12).

    Reaches history through ``service.history`` (``head``/``tags``/``log``) and
    the project path through ``service.store.path_of``. With a repo: ``ref`` is
    a tag pointing at HEAD, else the 7-char HEAD sha; ``date`` is HEAD's commit
    date (``%cI`` → the ``YYYY-MM-DD`` prefix). With NO repo/unborn: ``ref`` is
    ``"wt-" + sha256(manifest + scripts)[:7]`` and ``date`` is ``"-"``.

    **Never calls ``datetime.now()``** — the version date is the commit date or
    ``"-"``, so two renders at the same state produce identical bytes.
    """
    path = service.store.path_of(project)
    if service.history.available():
        head = service.history.head(path)
        if head:
            ref = head[:7]
            for tag in service.history.tags(path):
                if tag.get("commit") == head:
                    ref = tag["name"]
                    break
            rows = service.history.log(path, limit=1)
            date = str(rows[0]["ts"])[:10] if rows and rows[0].get("ts") else "-"
            return {"ref": ref, "date": date}
    return {"ref": "wt-" + _content_hash(service, project)[:7], "date": "-"}


def _mass_text(mass_g) -> str | None:
    """A readable mass string from grams, or None. Deterministic (no locale)."""
    if mass_g is None:
        return None
    if mass_g < 1000.0:
        return f"{mass_g:.1f} g"
    return f"{mass_g / 1000.0:.3f} kg"


def _mass_g(service, project: str, part_id: str, config: str | None):
    """Best-effort mass in grams for the (config's) built shape, or None. The
    build is cache-keyed on script+params, so this is a cache hit next to the
    drawing's own build."""
    try:
        if config:
            result = service._ensure_config_built(project, part_id, config)
        else:
            service.store.get_part(project, part_id)
            result = service._ensure_built(project, part_id)
        if result.get("ok"):
            return result["metrics"].get("mass_g")
    except Exception:                                          # noqa: BLE001
        return None
    return None


def register(registry, service) -> None:
    def generate_drawing(project: str, part_id: str, views: list | None = None,
                         format: str = "svg", config: str | None = None,
                         dim_table: bool = False, sheet: str = DEFAULT_SHEET,
                         scale: float | None = None,
                         version: dict | None = None,
                         sections: list | None = None,
                         details: list | None = None,
                         hole_table: bool = False) -> dict:
        if format not in ("svg", "dxf", "pdf"):
            raise ValidationError("drawing format must be svg, pdf, or dxf")
        # Section/detail specs are validated SERVICE-side (naming a bad entry's
        # index); the geometry itself happens in the kernel handler, which
        # already holds the built shape — no second kernel round-trip. DXF
        # renders neither (it is geometry exchange, no sheet wrapper), so the
        # specs are dropped for it exactly as PMI and the dim-table are.
        clean_sections = _validate_sections(sections) if sections else []
        clean_details = _validate_details(details) if details else []
        if sheet not in SHEETS:
            raise ValidationError(
                f"unknown sheet {sheet!r}; one of: {', '.join(sorted(SHEETS))}",
                details={"declared": sorted(SHEETS)})
        if scale is not None and (not isinstance(scale, (int, float))
                                  or isinstance(scale, bool) or scale <= 0):
            raise ValidationError("scale must be a positive number (a ratio, "
                                  "e.g. 2 for 2:1 or 0.5 for 1:2)")
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
        # Title-block data (FR2), all resolved SERVICE-side into plain strings —
        # the kernel handler renders them and never reads git or the clock.
        manifest = service.store.manifest(project)
        # `version` override (both {ref, date} strings) pins the title-block
        # version identity instead of deriving it from git — the "fixed-date"
        # path the geometry-CI determinism stage needs: a project and its
        # git-stripped mirror carry different git identity but identical
        # geometry, so the stage passes ONE fixed version to both sides and
        # compares the drawing, not the version cell (checks.py, the DXF-GUID
        # precedent). Absent, it is the real, deterministic project version.
        ver = version if isinstance(version, dict) else \
            _drawing_version(service, project)
        title = {
            "label": record.label,
            "units": "mm",
            "material": record.material,
            "mass": _mass_text(_mass_g(service, project, part_id, config)),
            "version_ref": str(ver.get("ref", "-")),
            "version_date": str(ver.get("date", "-")),
            **(manifest.get("drawing") or {}),   # only the set fields
        }
        request = {
            "script": script, "params": record.effective_params,
            "views": views, "format": format,
            "out_path": str(out), "label": f"{project} / {part_id}",
            "pmi": pmi, "sheet": sheet, "title": title,
        }
        if scale is not None:
            request["scale"] = float(scale)
        # Sections/details only reach the (SVG/PDF) sheet path; DXF ignores them.
        if format in ("svg", "pdf"):
            if clean_sections:
                request["sections"] = clean_sections
            if clean_details:
                request["details"] = clean_details
            # FR9: the hole table renders from the built shape's records (or the
            # detected circles), on the sheet path only — DXF has no annotation
            # layer. Opt-in, like the dimension table.
            if hole_table:
                request["hole_table"] = True
        timeout = 120.0
        # Each section is one build-shaped kernel op per solid body — scale the
        # request timeout by it, the same reasoning as the dim-table rows below.
        if format in ("svg", "pdf"):
            timeout += _SECTION_TIMEOUT_S * len(clean_sections)
        declared = record.configs or {}
        # Two ways this is a question rather than a fault, and neither costs
        # the kernel anything: a part with no configurations (the request
        # carries no table and the sheet is byte-identical to a plain call),
        # and a DXF request (DXF discards the table exactly as it discards
        # PMI, so measuring the family for it would buy a minute of builds per
        # eight members and throw every one of them away).
        if dim_table and declared and format in ("svg", "pdf"):
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
        # FR9: surface the hole table at the top level too (Decision 10), so an
        # agent can check "is every hole tabled?" without parsing `detected`.
        hole_table = (result.get("detected") or {}).get("hole_table")
        if hole_table is not None:
            result["hole_table"] = hole_table
        return result

    registry.register(Tool(
        "generate_drawing",
        "Generate a 2D engineering drawing (projected front/top/right/iso views "
        "with overall dimensions and hole callouts detected from geometry). "
        "Renders the part's PMI section (set_part_pmi) as toleranced dims, "
        "datum flags, and feature control frames — SVG and PDF; DXF ignores "
        "PMI. With config, draws that configuration (pure resolution) and "
        "writes exports/<part>_<config>_drawing.<ext>. With dim_table, adds a "
        "boxed table of every configuration — its configured parameters and the "
        "overall X/Y/Z extents measured from each built shape (SVG/PDF, up to "
        "8 rows). Formats: svg, pdf (deterministic vector), dxf. Writes to "
        "exports/<part>_drawing.<ext>.",
        schema(
            {
                "project": {"type": "string"},
                "part_id": {"type": "string"},
                "views": {"type": "array", "description":
                          "subset of [top, front, right, iso]; default all"},
                "format": {"type": "string", "description": "svg | pdf | dxf"},
                "config": {"type": "string", "description":
                           "Configuration to draw (pure resolution); omit for "
                           "the current state"},
                "dim_table": {"type": "boolean", "description":
                              "Draw a per-configuration dimension table (SVG/"
                              "PDF; ignored for DXF and when the part has no "
                              "configurations)"},
                "hole_table": {"type": "boolean", "description":
                               "Draw a hole table (SVG/PDF): tag, X/Y from the "
                               "top-view datum, and the standard designation "
                               "from PRD-010 hole metadata — or the detected "
                               "diameters when the part has no metadata. Prints "
                               "a tag at each hole"},
                "sheet": {"type": "string", "description":
                          "Sheet format: iso_a4|iso_a3|iso_a2|iso_a1|iso_a0|"
                          "ansi_a|ansi_b|ansi_c|ansi_d (landscape; default "
                          "iso_a3)"},
                "scale": {"type": "number", "description":
                          "Optional scale override as a ratio (2 = 2:1, 0.5 = "
                          "1:2); default auto from the preferred ladder"},
                "version": {"type": "object", "description":
                            "Advanced: pin the title-block version identity "
                            "{ref, date} instead of deriving it from git — for "
                            "deterministic regeneration (geometry-CI)"},
                "sections": {"type": "array", "description":
                             "Section views (SVG/PDF): a list of {plane: "
                             "xy|xz|yz, offset_mm: number, label?: string}. Each "
                             "cuts the shape and draws a hatched A-A, B-B, … view "
                             "with cutting-plane arrows on the parent view"},
                "details": {"type": "array", "description":
                            "Detail views (SVG/PDF): a list of {view, center_mm: "
                            "[x, y], radius_mm, scale}. Draws a labelled circle "
                            "on the parent view and a magnified A (n:1) view"},
            },
            ["project", "part_id"],
        ),
        generate_drawing,
    ))

    def set_drawing_fields(project: str, fields: dict) -> dict:
        """Set title-block fields at ``manifest["drawing"]`` (whitelist:
        company/author/project_code/approved_by/notes). Empty string clears a
        field; an empty section is omitted."""
        clean = _validate_drawing_fields(fields)
        manifest = service.store.manifest(project)  # notfound if missing
        section = dict(manifest.get("drawing") or {})
        for key, value in clean.items():
            if value == "":
                section.pop(key, None)              # empty string clears
            else:
                section[key] = value
        if section:
            manifest["drawing"] = section
        else:
            manifest.pop("drawing", None)           # empty section omitted
        service.store.save_manifest(project, manifest)
        service.bus.publish({"type": "project_changed", "project": project})
        return {"drawing": _filled(section)}

    def get_drawing_fields(project: str) -> dict:
        manifest = service.store.manifest(project)  # notfound if missing
        return {"drawing": _filled(manifest.get("drawing") or {})}

    registry.register(Tool(
        "set_drawing_fields",
        "Set the project's title-block fields (rendered by generate_drawing). "
        "Fields is a whitelist: company, author, project_code, approved_by, "
        "notes (strings, <=200 chars, no control characters). An empty string "
        "clears a field; unknown keys are refused. Stored at the top-level "
        "manifest 'drawing' section.",
        schema(
            {
                "project": {"type": "string"},
                "fields": {"type": "object", "description":
                           "Map of {company|author|project_code|approved_by|"
                           "notes: string}; '' clears a field"},
            },
            ["project", "fields"],
        ),
        set_drawing_fields,
    ))
    registry.register(Tool(
        "get_drawing_fields",
        "Get the project's title-block fields (every whitelisted field present, "
        "empty string when unset).",
        schema(
            {"project": {"type": "string"}},
            ["project"],
        ),
        get_drawing_fields,
    ))
