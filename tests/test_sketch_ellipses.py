"""Ellipses and elliptical arcs (PRD-009 slice 11), and the spike behind them.

Two feasibility questions had to be answered before this slice could ship, and
both are asserted here rather than remembered:

1. **Elliptical tangency conditioning** (design risk 7). Point-to-ellipse
   distance has no closed form, so tangency carries the tangency point's
   eccentric anomaly as an auxiliary parameter and two residuals. The plan's
   gate was 90% convergence over 20 randomized starts; measured **20/20** for
   line-ellipse (geometric error p50 9.25e-13 mm) and **20/20** for
   circle-ellipse (p50 1.32e-10 mm), so tangency ships.
2. **The parametrization has to be build123d's**, or the solved angles are not
   the emitted ones. Measured against the pinned 0.11.1:
   `EllipticalCenterArc`'s `start_angle` is the eccentric anomaly, agreeing
   with `c + R(phi)(a cos t, b sin t)` to 8.9e-16 mm.

The spike also found a **library bug**: `EllipticalCenterArc(..., end_angle=)`
raises `UnboundLocalError` in build123d 0.11.1 (its deprecation branch reads an
unbound `direction`). The emitter uses `arc_size`, and the test at the bottom
of this file pins that, because the plan's task list named the broken spelling.
"""

import math
import subprocess
import sys

import numpy as np
import pytest
from scipy.optimize import minimize_scalar

from agentcad.core.sketch_emit import emit, junction_gaps
from agentcad.toolkit.sketch import Sketch, SketchError, parse_sketch, solve_sketch

from .test_sketch_emit import build_metrics
from .test_sketch_jacobian import (assert_df_matches_central_difference,
                                   assert_df_stays_inside_params)

A, B, ROT = 29.7431, 11.3179, 12.7


# --------------------------------------------------------------------------
# parametrization and parameter count
# --------------------------------------------------------------------------
def _full_spec(**over) -> dict:
    spec = {
        "points": [{"name": "c", "x": 3.317, "y": -1.913, "fixed": True}],
        "ellipses": [{"name": "e", "center": "c", "a": A, "b": B,
                      "rotation": ROT}],
        "constraints": [],
    }
    spec.update(over)
    return spec


def _bounded_spec(**over) -> dict:
    spec = _full_spec()
    spec["ellipses"] = [{"name": "e", "center": "c", "a": A, "b": B,
                         "rotation": ROT, "start_deg": 0.0, "end_deg": 180.0}]
    spec.update(over)
    return spec


def _anomaly_point(cx, cy, a, b, rot_deg, t_deg):
    t, phi = math.radians(t_deg), math.radians(rot_deg)
    lx, ly = a * math.cos(t), b * math.sin(t)
    return (cx + lx * math.cos(phi) - ly * math.sin(phi),
            cy + lx * math.sin(phi) + ly * math.cos(phi))


def test_a_full_ellipse_costs_three_parameters():
    """`a`, `b` and the rotation — the centre's two belong to the point."""
    res = solve_sketch(_full_spec())
    assert res["n_params"] == 3          # centre is fixed
    assert res["ok"] is True
    e = res["ellipses"]["e"]
    assert (e["a"], e["b"]) == pytest.approx((A, B))
    assert e["rotation"] == pytest.approx(ROT)
    assert e["bounded"] is False
    assert "start" not in e


def test_a_bounded_ellipse_costs_two_more():
    res = solve_sketch(_bounded_spec())
    assert res["n_params"] == 5
    e = res["ellipses"]["e"]
    assert e["bounded"] is True
    assert (e["start"]["x"], e["start"]["y"]) == pytest.approx(
        _anomaly_point(3.317, -1.913, A, B, ROT, 0.0), abs=1e-12)
    assert (e["end"]["x"], e["end"]["y"]) == pytest.approx(
        _anomaly_point(3.317, -1.913, A, B, ROT, 180.0), abs=1e-12)


def test_the_axis_handles_are_ordinary_points():
    """This is what buys the ellipse the whole existing vocabulary: `.major`
    and `.minor` are point handles, so `distance` and `horizontal` pin an
    ellipse's size and orientation with no new constraint type."""
    spec = _full_spec(constraints=[
        {"type": "distance", "p": "e.center", "q": "e.major", "d": 25.0},
        {"type": "distance", "p": "e.center", "q": "e.minor", "d": 9.0},
        {"type": "horizontal", "ln": "axis"},
    ])
    spec["lines"] = [{"name": "axis", "p1": "c", "p2": "m"}]
    spec["points"].append({"name": "m", "x": 30.0, "y": 1.0})
    spec["constraints"].append(
        {"type": "coincident", "p": "m", "q": "e.major"})
    res = solve_sketch(spec)
    assert res["ok"] is True, res["diagnostics"]
    e = res["ellipses"]["e"]
    assert e["a"] == pytest.approx(25.0, abs=1e-9)
    assert e["b"] == pytest.approx(9.0, abs=1e-9)
    # a horizontal major axis: the rotation solved to 0 (or 180)
    assert min(e["rotation"] % 180.0, 180.0 - e["rotation"] % 180.0) \
        == pytest.approx(0.0, abs=1e-7)


def test_radius_and_equal_radius_take_a_named_semi_axis():
    res = solve_sketch(_full_spec(constraints=[
        {"type": "radius", "c": "e.a", "r": 20.0},
        {"type": "radius", "c": "e.b", "r": 20.0},
    ]))
    e = res["ellipses"]["e"]
    assert (e["a"], e["b"]) == pytest.approx((20.0, 20.0), abs=1e-9)
    assert res["dof"] == 1               # the rotation is still free


def test_naming_the_ellipse_itself_as_a_radius_says_what_to_write_instead():
    with pytest.raises(SketchError, match=r"two semi-axes"):
        solve_sketch(_full_spec(constraints=[
            {"type": "radius", "c": "e", "r": 20.0}]))


def test_equal_radius_against_a_circle_and_concentric():
    spec = _full_spec(constraints=[
        {"type": "equal_radius", "c1": "e.b", "c2": "C"},
        {"type": "concentric", "a": "e", "b": "C"},
        {"type": "radius", "c": "C", "r": 7.5},
    ])
    spec["points"].append({"name": "cc", "x": 9.0, "y": 4.0})
    spec["circles"] = [{"name": "C", "center": "cc", "r": 6.0}]
    res = solve_sketch(spec)
    assert res["ok"] is True, res["diagnostics"]
    assert res["ellipses"]["e"]["b"] == pytest.approx(7.5, abs=1e-9)
    assert (res["points"]["cc"]["x"], res["points"]["cc"]["y"]) == \
        pytest.approx((3.317, -1.913), abs=1e-9)


def test_angles_are_not_wrapped_mid_solve():
    """Same rule as an arc: normalized on output only, with the end carrying
    the whole signed sweep."""
    res = solve_sketch(_bounded_spec(ellipses=[
        {"name": "e", "center": "c", "a": A, "b": B, "rotation": ROT,
         "start_deg": -20.0, "end_deg": 200.0}]))
    e = res["ellipses"]["e"]
    assert e["start_deg"] == pytest.approx(340.0)
    assert e["end_deg"] - e["start_deg"] == pytest.approx(220.0)


def test_a_bounded_ellipse_needs_both_bounds_or_neither():
    with pytest.raises(SketchError, match="both"):
        solve_sketch(_full_spec(ellipses=[
            {"name": "e", "center": "c", "a": A, "b": B, "start_deg": 0.0}]))


def test_a_degenerate_ellipse_is_refused_at_ingestion():
    with pytest.raises(SketchError, match="positive semi-axes"):
        solve_sketch(_full_spec(ellipses=[
            {"name": "e", "center": "c", "a": A, "b": 0.0}]))


# --------------------------------------------------------------------------
# derivative coverage — the non-negotiable one
# --------------------------------------------------------------------------
def _ellipse_handles_sketch() -> Sketch:
    """Every residual an ellipse's handles can reach."""
    sk = Sketch()
    sk.point("c", 0.0, 0.0)
    sk.point("q", 40.0, 3.0)
    sk.point("z", 5.0, 9.0)
    sk.ellipse("e", "c", A, B, ROT, 10.0, 200.0)
    sk.line("ln", "q", "z")
    sk.distance("e.center", "e.major", 30.0)
    sk.distance("e.minor", "q", 21.0)
    sk.coincident("e.start", "z")
    sk.point_on_line("e.end", "ln")
    sk.midpoint("q", "ln")
    sk.radius("e.a", 30.0)
    sk.equal_radius("e.a", "e.b")
    return sk


def _ellipse_tangency_sketch() -> Sketch:
    """The auxiliary-anomaly form, both pairings."""
    sk = Sketch()
    sk.point("c", 0.0, 0.0)
    sk.point("o", 46.0, 8.0)
    sk.point("la", -60.0, 17.0)
    sk.point("lb", 60.0, 19.0)
    sk.ellipse("e", "c", A, B, ROT)
    sk.circle("C", "o", 9.0)
    sk.line("L", "la", "lb")
    sk.tangent("L", "e")
    sk.tangent("C", "e")
    return sk


def _ellipse_junction_sketch() -> Sketch:
    """A bounded elliptical arc joined to a line by a coincidence: the
    direction residual, with no auxiliary parameter."""
    sk = Sketch()
    sk.point("c", 0.0, 0.0)
    sk.point("j", 29.0, 7.0)
    sk.point("k", 55.0, 20.0)
    sk.ellipse("e", "c", A, B, ROT, 0.0, 140.0)
    sk.line("L", "j", "k")
    sk.coincident("e.start", "j")
    sk.tangent("L", "e")
    return sk


DERIV_BUILDERS = {
    "ellipse_handles": _ellipse_handles_sketch,
    "ellipse_tangency": _ellipse_tangency_sketch,
    "ellipse_junction": _ellipse_junction_sketch,
}


@pytest.mark.parametrize("name", sorted(DERIV_BUILDERS))
def test_every_df_matches_a_central_difference_of_its_own_f(name):
    assert_df_matches_central_difference(name, DERIV_BUILDERS[name]())


@pytest.mark.parametrize("name", sorted(DERIV_BUILDERS))
def test_df_writes_only_inside_its_declared_params(name):
    assert_df_stays_inside_params(name, DERIV_BUILDERS[name]())


# --------------------------------------------------------------------------
# the spike: tangency conditioning (design risk 7)
# --------------------------------------------------------------------------
def _line_tangency_spec(jit) -> dict:
    return {
        "points": [
            {"name": "la", "x": -60.0, "y": 18.0 + jit[0], "fixed": True},
            {"name": "lb", "x": 60.0, "y": 18.0 + jit[0], "fixed": True},
            {"name": "c", "x": 2.0 + jit[1], "y": -1.0 + jit[2]},
        ],
        "lines": [{"name": "L", "p1": "la", "p2": "lb"}],
        "ellipses": [{"name": "e", "center": "c", "a": 30.0 + jit[3],
                      "b": 12.0 + jit[4], "rotation": 20.0 + 8.0 * jit[5]}],
        "constraints": [
            {"type": "radius", "c": "e.a", "r": 30.0},
            {"type": "radius", "c": "e.b", "r": 12.0},
            {"type": "distance", "p": "c", "q": "la", "d": 62.0},
            {"type": "tangent", "a": "L", "b": "e"},
        ],
    }


def _circle_tangency_spec(jit) -> dict:
    return {
        "points": [{"name": "c", "x": 0.0, "y": 0.0, "fixed": True},
                   {"name": "o", "x": 44.0 + jit[0], "y": 9.0 + jit[1]}],
        "circles": [{"name": "C", "center": "o", "r": 10.0, "fixed_r": True}],
        "ellipses": [{"name": "e", "center": "c", "a": 30.0 + jit[2],
                      "b": 12.0 + jit[3], "rotation": 15.0 + 8.0 * jit[4]}],
        "constraints": [
            {"type": "radius", "c": "e.a", "r": 30.0},
            {"type": "radius", "c": "e.b", "r": 12.0},
            {"type": "distance_y", "p": "c", "q": "o", "d": 9.0},
            {"type": "tangent", "a": "C", "b": "e"},
        ],
    }


def _tangency_error(res: dict, kind: str) -> float:
    """The **geometric** error, not the residual: the true minimum distance
    from the other curve to the ellipse, sampled then refined."""
    e = res["ellipses"]["e"]
    phi = math.radians(e["rotation"])

    def pt(t):
        lx, ly = e["a"] * math.cos(t), e["b"] * math.sin(t)
        return (e["cx"] + lx * math.cos(phi) - ly * math.sin(phi),
                e["cy"] + lx * math.sin(phi) + ly * math.cos(phi))

    if kind == "line":
        a, b = res["points"]["la"], res["points"]["lb"]
        ux, uy = b["x"] - a["x"], b["y"] - a["y"]
        n = math.hypot(ux, uy)

        def gap(t):
            x, y = pt(t)
            return abs((x - a["x"]) * uy / n - (y - a["y"]) * ux / n)
    else:
        o, r = res["points"]["o"], res["circles"]["C"]["r"]

        def gap(t):
            x, y = pt(t)
            return abs(math.hypot(x - o["x"], y - o["y"]) - r)

    # A sampled minimum reports the sampling step, not the error: the distance
    # is quadratic at a tangency, so the grid is followed by a real 1-D
    # minimization — the same measurement the spike made.
    ts = np.linspace(0.0, 2 * math.pi, 2001)
    best = float(ts[int(np.argmin([gap(float(t)) for t in ts]))])
    step = float(ts[1] - ts[0])
    refined = minimize_scalar(gap, bounds=(best - step, best + step),
                              method="bounded", options={"xatol": 1e-14})
    return float(min(refined.fun, gap(best)))


@pytest.mark.parametrize("kind,builder", [("line", _line_tangency_spec),
                                          ("circle", _circle_tangency_spec)])
def test_the_tangency_spike_converges_from_twenty_randomized_starts(kind,
                                                                    builder):
    """**The slice-11 gate.** The plan's rule was: >= 90% of 20 randomized
    starts converge, or ship ellipses without tangency. Measured on exactly
    these 20 starts:

    ```
    line-ellipse    20/20   err p50 9.25e-13  max 5.58e-10   nfev p50  48  3.8 ms
    circle-ellipse  20/20   err p50 1.32e-10  max 9.08e-10   nfev p50 120  7.8 ms
    ```

    The error is geometric — the true minimum distance from the other curve to
    the ellipse — not the residual, because a residual can be small for a
    formulation that is measuring the wrong thing. The RNG seed is fixed so a
    regression is a regression and not a bad day."""
    rng = np.random.default_rng(20260812)
    converged, errors = 0, []
    for _ in range(20):
        res = solve_sketch(builder(rng.uniform(-6.0, 6.0, 8)))
        err = _tangency_error(res, kind)
        errors.append(err)
        converged += bool(res["ok"] and err < 1e-9)
    assert converged >= 18, (converged, max(errors))    # the plan's 90% gate
    assert converged == 20, (converged, max(errors))    # what was measured
    assert max(errors) < 1e-9
    assert float(np.median(errors)) < 1e-9


def test_a_tangency_adds_one_parameter_and_two_rows():
    """Net one degree of freedom, as a tangency should be — the auxiliary
    anomaly is machinery, not a degree of freedom the user gets."""
    rng = np.random.default_rng(1)
    spec = _line_tangency_spec(np.zeros(8))
    without = dict(spec, constraints=spec["constraints"][:-1])
    a, b = solve_sketch(without), solve_sketch(spec)
    assert b["n_params"] - a["n_params"] == 1
    assert b["n_residuals"] - a["n_residuals"] == 2
    assert b["dof"] == a["dof"] - 1
    assert rng is not None


def test_the_auxiliary_parameter_is_named_after_the_ellipse():
    """If it ever shows up in `free_entities` a reader must be able to tell
    what it is — it is not a constraint the user wrote."""
    sk = parse_sketch(_line_tangency_spec(np.zeros(8)))
    assert "e.tangency" in sk.slot_owner


def test_two_ellipses_cannot_be_tangent_and_the_message_says_why():
    spec = _full_spec(constraints=[{"type": "tangent", "a": "e", "b": "e2"}])
    spec["points"].append({"name": "c2", "x": 60.0, "y": 0.0})
    spec["ellipses"].append({"name": "e2", "center": "c2", "a": 20.0,
                             "b": 8.0})
    with pytest.raises(SketchError, match="out of scope"):
        solve_sketch(spec)


def test_a_pinned_junction_uses_the_direction_residual_and_no_aux_parameter():
    """The tangency fix carried over from slice 10 applies to ellipses too:
    where the curves already meet, tangency is a direction condition and the
    anomaly is the handle's own."""
    sk = _ellipse_junction_sketch()
    assert [r.kind for r in sk.residuals] == ["coincident", "tangent_dir"]
    assert not sk._aux_seeds
    res = solve_sketch({
        "points": [{"name": "c", "x": 0.0, "y": 0.0, "fixed": True},
                   {"name": "j", "x": 29.0, "y": 7.0},
                   {"name": "k", "x": 55.0, "y": 20.0}],
        "ellipses": [{"name": "e", "center": "c", "a": A, "b": B,
                      "rotation": ROT, "start_deg": 0.0, "end_deg": 140.0}],
        "lines": [{"name": "L", "p1": "j", "p2": "k"}],
        "constraints": [
            {"type": "coincident", "p": "e.start", "q": "j"},
            {"type": "tangent", "a": "L", "b": "e"},
        ],
    })
    assert res["ok"] is True, res["diagnostics"]
    assert res["diagnostics"]["redundant"] == []
    # the line leaves the junction along the ellipse's own tangent
    e = res["ellipses"]["e"]
    t = math.radians(e["start_deg"])
    phi = math.radians(e["rotation"])
    tx = -e["a"] * math.sin(t) * math.cos(phi) - e["b"] * math.cos(t) * math.sin(phi)
    ty = -e["a"] * math.sin(t) * math.sin(phi) + e["b"] * math.cos(t) * math.cos(phi)
    j, k = res["points"]["j"], res["points"]["k"]
    ux, uy = k["x"] - j["x"], k["y"] - j["y"]
    cross = (tx * uy - ty * ux) / (math.hypot(tx, ty) * math.hypot(ux, uy))
    assert cross == pytest.approx(0.0, abs=1e-9)


# --------------------------------------------------------------------------
# warm start
# --------------------------------------------------------------------------
def test_initial_seeds_an_ellipse_and_is_all_or_nothing():
    spec = _bounded_spec()
    full = {"points": {}, "ellipses": {"e": {"a": 31.0, "b": 10.0,
                                             "rotation": 15.0,
                                             "start_deg": 0.0,
                                             "end_deg": 180.0}}}
    res = solve_sketch(dict(spec, initial=full))
    assert res["warm_started"] is True
    assert res["ellipses"]["e"]["a"] == pytest.approx(31.0)
    partial = {"points": {}, "ellipses": {"e": {"a": 31.0, "b": 10.0}}}
    degraded = solve_sketch(dict(spec, initial=partial))
    assert degraded["warm_started"] is False
    assert degraded["warnings"][0]["code"] == "initial_incomplete"
    assert degraded["ok"] is True
    with pytest.raises(SketchError, match="unknown ellipse"):
        solve_sketch(dict(spec, initial={"ellipses": {"nope": {}}}))


# --------------------------------------------------------------------------
# emission
# --------------------------------------------------------------------------
def _half_ellipse_spec() -> dict:
    """A bounded elliptical arc closed by a chord — non-round throughout."""
    return {
        "points": [{"name": "c", "x": 3.317, "y": -1.913},
                   {"name": "p1", "x": 33.0, "y": -2.0},
                   {"name": "p2", "x": -27.0, "y": -2.0}],
        "ellipses": [{"name": "e", "center": "c", "a": A, "b": B,
                      "rotation": ROT, "start_deg": 0.0, "end_deg": 180.0}],
        "lines": [{"name": "L", "p1": "e.end", "p2": "e.start"}],
        "constraints": [{"type": "radius", "c": "e.a", "r": A},
                        {"type": "radius", "c": "e.b", "r": B}],
    }


def test_a_full_ellipse_emits_as_a_face():
    res = solve_sketch(_full_spec())
    code = emit(res, _full_spec(), style="buildline")["code"]
    assert "Ellipse(x_radius=29.7431, y_radius=11.3179, rotation=12.7)" in code
    assert "Locations((3.317, -1.913))" in code


def test_an_elliptical_arc_emits_with_arc_size_not_end_angle():
    """`end_angle=` raises `UnboundLocalError` in the pinned build123d 0.11.1
    (its deprecation branch reads an unbound `direction`). The plan's task
    list named that spelling; the spike found it broken, so this pins the one
    that works — and `arc_size` is the signed sweep the solver already
    reports."""
    spec = _half_ellipse_spec()
    code = emit(solve_sketch(spec), spec, style="buildline")["code"]
    assert "EllipticalCenterArc(" in code
    assert "arc_size=180.0" in code
    assert "end_angle" not in code


def test_the_closure_gate_measures_an_elliptical_arcs_derived_endpoints():
    """An elliptical arc has **no endpoint-anchored constructor** in
    build123d, so unlike a circular arc its endpoints are always derived by
    the reader from the rounded centre, axes, rotation and angles. Measured at
    9 decimals on this profile: 3.15e-10 mm, inside the 1e-8 gate."""
    spec = _half_ellipse_spec()
    res = solve_sketch(spec)
    gaps = junction_gaps(res, spec)
    assert set(gaps) == {"e.end", "e.start"}
    assert max(gaps.values()) < 1e-8
    assert max(gaps.values()) > 0.0        # it really is derived, not shared
    # and at 4 decimals the same profile is refused rather than emitted
    coarse = junction_gaps(res, spec, decimals=4)
    assert max(coarse.values()) > 1e-8


def test_emission_is_deterministic():
    spec = _half_ellipse_spec()
    res = solve_sketch(spec)
    assert emit(res, spec)["code"] == emit(res, spec)["code"]


def test_the_emitter_still_never_imports_ocp():
    code = ("import sys; import agentcad.core.sketch_emit; "
            "import agentcad.toolkit.sketch; "
            "print('OCP' in sys.modules or 'build123d' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, check=True)
    assert out.stdout.strip() == "False"


def test_the_emitted_half_ellipse_rebuilds_to_the_solved_area(kernel, tmp_path):
    """The round trip: solve -> emit -> rebuild through the real kernel, and
    the area is the half-ellipse's own pi*a*b/2."""
    spec = _half_ellipse_spec()
    res = solve_sketch(spec)
    out = emit(res, spec, style="function")
    assert out["warnings"] == []
    metrics = build_metrics(kernel, tmp_path, out["code"])
    e = res["ellipses"]["e"]
    assert metrics["volume_mm3"] == pytest.approx(
        math.pi * e["a"] * e["b"] / 2.0, rel=1e-6)


def test_a_full_ellipse_rebuilds_to_pi_a_b(kernel, tmp_path):
    spec = _full_spec()
    res = solve_sketch(spec)
    metrics = build_metrics(kernel, tmp_path,
                            emit(res, spec, style="function")["code"])
    assert metrics["volume_mm3"] == pytest.approx(math.pi * A * B, rel=1e-9)


def test_the_solvers_anomaly_is_build123ds_anomaly(kernel, tmp_path):
    """The parametrization claim, checked against the library rather than
    against the docs: a quarter arc's *area* against the chord pins where its
    endpoints are, and they land where `c + R(phi)(a cos t, b sin t)` says."""
    spec = _half_ellipse_spec()
    spec["ellipses"][0]["start_deg"] = 30.0
    spec["ellipses"][0]["end_deg"] = 150.0
    res = solve_sketch(spec)
    e = res["ellipses"]["e"]
    for which, t in (("start", 30.0), ("end", 150.0)):
        assert (e[which]["x"], e[which]["y"]) == pytest.approx(
            _anomaly_point(e["cx"], e["cy"], e["a"], e["b"], e["rotation"], t),
            abs=1e-12)
    metrics = build_metrics(kernel, tmp_path,
                            emit(res, spec, style="function")["code"])
    # area of the elliptical segment between anomalies t1 and t2, closed by
    # the chord: (a*b/2) * (dt - sin dt) in the unrotated frame
    dt = math.radians(120.0)
    assert metrics["volume_mm3"] == pytest.approx(
        e["a"] * e["b"] / 2.0 * (dt - math.sin(dt)), rel=1e-6)
