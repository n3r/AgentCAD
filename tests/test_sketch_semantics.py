"""What each residual **means**, asserted against geometry the test computes.

`tests/test_sketch_jacobian.py` proves every `df` is the derivative of its own
`f`. That is a strong property and it has caught real bugs, but it is closed
over `f`: **a geometrically wrong residual passes it, and passed it.** Review 2
found the internal circle/circle tangency computing `d - (r1 - r2)` instead of
`d - |r1 - r2|` (finding C1) — a residual that says two internally tangent
circles are 10 mm from tangent if you name them in the wrong order — and every
derivative test on the branch was green over it, because the derivative of the
wrong expression is the derivative of the wrong expression (finding C15).

So this module is the missing layer, and it is deliberately written the other
way round from the solver: each case builds a configuration whose geometry is
computed **here**, from first principles, and asserts

1. `f == 0` where the constraint holds, and
2. `f` is proportional to the geometric error, with the right **sign** and the
   right **scale**, where it does not.

(2) is the half that matters. "The residual is small at the solution" is what
`max_residual` already claims and it was measured lying by a factor of 6.1e+05
(changelog 0142): a residual that is the *square* of the error is small for
reasons that have nothing to do with the sketch being right. A slope check
catches that, an equality check at the answer does not.

Coverage is the three families the review's findings lived in — tangency (C1),
distance/norm (C2) and radius/equality — plus the linear vocabulary, which is
cheap to include and keeps the module from reading as a special case.
"""
from __future__ import annotations

import math

import pytest

from agentcad.toolkit.sketch import Sketch

# How far the probe configuration is moved off the constraint, in mm or in the
# residual's own units. Small enough that a first-order slope is the slope, big
# enough to be far above the 1e-16 floor.
NUDGE = 1e-3


def value(sk: Sketch, index: int = -1, row: int = 0) -> float:
    """One residual's value at the sketch's own starting configuration."""
    return float(sk.residuals[index].f(sk.initial_vector())[row])


def slope(place, *, at: float, span: float = NUDGE) -> float:
    """d(residual)/d(geometric error), measured by rebuilding the geometry.

    `place(e)` returns a sketch whose geometric error is `e`; the slope is
    taken across `at` so a residual that is the *square* of the error reads
    ~`2 * at` rather than a constant, and one that is the error itself reads 1.
    """
    hi, lo = value(place(at + span)), value(place(at - span))
    return (hi - lo) / (2 * span)


# --------------------------------------------------------------------------
# the tangency family — where C1 lived
# --------------------------------------------------------------------------
def circles(r1: float, r2: float, d: float, kind: str, *,
            swap: bool = False) -> Sketch:
    """Two fixed circles whose centres are `d` apart, and one tangency."""
    sk = Sketch()
    sk.point("c1", 0.0, 0.0, fixed=True)
    sk.point("c2", d, 0.0, fixed=True)
    sk.circle("C1", "c1", r1, fixed_r=True)
    sk.circle("C2", "c2", r2, fixed_r=True)
    a, b = ("C2", "C1") if swap else ("C1", "C2")
    sk.tangent_circles(a, b, kind)
    return sk


@pytest.mark.parametrize("swap", [False, True])
@pytest.mark.parametrize("r1,r2", [(10.0, 5.0), (5.0, 10.0), (7.0, 7.0)])
def test_external_tangency_is_zero_exactly_when_the_circles_touch(r1, r2, swap):
    """Externally tangent means `d == r1 + r2`, whichever way round."""
    assert circles(r1, r2, r1 + r2, "external", swap=swap) is not None
    assert value(circles(r1, r2, r1 + r2, "external", swap=swap)) == \
        pytest.approx(0.0, abs=1e-12)


@pytest.mark.parametrize("swap", [False, True])
@pytest.mark.parametrize("r1,r2", [(10.0, 5.0), (5.0, 10.0), (12.0, 3.0)])
def test_internal_tangency_is_zero_exactly_when_the_circles_touch(r1, r2, swap):
    """Internally tangent means `d == |r1 - r2|`. **This is finding C1**: with
    `d - (r1 - r2)` the same geometry read 0 one way round and `2 * |r1 - r2|`
    the other, so half of these cases returned `ok: false` on circles that are
    touching."""
    d = abs(r1 - r2)
    assert value(circles(r1, r2, d, "internal", swap=swap)) == \
        pytest.approx(0.0, abs=1e-12)


@pytest.mark.parametrize("kind", ["external", "internal"])
@pytest.mark.parametrize("swap", [False, True])
def test_the_tangency_residual_is_the_separation_error_one_for_one(kind, swap):
    """The scale, not the smallness: move the centres `e` past tangency and
    the residual must move by `e`."""
    r1, r2 = 10.0, 4.0
    touch = r1 + r2 if kind == "external" else abs(r1 - r2)
    got = slope(lambda e: circles(r1, r2, touch + e, kind, swap=swap), at=0.0)
    assert got == pytest.approx(1.0, abs=1e-9)
    # and the sign: further apart than tangency is a positive residual
    assert value(circles(r1, r2, touch + 0.5, kind, swap=swap)) > 0.0
    assert value(circles(r1, r2, touch - 0.5, kind, swap=swap)) < 0.0


def line_circle(gap: float) -> Sketch:
    """A circle of radius 10 and a line whose distance from its centre is
    `10 + gap`."""
    sk = Sketch()
    sk.point("c", 0.0, 0.0, fixed=True)
    sk.point("p", -20.0, 10.0 + gap, fixed=True)
    sk.point("q", 20.0, 10.0 + gap, fixed=True)
    sk.circle("C", "c", 10.0, fixed_r=True)
    sk.line("L", "p", "q")
    sk.tangent_line_circle("L", "C")
    return sk


def test_line_circle_tangency_is_zero_when_the_line_grazes_the_circle():
    assert value(line_circle(0.0)) == pytest.approx(0.0, abs=1e-12)


def test_line_circle_tangency_measures_the_gap_one_for_one():
    assert slope(line_circle, at=0.0) == pytest.approx(1.0, abs=1e-9)
    assert value(line_circle(0.25)) > 0.0
    assert value(line_circle(-0.25)) < 0.0


def pinned_tangency(err_deg: float) -> Sketch:
    """A line through a point held on a circle, `err_deg` off tangent there.

    The direction form, whose residual is the **sine** of the angle between the
    two tangents — which is the claim changelog 0142 made and this is the test
    that measures it rather than restating it.
    """
    sk = Sketch()
    sk.point("c", 0.0, 0.0, fixed=True)
    sk.point("p", 10.0, 0.0, fixed=True)
    a = math.radians(90.0 + err_deg)
    sk.point("q", 10.0 + 20.0 * math.cos(a), 20.0 * math.sin(a), fixed=True)
    sk.circle("C", "c", 10.0, fixed_r=True)
    sk.line("L", "p", "q")
    sk.point_on_circle("p", "C")
    sk.tangent("L", "C")
    return sk


def test_a_pinned_tangency_reads_the_sine_of_the_angle_error():
    assert value(pinned_tangency(0.0)) == pytest.approx(0.0, abs=1e-12)
    for err in (0.5, 2.0, 10.0):
        got = abs(value(pinned_tangency(err)))
        assert got == pytest.approx(abs(math.sin(math.radians(err))), rel=1e-9)


def test_a_pinned_tangency_is_first_order_not_second():
    """The property the whole direction form exists for: the residual's slope
    in the angle error is **1** at the solution, not 0. A second-order-flat
    residual reads 0 there, which is how `max_residual` came to be 6.1e+05
    times smaller than the error it was supposed to be measuring."""
    d = math.radians(1e-4)
    hi = value(pinned_tangency(1e-4))
    lo = value(pinned_tangency(-1e-4))
    # magnitude 1: the *sign* is which way round the cross product is taken,
    # which is a convention; the slope being 1 rather than 0 is the property.
    assert abs((hi - lo) / (2 * d)) == pytest.approx(1.0, abs=1e-6)


# --------------------------------------------------------------------------
# the distance family — where C2 lived
# --------------------------------------------------------------------------
def two_points(sep: float, target: float) -> Sketch:
    sk = Sketch()
    sk.point("a", 0.0, 0.0, fixed=True)
    sk.point("b", sep * 0.6, sep * 0.8, fixed=True)     # a 3-4-5 direction
    sk.distance("a", "b", target)
    return sk


def test_distance_is_zero_at_the_length_it_names():
    assert value(two_points(12.0, 12.0)) == pytest.approx(0.0, abs=1e-12)


def test_distance_is_the_length_error_one_for_one():
    assert slope(lambda e: two_points(12.0 + e, 12.0), at=0.0) == \
        pytest.approx(1.0, abs=1e-9)
    assert value(two_points(13.0, 12.0)) == pytest.approx(1.0, abs=1e-9)
    assert value(two_points(11.0, 12.0)) == pytest.approx(-1.0, abs=1e-9)


def test_a_zero_distance_is_the_two_coordinate_errors():
    """Compiled as a coincidence (finding C2), so its rows are the offsets —
    and each is that offset exactly, not its magnitude."""
    sk = Sketch()
    sk.point("a", 0.0, 0.0, fixed=True)
    sk.point("b", 0.3, -0.4, fixed=True)
    sk.distance("a", "b", 0.0)
    v = sk.initial_vector()
    assert list(sk.residuals[-1].f(v)) == pytest.approx([-0.3, 0.4], abs=1e-12)


def point_on_circle(off: float) -> Sketch:
    sk = Sketch()
    sk.point("c", 1.0, 2.0, fixed=True)
    sk.point("p", 1.0 + (10.0 + off) * 0.6, 2.0 + (10.0 + off) * 0.8,
             fixed=True)
    sk.circle("C", "c", 10.0, fixed_r=True)
    sk.point_on_circle("p", "C")
    return sk


def test_point_on_circle_is_the_signed_distance_from_the_rim():
    assert value(point_on_circle(0.0)) == pytest.approx(0.0, abs=1e-12)
    assert value(point_on_circle(1.5)) == pytest.approx(1.5, abs=1e-9)
    assert value(point_on_circle(-1.5)) == pytest.approx(-1.5, abs=1e-9)
    assert slope(point_on_circle, at=0.0) == pytest.approx(1.0, abs=1e-9)


def point_on_line(off: float) -> Sketch:
    sk = Sketch()
    sk.point("a", 0.0, 0.0, fixed=True)
    sk.point("b", 30.0, 40.0, fixed=True)               # a 3-4-5 direction
    # `off` mm along the line's left normal, which is (-0.8, 0.6)
    sk.point("p", 12.0 - 0.8 * off, 16.0 + 0.6 * off, fixed=True)
    sk.line("L", "a", "b")
    sk.point_on_line("p", "L")
    return sk


def test_point_on_line_is_the_signed_perpendicular_distance():
    assert value(point_on_line(0.0)) == pytest.approx(0.0, abs=1e-12)
    for off in (0.5, -2.0, 7.0):
        assert value(point_on_line(off)) == pytest.approx(-off, abs=1e-9)


def equal_length(len_a: float, len_b: float) -> Sketch:
    sk = Sketch()
    sk.point("a", 0.0, 0.0, fixed=True)
    sk.point("b", len_a * 0.6, len_a * 0.8, fixed=True)
    sk.point("c", 50.0, 0.0, fixed=True)
    sk.point("d", 50.0, len_b, fixed=True)
    sk.line("L1", "a", "b")
    sk.line("L2", "c", "d")
    sk.equal_length("L1", "L2")
    return sk


def test_equal_length_is_the_difference_of_the_two_lengths():
    assert value(equal_length(9.0, 9.0)) == pytest.approx(0.0, abs=1e-12)
    assert value(equal_length(11.0, 9.0)) == pytest.approx(2.0, abs=1e-9)
    assert value(equal_length(9.0, 11.0)) == pytest.approx(-2.0, abs=1e-9)


# --------------------------------------------------------------------------
# radius and equality
# --------------------------------------------------------------------------
def radius_of(actual: float, want: float) -> Sketch:
    sk = Sketch()
    sk.point("c", 0.0, 0.0, fixed=True)
    sk.circle("C", "c", actual)
    sk.radius("C", want)
    return sk


def test_radius_is_the_signed_error_in_the_radius():
    assert value(radius_of(6.0, 6.0)) == pytest.approx(0.0, abs=1e-12)
    assert value(radius_of(7.25, 6.0)) == pytest.approx(1.25, abs=1e-9)
    assert value(radius_of(4.0, 6.0)) == pytest.approx(-2.0, abs=1e-9)


def equal_radius(r1: float, r2: float) -> Sketch:
    sk = Sketch()
    sk.point("c1", 0.0, 0.0, fixed=True)
    sk.point("c2", 40.0, 0.0, fixed=True)
    sk.circle("C1", "c1", r1)
    sk.circle("C2", "c2", r2)
    sk.equal_radius("C1", "C2")
    return sk


def test_equal_radius_is_the_difference_of_the_two_radii():
    assert value(equal_radius(5.0, 5.0)) == pytest.approx(0.0, abs=1e-12)
    assert value(equal_radius(8.0, 5.0)) == pytest.approx(3.0, abs=1e-9)
    assert value(equal_radius(5.0, 8.0)) == pytest.approx(-3.0, abs=1e-9)


def test_an_arcs_radius_is_a_radius():
    """`radius` and `equal_radius` accept arcs and ellipse semi-axes, and they
    have to mean the same thing on each."""
    sk = Sketch()
    sk.point("c", 0.0, 0.0, fixed=True)
    sk.arc("A", "c", 7.5, 0.0, 90.0)
    sk.ellipse("E", "c", 12.0, 4.0)
    sk.radius("A", 6.0)
    assert value(sk) == pytest.approx(1.5, abs=1e-12)
    sk.radius("E.a", 10.0)
    assert value(sk) == pytest.approx(2.0, abs=1e-12)


# --------------------------------------------------------------------------
# the linear vocabulary, for completeness
# --------------------------------------------------------------------------
def test_the_axis_aligned_distances_are_signed_and_directed():
    sk = Sketch()
    sk.point("a", 1.0, 2.0, fixed=True)
    sk.point("b", 9.0, 20.0, fixed=True)
    sk.distance_x("a", "b", 5.0)
    assert value(sk) == pytest.approx(9.0 - 1.0 - 5.0, abs=1e-12)
    sk.distance_y("a", "b", 5.0)
    assert value(sk) == pytest.approx(20.0 - 2.0 - 5.0, abs=1e-12)


def test_horizontal_and_vertical_are_the_off_axis_component():
    sk = Sketch()
    sk.point("a", 1.0, 2.0, fixed=True)
    sk.point("b", 9.0, 20.0, fixed=True)
    sk.line("L", "a", "b")
    sk.horizontal("L")
    assert value(sk) == pytest.approx(18.0, abs=1e-12)
    sk.vertical("L")
    assert value(sk) == pytest.approx(8.0, abs=1e-12)


def two_lines(deg: float) -> Sketch:
    sk = Sketch()
    sk.point("a", 0.0, 0.0, fixed=True)
    sk.point("b", 10.0, 0.0, fixed=True)
    sk.point("c", 0.0, 5.0, fixed=True)
    sk.point("d", 10.0 * math.cos(math.radians(deg)),
             5.0 + 10.0 * math.sin(math.radians(deg)), fixed=True)
    sk.line("L1", "a", "b")
    sk.line("L2", "c", "d")
    return sk


@pytest.mark.parametrize("deg", [0.0, 17.0, 90.0, 143.0])
def test_parallel_perpendicular_and_angle_read_the_angle_between_them(deg):
    rad = math.radians(deg)
    sk = two_lines(deg)
    sk.parallel("L1", "L2")
    assert value(sk) == pytest.approx(math.sin(rad), abs=1e-9)
    sk.perpendicular("L1", "L2")
    assert value(sk) == pytest.approx(math.cos(rad), abs=1e-9)
    sk.angle("L1", "L2", deg)
    assert value(sk) == pytest.approx(0.0, abs=1e-9)
    sk.angle("L1", "L2", deg - 5.0)
    assert value(sk) == pytest.approx(math.radians(5.0), abs=1e-9)


def test_midpoint_is_the_offset_from_the_middle():
    sk = Sketch()
    sk.point("a", 0.0, 0.0, fixed=True)
    sk.point("b", 10.0, 20.0, fixed=True)
    sk.point("m", 5.5, 9.0, fixed=True)
    sk.line("L", "a", "b")
    sk.midpoint("m", "L")
    v = sk.initial_vector()
    assert list(sk.residuals[-1].f(v)) == pytest.approx([0.5, -1.0], abs=1e-12)


def test_symmetric_is_the_midpoint_offset_and_the_off_perpendicular():
    """Two rows per pair, and the second one is the reason: the midpoint row
    alone is satisfied by any pair whose middle lands on the axis."""
    sk = Sketch()
    sk.point("x1", 0.0, 0.0, fixed=True)
    sk.point("x2", 10.0, 0.0, fixed=True)      # the axis is y = 0
    sk.point("p", -3.0, 4.0, fixed=True)
    sk.point("q", -3.0, -4.0, fixed=True)      # the exact mirror
    sk.line("AX", "x1", "x2")
    sk.symmetric("p", "q", "AX")
    v = sk.initial_vector()
    assert max(abs(x) for x in sk.residuals[-1].f(v)) < 1e-12
    sk2 = Sketch()
    sk2.point("x1", 0.0, 0.0, fixed=True)
    sk2.point("x2", 10.0, 0.0, fixed=True)
    sk2.point("p", -3.0, 4.0, fixed=True)
    sk2.point("q", 1.0, -4.0, fixed=True)      # mirrored in y, moved in x
    sk2.line("AX", "x1", "x2")
    sk2.symmetric("p", "q", "AX")
    rows = list(sk2.residuals[-1].f(sk2.initial_vector()))
    assert abs(rows[0]) < 1e-12                # still centred on the axis
    # `(q - p) . u` — how far along the axis the pair is offset, 4 mm here, and
    # the point is that the second row sees what the first cannot. Not the
    # *sine* of that angle: normalizing `q - p` is review 2's finding C2, and
    # it made a pair the constraint itself holds together read 1.0 (0144).
    assert abs(rows[1]) == pytest.approx(4.0, abs=1e-9)
