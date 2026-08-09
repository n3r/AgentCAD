"""Drawing generation tests (SVG structure + DXF round-trip)."""

import pytest

from agentcad.core.service import AgentCADService, EventBus
from agentcad.core.tools import build_registry

# A flange-like part: plate with a central bore and a bolt circle.
FLANGE = '''\
from build123d import *

PARAMS = {
    "outer_d":  {"default": 140.0, "min": 40.0, "max": 400.0, "unit": "mm", "description": "OD"},
    "bore_d":   {"default": 80.0,  "min": 10.0, "max": 300.0, "unit": "mm", "description": "bore"},
    "thick":    {"default": 14.0,  "min": 4.0,  "max": 60.0,  "unit": "mm", "description": "thickness"},
    "n_bolts":  {"default": 8.0,   "min": 3.0,  "max": 16.0,  "unit": "ct", "description": "bolt count"},
    "bolt_d":   {"default": 9.0,   "min": 3.0,  "max": 30.0,  "unit": "mm", "description": "bolt hole dia"},
    "bc_d":     {"default": 118.0, "min": 20.0, "max": 360.0, "unit": "mm", "description": "bolt circle dia"},
}

def build(p):
    with BuildPart() as part:
        Cylinder(radius=p.outer_d / 2, height=p.thick)
        Cylinder(radius=p.bore_d / 2, height=p.thick, mode=Mode.SUBTRACT)
        with PolarLocations(radius=p.bc_d / 2, count=int(p.n_bolts)):
            Hole(radius=p.bolt_d / 2)
    return part.part
'''


@pytest.fixture
def demo(kernel, tmp_path):
    service = AgentCADService(tmp_path / "projects", kernel, EventBus())
    service.create_project("demo")
    service.create_part("demo", "flange", script=FLANGE)
    return service


def test_generate_svg_drawing(demo):
    registry = build_registry(demo)
    result = registry.call("generate_drawing", {
        "project": "demo", "part_id": "flange", "format": "svg"})
    assert "error" not in result, result
    assert result["size_bytes"] > 1000
    svg = (demo.store.exports_dir("demo") / "flange_drawing.svg").read_text(encoding="utf-8")
    assert svg.startswith("<svg")
    # four view labels present
    for label in ("TOP", "FRONT", "RIGHT", "ISO"):
        assert label in svg
    # detected the bolt hole group (8 x bolt_d) and the OD/bore circles
    detected = result["detected"]
    assert any(g["count"] == 8 for g in detected["hole_groups"])
    assert 140.0 in detected["diameters_mm"] or 139.99 in detected["diameters_mm"]


def test_generate_dxf_roundtrips(demo, tmp_path):
    import ezdxf

    registry = build_registry(demo)
    result = registry.call("generate_drawing", {
        "project": "demo", "part_id": "flange", "format": "dxf"})
    assert "error" not in result, result
    path = demo.store.exports_dir("demo") / "flange_drawing.dxf"
    doc = ezdxf.readfile(str(path))
    entities = list(doc.modelspace())
    assert len(entities) > 5  # OD, bore, 8 bolt holes at minimum
    assert any(e.dxftype() == "CIRCLE" for e in entities)


def test_views_subset(demo):
    registry = build_registry(demo)
    result = registry.call("generate_drawing", {
        "project": "demo", "part_id": "flange", "views": ["top", "front"]})
    svg = (demo.store.exports_dir("demo") / "flange_drawing.svg").read_text(encoding="utf-8")
    assert "TOP" in svg and "FRONT" in svg
    assert "RIGHT" not in svg
