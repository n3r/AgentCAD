"""Material card lint (PRD-028 FR3/FR8) — pure: no service, no kernel.

Two profiles:

``library``
    what the shipped ``materials_data/`` is held to, and what the CLI runs by
    default: every structural rule is an error, **every property must name its
    source** (``missing_citation``, naming the property — AC5), density must be
    a point, ``subcategory`` is required, a ``process`` block must carry one
    citation, and a source naming a licensed aggregator is refused outright
    (``disallowed_source`` — a primary source must be cited instead).
``user``
    what a hand-written project/global entry is held to: v1 flat entries are
    fine, an uncited property is a *warning*, a range density is a *warning*,
    and ``subcategory`` is optional.

Envelope checks (:data:`ENVELOPES`, per category) are always warnings: the
bands are sanity rails for a typo'd exponent, not a claim about materials
science, so they never block a publish.

A note on the ``schema`` code: the rules run over the RAW card first, so a
wrong unit is ``unit_mismatch`` and not a generic parse failure;
:func:`materials.normalize_entry` then runs as the backstop and its refusal is
reported as ``schema`` **only when a rule that normalization would also have
raised on has already fired** (:data:`_NORMALIZE_CODES`). One wrong thing
produces one finding, and a citation problem never hides a schema problem.

Everything that can be judged from the raw card is judged there — citations,
taxonomy, density-is-a-point — so a card that fails to normalize still gets a
complete report instead of one line and a re-run.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

PROFILES = ("library", "user")

#: Codes for rules that `materials.normalize_entry` ALSO refuses. When one of
#: these has fired, its refusal is the same fact, so the generic ``schema``
#: finding is suppressed; any other error (an uncited property, say) leaves the
#: schema finding in place, because they are two different problems.
_NORMALIZE_CODES = frozenset({"invalid_id", "unit_mismatch", "range_inverted",
                              "table_not_monotonic", "cost_in_two_places"})

#: A `source` naming a licensed aggregator is an error in every profile: the
#: PRD's non-goal is importing their data, and citing them by name is exactly
#: the claim we must not make (AC7).
DISALLOWED_SOURCE_RE = re.compile(r"matweb|makeitfrom|prospector|granta",
                                  re.IGNORECASE)

#: Per-category sanity bands, `key -> (lo, hi)` inclusive. A value outside its
#: band is a WARNING naming the property — the usual cause is a unit slip
#: (kg/m3 written as g/cm3) or a decimal point.
ENVELOPES: dict[str, dict[str, tuple[float, float]]] = {
    "metal": {"density_g_cm3": (0.5, 23.0), "E_gpa": (10.0, 450.0),
              "yield_mpa": (10.0, 2500.0),
              "max_service_temp_c": (50.0, 1400.0)},
    "polymer": {"density_g_cm3": (0.8, 2.5), "E_gpa": (0.001, 20.0),
                "max_service_temp_c": (-50.0, 400.0)},
    "composite": {"density_g_cm3": (0.9, 2.5), "E_gpa": (2.0, 300.0)},
    "wood": {"density_g_cm3": (0.1, 1.4), "E_gpa": (1.0, 30.0)},
    "masonry": {"density_g_cm3": (1.0, 3.0), "E_gpa": (5.0, 60.0)},
    "ceramic": {"density_g_cm3": (1.5, 7.0), "E_gpa": (20.0, 500.0)},
}


@dataclass
class Finding:
    code: str
    level: str            # error | warning
    id: str
    message: str
    property: str | None = None
    file: str | None = None

    def to_dict(self) -> dict:
        return {"code": self.code, "level": self.level, "id": self.id,
                "message": self.message, "property": self.property,
                "file": self.file}


def has_errors(findings) -> bool:
    return any(f.level == "error" for f in findings)


def _sort_key(f: Finding) -> tuple:
    return (f.file or "", f.id, f.property or "", f.code)


def _materials():
    """The schema module, imported lazily.

    ``materials`` imports this module inside its loader (a shipped card is
    linted before it becomes a ``Material``), so a module-level import here
    would be a cycle that breaks whichever module is imported first.
    """
    from . import materials

    return materials


# ------------------------------------------------------------------- cards

def lint_card(material_id, card, profile: str = "library", *,
              file=None) -> list[Finding]:
    """Lint one card (or one v1 flat entry). Findings are sorted."""
    if profile not in PROFILES:
        raise ValueError(f"unknown lint profile {profile!r}")
    m = _materials()
    library = profile == "library"
    path = None if file is None else str(file)

    def finding(code, level, message, prop=None) -> Finding:
        return Finding(code, level, str(material_id), message, prop, path)

    def named(message: str) -> str:
        return f"material {material_id!r}: {message}"

    findings: list[Finding] = []
    if not isinstance(material_id, str) or not m._ID_RE.match(material_id or ""):
        findings.append(finding("invalid_id", "error",
                                f"invalid material id {material_id!r}"))
    if not isinstance(card, dict):
        findings.append(finding("schema", "error", named("entry must be an object")))
        return sorted(findings, key=_sort_key)

    findings.extend(_raw_findings(m, card, library, named, finding))

    try:
        material = m.normalize_entry(str(material_id), card, "lint")
    except m.ValidationError as exc:
        if not any(f.code in _NORMALIZE_CODES for f in findings):
            findings.append(finding("schema", "error",
                                    getattr(exc, "message", str(exc))))
        return sorted(findings, key=_sort_key)
    except ValueError as exc:  # a card the raw pass could not even read
        findings.append(finding("schema", "error", named(str(exc))))
        return sorted(findings, key=_sort_key)

    findings.extend(_resolved_findings(material, named, finding))
    return sorted(findings, key=_sort_key)


def _numbers(row) -> bool:
    return (isinstance(row, (list, tuple)) and len(row) == 2
            and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                    for v in row))


def _raw_findings(m, card: dict, library: bool, named, finding) -> list[Finding]:
    """Every rule that can be judged from the raw card.

    Deliberately defensive: the card may be any shape at all, so every check
    guards its own types and simply says nothing when it cannot judge —
    ``normalize_entry`` is the backstop that refuses the rest.
    """
    findings: list[Finding] = []
    level = "error" if library else "warning"
    props = card.get("properties")
    flat = props is None

    if isinstance(props, dict):
        for key, obj in props.items():
            if key not in m.PROPERTY_UNITS or not isinstance(obj, dict):
                continue
            canonical = m.PROPERTY_UNITS[key]
            unit = obj.get("unit")
            if unit != canonical:
                findings.append(finding(
                    "unit_mismatch", "error",
                    named(f"property {key!r} unit {unit!r} must be "
                          f"{canonical!r}"), key))
            rng = obj.get("range")
            if _numbers(rng) and rng[0] > rng[1]:
                findings.append(finding(
                    "range_inverted", "error",
                    named(f"property {key!r} range {list(rng)} must have "
                          "lo <= hi"), key))
            findings.extend(_table_findings(key, obj, named, finding))
            source = obj.get("source")
            if source is None:
                findings.append(finding(
                    "missing_citation", level,
                    named(f"property {key!r} has no source"), key))
            elif isinstance(source, str) and DISALLOWED_SOURCE_RE.search(source):
                findings.append(finding(
                    "disallowed_source", "error",
                    named(f"property {key!r} cites a licensed aggregator "
                          f"({source!r}); cite the primary source instead"),
                    key))
            if key == "density_g_cm3" and obj.get("range") is not None:
                findings.append(finding(
                    "density_must_be_point", level,
                    named("density_g_cm3 is a range; mass and the mesh cache "
                          "key are computed from its midpoint"), key))
        if card.get("cost_usd_kg") is not None and "cost_usd_kg" in props:
            findings.append(finding(
                "cost_in_two_places", "error",
                named("cost_usd_kg is declared twice (top level and inside "
                      "properties)"), "cost_usd_kg"))
    elif flat:
        # A v1 flat entry cites nothing by construction — that is what it
        # means, and the profile decides whether it is a refusal.
        for key in m.PROPERTY_UNITS:
            if card.get(key) is not None:
                findings.append(finding(
                    "missing_citation", level,
                    named(f"property {key!r} has no source"), key))

    for where in ("process", "cost_usd_kg"):
        block = card.get(where)
        source = block.get("source") if isinstance(block, dict) else None
        if isinstance(source, str) and DISALLOWED_SOURCE_RE.search(source):
            findings.append(finding(
                "disallowed_source", "error",
                named(f"{where} cites a licensed aggregator ({source!r}); "
                      "cite the primary source instead"),
                where if where in m.PROPERTY_UNITS else None))

    if library:
        if card.get("subcategory") is None:
            findings.append(finding(
                "subcategory_required", "error",
                named("subcategory is required (category "
                      f"{card.get('category', 'metal')!r})")))
        process = card.get("process")
        if process is not None and not (isinstance(process, dict)
                                        and process.get("source")):
            findings.append(finding(
                "process_source_required", "error",
                named("process ratings are classifications, so the block "
                      "needs one source")))
    return findings


def _table_findings(key: str, obj: dict, named, finding) -> list[Finding]:
    table = obj.get("table")
    if not isinstance(table, (list, tuple)) or len(table) < 2:
        return []
    if not all(_numbers(row) for row in table):
        return []
    temps = [row[0] for row in table]
    values = [row[1] for row in table]
    if any(b <= a for a, b in zip(temps, temps[1:])):
        return [finding("table_not_monotonic", "error",
                        named(f"property {key!r} table temperatures must "
                              "strictly increase"), key)]
    point = obj.get("value")
    if point is None and _numbers(obj.get("range")):
        point = (obj["range"][0] + obj["range"][1]) / 2.0
    if isinstance(point, (int, float)) and not isinstance(point, bool):
        if not (min(values) <= point <= max(values)):
            return [finding("point_outside_table", "error",
                            named(f"property {key!r} point {point} is outside "
                                  f"its table's value range "
                                  f"[{min(values)}, {max(values)}]"), key)]
    return []


def _resolved_findings(material, named, finding) -> list[Finding]:
    """The one rule that needs the normalized record: the per-category
    envelopes, which compare the resolved POINT (a range contributes its
    midpoint) against the band."""
    findings: list[Finding] = []
    for key, (lo, hi) in ENVELOPES.get(material.category, {}).items():
        prop = material.prop(key)
        point = None if prop is None else prop.point
        if point is not None and not (lo <= point <= hi):
            findings.append(finding(
                "out_of_envelope", "warning",
                named(f"property {key!r} = {point} is outside the "
                      f"{material.category} band [{lo}, {hi}] — check the "
                      "unit and the decimal point"), key))
    return findings


# --------------------------------------------------------- catalogs & files

def lint_catalog(materials, profile: str = "library", *,
                 file=None) -> list[Finding]:
    """Lint a whole ``{id: card}`` mapping."""
    path = None if file is None else str(file)
    if not isinstance(materials, dict):
        return [Finding("schema", "error", "",
                        "materials must be an object of id -> entry",
                        None, path)]
    findings: list[Finding] = []
    for material_id, card in materials.items():
        findings.extend(lint_card(material_id, card, profile, file=path))
    return sorted(findings, key=_sort_key)


def lint_file(path, profile: str = "library") -> list[Finding]:
    """Lint one path: a card file, a bare ``{id: card}`` mapping, or a
    ``project.json`` (whose ``materials`` section is a USER layer whatever
    profile was asked for — it is hand-written, not shipped).

    A missing/unreadable path raises ``OSError`` (the CLI turns that into a
    usage exit); broken JSON is a lint error, because the file exists and is
    wrong.
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8")  # OSError: the caller's problem
    try:
        doc = json.loads(text)
    except (ValueError, RecursionError) as exc:
        return [Finding("schema", "error", "", f"invalid JSON: {exc}", None,
                        str(path))]
    if not isinstance(doc, dict):
        return [Finding("schema", "error", "", "expected a JSON object", None,
                        str(path))]
    if isinstance(doc.get("parts"), list) and "name" in doc:
        return lint_catalog(doc.get("materials") or {}, "user", file=path)
    if "materials" in doc:
        findings = []
        version = doc.get("schema_version")
        if version not in (None, 2):
            findings.append(Finding(
                "schema", "error", "",
                f"unsupported schema_version {version!r} (expected 2)",
                None, str(path)))
        return sorted(findings + lint_catalog(doc.get("materials"), profile,
                                              file=path), key=_sort_key)
    return lint_catalog(doc, profile, file=path)


def lint_paths(paths, profile: str = "library") -> list[Finding]:
    """Lint files and directories (``*.json``, skipping ``_``-prefixed markers
    like ``_library.json``). Findings are globally sorted by
    ``(file, id, property, code)`` so two runs print the same report."""
    findings: list[Finding] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            for child in sorted(path.glob("*.json")):
                if child.name.startswith("_"):
                    continue
                findings.extend(lint_file(child, profile))
        else:
            findings.extend(lint_file(path, profile))
    return sorted(findings, key=_sort_key)
