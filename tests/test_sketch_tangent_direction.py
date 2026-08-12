"""Tangency at a junction the sketch already pins: the direction residual.

Slice 6 measured that `dist(centre, line) - r` is second-order flat at a
junction two curves share **structurally** (a slot's side built on its cap's
handle) and fixed that case with the perpendicular form. Slice 10 measured the
*same* collapse for the junction idiom the GUI writes — a junction point tied
to an arc's handle by a `coincident` constraint — where it reported
`over-constrained (1)` against a constraint that was doing real work. This file
is that fix's regression: the residual kinds each form compiles to, the rank
and DOF on the exact configuration slice 10 measured, and the derivative of the
new kind against a central difference of its own `f`.
"""

import math

import numpy as np
import pytest

from agentcad.toolkit.sketch import (RANK_TOL_REL, Sketch, parse_sketch,
                                     solve_sketch)

from .test_sketch_jacobian import (assert_df_matches_central_difference,
                                   assert_df_stays_inside_params)


# --------------------------------------------------------------------------
# the two configurations, as the GUI writes them
# --------------------------------------------------------------------------
def chain_spec(*, off_deg: float = 0.0, tangent_first: bool = False) -> dict:
    """Slice 10's line -> tangent-arc chain (the `ArcT` tool's output).

    `p1` fixed, a line to `p2`, an arc whose `start` handle is `coincident`
    with `p2`, and `tangent {ln1, a1}`. 7 parameters, 3 rows. `off_deg` seeds
    the arc away from tangency, which is how slice 10 showed the constraint
    does real work despite being invisible to the rank count.
    """
    cons = [
        {"type": "coincident", "p": "a1.start", "q": "p2"},
        {"type": "tangent", "a": "ln1", "b": "a1"},
    ]
    return {
        "points": [{"name": "p1", "x": 0.0, "y": 0.0, "fixed": True},
                   {"name": "p2", "x": 30.0, "y": 0.0},
                   {"name": "p3", "x": 30.0, "y": 12.0}],
        "lines": [{"name": "ln1", "p1": "p1", "p2": "p2"}],
        "arcs": [{"name": "a1", "center": "p3", "r": 12.0,
                  "start_deg": -90.0 + off_deg, "end_deg": 10.0}],
        "constraints": cons[::-1] if tangent_first else cons,
    }


def arc_arc_spec() -> dict:
    """Two arcs meeting at a coincident junction, tangent there.

    The distance form `d(c1,c2) - (r1 + r2)` is *exactly* dependent on the
    coincidence rows here, not merely small.
    """
    return {
        "points": [{"name": "c1", "x": 0.0, "y": 0.0, "fixed": True},
                   {"name": "c2", "x": 16.0, "y": 0.0}],
        "arcs": [{"name": "a1", "center": "c1", "r": 10.0, "start_deg": -60.0,
                  "end_deg": 0.0, "fixed_r": True},
                 {"name": "a2", "center": "c2", "r": 6.0, "start_deg": 180.0,
                  "end_deg": 250.0, "fixed_r": True}],
        "constraints": [
            {"type": "coincident", "p": "a1.end", "q": "a2.start"},
            {"type": "tangent", "a": "a1", "b": "a2", "kind": "external"},
        ],
    }


# --------------------------------------------------------------------------
# derivative coverage for the new kind (the non-negotiable one)
# --------------------------------------------------------------------------
def _line_arc_junction_sketch() -> Sketch:
    sk = Sketch()
    sk.point("p1", 0.0, 0.0)
    sk.point("p2", 30.0, 0.0)
    sk.point("p3", 30.0, 12.0)
    sk.line("ln1", "p1", "p2")
    sk.arc("a1", "p3", 12.0, -90.0, 10.0)
    sk.coincident("a1.start", "p2")
    sk.tangent("ln1", "a1")
    return sk


def _arc_arc_junction_sketch() -> Sketch:
    sk = Sketch()
    sk.point("c1", 0.0, 0.0)
    sk.point("c2", 16.0, 0.0)
    sk.arc("a1", "c1", 10.0, -60.0, 0.0)
    sk.arc("a2", "c2", 6.0, 180.0, 250.0)
    sk.coincident("a1.end", "a2.start")
    sk.tangent("a1", "a2")
    return sk


DERIV_BUILDERS = {
    "tangent_dir_line_arc": _line_arc_junction_sketch,
    "tangent_dir_arc_arc": _arc_arc_junction_sketch,
}


@pytest.mark.parametrize("name", sorted(DERIV_BUILDERS))
def test_every_df_matches_a_central_difference_of_its_own_f(name):
    assert_df_matches_central_difference(name, DERIV_BUILDERS[name]())


@pytest.mark.parametrize("name", sorted(DERIV_BUILDERS))
def test_df_writes_only_inside_its_declared_params(name):
    assert_df_stays_inside_params(name, DERIV_BUILDERS[name]())


def test_the_direction_residual_touches_only_the_angle_of_the_arc():
    """Why it fixes the rank collapse, stated as a test.

    The arc side of the residual is `(-sin theta, cos theta)`: normalizing the
    derivative of `c + r e(theta)` drops the centre and the radius, so the row
    is no longer a function of the same quantities the coincidence rows pin.
    """
    sk = _line_arc_junction_sketch()
    row = [r for r in sk.residuals if r.kind == "tangent_dir"][0]
    arc = sk.arcs["a1"]
    assert arc.i1 in row.params            # theta1 (the `.start` handle)
    assert arc.i2 not in row.params        # not theta2
    assert arc.ir not in row.params        # and not the radius
    assert set(sk._refs["p3"].params).isdisjoint(row.params)   # nor the centre


# --------------------------------------------------------------------------
# the rank / DOF regression: the exact configuration slice 10 measured
# --------------------------------------------------------------------------
def _singular_values(spec: dict):
    """Singular values of J at the solved point, with the rank tolerance."""
    res = solve_sketch(spec)
    sk = parse_sketch(spec)
    v = sk.initial_vector()
    for name, p in sk.points.items():
        if not p.fixed:
            v[p.ix] = res["points"][name]["x"]
            v[p.ix + 1] = res["points"][name]["y"]
    for name, a in sk.arcs.items():
        if not a.fixed_r:
            v[a.ir] = res["arcs"][name]["r"]
        v[a.i1] = math.radians(res["arcs"][name]["start_deg"])
        v[a.i2] = math.radians(res["arcs"][name]["end_deg"])
    J = sk.make_functions()[1](v)[:sk.n_res]
    s = np.linalg.svd(J, compute_uv=False)
    return res, s, max(J.shape) * s[0] * RANK_TOL_REL


def test_a_coincident_tangent_junction_is_not_reported_over_constrained():
    """**The regression.** Measured before the fix, at exact tangency:

    ```
    sv 1.208e+01  2.449e+00  1.838e-16   rank tol 8.46e-09  -> rank 2, dof 5
    ```

    so the chip read `over-constrained (1)` and named `tangent`, while the same
    constraint reached tangency from an off-tangent seed. With the direction
    residual the third singular value is **1.20e-01** — nine orders of
    magnitude clear of the tolerance — and the DOF count is 4, which is the
    hand count (the arc's free end angle, its radius, and the line's free
    length and direction, less the one the tangency removes).
    """
    res, s, tol = _singular_values(chain_spec())
    assert [r.kind for r in parse_sketch(chain_spec()).residuals] == [
        "coincident", "tangent_dir"]
    assert res["n_params"] == 7 and res["n_residuals"] == 3
    assert res["rank"] == 3
    assert res["dof"] == 4
    assert res["diagnostics"]["status"] == "under_constrained"
    assert res["diagnostics"]["redundant"] == []
    assert res["diagnostics"]["conflicting"] == []
    # the number that moved: 1.84e-16 -> ~1.2e-01 against a ~8.5e-09 tolerance
    assert s[-1] > 1e-2
    assert s[-1] > 1e6 * tol


def test_the_constraint_still_does_the_geometric_work_from_an_off_seed():
    """It was never a no-op — it was invisible to the rank count. Assert the
    geometry, not the residual: the centre sits exactly `r` from the line."""
    res = solve_sketch(chain_spec(off_deg=8.0))
    assert res["ok"] is True, res["diagnostics"]
    arc = res["arcs"]["a1"]
    p1, p2 = res["points"]["p1"], res["points"]["p2"]
    ux, uy = p2["x"] - p1["x"], p2["y"] - p1["y"]
    n = math.hypot(ux, uy)
    dist = abs((arc["cx"] - p1["x"]) * uy / n - (arc["cy"] - p1["y"]) * ux / n)
    assert dist == pytest.approx(arc["r"], abs=1e-8)


def test_declaring_the_tangency_before_the_coincidence_is_the_same_sketch():
    """A spec is a set, not a program: `parse_sketch` registers every
    coincidence before compiling anything, so the junction is seen either way.
    """
    a = parse_sketch(chain_spec())
    b = parse_sketch(chain_spec(tangent_first=True))
    assert sorted(r.kind for r in a.residuals) == sorted(
        r.kind for r in b.residuals) == ["coincident", "tangent_dir"]
    assert solve_sketch(chain_spec(tangent_first=True))["dof"] == 4


def test_two_arcs_joined_at_a_coincident_junction():
    """The arc-arc half. Before the fix the distance form was **exactly**
    dependent on the coincidence rows (sv 4.70e-16 -> rank 2, dof 4)."""
    res, s, tol = _singular_values(arc_arc_spec())
    assert [r.kind for r in parse_sketch(arc_arc_spec()).residuals] == [
        "coincident", "tangent_dir"]
    assert res["rank"] == 3 and res["dof"] == 3
    assert res["diagnostics"]["status"] == "under_constrained"
    assert res["diagnostics"]["redundant"] == []
    assert s[-1] > 1e-2 and s[-1] > 1e6 * tol
    # and the junction really is tangent: the two centres and the junction are
    # collinear for an external touch
    a1, a2 = res["arcs"]["a1"], res["arcs"]["a2"]
    j = a1["end"]
    assert (j["x"], j["y"]) == pytest.approx((a2["start"]["x"],
                                              a2["start"]["y"]), abs=1e-9)
    d = math.hypot(a1["cx"] - a2["cx"], a1["cy"] - a2["cy"])
    assert d == pytest.approx(a1["r"] + a2["r"], abs=1e-8)


# --------------------------------------------------------------------------
# what must NOT change
# --------------------------------------------------------------------------
def test_a_tangency_with_no_junction_keeps_the_v1_distance_residual():
    """FR3. A line tangent to a circle it does not touch by a coincidence is
    the v1 residual, unchanged — the new form applies only where the sketch
    already pins the junction."""
    sk = Sketch()
    sk.point("c", 0.0, 0.0)
    sk.point("a", -20.0, 12.0)
    sk.point("b", 20.0, 12.0)
    sk.line("l", "a", "b")
    sk.circle("C", "c", 10.0)
    sk.arc("A", "c", 10.0, 0.0, 90.0)
    sk.tangent("l", "C")
    sk.tangent("l", "A")           # an arc, but no shared junction
    assert [r.kind for r in sk.residuals] == ["tangent_line_circle"] * 2


def test_a_structural_junction_still_uses_the_perpendicular_form():
    """Slice 6's fix is not replaced. `(at - centre) . u` is this same
    residual scaled by `r`, and the slot tests are written against that name.
    """
    sk = Sketch()
    sk.point("c1", 0.0, 0.0)
    sk.point("c2", 40.0, 0.0)
    sk.slot("s1", "c1", "c2", 14.0)
    assert [r.kind for r in sk.residuals] == ["radius"] + [
        "tangent_point_perp"] * 4


def test_the_explicit_at_form_is_untouched():
    """`tangent {..., at}` names the tangency point and keeps its 3 rows."""
    sk = Sketch()
    sk.point("c", 0.0, 0.0)
    sk.point("t", 0.0, 10.0)
    sk.point("a", -20.0, 10.0)
    sk.point("b", 20.0, 10.0)
    sk.line("l", "a", "b")
    sk.arc("A", "c", 10.0, 0.0, 180.0)
    sk.tangent("l", "A", at="t")
    assert [r.kind for r in sk.residuals] == [
        "point_on_circle", "point_on_line", "tangent_point_perp"]
