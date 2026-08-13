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
import statistics
import time

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


# --------------------------------------------------------------------------
# the CLASS, not the third instance (review P1)
# --------------------------------------------------------------------------
# Slices 6 and 10 fixed two *instances* of one bug: a tangency compiled to its
# distance form at a junction the rest of the sketch already pins is
# second-order flat there, so its row falls into the span of the pinning rows.
# Both fixes keyed off a hardcoded handle list (`_handles_of`), which returns
# `()` for a circle and only `.start`/`.end` for an arc — so a junction pinned
# by `point_on_circle` (a line endpoint held on the curve) was invisible and
# the bug reopened a third time. The detector now reads the **incidence
# graph**: any constraint that ties a point to a curve pins a junction there.
#
# Measured before this fix, at the solution, on the six specs below (the
# `before` column is the same solver with the incidence graph removed from
# `_on_curve_handles`, which is exactly what the handle list saw):
#
# | configuration                          | rank      | dof     | blame       |
# |----------------------------------------|-----------|---------|-------------|
# | line+circle by `point_on_circle`        | 1/2 -> 2/2 | 3 -> 2 | `[tangent]` |
# | line+arc   by `point_on_circle`         | 1/2 -> 2/2 | 5 -> 4 | `[tangent]` |
# | circle+circle sharing that point        | 2/3 -> 3/3 | 2 -> 1 | `[tangent]` |
# | line+circle by `point_on_line`          | 2/3 -> 3/3 | 4 -> 3 | `[tangent]` |
# | line+circle by `midpoint`               | 3/4 -> 4/4 | 3 -> 2 | `[tangent]` |
# | line+circle by a `coincident` relay     | 3/4 -> 4/4 | 3 -> 2 | `[tangent]` |
#
# and `max_residual` reported 8.11e-11 / `ok: true` on a sketch whose true
# tangency error was 4.97e-05 mm (ratio 6.1e+05) — the distance residual is
# the *square* of the geometric error near tangency, so it is not a
# measurement of it. With the direction form the ratio is the radius: 10.0.

def on_circle_line_spec(*, curve: str = "circles", off: float = 0.0) -> dict:
    """A line tangent to a circle (or arc) whose junction is pinned by
    `point_on_circle` — the idiom an agent writes when the touch point is a
    point it already has, and the one `_handles_of` could never see."""
    entity = ({"name": "C", "center": "c", "r": 10.0, "fixed_r": True}
              if curve == "circles" else
              {"name": "C", "center": "c", "r": 10.0, "start_deg": -80.0,
               "end_deg": 80.0, "fixed_r": True})
    return {
        "points": [{"name": "c", "x": 0.0, "y": 0.0, "fixed": True},
                   {"name": "p", "x": 10.0 + off, "y": 0.0 + off},
                   {"name": "q", "x": 10.0, "y": 20.0}],
        curve: [entity],
        "lines": [{"name": "L", "p1": "p", "p2": "q"}],
        "constraints": [{"type": "point_on_circle", "p": "p", "c": "C"},
                        {"type": "tangent", "a": "L", "b": "C"}],
    }


def on_circle_circle_spec() -> dict:
    """Two circles tangent at a point both hold by `point_on_circle`."""
    return {
        "points": [{"name": "c1", "x": 0.0, "y": 0.0, "fixed": True},
                   {"name": "c2", "x": 25.0, "y": 0.0},
                   {"name": "p", "x": 10.0, "y": 0.0}],
        "circles": [{"name": "C1", "center": "c1", "r": 10.0, "fixed_r": True},
                    {"name": "C2", "center": "c2", "r": 15.0, "fixed_r": True}],
        "constraints": [{"type": "point_on_circle", "p": "p", "c": "C1"},
                        {"type": "point_on_circle", "p": "p", "c": "C2"},
                        {"type": "tangent", "a": "C1", "b": "C2"}],
    }


def on_line_spec() -> dict:
    """The other half of the class: the junction is held on the *line* by
    `point_on_line` rather than by being one of its endpoints."""
    return {
        "points": [{"name": "c", "x": 0.0, "y": 0.0, "fixed": True},
                   {"name": "t", "x": 10.0, "y": 0.0},
                   {"name": "p", "x": 10.0, "y": -14.0},
                   {"name": "q", "x": 10.0, "y": 20.0}],
        "circles": [{"name": "C", "center": "c", "r": 10.0, "fixed_r": True}],
        "lines": [{"name": "L", "p1": "p", "p2": "q"}],
        "constraints": [{"type": "point_on_circle", "p": "t", "c": "C"},
                        {"type": "point_on_line", "p": "t", "ln": "L"},
                        {"type": "tangent", "a": "L", "b": "C"}],
    }


def midpoint_spec() -> dict:
    """`midpoint` puts a point on a line just as surely as `point_on_line`
    does — a constraint kind the old detector had no idea about."""
    return {
        "points": [{"name": "c", "x": 0.0, "y": 0.0, "fixed": True},
                   {"name": "m", "x": 10.0, "y": 0.0},
                   {"name": "p", "x": 10.0, "y": -14.0},
                   {"name": "q", "x": 10.0, "y": 14.0}],
        "circles": [{"name": "C", "center": "c", "r": 10.0, "fixed_r": True}],
        "lines": [{"name": "L", "p1": "p", "p2": "q"}],
        "constraints": [{"type": "point_on_circle", "p": "m", "c": "C"},
                        {"type": "midpoint", "p": "m", "ln": "L"},
                        {"type": "tangent", "a": "L", "b": "C"}],
    }


def coincident_relay_spec() -> dict:
    """The junction reaches the curve through a `coincident` *chain*: the
    line's endpoint is coincident with a point that `point_on_circle` holds.
    Union-find plus incidence, which is why they must be one lookup."""
    spec = on_circle_line_spec()
    spec["points"].append({"name": "j", "x": 10.0, "y": 0.0})
    spec["lines"][0]["p1"] = "j"
    spec["constraints"].insert(1, {"type": "coincident", "p": "j", "q": "p"})
    return spec


CLASS_SPECS = {
    "line_circle_on_circle": lambda: on_circle_line_spec(),
    "line_arc_on_circle": lambda: on_circle_line_spec(curve="arcs"),
    "circle_circle_on_circle": on_circle_circle_spec,
    "line_circle_on_line": on_line_spec,
    "line_circle_midpoint": midpoint_spec,
    "line_circle_coincident_relay": coincident_relay_spec,
}
# The class tests that sweep `CLASS_SPECS` live at the **bottom** of this file,
# after every configuration has registered itself. `@parametrize` is evaluated
# when the decorator runs, so a spec added to the dict further down the module
# is silently outside it: 0143 added three and the two sweeps kept collecting
# six ids. `test_every_class_spec_is_parametrized` is the guard that says so.


def test_the_order_of_the_pinning_constraint_does_not_matter():
    """A spec is a set, not a program (the rule `note_coincidence` already
    follows): a `tangent` written before the `point_on_circle` that pins its
    junction sees the same junction."""
    spec = on_circle_line_spec()
    spec["constraints"] = spec["constraints"][::-1]
    assert [r.kind for r in parse_sketch(spec).residuals] == [
        "tangent_dir", "point_on_circle"]


def test_the_reported_max_residual_measures_the_geometric_error():
    """**`max_residual` lied.** With the distance form the residual near
    tangency is the *square* of the geometric error: measured `ok: true`,
    `max_residual` 1.76e-10 on a sketch whose touch point sat 7.9e-05 mm off
    the tangency condition. Assert the two agree, not that one is small: with
    the direction form the residual is the sine of the tangency error, so the
    geometric error is `r` times it — measured ratio **10.0**, against 4.5e+05
    for the distance form, whose residual is that error *squared*."""
    spec = on_circle_line_spec(off=1.7)
    spec["constraints"].append({"type": "distance", "p": "p", "q": "q",
                                "d": 20.0})
    res = solve_sketch(spec)
    assert res["ok"] is True, res["diagnostics"]
    p, q, c = res["points"]["p"], res["points"]["q"], res["points"]["c"]
    ux, uy = q["x"] - p["x"], q["y"] - p["y"]
    n = math.hypot(ux, uy)
    # the true condition: the radius to the touch point meets the line square
    true_err = abs((p["x"] - c["x"]) * ux + (p["y"] - c["y"]) * uy) / n
    assert true_err < 1e-6, true_err
    assert true_err <= 1e3 * max(res["max_residual"], 1e-12), (
        true_err, res["max_residual"])


def test_the_tangency_still_does_its_geometric_work_from_an_off_seed():
    """It was never a no-op — assert the geometry, not the residual."""
    res = solve_sketch(on_circle_line_spec(off=2.5))
    assert res["ok"] is True, res["diagnostics"]
    p, q, c = res["points"]["p"], res["points"]["q"], res["points"]["c"]
    ux, uy = q["x"] - p["x"], q["y"] - p["y"]
    n = math.hypot(ux, uy)
    dist = abs((c["x"] - p["x"]) * uy - (c["y"] - p["y"]) * ux) / n
    assert dist == pytest.approx(10.0, abs=1e-9)
    assert math.hypot(p["x"] - c["x"], p["y"] - c["y"]) == pytest.approx(
        10.0, abs=1e-9)


def test_every_constraint_type_is_classified_as_on_curve_or_not():
    """**The guard that keeps this from happening a fourth time.** The
    detector is driven by `ON_CURVE_ARGS`, and every constraint the spec
    front-end accepts must be in it or in the explicit "does not put a point
    on a curve" set — so a new constraint kind cannot be added without a
    decision about whether it pins a junction."""
    from agentcad.toolkit.sketch import (NOT_ON_CURVE, ON_CURVE_ARGS,
                                         constraint_types)
    known = constraint_types()
    assert set(ON_CURVE_ARGS) <= known
    assert NOT_ON_CURVE <= known
    assert set(ON_CURVE_ARGS) | NOT_ON_CURVE == known, sorted(
        known - set(ON_CURVE_ARGS) - NOT_ON_CURVE)
    assert not (set(ON_CURVE_ARGS) & NOT_ON_CURVE)


# derivative coverage for the radial tangent reference the class fix adds
def _radial_junction_sketch() -> Sketch:
    sk = Sketch()
    sk.point("c", 0.0, 0.0)
    sk.point("p", 9.6, 2.8)
    sk.point("q", 4.0, 21.0)
    sk.circle("C", "c", 10.0)
    sk.line("L", "p", "q")
    sk.point_on_circle("p", "C")
    sk.tangent("L", "C")
    return sk


def _radial_circle_circle_sketch() -> Sketch:
    sk = Sketch()
    sk.point("c1", 0.0, 0.0)
    sk.point("c2", 25.0, 1.0)
    sk.point("p", 9.6, 2.8)
    sk.circle("C1", "c1", 10.0)
    sk.circle("C2", "c2", 15.0)
    sk.point_on_circle("p", "C1")
    sk.point_on_circle("p", "C2")
    sk.tangent("C1", "C2")
    return sk


DERIV_BUILDERS["tangent_dir_radial_line"] = _radial_junction_sketch
DERIV_BUILDERS["tangent_dir_radial_circles"] = _radial_circle_circle_sketch


# --------------------------------------------------------------------------
# the FOURTH class: a junction pinned by rows that are not `coincident`
# and not on the `ON_CURVE_ARGS` table (review 2, finding C4)
# --------------------------------------------------------------------------
def dimensional_junction_spec(*, off: float = 0.0) -> dict:
    """The junction is pinned by two **dimensional** rows.

    `distance_x(p2, a1.start, 0)` + `distance_y(p2, a1.start, 0)` removes both
    degrees of freedom of the offset exactly as a `coincident` does, and neither
    row is a coincidence nor an incidence. The union-find never sees it.
    """
    return {
        "points": [{"name": "p1", "x": -10.0, "y": 10.0},
                   {"name": "p2", "x": 0.0 + off, "y": 10.0},
                   {"name": "ct", "x": 0.0, "y": 0.0, "fixed": True}],
        "arcs": [{"name": "a1", "center": "ct", "r": 10.0, "start_deg": 90.0,
                  "end_deg": 0.0, "fixed_r": True}],
        "lines": [{"name": "l1", "p1": "p1", "p2": "p2"}],
        "constraints": [
            {"type": "distance_x", "p": "p2", "q": "a1.start", "d": 0.0},
            {"type": "distance_y", "p": "p2", "q": "a1.start", "d": 0.0},
            {"type": "tangent", "a": "l1", "b": "a1"},
        ],
    }


def symmetric_junction_spec() -> dict:
    """The same, pinned by a `symmetric` about a fixed axis instead.

    `symmetric` puts two points on opposite sides of a line — with the mirror
    axis running *through* the arc handle the two rows pin the pair together,
    which is a third spelling again.
    """
    return {
        "points": [{"name": "p1", "x": -10.0, "y": 10.0},
                   {"name": "p2", "x": 0.0, "y": 10.0},
                   {"name": "ax1", "x": 0.0, "y": 10.0, "fixed": True},
                   {"name": "ax2", "x": 1.0, "y": 10.0, "fixed": True},
                   {"name": "ct", "x": 0.0, "y": 0.0, "fixed": True}],
        "arcs": [{"name": "a1", "center": "ct", "r": 10.0, "start_deg": 90.0,
                  "end_deg": 0.0, "fixed_r": True}],
        "lines": [{"name": "l1", "p1": "p1", "p2": "p2"},
                  {"name": "axis", "p1": "ax1", "p2": "ax2",
                   "construction": True}],
        "constraints": [
            {"type": "symmetric", "a": "p2", "b": "a1.start", "about": "axis"},
            {"type": "tangent", "a": "l1", "b": "a1"},
        ],
    }


def measured_on_curve_spec() -> dict:
    """The junction is held **on the circle** by a measurement, not by an
    `ON_CURVE_ARGS` constraint: `distance(p, c, 10)` + `radius(C, 10)` says
    "p is 10 from the centre and the circle's radius is 10", which is exactly
    `point_on_circle` written in two rows the incidence table cannot see."""
    return {
        "points": [{"name": "c", "x": 0.0, "y": 0.0, "fixed": True},
                   {"name": "p", "x": 10.0, "y": 0.0},
                   {"name": "q", "x": 10.0, "y": 20.0}],
        "circles": [{"name": "C", "center": "c", "r": 10.0}],
        "lines": [{"name": "L", "p1": "p", "p2": "q"}],
        "constraints": [{"type": "distance", "p": "p", "q": "c", "d": 10.0},
                        {"type": "radius", "c": "C", "r": 10.0},
                        {"type": "tangent", "a": "L", "b": "C"}],
    }


CLASS_SPECS["line_arc_dimensional"] = dimensional_junction_spec
CLASS_SPECS["line_arc_symmetric"] = symmetric_junction_spec
CLASS_SPECS["line_circle_measured"] = measured_on_curve_spec


def test_a_dimensionally_pinned_junction_is_recognised():
    """Review 2, C4 — the exact configuration and the exact numbers.

    Measured on this spec, `n_params 6`, with the junction pass switched off
    and back on (0143 printed a different table here; these reproduce — 0144):

    | | singular values | rank | dof | status | blame |
    |---|---|---|---|---|---|
    | distance form | 10.05, 1.414, **4.3e-17** | 2/3 | 4 | over_constrained | `redundant: [tangent]` |
    | direction form | 10.05, 1.005, **0.1404** | 3/3 | 3 | under_constrained | none |

    The third singular value is the whole finding: 4.3e-17 is the tangency row
    lying in the span of the two dimensional rows, and the constraint being
    blamed for it.
    """
    spec = dimensional_junction_spec()
    sk = parse_sketch(spec)
    assert [r.kind for r in sk.residuals] == [
        "distance_x", "distance_y", "tangent_dir"]
    res, svals, tol = _singular_values(spec)
    assert svals[-1] > max(tol, 1e-3), (svals, tol)
    assert res["ok"] is True, res["diagnostics"]
    assert res["rank"] == 3 == res["n_residuals"], res["diagnostics"]
    assert res["diagnostics"]["redundant"] == []


def test_the_junction_criterion_is_the_jacobian_not_a_list_of_kinds():
    """The class-level statement, and the reason there is no fifth instance.

    A junction is pinned when the **rest of the residual rows** determine the
    junction's on-curve function on both curves and determine it to zero. That
    is a question about the row space of the Jacobian, so it cannot be made
    stale by adding a constraint type — which is what happened three times.
    Assert it the only way that means anything: over every spelling of "these
    two curves already meet", including two the incidence table has never
    heard of.
    """
    for name, build in sorted(CLASS_SPECS.items()):
        sk = parse_sketch(build())
        kinds = [r.kind for r in sk.residuals]
        assert "tangent_dir" in kinds, (name, kinds)
        assert "tangent_line_circle" not in kinds, (name, kinds)
        assert "tangent_circles" not in kinds, (name, kinds)


def test_a_pinned_but_offset_junction_is_not_a_junction():
    """The other half, and the one a value-blind rank test would get wrong:
    `distance_x(..., 5)` pins the offset just as firmly, to a point that is
    **not** on the arc. That is an ordinary tangency and must keep the
    distance residual."""
    spec = dimensional_junction_spec()
    spec["constraints"][0]["d"] = 5.0
    kinds = [r.kind for r in parse_sketch(spec).residuals]
    assert "tangent_line_circle" in kinds, kinds
    assert "tangent_dir" not in kinds, kinds


@pytest.mark.parametrize("off", [0.0, 0.5, 3.0, 12.0, 60.0, -25.0,
                                 275.0, 400.0, 575.0, -575.0])
def test_the_junction_is_found_however_far_the_seed_is_from_it(off):
    """The criterion is about the **solution**, so the seed must not decide it.

    0143 manufactured that solution with a budgeted `least_squares` projection
    of the non-tangency rows and read `fit.x` without reading `fit.status`. The
    seeds beyond 250 mm are the ones that exposed it: at 275 mm the projection
    stopped 76 mm short, `phi_arc` read 76 instead of 0, and the tangency
    quietly compiled to the degenerate distance form. Swept 0..1000 mm in
    25 mm steps, **21 of 41 seeds** got the flat form, and non-monotonically
    (275 flat, 575 direction, 600 direction, 650 flat) — numerical noise, not
    geometry. There is no projection now: the criterion is read at the
    provisional system's own solution, so the seed cannot decide it.

    The row-space half keeps its absolute floor: a *structural* incidence has
    an analytically zero gradient and a numerically 1e-16 one, which a purely
    relative test reads as a full-size vector pointing somewhere random.
    """
    spec = dimensional_junction_spec(off=off)
    assert [r.kind for r in parse_sketch(spec).residuals][-1] == "tangent_dir"
    res = solve_sketch(spec)
    assert res["ok"] is True, res["diagnostics"]
    assert res["rank"] == res["n_residuals"]


def test_the_seed_never_changes_which_residual_a_junction_compiles_to():
    """The sweep itself, as one assertion: **one** compiled form over the whole
    range, not 21 of one and 20 of the other."""
    forms = {off: [r.kind for r in
                   parse_sketch(dimensional_junction_spec(off=off)).residuals
                   if r.kind.startswith("tangent")]
             for off in range(0, 525, 25)}
    assert {tuple(v) for v in forms.values()} == {("tangent_dir",)}, forms


def test_an_undecidable_junction_says_so_instead_of_falling_back_quietly():
    """**The hard requirement.** When the rows the criterion is about cannot be
    solved, the question has no answer — and the one thing this pass may never
    do is answer it "no" in silence, because "no" compiles the residual that is
    rank-deficient at a junction. Here `distance_x` is written twice with
    contradictory values, so there is no configuration to read the criterion
    at."""
    spec = dimensional_junction_spec()
    spec["constraints"].insert(1, {"type": "distance_x", "p": "p2",
                                   "q": "a1.start", "d": 40.0})
    res = solve_sketch(spec)
    codes = [w["code"] for w in res["warnings"]]
    assert "tangency_junction_undecided" in codes, res["warnings"]
    warning = res["warnings"][codes.index("tangency_junction_undecided")]
    assert warning["entities"] == ["l1/a1"], warning
    assert "distance form" in warning["message"]


def test_the_undecided_warning_survives_the_tools_error_path():
    """`tangency_junction_undecided` is raised on exactly the sketches that do
    not converge, and `solve_sketch` (the tool) turns those into a
    `ValidationError` instead of returning the result — so the warning has to
    ride in the error's `details` or no caller ever sees it."""
    from agentcad.core.tools import ToolRegistry
    from agentcad.core.tools_sketch import register as register_sketch

    registry = ToolRegistry()
    register_sketch(registry, None)
    spec = dimensional_junction_spec(off=590.0)
    out = registry.call("solve_sketch", {
        "entities": {"points": spec["points"], "lines": spec["lines"],
                     "arcs": spec["arcs"]},
        "constraints": spec["constraints"]})
    details = out["error"]["details"]
    assert out["error"]["type"] == "validation_error", out["error"]
    assert [w["code"] for w in details["warnings"]] == [
        "tangency_junction_undecided"], details["warnings"]


def test_detection_reaches_exactly_as_far_as_the_solve_does():
    """**The honest limit, stated as a test.** There is no seed distance gate
    any more — the criterion is read at the provisional system's solution, so
    detection is attempted whenever that system has one and refused, out loud,
    whenever it does not.

    Swept -1000..1000 mm in 25 mm steps on this configuration (only `p2`
    moves, so the line grows with the offset): 59 seeds compile the direction
    form and 22 the distance form — and those 22 are **exactly** the 22 the
    sketch does not solve at all, each carrying the warning. Not one seed both
    solves and gets the flat form.
    """
    flat, undecided, unsolved = [], [], []
    for off in range(-1000, 1025, 25):
        sk = parse_sketch(dimensional_junction_spec(off=float(off)))
        res = sk.solve()
        if "tangent_line_circle" in [r.kind for r in sk.residuals]:
            flat.append(off)
        if "tangency_junction_undecided" in [w["code"] for w in
                                             res["warnings"]]:
            undecided.append(off)
        if not res["ok"]:
            unsolved.append(off)
    assert flat == undecided == unsolved, (flat, undecided, unsolved)
    assert len(flat) == 22, flat


@pytest.mark.parametrize("shift", [0.0, 1e2, 1e4, 1e6])
def test_the_flat_form_is_always_the_loud_one_wherever_the_sketch_sits(shift):
    """The half of the identity above that is a property of **the code**,
    swept along the translation axis the test above does not move on.

    `flat == undecided` says the pass is never quiet about refusing: every
    seed that keeps the distance form carries the warning naming it, and no
    seed carries the warning without it. That holds at every offset.

    `== unsolved` does not, and deliberately is not asserted here. It is not
    the junction pass's property: `ok` is an **absolute** `max_residual <
    1e-7` and the criterion is relative, so far from the origin the two
    yardsticks stop agreeing on the same drawing. Measured on this sweep, the
    worst direction-form solve reads 6.8e-09 at shift 0 and 4.1e-07 at shift
    1e4 — the same convergence in relative terms (4e-11 of a 1e4 mm
    coordinate), scored differently by an absolute gate. That is `solve`'s
    gate to answer for, not this one's.
    """
    flat, undecided = [], []
    for off in range(-1000, 1025, 25):
        spec = dimensional_junction_spec(off=float(off))
        for p in spec["points"]:
            p["x"] += shift
            p["y"] += shift
        sk = parse_sketch(spec)
        res = sk.solve()
        if "tangent_line_circle" in [r.kind for r in sk.residuals]:
            flat.append(off)
        if "tangency_junction_undecided" in [w["code"] for w in
                                             res["warnings"]]:
            undecided.append(off)
    assert flat == undecided, (shift, flat, undecided)


def test_the_object_api_may_keep_drawing_after_it_has_solved():
    """`Sketch(); point(...); solve()` is the documented object API
    (`agentcad/core/templates.py`), and a part script that keeps building
    after a look at the answer is the ordinary way to use it.

    The junction criterion cached a start configuration for the re-solve and
    `_tangencies_resolved` blocked it from ever being recomputed, so the
    *second* `solve()` started `least_squares` from a vector of the previous
    sketch's width: measured `IndexError: index 6 is out of bounds for axis 0
    with size 6` on this spec, and a tangency declared after a solve was never
    asked the junction question at all. The cache is a fact about a
    configuration, so it dies with the configuration it was read at.
    """
    sk = Sketch()
    sk.point("c", 0.0, 0.0, fixed=True)
    sk.point("t", 10.0, 0.0)
    sk.point("p", 10.0, 0.0)
    sk.point("q", 30.0, 0.0)
    sk.circle("C", "c", 10.0, fixed_r=True)
    sk.line("L", "p", "q")
    sk.horizontal("L")
    sk.distance_y("t", "p", 0.0)
    sk.point_on_circle("t", "C")
    sk.tangent("L", "C")
    def forms():
        return [r.kind for r in sk.residuals if r.kind.startswith("tangent")]

    first = sk.solve()
    assert first["ok"] is True, first["diagnostics"]
    assert forms() == ["tangent_dir"], forms()

    # more entities, then solve again
    sk.point("z", 40.0, 40.0)
    sk.distance("z", "c", 50.0)
    second = sk.solve()
    assert second["ok"] is True, second["diagnostics"]
    assert math.dist((second["points"]["z"]["x"], second["points"]["z"]["y"]),
                     (0.0, 0.0)) == pytest.approx(50.0, abs=1e-6)
    # the re-decision reached the same verdict, because the junction is still
    # pinned — a re-run is not a reset
    assert forms() == ["tangent_dir"], forms()

    # a second tangency declared after a solve is asked the question on its
    # own terms: nothing pins L2 to the circle, so it keeps the distance form
    sk.point("p2", -30.0, 10.0)
    sk.point("q2", -10.0, 10.0)
    sk.line("L2", "p2", "q2")
    sk.tangent("L2", "C")
    third = sk.solve()
    assert third["ok"] is True, third["diagnostics"]
    assert forms() == ["tangent_dir", "tangent_line_circle"], forms()
    assert [w["code"] for w in third["warnings"]] == [], third["warnings"]


def test_a_tangency_seeded_at_the_direction_residuals_own_stationary_point():
    """`t_L x t_C` has a stationary point of its own, and 0143 drove into it.

    A horizontal line tangent to a circle with the junction seeded at the
    3 o'clock point: the cross product is 1, its gradient there is parallel to
    the `point_on_circle` row, Gauss-Newton cannot move and the row that gets
    blamed is the one doing the work — measured `ok: false`, `max_residual 1`,
    `conflicting: [tangent]` against 3.8e-11 from the same sketch before 0143.
    The re-solve starts from the provisional system's own solution, which the
    distance form (no stationary point there) walks to the tangency first.
    """
    spec = {
        "points": [{"name": "c", "x": 0.0, "y": 0.0, "fixed": True},
                   {"name": "t", "x": 10.0, "y": 0.0},
                   {"name": "p", "x": 10.0, "y": 0.0},
                   {"name": "q", "x": 30.0, "y": 0.0}],
        "circles": [{"name": "C", "center": "c", "r": 10.0, "fixed_r": True}],
        "lines": [{"name": "L", "p1": "p", "p2": "q"}],
        "constraints": [
            {"type": "horizontal", "ln": "L"},
            {"type": "distance_y", "p": "t", "q": "p", "d": 0.0},
            {"type": "point_on_circle", "p": "t", "c": "C"},
            {"type": "tangent", "a": "L", "b": "C"},
        ],
    }
    assert "tangent_dir" in [r.kind for r in parse_sketch(spec).residuals]
    res = solve_sketch(spec)
    assert res["ok"] is True, res["diagnostics"]
    assert res["max_residual"] < 1e-7, res["max_residual"]
    assert res["diagnostics"]["conflicting"] == [], res["diagnostics"]
    # the junction ended up at the top of the circle, where a horizontal line
    # is tangent to it — not at the 3 o'clock point it was seeded at
    assert abs(res["points"]["t"]["y"] - 10.0) < 1e-6, res["points"]["t"]


@pytest.mark.parametrize("scale", [1e-3, 1.0, 1e3])
@pytest.mark.parametrize("rel_delta,form", [(0.0, "tangent_dir"),
                                            (1e-9, "tangent_dir"),
                                            (1e-8, "tangent_dir"),
                                            (1e-6, "tangent_line_circle"),
                                            (1e-4, "tangent_line_circle"),
                                            (1e-2, "tangent_line_circle")])
def test_the_junction_verdict_does_not_depend_on_the_unit_it_was_drawn_in(
        scale, rel_delta, form):
    """**A sketch has no units.** `distance(p, c, r(1 + d))` + `radius(C, r)`
    puts `p` a *relative* `d` off the circle, so the verdict must be a function
    of `d` alone. It was a function of the millimetre: `max(1.0, curve_scale)`
    in the value gate meant the same drawing authored in metres and in
    millimetres disagreed at 1e-7..1e-6 of relative offset."""
    r = 10.0 * scale
    spec = {
        "points": [{"name": "c", "x": 0.0, "y": 0.0, "fixed": True},
                   {"name": "p", "x": r, "y": 0.0},
                   {"name": "q", "x": r, "y": 20.0 * scale}],
        "circles": [{"name": "C", "center": "c", "r": r}],
        "lines": [{"name": "L", "p1": "p", "p2": "q"}],
        "constraints": [
            {"type": "distance", "p": "p", "q": "c", "d": r * (1 + rel_delta)},
            {"type": "radius", "c": "C", "r": r},
            {"type": "tangent", "a": "L", "b": "C"}],
    }
    kinds = [k for k in (r.kind for r in parse_sketch(spec).residuals)
             if k.startswith("tangent")]
    assert kinds == [form], (scale, rel_delta, kinds)


@pytest.mark.parametrize("shift", [0.0, 1e2, 1e4, 1e6])
@pytest.mark.parametrize("rel_delta,form", [(0.0, "tangent_dir"),
                                            (1e-9, "tangent_dir"),
                                            (1e-8, "tangent_dir"),
                                            (1e-6, "tangent_line_circle"),
                                            (1e-4, "tangent_line_circle"),
                                            (1e-2, "tangent_line_circle")])
def test_the_junction_verdict_does_not_depend_on_where_the_sketch_sits(
        shift, rel_delta, form):
    """**A sketch has no origin either.** The twin of the unit sweep above,
    along the *translation* axis: the same drawing (r = 10) moved away from
    (0, 0) must give the same verdict, because moving a drawing changes no
    length in it.

    It did not. `_configuration_scale` was `max(|x|, |y|)` over the
    coordinates — a *position*, not a length — so the manifold gate
    `JUNCTION_MANIFOLD_TOL * scale` grew with the distance from the origin:
    2.0e-06 at shift 0, 1.0e-03 at 1e4. Inside that band `_junction_probe`
    took its fast path, trusted a seed that is **not** on the other rows'
    manifold, and read the criterion at a configuration the user never gets —
    exactly what 0144 deleted the projection to prevent. Measured at
    d = 1e-4, shift 1e4: `tangent_dir`, `ok: true`, rank 3/3, no warning, and
    a true tangency error of +1.0e-03 mm.
    """
    r = 10.0
    spec = {
        "points": [{"name": "c", "x": shift, "y": shift, "fixed": True},
                   {"name": "p", "x": shift + r, "y": shift},
                   {"name": "q", "x": shift + r, "y": shift + 20.0}],
        "circles": [{"name": "C", "center": "c", "r": r}],
        "lines": [{"name": "L", "p1": "p", "p2": "q"}],
        "constraints": [
            {"type": "distance", "p": "p", "q": "c", "d": r * (1 + rel_delta)},
            {"type": "radius", "c": "C", "r": r},
            {"type": "tangent", "a": "L", "b": "C"}],
    }
    kinds = [k for k in (r.kind for r in parse_sketch(spec).residuals)
             if k.startswith("tangent")]
    assert kinds == [form], (shift, rel_delta, kinds)


def test_a_curve_with_no_size_holds_nothing_on_it():
    """A zero-length line: `cross(h - a, u)` is identically zero with an
    identically zero gradient, which is "carries no information" and not
    "fully determined". The gradient floor read it as the latter and compiled
    the direction form on a line that has no direction."""
    def spec(dx):
        return {
            "points": [{"name": "c", "x": 0.0, "y": 0.0},
                       {"name": "q", "x": 0.0, "y": 10.0},
                       {"name": "a", "x": 50.0, "y": 50.0, "fixed": True},
                       {"name": "b", "x": 50.0 + dx, "y": 50.0,
                        "fixed": True}],
            "circles": [{"name": "C", "center": "c", "r": 10.0,
                         "fixed_r": True}],
            "lines": [{"name": "L", "p1": "a", "p2": "b"}],
            "constraints": [{"type": "point_on_circle", "p": "q", "c": "C"},
                            {"type": "tangent", "a": "L", "b": "C"}],
        }
    for dx in (0.0, 1.0):
        kinds = [r.kind for r in parse_sketch(spec(dx)).residuals]
        assert kinds == ["point_on_circle", "tangent_line_circle"], (dx, kinds)


def test_a_symmetric_pair_the_axis_runs_through_is_not_a_conflict():
    """`symmetric` normalized `q - p`, which is review 2's finding C2 again:
    the 6.1e-16 mm between a pair the constraint itself holds together became a
    full unit vector and the row read 1.0. Measured `ok: false`, rank 2 of 3,
    `conflicting: [symmetric]` on geometry that was already right."""
    res = solve_sketch(symmetric_junction_spec())
    assert res["ok"] is True, res["diagnostics"]
    assert res["diagnostics"]["conflicting"] == [], res["diagnostics"]
    p2, start = res["points"]["p2"], res["arcs"]["a1"]["start"]
    assert math.dist((p2["x"], p2["y"]), (start["x"], start["y"])) < 1e-7


def test_symmetric_is_smooth_where_the_pair_meets():
    """The row is `(q - p) . u` in millimetres now, not the sine of an angle:
    same zero set, and a derivative that exists at `p == q`."""
    sk = Sketch()
    sk.point("x1", 0.0, 0.0, fixed=True)
    sk.point("x2", 10.0, 0.0, fixed=True)
    sk.point("p", 3.0, 0.0)
    sk.point("q", 3.0, 0.0)                  # the degenerate pair
    sk.line("AX", "x1", "x2")
    sk.symmetric("p", "q", "AX")
    v = sk.initial_vector()
    assert max(abs(x) for x in sk.residuals[-1].f(v)) < 1e-15
    row = np.zeros((2, sk.n_par))
    sk.residuals[-1].df(v, row, 0)
    assert np.all(np.isfinite(row))
    assert np.linalg.norm(row[1]) == pytest.approx(math.sqrt(2.0), abs=1e-9)


# --------------------------------------------------------------------------
# FR6: what the junction pass costs on a drag frame (review 3, H3)
# --------------------------------------------------------------------------
FR6_WARM_MS = 16.0


def _staircase_with_dimensional_junction(n: int = 50) -> dict:
    """0142's 50-segment bench staircase, plus one arc and one tangency whose
    junction is written **dimensionally** — the spelling that reaches the
    Jacobian pass instead of the symbolic detector."""
    pts = [{"name": f"p{i}", "x": float(i * 5), "y": float((i % 2) * 5)}
           for i in range(n + 1)]
    lines, cons = [], []
    for i in range(n):
        lines.append({"name": f"l{i}", "p1": f"p{i}", "p2": f"p{i+1}"})
        cons.append({"type": "horizontal" if i % 2 == 0 else "vertical",
                     "ln": f"l{i}"})
    cons.append({"type": "fixed", "p": "p0", "x": 0.0, "y": 0.0})
    pts += [{"name": "ct", "x": 400.0, "y": -60.0, "fixed": True},
            {"name": "t1", "x": 400.0, "y": -50.0},
            {"name": "t2", "x": 430.0, "y": -50.0}]
    lines.append({"name": "tl", "p1": "t1", "p2": "t2"})
    cons += [{"type": "distance_x", "p": "t1", "q": "a1.start", "d": 0.0},
             {"type": "distance_y", "p": "t1", "q": "a1.start", "d": 0.0},
             {"type": "tangent", "a": "tl", "b": "a1"}]
    return {"points": pts, "lines": lines,
            "arcs": [{"name": "a1", "center": "ct", "r": 10.0,
                      "start_deg": 90.0, "end_deg": 0.0, "fixed_r": True}],
            "constraints": cons}


@pytest.mark.slow
def test_the_junction_pass_costs_a_drag_frame_nothing(capsys):
    """**FR6 with a junction present**, and the whole frame — compile *and*
    solve, because the junction pass runs at compile time.

    0143 ran an unconditional `least_squares` projection on every compile:
    measured 8.52 ms compile + 16.53 ms solve on this bench, and 109.77 +
    516.72 ms when the seed was 300 mm out and the projection budget was
    exhausted. Nothing here projects. A drag frame arrives warm-started from
    the previous solution, which already solves the rows the criterion is
    about, so the criterion is read at the seed and no extra solve happens at
    all — measured p50 6.3 ms for the whole frame against 5.8 ms for the same
    staircase with no tangency in it.
    """
    spec = _staircase_with_dimensional_junction()
    first = solve_sketch(spec)
    assert first["ok"] is True, first["diagnostics"]
    assert "tangent_dir" in [r.kind for r in parse_sketch(spec).residuals]

    prev, frames = first, []
    for i in range(9):
        seed = {"points": {n: {"x": p["x"], "y": p["y"]}
                           for n, p in prev["points"].items()},
                "arcs": {n: {"r": a["r"], "start_deg": a["start_deg"],
                             "end_deg": a["end_deg"]}
                         for n, a in prev["arcs"].items()}}
        frame = dict(spec, initial=seed,
                     drag={"point": "p25", "x": 125.0 + 0.2 * i, "y": 5.0})
        t0 = time.perf_counter()
        sk = parse_sketch(frame)
        res = sk.solve()
        frames.append((time.perf_counter() - t0) * 1e3)
        assert res["ok"] is True, (i, res["diagnostics"])
        assert res["warnings"] == [], res["warnings"]
        assert "tangent_dir" in [r.kind for r in sk.residuals]
        prev = res
    p50 = statistics.median(frames)
    with capsys.disabled():
        print(f"\nstaircase-50 + dimensional junction: whole warm frame "
              f"(compile+solve) p50={p50:.2f} ms, best={min(frames):.2f} ms, "
              f"budget {FR6_WARM_MS} ms")
    # The budget is asserted on the fastest frame for the reason AC2 gives:
    # wall-clock on a shared machine, where the median is a flake and the best
    # frame is the measurement scheduler noise can only make worse.
    assert 0.0 < min(frames) <= FR6_WARM_MS, sorted(frames)
    assert p50 <= FR6_WARM_MS * 4.0, sorted(frames)


# --------------------------------------------------------------------------
# the class sweeps — LAST, so that every configuration is registered
# --------------------------------------------------------------------------
# `@parametrize` is evaluated where it is written. These two sweeps sat above
# the three review-2 configurations and collected six ids for nine specs, so
# the newest members of the class — the ones the criterion was rewritten for —
# were never rank-tested at all. Anything added to `CLASS_SPECS` from here on
# must go **above** this line; `test_every_class_spec_is_parametrized` fails if
# it does not.
CLASS_SPEC_IDS = sorted(CLASS_SPECS)


def test_every_class_spec_is_parametrized():
    """The guard on the guard: the two sweeps below cover the whole class."""
    assert CLASS_SPEC_IDS == sorted(CLASS_SPECS), (
        "a CLASS_SPECS entry was registered after the parametrized sweeps "
        "were collected, so it is not being tested: "
        f"{sorted(set(CLASS_SPECS) - set(CLASS_SPEC_IDS))}")
    assert len(CLASS_SPEC_IDS) == 9, CLASS_SPEC_IDS


@pytest.mark.parametrize("name", CLASS_SPEC_IDS)
def test_a_pinned_junction_never_compiles_to_a_distance_residual(name):
    """The class-level property. **Not** "these nine specs are fixed": the
    distance forms exist only for curves the sketch does not already hold
    together, so a pinned junction that still reaches them is the bug."""
    sk = parse_sketch(CLASS_SPECS[name]())
    kinds = [r.kind for r in sk.residuals]
    assert "tangent_line_circle" not in kinds, kinds
    assert "tangent_circles" not in kinds, kinds
    assert "tangent_dir" in kinds, kinds


@pytest.mark.parametrize("name", CLASS_SPEC_IDS)
def test_a_pinned_junction_has_full_row_rank_and_an_honest_dof(name):
    """Every row is doing work, so the rank is the row count and nothing is
    blamed. Before the fix each of these reported `over_constrained` with
    `redundant: [tangent]` against a constraint reaching tangency to 1e-11."""
    spec = CLASS_SPECS[name]()
    res = solve_sketch(spec)
    diag = res["diagnostics"]
    assert res["rank"] == res["n_residuals"], diag
    assert res["dof"] == res["n_params"] - res["n_residuals"]
    assert diag["status"] != "over_constrained", diag
    assert diag["redundant"] == []
    assert diag["conflicting"] == []
