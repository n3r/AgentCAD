"""PRD-010 slice 3 — `toolkit.patterns`: points, shape patterns, the guard.

The guard is the reason this module is not a one-liner. Measured through the
kernel worker (changelog 0149), OCCT does **not** fail on a misplaced feature:
cutting a tool that lies entirely off the part takes 0.9 ms, leaves the volume
unchanged to the last bit and reports `is_valid True`. So every "this instance
did nothing" fact in here is one the helper had to measure, and these tests
assert that it did.
"""

import math

import pytest


def _plate(w=120.0, d=120.0, t=10.0):
    from build123d import Box

    return Box(w, d, t)


def _seeded_plate():
    """A plate with one boss already fused, plus the boss solid on its own.

    That is the module's convention: the seed is already in the part, instance
    0 *is* that seed, and the helper adds the rest — which is why `count=1` is
    a genuine no-op.
    """
    from build123d import Align, BuildPart, Box, Cylinder, Locations, Pos

    with BuildPart() as builder:
        Box(120, 120, 10)
        with Locations((40, 0, 5)):
            Cylinder(6, 8, align=(Align.CENTER, Align.CENTER, Align.MIN))
    boss = Pos(40, 0, 5) * Cylinder(
        6, 8, align=(Align.CENTER, Align.CENTER, Align.MIN))
    return builder.part, boss


# ------------------------------------------------------------ point helpers

def test_bolt_circle_is_deterministic_and_counter_clockwise():
    from agentcad.toolkit import patterns

    points = patterns.bolt_circle(40, 4)
    assert points == [(40.0, 0.0), (0.0, 40.0), (-40.0, 0.0), (0.0, -40.0)]
    # trig noise never reaches a stored coordinate: cos(90 deg) is 6.1e-17 in
    # IEEE754 and 0.0 here.
    assert points == patterns.bolt_circle(40, 4)


def test_bolt_circle_start_angle_and_spacing():
    from agentcad.toolkit import patterns

    points = patterns.bolt_circle(10, 6, start_deg=30)
    assert len(points) == 6
    assert points[0] == pytest.approx((10 * math.cos(math.radians(30)),
                                       10 * math.sin(math.radians(30))))
    radii = {round(math.hypot(*point), 9) for point in points}
    assert radii == {10.0}


def test_grid_order_and_centering():
    from agentcad.toolkit import patterns

    assert patterns.grid(3, 2, 20, 10) == [
        (-20.0, -5.0), (0.0, -5.0), (20.0, -5.0),
        (-20.0, 5.0), (0.0, 5.0), (20.0, 5.0)]
    assert patterns.grid(2, 2, 20, 10, center=False) == [
        (0.0, 0.0), (20.0, 0.0), (0.0, 10.0), (20.0, 10.0)]
    # a single row needs no spacing to be meaningful
    assert patterns.grid(1, 1, 0, 0) == [(0.0, 0.0)]


@pytest.mark.parametrize("call,argument", [
    (lambda p: p.bolt_circle(0, 4), "r"),
    (lambda p: p.bolt_circle(-1, 4), "r"),
    (lambda p: p.bolt_circle(10, 0), "n"),
    (lambda p: p.grid(0, 2, 10, 10), "nx"),
    (lambda p: p.grid(2, 2, 0, 10), "dx"),
    (lambda p: p.grid(2, 2, 10, -3), "dy"),
])
def test_degenerate_point_arguments_raise_naming_the_argument(call, argument):
    from agentcad.toolkit import patterns

    with pytest.raises(ValueError, match=argument):
        call(patterns)


# -------------------------------------------------------------- the guard

def test_engagement_bbox_tier_flags_only_a_certain_miss():
    from build123d import Box, Pos

    from agentcad.toolkit import patterns

    part = _plate()
    near = Pos(0, 0, 0) * Box(10, 10, 30)
    far = Pos(500, 0, 0) * Box(10, 10, 30)
    report = patterns.engagement(part, [(0, near), (1, far)])
    assert [row["status"] for row in report] == ["engaged", "missed"]
    assert [row["probe"] for row in report] == ["bbox", "bbox"]
    assert all(row["engaged_mm3"] is None for row in report)


def test_engagement_exact_tier_reports_engaged_volume_per_instance():
    from build123d import Box, Pos

    from agentcad.toolkit import patterns

    part = _plate()
    inside = Box(10, 10, 30)
    touching = Pos(0, 0, 20) * Box(10, 10, 30)   # sits on the top face
    far = Pos(500, 0, 0) * Box(10, 10, 30)
    report = patterns.engagement(
        part, [(0, inside), (1, touching), (2, far)], verify="exact")
    assert [row["status"] for row in report] == ["engaged", "flush", "missed"]
    assert report[0]["engaged_mm3"] == pytest.approx(10 * 10 * 10)
    assert report[1]["engaged_mm3"] == 0.0


def test_engagement_off_is_honest_about_not_checking():
    from build123d import Box, Pos

    from agentcad.toolkit import patterns

    report = patterns.engagement(
        _plate(), [(0, Pos(500, 0, 0) * Box(5, 5, 5))], verify="off")
    assert report == [{"i": 0, "status": "unchecked", "probe": "off",
                       "engaged_mm3": None}]


def test_and_on_disjoint_shapes_is_an_empty_compound_not_none():
    """Spike S4, re-asserted as a live test: the probe is written with `&`
    because `Shape.intersect()` returns a ShapeList (AGENTS.md), and because on
    a disjoint pair `&` gives an empty Compound with `.volume == 0` — never
    `None`, never a raise. If that ever changes, `engagement` breaks silently."""
    from build123d import Box, Compound, Pos

    result = _plate() & (Pos(500, 0, 0) * Box(5, 5, 5))
    assert result is not None
    assert isinstance(result, Compound)
    assert result.volume == 0
    assert result.solids() == []


def test_spacing_conflicts_names_the_pair_and_the_distance():
    from agentcad.toolkit import patterns

    clashes = patterns.spacing_conflicts([(0, 0), (3, 0), (40, 0)], 5.0)
    assert clashes == [{"a": 0, "b": 1, "distance": 3.0}]


def test_boxes_overlap_is_a_screen_not_a_verdict():
    from agentcad.toolkit import patterns

    a = (0, 0, 0, 10, 10, 10)
    assert patterns.boxes_overlap(a, (5, 5, 5, 15, 15, 15))
    assert not patterns.boxes_overlap(a, (11, 0, 0, 20, 10, 10))
    # touching faces count as overlapping: the screen must never say "missed"
    # about a pair that might touch
    assert patterns.boxes_overlap(a, (10, 0, 0, 20, 10, 10))


# ------------------------------------------------------------ shape patterns

def test_linear_adds_count_minus_one_instances():
    from agentcad.toolkit import patterns

    part, boss = _seeded_plate()
    out, warning = patterns.linear(part, boss, (0, 1, 0), 3, 20)
    assert warning is None
    assert out.volume == pytest.approx(part.volume + 2 * boss.volume, rel=1e-9)
    assert len(out.solids()) == 1
    assert [row["status"] for row in patterns.instances(out)] == [
        "seed", "engaged", "engaged"]


def test_linear_warns_naming_the_instance_that_misses_the_part():
    from agentcad.toolkit import patterns

    part, boss = _seeded_plate()
    # the plate spans x in [-60, 60] and the seed boss sits at x=40 with r=6:
    # instance 1 lands at x=54 and still overlaps the plate, instance 2 at
    # x=68 is entirely off it.
    out, warning = patterns.linear(part, boss, (1, 0, 0), 3, 14)
    assert "instance(s) [2]" in warning
    assert "floating solid" in warning
    assert "2 disjoint solids" in warning
    assert [row["status"] for row in patterns.instances(out)][2] == "missed"


def test_linear_warns_when_spacing_makes_instances_overlap():
    from agentcad.toolkit import patterns

    part, boss = _seeded_plate()
    out, warning = patterns.linear(part, boss, (0, 1, 0), 3, 4)
    assert "spacing 4 mm is less than the seed's 12 mm extent" in warning
    assert "0&1, 1&2" in warning
    assert "overlap each other or existing material" in warning
    assert len(out.solids()) == 1


def test_linear_count_one_is_a_no_op_with_a_warning():
    from agentcad.toolkit import patterns

    part, boss = _seeded_plate()
    out, warning = patterns.linear(part, boss, (0, 1, 0), 1, 20)
    assert out is part
    assert "count=1" in warning


def test_linear_exact_verify_reports_engaged_volume_per_instance():
    from agentcad.toolkit import patterns

    part, boss = _seeded_plate()
    out, _warning = patterns.linear(part, boss, (0, 1, 0), 2, 20,
                                    verify="exact")
    report = patterns.instances(out)
    assert report[0]["status"] == "seed"
    # the boss sits ON the plate: it overlaps nothing, and `exact` says so
    # rather than calling it a miss
    assert report[1]["probe"] == "exact"
    assert report[1]["engaged_mm3"] == 0.0
    assert report[1]["status"] == "flush"


def test_polar_places_count_instances_around_the_axis():
    from build123d import Axis

    from agentcad.toolkit import patterns

    part, boss = _seeded_plate()
    out, warning = patterns.polar(part, boss, Axis.Z, 6)
    assert warning is None
    assert out.volume == pytest.approx(part.volume + 5 * boss.volume, rel=1e-9)
    assert len(out.solids()) == 1
    assert len(patterns.instances(out)) == 6


def test_polar_about_an_axis_that_is_not_z():
    """`PolarLocations` only rotates about the workplane's Z, so the helper
    conjugates its locations into the axis' frame. That path is invisible for
    `Axis.Z` (the conjugation is by identity), so it needs its own test."""
    from build123d import Align, Axis, Box, BuildPart, Cylinder, Locations, Pos

    from agentcad.toolkit import patterns

    with BuildPart() as builder:
        Box(80, 20, 20)
        with Locations((0, 0, 10)):
            Cylinder(4, 6, align=(Align.CENTER, Align.CENTER, Align.MIN))
    lug = Pos(0, 0, 10) * Cylinder(
        4, 6, align=(Align.CENTER, Align.CENTER, Align.MIN))

    out, warning = patterns.polar(builder.part, lug, Axis.X, 4)
    assert warning is None
    assert out.volume == pytest.approx(builder.part.volume + 3 * lug.volume,
                                       rel=1e-9)
    box = out.bounding_box()
    # lugs now stand off all four sides of the bar, not four times off the top
    assert (box.max.Y, box.max.Z) == pytest.approx((16.0, 16.0))
    assert (box.min.Y, box.min.Z) == pytest.approx((-16.0, -16.0))


def test_polar_partial_span_is_inclusive():
    from build123d import Axis

    from agentcad.toolkit import patterns

    part, boss = _seeded_plate()
    out, _warning = patterns.polar(part, boss, Axis.Z, 3, span_deg=180)
    # 3 instances over 180 deg sit at 0/90/180: the last one is the mirror of
    # the seed, at x = -40.
    assert out.volume == pytest.approx(part.volume + 2 * boss.volume, rel=1e-9)
    bbox = out.bounding_box()
    assert bbox.min.Z == pytest.approx(-5.0)
    assert bbox.max.Z == pytest.approx(13.0)


# ------------------------- where the instances actually LANDED

def _centres(part):
    from agentcad.toolkit import patterns

    return sorted(tuple(row["center"][:2])
                  for row in patterns.instances(part))


def _asymmetric_seeded_plate():
    """A plate with a RIGHT-TRIANGULAR boss fused in, plus the boss on its own.

    The shape matters: it has no 180 degree point symmetry, so its bounding box
    is not preserved by rotation. Every other pattern test in this module uses
    a box or a cylinder, both of which are immune — which is exactly how a
    layout assertion built on bounding-box centres passed a green suite while
    firing on correct geometry.
    """
    from build123d import (Align, Box, BuildLine, BuildPart, BuildSketch,
                           Plane, Polyline, extrude, make_face)

    with BuildPart() as bp:
        with BuildSketch(Plane.XY.offset(5)):
            with BuildLine():
                Polyline((30, -6), (42, -6), (30, 10), close=True)
            make_face()
        extrude(amount=8)
    boss = bp.part
    plate = Box(120, 120, 10,
                align=(Align.CENTER, Align.CENTER, Align.MIN)).translate((0, 0, -5))
    return plate + boss, boss


@pytest.mark.parametrize("count,span", [
    (3, 360.0), (5, 360.0), (7, 360.0), (8, 360.0),
    (4, 150.0), (3, 100.0), (5, 217.0),          # steps that are not 90 deg
])
def test_a_correct_polar_of_an_asymmetric_seed_is_silent(count, span):
    """The layout assertion must not fire on geometry that is right.

    Measured on the metric this used to use — the bounding-box centre of each
    MOVED instance — a correct 3-up pattern of this boss reported centres
    32.535898 to 36.055513 mm from the axis: a **3.5196 mm** spread, warned
    about as "a placement bug, not a tolerance", on a result that is one valid
    solid whose added volume is exact to 6e-11. Counts 5 and 8 gave 3.8507 and
    3.0404. A bounding box is only rotation-invariant for a seed with 180
    degree point symmetry, so the metric was wrong, not the tolerance —
    widening the tolerance to swallow 3.5 mm would have swallowed the 20 mm
    placement bug the assertion exists for.
    """
    from build123d import Axis

    from agentcad.toolkit import patterns

    part, boss = _asymmetric_seeded_plate()
    out, warning = patterns.polar(part, boss, Axis.Z, count, span_deg=span)
    assert warning is None, warning
    assert len(out.solids()) == 1
    assert out.volume == pytest.approx(part.volume + (count - 1) * boss.volume,
                                       rel=1e-9)
    radii = [math.hypot(row["center"][0], row["center"][1])
             for row in patterns.instances(out)]
    # rigid images of one reference point: the spread is the 9-decimal
    # rounding and nothing else
    assert max(radii) - min(radii) < 1e-8


def test_linear_and_mirror_of_an_asymmetric_seed_are_silent_too():
    from agentcad.toolkit import patterns

    part, boss = _asymmetric_seeded_plate()
    out, warning = patterns.linear(part, boss, (0, 1, 0), 3, 25)
    assert warning is None, warning
    assert _centres(out) == [(36.0, 2.0), (36.0, 27.0), (36.0, 52.0)]
    out, warning = patterns.mirror(part, "YZ", seed=boss)
    assert warning is None, warning
    # the mirror's centre is the reflection of the seed's reference point,
    # computed rather than re-measured off the image
    assert _centres(out) == [(-36.0, 2.0)]


def test_polar_records_where_every_instance_went():
    """Volume, solid count and engagement are all blind to placement. The
    report carries the centre of each instance so a test can assert the thing
    that actually went wrong."""
    from build123d import Axis

    from agentcad.toolkit import patterns

    part, boss = _seeded_plate()
    out, warning = patterns.polar(part, boss, Axis.Z, 4)
    assert warning is None
    assert _centres(out) == [(-40.0, 0.0), (0.0, -40.0), (0.0, 40.0),
                             (40.0, 0.0)]


def test_polar_with_a_radius_places_every_instance_on_the_circle():
    """`radius=r` translates EVERY instance onto the circle, so none of them is
    the seed where it already sits and none may be skipped.

    Measured before the fix (an independent review's probe): a centre boss
    patterned `count=4, radius=20` returned `warning=None`, one valid solid and
    the *expected* added volume of 3 x 301.592895 mm^3 — with the instances at
    (-20,0), (0,-20), (0,0) and (0,20). The (+20,0) instance was never placed
    and the seed was counted in its stead, because the helper skipped index 0
    on the assumption that index 0 is always the seed. It is the seed only when
    the placement moves it nowhere, which with a radius no placement does.
    """
    from build123d import Align, Axis, Box, BuildPart, Cylinder, Locations, Pos

    from agentcad.toolkit import patterns

    with BuildPart() as builder:
        Box(120, 120, 10)
        with Locations((0, 0, 5)):
            Cylinder(6, 8, align=(Align.CENTER, Align.CENTER, Align.MIN))
    boss = Pos(0, 0, 5) * Cylinder(
        6, 8, align=(Align.CENTER, Align.CENTER, Align.MIN))
    part = builder.part

    out, warning = patterns.polar(part, boss, Axis.Z, 4, radius=20)
    assert _centres(out) == [(-20.0, 0.0), (0.0, -20.0), (0.0, 20.0),
                             (20.0, 0.0)]
    assert len(patterns.instances(out)) == 4
    # all four are ADDED — the seed at the centre is not one of them.
    assert out.volume == pytest.approx(part.volume + 4 * boss.volume, rel=1e-9)
    assert warning is not None and "all 4 were ADDED" in warning
    # and the advice has to be one the caller can actually follow: building the
    # seed where instance 0 goes and passing `radius=None` yields exactly
    # `count` (asserted below), whereas "author the seed at the axis" — which
    # this warning used to recommend — guarantees the leftover it complains of.
    assert "radius=None" in warning


def test_the_radius_warnings_advice_actually_yields_a_clean_circle():
    """The follow-through for the warning above. `polar` cannot consume a seed
    it was handed, so `radius` always leaves one over; the only route to
    exactly `count` on a circle is a seed built on it and `radius=None`."""
    from build123d import Align, Axis, Box, BuildPart, Cylinder, Locations, Pos

    from agentcad.toolkit import patterns

    with BuildPart() as builder:
        Box(120, 120, 10)
        with Locations((20, 0, 5)):
            Cylinder(6, 8, align=(Align.CENTER, Align.CENTER, Align.MIN))
    seed = Pos(20, 0, 5) * Cylinder(
        6, 8, align=(Align.CENTER, Align.CENTER, Align.MIN))
    part = builder.part

    out, warning = patterns.polar(part, seed, Axis.Z, 4)
    assert warning is None, warning
    assert _centres(out) == [(-20.0, 0.0), (0.0, -20.0), (0.0, 20.0),
                             (20.0, 0.0)]
    # four bosses in total, not five: the seed IS instance 0 here
    assert out.volume == pytest.approx(part.volume + 3 * seed.volume, rel=1e-9)


def test_a_polar_pattern_that_lands_off_the_circle_is_not_silent():
    """The location-level assertion, exercised against a placement list that
    has been sabotaged the way the bug sabotaged it — one instance left at the
    axis. Volume, validity and per-instance engagement all still pass."""
    from build123d import Axis, Plane

    from agentcad.toolkit import patterns

    part, boss = _seeded_plate()
    warnings = patterns._polar_layout_warnings(
        "patterns.polar", Axis.Z, Plane.XY, 20.0, 360.0, 4,
        [(0.0, 0.0, 8.0), (0.0, 20.0, 8.0), (-20.0, 0.0, 8.0),
         (0.0, -20.0, 8.0)])
    assert any("same distance from the axis" in w for w in warnings), warnings


def test_linear_records_its_centres_and_asserts_the_step():
    from agentcad.toolkit import patterns

    part, boss = _seeded_plate()
    out, warning = patterns.linear(part, boss, (0, 1, 0), 3, 30)
    assert warning is None
    assert _centres(out) == [(40.0, 0.0), (40.0, 30.0), (40.0, 60.0)]
    # and the assertion fires on a step that is not the one requested
    assert patterns._linear_layout_warnings(
        "patterns.linear", patterns.Vector(0, 1, 0), 30.0,
        [(40.0, 0.0, 9.0), (40.0, 30.0, 9.0), (40.0, 55.0, 9.0)])


def test_mirror_doubles_an_asymmetric_part():
    from build123d import Box, Plane, Pos

    from agentcad.toolkit import patterns

    part = Pos(20, 0, 0) * Box(40, 20, 10)       # x in [0, 40], touches YZ
    out, warning = patterns.mirror(part, Plane.YZ)
    assert warning is None
    assert out.volume == pytest.approx(2 * part.volume, rel=1e-9)
    assert len(out.solids()) == 1


def test_mirror_warns_when_the_part_is_already_symmetric():
    from build123d import Box, Plane

    from agentcad.toolkit import patterns

    _out, warning = patterns.mirror(Box(40, 20, 10), Plane.YZ)
    assert "added no material" in warning
    assert "already symmetric" in warning


def test_mirror_warns_when_the_part_straddles_the_plane():
    from build123d import Box, Plane, Pos

    from agentcad.toolkit import patterns

    part = Pos(10, 0, 0) * Box(40, 20, 10)       # x in [-10, 30]
    _out, warning = patterns.mirror(part, Plane.YZ)
    assert "overlaps the original by" in warning
    assert "straddles the mirror plane" in warning


def test_mirror_accepts_a_plane_name():
    from build123d import Box, Plane, Pos

    from agentcad.toolkit import patterns

    part = Pos(20, 0, 0) * Box(40, 20, 10)
    named, _w1 = patterns.mirror(part, "YZ")
    explicit, _w2 = patterns.mirror(part, Plane.YZ)
    assert named.volume == pytest.approx(explicit.volume, rel=1e-12)


@pytest.mark.parametrize("call,argument", [
    (lambda p, part, seed: p.linear(part, seed, (0, 1, 0), 0, 10), "count"),
    (lambda p, part, seed: p.linear(part, seed, (0, 1, 0), 3, 0), "spacing"),
    (lambda p, part, seed: p.linear(part, seed, (0, 1, 0), 3, -5), "spacing"),
    (lambda p, part, seed: p.linear(part, seed, (0, 0, 0), 3, 10), "direction"),
    (lambda p, part, seed: p.linear(part, seed, (0, 1, 0), 2.5, 10), "count"),
    (lambda p, part, seed: p.polar(part, seed, count=3, span_deg=0), "span_deg"),
    (lambda p, part, seed: p.polar(part, seed, count=3, span_deg=400),
     "span_deg"),
    (lambda p, part, seed: p.polar(part, seed, count=3, radius=-1), "radius"),
    (lambda p, part, seed: p.linear(part, seed, (0, 1, 0), 3, 10,
                                    verify="sometimes"), "verify"),
    (lambda p, part, seed: p.mirror(part, "diagonal"), "plane"),
])
def test_impossible_requests_raise_naming_the_argument(call, argument):
    """An impossible request is not geometry: it raises at the call, where the
    script_error carries a line number, instead of becoming a warning nobody
    reads."""
    from agentcad.toolkit import patterns

    part, boss = _seeded_plate()
    with pytest.raises(ValueError, match=argument):
        call(patterns, part, boss)


def test_the_builder_route_falls_back_to_safe_bool_and_says_so(monkeypatch):
    """The fallback rung of design Decision 1. The builder is the primary route
    because it is the byte-faithful one; when OCCT refuses it, `safe_bool`'s
    fuzzy escalation still produces geometry — and the warning must say the
    result may no longer be byte-identical, because a compound fuse is a
    different construction (measured in slice 1: same volume, different mesh).

    The failure is injected rather than found: a genuine builder failure is not
    reproducible on demand, and an untested rung is a rung that does not work.
    """
    from agentcad.toolkit import patterns

    part, boss = _seeded_plate()

    def explode(*args, **kwargs):
        raise RuntimeError("BRep_API: command not done")

    monkeypatch.setattr(patterns, "BuildPart", explode)
    out, warning = patterns.linear(part, boss, (0, 1, 0), 3, 20)
    assert "fell back to safe_bool" in warning
    assert "may not be byte-identical" in warning
    assert out.volume == pytest.approx(part.volume + 2 * boss.volume, rel=1e-9)
    assert len(out.solids()) == 1


def test_instances_is_empty_for_a_part_no_pattern_touched():
    from agentcad.toolkit import patterns

    assert patterns.instances(_plate()) == []


# ------------------------------------------------------------------ identity

POLAR_HELPER = '''
from build123d import *
from agentcad.toolkit import patterns

PARAMS = {"n": {"default": 6, "type": "int", "min": 2, "max": 12}}

def build(p):
    with BuildPart() as bp:
        Box(120, 120, 10)
        with Locations((40, 0, 5)):
            Cylinder(6, 8, align=(Align.CENTER, Align.CENTER, Align.MIN))
    boss = Pos(40, 0, 5) * Cylinder(
        6, 8, align=(Align.CENTER, Align.CENTER, Align.MIN))
    out, warning = patterns.polar(bp.part, boss, Axis.Z, int(p.n))
    return out
'''

POLAR_HANDWRITTEN = '''
from build123d import *

PARAMS = {"n": {"default": 6, "type": "int", "min": 2, "max": 12}}

def build(p):
    with BuildPart() as bp:
        Box(120, 120, 10)
        with Locations((40, 0, 5)):
            Cylinder(6, 8, align=(Align.CENTER, Align.CENTER, Align.MIN))
    boss = Pos(40, 0, 5) * Cylinder(
        6, 8, align=(Align.CENTER, Align.CENTER, Align.MIN))
    n = int(p.n)
    locs = [Location(Vector(0, 0, 0), Vector(0, 0, 1), 360.0 / n * i)
            for i in range(1, n)]
    with BuildPart() as out:
        add(bp.part)
        with Locations(*locs):
            add(boss)
    return out.part
'''


@pytest.mark.integration
def test_polar_helper_is_byte_identical_to_the_handwritten_form(kernel,
                                                                tmp_path):
    """Design Decision 1, on this module: the helper is a *wrapper*, so the
    mesh it produces has to be the hand-written one's, byte for byte. Both
    scripts go through the real worker at the service's mesh tolerance; the
    probe is slice 1's harness, not a second one grown here."""
    from .test_examples_golden import REL, script_acm_sha256

    sha_helper, metrics_helper = script_acm_sha256(
        kernel, POLAR_HELPER, mesh_path=tmp_path / "helper.acm")
    sha_hand, metrics_hand = script_acm_sha256(
        kernel, POLAR_HANDWRITTEN, mesh_path=tmp_path / "hand.acm")
    assert metrics_helper["volume_mm3"] == pytest.approx(
        metrics_hand["volume_mm3"], rel=REL)
    assert metrics_helper["n_faces"] == metrics_hand["n_faces"]
    assert sha_helper == sha_hand, (
        "patterns.polar no longer reproduces the hand-written PolarLocations "
        "form byte for byte — the helper has stopped being a wrapper")


# ---------------------- R7: the exact tier must tell a seat from a miss

def _pocketed_plate_with_boss():
    """A plate with a 20x20 hole clean through at x = +40, and a boss already
    fused on top at x = -40. Patterning that boss along +X by 80 lands the
    copy squarely over the hole: its bounding box overlaps the plate, and it
    has nothing whatsoever to weld to.
    """
    from build123d import Align, Box, Pos

    plate = Box(120, 120, 10) - (Pos(40, 0, 0) * Box(20, 20, 30))
    boss = Pos(-40, 0, 5) * Box(10, 10, 8,
                                align=(Align.CENTER, Align.CENTER, Align.MIN))
    return plate + boss, boss


def test_exact_verify_does_not_warn_about_a_correctly_seated_instance():
    """**Regression.** A helper-built rib or boss sits ON its seat plane, so
    its interpenetration with the part is 0 **by construction**. The `exact`
    tier called that `flush` and warned about every one of them — the strong
    tier crying wolf on the happy path, which trains a reader to ignore it.

    A seat and an accidental tangency are not the same thing, and the
    difference is measurable: a seat shares a face of positive AREA.
    """
    from build123d import Box, Pos

    from agentcad.toolkit import patterns

    part = _plate()
    seat = Pos(0, 0, 10) * Box(20, 20, 10)      # face-to-face on the top face
    report = patterns.engagement(part, [(0, seat)], verify="exact")
    assert report[0]["status"] == "flush"
    assert report[0]["engaged_mm3"] == 0.0
    assert report[0]["contact_mm2"] == pytest.approx(400.0)

    # and through a real helper: a boss patterned onto the plate is correct
    # construction, so the strong tier has nothing to say about it
    seeded, boss = _seeded_plate()
    out, warning = patterns.linear(seeded, boss, (0, 1, 0), 2, 20,
                                   verify="exact")
    assert warning is None, warning
    assert patterns.instances(out)[1]["status"] == "flush"
    assert patterns.instances(out)[1]["contact_mm2"] > 0.0


def test_exact_verify_sees_a_miss_into_existing_void_that_bbox_cannot():
    """The other half. The default `bbox` tier only asks whether the bounding
    boxes overlap, so an instance dropped into a pocket that was already cut
    out of the part reads as `engaged` — the boxes do overlap, and nothing
    about them knows the material is gone. Only a measurement can see it, and
    the exact tier used to call this `flush` too, i.e. exactly what it called
    a correct seat.
    """
    from build123d import Box, Pos

    from agentcad.toolkit import patterns

    part, boss = _pocketed_plate_with_boss()
    over_hole = Pos(80, 0, 0) * boss             # the copy the pattern makes

    loose = patterns.engagement(part, [(0, over_hole)])
    assert loose[0]["status"] == "engaged", "the bbox tier cannot see this"

    strict = patterns.engagement(part, [(0, over_hole)], verify="exact")
    assert strict[0]["status"] == "missed"
    assert strict[0]["contact_mm2"] == 0.0

    # and the helper says so instead of fusing a floating solid in silence
    _out, warning = patterns.linear(part, boss, (1, 0, 0), 2, 80,
                                    verify="exact")
    assert warning is not None and "[1]" in warning


def test_exact_verify_still_flags_an_edge_only_tangency():
    """An instance meeting the part along an edge has zero interpenetration
    AND zero contact area: there is nothing to weld. It reads as a miss, which
    is what it structurally is, and not as the valid flush join a face seat
    is."""
    from build123d import Box, Pos

    from agentcad.toolkit import patterns

    part = _plate()
    tangent = Pos(65, 65, 10) * Box(10, 10, 10)   # corner-to-corner only
    report = patterns.engagement(part, [(0, tangent)], verify="exact")
    assert report[0]["status"] == "missed"
    assert report[0]["contact_mm2"] == 0.0
