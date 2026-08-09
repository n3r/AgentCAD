"""Tier-1 analysis tests (shipped deps). FEM tests skip without agentcad[fem]."""

import math

import pytest

from agentcad.core.tools import build_registry

from .conftest import clone_test_service, make_test_service

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

# Slender beam for the modal/thermal analytic references: 100 x 10 x 10 mm,
# al6061 (the default material: E = 68.9 GPa, rho = 2.70 g/cm^3, k = 167).
BEAM = '''\
from build123d import *
PARAMS = {"L": {"default": 100.0, "min": 10.0, "max": 500.0, "unit": "mm", "description": "len"}}
def build(p):
    return Box(p.L, 10, 10)
'''

FEM_TOOLS = {"fem_static", "fem_modal", "fem_thermal"}


def _require_fem():
    pytest.importorskip("skfem")
    pytest.importorskip("gmsh")
    pytest.importorskip("meshio")


def _populate_demo(service):
    service.create_project("demo")
    service.create_part("demo", "shell", script=SHELLED_BOX)
    service.create_part("demo", "box", script=BOX)
    return service


@pytest.fixture(scope="module")
def demo_projects(kernel, tmp_path_factory):
    projects = tmp_path_factory.mktemp("analysis_projects")
    _populate_demo(make_test_service(projects, kernel))
    return projects


@pytest.fixture
def demo(kernel, tmp_path, demo_projects):
    return clone_test_service(demo_projects, tmp_path / "projects", kernel)


@pytest.fixture(scope="module")
def fem_projects(kernel, tmp_path_factory):
    _require_fem()
    projects = tmp_path_factory.mktemp("fem_projects")
    _populate_demo(make_test_service(projects, kernel))
    return projects


@pytest.fixture
def fem_demo(kernel, tmp_path, fem_projects):
    return clone_test_service(fem_projects, tmp_path / "projects", kernel)


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


def test_fem_static_if_available(fem_demo):
    registry = build_registry(fem_demo)
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


# ---------------------------------------------------------------------------
# FEM tier 2: modal + thermal (skip without agentcad[fem], like fem_static)
# ---------------------------------------------------------------------------


def test_fem_tools_gated_by_extra(demo):
    # All FEM tools register together behind the same fem_available() gate, so
    # agents never see a tool that cannot run.
    from agentcad.kernel.handlers.fem import fem_available

    registry = build_registry(demo)
    names = {t.name for t in registry.list()}
    if fem_available():
        assert FEM_TOOLS <= names
    else:
        assert not (FEM_TOOLS & names)


def test_fem_routes_501_without_extra(kernel, tmp_path):
    from agentcad.kernel.handlers.fem import fem_available

    if fem_available():
        pytest.skip("agentcad[fem] installed; the 501 fallback is unreachable")
    from fastapi.testclient import TestClient

    from agentcad.server.app import create_app

    service = make_test_service(tmp_path / "projects", kernel)
    app = create_app(
        service, build_registry(service), extra_allowed_hosts={"testserver"}
    )
    client = TestClient(app, base_url="http://127.0.0.1")
    for suffix in ("fem", "fem/modal", "fem/thermal"):
        response = client.post(f"/api/projects/demo/parts/box/{suffix}", json={})
        assert response.status_code == 501, suffix
        assert response.json()["error"]["type"] == "FEMUnavailable"


def test_fem_modal_cantilever_vs_euler_bernoulli(fem_demo):
    fem_demo.create_part("demo", "beam", script=BEAM)  # al6061
    registry = build_registry(fem_demo)
    result = registry.call("fem_modal", {
        "project": "demo", "part_id": "beam", "n_modes": 4,
        "fixed_face": {"axis": "x", "side": "min"},
    })
    assert "error" not in result, result
    freqs = result["frequencies_hz"]
    assert result["constrained"] is True
    assert result["n_modes"] == len(freqs) == 4
    assert freqs == sorted(freqs)
    assert result["n_dof"] > 0 and result["mesh"]["n_tets"] > 0

    # Euler-Bernoulli first bending mode of a clamped-free beam (SI units),
    # al6061: E = 68.9 GPa, rho = 2700 kg/m^3; 100 x 10 x 10 mm section.
    E, rho = 68.9e9, 2700.0
    L, b, h = 0.1, 0.01, 0.01
    I, A = b * h**3 / 12, b * h
    f1 = (1.875104**2 / (2 * math.pi)) * math.sqrt(E * I / (rho * A * L**4))
    # Beam theory ignores shear deformation/rotary inertia (~2% high at
    # L/h = 10) and the FEM adds discretization error; 8% brackets both.
    assert freqs[0] == pytest.approx(f1, rel=0.08)
    # Square section: the first bending mode is doubly degenerate (y/z).
    assert freqs[1] == pytest.approx(freqs[0], rel=0.02)


def test_fem_modal_free_free_omits_rigid_modes(fem_demo):
    fem_demo.create_part("demo", "beam", script=BEAM)
    registry = build_registry(fem_demo)
    result = registry.call("fem_modal", {
        "project": "demo", "part_id": "beam", "n_modes": 6,
    })
    assert "error" not in result, result
    assert result["constrained"] is False
    assert "rigid-body" in result["note"]
    freqs = result["frequencies_hz"]
    assert 0 < len(freqs) <= 6
    # First free-free bending mode of this beam is ~5.2 kHz; anything near
    # zero would be a rigid-body mode that leaked through.
    assert min(freqs) > 100.0
    assert freqs == sorted(freqs)


def test_fem_thermal_bar_vs_analytic(fem_demo):
    fem_demo.create_part("demo", "beam", script=BEAM)  # al6061: k = 167 W/(m*K)
    registry = build_registry(fem_demo)
    result = registry.call("fem_thermal", {
        "project": "demo", "part_id": "beam",
        "hot_face": {"axis": "x", "side": "min"},
        "cold_face": {"axis": "x", "side": "max"},
        "t_hot_c": 100.0, "t_cold_c": 0.0,
    })
    assert "error" not in result, result
    # Dirichlet BCs are imposed exactly; the linear field is in P2's span.
    assert result["t_max_c"] == pytest.approx(100.0, abs=1e-3)
    assert result["t_min_c"] == pytest.approx(0.0, abs=1e-3)
    # flux = k * A * dT / L = 167 W/(m*K) * 1e-4 m^2 * 100 K / 0.1 m = 16.7 W
    assert result["flux_w"] == pytest.approx(167.0 * 1e-4 * 100.0 / 0.1, rel=0.02)


def test_fem_thermal_requires_conductivity(fem_demo):
    registry = build_registry(fem_demo)
    registry.call("set_project_materials", {
        "project": "demo", "materials": {"mystery": {"density_g_cm3": 1.0}}})
    fem_demo.create_part("demo", "blob", script=BOX, material="mystery")
    result = registry.call("fem_thermal", {
        "project": "demo", "part_id": "blob",
        "hot_face": {"axis": "x", "side": "min"},
        "cold_face": {"axis": "x", "side": "max"},
        "t_hot_c": 60.0, "t_cold_c": 20.0,
    })
    assert result["error"]["type"] == "validation_error"
    assert "thermal conductivity" in result["error"]["message"]
