"""Class-A surfacing toolkit (smooth_loft / blend_surface) + curvature analysis."""

import pytest

from agentcad.core.service import AgentCADService, EventBus
from agentcad.core.tools import build_registry
from agentcad.kernel.client import KernelError

CYL = '''\
from build123d import *
PARAMS = {"r": {"default": 10.0, "min": 1.0, "max": 50.0, "unit": "mm", "description": "radius"}}
def build(p):
    return Cylinder(radius=p.r, height=40)
'''

SPHERE = '''\
from build123d import *
PARAMS = {"r": {"default": 20.0, "min": 1.0, "max": 50.0, "unit": "mm", "description": "radius"}}
def build(p):
    return Sphere(radius=p.r)
'''

BOX = '''\
from build123d import *
PARAMS = {"a": {"default": 30.0, "min": 5.0, "max": 100.0, "unit": "mm", "description": "len"}}
def build(p):
    return Box(p.a, 20, 10)
'''


@pytest.fixture
def demo(kernel, tmp_path):
    service = AgentCADService(tmp_path / "projects", kernel, EventBus())
    service.create_project("demo")
    service.create_part("demo", "cyl", script=CYL)
    service.create_part("demo", "sphere", script=SPHERE)
    service.create_part("demo", "box", script=BOX)
    return service


# ---- toolkit: smooth_loft ----------------------------------------------------

def test_smooth_loft_between_offset_rounded_rects():
    from build123d import Pos, RectangleRounded

    from agentcad.toolkit import surfacing

    bottom = RectangleRounded(40, 30, 5)
    top = Pos(0, 0, 30) * RectangleRounded(24, 18, 4)
    part, warning = surfacing.smooth_loft([bottom, top])
    assert part.is_valid
    assert len(part.solids()) == 1
    # the loft lives between the prisms of the small and large profiles
    lo = min(bottom.area, top.area) * 30
    hi = max(bottom.area, top.area) * 30
    assert lo < part.volume < hi


def test_smooth_loft_identical_profiles_is_prism():
    from build123d import Pos, RectangleRounded

    from agentcad.toolkit import surfacing

    prof = RectangleRounded(30, 20, 4)
    part, warning = surfacing.smooth_loft([prof, Pos(0, 0, 25) * prof])
    assert part.is_valid and len(part.solids()) == 1
    assert part.volume == pytest.approx(prof.area * 25, rel=0.01)


def test_smooth_loft_needs_two_profiles():
    from build123d import RectangleRounded

    from agentcad.toolkit import surfacing

    with pytest.raises(ValueError, match="at least 2"):
        surfacing.smooth_loft([RectangleRounded(30, 20, 4)])


# ---- toolkit: blend_surface --------------------------------------------------

def _plate_faces():
    """Two coplanar 2mm plates, 20mm apart in Y; return their top faces."""
    from build123d import Axis, Box, Pos

    a = Box(20, 10, 2)
    b = Pos(0, 30, 0) * Box(20, 10, 2)
    return a.faces().sort_by(Axis.Z)[-1], b.faces().sort_by(Axis.Z)[-1]


def test_blend_surface_g1_between_plates():
    from agentcad.toolkit import surfacing

    face_a, face_b = _plate_faces()
    blend, warning = surfacing.blend_surface(face_a, face_b, continuity="G1")
    assert blend.is_valid
    assert blend.area > 0
    # tangent blend spanning a 20x20 gap between coplanar faces stays close
    # to the flat strip joining them
    assert blend.area == pytest.approx(400, rel=0.25)
    assert warning is None  # G1 filling succeeds on this geometry


def test_blend_surface_g2_coplanar_succeeds():
    from agentcad.toolkit import surfacing

    face_a, face_b = _plate_faces()
    blend, warning = surfacing.blend_surface(face_a, face_b, continuity="G2")
    assert blend.is_valid
    # coplanar supports: the curvature-constrained plate converges to the
    # flat strip — true G2, no degradation
    assert blend.area == pytest.approx(400, rel=0.05)
    assert warning is None


def test_blend_surface_g2_unstable_degrades_to_g1():
    from build123d import Axis, Box, Pos

    from agentcad.toolkit import surfacing

    # raising the second plate 10mm makes the curvature-constrained plate
    # balloon on OCCT 7.x — blend_surface must detect that and degrade
    a = Box(20, 10, 2)
    b = Pos(0, 30, 10) * Box(20, 10, 2)
    face_a = a.faces().sort_by(Axis.Z)[-1]
    face_b = b.faces().sort_by(Axis.Z)[-1]
    blend, warning = surfacing.blend_surface(face_a, face_b, continuity="G2")
    assert blend.is_valid and blend.area > 0
    assert blend.area < 1000  # the stable G1 surface (~460), not the balloon
    assert warning is not None and "degraded to G1" in warning


def test_blend_surface_g0_positional_only():
    from agentcad.toolkit import surfacing

    face_a, face_b = _plate_faces()
    blend, warning = surfacing.blend_surface(face_a, face_b, continuity="G0")
    assert blend.is_valid and blend.area > 0
    assert warning is None


def test_blend_surface_rejects_bad_continuity():
    from agentcad.toolkit import surfacing

    face_a, face_b = _plate_faces()
    with pytest.raises(ValueError, match="continuity"):
        surfacing.blend_surface(face_a, face_b, continuity="G7")


# ---- curvature analysis (registry) ------------------------------------------

def test_curvature_cylinder(demo):
    registry = build_registry(demo)
    result = registry.call("analyze_part", {
        "project": "demo", "part_id": "cyl", "kind": "curvature"})
    assert result["kind"] == "curvature"
    assert result["n_faces"] == 3
    assert result["sampled_points"] == 3 * 8 * 8  # default 8x8 grid per face
    # cylinder faces are planar or developable: gaussian K ~ 0 everywhere
    assert result["worst_gaussian_abs"] < 1e-6
    lateral = [f for f in result["faces"]
               if abs(f["mean_curvature"]["mean"]) > 0.01]
    assert len(lateral) == 1
    lat = lateral[0]
    # |H| = 1/(2r) = 0.05 for r=10 (sign is orientation-dependent)
    for key in ("min", "max", "mean"):
        assert abs(lat["mean_curvature"][key]) == pytest.approx(0.05, rel=0.02)
        assert abs(lat["gaussian"][key]) < 1e-6
    assert lat["area_mm2"] > 0


def test_curvature_sphere(demo):
    registry = build_registry(demo)
    result = registry.call("analyze_part", {
        "project": "demo", "part_id": "sphere", "kind": "curvature"})
    assert result["n_faces"] == 1
    face = result["faces"][0]
    # K = 1/r^2 = 0.0025 for r=20 (positive regardless of orientation)
    for key in ("min", "max", "mean"):
        assert face["gaussian"][key] == pytest.approx(0.0025, rel=0.02)
    assert result["worst_gaussian_abs"] == pytest.approx(0.0025, rel=0.02)
    assert abs(face["mean_curvature"]["mean"]) == pytest.approx(0.05, rel=0.02)


def test_curvature_box_all_flat(demo):
    registry = build_registry(demo)
    result = registry.call("analyze_part", {
        "project": "demo", "part_id": "box", "kind": "curvature"})
    assert result["n_faces"] == 6
    assert result["worst_gaussian_abs"] < 1e-9
    for f in result["faces"]:
        assert abs(f["mean_curvature"]["mean"]) < 1e-9
        assert abs(f["gaussian"]["mean"]) < 1e-9


def test_unknown_kind_still_errors(demo):
    registry = build_registry(demo)
    result = registry.call("analyze_part", {
        "project": "demo", "part_id": "box", "kind": "bogus"})
    assert result["error"]["type"] == "contract_error"
    assert "unknown analysis kind" in result["error"]["message"]


# ---- curvature: samples clamping + degenerate shapes (kernel direct) ---------
# `samples` is a kernel-level knob (not exposed by the analyze_part tool or the
# UI), so exercise it through the kernel protocol directly.

def test_curvature_samples_clamped(kernel):
    high = kernel.request("analyze", {
        "script": BOX, "params": {}, "kind": "curvature", "samples": 100})
    assert high["sampled_points"] == 6 * 16 * 16  # clamped to 16

    low = kernel.request("analyze", {
        "script": BOX, "params": {}, "kind": "curvature", "samples": 1})
    assert low["sampled_points"] == 6 * 4 * 4  # clamped to 4


def test_curvature_zero_faces_is_contract_error(kernel):
    empty = (
        "from build123d import *\n"
        "PARAMS = {}\n"
        "def build(p):\n"
        "    return Compound(children=[])\n"
    )
    with pytest.raises(KernelError) as exc_info:
        kernel.request("analyze", {
            "script": empty, "params": {}, "kind": "curvature"})
    assert exc_info.value.type == "contract_error"
    assert "faces" in exc_info.value.message
