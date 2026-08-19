"""PRD-013 Assembly v2 — slice 3: slider + planar joints, the DOF object, and
out-of-range CLAMPING (the divergence: clamp+warn, never raise).

The three existing mate types (rigid/revolute/cylindrical) and existing part
scripts are untouched — proven by the green test_mates/test_motion suites and
the in-range revolute test below.
"""

import pytest

from agentcad.core.tools import build_registry

from .conftest import make_test_service

RAIL = '''\
from build123d import *

PARAMS = {"L": {"default": 60.0, "min": 10.0, "max": 200.0}}

def build(p):
    with BuildPart() as part:
        Box(p.L, 10, 10)
    return part.part

def connectors(p, part):
    return {"track": {"type": "slider", "axis": ((0, 0, 0), (1, 0, 0)),
                      "linear_range": (0, 50)}}
'''

CARRIAGE = '''\
from build123d import *

PARAMS = {"s": {"default": 8.0, "min": 1.0, "max": 40.0}}

def build(p):
    with BuildPart() as part:
        Box(p.s, p.s, p.s)
    return part.part

def connectors(p, part):
    return {"foot": {"type": "rigid", "location": ((0, 0, 0), (0, 0, 0))}}
'''

WALL = '''\
from build123d import *

PARAMS = {"t": {"default": 4.0, "min": 1.0, "max": 40.0}}

def build(p):
    with BuildPart() as part:
        Box(200, 200, p.t)
    return part.part

def connectors(p, part):
    return {"face": {"type": "planar", "location": ((0, 0, 0), (0, 0, 0)),
                     "u_range": (-100, 100), "v_range": (-100, 100)}}
'''

TILE = '''\
from build123d import *

PARAMS = {"s": {"default": 10.0, "min": 1.0, "max": 40.0}}

def build(p):
    with BuildPart() as part:
        Box(p.s, p.s, p.s)
    return part.part

def connectors(p, part):
    return {"back": {"type": "rigid", "location": ((0, 0, 0), (0, 0, 0))}}
'''

# A plate + pin for the untouched-revolute regression.
PLATE = '''\
from build123d import *

PARAMS = {"t": {"default": 10.0, "min": 1.0, "max": 50.0}}

def build(p):
    with BuildPart() as part:
        Box(40, 40, p.t)
    return part.part

def connectors(p, part):
    return {"hinge": {"type": "revolute", "axis": ((0, 0, p.t / 2), (1, 0, 0)),
                      "range": (0, 90)}}
'''

PIN = '''\
from build123d import *

PARAMS = {"h": {"default": 15.0, "min": 1.0, "max": 50.0}}

def build(p):
    with BuildPart() as part:
        Cylinder(radius=3, height=p.h)
    return part.part

def connectors(p, part):
    return {"base": {"type": "rigid", "location": ((0, 0, 0), (0, 0, 0))}}
'''


@pytest.fixture
def svc(kernel, tmp_path):
    service = make_test_service(tmp_path / "projects", kernel)
    build_registry(service)
    return service


def _stored_params(service, proj, iid):
    inst = next(i for i in service.store.instances(proj) if i.id == iid)
    return (inst.mate or {}).get("params", {})


def _pos(a, iid):
    return next(i for i in a["instances"] if i["id"] == iid)["position"]


# ------------------------------------------------------------- slider

def test_slider_clamps_out_of_range_with_warning(svc):
    svc.create_project("s")
    svc.create_part("s", "rail", script=RAIL)
    svc.create_part("s", "carriage", script=CARRIAGE)
    svc.set_assembly("s", [
        {"id": "rail", "part": "rail", "position": [0, 0, 0]},
        {"id": "carriage", "part": "carriage", "position": [0, 0, 0]},
    ])
    registry = build_registry(svc)
    registry.call("set_mate", {
        "project": "s", "instance": "carriage", "connector": "foot",
        "to_instance": "rail", "to_connector": "track",
        "dof": {"offset_mm": 80}})           # range (0, 50)
    a = svc.get_assembly("s")
    assert _pos(a, "carriage")[0] == pytest.approx(50, abs=1e-6)
    clamps = [w for w in a["warnings"] if w["kind"] == "dof_clamped"]
    assert clamps and clamps[0]["requested"] == 80 and clamps[0]["clamped"] == 50
    assert clamps[0]["instance"] == "carriage" and clamps[0]["dof"] == "position"


def test_slider_in_range_no_warning(svc):
    svc.create_project("s2")
    svc.create_part("s2", "rail", script=RAIL)
    svc.create_part("s2", "carriage", script=CARRIAGE)
    svc.set_assembly("s2", [
        {"id": "rail", "part": "rail"},
        {"id": "carriage", "part": "carriage"},
    ])
    registry = build_registry(svc)
    registry.call("set_mate", {
        "project": "s2", "instance": "carriage", "connector": "foot",
        "to_instance": "rail", "to_connector": "track",
        "dof": {"offset_mm": 20}})
    a = svc.get_assembly("s2")
    assert _pos(a, "carriage")[0] == pytest.approx(20, abs=1e-6)
    assert [w for w in a["warnings"] if w["kind"] == "dof_clamped"] == []


# ------------------------------------------------------------- planar

def test_planar_uv_places_child(svc):
    svc.create_project("p")
    svc.create_part("p", "wall", script=WALL)
    svc.create_part("p", "tile", script=TILE)
    svc.set_assembly("p", [
        {"id": "wall", "part": "wall", "position": [0, 0, 0]},
        {"id": "tile", "part": "tile", "position": [0, 0, 0]},
    ])
    registry = build_registry(svc)
    registry.call("set_mate", {
        "project": "p", "instance": "tile", "connector": "back",
        "to_instance": "wall", "to_connector": "face",
        "dof": {"u_mm": 10, "v_mm": 5, "spin_deg": 0}})
    a = svc.get_assembly("p")
    x, y, _z = _pos(a, "tile")
    assert (x, y) == pytest.approx((10, 5), abs=1e-6)


def test_planar_clamps_uv_with_warning(svc):
    svc.create_project("p2")
    svc.create_part("p2", "wall", script=WALL)
    svc.create_part("p2", "tile", script=TILE)
    svc.set_assembly("p2", [
        {"id": "wall", "part": "wall"},
        {"id": "tile", "part": "tile"},
    ])
    registry = build_registry(svc)
    registry.call("set_mate", {
        "project": "p2", "instance": "tile", "connector": "back",
        "to_instance": "wall", "to_connector": "face",
        "dof": {"u_mm": 500, "v_mm": 0, "spin_deg": 0}})   # u_range (-100,100)
    a = svc.get_assembly("p2")
    assert _pos(a, "tile")[0] == pytest.approx(100, abs=1e-6)
    dofs = {w["dof"] for w in a["warnings"] if w["kind"] == "dof_clamped"}
    assert "u" in dofs


# --------------------------------------------------- set_mate dof mapping

def test_set_mate_dof_and_shorthand_map_to_params(svc):
    svc.create_project("m")
    svc.create_part("m", "plate", script=PLATE)
    svc.create_part("m", "pin", script=PIN)
    svc.set_assembly("m", [
        {"id": "plate", "part": "plate"},
        {"id": "pin", "part": "pin"},
    ])
    registry = build_registry(svc)
    # shorthand angle_deg -> params.angle (unchanged)
    registry.call("set_mate", {
        "project": "m", "instance": "pin", "connector": "base",
        "to_instance": "plate", "to_connector": "hinge", "angle_deg": 30})
    assert _stored_params(svc, "m", "pin") == {"angle": 30}


def test_set_mate_dof_offset_maps_to_position(svc):
    svc.create_project("m2")
    svc.create_part("m2", "rail", script=RAIL)
    svc.create_part("m2", "carriage", script=CARRIAGE)
    svc.set_assembly("m2", [
        {"id": "rail", "part": "rail"},
        {"id": "carriage", "part": "carriage"},
    ])
    registry = build_registry(svc)
    registry.call("set_mate", {
        "project": "m2", "instance": "carriage", "connector": "foot",
        "to_instance": "rail", "to_connector": "track",
        "dof": {"offset_mm": 10}})
    assert _stored_params(svc, "m2", "carriage") == {"position": 10}


# --------------------------------------------- existing types untouched

def test_revolute_in_range_still_resolves_without_warning(svc):
    svc.create_project("r")
    svc.create_part("r", "plate", script=PLATE)
    svc.create_part("r", "pin", script=PIN)
    svc.set_assembly("r", [
        {"id": "plate", "part": "plate"},
        {"id": "pin", "part": "pin"},
    ])
    registry = build_registry(svc)
    registry.call("set_mate", {
        "project": "r", "instance": "pin", "connector": "base",
        "to_instance": "plate", "to_connector": "hinge", "angle_deg": 45})
    a = svc.get_assembly("r")
    assert [w for w in a["warnings"] if w["kind"] == "dof_clamped"] == []


def test_slider_pattern_sweeps_all_members(svc):
    """sweep_motion reads the EXPANDED assembly: a static pattern beside the
    driven instance contributes its N members to every sample's frames."""
    svc.create_project("sw")
    svc.create_part("sw", "rail", script=RAIL)
    svc.create_part("sw", "carriage", script=CARRIAGE)
    svc.set_assembly("sw", [
        {"id": "rail", "part": "rail", "position": [0, 0, 0]},
        {"id": "carriage", "part": "carriage", "mate": {
            "connector": "foot", "to_instance": "rail", "to_connector": "track",
            "params": {"position": 0}}},
        {"id": "post", "part": "carriage", "position": [0, 40, 0],
         "pattern": {"kind": "linear", "count": 3, "step_mm": 12}},
    ])
    registry = build_registry(svc)
    out = registry.call("sweep_motion", {
        "project": "sw", "instance": "carriage",
        "offset_range": [0, 40], "samples": 3})
    # every frame carries the 3 expanded pattern members + rail + carriage
    frame_ids = set(out["frames"][0])
    assert {"post[0]", "post[1]", "post[2]"} <= frame_ids
