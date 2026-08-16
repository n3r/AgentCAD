"""Ribs, bosses and draft — the three shape features under the `safe_*`
honest-warning contract.

    from agentcad.toolkit import features

    part, warn = features.rib(part, [(-40, 0), (40, 0)], 3.0, to=8.0)
    part, warn = features.boss(part, (20, 0), 8.0, 6.0, hole="M3")
    part, achieved, warn = features.draft(part, sides, 5.0, Plane.XY)

Every helper returns the new part first and a warning string (or `None`) last,
and every helper **carries the hole records forward** (`holes.carry`) — a
boolean returns a brand-new object with none of the original's attributes, so a
feature that forgot would silently drop the metadata of every hole drilled
before it.

Which way is "out"
------------------
`plane` is resolved by `holes.resolve_plane`, so the six names mean exactly
what they mean for holes and the normal points **out of the material**. Holes
drill along `-z_dir` (into the part); ribs and bosses grow along `+z_dir` (away
from the seat). For a feature inside a cavity — a rib on an enclosure floor —
pass an explicit `Plane(origin=(0, 0, floor_t), z_dir=(0, 0, 1))`: a named
plane can only ever resolve to an *outer* face.

What was measured (changelog 0155)
----------------------------------
There is no rib operation in OCCT or build123d, so a rib is a construction and
the trim step is the part that decides the answer. Both trims of design
Decision 7 were measured through the kernel worker, on a plain plate and on
`prototyping/enclosure_base`'s cavity floor:

| trim | plate 100x60x5 | `enclosure_base` floor |
|---|---|---|
| `to=<mm>` (extrude to a stated height) | delta 1920.0 mm^3 = hand-built **exactly**, 1 solid, valid, 6.3 ms | delta 960.0 mm^3, hand-built - 4.4e-11, 1 solid, valid, 54 ms |
| `to="part"` (extrude generously, `&` the envelope) | **delta 0.0 mm^3** — the envelope of a convex part *is* the part | delta 3300 mm^3: the rib runs to the top of the bounding box, not to the wall it meets |

So `to=<mm>` is the default and `to="part"` always warns: it trims to the
part's bounding solid, which is an envelope, not a wall. Its no-op case on a
convex part is caught by the volume-delta check, not by OCCT.

And the reason the guard is not optional: a rib fused 25 mm **above** the part
raised nothing, reported `is_valid True`, and increased the volume by the rib's
full 960 mm^3 — a delta that looks exactly like success. Two things tell them
apart, and both are measured here: the result has 2 disjoint solids, and
`(part & rib).volume` is 0.
"""

from __future__ import annotations

import math

from build123d import (
    Align,
    Box,
    BuildLine,
    BuildPart,
    BuildSketch,
    Cone,
    Cylinder,
    Face,
    Location,
    Plane,
    Polyline,
    Pos,
    Vector,
    add,
    extrude,
    trace,
)
from build123d import draft as _b3d_draft

from . import holes
from .patterns import _fuse, _fuse_warnings, _join, _positive, engagement

_VOLUME_TOL = 1e-9


# ------------------------------------------------------------------- ribs

def rib(part, profile, thickness: float, *, to, plane="top",
        draft_deg: float | None = None, verify: str = "bbox"):
    """A rib standing on `plane`: the `profile` polyline traced to `thickness`,
    extruded away from the seat, and fused. Returns `(part, warning|None)`.

    `profile` is a sequence of `(u, v)` points in the plane's coordinates — two
    points make a straight rib, more make a folded one (measured: a two-segment
    L profile is one valid solid whose volume is the sum of its legs).

    `to` is the trim, and it is the whole design question:

    * `to=<mm>` — the rib's height above the seat. Dumb and exact: measured
      equal to a hand-built rib to the last bit.
    * `to="part"` — extrude generously and intersect the part's **bounding
      solid**. Robust, but that envelope is not the part: on a convex part the
      rib lands entirely inside existing material and adds nothing (measured
      0.0 mm^3 on a plate), and on a shelled part it runs to the top of the
      bounding box rather than to the wall it meets. This mode therefore always
      warns, and the guard's volume-delta check is what catches the no-op.

    `draft_deg` tapers the extrusion (build123d's `extrude(taper=)`) rather
    than calling the draft operation on the finished part — design Decision 7,
    and the measurement behind it: a shelled enclosure refuses draft above
    0.25 deg, so drafting a finished shelled part would fail where a tapered
    extrusion cannot. It needs a stated height, so it is not available with
    `to="part"`.
    """
    thickness = _positive(thickness, "thickness")
    points = _check_profile(profile)
    seat = holes.resolve_plane(part, plane)
    mode, height = _check_to(to)
    label = f"features.rib[to={'part' if mode == 'part' else f'{height:g}'}]"
    warnings = []

    if mode == "part":
        if draft_deg is not None:
            raise ValueError(
                "features.rib: draft_deg needs a stated height — a tapered "
                "extrusion has to know how far it tapers. Use to=<mm>.")
        reach = float(part.bounding_box().size.length) + height
        tall = _rib_solid(points, seat, thickness, 2 * reach, start=-reach)
        solid = tall & _envelope(part)
        warnings.append(
            f"{label}: trimmed to the part's bounding solid, which is an "
            f"envelope and not the part — the rib stops at the bounding box, "
            f"not at the wall it meets. Pass to=<mm> for an exact height.")
        if solid is None or solid.volume <= _VOLUME_TOL:
            warnings.append(
                f"{label}: the trim removed the whole rib — the profile lies "
                f"outside the part's bounding solid, so there is nothing to "
                f"fuse; the part is unchanged")
            return holes.carry(part, part), _join(warnings)
    else:
        taper = _check_draft(draft_deg, thickness, height)
        solid = _rib_solid(points, seat, thickness, height, taper=taper)

    report = engagement(part, [(0, solid)], verify=verify)
    out, fallback = _fuse(part, lambda: add(solid), [solid], label)
    if fallback:
        warnings.append(fallback)
    # `seed=solid` is what buys the second check: what the instance
    # CONTAINS against what actually arrived. Without it a rib or a boss
    # that lands half inside existing material fuses in silence — the
    # per-instance probe cannot see it, because the instance genuinely
    # does engage the part; only the volume knows how little of it is new.
    warnings += _fuse_warnings(label, part, out, report, 1, seed=solid)
    warnings += _floating_warning(label, part, out, solid)
    return holes.carry(out, part), _join(warnings)


def _floating_warning(label: str, part, out, solid) -> list[str]:
    """When the fuse left more solids than it started with, pay for the exact
    probe and say how much material the feature actually engages.

    The `&` probe costs ~2 ms (changelog 0149) and is skipped on the happy
    path, but on this path the caller already has a problem and "0 mm^3" is the
    fact that names it: OCCT fused a floating solid without complaint and the
    volume delta looks exactly like success.
    """
    if len(out.solids()) <= len(part.solids()):
        return []
    engaged = float((part & solid).volume)
    return [f"{label}: the feature engages {engaged:.6g} mm^3 of the part "
            f"(exact probe) — it was fused as a separate solid. OCCT does not "
            f"fail on a feature that touches nothing, and the volume delta "
            f"looks like success"]


def _check_profile(profile) -> list[tuple[float, float]]:
    try:
        points = [(float(point[0]), float(point[1])) for point in profile]
    except (TypeError, ValueError, IndexError) as exc:
        raise ValueError(
            f"features.rib: profile must be a sequence of (u, v) points in the "
            f"plane's coordinates, got {profile!r}") from exc
    if len(points) < 2:
        raise ValueError(
            f"features.rib: profile needs at least two points to trace a rib, "
            f"got {len(points)}")
    return points


def _check_to(to) -> tuple[str, float]:
    if isinstance(to, str):
        if to != "part":
            raise ValueError(
                f"to must be a height in millimetres or the string 'part', "
                f"got {to!r}")
        return "part", 0.0
    return "depth", _positive(to, "to")


def _check_draft(draft_deg, thickness: float, height: float) -> float:
    """A taper that consumes the profile is not geometry — say so at the call
    rather than letting `extrude` raise something OCCT-shaped."""
    if draft_deg is None:
        return 0.0
    angle = float(draft_deg)
    if not math.isfinite(angle) or not -90.0 < angle < 90.0:
        raise ValueError(
            f"draft_deg must be in (-90, 90) degrees, got {draft_deg!r}")
    half_at_top = thickness / 2.0 - height * math.tan(math.radians(angle))
    if angle > 0 and half_at_top <= 0:
        raise ValueError(
            f"draft_deg {angle:g} deg over {height:g} mm consumes the rib's "
            f"{thickness:g} mm thickness before the top; reduce draft_deg, the "
            f"height, or thicken the rib")
    return angle


def _rib_solid(points, seat: Plane, thickness: float, height: float, *,
               taper: float = 0.0, start: float = 0.0):
    """The rib body: the profile traced to `thickness` on `seat` (offset by
    `start` along the seat normal) and extruded `height` along it."""
    base = Plane(origin=Vector(seat.origin) + Vector(seat.z_dir) * start,
                 x_dir=Vector(seat.x_dir), z_dir=Vector(seat.z_dir))
    with BuildPart() as builder:
        with BuildSketch(base):
            with BuildLine():
                Polyline(*points)
            trace(line_width=thickness)
        extrude(amount=height, taper=taper)
    return builder.part


def _envelope(part):
    """The "material envelope" of design Decision 7(a): the part's own bounding
    box as a solid. Named, because what it is *not* is the part."""
    box = part.bounding_box()
    size = box.size
    return Pos(*box.center()) * Box(size.X, size.Y, size.Z)


# ------------------------------------------------------------------ bosses

def boss(part, at, d: float, h: float, *, hole: str | None = None,
         hole_depth: float | None = None, plane="top",
         draft_deg: float | None = None, std: str = "iso",
         verify: str = "bbox"):
    """A cylindrical boss of diameter `d` standing `h` above `plane` at `at`,
    fused. Returns `(part, warning|None)`.

    The conventions, which are the only real content of a boss:

    * the **bearing face is the seat** — `h` is measured from the plane the
      boss stands on, not from the origin;
    * `draft_deg` tapers it *inwards* going up (a moulding draft), so the top
      diameter is `d - 2*h*tan(draft_deg)`; a draft that closes the top raises
      rather than building a cone with a point;
    * `hole="M3"` bores the **tap drill** through the boss with
      `holes.tapped`, so the screw boss carries a record and reaches the
      drawing callouts. It is blind at the seat by default (`hole_depth`
      defaults to `h`); state `hole_depth` to go further.

    The hole's `thread_class` is deliberately not passed on: `holes.tapped`
    defaults it per standard, and a class named here would be a number invented
    by a boss helper.
    """
    d = _positive(d, "d")
    h = _positive(h, "h")
    seat = holes.resolve_plane(part, plane)
    u, v = _check_at(at)
    solid = _boss_solid(seat, u, v, d, h, draft_deg)
    label = "features.boss"

    report = engagement(part, [(0, solid)], verify=verify)
    out, fallback = _fuse(part, lambda: add(solid), [solid], label)
    warnings = [fallback] if fallback else []
    # `seed=solid` is what buys the second check: what the instance
    # CONTAINS against what actually arrived. Without it a rib or a boss
    # that lands half inside existing material fuses in silence — the
    # per-instance probe cannot see it, because the instance genuinely
    # does engage the part; only the volume knows how little of it is new.
    warnings += _fuse_warnings(label, part, out, report, 1, seed=solid)
    warnings += _floating_warning(label, part, out, solid)
    out = holes.carry(out, part)

    if hole is not None:
        top = Plane(origin=Vector(seat.origin) + Vector(seat.z_dir) * h,
                    x_dir=Vector(seat.x_dir), z_dir=Vector(seat.z_dir))
        out, _records, hole_warning = holes.tapped(
            out, [(u, v)], hole, plane=top,
            depth=h if hole_depth is None else float(hole_depth), std=std,
            verify=verify)
        if hole_warning:
            warnings.append(hole_warning)
    return out, _join(warnings)


def _check_at(at) -> tuple[float, float]:
    try:
        return (float(at[0]), float(at[1]))
    except (TypeError, ValueError, IndexError) as exc:
        raise ValueError(
            f"features.boss: at must be a (u, v) point in the plane's "
            f"coordinates, got {at!r}") from exc


def _boss_solid(seat: Plane, u: float, v: float, d: float, h: float,
                draft_deg: float | None):
    align = (Align.CENTER, Align.CENTER, Align.MIN)
    radius = d / 2.0
    at = seat.location * Location(Vector(u, v, 0.0))
    if draft_deg is None:
        return at * Cylinder(radius, h, align=align)
    angle = float(draft_deg)
    if not math.isfinite(angle) or not -90.0 < angle < 90.0:
        raise ValueError(
            f"draft_deg must be in (-90, 90) degrees, got {draft_deg!r}")
    top = radius - h * math.tan(math.radians(angle))
    if top <= 0:
        raise ValueError(
            f"draft_deg {angle:g} deg closes a {d:g} mm boss before its "
            f"{h:g} mm top (top diameter would be {2 * top:g} mm); reduce "
            f"draft_deg or the height")
    return at * Cone(bottom_radius=radius, top_radius=top, height=h,
                     align=align)


# ------------------------------------------------------------------- draft

#: The measured ceilings, kept next to the code that reports them. Every row is
#: the largest angle that produced a **valid** solid in the slice-10 sweep
#: (0.25 -> 60 deg, through the kernel worker, changelog 0156).
DRAFT_CEILINGS = {
    "box 40x30x20 (4 side faces)": 35.0,
    "box + R4 vertical fillets (8 faces)": 10.0,
    "box + boss (5 faces)": 15.0,
    "shelled box t=2 (8 faces)": 2.5,
    "construction/gusset_plate (18 faces)": 17.5,
    "prototyping/enclosure_base (56 faces)": 0.25,
    "rocketry/nozzle (2 faces)": None,          # fails at every angle
    "construction/angle_bracket (7 faces)": None,
}


@holes.carries_records  # a drafted part keeps its hole records
def draft(part, faces, angle_deg: float, neutral_plane, *,
          min_angle: float = 0.25, rel_tol: float = 0.02):
    """Draft `faces` by `angle_deg` about `neutral_plane`; on failure binary-
    search **down** to the largest angle that works. Returns
    `(part, achieved_deg, warning|None)` — the `safe_fillet` contract.

    `faces` is a list of `Face` objects or a **selector callable**
    `f(part) -> faces`, never indices: a draft that names eight ordinals is
    wrong the first time a fillet is added (design Decision 3).

    Two measurements decide everything about this helper (changelog 0156,
    swept 0.25 -> 60 deg through the kernel worker on four synthetic shapes and
    four bundled example parts):

    1. **Failure is monotone in the angle on all eight** — a clean
       `ok…ok fail…fail` boundary with no islands — which is exactly the
       precondition a binary search needs.
    2. **The dominant failure is silent.** Only the extreme angles raise
       (`Standard_Failure` with an *empty* message, or build123d's
       `DraftAngleError`); most failing angles come back as a *returned* shape
       with `is_valid False` and a plausible positive volume — a shelled box
       at 1 deg measured 32421 mm^3, invalid. So every attempt is validated,
       not merely tried.

    The practical ceilings, and they are lower than the textbook advice
    (see `DRAFT_CEILINGS`): a plain box takes 35 deg, a filleted box 10, a
    **shelled enclosure 0.25**, and two of the four bundled parts refuse every
    angle down to 0.25. The fallback is the feature, not a consolation — but
    when nothing works at all this returns the part **unchanged** with a
    warning naming the failing angle and what OCCT said, rather than an
    undrafted part that looks drafted.
    """
    angle = _check_angle(angle_deg, "angle_deg")
    floor = _check_angle(min_angle, "min_angle")
    if not isinstance(neutral_plane, Plane):
        raise ValueError(
            f"neutral_plane must be a build123d Plane, got {neutral_plane!r}")
    faces = _check_faces(part, faces)
    label = "features.draft"
    if not faces:
        return part, 0.0, (
            f"{label}: no faces selected, so there is nothing to draft; the "
            f"part is unchanged")

    def attempt(value: float):
        """`(shape|None, reason)` — the reason is what goes in the warning,
        because OCCT will not supply one."""
        try:
            out = _b3d_draft(faces, neutral_plane=neutral_plane, angle=value)
        except Exception as exc:  # noqa: BLE001 — OCCT raises many types
            message = str(exc).strip()
            if message:
                return None, f"raised {type(exc).__name__}: {message}"
            return None, (
                f"raised {type(exc).__name__} with no message — OCCT reports "
                f"nothing about why a draft fails")
        if not out.is_valid:
            return None, (
                f"returned an invalid solid ({out.volume:.6g} mm^3, "
                f"is_valid False) without raising")
        if out.volume <= 0:
            return None, "returned an empty solid without raising"
        return out, None

    result, reason = attempt(angle)
    if result is not None:
        return result, angle, None

    if floor >= angle:
        return part, 0.0, (
            f"{label}: {angle:.3f} deg failed on {len(faces)} face(s) and "
            f"min_angle {floor:.3f} deg is not below it, so there is nothing "
            f"to search; the part is unchanged. The attempt {reason}.")

    lo_part, lo_reason = attempt(floor)
    if lo_part is None:
        return part, 0.0, (
            f"{label}: draft failed at every angle from {angle:.3f} deg down "
            f"to the {floor:.3f} deg minimum on {len(faces)} face(s); the part "
            f"is unchanged. The last attempt {lo_reason}. Draft this geometry "
            f"before shelling or filleting it, or draft fewer faces.")

    best, best_part, lo, hi = floor, lo_part, floor, angle
    while hi - lo > max(rel_tol * angle, 1e-3):
        mid = 0.5 * (lo + hi)
        mid_part, _mid_reason = attempt(mid)
        if mid_part is not None:
            best, best_part, lo = mid, mid_part, mid
        else:
            hi = mid
    return best_part, best, (
        f"{label}: requested {angle:.3f} deg on {len(faces)} face(s) failed "
        f"(the attempt {reason}); applied the largest angle that produced a "
        f"valid solid, {best:.3f} deg. Draft failure is monotone in the angle "
        f"(measured), so nothing between {best:.3f} and {angle:.3f} deg works "
        f"on this geometry.")


def _check_angle(value, name: str) -> float:
    try:
        angle = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number of degrees, got "
                         f"{value!r}") from exc
    if not math.isfinite(angle) or not 0.0 < angle < 90.0:
        raise ValueError(
            f"{name} must be in (0, 90) degrees, got {value!r}")
    return angle


def _check_faces(part, faces) -> list:
    """A selector or a list of `Face`s. Indices are refused by name: face
    ordinals renumber on any topology change (AGENTS.md), so a drafted face set
    written as `[0, 2, 4]` is wrong the first time an edge is filleted."""
    if callable(faces):
        faces = faces(part)
    try:
        resolved = list(faces)
    except TypeError as exc:
        raise ValueError(
            f"faces must be a list of build123d Face objects or a selector "
            f"callable f(part) -> faces, got {faces!r}") from exc
    bad = [item for item in resolved if not isinstance(item, Face)]
    if bad:
        raise ValueError(
            f"faces must contain build123d Face objects, got {bad[0]!r}. Face "
            f"indices are not accepted: ordinals renumber on any topology "
            f"change. Select them, e.g. "
            f"part.faces().filter_by(lambda f: abs(f.normal_at().Z) < 1e-6).")
    return resolved
