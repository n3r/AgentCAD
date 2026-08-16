"""Linear / polar / mirror patterns, point helpers, and the per-instance guard.

Two kinds of thing live here, and they are used differently.

**Point helpers** (`bolt_circle`, `grid`) are pure arithmetic — no geometry, no
OCCT, no failure mode. They feed `holes.*` (`holes.clearance(part,
patterns.bolt_circle(40, 8), "M5")`) and plain build123d
(`with Locations(*patterns.grid(3, 2, 20, 20)): ...`) equally.

**Shape patterns** (`linear`, `polar`, `mirror`) replicate a *seed solid* over
the part. They are thin wrappers over build123d's own `Locations` /
`PolarLocations` / `mirror` driven inside a `BuildPart` the helper opens itself
(design Decision 1: measured byte-identical to the hand-written form, changelog
0147). They never hand-roll a boolean on the happy path.

The seed is already part of the part
------------------------------------
`seed` is the solid you already fused in — a boss, a rib, a lug. Instance 0 *is*
that seed where it already sits, so `count` is the total number of instances the
way every CAD package counts them, `count=1` is a genuine no-op, and the helper
adds instances 1..count-1.

That holds only while some placement leaves the seed where it is, which is the
transform this module tests for — never an index. `polar(..., radius=r)`
translates *every* instance onto the circle, so with `r > 0` none of them is the
seed: all `count` are added, the seed stays where it was, and the helper says so
(changelog 0162; skipping index 0 there dropped the instance at angle 0 and left
the seed off the circle, with the right volume and no warning).

Measured (changelog 0149): the natural hand-written form
`with PolarLocations(0, n): add(seed)` re-adds the seed on top of itself at
instance 0. That coincident re-fuse is *safe* — one valid solid, the same
volume to the last bit — but it is **not** byte-free: it tessellates
differently (`85dd9044…` vs `930d1ee7…`). This module skips instance 0, which
is byte-identical to the hand-written form that also skips it, and one boolean
cheaper.

Why there is a guard at all
---------------------------
OCCT does not fail on a misplaced feature. Measured through the kernel worker
(changelog 0149): cutting a tool that lies entirely off the part takes 0.9 ms,
leaves the volume unchanged **to the last bit**, returns `is_valid True` and
raises nothing; `part & tool` on a disjoint pair is an empty `Compound` with
`.volume == 0` (never `None`, never a raise). "Never silent geometry" is
therefore not something OCCT gives us — it is something this module has to
measure, and measurement costs:

| probe, per instance | gusset_plate (12) | 50-hole plate |
|---|---|---|
| bounding-box overlap | 0.014 ms | 0.014 ms |
| `(part & tool).volume` | 2.43 ms | 2.11 ms |

against ~98 ms for the whole 50-instance boolean. So the contract is two-tier
and the tier is in the API: the bbox screen and the solid-count check are
always on and free; `verify="exact"` opts into the `&` probe and reports
`engaged_mm3` per instance. `verify="off"` skips both.

Reading the per-instance detail
-------------------------------
The helpers return `(part, warning|None)` — the `safe_*` contract. The
per-instance report rides on the returned shape and is read back with
`patterns.instances(part)`; the warning names the offending indices, never a
count alone. Every row carries the instance's `center`, and the layout
assertions (`_polar_layout_warnings`, `_linear_layout_warnings`) check those
centres against the pattern that was asked for — because a pattern can be one
valid solid, with the right added volume, every instance engaged, and every
instance in the wrong place.
"""

from __future__ import annotations

import math
from typing import Sequence

from build123d import (
    Axis,
    BuildPart,
    Location,
    Locations,
    Plane,
    PolarLocations,
    Vector,
    add,
)
from build123d import mirror as _b3d_mirror

VERIFY_MODES = ("bbox", "exact", "off")

# Coordinates that become data are rounded here, once. The sketcher's lesson
# (PRD-009): never format a coordinate for data with a display formatter, and
# never let trig noise (`cos(90 deg) = 6.1e-17`) into a stored point. 9 decimals
# is a nanometre — below every tolerance in this system.
POSITION_DECIMALS = 9

# Where the per-instance report rides. Same mechanism as the hole records
# (`holes` module): an attribute on the returned shape, because the worker's
# `_SHAPE_CACHE` hands back the very object `build(p)` returned and a
# module-level registry would drain empty on a cache hit (design Decision 4).
_REPORT_ATTR = "_agentcad_pattern_instances"

# Volume below which "nothing happened". A mm^3 is already a big number next to
# OCCT's float noise on a boolean (measured: an off-part cut moves the volume by
# exactly 0.0), so this only has to be non-zero.
_VOLUME_TOL = 1e-9

# Shared face area below which two shapes are "not welded together". Looser
# than the volume tolerance on purpose: this number is a *difference of three
# areas*, so it carries their rounding, and the thing it has to separate is a
# real seat (hundreds of mm^2 on anything a rib or a boss sits on) from an
# edge tangency (exactly 0 — an edge has no area).
_AREA_TOL = 1e-6

# Placement tolerances for the location-level assertion. The centres it
# measures are rigid images of ONE reference point (`_centre`), so the metric's
# own radius residual on a correct pattern is **exactly 0.0** — the same
# arithmetic applied to every instance. What the assertion actually reads is
# those centres after `POSITION_DECIMALS` rounding, which is what puts a floor
# under it: measured 2.9e-10 to 8.7e-10 of radius spread across counts 3, 5, 7
# and 8 of an asymmetric seed, and nothing on the partial spans. Three orders
# inside `_PLACE_TOL`, against a whole missing instance (20 mm, 90 deg) above.
#
# These numbers are meaningless applied to the wrong metric: on bounding-box
# centres of the moved shapes, a *correct* 3-up pattern of an asymmetric seed
# spreads 3.5196 mm. Widening the tolerance to cover that would have to pass
# the bug too. The metric is what makes the tolerance honest.
_PLACE_TOL = 1e-6
_ANGLE_TOL = 1e-6


def _contact_area(part, tool) -> float:
    """The face area `part` and `tool` share, in mm^2.

    Arithmetic, from the areas of the two shapes and of their fusion: a fusion
    that welds them along a face hides that face from BOTH sides, so the area
    it loses is twice the contact. Disjoint shapes lose nothing and give 0.

    This is measured rather than intersected because `part & tool` is an empty
    Compound for a face-to-face seat, an edge tangency and a shape adrift in an
    existing void alike — same volume (0), same area (0), three very different
    situations.
    """
    try:
        fused = part + tool
    except Exception:                                          # noqa: BLE001
        # An OCCT fuse that will not run tells us nothing about contact; the
        # caller's own fuse is the one that decides, and it reports separately.
        return 0.0
    shared = (float(part.area) + float(tool.area) - float(fused.area)) / 2.0
    return max(0.0, shared)


# --------------------------------------------------------------- validation

def _count(value, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a whole number, got {value!r}")
    n = int(round(value))
    if abs(value - n) > 1e-9 or n < 1:
        raise ValueError(f"{name} must be a whole number >= 1, got {value!r}")
    return n


def _positive(value, name: str) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number, got {value!r}") from exc
    if not math.isfinite(out) or out <= 0:
        raise ValueError(f"{name} must be > 0, got {value!r}")
    return out


def _check_verify(verify: str) -> str:
    if verify not in VERIFY_MODES:
        raise ValueError(
            f"verify must be one of {list(VERIFY_MODES)}, got {verify!r}")
    return verify


def _round(value: float) -> float:
    # `+ 0.0` turns a rounded -0.0 back into 0.0: a stored coordinate should
    # not carry a sign that means nothing.
    return round(float(value), POSITION_DECIMALS) + 0.0


def _round_point(point) -> tuple[float, float]:
    return (_round(point[0]), _round(point[1]))


# ------------------------------------------------------------ point helpers

def bolt_circle(r: float, n: int, start_deg: float = 0.0
                ) -> list[tuple[float, float]]:
    """`n` points evenly spaced on a circle of radius `r`, counter-clockwise
    from `start_deg` (measured from +X). Pure arithmetic.

    Feed it to `holes.*` or straight to `Locations(*points)`. Note that this is
    NOT the same construction as build123d's `PolarLocations(r, n)`: these are
    translations, `PolarLocations` also *rotates* each instance, so the two
    tessellate differently even where the geometry agrees (measured, changelog
    0149). Use `PolarLocations` (or `patterns.polar`) when the instance's
    orientation matters; use these points for holes, which are round.
    """
    r = _positive(r, "r")
    n = _count(n, "n")
    step = 360.0 / n
    return [_round_point((r * math.cos(math.radians(start_deg + step * i)),
                          r * math.sin(math.radians(start_deg + step * i))))
            for i in range(n)]


def grid(nx: int, ny: int, dx: float, dy: float, center: bool = True
         ) -> list[tuple[float, float]]:
    """`nx` x `ny` points at `dx`/`dy` spacing, row-major (x fastest).

    `center=True` centres the grid on the origin (build123d's `GridLocations`
    default); `center=False` puts the first point at the origin and grows
    towards +X/+Y.
    """
    nx, ny = _count(nx, "nx"), _count(ny, "ny")
    if nx > 1:
        dx = _positive(dx, "dx")
    if ny > 1:
        dy = _positive(dy, "dy")
    x0 = -(nx - 1) * float(dx) / 2 if center else 0.0
    y0 = -(ny - 1) * float(dy) / 2 if center else 0.0
    return [_round_point((x0 + ix * float(dx), y0 + iy * float(dy)))
            for iy in range(ny) for ix in range(nx)]


# -------------------------------------------------------------- the guard

def bbox_of(shape) -> tuple[float, float, float, float, float, float]:
    """`(xmin, ymin, zmin, xmax, ymax, zmax)` — the plain tuple the overlap
    test works on, so a caller with an analytically known envelope (a hole's
    cylinder, say) can use the same test without building geometry."""
    box = shape.bounding_box()
    return (box.min.X, box.min.Y, box.min.Z, box.max.X, box.max.Y, box.max.Z)


def boxes_overlap(a, b, tol: float = 0.0) -> bool:
    """Do two `bbox_of` tuples overlap? Grown by `tol` on each side.

    This is a **screen, not a verdict**: overlapping boxes do not prove the
    solids touch. Disjoint boxes do prove they do not, which is the direction
    that matters — a "missed" from this test is always true.
    """
    return not (a[3] + tol < b[0] or b[3] + tol < a[0]
                or a[4] + tol < b[1] or b[4] + tol < a[1]
                or a[5] + tol < b[2] or b[5] + tol < a[2])


def engagement(part, tools: Sequence[tuple[int, object]], *,
               verify: str = "bbox") -> list[dict]:
    """Per-instance engagement of `tools` (as `(index, shape)` pairs) with
    `part`. Returns `[{"i", "status", "probe", "engaged_mm3"}]`.

    Statuses:

    * `missed` — nothing to work with. Under `bbox` that means the bounding
      boxes are disjoint; under `exact` it also covers the case the boxes
      cannot see — zero interpenetration **and** zero shared face area, i.e.
      an instance floating in a void that was already cut out of the part, or
      one meeting it along a single edge. A cut there removes nothing and a
      fuse there leaves a floating solid.
    * `engaged` — bounding boxes overlap (`probe="bbox"`), or the intersection
      has volume (`probe="exact"`).
    * `flush` — `verify="exact"` only: no interpenetration, but a shared face
      of positive area — a real seat. A helper-built rib or boss lands here **by
      construction**, because it sits ON its seat plane; that is why a fuse does
      not warn about it. For a *cut* it still means nothing was removed, which
      is why `holes` does warn.

    Under `exact`, `contact_mm2` is the area the two shapes share, from the
    identity ``(part.area + tool.area - (part + tool).area) / 2``. It is the
    only thing that separates a seat from a tangency or from a drop into
    existing void: `part & tool` is an empty compound in **all three** cases
    (measured), so its volume and its area alike are 0 and it cannot tell them
    apart. The extra fuse is only run on instances that already measured zero
    interpenetration, and `exact` is opt-in precisely because it buys accuracy
    with booleans.

    Shared with `holes.*` — the guard is written once, per the plan.
    """
    verify = _check_verify(verify)
    if verify == "off":
        return [{"i": i, "status": "unchecked", "probe": "off",
                 "engaged_mm3": None} for i, _tool in tools]
    part_box = bbox_of(part)
    report = []
    for i, tool in tools:
        overlap = boxes_overlap(part_box, bbox_of(tool))
        if not overlap:
            report.append({"i": i, "status": "missed", "probe": "bbox",
                           "engaged_mm3": 0.0 if verify == "exact" else None})
            continue
        if verify != "exact":
            report.append({"i": i, "status": "engaged", "probe": "bbox",
                           "engaged_mm3": None})
            continue
        # `&`, never `Shape.intersect()` — that returns a ShapeList
        # (AGENTS.md). On a disjoint pair it is an empty Compound with
        # `.volume == 0` (measured through the worker, changelog 0149).
        engaged = float((part & tool).volume)
        if engaged > _VOLUME_TOL:
            report.append({"i": i, "status": "engaged", "probe": "exact",
                           "engaged_mm3": _round(engaged),
                           "contact_mm2": _round(_contact_area(part, tool))})
            continue
        contact = _contact_area(part, tool)
        report.append({
            "i": i,
            "status": "flush" if contact > _AREA_TOL else "missed",
            "probe": "exact", "engaged_mm3": _round(engaged),
            "contact_mm2": _round(contact)})
    return report


def spacing_conflicts(points, min_distance: float) -> list[dict]:
    """Pairs of `points` closer together than `min_distance`, as
    `[{"a", "b", "distance"}]`. Arithmetic on the point set — no geometry, so
    it costs nothing and runs whatever `verify` says.

    O(n^2). The point counts here are bolt groups, not point clouds; a pattern
    big enough for that to matter is a pattern that will not fit in a rebuild
    anyway.
    """
    pts = [tuple(float(v) for v in point) for point in points]
    out = []
    for a in range(len(pts)):
        for b in range(a + 1, len(pts)):
            gap = math.dist(pts[a], pts[b])
            if gap < min_distance:
                out.append({"a": a, "b": b, "distance": _round(gap)})
    return out


def instances(part) -> list[dict]:
    """The per-instance report of the last pattern applied to `part`.

    Empty for a part no pattern helper produced — like the hole records, the
    report rides the shape, so an operation that returns a new object drops it
    (measured: `safe_fillet`, a raw boolean and even a re-entered `BuildPart`
    all return new objects, changelog 0150).
    """
    return list(getattr(part, _REPORT_ATTR, ()))


def _attach(new_part, prior_part, report: list[dict]):
    """Attach the report and carry the hole records forward."""
    setattr(new_part, _REPORT_ATTR, list(report))
    # Imported inside the call: `holes` imports this module at module level for
    # the guard, so the record carrier can only be reached lazily from here.
    from .holes import carry

    return carry(new_part, prior_part)


def _fuse(part, place, pieces, label: str):
    """Run `place()` inside a `BuildPart` that has `add(part)`-ed the caller's
    part; on failure fall back to `safe_bool` over `pieces`. Returns
    `(part, warning|None)`.

    `place` is a callable rather than a shape list because the *primary* route
    has to be build123d's own `Locations`/`PolarLocations` block — that is the
    construction measured byte-identical to a hand-written script (design
    Decision 1), and pre-placing the copies and adding them one by one is a
    different construction with different bytes.

    `safe_bool` is the **fallback rung**: it escalates the fuzzy tolerance and
    rescues geometry the plain route cannot fuse, and the warning says plainly
    that the result may no longer be byte-identical, because it is a different
    construction. If that fails too, `safe_bool` raises — nothing is swallowed.
    """
    try:
        with BuildPart() as builder:
            add(part)
            place()
        return builder.part, None
    except Exception as exc:  # noqa: BLE001 — OCCT raises many types
        from build123d import Compound

        from .boolean import safe_bool

        out, fuzzy = safe_bool(part, Compound(children=list(pieces)), "fuse")
        detail = f" {fuzzy}" if fuzzy else ""
        return out, (
            f"{label}: the build123d builder route failed "
            f"({type(exc).__name__}: {exc}); fell back to safe_bool. The "
            f"result may not be byte-identical to the primary route.{detail}")


def _seed_reference(seed) -> Vector:
    """One point rigidly attached to the seed — its own bounding-box centre,
    measured ONCE, on the seed where it was authored."""
    box = seed.bounding_box()
    return Vector((box.min.X + box.max.X) / 2, (box.min.Y + box.max.Y) / 2,
                  (box.min.Z + box.max.Z) / 2)


def _centre(reference: Vector, placement=None) -> tuple[float, float, float]:
    """Where this instance carried the seed's reference point: the rigid image
    of `reference` under `placement` (the seed's own position when there is
    none). This is what the report's `center` means, and what the layout
    assertions measure.

    **Never the bounding box of the MOVED shape.** A bounding box is not
    rotation-invariant unless the seed has 180 degree point symmetry, so
    re-measuring it after each placement makes a *correct* polar pattern look
    broken. Measured on a right-triangular gusset boss patterned `count=3`
    about Z — one valid solid, added volume exact to 6e-11 — the moved-bbox
    centres sit 32.535898 to 36.055513 mm from the axis, a **3.5196 mm**
    spread, and at counts 5 and 8 it is 3.8507 and 3.0404. The rigid image of
    one reference point spreads **exactly 0.0** on the same patterns, and
    still puts 20 mm between the seed and the circle in the placement bug this
    exists to catch. A box or a cylinder is immune, which is why a suite built
    on those cannot see the difference.
    """
    point = reference if placement is None else (
        placement * Location(reference)).position
    return (_round(point.X), _round(point.Y), _round(point.Z))


def _mirror_centre(reference: Vector, plane) -> tuple[float, float, float]:
    """The reflection of `reference` in `plane` — the mirror's placement, done
    as arithmetic so `center` means one thing everywhere in the report."""
    normal = Vector(plane.z_dir).normalized()
    offset = (reference - Vector(plane.origin)).dot(normal)
    point = reference - normal * (2.0 * offset)
    return (_round(point.X), _round(point.Y), _round(point.Z))


def _identity_placement(locations) -> int | None:
    """The index of the placement that moves the seed nowhere, or None.

    That placement — and only that one — is the instance the seed already IS,
    so it is the only one this module may skip. `PolarLocations(0, n)` puts it
    at index 0, which is why skipping index 0 unconditionally looked right for
    as long as `radius` stayed 0; with a radius, index 0 is a translation onto
    the circle and skipping it drops a real instance while leaving the seed
    off the pattern entirely.

    The test is the transform, not the resulting position: a rotationally
    symmetric seed spun about its own axis lands its bounding box back where it
    started at EVERY placement, and skipping all of them would silently turn a
    pattern into a no-op.
    """
    for index, loc in enumerate(locations):
        pos, rot = loc.position, loc.orientation
        if (abs(pos.X) <= _PLACE_TOL and abs(pos.Y) <= _PLACE_TOL
                and abs(pos.Z) <= _PLACE_TOL and abs(rot.X) <= _ANGLE_TOL
                and abs(rot.Y) <= _ANGLE_TOL and abs(rot.Z) <= _ANGLE_TOL):
            return index
    return None


def _polar_layout_warnings(label: str, axis, plane, radius: float, span: float,
                           count: int, centres: list[tuple[float, float, float]]
                           ) -> list[str]:
    """Assert the instances landed where a polar pattern puts them.

    Two properties, both true of a correct polar pattern whatever the seed's
    own offset is, because every instance is the same rigid seed carried round
    the same circle: they are **equidistant from the axis** and **evenly spaced
    in angle**. The bug this exists for produced the right volume, one valid
    solid and `warning=None` while placing three instances on the circle and
    counting the seed — sitting 20 mm away at the centre — as the fourth, so it
    is exactly the equidistance that broke.

    `centres` must be rigid images of one reference point (`_centre`), never
    bounding-box centres of the moved shapes: on an asymmetric seed the latter
    make this function fire on geometry that is right.
    """
    if len(centres) < 2:
        return []
    origin, direction = Vector(axis.position), Vector(axis.direction).normalized()
    radii, angles = [], []
    for centre in centres:
        rel = Vector(*centre) - origin
        planar = rel - direction * rel.dot(direction)
        radii.append(planar.length)
        angles.append(math.degrees(math.atan2(planar.dot(plane.y_dir),
                                              planar.dot(plane.x_dir))) % 360.0)
    out = []
    spread = max(radii) - min(radii)
    if spread > _PLACE_TOL:
        out.append(
            f"{label}: the instances are not all the same distance from the "
            f"axis ({min(radii):.6g} to {max(radii):.6g} mm) — a polar pattern "
            f"carries one seed round one circle, so this is a placement bug, "
            f"not a tolerance")
    step = span / count if span >= 360.0 - _PLACE_TOL else span / (count - 1)
    if min(radii) > _PLACE_TOL:
        gaps = sorted((b - a) % 360.0 for a, b in zip(sorted(angles),
                                                      sorted(angles)[1:]))
        if gaps and abs(gaps[0] - step) > _ANGLE_TOL:
            out.append(
                f"{label}: the instances are not evenly spaced — the closest "
                f"pair is {gaps[0]:.6g} deg apart where {step:.6g} deg was "
                f"requested ({count} instance(s) over {span:g} deg)")
    if radius > _PLACE_TOL and abs(min(radii) - radius) > _PLACE_TOL:
        out.append(
            f"{label}: the instances sit {min(radii):.6g} mm from the axis, "
            f"not the {radius:g} mm requested — `radius` places a seed that is "
            f"authored at the axis, so a seed already offset from it is "
            f"carried round a larger circle")
    return out


def _linear_layout_warnings(label: str, unit: Vector, spacing: float,
                            centres: list[tuple[float, float, float]]
                            ) -> list[str]:
    """The same assertion for `linear`: consecutive instances must be exactly
    `spacing` apart along `direction`, and must not have drifted across it."""
    if len(centres) < 2:
        return []
    along = [Vector(*c).dot(unit) for c in centres]
    across = [(Vector(*c) - unit * Vector(*c).dot(unit)) for c in centres]
    out = []
    steps = [b - a for a, b in zip(along, along[1:])]
    worst = max(abs(step - spacing) for step in steps)
    if worst > _PLACE_TOL:
        out.append(
            f"{label}: consecutive instances are not {spacing:g} mm apart "
            f"along the direction (worst error {worst:.6g} mm) — a placement "
            f"bug, not a tolerance")
    drift = max((c - across[0]).length for c in across)
    if drift > _PLACE_TOL:
        out.append(
            f"{label}: the instances drift {drift:.6g} mm off the pattern "
            f"direction; they are not on one line")
    return out


def _missed(report: list[dict]) -> list[int]:
    return [row["i"] for row in report if row["status"] == "missed"]


def _flush(report: list[dict]) -> list[int]:
    return [row["i"] for row in report if row["status"] == "flush"]


def _join(warnings: list[str]) -> str | None:
    return "; ".join(warnings) if warnings else None


def _fuse_warnings(label: str, part, out, report: list[dict],
                   n_instances: int, seed=None) -> list[str]:
    """The warnings every fuse-shaped pattern shares."""
    warnings = []
    missed = _missed(report)
    if missed:
        warnings.append(
            f"{label}: instance(s) {missed} do not reach the part "
            f"(bounding-box probe); a fused instance that touches nothing "
            f"leaves a floating solid")
    # `flush` is NOT warned about here, and that is the point. A fused
    # instance is a rib, a boss or a shape sitting on its seat plane, so zero
    # interpenetration is what correct construction looks like — warning on it
    # meant the strong tier fired on every happy path, which is how a reader
    # learns to ignore it. `flush` now carries a positive shared face area, so
    # the cases that used to hide inside it (an edge tangency, an instance in
    # existing void) come back as `missed` above and are reported there.
    solids = len(out.solids())
    if solids > 1:
        warnings.append(
            f"{label}: the result is {solids} disjoint solids, not one — "
            f"instance(s) {missed or 'unknown'} did not connect")
    added = out.volume - part.volume
    if added <= _VOLUME_TOL and n_instances:
        warnings.append(
            f"{label}: the pattern added no material (volume delta "
            f"{added:.6g} mm^3); every instance landed inside existing "
            f"material or off the part")
    elif seed is not None and n_instances:
        # What the instances contain vs what arrived. Cheap (two volumes) and
        # it is the only check that sees instances overlapping EACH OTHER,
        # which no per-instance probe against the part can.
        expected = seed.volume * n_instances
        if added < expected - max(1e-6, expected * 1e-6):
            warnings.append(
                f"{label}: the {n_instances} added instance(s) contain "
                f"{expected:.6g} mm^3 but only {added:.6g} mm^3 arrived — they "
                f"overlap each other or existing material")
    return warnings


# ------------------------------------------------------------------ patterns

def linear(part, seed, direction, count: int, spacing: float, *,
           verify: str = "bbox"):
    """`count` copies of `seed` along `direction` at `spacing`, fused.

    Instance 0 is the seed where it already sits in `part`, so this adds
    `count - 1` instances and `count=1` is a no-op. Returns
    `(part, warning|None)`; read `patterns.instances(part)` for the per-instance
    detail.

    `direction` is any 3-vector (a tuple, a `Vector`, or an `Axis`, whose
    direction is used); it is normalised, so its length is not the spacing.
    Degenerate arguments — `count < 1`, `spacing <= 0`, a zero-length direction
    — raise `ValueError` at the call. An impossible request is not geometry.
    """
    verify = _check_verify(verify)
    count = _count(count, "count")
    if count > 1:
        spacing = _positive(spacing, "spacing")
    unit = _unit(direction, "direction")

    if count == 1:
        return part, (
            "patterns.linear: count=1 places only the seed, which is already "
            "where you put it; the part is unchanged")

    offsets = [Location(unit * (i * float(spacing))) for i in range(1, count)]
    placed = [seed.moved(loc) for loc in offsets]
    reference = _seed_reference(seed)
    report = [{"i": 0, "status": "seed", "probe": "none", "engaged_mm3": None,
               "center": _centre(reference)}]
    rows = engagement(
        part, [(i + 1, piece) for i, piece in enumerate(placed)], verify=verify)
    for row, offset in zip(rows, offsets):
        row["center"] = _centre(reference, offset)
    report += rows

    def place():
        with Locations(*offsets):
            add(seed)

    out, fallback = _fuse(part, place, placed, "patterns.linear")

    warnings = [fallback] if fallback else []
    warnings += _linear_layout_warnings("patterns.linear", unit, float(spacing),
                                        [row["center"] for row in report])
    warnings += _spacing_warning("patterns.linear", seed, unit, spacing, count)
    warnings += _fuse_warnings("patterns.linear", part, out, report, count - 1,
                               seed)
    return _attach(out, part, report), _join(warnings)


def _spacing_warning(label: str, seed, unit: Vector, spacing: float,
                     count: int) -> list[str]:
    """Degenerate spacing, named by the pair it affects. Arithmetic on the
    seed's extent along the pattern direction — no geometry, so it runs
    whatever `verify` says, and it is the one check that catches instances
    landing on top of each other before OCCT is asked to fuse them."""
    box = seed.bounding_box()
    span = [Vector(x, y, z).dot(unit)
            for x in (box.min.X, box.max.X)
            for y in (box.min.Y, box.max.Y)
            for z in (box.min.Z, box.max.Z)]
    extent = max(span) - min(span)
    if float(spacing) >= extent - 1e-9:
        return []
    pairs = ", ".join(f"{i}&{i + 1}" for i in range(count - 1))
    return [f"{label}: spacing {float(spacing):g} mm is less than the seed's "
            f"{extent:g} mm extent along the direction, so instance(s) "
            f"{pairs} overlap"]


def polar(part, seed, axis=Axis.Z, count: int = 4, radius: float | None = None,
          span_deg: float = 360.0, *, verify: str = "bbox"):
    """`count` copies of `seed` around `axis`, fused.

    Instance 0 is the seed where it already sits. `radius=None` is the true polar
    pattern — each instance is the seed *rotated* about the axis, nothing else
    (build123d's `PolarLocations(0, count)`). Passing a `radius` additionally
    places the instances on a circle of that radius, which is the bolt-circle
    form; for round holes prefer `holes.*` over `patterns.bolt_circle(...)`
    points, which are cheaper and carry a record.

    `span_deg=360` spaces `count` instances over the full turn (the last one
    does not land on the first). A partial span is **inclusive**: 4 instances
    over 180 deg sit at 0/60/120/180, which is what a CAD user means by "4 over
    180". `span_deg` outside `(0, 360]` raises: a span that wraps onto itself
    would stack instances on each other.
    """
    verify = _check_verify(verify)
    count = _count(count, "count")
    span = float(span_deg)
    if not math.isfinite(span) or span <= 0 or span > 360.0:
        raise ValueError(
            f"span_deg must be in (0, 360], got {span_deg!r}; a larger span "
            f"wraps instances onto each other")
    radius_mm = 0.0 if radius is None else float(radius)
    if radius_mm < 0:
        raise ValueError(f"radius must be >= 0, got {radius!r}")
    axis = _axis(axis, "axis")

    if count == 1:
        return part, (
            "patterns.polar: count=1 places only the seed, which is already "
            "where you put it; the part is unchanged")

    endpoint = span < 360.0
    plane = Plane(origin=axis.position, z_dir=axis.direction)
    with PolarLocations(radius_mm, count, 0.0, span, True, endpoint) as ctx:
        local = list(ctx.locations)
    placed = [plane.location * loc * plane.location.inverse() for loc in local]

    # Which placement is the seed's own? The one that moves it nowhere — and
    # with a radius there is none, because every placement translates onto the
    # circle while the seed sits wherever it was authored. Skipping index 0
    # regardless dropped the instance at angle 0 and counted the seed in its
    # place: measured on a centre boss with `count=4, radius=20`, that returned
    # `warning=None`, one valid solid and the *expected* added volume with the
    # instances at (-20,0), (0,-20), (0,0), (0,20) — the (+20,0) one missing.
    seed_at = _identity_placement(placed)
    kept = [i for i in range(count) if i != seed_at]
    instances_ = [seed.moved(placed[i]) for i in kept]
    reference = _seed_reference(seed)

    report = ([] if seed_at is None else
              [{"i": seed_at, "status": "seed", "probe": "none",
                "engaged_mm3": None, "center": _centre(reference)}])
    rows = engagement(part, list(zip(kept, instances_)), verify=verify)
    for row, index in zip(rows, kept):
        row["center"] = _centre(reference, placed[index])
    report = sorted(report + rows, key=lambda row: row["i"])

    def place():
        with Locations(*[placed[i] for i in kept]):
            add(seed)

    out, fallback = _fuse(part, place, instances_, "patterns.polar")

    warnings = [fallback] if fallback else []
    if seed_at is None:
        warnings.append(
            f"patterns.polar: radius={radius_mm:g} translates every instance "
            f"onto the circle, so none of them is where the seed already sits: "
            f"all {count} were ADDED and the seed is still where you built it, "
            f"an extra feature this pattern does not count. For exactly "
            f"{count} on the circle, build the seed WHERE THE FIRST INSTANCE "
            f"GOES and call polar(...) with radius=None — that rotates it "
            f"about the axis and the seed IS instance 0. There is no argument "
            f"to this helper that removes a seed it was handed, so `radius` "
            f"always leaves one over; it is for a seed authored at the axis, "
            f"where that leftover is deliberate (a hub with its bolt circle)")
    warnings += _polar_layout_warnings("patterns.polar", axis, plane,
                                       radius_mm, span, count,
                                       [row["center"] for row in report])
    warnings += _fuse_warnings("patterns.polar", part, out, report, len(kept),
                               seed)
    return _attach(out, part, report), _join(warnings)


def mirror(part, plane=Plane.YZ, *, seed=None, verify: str = "bbox"):
    """Mirror `part` (or just `seed`) about `plane` and fuse the result.

    `plane` is a build123d `Plane` or one of the names `"XY" | "XZ" | "YZ"`.
    Returns `(part, warning|None)`; there is one instance to report on, so
    `patterns.instances(part)` has a single entry — index 0 is the mirrored
    copy, not a seed. Mirroring a part that straddles the plane
    warns: the copy lands on top of the original and the union is not what the
    caller asked for. Mirroring a part that is already symmetric about the
    plane adds nothing, and warns, because that is almost always a wrong plane
    rather than a deliberate no-op.
    """
    verify = _check_verify(verify)
    plane = _plane(plane, "plane")
    source = part if seed is None else seed
    image = _b3d_mirror(source, about=plane)

    report = engagement(part, [(0, image)], verify=verify)
    reference = _seed_reference(source)
    for row in report:
        row["center"] = _mirror_centre(reference, plane)

    def place():
        add(image)

    out, fallback = _fuse(part, place, [image], "patterns.mirror")

    warnings = [fallback] if fallback else []
    added = out.volume - part.volume
    if added <= _VOLUME_TOL:
        warnings.append(
            f"patterns.mirror: the mirrored copy added no material (volume "
            f"delta {added:.6g} mm^3) — the {'part' if seed is None else 'seed'}"
            f" is already symmetric about this plane")
    elif seed is None and added < source.volume - _VOLUME_TOL:
        warnings.append(
            f"patterns.mirror: the mirrored copy overlaps the original by "
            f"{source.volume - added:.6g} mm^3 — the part straddles the mirror "
            f"plane, so the union is not two copies of it")
    if len(out.solids()) > 1:
        warnings.append(
            f"patterns.mirror: the result is {len(out.solids())} disjoint "
            f"solids, not one — the mirrored copy does not touch the original")
    return _attach(out, part, report), _join(warnings)


# --------------------------------------------------------- argument coercion

def _unit(direction, name: str) -> Vector:
    if isinstance(direction, Axis):
        vec = Vector(direction.direction)
    else:
        try:
            vec = Vector(direction)
        except Exception as exc:  # noqa: BLE001 — build123d raises many types
            raise ValueError(
                f"{name} must be a 3-vector or an Axis, got "
                f"{direction!r}") from exc
    if vec.length <= 1e-12:
        raise ValueError(f"{name} has zero length, got {direction!r}")
    return vec.normalized()


def _axis(axis, name: str) -> Axis:
    if isinstance(axis, Axis):
        return axis
    try:
        return Axis((0, 0, 0), Vector(axis))
    except Exception as exc:  # noqa: BLE001
        raise ValueError(
            f"{name} must be an Axis or a 3-vector, got {axis!r}") from exc


_NAMED_PLANES = {"XY": Plane.XY, "XZ": Plane.XZ, "YZ": Plane.YZ}


def _plane(plane, name: str) -> Plane:
    if isinstance(plane, Plane):
        return plane
    if isinstance(plane, str) and plane.upper() in _NAMED_PLANES:
        return _NAMED_PLANES[plane.upper()]
    raise ValueError(
        f"{name} must be a Plane or one of {sorted(_NAMED_PLANES)}, got "
        f"{plane!r}")
