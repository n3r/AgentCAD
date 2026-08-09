import math

import pytest

from agentcad.toolkit.sketch import solve_sketch


def test_rectangle_with_corner_fillet_dimensions():
    # Rectangle W x H anchored at origin; solve the far corner.
    spec = {
        "points": [
            {"name": "o", "x": 0, "y": 0, "fixed": True},
            {"name": "bx", "x": 30, "y": 1},
            {"name": "ty", "x": 1, "y": 20},
        ],
        "lines": [
            {"name": "bottom", "p1": "o", "p2": "bx"},
            {"name": "left", "p1": "o", "p2": "ty"},
        ],
        "circles": [],
        "constraints": [
            {"type": "horizontal", "ln": "bottom"},
            {"type": "vertical", "ln": "left"},
            {"type": "distance", "p": "o", "q": "bx", "d": 80},
            {"type": "distance", "p": "o", "q": "ty", "d": 40},
        ],
    }
    r = solve_sketch(spec)
    assert r["ok"]
    assert r["points"]["bx"]["x"] == pytest.approx(80, abs=1e-6)
    assert r["points"]["bx"]["y"] == pytest.approx(0, abs=1e-6)
    assert r["points"]["ty"]["y"] == pytest.approx(40, abs=1e-6)


def test_line_tangent_to_two_circles():
    spec = {
        "points": [
            {"name": "c1", "x": 0, "y": 0, "fixed": True},
            {"name": "c2", "x": 60, "y": 0, "fixed": True},
            {"name": "t1", "x": 0, "y": 10},
            {"name": "t2", "x": 60, "y": 5},
        ],
        "lines": [{"name": "tan", "p1": "t1", "p2": "t2"}],
        "circles": [
            {"name": "C1", "center": "c1", "r": 10, "fixed_r": True},
            {"name": "C2", "center": "c2", "r": 6, "fixed_r": True},
        ],
        "constraints": [
            {"type": "tangent_line_circle", "ln": "tan", "c": "C1", "at": "t1"},
            {"type": "tangent_line_circle", "ln": "tan", "c": "C2", "at": "t2"},
        ],
    }
    r = solve_sketch(spec)
    assert r["ok"]
    # tangency points lie on their circles
    d1 = math.hypot(r["points"]["t1"]["x"], r["points"]["t1"]["y"])
    assert d1 == pytest.approx(10, abs=1e-6)
    d2 = math.hypot(r["points"]["t2"]["x"] - 60, r["points"]["t2"]["y"])
    assert d2 == pytest.approx(6, abs=1e-6)


def test_solved_coords_feed_build123d():
    from build123d import BuildLine, BuildSketch, Polyline, make_face

    spec = {
        "points": [
            {"name": "a", "x": 0, "y": 0, "fixed": True},
            {"name": "b", "x": 40, "y": 1},
            {"name": "c", "x": 41, "y": 25},
            {"name": "d", "x": 1, "y": 24},
        ],
        "lines": [
            {"name": "ab", "p1": "a", "p2": "b"},
            {"name": "ad", "p1": "a", "p2": "d"},
        ],
        "circles": [],
        "constraints": [
            {"type": "horizontal", "ln": "ab"},
            {"type": "vertical", "ln": "ad"},
            {"type": "distance", "p": "a", "q": "b", "d": 50},
            {"type": "distance", "p": "a", "q": "d", "d": 30},
            {"type": "distance_x", "p": "a", "q": "c", "d": 50},
            {"type": "distance_y", "p": "a", "q": "c", "d": 30},
        ],
    }
    r = solve_sketch(spec)
    assert r["ok"]
    P = r["points"]
    pts = [(P[n]["x"], P[n]["y"]) for n in ("a", "b", "c", "d")]
    with BuildSketch() as sk:
        with BuildLine():
            Polyline(*pts, close=True)
        make_face()
    assert sk.sketch.area == pytest.approx(50 * 30, rel=1e-6)


def test_underconstrained_returns_error_not_crash():
    from agentcad.core.model import ValidationError
    from agentcad.core.service import AgentCADService, EventBus
    from agentcad.core.tools import build_registry

    # Just the tool wrapper: an unsatisfiable/degenerate constraint set.
    spec_entities = {"points": [{"name": "a", "x": 0, "y": 0},
                                {"name": "b", "x": 1, "y": 0}], "lines": [], "circles": []}
    # distance to itself impossible value handled; use contradictory distances
    from agentcad.toolkit.sketch import solve_sketch as ss
    res = ss({**spec_entities, "constraints": [
        {"type": "distance", "p": "a", "q": "b", "d": 10},
        {"type": "coincident", "p": "a", "q": "b"},
    ]})
    assert res["ok"] is False  # contradictory: can't be both 10 apart and coincident
