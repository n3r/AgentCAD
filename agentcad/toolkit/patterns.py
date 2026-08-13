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
count alone.
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

    * `missed` — the bounding boxes are disjoint. The instance **cannot** touch
      the part: a cut there removes nothing, a fuse there leaves a floating
      solid.
    * `engaged` — bounding boxes overlap (`probe="bbox"`), or the intersection
      has volume (`probe="exact"`).
    * `flush` — `verify="exact"` only: the boxes overlap but
      `(part & tool).volume` is 0. For a cut that means nothing was removed;
      for a fuse it may still be a valid face-to-face join, which is why this
      is its own status and not a "missed".

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
        report.append({
            "i": i,
            "status": "engaged" if engaged > _VOLUME_TOL else "flush",
            "probe": "exact", "engaged_mm3": _round(engaged)})
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
    flush = _flush(report)
    if flush:
        warnings.append(
            f"{label}: instance(s) {flush} touch the part but do not "
            f"interpenetrate it (engaged volume 0) — a flush join is valid, "
            f"an accidental tangency is not")
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
    report = [{"i": 0, "status": "seed", "probe": "none", "engaged_mm3": None}]
    report += engagement(
        part, [(i + 1, piece) for i, piece in enumerate(placed)], verify=verify)

    def place():
        with Locations(*offsets):
            add(seed)

    out, fallback = _fuse(part, place, placed, "patterns.linear")

    warnings = [fallback] if fallback else []
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

    instances_ = [seed.moved(loc) for loc in placed[1:]]
    report = [{"i": 0, "status": "seed", "probe": "none", "engaged_mm3": None}]
    report += engagement(
        part, [(i + 1, piece) for i, piece in enumerate(instances_)],
        verify=verify)

    def place():
        with Locations(*placed[1:]):
            add(seed)

    out, fallback = _fuse(part, place, instances_, "patterns.polar")

    warnings = [fallback] if fallback else []
    warnings += _fuse_warnings("patterns.polar", part, out, report, count - 1,
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
