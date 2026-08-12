"""`initial` — the warm start, activated (PRD-009 FR4, slice 4).

`initial` was declared in the tool schema as "unused; reserved" and the route
did not even forward it. It is real now, and the thing to keep straight is what
it is *for*: it **selects the solution branch**, it is not the speed mechanism.
Measured on the v1 solver, seeding exactly at the solution cost 20 ms and
seeding 0.4 mm away cost 51 ms — iterations were never the cost, the Jacobian
was. So these tests are about *which* solution comes back, not how fast.
"""

import pytest
from fastapi.testclient import TestClient

from agentcad.core import tools_sketch
from agentcad.core.model import ValidationError
from agentcad.core.tools import ToolRegistry, build_registry
from agentcad.server.app import create_app
from agentcad.toolkit.sketch import SketchError, solve_sketch

from .conftest import make_test_service

# a and b pinned on the x axis, c held by two distances: two mirror solutions
# at (18, +24) and (18, -24). The spec's own guess is above the axis.
MIRROR = {
    "points": [{"name": "a", "x": 0, "y": 0, "fixed": True},
               {"name": "b", "x": 50, "y": 0, "fixed": True},
               {"name": "c", "x": 10, "y": 20}],
    "lines": [],
    "circles": [],
    "constraints": [{"type": "distance", "p": "a", "q": "c", "d": 30},
                    {"type": "distance", "p": "b", "q": "c", "d": 40}],
}


def sketch_tool():
    registry = ToolRegistry()
    tools_sketch.register(registry, None)
    return registry.get("solve_sketch")


def call_tool(spec, initial=None):
    return sketch_tool().handler(
        entities={k: spec[k] for k in ("points", "lines", "circles")},
        constraints=spec["constraints"], initial=initial)


@pytest.mark.parametrize("seed_y,want_y", [(-20.0, -24.0), (20.0, 24.0)])
def test_initial_selects_the_solution_branch(seed_y, want_y):
    """The same spec, the same starting coordinates in `points`, two different
    answers — chosen by `initial` alone. This is what FR4 buys."""
    result = solve_sketch({**MIRROR,
                           "initial": {"points": {"c": {"x": 10, "y": seed_y}}}})
    assert result["ok"] is True
    assert result["warm_started"] is True
    assert result["warnings"] == []
    assert result["points"]["c"]["x"] == pytest.approx(18.0, abs=1e-9)
    assert result["points"]["c"]["y"] == pytest.approx(want_y, abs=1e-9)


def test_without_initial_the_spec_coordinates_still_choose():
    """No `initial` is a cold start, honestly reported."""
    result = solve_sketch(MIRROR)
    assert result["warm_started"] is False
    assert result["warnings"] == []
    assert result["points"]["c"]["y"] == pytest.approx(24.0, abs=1e-9)


def test_an_unknown_entity_name_is_a_validation_error():
    """FR4, verbatim. A silent ignore turns a client desync into a sketch that
    mysteriously stops warm-starting."""
    with pytest.raises(SketchError, match="unknown point 'nope'"):
        solve_sketch({**MIRROR, "initial": {"points": {"nope": {"x": 1, "y": 2}}}})
    with pytest.raises(SketchError, match="unknown circle 'nope'"):
        solve_sketch({**MIRROR, "initial": {"circles": {"nope": {"r": 2}}}})
    with pytest.raises(ValidationError, match="unknown point"):
        call_tool(MIRROR, initial={"points": {"nope": {"x": 1, "y": 2}}})


def test_an_unknown_initial_section_is_a_validation_error():
    with pytest.raises(SketchError, match="unknown section"):
        solve_sketch({**MIRROR, "initial": {"pionts": {"c": {"x": 1, "y": 2}}}})


def test_an_entity_added_mid_drag_degrades_to_a_cold_start():
    """The client gained an entity between frames. Never crash, never seed
    half a sketch, and never claim a warm start that did not happen."""
    spec = {
        "points": MIRROR["points"] + [{"name": "d", "x": 5, "y": 5}],
        "lines": [], "circles": [],
        "constraints": MIRROR["constraints"] + [
            {"type": "distance", "p": "a", "q": "d", "d": 10},
            {"type": "distance", "p": "b", "q": "d", "d": 45}],
        # the previous frame's solution, which knows nothing about `d`
        "initial": {"points": {"c": {"x": 10, "y": -20}}},
    }
    result = solve_sketch(spec)
    assert result["ok"] is True
    assert result["warm_started"] is False
    assert [w["code"] for w in result["warnings"]] == ["initial_incomplete"]
    assert result["warnings"][0]["entities"] == ["d"]
    # cold start means the spec's own coordinates decided the branch
    assert result["points"]["c"]["y"] == pytest.approx(24.0, abs=1e-9)


def test_a_half_covered_entity_is_also_incomplete():
    """`{"x": 12}` with no `y` is a desync, not a partial seed."""
    result = solve_sketch({**MIRROR, "initial": {"points": {"c": {"x": 12}}}})
    assert result["warm_started"] is False
    assert result["warnings"][0]["code"] == "initial_incomplete"
    assert result["ok"] is True


def test_initial_cannot_un_fix_a_fixed_point():
    """It seeds x0; it never edits the spec."""
    result = solve_sketch({**MIRROR, "initial": {"points": {
        "a": {"x": 5, "y": 5}, "c": {"x": 10, "y": -20}}}})
    assert result["warm_started"] is True
    assert result["points"]["a"] == {"x": 0.0, "y": 0.0}
    assert result["points"]["c"]["y"] == pytest.approx(-24.0, abs=1e-9)


def test_initial_cannot_override_a_fixed_radius():
    spec = {
        "points": [{"name": "o", "x": 0, "y": 0, "fixed": True},
                   {"name": "p", "x": 5, "y": 5}],
        "lines": [], "circles": [{"name": "C", "center": "o", "r": 10,
                                  "fixed_r": True}],
        "constraints": [{"type": "point_on_circle", "p": "p", "c": "C"},
                        {"type": "distance_y", "p": "o", "q": "p", "d": 0}],
        "initial": {"points": {"p": {"x": -5, "y": 1}},
                    "circles": {"C": {"r": 999.0}}},
    }
    result = solve_sketch(spec)
    assert result["ok"] is True
    assert result["warm_started"] is True
    assert result["circles"]["C"]["r"] == 10.0
    # the seed still chose the branch: the left-hand tangency point
    assert result["points"]["p"]["x"] == pytest.approx(-10.0, abs=1e-9)


def test_a_free_radius_is_seeded_and_must_be_covered():
    spec = {
        "points": [{"name": "o", "x": 0, "y": 0, "fixed": True}],
        "lines": [], "circles": [{"name": "C", "center": "o", "r": 3}],
        "constraints": [],
    }
    warm = solve_sketch({**spec, "initial": {"circles": {"C": {"r": 12.5}}}})
    assert warm["warm_started"] is True
    assert warm["circles"]["C"]["r"] == pytest.approx(12.5, abs=1e-12)

    cold = solve_sketch({**spec, "initial": {"points": {}}})
    assert cold["warm_started"] is False
    assert cold["warnings"][0]["entities"] == ["C"]
    assert cold["circles"]["C"]["r"] == pytest.approx(3.0, abs=1e-12)


def test_initial_does_not_change_what_is_reported():
    """Warm and cold agree on everything except `warm_started` when they land
    on the same branch — `initial` is a starting point, not a constraint."""
    warm = solve_sketch({**MIRROR, "initial": {"points": {"c": {"x": 9, "y": 25}}}})
    cold = solve_sketch(MIRROR)
    for key in ("n_params", "n_residuals", "rank", "dof"):
        assert warm[key] == cold[key]
    assert warm["diagnostics"]["status"] == cold["diagnostics"]["status"]
    assert warm["points"]["c"]["y"] == pytest.approx(
        cold["points"]["c"]["y"], abs=1e-9)


def test_the_tool_forwards_initial_to_the_solver():
    result = call_tool(MIRROR, initial={"points": {"c": {"x": 10, "y": -20}}})
    assert result["warm_started"] is True
    assert result["points"]["c"]["y"] == pytest.approx(-24.0, abs=1e-9)


@pytest.mark.integration
def test_the_route_forwards_initial_to_the_solver(kernel, tmp_path):
    """The route pack whitelists explicit keys, so a key it does not name is
    dead however well the solver supports it — which is exactly what `initial`
    was before this slice."""
    service = make_test_service(tmp_path / "projects", kernel)
    client = TestClient(create_app(service, build_registry(service)),
                        base_url="http://127.0.0.1")
    body = {"entities": {k: MIRROR[k] for k in ("points", "lines", "circles")},
            "constraints": MIRROR["constraints"]}

    above = client.post("/api/sketch/solve", json=body).json()
    assert above["warm_started"] is False
    assert above["points"]["c"]["y"] == pytest.approx(24.0, abs=1e-9)

    below = client.post("/api/sketch/solve", json={
        **body, "initial": {"points": {"c": {"x": 10, "y": -20}}}}).json()
    assert below["warm_started"] is True
    assert below["points"]["c"]["y"] == pytest.approx(-24.0, abs=1e-9)

    bad = client.post("/api/sketch/solve", json={
        **body, "initial": {"points": {"nope": {"x": 0, "y": 0}}}}).json()
    assert bad["error"]["type"] == "validation_error"
    assert "unknown point" in bad["error"]["message"]
