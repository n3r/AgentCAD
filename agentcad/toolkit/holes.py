"""Holes, with a machine-readable record per call.

    from agentcad.toolkit import holes, patterns

    part, recs, warn = holes.clearance(part, patterns.bolt_circle(40, 6), "M5")
    part, recs, warn = holes.tapped(part, [(0, 0)], "M6", depth=12)
    part, recs, warn = holes.counterbore(part, [(20, 0)], "M8")
    part, recs, warn = holes.countersink(part, [(-20, 0)], "M6")
    part, recs, warn = holes.drill(part, [(0, 30)], 18.0)      # no table
    part, recs, warn = holes.clearance(part, [(0, 0)], "1/4", std="ansi")

Each call returns `(part, records, warning|None)`: the new part, the records
*this call* created, and one warning string naming what it found — the `safe_*`
contract, extended with the metadata the drawing callouts and PRD-021's DFM
rules read.

A record counts what came off, not what was asked for
-----------------------------------------------------
`count`, `positions` and `centers` cover only the instances the guard proved
removed material; the rest are listed under `dropped` and named in the warning.
That is not defensive tidiness — OCCT does not fail on a misplaced cut, it
succeeds and changes nothing, and the *aggregate* volume delta cannot be
attributed to an instance, so one successful cut used to hide any number of
no-ops (see `_guard` for the frame that measured it). `verify="off"` is the one
setting under which a count is intent rather than measurement.

**`record["verify"]` is the mode that was REQUESTED, not the tier that
answered.** It echoes the `verify=` argument, and it has to: the default runs
up to three different probes and a 50-hole call routinely uses two of them, so
there is no single tier for the record to name. The tier that actually decided
an instance is `record["instances"][i]["probe"]` — `"bbox"`, `"axis"`,
`"exact"` or `"off"` — and that is the field to read when the question is "how
do we know". `"verify": "bbox"` beside `"probe": "axis"` is not a
contradiction; it is the request beside the answer.

`record["provenance"]` carries the published sources behind the *diameter* —
`{standard, sources, corroborated, conflicts}`, unioned over every table row
that fed the hole (a counterbore has two). `corroborated` is true only for two
or more independent sources that agree, so a single-sourced or adjudicated
diameter says so at the point where it becomes a manufacturing callout instead
of only in the `hole_standards` tool. A `drilled` hole has no table row and
carries `None`.

`designation` is derived from the finished record by
`hole_standards.designation_for_record` — never assembled beside it — so a
reader can re-derive it and refuse a carrier whose text and numbers have come
apart. `designation_base` is the same callout with no depth qualifier, for a
reader that has *measured* that a recorded blind depth no longer holds.

Diameters are never invented here. They come from
`agentcad.toolkit.hole_standards`, which is the vendored ISO/ASME tables with
their provenance — `clearance(size, fit)["d"]` and `thread(size)["tap_drill"]`.
The one helper with no table behind it is `drill`, which takes millimetres
because a structural bolt hole *has* no fastener row (18 mm for an M16 is
none of ISO 273's M16 values), and its record says so by carrying no `size`.

**Every length crossing this module is millimetres**, including under
`std="ansi"`; only the designation text is in the standard's own unit. See
`hole_standards`' "Units" section — a millimetre formatted as an inch callout
is a 25.4x error that looks like a plausible number.

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

# How many points along one instance's bore axis the default tier classifies
# against the solid before it falls back to the exact boolean probe. A point
# strictly INSIDE the part at a parameter the tool covers is a *proof* that the
# tool removes material (the tool contains a ball around that point), so this
# is a performance knob and NOT a tolerance: more samples only ever save a
# boolean, and fewer only ever pay for one. Nine costs 0.041 ms per instance
# against the exact probe's ~5 ms, and proved every instance on a 12 mm plate,
# on a 1 mm plate (where the tool's reach is the whole stock) and on the
# inward-normal slab.
_AXIS_SAMPLES = 9

# The classifier's own tolerance. Points ON the boundary answer `ON`, which is
# not `IN`, so a tangent tool is never "proved" here and falls through to the
# exact probe that can tell a seat from a miss.
_CLASSIFY_TOL = 1e-7

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

    **That is deliberate and it is why every reader holding the geometry
    re-measures.** Verifying survival here would cost a boolean per instance on
    every helper in the chain, and the reader that needs the answer is the one
    producing a manufacturing claim. So the drawing pack prints the count of
    circles it actually matched (not the record's), and drops a recorded blind
    depth whose material is gone. A record is intent; a drawing is a
    measurement of the part in front of it.
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

def drill(part, points, diameter: float, *, plane="top", std: str = "iso",
          depth: float | None = None, thru: bool = True,
          verify: str = "bbox"):
    """A plain drilled hole of a stated diameter — no fastener, no table.

    `clearance` and `tapped` exist because a hole *for a fastener* has a right
    diameter that a published table owns. A hole in a structural plate often
    has none: `construction/gusset_plate` drills 18 mm for an M16 bolt (EN 1090
    gives structural bolts 2 mm of clearance, so it is none of ISO 273's M16
    values 17.0/17.5/18.5), and the diameter is a *parameter* the designer
    sweeps. So this takes the millimetres, and the record carries no `size` and
    no table provenance — claiming one would put a standard's name on a number
    the standard did not supply.

    `std` selects the callout **symbology** only (the `⌀`/`↧` grammar), which
    is why it is still an argument: it decides how the number is written, not
    what the number is.
    """
    d = _check_diameter(diameter, "holes.drill")
    # No `designation` here, or in any helper below: `_drill` derives it from
    # the finished record with `hole_standards.designation_for_record`, so the
    # text and the numbers cannot drift apart and a reader can re-derive it to
    # check a carrier. The millimetre -> callout-unit conversion lives there
    # too — `⌀18` under an ASME label would be an 18-inch hole.
    record = {
        "family": "drilled", "standard": hole_standards.check_std(std),
        "size": None, "fit": None, "d": d, "tap": None, "cbore": None,
        "csk": None,
        # No table supplied this number, so there is no provenance to claim.
        # `null` here is a fact, not an omission — `validate_record` refuses a
        # drilled record that carries one.
        "provenance": None,
    }
    return _drill(part, points, d / 2.0, record, plane=plane, depth=depth,
                  thru=thru, verify=verify, label="holes.drill")


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
        "csk": None,
        "provenance": hole_standards.merge_provenance(row),
    }
    return _drill(part, points, row["d"] / 2.0, record, plane=plane,
                  depth=depth, thru=thru, verify=verify,
                  label="holes.clearance")


def tapped(part, points, size: str, *, pitch: float | None = None,
           depth: float | None = None, thread_class: str | None = None,
           plane="top", std: str = "iso", thread: str = "none",
           verify: str = "bbox"):
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
        "tap": {"pitch": row["pitch"], "tpi": row["tpi"],
                "class": row["thread_class"], "drill_mm": row["tap_drill"],
                "drill": row["drill"], "thread": row["thread"],
                "series": row["series"], "geometry": thread},
        "provenance": hole_standards.merge_provenance(row),
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


def counterbore(part, points, size: str, *, plane="top", fit: str = "medium",
                std: str = "iso", fastener: str | None = None,
                cbore_d: float | None = None, cbore_depth: float | None = None,
                depth: float | None = None, thru: bool = True,
                verify: str = "bbox"):
    """A clearance hole with a flat-bottomed pocket for the fastener's head.

    The through hole is the standard's clearance hole for `size` at `fit`. The
    **pocket is not a table value**: the published counterbore charts disagree
    by up to 0.75 mm on one M8 (measured, changelog 0148), so
    `hole_standards.cbore` returns the corroborated *head* geometry and applies
    a named clearance rule to it. Pass `cbore_d`/`cbore_depth` (millimetres) to
    use your own shop's numbers; the record then carries yours.

    Returns `(part, records, warning|None)`.
    """
    row = hole_standards.clearance(size, fit=fit, std=std)
    head = hole_standards.cbore(size, fastener=fastener, std=std)
    bore_d = float(head["d"] if cbore_d is None else cbore_d)
    bore_depth = float(head["depth"] if cbore_depth is None else cbore_depth)
    if bore_d <= row["d"]:
        raise ValueError(
            f"holes.counterbore: cbore_d {bore_d:g} mm is not larger than the "
            f"{row['designation']} clearance hole it sits over")
    if not math.isfinite(bore_depth) or bore_depth <= 0:
        raise ValueError(
            f"holes.counterbore: cbore_depth must be > 0, got {cbore_depth!r}")
    record = {
        "family": "counterbore", "standard": row["std"], "size": row["size"],
        "fit": row["fit"], "d": row["d"], "tap": None, "csk": None,
        "cbore": {"d": _round(bore_d), "depth": _round(bore_depth),
                  "fastener": head["fastener"]},
        # TWO published rows feed this hole - the clearance diameter and the
        # fastener head - so the record claims what backs both and no more.
        "provenance": hole_standards.merge_provenance(row, head),
    }
    # Resolved once and passed on as a Plane: the named-plane predicate walks
    # every face, and the tool factory below and the bore itself must agree on
    # the answer, not merely on the question.
    workplane = resolve_plane(part, plane)
    # The same into-the-material frame `_drill` will derive from this plane;
    # the tool factory below has to agree with it or the guard probes and the
    # fallback cut are built facing the wrong way (see `_tool_frame`).
    frame = _tool_frame(part, workplane)
    radius = row["d"] / 2.0
    bore_r = bore_d / 2.0

    def cut():
        from build123d import CounterBoreHole

        CounterBoreHole(radius=radius, counter_bore_radius=bore_r,
                        counter_bore_depth=bore_depth,
                        depth=None if depth is None else depth)

    def tool(loc, reach):
        from build123d import Align, Cylinder

        plane_at = _bore_plane(loc, frame)
        align = (Align.CENTER, Align.CENTER, Align.MIN)
        return plane_at.location * (Cylinder(radius, reach, align=align)
                                    + Cylinder(bore_r, bore_depth, align=align))

    out, records_, warning = _drill(
        part, points, radius, record, plane=workplane, depth=depth, thru=thru,
        verify=verify, label="holes.counterbore", cut=cut, tool=tool,
        envelope_r=bore_r)
    stock = _extent(part, frame)
    if bore_depth >= stock - _VOLUME_TOL:
        note = (f"holes.counterbore: the {bore_depth:g} mm pocket is not "
                f"shallower than the {stock:g} mm of stock below this plane, "
                f"so the head has nothing to bear on")
        warning = f"{warning}; {note}" if warning else note
    return out, records_, warning


def countersink(part, points, size: str, *, plane="top", fit: str = "medium",
                std: str = "iso", fastener: str | None = None,
                angle: float | None = None, csk_d: float | None = None,
                depth: float | None = None, thru: bool = True,
                verify: str = "bbox"):
    """A clearance hole with a conical seat for a flat-head fastener.

    **The angle is passed to build123d explicitly, always.** `CounterSinkHole`
    defaults to 82 deg, which is an ASME default; an ISO countersink is 90, and
    a default inherited from the geometry library would put an ASME cone inside
    an ISO-labelled call. `hole_standards.default_csk_angle` owns the number.

    `csk_d` is the seat diameter at the surface and defaults to the fastener's
    **theoretical sharp** head diameter — the dimension a countersink callout
    names, which already stands off the machined head max, so no clearance is
    added to it.

    Returns `(part, records, warning|None)`.
    """
    row = hole_standards.clearance(size, fit=fit, std=std)
    head = hole_standards.csk(size, angle=angle, fastener=fastener, std=std)
    seat_d = float(head["d"] if csk_d is None else csk_d)
    included = float(head["angle_deg"])
    if seat_d <= row["d"]:
        raise ValueError(
            f"holes.countersink: csk_d {seat_d:g} mm is not larger than the "
            f"{row['designation']} clearance hole it sits over")
    if not 0.0 < included < 180.0:
        raise ValueError(
            f"holes.countersink: angle must be an included angle in "
            f"(0, 180) degrees, got {angle!r}")
    record = {
        "family": "countersink", "standard": row["std"], "size": row["size"],
        "fit": row["fit"], "d": row["d"], "tap": None, "cbore": None,
        "csk": {"d": _round(seat_d), "angle_deg": _round(included),
                "fastener": head["fastener"]},
        "provenance": hole_standards.merge_provenance(row, head),
    }
    workplane = resolve_plane(part, plane)
    frame = _tool_frame(part, workplane)      # see counterbore, and _tool_frame
    radius = row["d"] / 2.0
    seat_r = seat_d / 2.0
    cone_h = (seat_r - radius) / math.tan(math.radians(included / 2.0))

    def cut():
        from build123d import CounterSinkHole

        CounterSinkHole(radius=radius, counter_sink_radius=seat_r,
                        depth=None if depth is None else depth,
                        counter_sink_angle=included)

    def tool(loc, reach):
        from build123d import Align, Cone, Cylinder

        plane_at = _bore_plane(loc, frame)
        align = (Align.CENTER, Align.CENTER, Align.MIN)
        return plane_at.location * (
            Cylinder(radius, reach, align=align)
            + Cone(bottom_radius=seat_r, top_radius=radius, height=cone_h,
                   align=align))

    return _drill(part, points, radius, record, plane=workplane, depth=depth,
                  thru=thru, verify=verify, label="holes.countersink",
                  cut=cut, tool=tool, envelope_r=seat_r)


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
           thru: bool, verify: str, label: str, fuse=None, cut=None,
           tool=None, envelope_r=None):
    """One bore, one record. The single place a hole is cut, so `clearance`,
    `tapped`, `drill`, `counterbore` and `countersink` cannot drift apart in
    how they place, guard or record it.

    `fuse` is an optional solid added at every instance after the bore (real
    thread geometry), placed on the same into-the-material frame as the tool.

    `cut` is the build123d operator to run inside the `Locations` block — the
    default is `Hole`, and `counterbore`/`countersink` pass their own so the
    primary (byte-faithful) route stays build123d's own operator rather than a
    hand-rolled boolean. `tool` builds the equivalent solid for the guard's
    exact probe and for the `safe_bool` fallback; `envelope_r` is the radius of
    the bbox screen, which for a counterbore is the *pocket*, not the bore.
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

    # Everything that has to know which way the material lies uses `frame`;
    # everything that positions a hole uses `workplane`. See `_tool_frame`.
    frame = _tool_frame(part, workplane)
    stock = _extent(part, frame)
    reach = stock if thru else float(depth)
    envelope_r = radius if envelope_r is None else float(envelope_r)
    if tool is None:
        def tool(loc, reach_mm):
            return _tool_solid(loc, frame, radius, reach_mm)
    warnings = []
    report, guard_warnings = _guard(
        part, frame, locations, radius, reach, stock, thru, verify, label,
        tool=tool, envelope_r=envelope_r)
    warnings += guard_warnings
    warnings += _spacing_warnings(pts, envelope_r * 2.0, label)
    warnings += _proximity_warnings(records(part), centers, envelope_r * 2.0,
                                    label)

    def place():
        with Locations(*locations):
            if cut is None:
                Hole(radius=radius, depth=None if thru else depth)
            else:
                cut()
        if fuse is not None:
            for loc in locations:
                add(_bore_plane(loc, frame).location * fuse)

    out, fallback = _bore(
        part, place, locations, frame, reach, label, tool,
        extra=[] if fuse is None else
        [_bore_plane(loc, frame).location * fuse for loc in locations])
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

    # WHICH INSTANCES THE RECORD IS ALLOWED TO CLAIM. `_guard` proves, per
    # instance, whether material came off; anything it proved did not (`missed`,
    # `flush`) is dropped from the count, the positions and the centres, because
    # every downstream reader treats those as holes that exist — the drawing
    # points a leader at each centre, the DFM rules count them, and the
    # proximity check measures against them. The aggregate volume delta below
    # cannot do this job: one successful cut hides any number of no-ops.
    #
    # `verify="off"` returns no report and therefore drops nothing: the caller
    # asked for no measurement, and the record says so in `verify` rather than
    # quietly presenting intent as fact.
    status = {row["i"]: row["status"] for row in report}
    no_op = {i for i in range(len(pts))
             if status.get(i, "unchecked") in ("missed", "flush")}
    kept = [i for i in range(len(pts)) if i not in no_op]
    dropped = [{"i": i, "status": status[i],
                "position": [_round(pts[i][0]), _round(pts[i][1])]}
               for i in sorted(no_op)]

    # The direction the tool actually travelled, which is `frame`'s, not the
    # caller's. `plane` below still reports the frame the caller gave, because
    # that is the one the recorded (u, v) positions are expressed in.
    axis = tuple(-Vector(frame.z_dir))
    full = dict(record)
    full.update({
        "id": f"h{len(records(part))}",
        "count": len(kept),
        "positions": [[_round(pts[i][0]), _round(pts[i][1])] for i in kept],
        "centers": [centers[i] for i in kept],
        "axis": [_round(v) for v in axis],
        "plane": {"origin": _round3(workplane.origin),
                  "z_dir": _round3(workplane.z_dir),
                  "x_dir": _round3(workplane.x_dir)},
        "depth_mm": None if thru else _round(depth),
        "thru": thru,
        "removed_mm3": _round(removed),
        "instances": report,
        "verify": verify,
        "dropped": dropped,
        "pattern": None,
    })
    # Derived from the finished record, never assembled beside it, so
    # `hole_standards.validate_record` can re-derive it and catch a carrier
    # whose text and numbers have drifted apart. `designation_base` is the same
    # callout with no depth qualifier: it is what a reader prints when it has
    # measured that the geometry no longer supports the recorded depth.
    full["designation"] = hole_standards.designation_for_record(full)
    full["designation_base"] = hole_standards.designation_for_record(
        {**full, "thru": True, "depth_mm": None})
    if dropped:
        warnings.append(
            f"{label}: the record claims {len(kept)} of {len(pts)} instance(s) "
            f"— {[row['i'] for row in dropped]} removed no material and are "
            f"recorded under `dropped` instead of being counted as holes")
    # A DISPUTED table cell warns; a merely SINGLE-SOURCED one does not.
    # Both travel in `record["provenance"]` and out through `add_holes`, so
    # neither is invisible — the difference is that a resolved disagreement is
    # a decision someone made about two conflicting publications and is worth
    # interrupting for, while "the ISO 10642 head table has one source" is a
    # permanent, unfixable property of every metric countersink this repo can
    # ship. A warning nothing can ever clear teaches readers to ignore
    # warnings (the `strict_exempt` lesson from PRD-004's DXF row).
    conflicts = (full.get("provenance") or {}).get("conflicts") or []
    if conflicts:
        warnings.append(
            f"{label}: the {full.get('size') or ''} row this hole's diameter "
            f"came from is DISPUTED — its two published sources disagreed and "
            f"the value shipped is an adjudication, not a corroboration. "
            f"{' '.join(conflicts)}")
    _CREATED += 1
    carry(out, part, [full])
    return out, [full], "; ".join(warnings) if warnings else None


def _bore(part, place, locations, workplane, reach, label, tool, extra=()):
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

        tools = Compound(children=[tool(loc, reach) for loc in locations])
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


def _axis_proof(classifier, loc, workplane, reach: float) -> bool:
    """Whether a point strictly INSIDE the part lies on this instance's bore
    axis, within the length the tool covers.

    This is a **proof of engagement, not a screen**: every tool this module
    builds contains the bore cylinder of radius > 0 over `[0, reach]`, so an
    interior point of the solid on that segment has a neighbourhood inside both
    shapes and the intersection therefore has positive volume. `False` proves
    nothing at all — a thin wall between two samples, or a tool that only
    touches — which is why the caller escalates rather than concluding.

    Measured against the alternatives, per instance, on a 200x200x12 plate:
    bounding-box overlap 0.014 ms, this 0.041 ms, the exact `(part & tool)`
    probe about 5 ms. On a 50-hole pattern that is 114.7 ms end to end with
    this against 372.1 ms with `verify="exact"`.

    The classifier is built from `part.wrapped`, which is routinely a Compound
    of several solids — measured working on a two-solid part, where the gap
    between the solids is inside the part's bounding box and a hole placed
    there is exactly the miss the box cannot see.
    """
    from OCP.TopAbs import TopAbs_IN
    from OCP.gp import gp_Pnt

    start = Vector(loc.position)
    step = -Vector(workplane.z_dir)
    for k in range(_AXIS_SAMPLES):
        point = start + step * (reach * (k + 0.5) / _AXIS_SAMPLES)
        classifier.Perform(gp_Pnt(point.X, point.Y, point.Z), _CLASSIFY_TOL)
        if classifier.State() == TopAbs_IN:
            return True
    return False


def _guard(part, workplane, locations, radius, reach, stock, thru, verify,
           label, *, tool, envelope_r):
    """The per-instance contract. Every status it reports is **measured on that
    instance**, under both `verify="bbox"` and `verify="exact"`.

    It did not used to be. The default tier compared each tool against the
    whole part's bounding box, which says nothing about the material actually
    under the tool — so a hole drilled into a part's own void reported
    `engaged`, raised nothing, and the aggregate "did anything come off?" check
    passed because a *different* instance had removed material. Measured on a
    100x100x10 frame with a 60x60 void, drilling two 10 mm holes at (40, 0) and
    (0, 0): one valid solid, `warning=None`, both instances `engaged`, removed
    volume exactly one cylinder (785.398163397 mm^3) and the record claimed 2.
    An aggregate volume check cannot be attributed to an instance, and a
    per-instance check that only knows a bounding box is not per-instance.

    So the default tier is now three rungs, cheapest first, and only the last
    two ever conclude:

    1. the bounding-box screen (0.014 ms) — disjoint boxes is a **proved miss**,
       because a box contains its shape;
    2. `_axis_proof` (0.041 ms) — an interior point of the part on the bore
       axis is a **proved engagement**;
    3. otherwise the exact `(part & tool)` probe for **that instance alone**,
       which is the only thing that separates a seat (`flush`) from a miss.

    The statuses this produces are therefore identical to `verify="exact"`'s;
    what `exact` still buys is the per-instance `engaged_mm3` / `contact_mm2`
    numbers on every instance, which cost a boolean each. `verify="off"` opts
    out of the question entirely and is the one mode whose record counts
    intent rather than measurement.
    """
    if verify == "off":
        return [], []
    warnings = []
    if verify == "exact":
        report = engagement(
            part, [(i, tool(loc, reach)) for i, loc in enumerate(locations)],
            verify="exact")
    else:
        from OCP.BRepClass3d import BRepClass3d_SolidClassifier

        part_box = bbox_of(part)
        # Built once for the part and reused across instances: constructing it
        # costs 0.032 ms and classifying a point 0.010 ms, so per-instance
        # construction would triple the tier's cost for nothing.
        classifier = BRepClass3d_SolidClassifier(part.wrapped)
        report = []
        for i, loc in enumerate(locations):
            if not boxes_overlap(part_box,
                                 _tool_box(loc, workplane, envelope_r, reach)):
                report.append({"i": i, "status": "missed", "probe": "bbox",
                               "engaged_mm3": None})
            elif _axis_proof(classifier, loc, workplane, reach):
                report.append({"i": i, "status": "engaged", "probe": "axis",
                               "engaged_mm3": None})
            else:
                # Nothing cheap could decide this one. Pay for the boolean here
                # and nowhere else — it is what the whole tier exists to avoid
                # paying 50 times over.
                report += engagement(part, [(i, tool(loc, reach))],
                                     verify="exact")

    missed = [row["i"] for row in report if row["status"] == "missed"]
    if missed:
        warnings.append(
            f"{label}: instance(s) {missed} do not reach the part; a cut that "
            f"misses is a silent no-op in OCCT, not an error. They are NOT in "
            f"the record's count, positions or centers")
    flush = [row["i"] for row in report if row["status"] == "flush"]
    if flush:
        warnings.append(
            f"{label}: instance(s) {flush} touch the part but remove no "
            f"material (engaged volume 0). They are NOT in the record's count, "
            f"positions or centers")
    if not thru and reach >= stock - _VOLUME_TOL:
        # `>=`, not `>`. `stock` is a bounding-box extent, so it bounds every
        # bit of material along the axis: a depth that REACHES it opens on the
        # far side just as surely as one that exceeds it, and the equality case
        # is the one an author writes on purpose (`depth=t` on a `t` plate).
        # It used to pass silently and the drawing then printed a blind depth
        # callout on a through hole.
        warnings.append(
            f"{label}: depth {reach:g} mm reaches the far side of the stock "
            f"below this plane ({stock:g} mm), so the hole is not blind — it "
            f"breaks through, and a callout stating a depth would be wrong. "
            f"Use thru=True, or a depth inside the stock")
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


def _stock_sides(part, workplane) -> tuple[float, float]:
    """``(into, behind)`` — how far the part's bounding box reaches from the
    plane along ``-z_dir`` (the direction a tool conventionally travels) and
    along ``+z_dir``.

    A bounding-box measure, so both are over-estimates for anything but a
    prism: they answer "could a depth fit at all", not "does it miss a local
    pocket".
    """
    box = part.bounding_box()
    corners = [Vector(x, y, z)
               for x in (box.min.X, box.max.X)
               for y in (box.min.Y, box.max.Y)
               for z in (box.min.Z, box.max.Z)]
    normal = Vector(workplane.z_dir)
    origin = Vector(workplane.origin)
    depths = [(origin - corner).dot(normal) for corner in corners]
    return max(0.0, max(depths)), max(0.0, -min(depths))


def _tool_frame(part, workplane):
    """`workplane`, oriented so that ``-z_dir`` points at the material.

    A workplane says WHERE to drill. Which way is "into the part" is a fact
    about the part, not about the caller's frame — and a perfectly reasonable
    frame can have its normal pointing inward. The bundled `angle_bracket`
    uses exactly one: ``Plane(origin=(0, 0, hz), z_dir=(1, 0, 0))`` on a leg
    whose material lies at +X, chosen deliberately because sliding a workplane
    along the hole axis is free while the named "left" face would rotate the
    tool and re-tessellate the part.

    build123d's `Hole` does not care — a thru hole is cut in both directions —
    which is why the primary route always produced correct geometry and only
    the things *derived* from the frame were wrong: stock measured 0 mm, the
    `exact` guard probed a tool standing in fresh air and reported
    `flush, engaged 0` on a hole that had just removed real material, and
    `_bore`'s `safe_bool` fallback built a zero-height cylinder (which does not
    quietly cut nothing — it fails the boolean outright).

    Placement is deliberately NOT re-based on this frame. The points are (u, v)
    in the caller's plane, so flipping the plane they are placed on would move
    the holes; this frame is used only to measure stock, to build tools, and to
    place fused thread solids.
    """
    into, behind = _stock_sides(part, workplane)
    if behind <= into:
        return workplane
    return Plane(origin=Vector(workplane.origin),
                 x_dir=Vector(workplane.x_dir),
                 z_dir=-Vector(workplane.z_dir))


def _extent(part, workplane) -> float:
    """How much stock lies along the drilling axis from the plane.

    Measured on whichever side of the plane the material is actually on, so a
    normal that points into the material reports the stock it points at rather
    than the 0 mm of empty space behind it.
    """
    return max(_stock_sides(part, workplane))


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


def _check_diameter(diameter, label) -> float:
    try:
        value = float(diameter)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{label}: diameter must be a number of millimetres, got "
            f"{diameter!r}. For a fastener size like 'M5', use "
            f"holes.clearance or holes.tapped, which read the table.") from exc
    if not math.isfinite(value) or value <= 0:
        raise ValueError(
            f"{label}: diameter must be > 0, got {diameter!r}")
    return value


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
