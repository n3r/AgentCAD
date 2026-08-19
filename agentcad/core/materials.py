"""Materials v2: the property card schema, the shipped library, and the
layered resolution of user-defined materials.

Schema v2 (PRD-028)
-------------------
A **card** is the on-disk/contribution format (JSON); a :class:`Material` is
the resolved in-process object. A card carries per-property evidence::

    {
      "label": "Aluminum 6061-T6",
      "category": "metal", "subcategory": "aluminum", "condition": "T6",
      "standards": ["ASTM B209"],
      "properties": {
        "density_g_cm3": {"value": 2.70, "unit": "g/cm3", "basis": "typical",
                          "source": "Aluminum Association, ..."},
        "k_w_m_k": {"value": 167, "unit": "W/(m*K)", "basis": "typical",
                    "table": [[25, 167], [100, 172]], "source": "..."}
      },
      "process": {"machinability": "excellent", "source": "..."},
      "links": [{"label": "MMPDS (allowables)", "url": "https://..."}],
      "notes": "..."
    }

* Property keys are a **closed set** (:data:`PROPERTY_UNITS`, 15 keys) and each
  has exactly one canonical unit — a wrong unit is a refusal, never a silent
  conversion.
* A property object carries exactly one of ``value`` or ``range: [lo, hi]``,
  the canonical ``unit``, a ``basis`` in :data:`BASES` (default ``typical``),
  an optional ``source`` (absent = *uncited*; the ``library`` lint profile is
  what makes that an error), an optional ``T_c`` (default 20 °C) and an
  optional ``table`` of ``[T_c, value]`` rows with strictly increasing T.
* ``cost_usd_kg`` may be written beside ``properties`` (``{range|value,
  as_of?, source?}``) or inside it — never both.
* ``density_g_cm3`` is the only REQUIRED property (the kernel consumes it:
  mass_g = volume_mm3 * density_g_cm3 / 1000, see `kernel/worker.py:_metrics`).
  A range density resolves to its midpoint and records the warning
  ``density_range_midpoint``; the shipped library is held to points.

**Backward compatibility.** A flat v1 entry (``{"density_g_cm3": 2.7,
"E_gpa": 70, "category": "metal"}``) stays valid everywhere — it normalizes to
v2 points with ``basis: typical``, the canonical unit and ``source: None``
(reported as uncited, never invented). Every v1 rejection is preserved
(unknown keys, density in (0, 25], non-negative numbers, known category,
string label/notes). A card and a v1 entry are told apart by the presence of
``properties``; mixing flat numeric keys with ``properties`` is an error.

Values are *typical room-temperature datasheet figures to 2-3 significant
figures*, not design allowables, and every shipped value names its primary
source (standards, handbooks and manufacturer datasheets; licensed
aggregators are refused by the lint). Do not use for certification.

NOTE ``ultimate_mpa >= yield_mpa`` is deliberately NOT enforced: ductile
polymers (e.g. Ultem) legitimately report break strength below yield.

The shipped library
-------------------
``materials_data/*.json`` (one file per family, each ``{"schema_version": 2,
"materials": {...}}``) plus ``materials_data/_library.json`` carrying
:data:`LIBRARY_VERSION`. The directory is loaded at import and **every card is
linted with the ``library`` profile**: a broken shipped card raises at import
naming the file, the id and the property rather than degrading quietly.

Editorial immutability: a builtin id's density is never changed in place (the
mesh cache key hashes it) — a corrected or re-based material gets a new id.

Layering / precedence
---------------------
Three layers, highest wins, resolved per lookup:

    1. project   — "materials" object in project.json      (per-project)
    2. global    — ~/.agentcad/materials.json              (per-machine)
    3. builtin   — MATERIALS below                          (shipped)

An entry in a higher layer with an existing id REPLACES the whole lower
entry (no field merging — predictable, and validation always requires
density_g_cm3, so a half-specified override cannot silently inherit the
wrong density). New ids simply extend the catalog. Builtin ids can be
overridden but never deleted (a project that references "al6061" must keep
resolving). Each resolved Material carries provenance in ``source``.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType

try:  # inside agentcad
    from .model import ValidationError
except ImportError:  # standalone (spike/tests)
    class ValidationError(Exception):
        def __init__(self, message: str, details: dict | None = None):
            super().__init__(message)
            self.message = message
            self.details = details or {}


# --------------------------------------------------------------- vocabulary

#: The closed property key set and each key's ONE canonical unit. The first
#: nine are the v1 keys in their v1 order (`_NUMERIC_FIELDS` is derived from
#: this mapping, and existing code iterates it).
PROPERTY_UNITS: dict[str, str] = {
    "density_g_cm3": "g/cm3",
    "E_gpa": "GPa",
    "yield_mpa": "MPa",
    "ultimate_mpa": "MPa",
    "elongation_pct": "%",
    "cte_um_m_k": "um/(m*K)",
    "k_w_m_k": "W/(m*K)",
    "max_service_temp_c": "C",
    "cost_usd_kg": "USD/kg",
    # v2 additions
    "poisson_ratio": "-",
    "cp_j_kg_k": "J/(kg*K)",
    "shear_modulus_gpa": "GPa",
    "compressive_mpa": "MPa",
    "bending_mpa": "MPa",
    "E_perp_gpa": "GPa",
}

#: How honest a number is. ``characteristic`` exists because EN 338 / EN 1992
#: publish 5 %-fractile values that are neither a typical nor a US-style spec
#: minimum; calling them ``minimum`` would be the dishonesty the basis field
#: exists to prevent.
BASES = ("typical", "minimum", "characteristic")

CATEGORIES = ("metal", "polymer", "composite", "wood", "masonry", "ceramic",
              "other")

#: Closed per category. ``masonry`` is kept (not renamed to ``concrete``):
#: user files already validate against it, and it is the honest parent of
#: concrete/mortar/brick/stone.
SUBCATEGORIES: dict[str, tuple[str, ...]] = {
    "metal": ("steel", "stainless", "tool_steel", "cast_iron", "aluminum",
              "titanium", "copper", "nickel", "magnesium", "zinc",
              "other_metal"),
    "polymer": ("commodity", "engineering", "high_performance", "thermoset",
                "elastomer", "foam"),
    "composite": ("laminate", "sandwich", "reinforced_polymer"),
    "wood": ("softwood", "hardwood", "engineered"),
    "masonry": ("concrete", "mortar", "brick", "stone"),
    "ceramic": ("technical_ceramic", "glass"),
    "other": ("other",),
}

#: Process metadata (G4): one 4-step enum; ABSENT means "not applicable / not
#: rated" and is never a fifth value.
PROCESS_RATINGS = ("excellent", "good", "fair", "poor")
PRINT_PROCESSES = ("fdm", "sla", "sls", "mjf", "dmls")
PROCESS_KEYS = ("machinability", "weldability", "printable", "sheet", "im",
                "casting", "source")

_RATED_PROCESS_KEYS = ("machinability", "weldability", "im", "casting")
_SHEET_KEYS = ("k_factor_range", "min_bend_radius_t")

_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")

# Fields a flat (v1) entry may set: every numeric property plus the three
# strings. A v1 entry stays strictly v1 — subcategory/condition/standards and
# the rest of the v2 vocabulary live on cards.
_NUMERIC_FIELDS = tuple(PROPERTY_UNITS)
_ENTRY_FIELDS = _NUMERIC_FIELDS + ("label", "category", "notes")
# Only density must be > 0; the others must merely be >= 0 when present
# (CTE can be ~0 for invar-likes; keep it simple: non-negative).
_REQUIRED = ("density_g_cm3",)

#: Top-level keys of a v2 card.
CARD_FIELDS = ("label", "category", "subcategory", "condition", "standards",
               "properties", "process", "cost_usd_kg", "links", "notes")
#: Keys of one property object.
PROPERTY_FIELDS = ("value", "range", "unit", "basis", "source", "T_c",
                   "table", "as_of")
#: Keys of the top-level ``cost_usd_kg`` shorthand.
_COST_FIELDS = ("value", "range", "unit", "basis", "source", "as_of")

DEFAULT_MATERIAL = "al6061"

GLOBAL_MATERIALS_PATH = Path.home() / ".agentcad" / "materials.json"

_DATA_DIR = Path(__file__).resolve().parent / "materials_data"
_LIBRARY_FILE = "_library.json"


# ----------------------------------------------------------------- Property

@dataclass(frozen=True)
class Property:
    """One measured (or specified) property with its evidence.

    ``value`` XOR ``range``; ``point`` is what every non-thermal consumer
    reads (the flat ``Material`` fields are exactly these points).
    """

    key: str
    value: float | None
    range: tuple[float, float] | None
    unit: str
    basis: str = "typical"
    source: str | None = None
    T_c: float = 20.0
    table: tuple[tuple[float, float], ...] | None = None
    as_of: str | None = None

    @property
    def point(self) -> float | None:
        """The single number: ``value``, else the midpoint of ``range``."""
        if self.value is not None:
            return float(self.value)
        if self.range is not None:
            return (float(self.range[0]) + float(self.range[1])) / 2.0
        return None

    def at(self, T_c: float) -> tuple[float | None, bool, bool]:
        """``(value, interpolated, clamped)`` at a temperature.

        With a table: linear interpolation between rows, **clamped** to the end
        rows outside the table (the SolidWorks/Ansys convention) so a consumer
        always gets a number plus a flag it must surface. Without a table: the
        point, ``(value, False, False)`` — the temperature is ignored, which is
        exactly what a single datasheet figure means.
        """
        if not self.table:
            return (self.point, False, False)
        if not math.isfinite(T_c):
            # NaN compares false against every row and used to fall through to
            # the last row with no clamp flag — a silent wrong answer.
            raise ValueError(f"temperature must be finite, got {T_c!r}")
        rows = self.table
        if T_c < rows[0][0]:
            return (rows[0][1], True, True)
        if T_c > rows[-1][0]:
            return (rows[-1][1], True, True)
        for (t0, v0), (t1, v1) in zip(rows, rows[1:]):
            if t0 <= T_c <= t1:
                if t1 == t0:
                    return (v0, True, False)
                frac = (T_c - t0) / (t1 - t0)
                return (v0 + frac * (v1 - v0), True, False)
        return (rows[-1][1], True, False)  # unreachable; total by construction

    def to_payload(self) -> dict:
        out: dict = {}
        if self.value is not None:
            out["value"] = self.value
        if self.range is not None:
            out["range"] = [self.range[0], self.range[1]]
        out["unit"] = self.unit
        out["basis"] = self.basis
        out["source"] = self.source
        if self.T_c != 20.0:
            out["T_c"] = self.T_c
        if self.table:
            out["table"] = [[t, v] for t, v in self.table]
        if self.as_of is not None:
            out["as_of"] = self.as_of
        return out


_EMPTY: Mapping[str, Property] = MappingProxyType({})


@dataclass(frozen=True)
class Material:
    """A resolved material. Every v1 field keeps its v1 meaning and position
    (consumers read the flat point values); v2 adds defaulted fields only."""

    id: str
    label: str
    density_g_cm3: float
    category: str = "metal"
    E_gpa: float | None = None
    yield_mpa: float | None = None
    ultimate_mpa: float | None = None
    elongation_pct: float | None = None
    cte_um_m_k: float | None = None
    k_w_m_k: float | None = None
    max_service_temp_c: float | None = None
    cost_usd_kg: float | None = None
    notes: str | None = None
    source: str = "builtin"  # builtin | global | project (provenance)
    # -- v2 ---------------------------------------------------------------
    poisson_ratio: float | None = None
    cp_j_kg_k: float | None = None
    shear_modulus_gpa: float | None = None
    compressive_mpa: float | None = None
    bending_mpa: float | None = None
    E_perp_gpa: float | None = None
    subcategory: str | None = None
    condition: str | None = None
    standards: tuple[str, ...] = ()
    process: Mapping | None = None
    links: tuple[dict, ...] = ()
    properties: Mapping[str, Property] = field(default_factory=lambda: _EMPTY)
    warnings: tuple[str, ...] = ()
    library_version: str | None = None

    def prop(self, key: str) -> Property | None:
        """The typed property (value|range, unit, basis, source, T_c, table)."""
        return self.properties.get(key)

    def to_payload(self, full: bool = False) -> dict:
        """JSON-able dict for API/tool responses (drops None properties).

        The flat v1 shape is unchanged — the UI and the v1 tests read it —
        with the v2 record-level fields beside it. ``full=True`` adds every
        property object (source, basis, table): the ``get_material`` payload.
        """
        out = {"id": self.id, "label": self.label, "category": self.category,
               "density_g_cm3": self.density_g_cm3, "source": self.source}
        for f in _NUMERIC_FIELDS[1:]:
            v = getattr(self, f)
            if v is not None:
                out[f] = v
        if self.notes:
            out["notes"] = self.notes
        out["subcategory"] = self.subcategory
        out["condition"] = self.condition
        out["standards"] = list(self.standards)
        if self.process:
            out["process"] = {k: (dict(v) if isinstance(v, Mapping) else v)
                              for k, v in self.process.items()}
        out["links"] = [dict(link) for link in self.links]
        out["warnings"] = list(self.warnings)
        out["basis"] = {k: p.basis for k, p in self.properties.items()}
        out["uncited"] = [k for k, p in self.properties.items()
                          if p.source is None]
        out["library_version"] = self.library_version
        if full:
            out["properties"] = {k: p.to_payload()
                                 for k, p in self.properties.items()}
        return out


# ---------------------------------------------------------------- validation

def _fail(material_id: str, message: str, details: dict | None = None):
    raise ValidationError(f"material {material_id!r}: {message}", details or {})


def _number(material_id: str, where: str, value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(material_id, f"{where} must be a number")
    v = float(value)
    if not math.isfinite(v):
        _fail(material_id, f"{where} must be a finite number")
    return v


def _check_magnitude(material_id: str, key: str, where: str, v: float) -> float:
    if key == "density_g_cm3":
        if not (0 < v <= 25):  # osmium is 22.6; anything above is a typo
            _fail(material_id, "density_g_cm3 must be in (0, 25]")
    elif v < 0:
        _fail(material_id, f"{where} must be >= 0")
    return v


def _string(material_id: str, where: str, value) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(material_id, f"{where} must be a non-empty string")
    return value


def _property_from_object(material_id: str, key: str, obj) -> Property:
    """One property object of a v2 card → a :class:`Property`."""
    where = f"property {key!r}"
    if not isinstance(obj, dict):
        _fail(material_id, f"{where} must be an object")
    unknown = sorted(set(obj) - set(PROPERTY_FIELDS))
    if unknown:
        _fail(material_id, f"{where}: unknown field(s): {', '.join(unknown)}",
              {"unknown": unknown, "known": list(PROPERTY_FIELDS)})

    has_value = obj.get("value") is not None
    has_range = obj.get("range") is not None
    if has_value == has_range:
        _fail(material_id,
              f"{where} must carry exactly one of 'value' or 'range'")

    canonical = PROPERTY_UNITS[key]
    unit = obj.get("unit")
    if unit is None:
        _fail(material_id, f"{where} must declare unit {canonical!r}")
    if unit != canonical:
        _fail(material_id,
              f"{where} unit {unit!r} must be {canonical!r}",
              {"unit": unit, "expected": canonical})

    value = range_ = None
    if has_value:
        value = _check_magnitude(
            material_id, key, where, _number(material_id, where, obj["value"]))
    else:
        raw = obj["range"]
        if not isinstance(raw, (list, tuple)) or len(raw) != 2:
            _fail(material_id, f"{where} range must be [lo, hi]")
        lo = _check_magnitude(material_id, key, f"{where} range",
                              _number(material_id, f"{where} range", raw[0]))
        hi = _check_magnitude(material_id, key, f"{where} range",
                              _number(material_id, f"{where} range", raw[1]))
        if lo > hi:
            _fail(material_id, f"{where} range must have lo <= hi")
        range_ = (lo, hi)

    basis = obj.get("basis", "typical")
    if basis not in BASES:
        _fail(material_id, f"{where} basis {basis!r} is unknown",
              {"known": list(BASES)})

    source = obj.get("source")
    if source is not None:
        source = _string(material_id, f"{where} source", source)

    T_c = 20.0 if obj.get("T_c") is None else _number(
        material_id, f"{where} T_c", obj["T_c"])

    table = None
    if obj.get("table") is not None:
        raw = obj["table"]
        if not isinstance(raw, (list, tuple)) or len(raw) < 2:
            _fail(material_id, f"{where} table needs at least two rows")
        rows: list[tuple[float, float]] = []
        for row in raw:
            if not isinstance(row, (list, tuple)) or len(row) != 2:
                _fail(material_id, f"{where} table rows must be [T_c, value]")
            t = _number(material_id, f"{where} table T_c", row[0])
            v = _check_magnitude(material_id, key, f"{where} table value",
                                 _number(material_id, f"{where} table value",
                                         row[1]))
            if rows and t <= rows[-1][0]:
                _fail(material_id,
                      f"{where} table temperatures must strictly increase")
            rows.append((t, v))
        table = tuple(rows)

    as_of = obj.get("as_of")
    if as_of is not None:
        as_of = _string(material_id, f"{where} as_of", as_of)

    return Property(key=key, value=value, range=range_, unit=canonical,
                    basis=basis, source=source, T_c=T_c, table=table,
                    as_of=as_of)


def _validate_process(material_id: str, process) -> dict:
    if not isinstance(process, dict):
        _fail(material_id, "process must be an object")
    unknown = sorted(set(process) - set(PROCESS_KEYS))
    if unknown:
        _fail(material_id, f"process: unknown key(s): {', '.join(unknown)}",
              {"unknown": unknown, "known": list(PROCESS_KEYS)})
    out: dict = {}
    for key in _RATED_PROCESS_KEYS:
        if process.get(key) is None:
            continue
        rating = process[key]
        if rating not in PROCESS_RATINGS:
            _fail(material_id, f"process.{key} {rating!r} is not a rating",
                  {"known": list(PROCESS_RATINGS)})
        out[key] = rating
    if process.get("printable") is not None:
        printable = process["printable"]
        if not isinstance(printable, dict):
            _fail(material_id, "process.printable must be an object")
        unknown = sorted(set(printable) - set(PRINT_PROCESSES))
        if unknown:
            _fail(material_id,
                  f"process.printable: unknown process(es): {', '.join(unknown)}",
                  {"unknown": unknown, "known": list(PRINT_PROCESSES)})
        for proc, rating in printable.items():
            if rating not in PROCESS_RATINGS:
                _fail(material_id,
                      f"process.printable.{proc} {rating!r} is not a rating",
                      {"known": list(PROCESS_RATINGS)})
        out["printable"] = dict(printable)
    if process.get("sheet") is not None:
        sheet = process["sheet"]
        if not isinstance(sheet, dict):
            _fail(material_id, "process.sheet must be an object")
        unknown = sorted(set(sheet) - set(_SHEET_KEYS))
        if unknown:
            _fail(material_id,
                  f"process.sheet: unknown key(s): {', '.join(unknown)}",
                  {"unknown": unknown, "known": list(_SHEET_KEYS)})
        entry: dict = {}
        if sheet.get("k_factor_range") is not None:
            raw = sheet["k_factor_range"]
            if not isinstance(raw, (list, tuple)) or len(raw) != 2:
                _fail(material_id, "process.sheet.k_factor_range must be [lo, hi]")
            lo = _number(material_id, "process.sheet.k_factor_range", raw[0])
            hi = _number(material_id, "process.sheet.k_factor_range", raw[1])
            if not (0 < lo <= hi <= 1):
                _fail(material_id,
                      "process.sheet.k_factor_range must satisfy "
                      "0 < lo <= hi <= 1")
            entry["k_factor_range"] = [lo, hi]
        if sheet.get("min_bend_radius_t") is not None:
            r = _number(material_id, "process.sheet.min_bend_radius_t",
                        sheet["min_bend_radius_t"])
            if r < 0:
                _fail(material_id, "process.sheet.min_bend_radius_t must be >= 0")
            entry["min_bend_radius_t"] = r
        out["sheet"] = entry
    if process.get("source") is not None:
        out["source"] = _string(material_id, "process.source",
                                process["source"])
    return out


def _validate_links(material_id: str, links) -> tuple[dict, ...]:
    if not isinstance(links, list):
        _fail(material_id, "links must be a list of {label, url}")
    out = []
    for link in links:
        if not isinstance(link, dict) or set(link) != {"label", "url"}:
            _fail(material_id, "each link must be {label, url}")
        url = _string(material_id, "link url", link["url"])
        # A link lands in an ``href`` in the browser: only https may pass, so
        # a ``javascript:`` URL in a user-layer card can never become a click.
        if not url.lower().startswith("https://"):
            _fail(material_id, "link url must start with https://", {"url": url})
        out.append({"label": _string(material_id, "link label", link["label"]),
                    "url": url})
    return tuple(out)


def _validate_standards(material_id: str, standards) -> tuple[str, ...]:
    if not isinstance(standards, list):
        _fail(material_id, "standards must be a list of strings")
    return tuple(_string(material_id, "standard", s) for s in standards)


def _category_of(material_id: str, entry: dict) -> str:
    category = entry.get("category", "metal")
    if category not in CATEGORIES:
        _fail(material_id, f"unknown category {category!r}",
              {"known": list(CATEGORIES)})
    return category


def _label_and_notes(material_id: str, entry: dict) -> tuple[str, str | None]:
    label = entry.get("label", material_id)
    notes = entry.get("notes")
    if not isinstance(label, str) or (notes is not None
                                      and not isinstance(notes, str)):
        _fail(material_id, "label/notes must be strings")
    return label, notes


def _material_from(material_id: str, source: str, label: str, category: str,
                   notes: str | None, properties: dict[str, Property],
                   **extra) -> Material:
    """Assemble a Material: every flat numeric field is its property's point."""
    ordered = {key: properties[key] for key in PROPERTY_UNITS
               if key in properties}
    values = {key: prop.point for key, prop in ordered.items()}
    return Material(id=material_id, label=label, category=category,
                    notes=notes, source=source,
                    properties=MappingProxyType(ordered), **values, **extra)


def _normalize_flat(material_id: str, entry: dict, source: str) -> Material:
    """A v1 flat entry → points with basis ``typical`` and no citation."""
    unknown = sorted(set(entry) - set(_ENTRY_FIELDS))
    if unknown:
        _fail(material_id, f"unknown field(s): {', '.join(unknown)}",
              {"unknown": unknown, "known": sorted(_ENTRY_FIELDS)})
    for key in _REQUIRED:
        if key not in entry:
            _fail(material_id, f"{key} is required")
    properties: dict[str, Property] = {}
    for key in _NUMERIC_FIELDS:
        if entry.get(key) is None:
            continue
        v = _check_magnitude(material_id, key, key,
                             _number(material_id, key, entry[key]))
        properties[key] = Property(key=key, value=v, range=None,
                                   unit=PROPERTY_UNITS[key], basis="typical")
    label, notes = _label_and_notes(material_id, entry)
    return _material_from(material_id, source, label,
                          _category_of(material_id, entry), notes, properties)


def _normalize_card(material_id: str, entry: dict, source: str) -> Material:
    """A v2 card → a Material carrying every property object."""
    unknown = sorted(set(entry) - set(CARD_FIELDS))
    if unknown:
        _fail(material_id, f"unknown field(s): {', '.join(unknown)}",
              {"unknown": unknown, "known": list(CARD_FIELDS)})

    raw = entry.get("properties")
    if not isinstance(raw, dict):
        _fail(material_id, "properties must be an object of key -> property")
    unknown = sorted(set(raw) - set(PROPERTY_UNITS))
    if unknown:
        _fail(material_id, f"unknown propert(ies): {', '.join(unknown)}",
              {"unknown": unknown, "known": list(PROPERTY_UNITS)})

    properties = {key: _property_from_object(material_id, key, raw[key])
                  for key in PROPERTY_UNITS if key in raw}

    if entry.get("cost_usd_kg") is not None:
        if "cost_usd_kg" in properties:
            _fail(material_id, "cost_usd_kg is declared twice (top level and "
                               "inside properties)")
        cost = entry["cost_usd_kg"]
        if not isinstance(cost, dict):
            _fail(material_id, "cost_usd_kg must be an object "
                               "{value|range, as_of?, source?}")
        unknown = sorted(set(cost) - set(_COST_FIELDS))
        if unknown:
            _fail(material_id,
                  f"cost_usd_kg: unknown field(s): {', '.join(unknown)}",
                  {"unknown": unknown, "known": list(_COST_FIELDS)})
        obj = dict(cost)
        obj.setdefault("unit", PROPERTY_UNITS["cost_usd_kg"])
        properties["cost_usd_kg"] = _property_from_object(
            material_id, "cost_usd_kg", obj)

    if "density_g_cm3" not in properties:
        _fail(material_id, "density_g_cm3 is required")

    warnings: list[str] = []
    if properties["density_g_cm3"].range is not None:
        warnings.append("density_range_midpoint")

    category = _category_of(material_id, entry)
    subcategory = entry.get("subcategory")
    if subcategory is not None:
        if not isinstance(subcategory, str):
            _fail(material_id, "subcategory must be a string")
        if subcategory not in SUBCATEGORIES[category]:
            _fail(material_id,
                  f"subcategory {subcategory!r} does not belong to category "
                  f"{category!r}", {"known": list(SUBCATEGORIES[category])})
    condition = entry.get("condition")
    if condition is not None and not isinstance(condition, str):
        _fail(material_id, "condition must be a string")

    label, notes = _label_and_notes(material_id, entry)
    return _material_from(
        material_id, source, label, category, notes, properties,
        subcategory=subcategory, condition=condition,
        standards=_validate_standards(material_id, entry.get("standards", [])),
        process=(MappingProxyType(_validate_process(material_id,
                                                    entry["process"]))
                 if entry.get("process") is not None else None),
        links=_validate_links(material_id, entry.get("links", [])),
        warnings=tuple(warnings))


def normalize_entry(material_id: str, entry: dict, source: str) -> Material:
    """Validate one entry — a **v1 flat entry** or a **v2 card** — and resolve
    it into a :class:`Material`.

    The presence of ``properties`` decides which shape it is; mixing flat
    numeric keys with ``properties`` is a refusal (it reads as a half-migrated
    record, and guessing which half wins is how a wrong density ships).
    """
    if not _ID_RE.match(material_id or ""):
        raise ValidationError(
            f"invalid material id {material_id!r}",
            {"expected": "[a-z][a-z0-9_]{0,39}"},
        )
    if not isinstance(entry, dict):
        _fail(material_id, "entry must be an object")
    if "properties" in entry:
        mixed = sorted(set(entry) & set(_NUMERIC_FIELDS) - {"cost_usd_kg"})
        if mixed:
            _fail(material_id,
                  "flat propert(ies) beside 'properties': "
                  f"{', '.join(mixed)}",
                  {"mixed": mixed})
        return _normalize_card(material_id, entry, source)
    return _normalize_flat(material_id, entry, source)


def validate_material_entry(material_id: str, entry: dict,
                            source: str) -> Material:
    """Validate one user-supplied materials entry, return a Material.

    Rejects: bad id, non-dict entry, unknown keys, missing/non-positive
    density, negative numbers, unknown category, non-string label/notes — and
    every v2 card rule when the entry carries ``properties``.
    """
    return normalize_entry(material_id, entry, source)


def validate_materials_dict(materials: dict, source: str) -> dict[str, Material]:
    if not isinstance(materials, dict):
        raise ValidationError(f"{source} materials must be an object of id -> entry")
    return {
        mid: validate_material_entry(mid, entry, source)
        for mid, entry in materials.items()
    }


# -------------------------------------------------------- the shipped library

def _read_json(path: Path):
    """A shipped data file, parsed — every failure is a ``RuntimeError``
    naming the file (a bare ``FileNotFoundError``/``JSONDecodeError``/
    ``RecursionError`` at import said nothing about which card to fix)."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as exc:
        raise RuntimeError(f"{path}: cannot read the materials data file "
                           f"({exc.__class__.__name__}: {exc})") from exc


def load_library(data_dir: Path = _DATA_DIR) -> tuple[str, dict[str, Material]]:
    """Load ``materials_data/`` — every card linted at the ``library`` profile.

    A broken shipped card raises :class:`RuntimeError` naming the file, the id
    and (when the finding has one) the property. Degrading quietly would ship
    an uncited or mis-united number into somebody's mass calculation, which is
    the one thing this library exists not to do. Lint *warnings* (envelope
    bands, empty taxonomy leaves) are informational and ignored here.
    """
    from .materials_lint import lint_card  # deferred: the lint imports us back

    marker = _read_json(data_dir / _LIBRARY_FILE)
    version = marker.get("library_version") if isinstance(marker, dict) else None
    if not isinstance(version, str) or not version:
        raise RuntimeError(f"{data_dir / _LIBRARY_FILE}: library_version must "
                           "be a version string")

    materials: dict[str, Material] = {}
    for path in sorted(data_dir.glob("*.json")):
        if path.name.startswith("_"):
            continue
        doc = _read_json(path)
        entries = doc.get("materials") if isinstance(doc, dict) else None
        if not isinstance(entries, dict):
            raise RuntimeError(f"{path.name}: expected "
                               '{"schema_version": 2, "materials": {...}}')
        if doc.get("schema_version") != 2:
            raise RuntimeError(f"{path.name}: schema_version must be 2, got "
                               f"{doc.get('schema_version')!r}")
        for mid, card in entries.items():
            if mid in materials:
                raise RuntimeError(f"{path.name}: material {mid!r} is already "
                                   "defined in another family file")
            errors = [f for f in lint_card(mid, card, "library")
                      if f.level == "error"]
            if errors:
                finding = errors[0]
                where = f" property {finding.property!r}" if finding.property else ""
                raise RuntimeError(
                    f"{path.name}: material {mid!r}{where}: "
                    f"[{finding.code}] {finding.message}")
            materials[mid] = replace(normalize_entry(mid, card, "builtin"),
                                     library_version=version)
    return version, materials


LIBRARY_VERSION, MATERIALS = load_library()


# ---------------------------------------------------------------------------
# Layered resolution
# ---------------------------------------------------------------------------

class MaterialLibrary:
    """Builtins + ~/.agentcad/materials.json + per-project overrides.

    The global file is re-read only when its mtime changes (cheap stat per
    lookup; the service calls resolve() on every rebuild). A corrupt or
    invalid global file degrades to builtins with a warning recorded in
    ``global_error`` rather than breaking every build.
    """

    def __init__(self, global_path: Path | str = GLOBAL_MATERIALS_PATH):
        self.global_path = Path(global_path)
        self._global_cache: dict[str, Material] = {}
        self._global_mtime: float | None = None
        self.global_error: str | None = None

    # -- layers -----------------------------------------------------------

    def _global_layer(self) -> dict[str, Material]:
        try:
            mtime = self.global_path.stat().st_mtime
        except OSError:
            self._global_cache, self._global_mtime = {}, None
            self.global_error = None
            return self._global_cache
        if mtime == self._global_mtime:
            return self._global_cache
        try:
            raw = json.loads(self.global_path.read_text(encoding="utf-8"))
            entries = raw.get("materials", {}) if isinstance(raw, dict) else None
            if entries is None:
                raise ValidationError("materials.json must be an object")
            self._global_cache = validate_materials_dict(entries, "global")
            self.global_error = None
        except (ValidationError, json.JSONDecodeError, OSError, ValueError,
                RecursionError) as exc:
            # ValueError covers UnicodeDecodeError (a cp1252 file used to
            # break every build); RecursionError is NOT a ValueError.
            self._global_cache = {}
            self.global_error = f"{self.global_path}: {exc}"
        self._global_mtime = mtime
        return self._global_cache

    # -- public API --------------------------------------------------------

    def effective(self, project_materials: dict | None = None) -> dict[str, Material]:
        """Full resolved catalog: builtin < global < project."""
        catalog = dict(MATERIALS)
        catalog.update(self._global_layer())
        if project_materials:
            catalog.update(validate_materials_dict(project_materials, "project"))
        return catalog

    def resolve(self, material_id: str,
                project_materials: dict | None = None) -> Material:
        catalog = self.effective(project_materials)
        material = catalog.get(material_id)
        if material is None:
            raise ValidationError(
                f"unknown material {material_id!r}",
                {"known": sorted(catalog)},
            )
        return material


# Backward-compatible module-level accessor (builtin layer only). Existing
# call sites in project.py/service.py migrate to MaterialLibrary.resolve();
# keep this for tests and for contexts with no project (e.g. templates).
def get_material(material_id: str) -> Material:
    material = MATERIALS.get(material_id)
    if material is None:
        raise ValidationError(
            f"unknown material {material_id!r}",
            {"known": sorted(MATERIALS)},
        )
    return material
