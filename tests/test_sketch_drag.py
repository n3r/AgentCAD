"""The drag protocol (PRD-009 slice 8, AC2's solver half).

The design's central measured correction: **warm-starting from the on-screen
state does not prevent the mirror flip — it causes it**, because the on-screen
state includes the cursor and the cursor crossed the branch boundary. What
works is the other shape: every parameter seeded from the **previous frame's
solution**, with the cursor entering as a *weighted soft objective*.

That objective is not a constraint, and the contract that follows is the thing
to get right: it is excluded from `ok`, `max_residual`, `n_residuals`, `rank`,
`dof` and the whole `diagnostics` block. Measured, including it makes every
drag of a fully-constrained entity report `ok: false` with `max_residual`
climbing to 2.43 over a 48 mm drag — a verdict about the cursor, not about the
sketch.
"""

import copy
import math
import statistics
import time

import pytest
from fastapi.testclient import TestClient

from agentcad.core import tools_sketch
from agentcad.core.tools import ToolRegistry, build_registry
from agentcad.server import routes_sketch
from agentcad.server.app import create_app
from agentcad.toolkit.sketch import DRAG_WEIGHT, SketchError, solve_sketch

from .conftest import make_test_service

# The design's mirror-flip probe, verbatim: a, b pinned on the x axis and c
# held by two distances, so there are exactly two solutions at
# (23.4375, +-18.7265). Dragging c downward crosses the branch boundary.
TRIANGLE = {
    "points": [{"name": "a", "x": 0.0, "y": 0.0, "fixed": True},
               {"name": "b", "x": 50.0, "y": 0.0, "fixed": True},
               {"name": "c", "x": 20.0, "y": 15.0}],
    "lines": [], "circles": [],
    "constraints": [{"type": "distance", "p": "a", "q": "c", "d": 30.0},
                    {"type": "distance", "p": "b", "q": "c", "d": 32.5}],
}

CURSOR_SWEEP = (18.0, 1.0, -1.0, -18.0, -30.0)


def seed_from(result: dict) -> dict:
    return {"points": {n: {"x": p["x"], "y": p["y"]}
                       for n, p in result["points"].items()}}


def test_the_branch_the_probe_measured():
    base = solve_sketch(TRIANGLE)
    assert base["points"]["c"]["x"] == pytest.approx(23.4375, abs=1e-4)
    assert base["points"]["c"]["y"] == pytest.approx(18.7265, abs=1e-4)


def test_seeding_the_dragged_point_at_the_cursor_flips_the_branch():
    """**The naive implementation, pinned as a failure.** This is the "warm
    start from the on-screen state" the PRD proposed; it flips the moment the
    cursor crosses the boundary. The test asserts the flip so the
    simplification can never be reintroduced as an improvement."""
    ys = []
    for cursor_y in CURSOR_SWEEP:
        seed = {"points": {"a": {"x": 0.0, "y": 0.0}, "b": {"x": 50.0, "y": 0.0},
                           "c": {"x": 23.4375, "y": cursor_y}}}
        res = solve_sketch({**TRIANGLE, "initial": seed})
        ys.append(res["points"]["c"]["y"])
    assert ys[0] > 0 and ys[-1] < 0, ys
    assert any(a > 0 > b for a, b in zip(ys, ys[1:])), ys


def test_the_weak_pull_seeded_from_the_previous_frame_never_flips():
    """**AC2's stability half.** Same sweep, same cursor, zero flips."""
    prev = solve_sketch(TRIANGLE)
    ys = []
    for cursor_y in CURSOR_SWEEP:
        prev = solve_sketch({**TRIANGLE, "initial": seed_from(prev),
                             "drag": {"point": "c", "x": 23.4375, "y": cursor_y}})
        assert prev["ok"] is True, prev
        ys.append(prev["points"]["c"]["y"])
    assert all(y > 18.0 for y in ys), ys
    assert prev["points"]["c"]["x"] == pytest.approx(23.4375, abs=1e-4)


def test_dragging_a_fully_constrained_entity_still_returns_ok():
    """The drag row must not poison the verdict. Measured: with the objective
    counted, a 48 mm drag reports `max_residual` 2.43 (= weight x the drag) and
    `ok: false`; the pull's own slack lives in `drag.gap` instead."""
    prev = solve_sketch(TRIANGLE)
    before = (prev["points"]["c"]["x"], prev["points"]["c"]["y"])
    res = solve_sketch({**TRIANGLE, "initial": seed_from(prev),
                        "drag": {"point": "c", "x": 23.4375, "y": -30.0}})
    assert res["ok"] is True
    assert res["max_residual"] < 1e-7, res["max_residual"]
    assert res["points"]["c"]["x"] == pytest.approx(before[0], abs=1e-6)
    assert res["points"]["c"]["y"] == pytest.approx(before[1], abs=1e-6)
    assert res["drag"]["gap"] == pytest.approx(48.7265, abs=1e-3)
    assert res["drag"]["weight"] == DRAG_WEIGHT
    # what the objective would have contributed if it were counted
    assert DRAG_WEIGHT * res["drag"]["gap"] == pytest.approx(2.436, abs=1e-2)


def test_a_drag_moves_the_free_dof_it_is_allowed_to_move():
    """The other half of the same contract: an under-constrained point follows
    the cursor along the DOF it has, and the constraint stays exact."""
    spec = {
        "points": [{"name": "a", "x": 0.0, "y": 0.0, "fixed": True},
                   {"name": "c", "x": 30.0, "y": 0.0}],
        "lines": [], "circles": [],
        "constraints": [{"type": "distance", "p": "a", "q": "c", "d": 30.0}],
    }
    base = solve_sketch(spec)
    assert base["dof"] == 1
    res = solve_sketch({**spec, "initial": seed_from(base),
                        "drag": {"point": "c", "x": 21.2132, "y": 21.2132}})
    assert res["ok"] is True and res["dof"] == 1
    assert math.hypot(res["points"]["c"]["x"], res["points"]["c"]["y"]) == \
        pytest.approx(30.0, abs=1e-7)
    assert res["points"]["c"]["y"] > 15.0


def test_the_reported_metrics_are_identical_with_and_without_a_drag():
    """`n_residuals`, `rank` and `dof` describe the constraint rows. A drag
    adds two rows to the solve and none to any of them."""
    base = solve_sketch(TRIANGLE)
    dragged = solve_sketch({**TRIANGLE, "initial": seed_from(base),
                            "drag": {"point": "c", "x": 40.0, "y": -12.0}})
    for key in ("n_params", "n_residuals", "rank", "dof"):
        assert dragged[key] == base[key], key
    for key in ("status", "dof", "rank", "n_residuals", "free_entities",
                "redundant", "conflicting"):
        assert dragged["diagnostics"][key] == base["diagnostics"][key], key


def test_a_drag_weight_that_is_not_positive_is_an_error():
    for weight in (0.0, -1.0, float("nan")):
        with pytest.raises(SketchError):
            solve_sketch({**TRIANGLE,
                          "drag": {"point": "c", "x": 1.0, "y": 2.0,
                                   "weight": weight}})


def test_dragging_an_unknown_or_fixed_handle_is_an_error():
    with pytest.raises(SketchError, match="unknown point handle"):
        solve_sketch({**TRIANGLE, "drag": {"point": "zz", "x": 0.0, "y": 0.0}})
    with pytest.raises(SketchError, match="no free parameters"):
        solve_sketch({**TRIANGLE, "drag": {"point": "a", "x": 0.0, "y": 0.0}})


def test_a_malformed_drag_block_is_an_error():
    with pytest.raises(SketchError, match="drag needs"):
        solve_sketch({**TRIANGLE, "drag": {"point": "c", "x": 1.0}})
    with pytest.raises(SketchError, match="unknown key"):
        solve_sketch({**TRIANGLE,
                      "drag": {"point": "c", "x": 1.0, "y": 2.0, "w": 3}})


def test_a_drag_on_a_virtual_handle_is_a_drag():
    """Arcs are dragged by their endpoints, and a handle is a point."""
    spec = {
        "points": [{"name": "ctr", "x": 0.0, "y": 0.0}],
        "arcs": [{"name": "a", "center": "ctr", "r": 12.7183,
                  "start_deg": 10.0, "end_deg": 100.0}],
        "constraints": [{"type": "radius", "c": "a", "r": 12.7183}],
    }
    base = solve_sketch(spec)
    seed = {"points": {"ctr": {"x": base["points"]["ctr"]["x"],
                               "y": base["points"]["ctr"]["y"]}},
            "arcs": {"a": {"r": base["arcs"]["a"]["r"],
                           "start_deg": base["arcs"]["a"]["start_deg"],
                           "end_deg": base["arcs"]["a"]["end_deg"]}}}
    res = solve_sketch({**spec, "initial": seed,
                        "drag": {"point": "a.end", "x": 0.0, "y": 20.0}})
    assert res["ok"] is True
    assert res["arcs"]["a"]["end"]["x"] == pytest.approx(0.0, abs=2.0)
    assert res["arcs"]["a"]["r"] == pytest.approx(12.7183, abs=1e-9)


# --------------------------------------------------------------------------
# the diagnostics cache
# --------------------------------------------------------------------------
def staircase(n_seg: int, redundant: bool = True) -> dict:
    """A dimensioned staircase, optionally with one duplicated dimension so
    the greedy dependent-set pass actually runs."""
    points = [{"name": "p0", "x": 0.0, "y": 0.0, "fixed": True}]
    lines, cons = [], []
    x = y = 0.0
    for i in range(n_seg):
        horiz = i % 2 == 0
        x, y = (x + 10.0, y) if horiz else (x, y + 7.0)
        points.append({"name": f"p{i + 1}", "x": x + 0.3, "y": y - 0.2})
        lines.append({"name": f"l{i}", "p1": f"p{i}", "p2": f"p{i + 1}"})
        cons.append({"type": "horizontal" if horiz else "vertical",
                     "ln": f"l{i}"})
        cons.append({"type": "distance", "p": f"p{i}", "q": f"p{i + 1}",
                     "d": 10.0 if horiz else 7.0})
    if redundant:
        cons.append({"type": "distance", "p": "p0", "q": "p1", "d": 10.0})
    return {"points": points, "lines": lines, "circles": [], "constraints": cons}


def without_ms(res: dict) -> dict:
    """A diagnostics block without its own wall-clock measurement."""
    return {k: v for k, v in res["diagnostics"].items() if k != "analysis_ms"}


def test_a_drag_frame_serves_the_cached_diagnostics_block():
    spec = staircase(6)
    full = solve_sketch({**spec, "diagnostics": "full"})
    assert full["diagnostics_source"] == "computed"
    frame = solve_sketch({**spec, "initial": seed_from(full),
                          "drag": {"point": "p3", "x": 12.0, "y": 9.0}})
    assert frame["diagnostics_source"] == "cached"
    # Everything but `analysis_ms`: a served block reports the time *this*
    # frame spent, because the cache no longer carries a verdict — it carries
    # the greedy dependent-row set, and the rank that set was found at is
    # re-verified against this frame's Jacobian (review 2, C10).
    assert without_ms(frame) == without_ms(full)
    # and `full` on the same drag frame recomputes it
    forced = solve_sketch({**spec, "initial": seed_from(full),
                           "diagnostics": "full",
                           "drag": {"point": "p3", "x": 12.0, "y": 9.0}})
    assert forced["diagnostics_source"] == "computed"
    assert forced["diagnostics"]["dof"] == full["diagnostics"]["dof"]
    assert forced["diagnostics"]["redundant"] == full["diagnostics"]["redundant"]


def redundant_box(dups: int) -> dict:
    """A rectangle with `dups` duplicated dimensions: a tiny solve and a large
    dependent-set pass, which is where the cache is worth measuring."""
    spec = {
        "points": [{"name": "a", "x": 0.0, "y": 0.0, "fixed": True},
                   {"name": "b", "x": 30.3, "y": 0.2},
                   {"name": "c", "x": 30.1, "y": 24.4},
                   {"name": "d", "x": 0.2, "y": 24.1}],
        "lines": [{"name": "ab", "p1": "a", "p2": "b"},
                  {"name": "bc", "p1": "b", "p2": "c"},
                  {"name": "cd", "p1": "c", "p2": "d"},
                  {"name": "da", "p1": "d", "p2": "a"}],
        "circles": [],
        "constraints": [
            {"type": "horizontal", "ln": "ab"}, {"type": "vertical", "ln": "bc"},
            {"type": "horizontal", "ln": "cd"}, {"type": "vertical", "ln": "da"},
            {"type": "distance", "p": "a", "q": "b", "d": 30.7183},
            {"type": "distance", "p": "b", "q": "c", "d": 24.3319}],
    }
    spec["constraints"] += [{"type": "distance", "p": "a", "q": "b",
                             "d": 30.7183} for _ in range(dups)]
    return spec


def test_the_cached_block_is_measurably_cheaper(capsys):
    """This is the whole point of the cache: rank and the dependent set are
    functions of the constraint set, and a drag changes no constraints."""
    spec = redundant_box(150)
    warm = solve_sketch({**spec, "diagnostics": "full"})
    frame = {**spec, "initial": seed_from(warm),
             "drag": {"point": "c", "x": 32.0, "y": 25.0}}

    def p50(mode):
        times = []
        for _ in range(7):
            t0 = time.perf_counter()
            res = solve_sketch({**frame, "diagnostics": mode})
            times.append((time.perf_counter() - t0) * 1e3)
        return statistics.median(times), res

    computed_ms, computed = p50("full")
    cached_ms, cached = p50("cached")
    with capsys.disabled():
        print(f"\n  {computed['n_residuals']} rows: diagnostics full   "
              f"{computed_ms:6.2f} ms/frame "
              f"(analysis {computed['diagnostics']['analysis_ms']:.2f} ms)")
        print(f"  {cached['n_residuals']} rows: diagnostics cached "
              f"{cached_ms:6.2f} ms/frame")
    assert cached["diagnostics_source"] == "cached"
    assert without_ms(cached) == without_ms(computed)
    assert cached_ms < computed_ms


def test_the_cache_key_separates_a_redundant_target_from_a_conflicting_one():
    """The trap the key exists to avoid: two specs with an **identical**
    residual structure and opposite verdicts. Keying on the structure alone
    would serve one sketch's `redundant` as the other's."""
    spec = staircase(4)
    good = solve_sketch({**spec, "diagnostics": "full"})
    assert good["diagnostics"]["redundant"] and not good["diagnostics"]["conflicting"]

    bad = copy.deepcopy(spec)
    bad["constraints"][-1]["d"] = 12.0        # same structure, contradictory
    out = solve_sketch({**bad, "diagnostics": "cached"})
    assert out["diagnostics_source"] == "computed"
    assert out["diagnostics"]["conflicting"], out["diagnostics"]


def test_a_constraint_edit_invalidates_the_cache():
    spec = staircase(4, redundant=False)
    first = solve_sketch({**spec, "diagnostics": "full"})
    assert first["diagnostics"]["dof"] == 0
    loosened = copy.deepcopy(spec)
    loosened["constraints"] = loosened["constraints"][:-1]
    out = solve_sketch({**loosened, "diagnostics": "cached"})
    assert out["diagnostics_source"] == "computed"
    assert out["diagnostics"]["dof"] == 1


def test_an_unknown_diagnostics_mode_is_an_error():
    with pytest.raises(SketchError, match="diagnostics must be one of"):
        solve_sketch({**TRIANGLE, "diagnostics": "sometimes"})


# --------------------------------------------------------------------------
# the surfaces
# --------------------------------------------------------------------------
def test_the_tool_forwards_drag_and_diagnostics():
    registry = ToolRegistry()
    tools_sketch.register(registry, None)
    base = solve_sketch(TRIANGLE)
    res = registry.get("solve_sketch").handler(
        entities={k: TRIANGLE[k] for k in ("points", "lines", "circles")},
        constraints=TRIANGLE["constraints"], initial=seed_from(base),
        drag={"point": "c", "x": 23.4375, "y": -30.0}, diagnostics="cached")
    assert res["ok"] is True
    assert res["drag"]["gap"] == pytest.approx(48.7265, abs=1e-3)
    assert res["diagnostics_source"] in ("cached", "computed")


@pytest.mark.integration
def test_the_route_forwards_drag_and_is_not_a_coroutine(tmp_path):
    """The handler must be a **sync `def`**: FastAPI runs a sync handler in the
    threadpool, while an `async def` would run the solver on the event loop and
    block the `/ws` channel for the length of the solve."""
    import inspect

    router = routes_sketch.build_router(None, None)
    handler = router.routes[0].endpoint
    assert not inspect.iscoroutinefunction(handler), (
        "the sketch route ran the solver on the event loop")

    service = make_test_service(tmp_path / "projects", None)
    client = TestClient(create_app(service, build_registry(service)),
                        base_url="http://127.0.0.1")
    body = {"entities": {k: TRIANGLE[k] for k in ("points", "lines", "circles")},
            "constraints": TRIANGLE["constraints"]}
    base = client.post("/api/sketch/solve", json=body).json()
    frame = client.post("/api/sketch/solve", json={
        **body, "initial": seed_from(base), "diagnostics": "cached",
        "drag": {"point": "c", "x": 23.4375, "y": -30.0}}).json()
    assert frame["ok"] is True
    assert frame["points"]["c"]["y"] > 18.0
    assert frame["drag"]["point"] == "c"

    bad = client.post("/api/sketch/solve", json={
        **body, "drag": {"point": "nope", "x": 0.0, "y": 0.0}}).json()
    assert bad["error"]["type"] == "validation_error"
