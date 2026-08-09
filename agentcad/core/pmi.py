"""PMI / GD&T tolerance model: validation and normalization (pure Python).

A part entry in project.json may carry an optional ``"pmi"`` section —
Product and Manufacturing Information: toleranced dimensions, datum flags,
and feature control frames. This module owns the schema; validation happens
once at write time (set_part_pmi) and the drawing handler consumes the
*normalized* dict verbatim.

Shape::

    {"dims":   [{"id", "kind", "target", "plus", "minus", "note"?}],
     "datums": [{"id", "face"}],
     "fcf":    [{"id", "type", "tol_mm", "datums", "note"?}]}

- dims.kind "linear": target names an overall extent ("width" = X,
  "height" = Z as seen in the front view, "depth" = Y as seen in the top
  view). kind "diameter": target is the nominal hole diameter in mm, matched
  against diameters detected in the drawing's top view (within 0.05 mm).
- datums anchor a boxed letter to a face of the part's bounding box.
- fcf frames reference declared datum letters; position/perpendicularity/
  parallelism require at least one.

Unknown keys are rejected everywhere (typo safety, same philosophy as
materials.validate_material_entry). An empty dict clears PMI.
"""

from __future__ import annotations

import re

from .model import ValidationError

_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,15}$")
_DATUM_RE = re.compile(r"^[A-Z]$")

DIM_KINDS = ("linear", "diameter")
LINEAR_TARGETS = ("width", "height", "depth")
DATUM_FACES = ("top", "bottom", "left", "right", "front", "back")
FCF_TYPES = ("flatness", "position", "perpendicularity", "parallelism",
             "cylindricity")
FCF_DATUMS_REQUIRED = ("position", "perpendicularity", "parallelism")

MAX_NOTE_LEN = 80

_TOP_KEYS = ("dims", "datums", "fcf")
_DIM_KEYS = ("id", "kind", "target", "plus", "minus", "note")
_DATUM_KEYS = ("id", "face")
_FCF_KEYS = ("id", "type", "tol_mm", "datums", "note")


def _reject_unknown(entry: dict, known: tuple, what: str) -> None:
    unknown = sorted(set(entry) - set(known))
    if unknown:
        raise ValidationError(
            f"{what}: unknown field(s): {', '.join(unknown)}",
            {"unknown": unknown, "known": sorted(known)},
        )


def _number(entry: dict, key: str, what: str) -> float:
    if key not in entry:
        raise ValidationError(f"{what}: {key} is required")
    v = entry[key]
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValidationError(f"{what}: {key} must be a number")
    return float(v)


def _note(entry: dict, what: str) -> str | None:
    note = entry.get("note")
    if note is None:
        return None
    if not isinstance(note, str):
        raise ValidationError(f"{what}: note must be a string")
    if len(note) > MAX_NOTE_LEN:
        raise ValidationError(f"{what}: note must be <= {MAX_NOTE_LEN} characters")
    return note


def _section(pmi: dict, key: str) -> list:
    items = pmi.get(key) or []
    if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
        raise ValidationError(f"pmi.{key} must be an array of objects")
    return items


def _validate_dim(entry: dict, index: int) -> dict:
    what = f"pmi dim [{index}]"
    _reject_unknown(entry, _DIM_KEYS, what)
    dim_id = entry.get("id")
    if not isinstance(dim_id, str) or not _ID_RE.match(dim_id):
        raise ValidationError(
            f"{what}: id must match [a-z][a-z0-9_]{{0,15}}")
    what = f"pmi dim {dim_id!r}"
    kind = entry.get("kind")
    if kind not in DIM_KINDS:
        raise ValidationError(
            f"{what}: kind must be one of: {', '.join(DIM_KINDS)}",
            {"known": list(DIM_KINDS)},
        )
    target = entry.get("target")
    if kind == "linear":
        if target not in LINEAR_TARGETS:
            raise ValidationError(
                f"{what}: linear target must be one of: "
                f"{', '.join(LINEAR_TARGETS)}",
                {"known": list(LINEAR_TARGETS)},
            )
    else:  # diameter
        if isinstance(target, bool) or not isinstance(target, (int, float)) \
                or target <= 0:
            raise ValidationError(
                f"{what}: diameter target must be a positive number (nominal "
                "hole diameter, mm)")
        target = float(target)
    plus = _number(entry, "plus", what)
    minus = _number(entry, "minus", what)
    if plus < 0 or minus < 0:
        raise ValidationError(f"{what}: plus/minus must be >= 0")
    if plus == 0 and minus == 0:
        raise ValidationError(f"{what}: plus and minus must not both be 0")
    dim = {"id": dim_id, "kind": kind, "target": target,
           "plus": plus, "minus": minus}
    note = _note(entry, what)
    if note is not None:
        dim["note"] = note
    return dim


def _validate_datum(entry: dict, index: int) -> dict:
    what = f"pmi datum [{index}]"
    _reject_unknown(entry, _DATUM_KEYS, what)
    datum_id = entry.get("id")
    if not isinstance(datum_id, str) or not _DATUM_RE.match(datum_id):
        raise ValidationError(
            f"{what}: id must be a single uppercase letter A-Z")
    face = entry.get("face")
    if face not in DATUM_FACES:
        raise ValidationError(
            f"pmi datum {datum_id!r}: face must be one of: "
            f"{', '.join(DATUM_FACES)}",
            {"known": list(DATUM_FACES)},
        )
    return {"id": datum_id, "face": face}


def _validate_fcf(entry: dict, index: int, declared: set[str]) -> dict:
    what = f"pmi fcf [{index}]"
    _reject_unknown(entry, _FCF_KEYS, what)
    fcf_id = entry.get("id")
    if not isinstance(fcf_id, str) or not _ID_RE.match(fcf_id):
        raise ValidationError(
            f"{what}: id must match [a-z][a-z0-9_]{{0,15}}")
    what = f"pmi fcf {fcf_id!r}"
    fcf_type = entry.get("type")
    if fcf_type not in FCF_TYPES:
        raise ValidationError(
            f"{what}: type must be one of: {', '.join(FCF_TYPES)}",
            {"known": list(FCF_TYPES)},
        )
    tol = _number(entry, "tol_mm", what)
    if tol <= 0:
        raise ValidationError(f"{what}: tol_mm must be > 0")
    refs = entry.get("datums", [])
    if not isinstance(refs, list) or not all(isinstance(r, str) for r in refs):
        raise ValidationError(f"{what}: datums must be a list of datum letters")
    if len(set(refs)) != len(refs):
        raise ValidationError(f"{what}: duplicate datum reference")
    undeclared = sorted(set(refs) - declared)
    if undeclared:
        raise ValidationError(
            f"{what}: undeclared datum(s): {', '.join(undeclared)}",
            {"declared": sorted(declared)},
        )
    if fcf_type in FCF_DATUMS_REQUIRED and not refs:
        raise ValidationError(
            f"{what}: {fcf_type} requires at least one datum reference")
    fcf = {"id": fcf_id, "type": fcf_type, "tol_mm": tol, "datums": list(refs)}
    note = _note(entry, what)
    if note is not None:
        fcf["note"] = note
    return fcf


def validate_pmi(pmi: dict) -> dict:
    """Validate and normalize a part's PMI section.

    Returns ``{"dims": [...], "datums": [...], "fcf": [...]}`` (each section
    optional in the input, defaulting to empty). Raises ValidationError with
    known/unknown details on any unknown key or bad value.
    """
    if not isinstance(pmi, dict):
        raise ValidationError("pmi must be an object")
    _reject_unknown(pmi, _TOP_KEYS, "pmi")

    dims, dim_ids = [], set()
    for i, entry in enumerate(_section(pmi, "dims")):
        dim = _validate_dim(entry, i)
        if dim["id"] in dim_ids:
            raise ValidationError(f"duplicate pmi dim id {dim['id']!r}")
        dim_ids.add(dim["id"])
        dims.append(dim)

    datums, letters = [], set()
    for i, entry in enumerate(_section(pmi, "datums")):
        datum = _validate_datum(entry, i)
        if datum["id"] in letters:
            raise ValidationError(f"duplicate pmi datum id {datum['id']!r}")
        letters.add(datum["id"])
        datums.append(datum)

    fcfs, fcf_ids = [], set()
    for i, entry in enumerate(_section(pmi, "fcf")):
        fcf = _validate_fcf(entry, i, letters)
        if fcf["id"] in fcf_ids:
            raise ValidationError(f"duplicate pmi fcf id {fcf['id']!r}")
        fcf_ids.add(fcf["id"])
        fcfs.append(fcf)

    return {"dims": dims, "datums": datums, "fcf": fcfs}
