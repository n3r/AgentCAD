"""Design specs: executable, versioned assertions over built geometry.

Declaration is data; evaluation is the kernel's job. Every constructor here
returns a plain dict — no geometry, no measurement, no I/O — so this module
imports nothing from ``agentcad.kernel`` and no geometry kernel at all. That
is what lets a ``check_fem_static(...)`` declare cleanly on a machine with no
``[fem]`` extra: whether the measurement can run is asked at evaluation time,
not here.

Part scripts declare a module-level ``SPECS`` list; a project declares
assembly-scope specs in a root ``specs.py`` the same way::

    from agentcad.toolkit.specs import check_wall, check_mass, check_that

    SPECS = [
        check_wall(min_mm=2.5, requirement="ENG-014"),
        check_mass(max_g=120.0, requirement="SYS-042"),
        check_that(lambda part, metrics:
                   metrics["bbox"]["max"][2] - metrics["bbox"]["min"][2] <= 80,
                   name="fits_fairing"),
    ]

**Constructors validate eagerly**, and that is the whole error contract:
``SPECS`` is built while the module executes, so ``check_wall(min_mm="thick")``
raises inside the script and surfaces as a ``script_error`` carrying
``details.line`` — byte-identically to a malformed ``PARAMS``. Every validator
raises ``ValueError`` naming the offending argument; nothing here raises a
custom type.

Every declaration is::

    {"spec": 1, "kind": str, "scope": "part"|"project", "name": str,
     "limit": dict, "requirement": str|None, "options": dict}

``limit`` holds what makes the check pass or fail (a dict, not a scalar: a
two-sided check has two bounds and the key says which is which); ``options``
holds what/how to measure. ``check_that`` additionally carries its callable
under ``"fn"`` — the one non-JSON value in the vocabulary, dropped at the
JSON-RPC boundary by :func:`json_safe`.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

#: Declaration marker *and* format version — a dict without it is not a spec.
SPEC_FORMAT = 1

PART_KINDS = ("valid", "mass", "volume", "bbox", "wall", "that", "fem_static")
PROJECT_KINDS = ("interference_free", "clearance", "stackup")

AXES = ("x", "y", "z")
SIDES = ("min", "max")


# ---- validators (private; every failure names its argument) ------------------

def _number(label: str, value) -> float:
    # bool is an int subclass; a True wall minimum is a typo, not a number.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number, got {value!r}")
    number = float(value)
    # NaN and the infinities are rejected here, where the argument is read,
    # because a comparison predicate cannot: every ordered comparison against
    # NaN is false, so a NaN limit reports *pass* while measuring nothing, and
    # an infinite limit is one no measurement can ever breach. A limit that
    # cannot fail is not a limit.
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite number, got {value!r}")
    return number


def _positive(label: str, value) -> float:
    number = _number(label, value)
    if number <= 0:
        raise ValueError(f"{label} must be > 0, got {number!r}")
    return number


def _non_negative(label: str, value) -> float:
    number = _number(label, value)
    if number < 0:
        raise ValueError(f"{label} must be >= 0, got {number!r}")
    return number


def _bounds(lo_label: str, lo, hi_label: str, hi) -> dict:
    """At least one bound, each a number, and low <= high."""
    if lo is None and hi is None:
        raise ValueError(f"give at least one of {lo_label} or {hi_label}")
    limit = {}
    if lo is not None:
        limit[lo_label] = _non_negative(lo_label, lo)
    if hi is not None:
        limit[hi_label] = _non_negative(hi_label, hi)
    if lo is not None and hi is not None and limit[hi_label] < limit[lo_label]:
        raise ValueError(
            f"{hi_label} ({limit[hi_label]}) must be >= {lo_label} "
            f"({limit[lo_label]})")
    return limit


def _vec3(label: str, value) -> list[float]:
    """A scalar (all three axes) or an ``[x, y, z]``; always positive."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return [_positive(label, value)] * 3
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        if len(value) != 3:
            raise ValueError(
                f"{label} must be a number or 3 numbers [x, y, z], "
                f"got {len(value)}")
        return [_positive(label, v) for v in value]
    raise ValueError(
        f"{label} must be a number or 3 numbers [x, y, z], got {value!r}")


def _identifier(label: str, value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string, got {value!r}")
    return value


def _name(value, default: str) -> str:
    if value is None:
        return default
    name = _identifier("name", value)
    # ids are "<part_id>:<name>" / "project:<name>", so a colon would be
    # ambiguous in every report that joins on them.
    if ":" in name:
        raise ValueError(f"name must not contain ':', got {name!r}")
    return name


def _requirement(value) -> str | None:
    """An opaque id or URL. We store it; we never parse or resolve it."""
    if value is None:
        return None
    return _identifier("requirement", value)


def _face(label: str, value) -> dict:
    """An axis-aligned face selector, as ``fem_static`` already takes it."""
    if not isinstance(value, dict):
        raise ValueError(
            f"{label} must be {{axis: x|y|z, side: min|max}}, got {value!r}")
    axis, side = value.get("axis"), value.get("side")
    if axis not in AXES:
        raise ValueError(f"{label}.axis must be one of {AXES}, got {axis!r}")
    if side not in SIDES:
        raise ValueError(f"{label}.side must be one of {SIDES}, got {side!r}")
    return {"axis": axis, "side": side}


def _range_name(kind: str, limit: dict) -> str:
    """``mass_max`` / ``mass_min`` / ``mass_range``, from which bounds exist."""
    if len(limit) == 2:
        return f"{kind}_range"
    return f"{kind}_{'min' if next(iter(limit)).startswith('min') else 'max'}"


def _declaration(kind: str, scope: str, name: str, limit: dict,
                 requirement: str | None, options: dict) -> dict:
    return {"spec": SPEC_FORMAT, "kind": kind, "scope": scope, "name": name,
            "limit": limit, "requirement": requirement, "options": options}


# ---- part-scope constructors -------------------------------------------------

def check_valid(*, name: str | None = None,
                requirement: str | None = None) -> dict:
    """The part builds into at least one valid solid."""
    return _declaration("valid", "part", _name(name, "valid"), {},
                        _requirement(requirement), {})


def check_mass(min_g: float | None = None, max_g: float | None = None, *,
               name: str | None = None,
               requirement: str | None = None) -> dict:
    """Mass budget in grams (material-dependent by design)."""
    limit = _bounds("min_g", min_g, "max_g", max_g)
    return _declaration("mass", "part", _name(name, _range_name("mass", limit)),
                        limit, _requirement(requirement), {})


def check_volume(min_mm3: float | None = None, max_mm3: float | None = None, *,
                 name: str | None = None,
                 requirement: str | None = None) -> dict:
    """Volume budget in mm³ (the solids-sum volume, never ``.volume``)."""
    limit = _bounds("min_mm3", min_mm3, "max_mm3", max_mm3)
    return _declaration("volume", "part",
                        _name(name, _range_name("volume", limit)), limit,
                        _requirement(requirement), {})


def check_bbox(within_mm, *, name: str | None = None,
               requirement: str | None = None) -> dict:
    """The bounding-box size fits within *within_mm* (scalar or ``[x, y, z]``)."""
    return _declaration("bbox", "part", _name(name, "bbox_within"),
                        {"within_mm": _vec3("within_mm", within_mm)},
                        _requirement(requirement), {})


def check_wall(min_mm: float, *, grid: int = 8, name: str | None = None,
               requirement: str | None = None) -> dict:
    """Minimum wall thickness over the built part (mm).

    *grid* is the per-face UV sample density of the underlying ray-cast
    measurement — the cost knob, quadratic in the sample count.
    """
    min_mm = _positive("min_mm", min_mm)
    grid_n = int(_positive("grid", grid))
    return _declaration("wall", "part", _name(name, "wall_min"),
                        {"min_mm": min_mm}, _requirement(requirement),
                        {"grid": grid_n})


def check_that(fn, name: str, *, requirement: str | None = None) -> dict:
    """An arbitrary predicate ``fn(part, metrics) -> bool`` over the built part.

    The callable rides under ``"fn"``, never crosses the JSON-RPC boundary and
    is executed only inside the confined kernel worker. *name* is required —
    there is nothing to name a predicate after.
    """
    if not callable(fn):
        raise ValueError(f"fn must be callable, got {fn!r}")
    if name is None:
        raise ValueError("name is required for check_that")
    return dict(_declaration("that", "part", _name(name, ""), {},
                             _requirement(requirement), {}), fn=fn)


def check_fem_static(fixed_face, load_face, load_N: float, *,
                     max_vm_mpa: float | None = None,
                     max_disp_mm: float | None = None,
                     name: str | None = None,
                     requirement: str | None = None) -> dict:
    """Linear-static FEM budget: clamp one face, load another, bound the result.

    Faces are ``{"axis": "x"|"y"|"z", "side": "min"|"max"}``. At least one of
    *max_vm_mpa* (von Mises) or *max_disp_mm* (displacement) is required —
    a check with no limit can never pass or fail. Declaring costs nothing and
    needs no ``[fem]`` extra; evaluation skips honestly without it.
    """
    limit = {}
    if max_vm_mpa is not None:
        limit["max_vm_mpa"] = _positive("max_vm_mpa", max_vm_mpa)
    if max_disp_mm is not None:
        limit["max_disp_mm"] = _positive("max_disp_mm", max_disp_mm)
    if not limit:
        raise ValueError("give at least one of max_vm_mpa or max_disp_mm")
    options = {"fixed_face": _face("fixed_face", fixed_face),
               "load_face": _face("load_face", load_face),
               "load_N": _positive("load_N", load_N)}
    return _declaration("fem_static", "part", _name(name, "fem_static"), limit,
                        _requirement(requirement), options)


# ---- project-scope constructors ---------------------------------------------

def check_interference_free(min_volume_mm3: float = 0.001, *,
                            name: str | None = None,
                            requirement: str | None = None) -> dict:
    """No two placed instances overlap by more than *min_volume_mm3*."""
    return _declaration(
        "interference_free", "project", _name(name, "no_interference"),
        {"min_volume_mm3": _non_negative("min_volume_mm3", min_volume_mm3)},
        _requirement(requirement), {})


def check_clearance(a: str, b: str, min_mm: float, *,
                    max_mm: float | None = None, name: str | None = None,
                    requirement: str | None = None) -> dict:
    """At least *min_mm* of clear space between two placed instances.

    *max_mm* closes the other side of the bound — "these two are within X mm
    of each other" — so a check can grade *placement* and not only
    non-interference: a one-sided floor is satisfied by parking both instances
    500 mm apart. It is optional and additive; omitted, the declaration is
    byte-for-byte the one this constructor has always emitted.

    ``max_mm`` must be strictly greater than ``min_mm``: equal bounds are a
    window no real measurement can land in, and that is a limit that cannot
    pass rather than one that cannot fail (``_number``'s reasoning, the other
    way round).
    """
    a = _identifier("a", a)
    b = _identifier("b", b)
    if a == b:
        raise ValueError(f"a and b must be different instances, both are {a!r}")
    limit = {"min_mm": _positive("min_mm", min_mm)}
    if max_mm is not None:
        limit["max_mm"] = _positive("max_mm", max_mm)
        if limit["max_mm"] <= limit["min_mm"]:
            raise ValueError(
                f"max_mm ({limit['max_mm']}) must be > min_mm "
                f"({limit['min_mm']})")
    return _declaration("clearance", "project", _name(name, f"clearance_{a}_{b}"),
                        limit, _requirement(requirement), {"a": a, "b": b})


def check_stackup(from_instance: str, to_instance: str, axis: str, within: float,
                  *, name: str | None = None,
                  requirement: str | None = None) -> dict:
    """The 1-D tolerance stack-up between two mated instances, in mm.

    Worst-case accumulation along the mate chain must stay within *within*.
    """
    from_instance = _identifier("from_instance", from_instance)
    to_instance = _identifier("to_instance", to_instance)
    if from_instance == to_instance:
        raise ValueError(
            f"to_instance must differ from from_instance, both are "
            f"{from_instance!r}")
    if axis not in AXES:
        raise ValueError(f"axis must be one of {AXES}, got {axis!r}")
    return _declaration(
        "stackup", "project",
        _name(name, f"stackup_{from_instance}_{to_instance}_{axis}"),
        {"within_mm": _positive("within", within)}, _requirement(requirement),
        {"from_instance": from_instance, "to_instance": to_instance,
         "axis": axis})


# ---- helpers for the evaluators (kernel pack, runner) ------------------------

def declaration_problem(value) -> str | None:
    """Why *value* is not a declaration at this format version, or None.

    The ``spec`` marker alone used to be the whole test, and a dict carrying
    only ``spec``/``kind``/``scope`` was therefore accepted — while every
    reader downstream (the kernel pack's ``_record``, the service's ``_record``
    and ``_residue``) reads ``name``/``limit``/``options`` as required keys. An
    incomplete hand-written entry became a ``KeyError`` in the *server*, i.e. a
    500, instead of structural residue. So the whole emitted shape is validated
    here, once, and the reason names the offending key: it is the only text a
    script author gets back.
    """
    if not isinstance(value, dict):
        return f"a declaration is a dict, got {type(value).__name__}"
    if value.get("spec") != SPEC_FORMAT:
        return (f"'spec' must be {SPEC_FORMAT}, got {value.get('spec')!r}")
    if value.get("kind") not in PART_KINDS + PROJECT_KINDS:
        return f"unknown 'kind' {value.get('kind')!r}"
    if value.get("scope") not in ("part", "project"):
        return f"'scope' must be 'part' or 'project', got {value.get('scope')!r}"
    if not isinstance(value.get("name"), str) or not value["name"]:
        return f"'name' must be a non-empty string, got {value.get('name')!r}"
    for key in ("limit", "options"):
        if not isinstance(value.get(key), dict):
            return f"{key!r} must be a dict, got {value.get(key)!r}"
    requirement = value.get("requirement")
    if requirement is not None and not isinstance(requirement, str):
        return f"'requirement' must be a string or None, got {requirement!r}"
    return None


def is_declaration(value) -> bool:
    """True for a dict with the shape this module produces at this version.

    The *shape*, not the identity: a hand-written dict carrying every key is a
    declaration, and one missing any of them is structural residue that must be
    rejected before a reader indexes into it. See :func:`declaration_problem`,
    which is the same test with the reason attached.
    """
    return declaration_problem(value) is None


def json_safe(declaration: dict) -> dict:
    """A copy of *declaration* that crosses JSON-RPC: no callable, ever.

    ``check_that``'s ``fn`` becomes ``"predicate": true`` — the predicate
    itself never leaves the worker process that built the shape.
    """
    safe = {k: v for k, v in declaration.items() if k != "fn"}
    if "fn" in declaration:
        safe["predicate"] = True
    return safe
