"""`specToModel` and `entitiesSpec` are an inverse pair — asserted, in node.

Review 2 (finding C13) found they were not, in four separate ways, and every
one of them is silent: a construction spline or slot came back as **emitted
geometry** after one GUI round trip, a fixed-radius arc came back with a free
radius, and a three-point arc opened as `{start, mid, end}` and re-serialized
as a centre form full of `undefined` — a validation error where the user
expected their sketch.

None of that is reachable from Python, and none of it is visible in a
screenshot: it is a property of two functions that have to compose to the
identity. So this module runs them, in node, over a spec carrying **every
entity kind and every flag**, and compares what the next solve would send with
what the block said. `frontend/js/sketcher.js` exports `__roundTrip__` for
exactly this and nothing else.

The one deliberate exception is the three-point arc, which the canvas has no
representation for: it is normalized to the centre form (its circumcentre
becomes a real point), and the test asserts the *geometry* survives rather than
the spelling.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[1] / "frontend" / "js"
SKETCHER = FRONTEND / "sketcher.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not installed")

HARNESS = """
import {{ __roundTrip__ }} from {module};
const spec = JSON.parse(process.env.AGENTCAD_SPEC);
process.stdout.write(JSON.stringify({{
  entities: __roundTrip__.entitiesOf(spec),
  constraints: __roundTrip__.constraintsOf(spec),
}}));
"""


def round_trip(spec: dict) -> dict:
    """`entitiesSpec(specToModel(spec))`, run through the real module."""
    script = HARNESS.format(module=json.dumps(SKETCHER.as_uri()))
    out = subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        capture_output=True, text=True, timeout=60,
        env={**os.environ, "AGENTCAD_SPEC": json.dumps(spec)})
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def every_kind_spec() -> dict:
    """One of every entity, and both settings of every flag that survives."""
    return {
        "v": 2,
        "entities": {
            "points": [
                {"name": "p1", "x": 0.0, "y": 0.0, "fixed": True},
                {"name": "p2", "x": 30.0, "y": 0.0, "fixed": False},
                {"name": "p3", "x": 30.0, "y": 20.0, "fixed": False},
                {"name": "p4", "x": 0.0, "y": 20.0, "fixed": False},
                {"name": "p5", "x": 12.0, "y": 40.0, "fixed": False},
                {"name": "p6", "x": 60.0, "y": 0.0, "fixed": False},
                {"name": "p7", "x": 80.0, "y": 0.0, "fixed": False},
                {"name": "p8", "x": -20.0, "y": -20.0, "fixed": False,
                 "construction": True},
                {"name": "p9", "x": 4.0, "y": 44.0, "fixed": False},
            ],
            "lines": [
                {"name": "ln1", "p1": "p1", "p2": "p2"},
                {"name": "ln2", "p1": "p3", "p2": "p4", "construction": True},
            ],
            "circles": [
                {"name": "c1", "center": "p2", "r": 5.0},
                {"name": "c2", "center": "p3", "r": 6.0, "fixed_r": True},
                {"name": "c3", "center": "p4", "r": 7.0, "construction": True},
            ],
            "arcs": [
                {"name": "a1", "center": "p1", "r": 9.0,
                 "start_deg": 0.0, "end_deg": 90.0},
                {"name": "a2", "center": "p2", "r": 8.0,
                 "start_deg": 10.0, "end_deg": 100.0, "fixed_r": True},
                {"name": "a3", "center": "p3", "r": 4.0,
                 "start_deg": 20.0, "end_deg": 110.0, "fixed": True},
                {"name": "a4", "center": "p4", "r": 3.0,
                 "start_deg": 30.0, "end_deg": 120.0, "construction": True},
            ],
            "ellipses": [
                {"name": "e1", "center": "p5", "a": 12.0, "b": 6.0,
                 "rotation": 15.0},
                {"name": "e2", "center": "p5", "a": 9.0, "b": 4.0,
                 "rotation": 0.0, "start_deg": 10.0, "end_deg": 200.0},
                {"name": "e3", "center": "p5", "a": 3.0, "b": 2.0,
                 "rotation": 0.0, "construction": True},
            ],
            "splines": [
                {"name": "sp1", "points": ["p1", "p5", "p9"]},
                {"name": "sp2", "points": ["p2", "p5", "p9"],
                 "construction": True},
            ],
            "slots": [
                {"name": "sl1", "c1": "p6", "c2": "p7", "width": 6.0},
                {"name": "sl2", "c1": "p1", "c2": "p7", "width": 4.0,
                 "construction": True},
            ],
        },
        "constraints": [
            {"type": "horizontal", "ln": "ln1"},
            {"type": "distance", "p": "p1", "q": "p2", "d": 30.0},
        ],
        "plane": {"origin": [0, 0, 5], "x_dir": [1, 0, 0], "y_dir": [0, 1, 0],
                  "normal": [0, 0, 1], "face_index": 3},
    }


KINDS = ("points", "lines", "circles", "arcs", "ellipses", "splines", "slots")


def test_every_entity_kind_and_flag_survives_the_pair():
    """The property: what the next solve sends is what the block said."""
    spec = every_kind_spec()
    got = round_trip(spec)["entities"]
    for kind in KINDS:
        want = spec["entities"][kind]
        assert [e["name"] for e in got[kind]] == [e["name"] for e in want], kind
        for before, after in zip(want, got[kind]):
            for flag in ("fixed", "fixed_r", "construction"):
                assert bool(after.get(flag)) == bool(before.get(flag)), (
                    kind, before["name"], flag)


@pytest.mark.parametrize("kind,flag", [
    ("points", "construction"), ("lines", "construction"),
    ("circles", "construction"), ("circles", "fixed_r"),
    ("arcs", "construction"), ("arcs", "fixed_r"), ("arcs", "fixed"),
    ("ellipses", "construction"),
    ("splines", "construction"), ("slots", "construction"),
])
def test_the_flag_is_carried_by_the_entity_that_had_it(kind, flag):
    """One flag at a time, so a failure names the flag rather than the spec.

    `splines`/`slots` `construction` and `arcs` `fixed_r` are the three the
    review measured being dropped — a construction spline became emitted
    geometry, and an arc's fixed radius became free.
    """
    spec = every_kind_spec()
    marked = [e["name"] for e in spec["entities"][kind] if e.get(flag)]
    assert marked, f"the corpus has no {kind} carrying {flag!r}"
    got = {e["name"]: e for e in round_trip(spec)["entities"][kind]}
    for name in marked:
        assert got[name].get(flag) is True, (kind, name, flag, got[name])
    for entity in spec["entities"][kind]:
        if entity["name"] not in marked:
            assert not got[entity["name"]].get(flag), (kind, entity["name"])


def test_the_constraints_survive_untouched():
    spec = every_kind_spec()
    assert round_trip(spec)["constraints"] == spec["constraints"]


def test_a_spline_keeps_its_control_points():
    spec = every_kind_spec()
    got = {s["name"]: s for s in round_trip(spec)["entities"]["splines"]}
    assert got["sp1"]["points"] == ["p1", "p5", "p9"]


def test_a_bounded_ellipse_keeps_its_bounds_and_a_full_one_gains_none():
    got = {e["name"]: e for e in round_trip(every_kind_spec())["entities"]
           ["ellipses"]}
    assert got["e2"]["start_deg"] == 10.0 and got["e2"]["end_deg"] == 200.0
    assert "start_deg" not in got["e1"]


def three_point_spec() -> dict:
    return {
        "v": 2,
        "entities": {"arcs": [{"name": "a1", "start": [0.0, 0.0],
                               "mid": [1.0, 1.0], "end": [2.0, 0.0]}]},
        "constraints": [],
    }


def test_a_three_point_arc_reopens_as_the_same_geometry():
    """The one deliberate normalization. It used to come back as a centre form
    with `center`, `r`, `start_deg` and `end_deg` all `undefined`, which the
    solve route rejects — an agent-authored block that could not be opened."""
    got = round_trip(three_point_spec())["entities"]
    arc = got["arcs"][0]
    assert arc["name"] == "a1"
    assert arc["r"] == pytest.approx(1.0, abs=1e-9)
    assert isinstance(arc["center"], str) and arc["center"]
    centre = {p["name"]: p for p in got["points"]}[arc["center"]]
    assert centre["x"] == pytest.approx(1.0, abs=1e-9)
    assert centre["y"] == pytest.approx(0.0, abs=1e-9)
    # the sweep runs start -> mid -> end, so it is the upper half turn
    sweep = arc["end_deg"] - arc["start_deg"]
    assert abs(sweep) == pytest.approx(180.0, abs=1e-9)
    for key in ("center", "r", "start_deg", "end_deg"):
        assert arc[key] is not None, arc


def test_the_normalized_three_point_arc_solves():
    """And what comes back is a spec the server accepts — which is the whole
    complaint: the old output was rejected."""
    from agentcad.toolkit.sketch import solve_sketch
    ents = round_trip(three_point_spec())["entities"]
    res = solve_sketch({**ents, "constraints": []})
    assert res["ok"] is True, res["diagnostics"]
    assert res["arcs"]["a1"]["r"] == pytest.approx(1.0, abs=1e-9)


def test_the_round_trip_is_idempotent():
    """Twice is once: a normalization that keeps normalizing is a drift."""
    once = round_trip(every_kind_spec())["entities"]
    twice = round_trip({"v": 2, "entities": once, "constraints": []})["entities"]
    assert twice == once


def test_a_collinear_three_point_arc_is_dropped_rather_than_thrown():
    """There is no arc through three collinear points and the solver refuses
    the spec; the canvas must not take the panel down over it."""
    spec = three_point_spec()
    spec["entities"]["arcs"][0]["mid"] = [1.0, 0.0]
    assert round_trip(spec)["entities"]["arcs"] == []
