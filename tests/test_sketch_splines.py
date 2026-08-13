"""Splines, and the spike that decided their emitted form (PRD-009 slice 6).

## The spike, and its numbers

The solver models a spline as its **through points**. build123d's `Spline`
interpolates a point list with its own end conditions; `Bezier` treats the same
list as *control* points. Design Decision 3 left the choice open and the plan
required it to be measured before anything was built on it, against the 1e-8 mm
emission tolerance of Decision 10. Measured over five representative control
polygons (gentle, tight, S-curve, near-collinear, closed-ish):

| polygon | `Spline` max deviation | `Bezier` max deviation |
|---|---:|---:|
| gentle | 7.105e-15 mm | 1.514 mm |
| tight | 3.553e-15 mm | 5.202 mm |
| s_curve | 3.662e-15 mm | 6.911 mm |
| near_collinear | 1.776e-15 mm | 8.854e-04 mm |
| closed_ish | 3.662e-15 mm | 9.805 mm |

**`Spline`, by seven orders of margin.** `Bezier` is off by whole millimetres,
which is not a tolerance question but a different curve — the fallback would
have been a semantics change, and it is not needed.

The spike also measured something the design did *not* anticipate, and it
changes what an end-tangent constraint has to emit: a free-end `Spline`'s
tangent at its first point is up to **44.6 deg** away from the first
control-polygon leg (gentle 8.0, s_curve 25.9, tight 41.1, closed_ish 44.6,
near_collinear 0.016). Passing `tangents=(leg0, legN)` pins it to **7.1e-15
deg** while still interpolating every point to **7.3e-15 mm**. So the solver's
end-tangent residual is only true of the emitted curve if the emitter passes
`tangents=`, and the solve result carries `splines[name]["end_tangent"]` and
the solved directions for exactly that reason.

The build123d half of the spike is asserted below; it is the only part of this
module that needs a geometry kernel.
"""

import math

import pytest

from agentcad.toolkit.sketch import Sketch, SketchError, solve_sketch

from .test_sketch_jacobian import (assert_df_matches_central_difference,
                                   assert_df_stays_inside_params)

# The five control polygons the spike measured, kept verbatim so the test and
# the recorded numbers are the same experiment.
POLYGONS = {
    "gentle": [(0.0, 0.0), (10.3, 4.7), (21.9, 6.1), (33.4, 2.8)],
    "tight": [(0.0, 0.0), (12.7, 1.3), (14.1, 13.9), (2.2, 15.4)],
    "s_curve": [(0.0, 0.0), (9.1, 7.3), (18.6, -6.4), (27.7, 1.9), (36.2, 9.8)],
    "near_collinear": [(0.0, 0.0), (11.13, 0.0007), (22.47, -0.0011),
                       (33.91, 0.0004)],
    "closed_ish": [(0.0, 0.0), (14.7, 9.3), (27.1, -1.7), (13.9, -11.2),
                   (0.0001, -0.0002)],
}

EMISSION_TOL_MM = 1e-8          # design Decision 10's closure gate


def _spline_sketch() -> Sketch:
    """End tangency: a direction residual against the control-polygon leg."""
    sk = Sketch()
    for i, (x, y) in enumerate(POLYGONS["s_curve"]):
        sk.point(f"p{i}", x, y)
    sk.point("la", -4.1, -3.7)
    sk.point("lb", 6.3, 9.4)
    sk.line("L", "la", "lb")
    sk.spline("sp", [f"p{i}" for i in range(len(POLYGONS["s_curve"]))])
    sk.tangent("sp.start", "L")
    sk.tangent("L", "sp.end")
    return sk


DERIV_BUILDERS = {"splines": _spline_sketch}


@pytest.mark.parametrize("name", sorted(DERIV_BUILDERS))
def test_every_df_matches_a_central_difference_of_its_own_f(name):
    assert_df_matches_central_difference(name, DERIV_BUILDERS[name]())


@pytest.mark.parametrize("name", sorted(DERIV_BUILDERS))
def test_df_writes_only_inside_its_declared_params(name):
    assert_df_stays_inside_params(name, DERIV_BUILDERS[name]())


# --------------------------------------------------------------------------
# the spike, as a regression
# --------------------------------------------------------------------------
def _min_distance(edge, p):
    """Exact distance from a point to the curve (OCCT extrema)."""
    from build123d import Vertex

    return Vertex(p[0], p[1], 0.0).distance_to(edge)


@pytest.mark.parametrize("name", sorted(POLYGONS))
def test_the_emitted_spline_passes_through_the_solved_points(name, capsys):
    """The spike's verdict, pinned: `Spline` interpolates to ~1e-15 mm."""
    from build123d import Spline

    pts = POLYGONS[name]
    curve = Spline(*[(x, y, 0.0) for x, y in pts])
    worst = max(_min_distance(curve, p) for p in pts)
    with capsys.disabled():
        print(f"\nspline fidelity [{name}]: max through-point deviation "
              f"{worst:.3e} mm (tolerance {EMISSION_TOL_MM:.0e})")
    assert worst <= EMISSION_TOL_MM


def test_bezier_does_not_interpolate_which_is_why_it_stayed_the_fallback():
    """The rejected branch, asserted so nobody 'simplifies' into it."""
    from build123d import Bezier

    pts = POLYGONS["s_curve"]
    curve = Bezier(*[(x, y, 0.0) for x, y in pts])
    worst = max(_min_distance(curve, p) for p in pts)
    assert worst > 1.0, "Bezier suddenly interpolates; re-run the spike"


def test_a_free_end_spline_ignores_the_control_polygon_leg_unless_tangents_are_passed():
    """Why `end_tangent` is on the payload: the constraint is a promise the
    *emitter* has to keep. Measured drift up to 44.6 deg without `tangents=`."""
    from build123d import Spline, Vector

    pts = POLYGONS["closed_ish"]
    p3 = [(x, y, 0.0) for x, y in pts]
    leg0 = (pts[1][0] - pts[0][0], pts[1][1] - pts[0][1])
    legN = (pts[-1][0] - pts[-2][0], pts[-1][1] - pts[-2][1])
    want = math.degrees(math.atan2(leg0[1], leg0[0]))

    free = Spline(*p3).tangent_at(0.0)
    assert abs(math.degrees(math.atan2(free.Y, free.X)) - want) > 40.0

    pinned = Spline(*p3, tangents=(Vector(*leg0, 0.0), Vector(*legN, 0.0)))
    got = pinned.tangent_at(0.0)
    assert math.degrees(math.atan2(got.Y, got.X)) == pytest.approx(want, abs=1e-9)
    # and it still passes through every point
    assert max(_min_distance(pinned, p) for p in pts) <= EMISSION_TOL_MM


# --------------------------------------------------------------------------
# the solver side
# --------------------------------------------------------------------------
def test_a_spline_owns_no_parameters_and_no_residuals():
    """Its points are ordinary points, which is what makes every point
    constraint work on them for free."""
    sk = Sketch()
    for i, (x, y) in enumerate(POLYGONS["gentle"]):
        sk.point(f"p{i}", x, y)
    before = sk.n_par
    sk.spline("sp", ["p0", "p1", "p2", "p3"])
    assert sk.n_par == before and sk.n_res == 0 and sk.residuals == []


def test_ordinary_point_constraints_apply_to_control_points():
    spec = {
        "points": [{"name": "p0", "x": 0.0, "y": 0.0, "fixed": True},
                   {"name": "p1", "x": 10.0, "y": 4.0},
                   {"name": "p2", "x": 21.3, "y": 5.0},
                   {"name": "p3", "x": 33.4, "y": 2.8, "fixed": True}],
        "splines": [{"name": "sp", "points": ["p0", "p1", "p2", "p3"]}],
        "constraints": [
            {"type": "distance_x", "p": "p0", "q": "p1", "d": 11.37},
            {"type": "distance_y", "p": "p0", "q": "p1", "d": 4.19},
            {"type": "distance_x", "p": "p0", "q": "p2", "d": 22.74},
            {"type": "distance_y", "p": "p0", "q": "p2", "d": 5.63},
        ],
    }
    res = solve_sketch(spec)
    assert res["ok"] is True and res["dof"] == 0
    coords = res["splines"]["sp"]["coords"]
    assert coords[1]["x"] == pytest.approx(11.37, abs=1e-9)
    assert coords[2]["y"] == pytest.approx(5.63, abs=1e-9)
    assert res["splines"]["sp"]["degree"] == 3
    assert res["splines"]["sp"]["periodic"] is False


def test_a_spline_with_a_fixed_end_tangent_solves_and_the_tangent_holds():
    spec = {
        "points": [{"name": "p0", "x": 0.0, "y": 0.0, "fixed": True},
                   {"name": "p1", "x": 8.0, "y": 3.0},
                   {"name": "p2", "x": 19.4, "y": 7.3, "fixed": True},
                   {"name": "la", "x": 0.0, "y": 0.0, "fixed": True},
                   {"name": "lb", "x": 9.31, "y": 5.47, "fixed": True}],
        "lines": [{"name": "L", "p1": "la", "p2": "lb"}],
        "splines": [{"name": "sp", "points": ["p0", "p1", "p2"]}],
        "constraints": [
            {"type": "tangent", "a": "sp.start", "b": "L"},
            {"type": "distance", "p": "p0", "q": "p1", "d": 9.0},
        ],
    }
    res = solve_sketch(spec)
    assert res["ok"] is True and res["dof"] == 0
    p1 = res["points"]["p1"]
    # the first leg is parallel to the line, at the dimensioned length
    assert math.atan2(p1["y"], p1["x"]) == pytest.approx(
        math.atan2(5.47, 9.31), abs=1e-9)
    assert math.hypot(p1["x"], p1["y"]) == pytest.approx(9.0, abs=1e-9)
    # and the payload tells the emitter that this end needs `tangents=`
    sp = res["splines"]["sp"]
    assert sp["end_tangent"] == {"start": True, "end": False}
    assert sp["tangents"]["end"] is None
    got = sp["tangents"]["start"]
    assert math.atan2(got["y"], got["x"]) == pytest.approx(
        math.atan2(5.47, 9.31), abs=1e-9)


def test_the_end_tangent_residual_is_one_row_at_each_end():
    sk = _spline_sketch()
    assert sk.n_res == 2
    assert [r.kind for r in sk.residuals] == ["parallel", "parallel"]
    assert sk.con_types == ["tangent", "tangent"]


def test_a_spline_is_tangent_at_its_ends_not_as_a_whole():
    """On-curve constraints are out of MVP, and the error says so rather than
    quietly constraining something adjacent."""
    sk = _spline_sketch()
    with pytest.raises(SketchError, match=r"sp\.start"):
        sk.tangent("sp", "L")


def test_a_spline_needs_at_least_two_points():
    sk = Sketch()
    sk.point("p0", 0.0, 0.0)
    with pytest.raises(SketchError, match="at least 2"):
        sk.spline("sp", ["p0"])


def test_a_spline_names_an_unknown_point():
    sk = Sketch()
    sk.point("p0", 0.0, 0.0)
    with pytest.raises(SketchError, match="unknown point"):
        sk.spline("sp", ["p0", "nope"])
