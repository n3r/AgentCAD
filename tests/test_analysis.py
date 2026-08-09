"""Tier-1 analysis tests (shipped deps). FEM tests skip without agentcad[fem]."""

import math

import pytest

from agentcad.core.service import AgentCADService, EventBus
from agentcad.core.tools import build_registry

SHELLED_BOX = '''\
from build123d import *

PARAMS = {"wall": {"default": 2.5, "min": 1.0, "max": 5.0, "unit": "mm", "description": "wall"}}

def build(p):
    box = Box(50, 40, 30)
    shelled = offset(box, amount=-p.wall, openings=box.faces().sort_by(Axis.Z)[-1])
    return shelled.solids()[0]
'''

BOX = '''\
from build123d import *
PARAMS = {"a": {"default": 40.0, "min": 10.0, "max": 100.0, "unit": "mm", "description": "len"}}
def build(p):
    return Box(p.a, 30, 20)
'''


@pytest.fixture
def demo(kernel, tmp_path):
    service = AgentCADService(tmp_path / "projects", kernel, EventBus())
    service.create_project("demo")
    service.create_part("demo", "shell", script=SHELLED_BOX)
    service.create_part("demo", "box", script=BOX)
    return service


def test_wall_thickness_probe(demo):
    registry = build_registry(demo)
    result = registry.call("analyze_part", {
        "project": "demo", "part_id": "shell", "kind": "wall", "min_required": 2.0})
    assert result["min_thickness_mm"] == pytest.approx(2.5, abs=0.15)
    assert result["ok"] is True


def test_inertia_tensor_vs_analytic(demo):
    registry = build_registry(demo)
    result = registry.call("analyze_part", {
        "project": "demo", "part_id": "box", "kind": "inertia"})
    # box 40x30x20, unit-ish; check volume and tensor diagonal ratios
    assert result["volume_mm3"] == pytest.approx(40 * 30 * 20, rel=1e-6)
    t = result["inertia_tensor_g_mm2"]
    # off-diagonals ~0 for an origin-centered box
    assert abs(t[0][1]) < 1e-3 * abs(t[0][0])


def test_section_area(demo):
    registry = build_registry(demo)
    result = registry.call("analyze_part", {
        "project": "demo", "part_id": "box", "kind": "section", "plane": "XY"})
    assert result["area_mm2"] == pytest.approx(40 * 30, rel=1e-3)


def test_projected_area(demo):
    registry = build_registry(demo)
    result = registry.call("analyze_part", {
        "project": "demo", "part_id": "box", "kind": "projected_area", "axis": "Z"})
    assert result["area_mm2"] == pytest.approx(40 * 30, rel=0.02)


def test_fem_static_if_available(demo):
    pytest.importorskip("skfem")
    pytest.importorskip("gmsh")
    pytest.importorskip("meshio")
    registry = build_registry(demo)
    # cantilever: fix x-min, load x-max downward
    result = registry.call("fem_static", {
        "project": "demo", "part_id": "box",
        "fixed_face": {"axis": "x", "side": "min"},
        "load_face": {"axis": "x", "side": "max"},
        "load_N": 100.0, "load_dir": [0, 0, -1],
    })
    assert result["max_disp_mm"] > 0
    assert result["max_von_mises_mpa"] > 0


def test_wall_probe_covers_all_solids(demo):
    # two disjoint solids: a chunky block and a thin 0.4mm plate; min wall must
    # find the thin one, not just the first solid.
    thin = '''\
from build123d import *
PARAMS = {"t": {"default": 0.4, "min": 0.2, "max": 2.0, "unit": "mm", "description": "thin"}}
def build(p):
    block = Box(20, 20, 20)
    plate = Pos(40, 0, 0) * Box(20, 20, p.t)
    return Compound(children=[block, plate])
'''
    demo.create_part("demo", "twosolid", script=thin)
    registry = build_registry(demo)
    result = registry.call("analyze_part", {
        "project": "demo", "part_id": "twosolid", "kind": "wall", "min_required": 1.0})
    assert result["min_thickness_mm"] == pytest.approx(0.4, abs=0.1)
    assert result["ok"] is False  # 0.4 < 1.0 required
