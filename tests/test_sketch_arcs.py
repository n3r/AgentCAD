"""Arcs, virtual handles and the generalized constraints (PRD-009 slice 5).

The load-bearing idea is **virtual point handles**: `arc1.start` and
`arc1.end` are *names* that resolve through `PointRef` to derived coordinates
and a chain-ruled gradient over `{cx, cy, r, theta}`. They add **no parameters
and no residuals**, so `coincident {p: "arc1.end", q: "p3"}` is the same two
rows as any other coincidence and the whole v1 constraint vocabulary applies to
arc endpoints for free (design Decision 3, option b).

An arc therefore costs exactly **3 parameters** — `r`, `theta1`, `theta2` —
plus the 2 its centre point costs if that point is new. The rejected
alternative (free endpoint points tied back by residuals) costs 7 and puts
machinery the user never wrote into every conflict report.

Coordinates here are deliberately **non-round**. A tidy profile hides the
failures this feature actually produces: the emission-closure bug the design
measured (7.58e-7 mm at 6 decimals) does not reproduce on round numbers at
all, and a junction that agrees to 1e-9 on `(0, 10)` proves nothing about one
at `(34.95173, 6.35271)`.
"""

import math
import statistics
import time

import pytest
from fastapi.testclient import TestClient

from agentcad.core import tools_sketch
from agentcad.core.tools import ToolRegistry, build_registry
from agentcad.server.app import create_app
from agentcad.toolkit.sketch import Sketch, SketchError, solve_sketch

from .conftest import make_test_service
from .test_sketch_jacobian import (assert_df_matches_central_difference,
                                   assert_df_stays_inside_params)


# --------------------------------------------------------------------------
# derivative coverage — the highest-value test in the plan, extended to the
# residual kinds this slice adds (`symmetric`, `equal_length`) and to every v1
# residual now evaluated through an arc's virtual handles.
# --------------------------------------------------------------------------
def _arc_sketch() -> Sketch:
    """Arcs everywhere: virtual handles feeding v1 residuals, arc tangency."""
    sk = Sketch()
    sk.point("c1", 0.0, 0.0)
    sk.point("c2", 31.7, 4.3)
    sk.point("c3", 9.1, 26.4)
    sk.point("j", 7.3, 9.6)
    sk.point("la", -19.4, 12.1)
    sk.point("lb", 24.6, 14.7)
    sk.line("L", "la", "lb")
    sk.arc("a1", "c1", 9.4, 12.0, 103.0)
    sk.arc("a2", "c2", 6.2, 191.0, 297.0)
    sk.circle("C", "c3", 11.3)
    sk.coincident("j", "a1.end")            # chain rule through {cx,cy,r,th}
    sk.distance("a1.start", "a2.end", 20.5)
    sk.distance_x("a1.start", "c2", 12.0)
    sk.distance_y("a2.start", "j", 3.0)
    sk.point_on_line("a2.start", "L")
    sk.point_on_circle("a1.start", "C")
    sk.midpoint("j", "L")
    sk.radius("a1", 9.4)
    sk.equal_radius("a1", "a2")
    sk.concentric("a1", "C")
    sk.tangent("L", "a1")
    sk.tangent("a1", "a2", kind="external")
    sk.tangent("a2", "a1", kind="internal")
    sk.tangent("L", "a2", at="j")           # the 3-row `at` form, on an arc
    return sk


def _new_constraint_sketch() -> Sketch:
    """`symmetric` (point pair and line pair) and `equal_length`."""
    sk = Sketch()
    sk.point("ax", 6.7, 0.0)
    sk.point("ay", 6.7, 21.3)
    sk.point("p", 2.3, 5.4)
    sk.point("q", 11.9, 6.1)
    sk.point("r1", 1.1, 14.2)
    sk.point("r2", 4.7, 18.8)
    sk.point("s1", 12.4, 13.9)
    sk.point("s2", 9.3, 19.4)
    sk.line("axis", "ax", "ay")
    sk.line("lr", "r1", "r2")
    sk.line("ls", "s1", "s2")
    sk.symmetric("p", "q", "axis")
    sk.symmetric("lr", "ls", "axis")
    sk.equal_length("lr", "ls")
    return sk


DERIV_BUILDERS = {
    "arcs": _arc_sketch,
    "new_constraints": _new_constraint_sketch,
}


@pytest.mark.parametrize("name", sorted(DERIV_BUILDERS))
def test_every_df_matches_a_central_difference_of_its_own_f(name):
    assert_df_matches_central_difference(name, DERIV_BUILDERS[name]())


@pytest.mark.parametrize("name", sorted(DERIV_BUILDERS))
def test_df_writes_only_inside_its_declared_params(name):
    assert_df_stays_inside_params(name, DERIV_BUILDERS[name]())


# --------------------------------------------------------------------------
# parametrization
# --------------------------------------------------------------------------
def test_an_arc_costs_three_parameters_plus_its_centre():
    """Never 7 (design Decision 3): the endpoints are derived, not free."""
    sk = Sketch()
    sk.point("c", 1.7, 2.9)
    sk.arc("a", "c", 8.4, 15.0, 95.0)
    assert sk.n_par == 5                       # cx, cy + r, theta1, theta2
    assert sk.slot_owner == ["c", "c", "a", "a", "a"]

    fixed = Sketch()
    fixed.point("c", 1.7, 2.9, fixed=True)
    fixed.arc("a", "c", 8.4, 15.0, 95.0)
    assert fixed.n_par == 3

    pinned = Sketch()
    pinned.point("c", 1.7, 2.9, fixed=True)
    pinned.arc("a", "c", 8.4, 15.0, 95.0, fixed_r=True)
    assert pinned.n_par == 2                   # the two angles only


def test_an_arc_adds_no_residuals_of_its_own():
    sk = Sketch()
    sk.point("c", 0.0, 0.0)
    sk.arc("a", "c", 8.4, 15.0, 95.0)
    assert sk.residuals == [] and sk.n_res == 0


def test_virtual_handles_resolve_to_the_derived_endpoint():
    sk = Sketch()
    sk.point("c", 3.7, -1.9)
    sk.arc("a", "c", 8.4, 30.0, 120.0)
    v = sk.initial_vector()
    sx, sy = sk._refs["a.start"].value(v)
    assert (sx, sy) == pytest.approx(
        (3.7 + 8.4 * math.cos(math.radians(30.0)),
         -1.9 + 8.4 * math.sin(math.radians(30.0))), abs=1e-12)
    assert sk._refs["a.center"].value(v) == pytest.approx((3.7, -1.9))


def test_an_unknown_handle_names_what_it_could_not_resolve():
    sk = Sketch()
    sk.point("c", 0.0, 0.0)
    sk.arc("a", "c", 8.4, 0.0, 90.0)
    with pytest.raises(SketchError, match="a.middle"):
        sk.coincident("c", "a.middle")


def test_entity_names_may_not_contain_a_dot():
    """The sub-entity/handle namespace is reserved, never a silent collision."""
    sk = Sketch()
    with pytest.raises(SketchError, match="reserved"):
        sk.point("slot1.arc_a", 0.0, 0.0)
    sk.point("c", 0.0, 0.0)
    with pytest.raises(SketchError, match="reserved"):
        sk.arc("a1.start", "c", 5.0, 0.0, 90.0)


def test_arc_and_circle_names_share_one_namespace():
    sk = Sketch()
    sk.point("c", 0.0, 0.0)
    sk.circle("x", "c", 5.0)
    with pytest.raises(SketchError, match="duplicate"):
        sk.arc("x", "c", 5.0, 0.0, 90.0)


# --------------------------------------------------------------------------
# angles: degrees in the spec, radians inside, normalized on output only
# --------------------------------------------------------------------------
def test_angles_are_degrees_in_the_spec_and_radians_inside():
    sk = Sketch()
    sk.point("c", 0.0, 0.0)
    sk.arc("a", "c", 5.0, 90.0, 270.0)
    v = sk.initial_vector()
    assert v[sk.arcs["a"].i1] == pytest.approx(math.pi / 2)
    assert v[sk.arcs["a"].i2] == pytest.approx(3 * math.pi / 2)


def test_the_sweep_survives_output_normalization():
    """theta is never wrapped mid-solve; only the reported start is normalized,
    and the reported end keeps the full sweep (a wrapped parameter is a
    Jacobian discontinuity and is how an arc jumps the long way round)."""
    spec = {"points": [{"name": "c", "x": 0.0, "y": 0.0, "fixed": True}],
            "arcs": [{"name": "a", "center": "c", "r": 7.3,
                      "start_deg": -10.0, "end_deg": 20.0, "fixed_r": True}],
            "constraints": []}
    out = solve_sketch(spec)["arcs"]["a"]
    assert out["start_deg"] == pytest.approx(350.0)
    assert out["end_deg"] == pytest.approx(380.0)
    assert out["end_deg"] - out["start_deg"] == pytest.approx(30.0)


# --------------------------------------------------------------------------
# tangency
# --------------------------------------------------------------------------
def _fillet_spec() -> dict:
    """line -> arc -> line, both junctions tangent. Non-round throughout."""
    return {
        "points": [
            {"name": "o", "x": 0.0, "y": 0.0, "fixed": True},
            {"name": "j1", "x": 33.0, "y": 0.4},          # line A -> arc
            {"name": "ctr", "x": 34.0, "y": 6.0},
            {"name": "j2", "x": 41.0, "y": 6.1},          # arc -> line B
            {"name": "top", "x": 41.37, "y": 27.9, "fixed": True},
        ],
        "lines": [{"name": "A", "p1": "o", "p2": "j1"},
                  {"name": "B", "p1": "j2", "p2": "top"}],
        "arcs": [{"name": "f", "center": "ctr", "r": 6.0,
                  "start_deg": 273.0, "end_deg": 4.0}],
        "constraints": [
            {"type": "horizontal", "ln": "A"},
            {"type": "vertical", "ln": "B"},
            {"type": "radius", "c": "f", "r": 6.3527},
            {"type": "coincident", "p": "j1", "q": "f.start"},
            {"type": "coincident", "p": "j2", "q": "f.end"},
            {"type": "tangent", "a": "A", "b": "f"},
            {"type": "tangent", "a": "B", "b": "f"},
        ],
    }


def test_a_line_arc_line_fillet_solves_and_is_analytically_tangent():
    res = solve_sketch(_fillet_spec())
    assert res["ok"] is True, res["diagnostics"]
    assert res["dof"] == 0
    arc = res["arcs"]["f"]
    r = arc["r"]
    assert r == pytest.approx(6.3527, abs=1e-9)

    # tangency, asserted analytically rather than trusted: the centre sits
    # exactly `r` from each line, and each junction is the shared point.
    assert abs(arc["cy"]) == pytest.approx(r, abs=1e-9)          # line A: y = 0
    assert abs(arc["cx"] - 41.37) == pytest.approx(r, abs=1e-9)  # line B: x = 41.37
    j1, j2 = res["points"]["j1"], res["points"]["j2"]
    assert (j1["x"], j1["y"]) == pytest.approx(
        (arc["start"]["x"], arc["start"]["y"]), abs=1e-9)
    assert (j2["x"], j2["y"]) == pytest.approx(
        (arc["end"]["x"], arc["end"]["y"]), abs=1e-9)
    # And the junctions are where a hand calculation puts them. This assertion
    # used to hold only to ~1e-4 mm, because `dist(centre, line) - r` is
    # quadratically flat in the arc's angle at a tangency and left ~4e-6 rad of
    # genuine slack (measured: the junction landed 1.7e-5 mm along the line
    # from the ideal tangency point, with both residuals at 1e-11). Since the
    # tangency fix carried over from slice 10 the junction of a
    # coincident-tied chain compiles to a **direction** residual, which is
    # first-order there: this now lands exactly (measured 0.0 mm, nfev 17 -> 4,
    # max_residual 2.3e-11 -> 1.8e-16). The tolerance is left loose on purpose
    # — the assertion is about the geometry, not about the conditioning, and
    # `tests/test_sketch_tangent_direction.py` owns the latter.
    #
    # Slice 7's emitter rule is unchanged either way: **anchor arcs on the
    # shared solved endpoint**, never recompute an endpoint from centre +
    # radius + angle.
    assert res["max_residual"] < 1e-9
    assert (j1["x"], j1["y"]) == pytest.approx((41.37 - r, 0.0), abs=1e-4)
    assert (j2["x"], j2["y"]) == pytest.approx((41.37, r), abs=1e-4)


def test_both_chain_idioms_are_exact_since_the_junction_fix():
    """Both ways of writing a junction are now first-order exact.

    This test used to assert the opposite, and the change is the point.
    `_fillet_spec` writes the GUI's idiom — a junction *point* tied to the
    handle by `coincident` — which compiled to `dist(centre, line) − r`,
    quadratically flat in the arc's angle: 17 evaluations, `max_residual`
    2.3e-11, the junction 1.7e-5 mm off, and (slice 10, on a shorter chain)
    a rank count that reported the tangency as redundant. Writing the line's
    endpoint **as** the handle used the perpendicular form and was exact in 5.

    Since the fix carried over from slice 10 a coincident-tied junction
    compiles to the direction residual, and the two idioms agree: 4 and 5
    evaluations, both exact. The rank/DOF half of that fix lives in
    `tests/test_sketch_tangent_direction.py`.
    """
    handles = {
        "points": [{"name": "o", "x": 0.0, "y": 0.0, "fixed": True},
                   {"name": "ctr", "x": 34.0, "y": 6.0},
                   {"name": "top", "x": 41.37, "y": 27.9, "fixed": True}],
        "arcs": [{"name": "f", "center": "ctr", "r": 6.0,
                  "start_deg": 273.0, "end_deg": 4.0}],
        "lines": [{"name": "A", "p1": "o", "p2": "f.start"},
                  {"name": "B", "p1": "f.end", "p2": "top"}],
        "constraints": [
            {"type": "horizontal", "ln": "A"},
            {"type": "vertical", "ln": "B"},
            {"type": "radius", "c": "f", "r": 6.3527},
            {"type": "tangent", "a": "A", "b": "f"},
            {"type": "tangent", "a": "B", "b": "f"},
        ],
    }
    direct = solve_sketch(handles)
    via_point = solve_sketch(_fillet_spec())
    assert direct["ok"] and direct["dof"] == 0
    assert via_point["ok"] and via_point["dof"] == 0
    r = direct["arcs"]["f"]["r"]
    # exact, not 1.7e-5 away — now from either idiom
    assert direct["arcs"]["f"]["start"]["x"] == pytest.approx(41.37 - r, abs=1e-12)
    assert via_point["points"]["j1"]["x"] == pytest.approx(
        41.37 - via_point["arcs"]["f"]["r"], abs=1e-12)
    assert direct["max_residual"] < 1e-15
    assert via_point["max_residual"] < 1e-15
    # and the coincident-tied form no longer costs 3x the evaluations
    assert via_point["nfev"] <= direct["nfev"] + 1


@pytest.mark.parametrize("kind,sep", [("external", 15.9), ("internal", 3.3)])
def test_arc_arc_tangency(kind, sep):
    """d(c1, c2) == r1 + r2 externally, |r1 - r2| internally."""
    spec = {
        "points": [{"name": "c1", "x": 0.0, "y": 0.0, "fixed": True},
                   {"name": "c2", "x": 14.0, "y": 3.0}],
        "arcs": [{"name": "a1", "center": "c1", "r": 9.6, "start_deg": 0.0,
                  "end_deg": 140.0, "fixed_r": True},
                 {"name": "a2", "center": "c2", "r": 6.3, "start_deg": 180.0,
                  "end_deg": 320.0, "fixed_r": True}],
        "constraints": [
            {"type": "tangent", "a": "a1", "b": "a2", "kind": kind},
            {"type": "distance_y", "p": "c1", "q": "c2", "d": 3.0},
        ],
    }
    res = solve_sketch(spec)
    assert res["ok"] is True, res["diagnostics"]
    c2 = res["points"]["c2"]
    d = math.hypot(c2["x"], c2["y"])
    want = 9.6 + 6.3 if kind == "external" else 9.6 - 6.3
    assert d == pytest.approx(want, abs=1e-9)
    assert c2["y"] == pytest.approx(3.0, abs=1e-9)
    assert sep  # the parametrization documents the two separations


def test_tangent_dispatches_on_the_pair_and_refuses_what_it_cannot_do():
    sk = Sketch()
    sk.point("c", 0.0, 0.0)
    sk.point("a", -5.0, 4.0)
    sk.point("b", 9.0, 4.2)
    sk.line("l1", "a", "b")
    sk.line("l2", "c", "b")
    sk.arc("arc", "c", 5.0, 0.0, 90.0)
    sk.tangent("l1", "arc")            # line first
    sk.tangent("arc", "l1")            # curve first — same residual
    assert [r.kind for r in sk.residuals] == ["tangent_line_circle"] * 2
    assert sk.con_types == ["tangent", "tangent"]
    with pytest.raises(SketchError, match="line"):
        sk.tangent("l1", "l2")


def test_tangent_line_circle_and_tangent_circles_still_exist():
    """FR3: `tangent` is a new front door, not a rename. Both v1 names keep
    working, and they now accept arcs as well as circles."""
    sk = Sketch()
    sk.point("c1", 0.0, 0.0)
    sk.point("c2", 20.0, 1.0)
    sk.point("a", -5.0, 8.0)
    sk.point("b", 9.0, 8.2)
    sk.line("l", "a", "b")
    sk.arc("arc", "c1", 5.0, 0.0, 90.0)
    sk.circle("C", "c2", 4.0)
    sk.tangent_line_circle("l", "arc")
    sk.tangent_circles("arc", "C", "external")
    assert sk.con_types == ["tangent_line_circle", "tangent_circles"]


# --------------------------------------------------------------------------
# a closed chain of lines and arcs, joined on virtual handles
# --------------------------------------------------------------------------
def _stadium_spec() -> dict:
    """Two arcs and two lines, closed by `coincident` on virtual handles.

    Non-round on purpose: centres 37.421 mm apart, radius 8.7341 mm.
    """
    return {
        "points": [
            {"name": "cl", "x": 0.0, "y": 0.0, "fixed": True},
            {"name": "cr", "x": 37.421, "y": 0.0, "fixed": True},
            {"name": "tl", "x": 0.2, "y": 8.6},
            {"name": "tr", "x": 37.2, "y": 8.8},
            {"name": "br", "x": 37.3, "y": -8.7},
            {"name": "bl", "x": 0.1, "y": -8.9},
        ],
        "lines": [{"name": "top", "p1": "tl", "p2": "tr"},
                  {"name": "bot", "p1": "br", "p2": "bl"}],
        "arcs": [{"name": "L", "center": "cl", "r": 8.6, "start_deg": 91.0,
                  "end_deg": 269.0},
                 {"name": "R", "center": "cr", "r": 8.8, "start_deg": -89.0,
                  "end_deg": 92.0}],
        "constraints": [
            {"type": "radius", "c": "L", "r": 8.7341},
            {"type": "equal_radius", "c1": "L", "c2": "R"},
            {"type": "coincident", "p": "tl", "q": "L.start"},
            {"type": "coincident", "p": "tr", "q": "R.end"},
            {"type": "coincident", "p": "br", "q": "R.start"},
            {"type": "coincident", "p": "bl", "q": "L.end"},
            {"type": "tangent", "a": "top", "b": "L"},
            {"type": "tangent", "a": "top", "b": "R"},
            {"type": "tangent", "a": "bot", "b": "L"},
            {"type": "tangent", "a": "bot", "b": "R"},
        ],
    }


def test_a_closed_line_arc_chain_solves_with_zero_dof():
    res = solve_sketch(_stadium_spec())
    assert res["ok"] is True, res["diagnostics"]
    assert res["diagnostics"]["status"] == "well_constrained"
    assert res["dof"] == 0
    assert res["n_params"] == 14        # 4 free points + 3 per arc

    r = res["arcs"]["L"]["r"]
    assert r == pytest.approx(8.7341, abs=1e-9)
    assert res["arcs"]["R"]["r"] == pytest.approx(r, abs=1e-9)
    # the stadium's sides sit at +-r; both are non-round numbers
    assert res["points"]["tl"]["y"] == pytest.approx(r, abs=1e-9)
    assert res["points"]["bl"]["y"] == pytest.approx(-r, abs=1e-9)


def test_every_junction_of_the_chain_agrees_to_1e_9():
    """What `coincident` on a virtual handle has to buy: the arc's derived
    endpoint and the line's own endpoint are the same coordinate."""
    res = solve_sketch(_stadium_spec())
    pts, arcs = res["points"], res["arcs"]
    for point, (arc, end) in [("tl", ("L", "start")), ("tr", ("R", "end")),
                              ("br", ("R", "start")), ("bl", ("L", "end"))]:
        got, want = pts[point], arcs[arc][end]
        gap = math.hypot(got["x"] - want["x"], got["y"] - want["y"])
        assert gap < 1e-9, f"{point} vs {arc}.{end}: {gap:.3e} mm"


# --------------------------------------------------------------------------
# the new constraints
# --------------------------------------------------------------------------
def test_symmetric_on_a_point_pair():
    spec = {
        "points": [{"name": "ax", "x": 6.7, "y": 0.0, "fixed": True},
                   {"name": "ay", "x": 6.7, "y": 12.0, "fixed": True},
                   {"name": "p", "x": 2.3, "y": 5.4, "fixed": True},
                   {"name": "q", "x": 10.0, "y": 4.0}],
        "lines": [{"name": "axis", "p1": "ax", "p2": "ay"}],
        "constraints": [{"type": "symmetric", "a": "p", "b": "q",
                         "about": "axis"}],
    }
    res = solve_sketch(spec)
    assert res["ok"] is True and res["dof"] == 0
    assert res["points"]["q"]["x"] == pytest.approx(2 * 6.7 - 2.3, abs=1e-9)
    assert res["points"]["q"]["y"] == pytest.approx(5.4, abs=1e-9)


def test_symmetric_needs_both_rows():
    """The midpoint row alone looks right on a rectangle and is wrong on
    everything else: a mirrored pair must also be perpendicular to the axis."""
    sk = Sketch()
    sk.point("ax", 0.0, 0.0)
    sk.point("ay", 0.0, 10.0)
    sk.point("p", -3.1, 4.2)
    sk.point("q", 3.4, 5.9)
    sk.line("axis", "ax", "ay")
    sk.symmetric("p", "q", "axis")
    assert sk.n_res == 2
    assert [r.kind for r in sk.residuals] == ["symmetric"]


def test_symmetric_on_a_line_pair_about_a_construction_line():
    spec = {
        "points": [{"name": "ax", "x": 0.0, "y": 0.0, "fixed": True},
                   {"name": "ay", "x": 0.0, "y": 10.0, "fixed": True},
                   {"name": "r1", "x": -4.3, "y": 2.7, "fixed": True},
                   {"name": "r2", "x": -9.1, "y": 13.4, "fixed": True},
                   {"name": "s1", "x": 3.0, "y": 3.0},
                   {"name": "s2", "x": 8.0, "y": 13.0}],
        "lines": [{"name": "axis", "p1": "ax", "p2": "ay"},
                  {"name": "lr", "p1": "r1", "p2": "r2"},
                  {"name": "ls", "p1": "s1", "p2": "s2"}],
        "constraints": [{"type": "symmetric", "a": "lr", "b": "ls",
                         "about": "axis"}],
    }
    res = solve_sketch(spec)
    assert res["ok"] is True and res["dof"] == 0
    assert res["n_residuals"] == 4          # two point pairs, two rows each
    assert res["points"]["s1"]["x"] == pytest.approx(4.3, abs=1e-9)
    assert res["points"]["s1"]["y"] == pytest.approx(2.7, abs=1e-9)
    assert res["points"]["s2"]["x"] == pytest.approx(9.1, abs=1e-9)
    assert res["points"]["s2"]["y"] == pytest.approx(13.4, abs=1e-9)


def test_symmetric_refuses_a_mixed_pair():
    sk = Sketch()
    sk.point("ax", 0.0, 0.0)
    sk.point("ay", 0.0, 10.0)
    sk.point("p", 1.0, 1.0)
    sk.point("z", 5.0, 1.0)
    sk.line("axis", "ax", "ay")
    sk.line("l", "p", "z")
    with pytest.raises(SketchError, match="two points or two lines"):
        sk.symmetric("l", "p", "axis")


def test_equal_length():
    spec = {
        "points": [{"name": "a", "x": 0.0, "y": 0.0, "fixed": True},
                   {"name": "b", "x": 17.83, "y": 0.0, "fixed": True},
                   {"name": "c", "x": 0.0, "y": 4.0, "fixed": True},
                   {"name": "d", "x": 0.0, "y": 20.0}],
        "lines": [{"name": "ab", "p1": "a", "p2": "b"},
                  {"name": "cd", "p1": "c", "p2": "d"}],
        "constraints": [{"type": "equal_length", "l1": "ab", "l2": "cd"},
                        {"type": "vertical", "ln": "cd"}],
    }
    res = solve_sketch(spec)
    assert res["ok"] is True and res["dof"] == 0
    assert res["points"]["d"]["y"] == pytest.approx(4.0 + 17.83, abs=1e-9)


def test_concentric_on_an_arc_and_a_circle():
    spec = {
        "points": [{"name": "c1", "x": 3.71, "y": -2.49, "fixed": True},
                   {"name": "c2", "x": 9.0, "y": 4.0}],
        "circles": [{"name": "C", "center": "c1", "r": 5.0, "fixed_r": True}],
        "arcs": [{"name": "a", "center": "c2", "r": 8.0, "start_deg": 0.0,
                  "end_deg": 210.0, "fixed_r": True}],
        "constraints": [{"type": "concentric", "a": "a", "b": "C"}],
    }
    res = solve_sketch(spec)
    assert res["ok"] is True
    assert res["n_residuals"] == 2
    assert res["points"]["c2"]["x"] == pytest.approx(3.71, abs=1e-9)
    assert res["points"]["c2"]["y"] == pytest.approx(-2.49, abs=1e-9)
    # the arc's two angles are still free, and the diagnostics say so by name
    assert res["dof"] == 2
    assert res["diagnostics"]["free_entities"] == ["a"]


def test_equal_radius_and_radius_work_on_arcs():
    spec = {
        "points": [{"name": "c1", "x": 0.0, "y": 0.0, "fixed": True},
                   {"name": "c2", "x": 30.0, "y": 0.0, "fixed": True}],
        "arcs": [{"name": "a1", "center": "c1", "r": 4.0, "start_deg": 0.0,
                  "end_deg": 90.0},
                 {"name": "a2", "center": "c2", "r": 9.0, "start_deg": 0.0,
                  "end_deg": 90.0}],
        "constraints": [{"type": "radius", "c": "a1", "r": 7.4813},
                        {"type": "equal_radius", "c1": "a1", "c2": "a2"}],
    }
    res = solve_sketch(spec)
    assert res["ok"] is True
    assert res["arcs"]["a1"]["r"] == pytest.approx(7.4813, abs=1e-9)
    assert res["arcs"]["a2"]["r"] == pytest.approx(7.4813, abs=1e-9)
    assert res["dof"] == 4                  # the four angles are still free
    assert set(res["diagnostics"]["free_entities"]) == {"a1", "a2"}


# --------------------------------------------------------------------------
# the 3-point authoring form
# --------------------------------------------------------------------------
def test_the_three_point_form_compiles_to_the_centre_form():
    """Authored 3-point, solved centre-parametrized, and the authoring is
    recorded — slice 7's emitter picks `ThreePointArc` off it."""
    # a quarter of the circle centred (2.5, -1.25) with r = 7.9
    cx, cy, r = 2.5, -1.25, 7.9
    pts = [(cx + r * math.cos(math.radians(t)), cy + r * math.sin(math.radians(t)))
           for t in (17.0, 62.0, 118.0)]
    spec = {
        "points": [],
        "arcs": [{"name": "a", "start": pts[0], "mid": pts[1], "end": pts[2]}],
        "constraints": [],
    }
    res = solve_sketch(spec)
    assert res["ok"] is True
    arc = res["arcs"]["a"]
    assert arc["authored"] == "three_point"
    assert (arc["cx"], arc["cy"]) == pytest.approx((cx, cy), abs=1e-9)
    assert arc["r"] == pytest.approx(r, abs=1e-9)
    assert arc["start_deg"] == pytest.approx(17.0, abs=1e-9)
    assert arc["end_deg"] == pytest.approx(118.0, abs=1e-9)
    assert (arc["start"]["x"], arc["start"]["y"]) == pytest.approx(pts[0], abs=1e-9)
    # its centre is a compiled sub-entity, in the reserved dotted namespace
    assert arc["center"] == "a.center"
    assert "a.center" in res["points"]


def test_a_three_point_arc_sweeping_the_other_way():
    cx, cy, r = -3.3, 4.8, 5.25
    pts = [(cx + r * math.cos(math.radians(t)), cy + r * math.sin(math.radians(t)))
           for t in (110.0, 40.0, -25.0)]
    res = solve_sketch({"points": [], "arcs": [
        {"name": "a", "start": pts[0], "mid": pts[1], "end": pts[2]}],
        "constraints": []})
    arc = res["arcs"]["a"]
    assert arc["start_deg"] == pytest.approx(110.0, abs=1e-9)
    assert arc["end_deg"] == pytest.approx(-25.0, abs=1e-9)   # clockwise sweep


def test_three_collinear_points_are_not_an_arc():
    with pytest.raises(SketchError, match="collinear"):
        solve_sketch({"points": [], "arcs": [
            {"name": "a", "start": [0.0, 0.0], "mid": [4.1, 4.1],
             "end": [9.3, 9.3]}], "constraints": []})


# --------------------------------------------------------------------------
# `initial` on arcs, and the plumbing
# --------------------------------------------------------------------------
def test_initial_seeds_arc_parameters_and_selects_the_branch():
    """Two mirror solutions for a tangent arc; `initial` picks one."""
    def spec(seed_deg):
        return {
            "points": [{"name": "c", "x": 0.0, "y": 0.0, "fixed": True},
                       {"name": "p", "x": 5.0, "y": 5.0}],
            "arcs": [{"name": "a", "center": "c", "r": 7.5, "start_deg": 0.0,
                      "end_deg": 90.0, "fixed_r": True}],
            "constraints": [{"type": "coincident", "p": "p", "q": "a.start"},
                            {"type": "distance_x", "p": "c", "q": "p",
                             "d": 4.2}],
            # `initial` must cover every free entity or it degrades to a cold
            # start, so the point is seeded too — only the arc's angle differs
            "initial": {"points": {"p": {"x": 5.0, "y": 5.0}},
                        "arcs": {"a": {"start_deg": seed_deg,
                                       "end_deg": 90.0}}},
        }
    up = solve_sketch(spec(40.0))
    down = solve_sketch(spec(-40.0))
    assert up["ok"] and down["ok"]
    assert up["warm_started"] is True
    assert up["points"]["p"]["y"] > 0 > down["points"]["p"]["y"]
    assert up["points"]["p"]["x"] == pytest.approx(4.2, abs=1e-9)


def test_initial_naming_an_unknown_arc_raises():
    with pytest.raises(SketchError, match="nope"):
        solve_sketch({"points": [{"name": "c", "x": 0, "y": 0, "fixed": True}],
                      "arcs": [{"name": "a", "center": "c", "r": 5.0,
                                "start_deg": 0.0, "end_deg": 90.0}],
                      "constraints": [],
                      "initial": {"arcs": {"nope": {"r": 5.0}}}})


def test_a_partial_arc_initial_degrades_to_a_cold_start():
    res = solve_sketch({
        "points": [{"name": "c", "x": 0, "y": 0, "fixed": True}],
        "arcs": [{"name": "a", "center": "c", "r": 5.0, "start_deg": 0.0,
                  "end_deg": 90.0}],
        "constraints": [],
        "initial": {"arcs": {"a": {"r": 6.0}}}})     # no angles
    assert res["ok"] is True
    assert res["warm_started"] is False
    assert res["warnings"][0]["code"] == "initial_incomplete"
    assert res["warnings"][0]["entities"] == ["a"]
    assert res["arcs"]["a"]["r"] == pytest.approx(5.0)


def test_the_tool_accepts_arcs_and_the_new_constraints():
    registry = ToolRegistry()
    tools_sketch.register(registry, None)
    spec = _fillet_spec()
    res = registry.get("solve_sketch").handler(
        entities={"points": spec["points"], "lines": spec["lines"],
                  "arcs": spec["arcs"]},
        constraints=spec["constraints"])
    assert res["ok"] is True
    assert res["arcs"]["f"]["r"] == pytest.approx(6.3527, abs=1e-9)


@pytest.mark.integration
def test_arcs_reach_the_solver_through_the_route(tmp_path):
    service = make_test_service(tmp_path / "projects", None)
    client = TestClient(create_app(service, build_registry(service)),
                        base_url="http://127.0.0.1")
    spec = _fillet_spec()
    body = {"entities": {"points": spec["points"], "lines": spec["lines"],
                         "arcs": spec["arcs"]},
            "constraints": spec["constraints"]}
    out = client.post("/api/sketch/solve", json=body).json()
    assert out["ok"] is True
    assert out["arcs"]["f"]["start"]["y"] == pytest.approx(0.0, abs=1e-9)


# --------------------------------------------------------------------------
# FR6 with arcs in the sketch
# --------------------------------------------------------------------------
FR6_WARM_MS = 16.0


def _half_arc_ring(n_pair: int = 25) -> dict:
    """`n_pair` arcs alternating with `n_pair` lines, closed end to end.

    50 entities, half of them arcs. Every line runs **between virtual
    handles** (`a3.end` -> `a4.start`), so the only free points are the arc
    centres: 5 parameters per unit (centre 2, arc 3) against 5 constraint
    rows, exactly constrained. Every junction is a tangency, which makes this
    the *worst-conditioned* realistic profile of its size — see the note on
    the assertion below.
    """
    points, lines, arcs, cons = [], [], [], []
    ring = 90.0
    for i in range(n_pair):
        th = 2 * math.pi * i / n_pair
        cx, cy = ring * math.cos(th), ring * math.sin(th)
        points.append({"name": f"c{i}", "x": cx, "y": cy,
                       **({"fixed": True} if i == 0 else {})})
        arcs.append({"name": f"a{i}", "center": f"c{i}", "r": 7.0,
                     "start_deg": math.degrees(th) + 150.0,
                     "end_deg": math.degrees(th) + 30.0})
        cons.append({"type": "radius", "c": f"a{i}", "r": 7.0})
        if i:
            cons.append({"type": "distance_x", "p": "c0", "q": f"c{i}",
                         "d": cx - ring})
            cons.append({"type": "distance_y", "p": "c0", "q": f"c{i}",
                         "d": cy})
    for i in range(n_pair):
        j = (i + 1) % n_pair
        lines.append({"name": f"l{i}", "p1": f"a{i}.end", "p2": f"a{j}.start"})
        cons.append({"type": "tangent", "a": f"l{i}", "b": f"a{i}"})
        cons.append({"type": "tangent", "a": f"l{i}", "b": f"a{j}"})
    return {"points": points, "lines": lines, "arcs": arcs, "constraints": cons}


@pytest.mark.slow
def test_a_50_entity_sketch_that_is_half_arcs_still_clears_the_drag_budget(capsys):
    """FR6, with the entity type slice 5 added: warm-drag p50 <= 16 ms.

    Measured on an M1 Max at ~11 ms, against ~0.8 ms for the 50-segment
    all-line staircase in `test_sketch_bench.py`. The difference is *not* the
    Jacobian: it is 7 warm iterations instead of 2 (tangency is quadratically
    flat in an arc's angle, so the last digits take several steps) times
    scipy's `tr_solver="exact"`, which factors a 123x123 matrix per iteration.
    Measured alternatives on this sketch, for whoever owns the drag budget in
    slice 8: `tr_solver="lsmr"` 5.3 ms (same nfev, same solution),
    `x_scale="jac"` no change.
    """
    spec = _half_arc_ring()
    first = solve_sketch(spec)
    assert first["ok"] is True and first["dof"] == 0, first["diagnostics"]
    seed = {"points": {n: {"x": p["x"], "y": p["y"]}
                       for n, p in first["points"].items()},
            "arcs": {n: {"r": a["r"], "start_deg": a["start_deg"],
                         "end_deg": a["end_deg"]}
                     for n, a in first["arcs"].items()}}
    seed["points"]["c3"]["x"] += 0.4          # a drag frame's worth of nudge
    warm = dict(spec, initial=seed)

    times = []
    for _ in range(9):
        t0 = time.perf_counter()
        res = solve_sketch(warm)
        times.append((time.perf_counter() - t0) * 1e3)
    p50 = statistics.median(times)
    with capsys.disabled():
        print(f"\n50-entity half-arc ring: n_params={res['n_params']} "
              f"n_residuals={res['n_residuals']} nfev={res['nfev']} "
              f"warm-drag p50={p50:.2f} ms (max {max(times):.2f} ms, budget "
              f"{FR6_WARM_MS} ms), max_residual={res['max_residual']:.2e}")
    assert res["ok"] is True, res["diagnostics"]
    assert res["warm_started"] is True
    assert p50 <= FR6_WARM_MS, f"warm-drag p50 {p50:.2f} ms over FR6's budget"
