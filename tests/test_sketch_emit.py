"""The shared server-side emitter (PRD-009 slice 7, AC1).

Two properties are load-bearing here and both are measured, not assumed:

- **One emitter, both layers.** The GUI (the `/api/sketch/solve` route) and an
  agent (the `solve_sketch` tool) must produce **byte-identical** code for the
  same spec. That identity *is* AC1's "one solver, both layers" thesis applied
  to emission, so it is tested by generating from both paths and comparing
  bytes.
- **Emitted code that rebuilds.** A centre-parametrized arc chain rounded to 6
  decimals leaves a 7.58e-7 mm gap and `make_face()` raises *"Face can only be
  created with closed wires"*. The bug **only reproduces on non-round
  coordinates** — the same profile with tidy numbers closes at 3 decimals and
  proves nothing — so every profile here has irrational junctions.

Tests that *build* the emitted source go through the session-scoped `kernel`
fixture: the server process (and this module) may not import OCP.
"""

import json
import math
import re
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from agentcad.core import sketch_emit, tools_sketch
from agentcad.core.model import ValidationError
from agentcad.core.sketch_emit import EmitError, emit
from agentcad.core.tools import ToolRegistry, build_registry
from agentcad.server.app import create_app
from agentcad.toolkit.sketch import solve_sketch

from .conftest import make_test_service

# --------------------------------------------------------------------------
# specs — every one of them deliberately non-round
# --------------------------------------------------------------------------
CAM_R_BIG = 18.3691
CAM_R_SMALL = 7.2143
CAM_SEP = 41.7259
SLOT_WIDTH = 9.4271


def cam_spec() -> dict:
    """A cam lobe: two arcs of different radii joined by two tangent lines.

    Every line runs **between the arcs' virtual handles**, so the four
    junctions are structural and the four tangencies take the perpendicular
    form (slice 6's measurement). The radii and the centre separation are
    irrational-ish on purpose: the junction coordinates are then non-round,
    which is the only regime in which the rounding bug reproduces.
    """
    return {
        "points": [{"name": "cL", "x": 0.0, "y": 0.0, "fixed": True},
                   {"name": "cR", "x": 40.0, "y": 0.5}],
        "arcs": [
            {"name": "L", "center": "cL", "r": 18.0,
             "start_deg": 100.0, "end_deg": 260.0},
            {"name": "R", "center": "cR", "r": 7.0,
             "start_deg": 280.0, "end_deg": 80.0},
        ],
        "lines": [{"name": "top", "p1": "L.start", "p2": "R.end"},
                  {"name": "bot", "p1": "R.start", "p2": "L.end"}],
        "constraints": [
            {"type": "radius", "c": "L", "r": CAM_R_BIG},
            {"type": "radius", "c": "R", "r": CAM_R_SMALL},
            {"type": "distance_x", "p": "cL", "q": "cR", "d": CAM_SEP},
            {"type": "distance_y", "p": "cL", "q": "cR", "d": 0.0},
            {"type": "tangent", "a": "top", "b": "L"},
            {"type": "tangent", "a": "top", "b": "R"},
            {"type": "tangent", "a": "bot", "b": "R"},
            {"type": "tangent", "a": "bot", "b": "L"},
        ],
    }


def slotted_cam_spec() -> dict:
    """AC1's sketch: the cam lobe **and** a slot, in one profile."""
    spec = cam_spec()
    spec["points"] += [{"name": "sa", "x": -6.1387, "y": -33.4271, "fixed": True},
                       {"name": "sb", "x": 26.5139, "y": -37.9163, "fixed": True}]
    spec["slots"] = [{"name": "sl", "c1": "sa", "c2": "sb", "width": SLOT_WIDTH}]
    return spec


def square_spec() -> dict:
    """A pure-line closed chain, for the Polyline path."""
    return {
        "points": [{"name": "a", "x": 0.0, "y": 0.0, "fixed": True},
                   {"name": "b", "x": 30.0, "y": 1.0},
                   {"name": "c", "x": 31.0, "y": 25.0},
                   {"name": "d", "x": 1.0, "y": 24.0}],
        "lines": [{"name": "ab", "p1": "a", "p2": "b"},
                  {"name": "bc", "p1": "b", "p2": "c"},
                  {"name": "cd", "p1": "c", "p2": "d"},
                  {"name": "da", "p1": "d", "p2": "a"}],
        "constraints": [
            {"type": "horizontal", "ln": "ab"},
            {"type": "vertical", "ln": "bc"},
            {"type": "horizontal", "ln": "cd"},
            {"type": "vertical", "ln": "da"},
            {"type": "distance", "p": "a", "q": "b", "d": 30.7183},
            {"type": "distance", "p": "b", "q": "c", "d": 24.3319},
        ],
    }


def solved(spec: dict) -> dict:
    out = solve_sketch(spec)
    assert out["ok"] is True, out["diagnostics"]
    return out


# --------------------------------------------------------------------------
# geometry the emitted code has to reproduce (computed from the SOLVED values)
# --------------------------------------------------------------------------
def _arc_extremes(cx, cy, r, t1, t2):
    """Endpoints plus every axis-aligned extreme the sweep actually passes."""
    pts = [(cx + r * math.cos(t1), cy + r * math.sin(t1)),
           (cx + r * math.cos(t2), cy + r * math.sin(t2))]
    lo, hi = min(t1, t2), max(t1, t2)
    k = math.floor(lo / (math.pi / 2))
    while k * math.pi / 2 <= hi:
        t = k * math.pi / 2
        if lo <= t <= hi:
            pts.append((cx + r * math.cos(t), cy + r * math.sin(t)))
        k += 1
    return pts


def cam_area_and_bbox(sol: dict) -> tuple[float, tuple]:
    """Exact area (Green's theorem) and bbox of the solved cam profile.

    The area of an arc from t1 to t2 about (cx, cy) contributes
    ``(r^2 dt + cx (y2 - y1) - cy (x2 - x1)) / 2`` to the contour integral;
    a segment contributes ``(ax by - ay bx) / 2``.
    """
    arcs = sol["arcs"]
    area = 0.0
    xs, ys = [], []
    # walk: L.start -top-> R.end -R reversed-> R.start -bot-> L.end -L rev-> L.start
    def seg(a, b):
        return (a[0] * b[1] - a[1] * b[0]) / 2.0

    def arc_term(a, t1, t2):
        cx, cy, r = a["cx"], a["cy"], a["r"]
        x1, y1 = cx + r * math.cos(t1), cy + r * math.sin(t1)
        x2, y2 = cx + r * math.cos(t2), cy + r * math.sin(t2)
        for px, py in _arc_extremes(cx, cy, r, t1, t2):
            xs.append(px)
            ys.append(py)
        return (r * r * (t2 - t1) + cx * (y2 - y1) - cy * (x2 - x1)) / 2.0

    L, R = arcs["L"], arcs["R"]
    lt1, lt2 = math.radians(L["start_deg"]), math.radians(L["end_deg"])
    rt1, rt2 = math.radians(R["start_deg"]), math.radians(R["end_deg"])
    l_start = (L["start"]["x"], L["start"]["y"])
    l_end = (L["end"]["x"], L["end"]["y"])
    r_start = (R["start"]["x"], R["start"]["y"])
    r_end = (R["end"]["x"], R["end"]["y"])
    area += (seg(l_start, r_end) + arc_term(R, rt2, rt1)
             + seg(r_start, l_end) + arc_term(L, lt2, lt1))
    for px, py in (l_start, l_end, r_start, r_end):
        xs.append(px)
        ys.append(py)
    return abs(area), (min(xs), min(ys), max(xs), max(ys))


def slot_area_and_bbox(sol: dict, name: str = "sl") -> tuple[float, tuple]:
    s = sol["slots"][name]
    c1, c2, r = s["center1"], s["center2"], s["r"]
    sep = math.hypot(c2["x"] - c1["x"], c2["y"] - c1["y"])
    area = math.pi * r * r + 2 * r * sep
    xs = [c1["x"] - r, c1["x"] + r, c2["x"] - r, c2["x"] + r]
    ys = [c1["y"] - r, c1["y"] + r, c2["y"] - r, c2["y"] + r]
    return area, (min(xs), min(ys), max(xs), max(ys))


PART_TEMPLATE = '''\
from build123d import *

PARAMS = {}


%s


def build(p):
    return extrude(sketch_profile(), amount=1.0)
'''


def part_script(code: str) -> str:
    return PART_TEMPLATE % code


def build_metrics(kernel, tmp_path, code: str) -> dict:
    return kernel.request("build", {
        "script": part_script(code), "params": {},
        "mesh_path": str(tmp_path / "m.acm"),
    })["metrics"]


# --------------------------------------------------------------------------
# the emitter's own contract
# --------------------------------------------------------------------------
def test_the_emitter_never_imports_ocp():
    """`core/sketch_emit.py` runs in the server process (FR9): emitting
    build123d source is not importing build123d."""
    code = ("import sys; import agentcad.core.sketch_emit; "
            "print('OCP' in sys.modules or 'build123d' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, check=True)
    assert out.stdout.strip() == "False", out.stdout


def test_emitted_code_never_imports_agentcad():
    """G6: the emitted script must run under plain build123d — the only
    `agentcad` in it is the marker comment the GUI has always written."""
    code = emit(solved(slotted_cam_spec()), slotted_cam_spec())["code"]
    assert "import" not in code
    assert "agentcad." not in code


def test_emission_is_deterministic():
    spec = slotted_cam_spec()
    sol = solved(spec)
    assert emit(sol, spec)["code"] == emit(sol, spec)["code"]
    assert emit(solved(spec), spec)["code"] == emit(sol, spec)["code"]


def test_precision_is_nine_decimals():
    """Up from `fmtNum`'s 6 — the measured first-safe precision is 7, and 9
    leaves two orders of margin while still reading as an editable number."""
    code = emit(solved(cam_spec()), cam_spec())["code"]
    longest = max((frac for line in code.splitlines()
                   for frac in _fractions(line)), key=len, default="")
    assert len(longest) <= 9
    assert len(longest) >= 8, f"no coordinate needed the precision: {code}"


def _fractions(line: str) -> list[str]:
    out, i = [], 0
    while (i := line.find(".", i) + 1) > 0:
        j = i
        while j < len(line) and line[j].isdigit():
            j += 1
        out.append(line[i:j])
    return out


def test_a_junction_is_one_shared_literal_referenced_by_both_curves():
    """Rule 1 of design Decision 10: a vertex is formatted **once**. Each
    junction of the cam becomes one `v<n> = (...)` binding, and the two curves
    that meet there both name it."""
    code = emit(solved(cam_spec()), cam_spec())["code"]
    binds = [l.strip() for l in code.splitlines() if l.strip().startswith("v")]
    assert len(binds) == 4, code
    for bind in binds:
        name = bind.split(" =")[0]
        # bound once, used by exactly the two curves that share it
        assert code.count(f"{name} = ") == 1
        assert sum(l.count(name) for l in code.splitlines()
                   if not l.strip().startswith(f"{name} = ")) == 2, (name, code)


def test_arcs_are_endpoint_anchored_on_the_shared_solved_endpoint():
    """Slice 5 and slice 6 both measured this: never recompute an endpoint
    from centre + radius + angle. `RadiusArc` takes the same rounded pair the
    neighbouring lines take."""
    code = emit(solved(cam_spec()), cam_spec())["code"]
    assert "CenterArc" not in code
    assert code.count("RadiusArc(") == 2
    for line in code.splitlines():
        if "RadiusArc(" in line:
            args = line.split("RadiusArc(")[1]
            assert args.startswith("v"), line
            assert args.split(", ")[1].startswith("v"), line


def test_the_short_and_long_sagitta_flags_follow_the_solved_sweep():
    """Measured against build123d 0.11.1: the short sagitta is the minor arc
    and the sign of `radius` picks the side the centre falls on. Both cam arcs
    sweep more than 180 deg, so both are long-sagitta."""
    sol, spec = solved(cam_spec()), cam_spec()
    for name in ("L", "R"):
        sweep = sol["arcs"][name]["end_deg"] - sol["arcs"][name]["start_deg"]
        assert abs(sweep) > 180.0, sweep
    code = emit(sol, spec)["code"]
    calls = [l for l in code.splitlines() if "RadiusArc(" in l]
    assert len(calls) == 2
    assert all("short_sagitta=False" in c for c in calls), calls


def test_a_three_point_authored_arc_emits_a_three_point_arc():
    """`arcs[n]["authored"]` is the emitter's constructor switch."""
    spec = {
        "points": [{"name": "p", "x": 0.0, "y": 0.0, "fixed": True}],
        "arcs": [{"name": "a", "start": [0.0, 0.0], "mid": [7.3141, 4.1379],
                  "end": [16.7183, 1.2345]}],
        "constraints": [],
    }
    sol = solved(spec)
    assert sol["arcs"]["a"]["authored"] == "three_point"
    code = emit(sol, spec)["code"]
    assert "ThreePointArc(" in code and "RadiusArc(" not in code


def test_a_pure_line_chain_emits_a_polyline():
    code = emit(solved(square_spec()), square_spec())["code"]
    assert code.count("Polyline(") == 1
    assert "make_face()" in code


def test_a_circle_still_emits_under_locations():
    spec = {
        "points": [{"name": "c", "x": 3.1379, "y": -2.7183, "fixed": True}],
        "circles": [{"name": "hole", "center": "c", "r": 4.0}],
        "constraints": [{"type": "radius", "c": "hole", "r": 4.7183}],
    }
    code = emit(solved(spec), spec)["code"]
    assert "with Locations((3.1379, -2.7183)):" in code
    assert "Circle(radius=4.7183)" in code


# --------------------------------------------------------------------------
# splines and slots — slice 6's handoff
# --------------------------------------------------------------------------
def spline_spec() -> dict:
    return {
        "points": [{"name": "s0", "x": 0.0, "y": 0.0, "fixed": True},
                   {"name": "s1", "x": 11.3137, "y": 9.4271},
                   {"name": "s2", "x": 24.1421, "y": 3.7183},
                   {"name": "s3", "x": 33.7183, "y": 14.1421, "fixed": True},
                   {"name": "t0", "x": 0.0, "y": -8.0, "fixed": True}],
        "splines": [{"name": "sp", "points": ["s0", "s1", "s2", "s3"]}],
        "lines": [{"name": "guide", "p1": "t0", "p2": "s0"}],
        "constraints": [
            {"type": "tangent", "a": "sp.start", "b": "guide"},
            {"type": "distance", "p": "s0", "q": "s1", "d": 14.7183},
            {"type": "distance_x", "p": "s1", "q": "s2", "d": 12.3141},
            {"type": "distance_y", "p": "s1", "q": "s2", "d": -5.7183},
        ],
    }


def test_a_pinned_spline_end_emits_tangents():
    """Measured in slice 6: without `tangents=` the emitted curve's end
    direction is up to 44.6 deg off what the constraint solved for."""
    spec = spline_spec()
    sol = solved(spec)
    assert sol["splines"]["sp"]["end_tangent"]["start"] is True
    res = emit(sol, spec)
    assert "Spline(" in res["code"] and "tangents=(" in res["code"]
    t = sol["splines"]["sp"]["tangents"]["start"]
    assert sketch_emit.fmt(t["x"]) in res["code"]
    assert sketch_emit.fmt(t["y"]) in res["code"]
    # only one end was pinned, so the other is pinned to its own control-polygon
    # leg and the caller is told
    assert any(w["code"] == "spline_free_end_pinned" for w in res["warnings"])


def test_an_unpinned_spline_emits_no_tangents():
    spec = spline_spec()
    spec["constraints"] = spec["constraints"][1:]
    res = emit(solved(spec), spec)
    assert "Spline(" in res["code"] and "tangents=" not in res["code"]
    assert res["warnings"] == []


def slot_only_spec() -> dict:
    return {
        "points": [{"name": "sa", "x": -6.1387, "y": -3.4271, "fixed": True},
                   {"name": "sb", "x": 26.5139, "y": -7.9163, "fixed": True}],
        "slots": [{"name": "sl", "c1": "sa", "c2": "sb", "width": SLOT_WIDTH}],
        "constraints": [],
    }


def test_a_standalone_slot_emits_slot_center_to_center():
    spec = slot_only_spec()
    sol = solved(spec)
    code = emit(sol, spec)["code"]
    assert "SlotCenterToCenter(" in code
    assert "RadiusArc(" not in code
    s = sol["slots"]["sl"]
    sep = math.hypot(s["center2"]["x"] - s["center1"]["x"],
                     s["center2"]["y"] - s["center1"]["y"])
    assert f"SlotCenterToCenter({sketch_emit.fmt(sep)}" in code


def test_a_slot_tied_to_the_rest_of_the_sketch_emits_its_primitives():
    """Design Decision 10's trap: `SlotCenterToCenter` is a BuildSketch **face**
    at the origin, not a curve that can join a `BuildLine` chain. A slot whose
    sub-entities carry constraints of their own emits as the primitives slice 6
    already compiled (`slots[n]["arcs"]` / `["sides"]`)."""
    spec = slot_only_spec()
    spec["points"].append({"name": "g", "x": 0.0, "y": 30.0, "fixed": True})
    spec["circles"] = [{"name": "hub", "center": "g", "r": 5.0}]
    spec["constraints"] = [{"type": "equal_radius", "c1": "hub", "c2": "sl.arc_a"}]
    sol = solved(spec)
    code = emit(sol, spec)["code"]
    assert "SlotCenterToCenter(" not in code
    assert code.count("RadiusArc(") == 2
    assert len(re.findall(r"\bLine\(", code)) == 2, code
    assert "make_face()" in code


# --------------------------------------------------------------------------
# the closure gate
# --------------------------------------------------------------------------
def welded_square_spec() -> dict:
    """A closed chain whose last junction is **two different points** meeting
    at the same coordinates — the shape a chain tied by `coincident` has, and
    the only shape in which a junction can have a gap at all (a junction on one
    shared handle cannot)."""
    return {
        "points": [{"name": "a", "x": 0.0, "y": 0.0, "fixed": True},
                   {"name": "b", "x": 30.7183, "y": 0.0, "fixed": True},
                   {"name": "c", "x": 30.7183, "y": 24.3319, "fixed": True},
                   {"name": "d", "x": 0.0, "y": 24.3319, "fixed": True},
                   {"name": "d2", "x": 0.0, "y": 24.3319, "fixed": True}],
        "lines": [{"name": "ab", "p1": "a", "p2": "b"},
                  {"name": "bc", "p1": "b", "p2": "c"},
                  {"name": "cd", "p1": "c", "p2": "d"},
                  {"name": "da", "p1": "d2", "p2": "a"}],
        "constraints": [],
    }


def test_the_closure_gate_names_the_junction_it_refuses():
    """A hand-built solution whose two chained endpoints sit 1e-6 mm apart:
    sharing one literal would silently move the geometry, and emitting two
    literals would not close. Refuse, and say where."""
    spec, sol = welded_square_spec(), solved(welded_square_spec())
    assert "make_face()" in emit(sol, spec)["code"]      # it closes as solved
    sol["points"]["d2"]["x"] += 1e-6
    with pytest.raises(EmitError) as exc:
        emit(sol, spec)
    assert "d/d2" in str(exc.value) or "d2/d" in str(exc.value)
    assert "1.000e-06 mm" in str(exc.value)
    assert "make_face" in str(exc.value)


def test_the_gate_is_the_reason_six_decimals_is_refused():
    """The old GUI rule (`fmtNum`, 6 decimals) is still reachable so the
    regression can be *proved* rather than remembered — and the gate refuses
    it on a non-round profile."""
    spec, sol = cam_spec(), solved(cam_spec())
    with pytest.raises(EmitError) as exc:
        emit(sol, spec, decimals=6, arc_anchor="center")
    assert "make_face" in str(exc.value)


def test_an_open_chain_gap_is_a_warning_not_an_error():
    """The gate guards `make_face()`; an open chain still says what it saw."""
    spec = welded_square_spec()
    spec["lines"] = spec["lines"][1:]            # drop the closing segment
    sol = solved(spec)
    sol["points"]["d2"]["x"] += 1e-6
    res = emit(sol, spec)
    assert "make_face()" not in res["code"]
    assert any(w["code"] == "junction_gap" for w in res["warnings"])


# --------------------------------------------------------------------------
# AC1 — one emitter, both layers, byte for byte
# --------------------------------------------------------------------------
def entities_of(spec: dict) -> dict:
    return {k: spec.get(k, []) for k in
            ("points", "lines", "circles", "arcs", "splines", "slots")}


def _tool_emit(spec: dict, **kwargs) -> dict:
    registry = ToolRegistry()
    tools_sketch.register(registry, None)
    return registry.get("solve_sketch").handler(
        entities=entities_of(spec), constraints=spec["constraints"], **kwargs)


def test_the_tool_returns_emitted_code():
    out = _tool_emit(slotted_cam_spec(), emit="function")
    assert out["emit"]["code"].startswith("\n")
    assert "def sketch_profile():" in out["emit"]["code"]
    assert out["emit"]["style"] == "function"


def test_the_buildline_style_is_a_bare_block():
    out = _tool_emit(square_spec(), emit="buildline")
    assert out["emit"]["code"].startswith("with BuildSketch(Plane.XY)")
    assert "def sketch_profile" not in out["emit"]["code"]


def test_emit_omitted_costs_nothing():
    assert "emit" not in _tool_emit(slotted_cam_spec())
    assert "emit" not in _tool_emit(slotted_cam_spec(), emit=None)


@pytest.mark.integration
def test_the_gui_and_the_agent_emit_identical_bytes(tmp_path):
    """**AC1's identity half.** The browser posts to `/api/sketch/solve`; an
    agent calls the `solve_sketch` tool; a part script could call the emitter
    directly. All three must be the same bytes, because there is exactly one
    emitter — the whole reason it moved off the front end."""
    spec = slotted_cam_spec()
    service = make_test_service(tmp_path / "projects", None)
    client = TestClient(create_app(service, build_registry(service)),
                        base_url="http://127.0.0.1")
    gui = client.post("/api/sketch/solve", json={
        "entities": entities_of(spec), "constraints": spec["constraints"],
        "emit": "function",
    }).json()
    agent = _tool_emit(spec, emit="function")
    direct = emit(solved(spec), spec)

    assert gui["emit"]["code"] == agent["emit"]["code"] == direct["code"]
    assert gui["emit"]["code"].encode() == direct["code"].encode()
    assert json.dumps(gui["emit"]["warnings"]) == json.dumps(direct["warnings"])
    # and `false` on the route is "do not emit", not a type error
    off = client.post("/api/sketch/solve", json={
        "entities": entities_of(spec), "constraints": spec["constraints"],
        "emit": False}).json()
    assert "emit" not in off and off["ok"] is True


# --------------------------------------------------------------------------
# AC1 — the golden test: the emitted code rebuilds to the solved geometry
# --------------------------------------------------------------------------
@pytest.mark.integration
def test_ac1_the_slotted_cam_rebuilds_to_the_solved_metrics(kernel, tmp_path):
    spec = slotted_cam_spec()
    sol = solved(spec)
    code = emit(sol, spec)["code"]

    metrics = build_metrics(kernel, tmp_path, code)
    cam_area, cam_bb = cam_area_and_bbox(sol)
    slot_area, slot_bb = slot_area_and_bbox(sol)
    # extruded 1 mm, so volume is the profile's area
    assert metrics["volume_mm3"] == pytest.approx(cam_area + slot_area, rel=1e-6)
    bb = metrics["bbox"]
    for got, want in ((bb["min"][0], min(cam_bb[0], slot_bb[0])),
                      (bb["min"][1], min(cam_bb[1], slot_bb[1])),
                      (bb["max"][0], max(cam_bb[2], slot_bb[2])),
                      (bb["max"][1], max(cam_bb[3], slot_bb[3]))):
        assert got == pytest.approx(want, rel=1e-6, abs=1e-6)


@pytest.mark.integration
def test_the_six_decimal_centre_parametrized_emission_does_not_close(kernel,
                                                                     tmp_path):
    """**The closure regression, on non-round coordinates.**

    Measured in the design spike: 6 decimals leaves a 7.58e-7 mm gap between a
    centre-parametrized arc's derived end and the next line's start, and
    `make_face()` refuses it. The *same profile with tidy numbers closes at 3
    decimals* — which is exactly how this bug reaches a user and not a
    reviewer. The gate would refuse to emit this, so the test asks for it
    explicitly (`closure_tol=inf`) and then proves the kernel agrees.
    """
    spec = cam_spec()
    sol = solved(spec)
    legacy = emit(sol, spec, decimals=6, arc_anchor="center",
                  closure_tol=math.inf)
    with pytest.raises(Exception) as exc:
        build_metrics(kernel, tmp_path, legacy["code"])
    assert "closed wires" in str(exc.value) or "closed" in str(exc.value)

    # and the emitter's own output rebuilds
    ok = emit(sol, spec)
    metrics = build_metrics(kernel, tmp_path, ok["code"])
    area, _ = cam_area_and_bbox(sol)
    assert metrics["volume_mm3"] == pytest.approx(area, rel=1e-6)


def test_the_gap_the_gate_measures_is_the_measured_one(capsys):
    """The number, not the story: print the gap the gate sees, both ways."""
    spec, sol = cam_spec(), solved(cam_spec())
    legacy = sketch_emit.junction_gaps(sol, spec, decimals=6,
                                       arc_anchor="center")
    tight = sketch_emit.junction_gaps(sol, spec)
    with capsys.disabled():
        print(f"\n  6 decimals, centre-parametrized: "
              f"{max(legacy.values()):.3e} mm  (make_face refuses)")
        print(f"  9 decimals, endpoint-anchored:   "
              f"{max(tight.values()):.3e} mm  (gate {sketch_emit.CLOSURE_TOL_MM:.0e})")
    assert max(legacy.values()) > 1e-7, legacy
    assert max(tight.values()) < sketch_emit.CLOSURE_TOL_MM, tight


def test_an_unknown_emit_style_is_rejected():
    with pytest.raises(EmitError):
        emit(solved(square_spec()), square_spec(), style="nope")


def test_the_tool_turns_an_emit_failure_into_a_validation_error():
    spec = square_spec()
    with pytest.raises(ValidationError) as exc:
        _tool_emit(spec, emit="nope")
    assert "emit style" in str(exc.value)
