"""Materials v2 query engine (PRD-028 slice 2, Decision 6 / FR6 / G5).

Pure functions over a resolved catalog (``dict[str, Material]``) — no kernel,
no I/O, no service. Used by ``tools_materials.py`` (``find_materials``,
``get_material``, the filtered ``list_materials``) and, through the routes,
by the browser.

**Constraint grammar** (``require`` in ``find_materials``, ``filter`` in
``list_materials``):

* ``<property>_min`` / ``<property>_max`` for any key in
  :data:`~agentcad.core.materials.PROPERTY_UNITS` — a range satisfies
  ``_min`` by its LOWER bound and ``_max`` by its UPPER bound (conservative:
  the whole range must clear the bar); a material lacking the property never
  qualifies (a missing value is not a pass).
* ``category`` / ``subcategory``: exact match.
* ``process``: one of :data:`CONSTRAINT_PROCESSES` — qualifies when the
  mapped rating is ``excellent|good|fair`` (``sheet`` qualifies when the
  ``sheet`` block is present at all).
* ``basis``: restricts to records whose *constraining* properties (the ones
  this call actually tests) carry this basis.

Unknown keys/values raise :class:`~agentcad.core.model.ValidationError`
naming the full known grammar.
"""

from __future__ import annotations

from collections.abc import Mapping

from dataclasses import dataclass, field, replace

from .materials import BASES, CATEGORIES, PROPERTY_UNITS, SUBCATEGORIES, Material, Property
from .model import ValidationError

#: process constraint value -> path into `Material.process` that decides it.
#: `("printable", "fdm")` reads `process["printable"]["fdm"]`; `("sheet",)`
#: is checked for PRESENCE (a rating vocabulary does not apply to it).
CONSTRAINT_PROCESSES: dict[str, tuple[str, ...]] = {
    "cnc": ("machinability",),
    "weld": ("weldability",),
    "fdm": ("printable", "fdm"),
    "sla": ("printable", "sla"),
    "sls": ("printable", "sls"),
    "mjf": ("printable", "mjf"),
    "dmls": ("printable", "dmls"),
    "im": ("im",),
    "sheet": ("sheet",),
    "casting": ("casting",),
}

_QUALIFYING_RATINGS = ("excellent", "good", "fair")

_ALL_SUBCATEGORIES: tuple[str, ...] = tuple(sorted(
    {s for values in SUBCATEGORIES.values() for s in values}))

_GRAMMAR_KEYS: tuple[str, ...] = tuple(sorted(
    [f"{p}_min" for p in PROPERTY_UNITS] + [f"{p}_max" for p in PROPERTY_UNITS]
    + ["category", "subcategory", "process", "basis"]))


# ------------------------------------------------------------- constraints

@dataclass(frozen=True)
class Constraints:
    """A normalized, validated constraint set (the output of
    :func:`parse_constraints`)."""

    mins: dict[str, float] = field(default_factory=dict)
    maxs: dict[str, float] = field(default_factory=dict)
    category: str | None = None
    subcategory: str | None = None
    process: str | None = None
    basis: str | None = None

    def to_dict(self) -> dict:
        """The wire/echo shape — the same grammar the caller wrote, with
        numbers coerced to `float` and defaults omitted."""
        out: dict = {}
        for key in sorted(self.mins):
            out[f"{key}_min"] = self.mins[key]
        for key in sorted(self.maxs):
            out[f"{key}_max"] = self.maxs[key]
        if self.category is not None:
            out["category"] = self.category
        if self.subcategory is not None:
            out["subcategory"] = self.subcategory
        if self.process is not None:
            out["process"] = self.process
        if self.basis is not None:
            out["basis"] = self.basis
        return out

    def droppable_keys(self) -> list[str]:
        """The grammar keys `nearest_relaxation` may try leaving out —
        the property/process/basis constraints (§6); `category`/`subcategory`
        are identity filters, not the "nearly qualifies" the relaxation asks."""
        keys = [f"{k}_min" for k in self.mins] + [f"{k}_max" for k in self.maxs]
        if self.process is not None:
            keys.append("process")
        if self.basis is not None:
            keys.append("basis")
        return keys

    def without(self, key: str) -> "Constraints":
        mins, maxs = dict(self.mins), dict(self.maxs)
        process, basis = self.process, self.basis
        if key.endswith("_min") and key[:-4] in mins:
            del mins[key[:-4]]
        elif key.endswith("_max") and key[:-4] in maxs:
            del maxs[key[:-4]]
        elif key == "process":
            process = None
        elif key == "basis":
            basis = None
        return replace(self, mins=mins, maxs=maxs, process=process, basis=basis)


def _refuse_unknown(key) -> None:
    raise ValidationError(f"unknown constraint {key!r}", {"known": list(_GRAMMAR_KEYS)})


def parse_constraints(raw: dict | None) -> Constraints:
    """Validate and normalize a `require`/`filter` object into
    :class:`Constraints`. `None`/`{}` -> no constraints (everything qualifies)."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValidationError("constraints must be an object",
                              {"known": list(_GRAMMAR_KEYS)})

    mins: dict[str, float] = {}
    maxs: dict[str, float] = {}
    category = subcategory = process = basis = None

    for key, value in raw.items():
        if key.endswith("_min") or key.endswith("_max"):
            prop = key[:-4]
            if prop not in PROPERTY_UNITS:
                _refuse_unknown(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValidationError(f"constraint {key!r} must be a number",
                                      {"known": list(_GRAMMAR_KEYS)})
            (mins if key.endswith("_min") else maxs)[prop] = float(value)
        elif key == "category":
            if value not in CATEGORIES:
                raise ValidationError(f"unknown category {value!r}",
                                      {"known": list(CATEGORIES)})
            category = value
        elif key == "subcategory":
            if value not in _ALL_SUBCATEGORIES:
                raise ValidationError(f"unknown subcategory {value!r}",
                                      {"known": list(_ALL_SUBCATEGORIES)})
            subcategory = value
        elif key == "process":
            if value not in CONSTRAINT_PROCESSES:
                raise ValidationError(f"unknown process {value!r}",
                                      {"known": sorted(CONSTRAINT_PROCESSES)})
            process = value
        elif key == "basis":
            if value not in BASES:
                raise ValidationError(f"unknown basis {value!r}",
                                      {"known": list(BASES)})
            basis = value
        else:
            _refuse_unknown(key)

    for prop in sorted(set(mins) & set(maxs)):
        if mins[prop] > maxs[prop]:
            raise ValidationError(
                f"{prop}_min must be <= {prop}_max",
                {"property": prop, f"{prop}_min": mins[prop], f"{prop}_max": maxs[prop]})

    return Constraints(mins=mins, maxs=maxs, category=category,
                       subcategory=subcategory, process=process, basis=basis)


def normalize_constraints(raw: dict | None, category: str | None = None,
                          subcategory: str | None = None) -> Constraints:
    """`parse_constraints` plus an explicit `category`/`subcategory` argument
    (the tools' own named parameters) merged in, validated the same way."""
    constraints = parse_constraints(raw)
    if category is not None:
        if category not in CATEGORIES:
            raise ValidationError(f"unknown category {category!r}",
                                  {"known": list(CATEGORIES)})
        constraints = replace(constraints, category=category)
    if subcategory is not None:
        if subcategory not in _ALL_SUBCATEGORIES:
            raise ValidationError(f"unknown subcategory {subcategory!r}",
                                  {"known": list(_ALL_SUBCATEGORIES)})
        constraints = replace(constraints, subcategory=subcategory)
    return constraints


# ---------------------------------------------------------------- qualifies

def _process_ok(material: Material, proc: str) -> bool:
    path = CONSTRAINT_PROCESSES[proc]
    process = material.process or {}
    if proc == "sheet":
        return bool(process.get("sheet"))
    # ``Material.process`` is a read-only mapping (``MappingProxyType``), not a
    # ``dict`` — test against ``Mapping`` or every nested lookup silently fails
    # (every process filter except ``sheet`` matched nothing on the real
    # catalog while the hand-built test doubles, plain dicts, passed).
    node = process
    for part in path[:-1]:
        node = node.get(part) if isinstance(node, Mapping) else None
        if not isinstance(node, Mapping):
            return False
    rating = node.get(path[-1]) if isinstance(node, Mapping) else None
    return rating in _QUALIFYING_RATINGS


def _evidence(prop: Property) -> dict:
    out: dict = {}
    if prop.value is not None:
        out["value"] = prop.value
    else:
        out["range"] = [prop.range[0], prop.range[1]]
    out["unit"] = prop.unit
    out["basis"] = prop.basis
    out["source"] = prop.source
    return out


def qualifies(material: Material, constraints: Constraints) -> dict | None:
    """`None` if `material` fails any constraint, else the constraining
    evidence dict for every property constraint that was tested (`{}` when
    only category/subcategory/process were constrained, or nothing was)."""
    if constraints.category is not None and material.category != constraints.category:
        return None
    if constraints.subcategory is not None and material.subcategory != constraints.subcategory:
        return None
    if constraints.process is not None and not _process_ok(material, constraints.process):
        return None

    constraining: dict[str, dict] = {}
    for key in sorted(set(constraints.mins) | set(constraints.maxs)):
        prop = material.prop(key)
        if prop is None:
            return None
        if constraints.basis is not None and prop.basis != constraints.basis:
            return None
        if prop.range is not None:
            lo, hi = prop.range
        else:
            lo = hi = prop.value
        if key in constraints.mins and lo < constraints.mins[key]:
            return None
        if key in constraints.maxs and hi > constraints.maxs[key]:
            return None
        constraining[key] = _evidence(prop)
    return constraining


# -------------------------------------------------------------------- rank

def _validate_prefer(prefer: dict | None) -> dict[str, str]:
    if not prefer:
        return {}
    if not isinstance(prefer, dict):
        raise ValidationError("prefer must be an object of property -> min|max",
                              {"known": list(PROPERTY_UNITS)})
    out: dict[str, str] = {}
    for key, direction in prefer.items():
        if key not in PROPERTY_UNITS:
            raise ValidationError(f"unknown constraint {key!r}",
                                  {"known": list(PROPERTY_UNITS)})
        if direction not in ("min", "max"):
            raise ValidationError(f"prefer[{key!r}] must be 'min' or 'max'",
                                  {"known": ["min", "max"]})
        out[key] = direction
    return out


def _sort_key(candidate: dict) -> tuple:
    material: Material = candidate["material"]
    return (material.category, material.subcategory or "", material.id)


def rank(rows: list[dict], prefer: dict | None) -> list[dict]:
    """`rows`: `{"material": Material, "constraining": dict, ...}`.

    Returns the same rows, sorted, each carrying `"score"` when `prefer` is
    given (rounded to 4 dp; missing preferred property -> 1.0, worst).
    No `prefer` -> the stable `(category, subcategory, id)` order, no score.
    """
    preferred = _validate_prefer(prefer)
    if not preferred:
        return sorted(rows, key=_sort_key)

    per_key_values: dict[str, list[float]] = {}
    for key in preferred:
        per_key_values[key] = [
            c["material"].prop(key).point for c in rows
            if c["material"].prop(key) is not None
            and c["material"].prop(key).point is not None
        ]

    out = []
    for candidate in rows:
        material: Material = candidate["material"]
        score = 0.0
        for key, direction in preferred.items():
            prop = material.prop(key)
            values = per_key_values[key]
            if prop is None or prop.point is None or not values:
                score += 1.0
                continue
            vmin, vmax = min(values), max(values)
            if vmax == vmin:
                continue  # every candidate ties on this key -> contributes 0
            v = prop.point
            score += (v - vmin) / (vmax - vmin) if direction == "min" \
                else (vmax - v) / (vmax - vmin)
        entry = dict(candidate)
        entry["score"] = round(score, 4)
        out.append(entry)
    out.sort(key=lambda c: (c["score"], _sort_key(c)))
    return out


# ------------------------------------------------------------- relaxation

def nearest_relaxation(catalog: dict[str, Material],
                       constraints: Constraints) -> dict | None:
    """Leave-one-out over the property/process/basis constraints: the single
    key whose removal admits the most records. `None` with <= 1 droppable
    constraint, or when no removal admits more than the constraints as given."""
    keys = sorted(constraints.droppable_keys())
    if len(keys) <= 1:
        return None
    baseline = sum(1 for m in catalog.values() if qualifies(m, constraints) is not None)
    best_key, best_count = None, baseline
    for key in keys:
        relaxed = constraints.without(key)
        count = sum(1 for m in catalog.values() if qualifies(m, relaxed) is not None)
        if count > best_count:
            best_key, best_count = key, count
    if best_key is None:
        return None
    return {"drop": best_key, "count": best_count}


# ------------------------------------------------------------------- row

def row(material: Material, constraining: dict, score: float | None = None) -> dict:
    out = {
        "id": material.id,
        "label": material.label,
        "category": material.category,
        "subcategory": material.subcategory,
        "condition": material.condition,
        "constraining": constraining,
    }
    if score is not None:
        out["score"] = score
    return out


# ------------------------------------------------------------------- find

def _validate_limit(limit) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ValidationError("limit must be an integer", {"got": limit})
    if not (1 <= limit <= 50):
        raise ValidationError("limit must be between 1 and 50", {"got": limit})
    return limit


def find(catalog: dict[str, Material], require: dict | None = None,
        prefer: dict | None = None, category: str | None = None,
        limit: int = 10) -> list[dict]:
    """Compose `parse_constraints` + `qualifies` + `rank` + `row` into the
    `find_materials` result. Raises `ValidationError("no material satisfies
    the constraints", {nearest_relaxation, tried})` when nothing qualifies."""
    limit = _validate_limit(limit)
    constraints = normalize_constraints(require, category=category)

    candidates = []
    for material in catalog.values():
        constraining = qualifies(material, constraints)
        if constraining is not None:
            candidates.append({"material": material, "constraining": constraining})

    if not candidates:
        raise ValidationError(
            "no material satisfies the constraints",
            {"nearest_relaxation": nearest_relaxation(catalog, constraints),
             "tried": constraints.to_dict()})

    ranked = rank(candidates, prefer)
    return [row(c["material"], c["constraining"], c.get("score"))
            for c in ranked[:limit]]
