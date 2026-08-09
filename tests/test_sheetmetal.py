"""Sheet-metal toolkit (fold/unfold/bend lines) + flat-pattern export tests."""

import math
import subprocess
import sys

import pytest

from agentcad.core.service import AgentCADService, EventBus
from agentcad.core.tools import build_registry

from .conftest import BOX_SCRIPT

# BA for the canonical test bend: 90 deg, R=3, t=2, K=0.44
BA_90 = (math.pi / 2) * (3 + 0.44 * 2)


def _bracket():
    from agentcad.toolkit.sheetmetal import SheetPart

    return (SheetPart(2.0, k_factor=0.44)
            .base(60, 40)
            .flange("front", 90, 30, inner_radius=3))


# ---- toolkit: folded solid ---------------------------------------------------

def test_fold_single_flange_90():
    part = _bracket().fold()
    assert part.is_valid
    assert len(part.solids()) == 1
    # base + bend sector + leaf (construction is exactly additive: the sector
    # starts at the base end-face, the leaf is tangent to the sector)
    expected = (60 * 40 * 2                          # base plate
                + (math.pi / 2) * 2 * (3 + 1) * 60   # sector: a*t*(R + t/2)*w
                + 30 * 2 * 60)                       # leaf: L*t*w
    assert part.volume == pytest.approx(expected, rel=0.01)


def test_two_flanges_fold_and_unfold():
    from agentcad.toolkit.sheetmetal import SheetPart

    sp = (SheetPart(2.0)
          .base(60, 40)
          .flange("left", 90, 20, inner_radius=3)
          .flange("right", 90, 20, inner_radius=3))
    folded = sp.fold()
    assert folded.is_valid
    assert len(folded.solids()) == 1
    sector = (math.pi / 2) * 2 * (3 + 1) * 40        # edge width = depth
    expected = 60 * 40 * 2 + 2 * (sector + 20 * 2 * 40)
    assert folded.volume == pytest.approx(expected, rel=0.01)

    flat = sp.unfold()
    bb = flat.bounding_box()
    assert bb.max.X - bb.min.X == pytest.approx(60 + 2 * (BA_90 + 20), abs=1e-6)
    assert bb.max.Y - bb.min.Y == pytest.approx(40, abs=1e-6)
    assert len(sp.bend_lines()) == 2


# ---- toolkit: flat pattern ---------------------------------------------------

def test_unfold_flat_pattern():
    flat = _bracket().unfold()
    assert flat.is_valid
    bb = flat.bounding_box()
    # extent along the flange direction = depth + BA + length, exactly
    assert bb.max.Y - bb.min.Y == pytest.approx(40 + BA_90 + 30, abs=1e-6)
    assert bb.max.X - bb.min.X == pytest.approx(60, abs=1e-6)
    assert bb.max.Z - bb.min.Z == pytest.approx(2, abs=1e-6)   # thickness kept
    flat_area = 60 * 40 + (BA_90 + 30) * 60
    assert flat.volume == pytest.approx(flat_area * 2, rel=1e-6)


def test_bend_lines_single_flange():
    lines = _bracket().bend_lines()
    assert len(lines) == 1
    bl = lines[0]
    assert bl["edge"] == "front"
    assert bl["angle_deg"] == 90
    assert bl["inner_radius"] == 3
    # front edge sits at y = -depth/2; the bend zone spans [edge, edge + BA]
    # outward, so the midline is at y = -depth/2 - BA/2, spanning the edge in X.
    mid_y = -20 - BA_90 / 2
    assert bl["a"] == pytest.approx((-30, mid_y), abs=1e-9)
    assert bl["b"] == pytest.approx((30, mid_y), abs=1e-9)


def test_flat_outline_polygon():
    pts = _bracket().flat_outline()
    # shoelace area == flat pattern area
    area = 0.0
    for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
        area += x0 * y1 - x1 * y0
    area = abs(area) / 2
    assert area == pytest.approx(60 * 40 + (BA_90 + 30) * 60, rel=1e-9)
    rounded = [(round(x, 6), round(y, 6)) for x, y in pts]
    assert (30.0, 20.0) in rounded                       # base corner kept
    assert (30.0, round(-20 - BA_90 - 30, 6)) in rounded  # tab corner


# ---- toolkit: validation -----------------------------------------------------

def test_validation_errors():
    from agentcad.toolkit.sheetmetal import SheetPart

    sp = SheetPart(2.0).base(60, 40).flange("front", 90, 30)
    with pytest.raises(ValueError):
        sp.flange("front", 45, 10)              # duplicate edge
    with pytest.raises(ValueError):
        sp.flange("back", 0, 10)                # angle 0 excluded
    with pytest.raises(ValueError):
        sp.flange("back", 180, 10)              # angle 180 excluded
    with pytest.raises(ValueError):
        sp.flange("top", 90, 10)                # unknown edge
    with pytest.raises(ValueError):
        SheetPart(2.0).flange("front", 90, 10)  # base() not called yet


def test_default_inner_radius_is_thickness():
    from agentcad.toolkit.sheetmetal import SheetPart

    sp = SheetPart(2.0).base(60, 40).flange("front", 90, 30)
    assert sp.bend_lines()[0]["inner_radius"] == 2.0


# ---- handler + tool: flat_pattern export ------------------------------------

BRACKET = '''\
from agentcad.toolkit.sheetmetal import SheetPart

PARAMS = {
    "width":      {"default": 60.0, "min": 10.0, "max": 500.0, "unit": "mm",
                   "description": "base plate width (X)"},
    "depth":      {"default": 40.0, "min": 10.0, "max": 500.0, "unit": "mm",
                   "description": "base plate depth (Y)"},
    "thick":      {"default": 2.0,  "min": 0.5,  "max": 6.0,   "unit": "mm",
                   "description": "sheet thickness"},
    "flange_len": {"default": 30.0, "min": 5.0,  "max": 200.0, "unit": "mm",
                   "description": "flange leaf length beyond the bend"},
    "bend_r":     {"default": 3.0,  "min": 0.5,  "max": 20.0,  "unit": "mm",
                   "description": "inner bend radius"},
}

def _sheet(p):
    return (SheetPart(p.thick)
            .base(p.width, p.depth)
            .flange("front", 90, p.flange_len, inner_radius=p.bend_r))

def build(p):
    return _sheet(p).fold()

def flat_pattern(p):
    sp = _sheet(p)
    return sp.unfold(), sp.bend_lines()
'''


@pytest.fixture
def demo(kernel, tmp_path):
    service = AgentCADService(tmp_path / "projects", kernel, EventBus())
    service.create_project("demo")
    service.create_part("demo", "bracket", script=BRACKET)
    return service


def test_flat_pattern_svg(demo):
    registry = build_registry(demo)
    result = registry.call("flat_pattern", {
        "project": "demo", "part_id": "bracket", "format": "svg"})
    assert "error" not in result, result
    assert result["n_bend_lines"] == 1
    assert result["size_bytes"] > 0
    assert result["flat_bbox_mm"]["w"] == pytest.approx(60, abs=0.1)
    assert result["flat_bbox_mm"]["h"] == pytest.approx(40 + BA_90 + 30, abs=0.1)
    svg = (demo.store.exports_dir("demo") / "bracket_flat.svg").read_text(encoding="utf-8")
    assert svg.startswith("<svg")
    assert 'id="BEND"' in svg and "stroke-dasharray" in svg
    assert "demo / bracket" in svg


def test_flat_pattern_dxf(demo):
    import ezdxf

    registry = build_registry(demo)
    result = registry.call("flat_pattern", {
        "project": "demo", "part_id": "bracket", "format": "dxf"})
    assert "error" not in result, result
    doc = ezdxf.readfile(str(demo.store.exports_dir("demo") / "bracket_flat.dxf"))
    entities = list(doc.modelspace())
    layers = {e.dxf.layer for e in entities}
    assert "OUTLINE" in layers and "BEND" in layers
    assert sum(1 for e in entities if e.dxf.layer == "BEND") == 1


def test_flat_pattern_rejects_reference_part(demo, kernel, tmp_path):
    stl = tmp_path / "blob.stl"
    kernel.request("export", {"script": BOX_SCRIPT, "params": {},
                              "format": "stl", "out_path": str(stl)})
    registry = build_registry(demo)
    registry.call("import_cad_file", {"project": "demo", "source": str(stl),
                                      "part_id": "blob"})
    result = registry.call("flat_pattern", {"project": "demo", "part_id": "blob"})
    assert result["error"]["type"] == "validation_error"


def test_flat_pattern_missing_contract(demo):
    demo.create_part("demo", "box", script=BOX_SCRIPT)
    registry = build_registry(demo)
    result = registry.call("flat_pattern", {"project": "demo", "part_id": "box"})
    assert result["error"]["type"] == "contract_error"
    assert "flat_pattern" in result["error"]["message"]


# ---- lazy-import discipline --------------------------------------------------

def test_toolkit_import_stays_lazy():
    # importing the package (as the server process does) must not pull build123d
    code = "import sys, agentcad.toolkit; assert 'build123d' not in sys.modules"
    subprocess.run([sys.executable, "-c", code], check=True)
