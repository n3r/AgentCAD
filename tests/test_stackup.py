"""Tolerance stack-up tests: worst-case / RSS accumulation along mate chains.

Assembly under test: three plates chained edge-to-edge along X via rigid
connectors (base spans [-20, 20], mid [20, 60], top [60, 100]), each part
carrying a linear "width" PMI dim of +/-0.1 mm.
"""

import math

import pytest

from agentcad.core.tools import build_registry

from .conftest import clone_test_service, make_test_service

# A plate with rigid connectors at its left/right edges so instances chain
# edge-to-edge along X (same PLATE-with-connectors pattern as test_mates.py).
PLATE = '''\
from build123d import *

PARAMS = {"w": {"default": 40.0, "min": 5.0, "max": 200.0}}

def build(p):
    with BuildPart() as part:
        Box(p.w, 40, 10)
    return part.part

def connectors(p, part):
    return {"left": {"type": "rigid", "location": ((-p.w / 2, 0, 0), (0, 0, 0))},
            "right": {"type": "rigid", "location": ((p.w / 2, 0, 0), (0, 0, 0))}}
'''

CHAIN_PARTS = ("plate_a", "plate_b", "plate_c")

WIDTH_PMI = {"dims": [{"id": "w1", "kind": "linear", "target": "width",
                       "plus": 0.1, "minus": 0.1}]}


@pytest.fixture(scope="module")
def stackup_projects(kernel, tmp_path_factory):
    projects = tmp_path_factory.mktemp("stackup_projects")
    service = make_test_service(projects, kernel)
    service.create_project("demo")
    for part_id in CHAIN_PARTS:
        service.create_part("demo", part_id, script=PLATE)
    registry = build_registry(service)
    service.set_assembly("demo", [
        {"id": "base", "part": "plate_a", "position": [0, 0, 0]},
        {"id": "mid", "part": "plate_b"},
        {"id": "top", "part": "plate_c"},
    ])
    for instance, anchor in (("mid", "base"), ("top", "mid")):
        result = registry.call("set_mate", {
            "project": "demo", "instance": instance, "connector": "left",
            "to_instance": anchor, "to_connector": "right"})
        assert "error" not in result, result
    for part_id in CHAIN_PARTS:
        result = registry.call("set_part_pmi", {
            "project": "demo", "part_id": part_id, "pmi": WIDTH_PMI})
        assert "error" not in result, result
    return projects


@pytest.fixture
def demo(kernel, tmp_path, stackup_projects):
    return clone_test_service(stackup_projects, tmp_path / "projects", kernel)


@pytest.fixture
def registry(demo):
    return build_registry(demo)


@pytest.fixture
def chain(registry):
    """Registry over a copied base -> mid -> top tolerance chain."""
    return registry


def test_stackup_worst_case_and_rss_along_x(demo, chain):
    result = chain.call("tolerance_stackup", {
        "project": "demo", "axis": "x",
        "from_instance": "base", "to_instance": "top"})
    assert "error" not in result, result
    assert result["axis"] == "x"
    assert result["target"] == "width"
    # 3 contributors (base, mid, top all on the path) x +/-0.1 each
    assert result["worst_case"]["plus"] == pytest.approx(0.3)
    assert result["worst_case"]["minus"] == pytest.approx(0.3)
    assert result["rss"]["plus"] == pytest.approx(math.sqrt(3 * 0.01))
    assert result["rss"]["minus"] == pytest.approx(math.sqrt(3 * 0.01))
    assert result["path"] == ["base", "mid", "top"]
    assert [c["instance"] for c in result["contributors"]] == ["base", "mid", "top"]
    mid = result["contributors"][1]
    assert mid["part"] == "plate_b"
    assert mid["dims"] == [{"id": "w1", "plus": 0.1, "minus": 0.1}]
    assert mid["plus"] == pytest.approx(0.1)
    assert mid["minus"] == pytest.approx(0.1)
    assert result["warnings"] == []
    # nominal = |x_top - x_base| from the resolved (mate-driven) assembly
    assembly = chain.call("get_assembly", {"project": "demo"})
    pos = {i["id"]: i["position"] for i in assembly["instances"]}
    expected = abs(pos["top"][0] - pos["base"][0])
    assert expected == pytest.approx(80.0)  # non-degenerate edge-to-edge chain
    assert result["nominal_mm"] == pytest.approx(expected)


def test_stackup_axis_without_dims_warns(demo, chain):
    # no "height" dims declared anywhere -> zero totals, one warning per instance
    result = chain.call("tolerance_stackup", {
        "project": "demo", "axis": "z",
        "from_instance": "base", "to_instance": "top"})
    assert "error" not in result, result
    assert result["target"] == "height"
    assert result["worst_case"] == {"plus": 0.0, "minus": 0.0}
    assert result["rss"] == {"plus": 0.0, "minus": 0.0}
    assert len(result["warnings"]) == 3
    assert "instance base (part plate_a) has no height tolerance" in result["warnings"]
    assert "instance mid (part plate_b) has no height tolerance" in result["warnings"]
    assert "instance top (part plate_c) has no height tolerance" in result["warnings"]
    assert all(c["dims"] == [] for c in result["contributors"])


def test_stackup_direction_symmetric(demo, chain):
    fwd = chain.call("tolerance_stackup", {
        "project": "demo", "axis": "x",
        "from_instance": "base", "to_instance": "top"})
    rev = chain.call("tolerance_stackup", {
        "project": "demo", "axis": "x",
        "from_instance": "top", "to_instance": "base"})
    assert "error" not in fwd and "error" not in rev
    assert rev["worst_case"] == fwd["worst_case"]
    assert rev["rss"] == fwd["rss"]
    assert rev["nominal_mm"] == pytest.approx(fwd["nominal_mm"])
    assert rev["path"] == list(reversed(fwd["path"]))


def test_stackup_across_branches(demo, chain):
    # side hangs off base's left edge: path side -> base -> mid -> top crosses
    # the common ancestor (base) rather than one endpoint being the other's root
    demo.create_part("demo", "plate_e", script=PLATE)
    demo.set_assembly("demo", [
        {"id": "base", "part": "plate_a", "position": [0, 0, 0]},
        {"id": "mid", "part": "plate_b",
         "mate": {"connector": "left", "to_instance": "base", "to_connector": "right"}},
        {"id": "top", "part": "plate_c",
         "mate": {"connector": "left", "to_instance": "mid", "to_connector": "right"}},
        {"id": "side", "part": "plate_e",
         "mate": {"connector": "right", "to_instance": "base", "to_connector": "left"}},
    ])
    result = chain.call("set_part_pmi", {
        "project": "demo", "part_id": "plate_e",
        "pmi": {"dims": [{"id": "w1", "kind": "linear", "target": "width",
                          "plus": 0.2, "minus": 0.2}]}})
    assert "error" not in result, result
    result = chain.call("tolerance_stackup", {
        "project": "demo", "axis": "x",
        "from_instance": "side", "to_instance": "top"})
    assert "error" not in result, result
    assert result["path"] == ["side", "base", "mid", "top"]
    assert result["worst_case"]["plus"] == pytest.approx(0.5)  # 0.2 + 3 x 0.1
    assert result["rss"]["plus"] == pytest.approx(math.sqrt(0.04 + 3 * 0.01))
    assert result["nominal_mm"] == pytest.approx(120.0)  # side at -40, top at 80


def test_stackup_unconnected_instance_rejected(demo, chain):
    demo.create_part("demo", "plate_d", script=PLATE)
    demo.set_assembly("demo", [
        {"id": "base", "part": "plate_a", "position": [0, 0, 0]},
        {"id": "mid", "part": "plate_b",
         "mate": {"connector": "left", "to_instance": "base", "to_connector": "right"}},
        {"id": "top", "part": "plate_c",
         "mate": {"connector": "left", "to_instance": "mid", "to_connector": "right"}},
        {"id": "loose", "part": "plate_d", "position": [500, 0, 0]},
    ])
    result = chain.call("tolerance_stackup", {
        "project": "demo", "axis": "x",
        "from_instance": "base", "to_instance": "loose"})
    assert result["error"]["type"] == "validation_error"
    assert "not connected" in result["error"]["message"]


def test_stackup_unknown_instance_not_found(demo, chain):
    result = chain.call("tolerance_stackup", {
        "project": "demo", "axis": "x",
        "from_instance": "base", "to_instance": "ghost"})
    assert result["error"]["type"] == "notfound_error"


def test_stackup_bad_axis_rejected(demo, chain):
    result = chain.call("tolerance_stackup", {
        "project": "demo", "axis": "w",
        "from_instance": "base", "to_instance": "top"})
    assert result["error"]["type"] == "validation_error"


def test_stackup_self_path_counts_own_dims_once(demo, chain):
    # from == to: the path is that single instance; its own dims are the stack
    result = chain.call("tolerance_stackup", {
        "project": "demo", "axis": "x",
        "from_instance": "mid", "to_instance": "mid"})
    assert "error" not in result, result
    assert result["path"] == ["mid"]
    assert [c["instance"] for c in result["contributors"]] == ["mid"]
    assert result["nominal_mm"] == pytest.approx(0.0)
    assert result["worst_case"]["plus"] == pytest.approx(0.1)
    assert result["worst_case"]["minus"] == pytest.approx(0.1)
    assert result["rss"]["plus"] == pytest.approx(0.1)
