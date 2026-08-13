"""ISO clearance and tapped holes, with a machine-readable record per call.

    from agentcad.toolkit import holes, patterns

    part, recs, warn = holes.clearance(part, patterns.bolt_circle(40, 6), "M5")
    part, recs, warn = holes.tapped(part, [(0, 0)], "M6", depth=12)

Each call returns `(part, records, warning|None)`: the new part, the records
*this call* created, and one warning string naming what it found — the `safe_*`
contract, extended with the metadata the drawing callouts and PRD-021's DFM
rules read.

Diameters are never invented here. They come from
`agentcad.toolkit.hole_standards`, which is the vendored ISO tables with their
provenance — `clearance(size, fit)["d"]` and `thread(size)["tap_drill"]`.

Where the records live, and why it is not a registry
----------------------------------------------------
The records ride **on the shape object the helper returns** (design Decision 4).
The alternative — a module-level registry the worker drains after `build(p)` —
is not fragile but wrong: the worker's `_SHAPE_CACHE` returns the cached shape
**without calling `build(p)`**, so on the second and every later build of an
unchanged part the registry drains empty and the records vanish silently.
Measured through the worker (changelog 0150): the attribute survives the cache
hit (the very same object comes back), survives LRU eviction and the real
rebuild that follows, and survives `handle_build`'s tessellation.

What it does **not** survive is any operation that returns a new object — also
measured: `safe_fillet`, `safe_bool` (both directions), a raw `part - tool` and
even a re-entered `BuildPart` + `add(part)` all come back without it. So every
helper that returns a part calls `carry()`, and `safe_fillet`/`safe_shell`/
`safe_bool` were taught to as well. For a raw build123d operation of your own,
call `holes.carry(new_part, old_part)` — or let slice 5's harvest tell you: it
compares `holes.created()` before and after the build and warns when records
went missing.

Naming a face without naming an ordinal
---------------------------------------
`plane` is a build123d `Plane`, or one of six names resolved **by predicate,
every rebuild** — never by face index, which renumbers on any topology change
(AGENTS.md, sketcher gotchas). A name resolves to the extreme planar face along
that axis, chosen by area, and the plane's origin is the part origin projected
onto it, so `points` stay part coordinates. As seen from outside the face:

| name | drills along | point `(u, v)` means |
|---|---|---|
| `top` | -Z | `(x, y)` at z = max |
| `bottom` | +Z | `(x, -y)` at z = min |
| `front` | +Y | `(x, z)` at y = min |
| `back` | -Y | `(-x, z)` at y = max |
| `right` | -X | `(y, z)` at x = max |
| `left` | +X | `(-y, z)` at x = min |

Ties (two coplanar faces at the same extreme) go to the larger area, then to
the lower `(u, v)` centre — documented so it is a rule, not an accident.
"""

from __future__ import annotations

import math

from build123d import (
    Axis,
    BuildPart,
    GeomType,
    Hole,
    Location,
    Locations,
    Plane,
    Vector,
    add,
)

from . import hole_standards
from .patterns import (
    POSITION_DECIMALS,
    boxes_overlap,
    bbox_of,
    engagement,
    spacing_conflicts,
)

#: The attribute the records ride on. Public because slice 5's handler pack and
#: the drawing pack read it off a built shape.
ATTR = "_agentcad_hole_records"

# Monotonic, NEVER reset, and only ever read as a delta across one build. An
# absolute count would be meaningless on a warm worker; a resettable counter
# would be exactly the shared mutable state Decision 4 removed.
_CREATED = 0

_VOLUME_TOL = 1e-9

# A face is "at the extreme" if it is within this of the extreme coordinate.
# Coplanar faces of one solid agree far more closely than this; a face 1 um
# lower is a different face.
_COPLANAR_TOL = 1e-6

# (axis, sign, x_dir) per named plane — see the module docstring's table.
_NAMED_PLANES = {
    "top": (Axis.Z, 1.0, (1.0, 0.0, 0.0)),
    "bottom": (Axis.Z, -1.0, (1.0, 0.0, 0.0)),
    "front": (Axis.Y, -1.0, (1.0, 0.0, 0.0)),
    "back": (Axis.Y, 1.0, (-1.0, 0.0, 0.0)),
    "right": (Axis.X, 1.0, (0.0, 1.0, 0.0)),
    "left": (Axis.X, -1.0, (0.0, -1.0, 0.0)),
}


# ------------------------------------------------------------- the carrier

def records(part) -> list[dict]:
    """Every hole record carried by `part`, oldest first. Empty is empty —
    a part with no holes and a part whose records were dropped look the same
    here, which is why the harvest compares `created()` deltas instead."""
    return list(getattr(part, ATTR, ()))


def carry(new_part, prior_part, new_records=()):
    """Move `prior_part`'s records onto `new_part`, appending `new_records`.

    Call this after any raw build123d operation that produces a new shape, or
    the records stop at that operation. It is a bookkeeping copy and asserts
    nothing about the geometry: carrying records across a cut that removed one
    of the holes leaves a record for a hole that is no longer there.
    """
    combined = records(prior_part) + [dict(record) for record in new_records]
    try:
        setattr(new_part, ATTR, combined)
    except AttributeError:  # pragma: no cover — a future slotted Shape
        return new_part
    return new_part


def carries_records(fn):
    """Decorator for a helper whose first argument is a part and whose first
    return value is the new part: the records travel with the geometry.

    This is how `safe_fillet`, `safe_shell` and `safe_bool` keep a part's hole
    records — measured (changelog 0150), all three return a brand-new object
    that has none of the original's attributes, so without this a script that
    drills and then fillets loses every record and every drawing callout with
    it. It is the design's "records compose along the helper chain" made true
    rather than assumed.
    """
    import functools

    @functools.wraps(fn)
    def wrapper(part, *args, **kwargs):
        out = fn(part, *args, **kwargs)
        if isinstance(out, tuple) and out and out[0] is not None:
            carry(out[0], part)
        return out

    return wrapper


def created() -> int:
    """The process-wide count of records ever created. Read it before and after
    a build and compare the delta with what the returned shape carries; that
    difference is the only reliable "records were dropped" signal, and it is
    immune to the warm-worker problem a resettable registry has."""
    return _CREATED


def dropped_records_warning(part, created_before: int) -> str | None:
    """The harvest's delta check: were records created that the returned part
    does not carry? Returns the warning, or `None` when everything arrived.

    A delta of **zero** means `build(p)` never ran — the worker served the
    shape from `_SHAPE_CACHE` — and no comparison is made, which is what makes
    this immune to the warm-worker contamination a resettable registry has.
    """
    delta = created() - int(created_before)
    if delta <= 0:
        return None
    missing = delta - len(records(part))
    if missing <= 0:
        return None
    return (
        f"holes: {missing} hole record(s) were created but did not reach the "
        f"returned part — an operation after the last toolkit call dropped "
        f"them. Return the part the helper gave you, or carry them across with "
        f"holes.carry(new_part, old_part).")


# --------------------------------------------------------- plane resolution

def resolve_plane(part, plane="top") -> Plane:
    """A `Plane` from a `Plane` or one of the six names. See the module
    docstring for the naming table and the tie-break.

    Raises `ValueError` naming the reason when a name resolves to nothing —
    a lathe part with no planar top face is a request that cannot be honoured,
    and honouring it approximately would drill into a curve.
    """
    if isinstance(plane, Plane):
        return plane
    if not isinstance(plane, str) or plane.lower() not in _NAMED_PLANES:
        raise ValueError(
            f"plane must be a build123d Plane or one of "
            f"{sorted(_NAMED_PLANES)}, got {plane!r}")
    name = plane.lower()
    axis, sign, x_dir = _NAMED_PLANES[name]
    faces = [face for face in part.faces()
             if face.geom_type == GeomType.PLANE
             and abs(face.normal_at(face.center()).dot(
                 Vector(axis.direction))) > 1 - 1e-6]
    if not faces:
        raise ValueError(
            f"plane={plane!r}: this part has no planar face normal to "
            f"{name}'s axis, so there is nothing to drill into. Pass an "
            f"explicit Plane(origin=..., z_dir=...) instead.")

    def coordinate(face) -> float:
        return float(Vector(face.center()).dot(Vector(axis.direction)))

    extreme = max(coordinate(face) * sign for face in faces) * sign
    at_extreme = [face for face in faces
                  if abs(coordinate(face) - extreme) <= _COPLANAR_TOL]
    # Documented tie-break: largest area first, then the lowest centre, so two
    # equal coplanar faces resolve the same way on every rebuild.
    chosen = sorted(at_extreme,
                    key=lambda f: (-f.area, Vector(f.center()).X,
                                   Vector(f.center()).Y,
                                   Vector(f.center()).Z))[0]
    origin = Vector(axis.direction) * coordinate(chosen)
    return Plane(origin=origin, x_dir=Vector(x_dir),
                 z_dir=Vector(axis.direction) * sign)


# ------------------------------------------------------------------- holes

def clearance(part, points, size: str, *, plane="top", fit: str = "medium",
              std: str = "iso", depth: float | None = None, thru: bool = True,
              verify: str = "bbox"):
    """Clearance holes for `size` at `fit`, at `points` on `plane`.

    The diameter is ISO 273's, from the vendored table — `M5` medium is 5.5 mm,
    not "about 5.5". Returns `(part, records, warning|None)`; the record is one
    **group** for the whole call (`count`, `positions`), which is FR3 for free.

    `depth=None, thru=True` drills through. A `depth` makes it blind and `thru`
    is reported as False whatever you passed.
    """
    row = hole_standards.clearance(size, fit=fit, std=std)
    record = {
        "family": "clearance", "standard": row["std"], "size": row["size"],
        "fit": row["fit"], "d": row["d"], "tap": None, "cbore": None,
        "csk": None, "designation": row["designation"],
    }
    return _drill(part, points, row["d"] / 2.0, record, plane=plane,
                  depth=depth, thru=thru, verify=verify,
                  label="holes.clearance")


def tapped(part, points, size: str, *, pitch: float | None = None,
           depth: float | None = None, thread_class: str = "6H", plane="top",
           std: str = "iso", thread: str = "none", verify: str = "bbox"):
    """Tapped holes: bore the **tap drill** and record the thread.

    `thread="none"` (the default) builds no thread geometry — a tapped hole on
    a drawing is a callout, not a helix, and the record carries the
    designation. `thread="real"` additionally fuses real ISO thread geometry,
    which costs **~9k triangles per hole** (`toolkit.threads`); the warning
    says so, and `depth` is then required because a thread has a length.

    The two radii are not interchangeable, and this is the CHEATSHEET's
    hard-won rule: bore at the **tap drill** for a cosmetic/recorded thread,
    and at `thread.root_radius` when you fuse real thread geometry — boring at
    the physical tap-drill diameter buries the ridges in the wall and you get a
    valid, fast, invisible thread.
    """
    if thread not in ("none", "real"):
        raise ValueError(
            f"thread must be 'none' or 'real', got {thread!r}")
    row = hole_standards.thread(size, pitch=pitch, depth=depth,
                                thread_class=thread_class, std=std)
    if thread == "real" and depth is None:
        raise ValueError(
            "depth is required when thread='real': real thread geometry has a "
            "length. Use thread='none' for a through tapped hole and let the "
            "record carry the designation.")
    record = {
        "family": "tapped", "standard": row["std"], "size": row["size"],
        "fit": None, "d": row["tap_drill"], "cbore": None, "csk": None,
        "designation": row["designation"],
        "tap": {"pitch": row["pitch"], "class": thread_class,
                "drill_mm": row["tap_drill"], "thread": row["thread"],
                "series": row["series"], "geometry": thread},
    }
    radius = row["tap_drill"] / 2.0
    thread_solid = None
    if thread == "real":
        from . import threads as threads_module

        thread_solid = threads_module.tapped_hole_thread(
            _major_diameter(row["size"]), row["pitch"], depth)
        # Bore at the ROOT radius, not the tap drill: the ridges have to have
        # somewhere to protrude into (CHEATSHEET, templates.py).
        radius = float(thread_solid.root_radius)
        record["tap"]["bore_mm"] = _round(radius * 2)
    return _drill(part, points, radius, record, plane=plane, depth=depth,
                  thru=depth is None, verify=verify, label="holes.tapped",
                  fuse=thread_solid)


def _major_diameter(size: str) -> float:
    """`M5` -> 5.0. The tables key on the ISO designation, the thread builder
    wants the nominal diameter."""
    try:
        return float(str(size).strip().upper().lstrip("M"))
    except ValueError as exc:
        raise ValueError(
            f"size {size!r} is not an ISO metric designation like 'M5', so its "
            f"major diameter cannot be read for real thread geometry") from exc


# ------------------------------------------------------------------ drilling

def _drill(part, points, radius: float, record: dict, *, plane, depth,
           thru: bool, verify: str, label: str, fuse=None):
    """One bore, one record. The single place a hole is cut, so `clearance` and
    `tapped` cannot drift apart in how they place, guard or record it.

    `fuse` is an optional solid added at every instance after the bore (real
    thread geometry), placed on the same into-the-material frame as the tool.
    """
    global _CREATED

    pts = _check_points(points, label)
    depth = _check_depth(depth, label)
    if not thru and depth is None:
        raise ValueError(
            f"{label}: thru=False needs a depth — a blind hole of no stated "
            f"depth is not geometry")
    thru = bool(thru and depth is None)
    workplane = resolve_plane(part, plane)
    locations = [workplane.location * Location(Vector(u, v, 0.0))
                 for u, v in pts]
    centers = [_round3(loc.position) for loc in locations]

    stock = _extent(part, workplane)
    reach = stock if thru else float(depth)
    warnings = []
    report, guard_warnings = _guard(
        part, workplane, locations, radius, reach, stock, thru, verify, label)
    warnings += guard_warnings
    warnings += _spacing_warnings(pts, radius * 2.0, label)
    warnings += _proximity_warnings(records(part), centers, radius * 2.0, label)

    def place():
        with Locations(*locations):
            Hole(radius=radius, depth=None if thru else depth)
        if fuse is not None:
            for loc in locations:
                add(_bore_plane(loc, workplane).location * fuse)

    out, fallback = _bore(
        part, place, locations, workplane, radius, reach, label,
        extra=[] if fuse is None else
        [_bore_plane(loc, workplane).location * fuse for loc in locations])
    if fallback:
        warnings.append(fallback)

    removed = part.volume - out.volume
    if removed <= _VOLUME_TOL and fuse is None:
        warnings.append(
            f"{label}: nothing was removed (volume delta {removed:.6g} mm^3). "
            f"OCCT does not fail on a misplaced cut — it succeeds and changes "
            f"nothing — so this is the only signal that every instance missed "
            f"the material.")
    if fuse is not None:
        warnings.append(
            f"{label}: thread='real' fused {len(pts)} real ISO thread solid(s) "
            f"— about 9k triangles each at mesh tolerance 0.1. Use "
            f"thread='none' unless the thread has to be manufactured from this "
            f"mesh.")

    axis = tuple(-Vector(workplane.z_dir))
    full = dict(record)
    full.update({
        "id": f"h{len(records(part))}",
        "count": len(pts),
        "positions": [[_round(u), _round(v)] for u, v in pts],
        "centers": centers,
        "axis": [_round(v) for v in axis],
        "plane": {"origin": _round3(workplane.origin),
                  "z_dir": _round3(workplane.z_dir),
                  "x_dir": _round3(workplane.x_dir)},
        "depth_mm": None if thru else _round(depth),
        "thru": thru,
        "removed_mm3": _round(removed),
        "instances": report,
        "pattern": None,
    })
    _CREATED += 1
    carry(out, part, [full])
    return out, [full], "; ".join(warnings) if warnings else None


def _bore(part, place, locations, workplane, radius, reach, label, extra=()):
    """Run `place()` inside a `BuildPart` that has `add(part)`-ed the caller's
    part; on failure fall back to one `safe_bool` cut of all the tools at once.

    The builder is the primary route because it is the one measured
    byte-identical to the hand-written `Locations` + `Hole` block (design
    Decision 1, changelog 0147) — which is the whole reason a wrapper is worth
    writing. `safe_bool` is the fallback rung, and its warning says the result
    may no longer be byte-identical, because a compound cut is a different
    construction (measured: same volume, different mesh — changelog 0147).
    """
    try:
        with BuildPart() as builder:
            add(part)
            place()
        return builder.part, None
    except Exception as exc:  # noqa: BLE001 — OCCT raises many types
        from build123d import Compound

        from .boolean import safe_bool

        tools = Compound(children=[_tool_solid(loc, workplane, radius, reach)
                                   for loc in locations])
        out, fuzzy = safe_bool(part, tools, "cut")
        notes = [fuzzy] if fuzzy else []
        for solid in extra:
            # thread geometry the primary route would have fused in; the
            # fallback has to put it back or the hole loses its thread
            out, fuzzy = safe_bool(out, solid, "fuse")
            if fuzzy:
                notes.append(fuzzy)
        detail = (" " + " ".join(notes)) if notes else ""
        return out, (
            f"{label}: the build123d Hole route failed "
            f"({type(exc).__name__}: {exc}); fell back to a safe_bool cut. The "
            f"result may not be byte-identical to the primary route.{detail}")


def _guard(part, workplane, locations, radius, reach, stock, thru, verify,
           label):
    """The two-tier per-instance contract, shared with `patterns`.

    The free tier builds no geometry at all: a hole's tool is a cylinder, so
    its axis-aligned envelope is arithmetic. The `exact` tier has to build the
    tools, and costs about 2.1-2.4 ms per instance through the worker against
    0.014 ms for the box (changelog 0149).
    """
    if verify == "off":
        return [], []
    warnings = []
    if verify == "exact":
        report = engagement(
            part, [(i, _tool_solid(loc, workplane, radius, reach))
                   for i, loc in enumerate(locations)], verify="exact")
    else:
        part_box = bbox_of(part)
        report = [
            {"i": i,
             "status": "engaged" if boxes_overlap(
                 part_box, _tool_box(loc, workplane, radius, reach))
             else "missed",
             "probe": "bbox", "engaged_mm3": None}
            for i, loc in enumerate(locations)]

    missed = [row["i"] for row in report if row["status"] == "missed"]
    if missed:
        warnings.append(
            f"{label}: instance(s) {missed} do not reach the part; a cut that "
            f"misses is a silent no-op in OCCT, not an error")
    flush = [row["i"] for row in report if row["status"] == "flush"]
    if flush:
        warnings.append(
            f"{label}: instance(s) {flush} touch the part but remove no "
            f"material (engaged volume 0)")
    if not thru and reach > stock + _VOLUME_TOL:
        warnings.append(
            f"{label}: depth {reach:g} mm is deeper than the stock "
            f"below this plane ({stock:g} mm), so the hole breaks through")
    return report, warnings


def _bore_plane(loc, workplane) -> Plane:
    """The plane at one instance whose +Z points **into** the material, i.e.
    the direction the tool travels. `x_dir` is inherited from the workplane so
    the frame is defined rather than derived (build123d picks an arbitrary
    x_dir for a bare z_dir, and an arbitrary frame is not reproducible)."""
    return Plane(origin=Vector(loc.position), x_dir=Vector(workplane.x_dir),
                 z_dir=-Vector(workplane.z_dir))


def _tool_solid(loc, workplane, radius: float, reach: float):
    """The cutting cylinder for one instance, placed where `Hole` puts it:
    starting at the location and running into the material."""
    from build123d import Align, Cylinder

    cylinder = Cylinder(radius, reach,
                        align=(Align.CENTER, Align.CENTER, Align.MIN))
    return _bore_plane(loc, workplane).location * cylinder


def _tool_box(loc, workplane, radius, reach):
    """The axis-aligned envelope of one hole's cutting cylinder, arithmetic.

    Conservative by construction: the cylinder is enclosed by the box swept
    from its start point to its end point and grown by `radius` on every axis.
    Exact for the axis-aligned named planes; an over-estimate for a tilted
    plane, which can only ever turn a real miss into an "engaged" — it never
    invents a miss.
    """
    start = Vector(loc.position)
    end = start - Vector(workplane.z_dir) * reach
    lo = [min(start.X, end.X) - radius, min(start.Y, end.Y) - radius,
          min(start.Z, end.Z) - radius]
    hi = [max(start.X, end.X) + radius, max(start.Y, end.Y) + radius,
          max(start.Z, end.Z) + radius]
    return (lo[0], lo[1], lo[2], hi[0], hi[1], hi[2])


def _extent(part, workplane) -> float:
    """How much stock there is below the plane, measured on the part's bounding
    box along the plane's normal. A bounding-box measure, so it is an
    over-estimate for anything but a prism — it warns about a depth that cannot
    fit at all, not about a depth that misses a local pocket."""
    box = part.bounding_box()
    corners = [Vector(x, y, z)
               for x in (box.min.X, box.max.X)
               for y in (box.min.Y, box.max.Y)
               for z in (box.min.Z, box.max.Z)]
    normal = Vector(workplane.z_dir)
    origin = Vector(workplane.origin)
    depths = [(origin - corner).dot(normal) for corner in corners]
    return max(0.0, max(depths))


# ------------------------------------------------------------------ warnings

def _spacing_warnings(points, diameter: float, label: str) -> list[str]:
    clashes = spacing_conflicts(points, diameter)
    if not clashes:
        return []
    pairs = ", ".join(f"{c['a']}&{c['b']} ({c['distance']:g} mm)"
                      for c in clashes)
    return [f"{label}: instance pair(s) {pairs} are closer than one diameter "
            f"({diameter:g} mm) apart; the holes merge into a slot"]


def _proximity_warnings(prior: list[dict], centers, diameter: float,
                        label: str) -> list[str]:
    if not prior:
        return []
    hits = []
    for i, center in enumerate(centers):
        for record in prior:
            for other in record.get("centers", ()):
                if math.dist(center, other) < diameter:
                    hits.append((i, record.get("id", "?")))
                    break
    if not hits:
        return []
    named = ", ".join(f"{i} near {rid}" for i, rid in sorted(set(hits)))
    return [f"{label}: instance(s) {named} sit within one diameter "
            f"({diameter:g} mm) of an existing recorded hole"]


# ---------------------------------------------------------------- coercion

def _check_points(points, label) -> list[tuple[float, float]]:
    try:
        pts = [(float(point[0]), float(point[1])) for point in points]
    except (TypeError, ValueError, IndexError) as exc:
        raise ValueError(
            f"{label}: points must be a sequence of (u, v) pairs on the "
            f"plane, got {points!r}") from exc
    if not pts:
        raise ValueError(f"{label}: points is empty; there is no hole to drill")
    return pts


def _check_depth(depth, label) -> float | None:
    if depth is None:
        return None
    value = float(depth)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(
            f"{label}: depth must be > 0 (or None for a through hole), got "
            f"{depth!r}")
    return value


def _round(value: float) -> float:
    return round(float(value), POSITION_DECIMALS) + 0.0


def _round3(vec) -> list[float]:
    point = Vector(vec)
    return [_round(point.X), _round(point.Y), _round(point.Z)]
