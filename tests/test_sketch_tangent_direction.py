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


@pytest.mark.parametrize("name", sorted(CLASS_SPECS))
def test_a_pinned_junction_never_compiles_to_a_distance_residual(name):
    """The class-level property. **Not** "these six specs are fixed": the
    distance forms exist only for curves the sketch does not already hold
    together, so a pinned junction that still reaches them is the bug."""
    sk = parse_sketch(CLASS_SPECS[name]())
    kinds = [r.kind for r in sk.residuals]
    assert "tangent_line_circle" not in kinds, kinds
    assert "tangent_circles" not in kinds, kinds
    assert "tangent_dir" in kinds, kinds


@pytest.mark.parametrize("name", sorted(CLASS_SPECS))
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
