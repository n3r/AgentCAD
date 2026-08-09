"""PMI / GD&T tests: validation, manifest persistence, and drawing callouts."""

import json

import pytest

from agentcad.core.model import ValidationError
from agentcad.core.pmi import validate_pmi
from agentcad.core.service import AgentCADService, EventBus
from agentcad.core.tools import build_registry

# A flange-like part: plate with a central bore and a bolt circle (the same
# known-good pattern as tests/test_drawings.py — 8 x 9 mm bolt holes).
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

FULL_PMI = {
    "dims": [
        {"id": "d1", "kind": "linear", "target": "width", "plus": 0.1, "minus": 0.1},
        {"id": "d2", "kind": "diameter", "target": 9, "plus": 0.05, "minus": 0.1,
         "note": "bolt holes"},
    ],
    "datums": [{"id": "A", "face": "bottom"}],
    "fcf": [
        {"id": "f1", "type": "flatness", "tol_mm": 0.05, "note": "mounting face"},
        {"id": "f2", "type": "position", "tol_mm": 0.2, "datums": ["A"]},
    ],
}


@pytest.fixture
def demo(kernel, tmp_path):
    service = AgentCADService(tmp_path / "projects", kernel, EventBus())
    service.create_project("demo")
    service.create_part("demo", "flange", script=FLANGE)
    return service


@pytest.fixture
def registry(demo):
    return build_registry(demo)


# ------------------------------------------------------------- validate_pmi

def test_validate_pmi_normalizes_full_payload():
    out = validate_pmi(FULL_PMI)
    assert sorted(out) == ["datums", "dims", "fcf"]
    d1, d2 = out["dims"]
    assert d1 == {"id": "d1", "kind": "linear", "target": "width",
                  "plus": 0.1, "minus": 0.1}
    assert d2["target"] == 9.0 and isinstance(d2["target"], float)
    assert d2["note"] == "bolt holes"
    assert out["datums"] == [{"id": "A", "face": "bottom"}]
    f1, f2 = out["fcf"]
    assert f1["datums"] == [] and f1["note"] == "mounting face"
    assert f2["datums"] == ["A"] and f2["tol_mm"] == 0.2
    # empty dict clears (all sections default to empty)
    assert validate_pmi({}) == {"dims": [], "datums": [], "fcf": []}


def test_validate_pmi_duplicate_datum_letters_rejected():
    with pytest.raises(ValidationError):
        validate_pmi({"datums": [{"id": "A", "face": "top"},
                                 {"id": "A", "face": "bottom"}]})


def test_validate_pmi_position_requires_datums():
    with pytest.raises(ValidationError):
        validate_pmi({"fcf": [{"id": "f1", "type": "position", "tol_mm": 0.1}]})


def test_validate_pmi_plus_minus_both_zero_rejected():
    with pytest.raises(ValidationError):
        validate_pmi({"dims": [{"id": "d1", "kind": "linear", "target": "width",
                                "plus": 0, "minus": 0}]})


def test_unknown_dim_key_rejected_via_tool(demo, registry):
    result = registry.call("set_part_pmi", {
        "project": "demo", "part_id": "flange",
        "pmi": {"dims": [{"id": "d1", "kind": "linear", "target": "width",
                          "plus": 0.1, "minus": 0.1, "tolzone": 1}]},
    })
    assert result["error"]["type"] == "validation_error"
    assert "tolzone" in result["error"]["details"]["unknown"]
    assert "plus" in result["error"]["details"]["known"]


# ------------------------------------------------------- set/get persistence

def test_set_get_clear_roundtrip(demo, registry):
    result = registry.call("set_part_pmi", {
        "project": "demo", "part_id": "flange", "pmi": FULL_PMI})
    assert "error" not in result, result
    assert result["part_id"] == "flange"

    def on_disk_entry():
        manifest = json.loads(
            (demo.store.path_of("demo") / "project.json").read_text(encoding="utf-8"))
        return next(p for p in manifest["parts"] if p["id"] == "flange")

    entry = on_disk_entry()
    assert entry["pmi"]["datums"] == [{"id": "A", "face": "bottom"}]
    assert [d["id"] for d in entry["pmi"]["dims"]] == ["d1", "d2"]

    got = registry.call("get_part_pmi", {"project": "demo", "part_id": "flange"})
    assert got["pmi"] == result["pmi"]

    # empty dict clears the manifest key
    cleared = registry.call("set_part_pmi", {
        "project": "demo", "part_id": "flange", "pmi": {}})
    assert cleared["pmi"] == {"dims": [], "datums": [], "fcf": []}
    assert "pmi" not in on_disk_entry()
    got = registry.call("get_part_pmi", {"project": "demo", "part_id": "flange"})
    assert got["pmi"] == {"dims": [], "datums": [], "fcf": []}


def test_pmi_unknown_part_is_not_found(demo, registry):
    result = registry.call("get_part_pmi", {"project": "demo", "part_id": "nope"})
    assert result["error"]["type"] == "notfound_error"


def test_reference_part_pmi_set_get(demo, registry):
    # PMI is annotation, not geometry — reference (imported) parts take it too.
    demo.store.add_part("demo", "widget", "widget", "al6061", "",
                        kind="reference", source="widget.step")
    result = registry.call("set_part_pmi", {
        "project": "demo", "part_id": "widget",
        "pmi": {"datums": [{"id": "B", "face": "top"}]}})
    assert "error" not in result, result
    got = registry.call("get_part_pmi", {"project": "demo", "part_id": "widget"})
    assert got["pmi"]["datums"] == [{"id": "B", "face": "top"}]


# --------------------------------------------------------- drawing callouts

def test_drawing_renders_pmi_callouts(demo, registry):
    registry.call("set_part_pmi", {
        "project": "demo", "part_id": "flange", "pmi": FULL_PMI})
    result = registry.call("generate_drawing", {
        "project": "demo", "part_id": "flange", "format": "svg"})
    assert "error" not in result, result
    svg = (demo.store.exports_dir("demo") / "flange_drawing.svg").read_text(encoding="utf-8")
    # linear width tolerance (plus == minus renders as ±)
    assert "±0.10" in svg
    # matched hole-group diameter callout gains the asymmetric suffix
    assert "8x ⌀9.00 +0.05/-0.10" in svg
    # datum flag: boxed letter (letter text + a white-filled boxed rect style)
    assert ">A</text>" in svg
    assert 'fill="white" stroke="#1a56db"' in svg
    # FCF glyphs: flatness U+23E5 and position U+2316, plus the gray note
    assert "⏥" in svg and "⌖" in svg
    assert "mounting face" in svg
    detected = result["detected"]
    assert detected["pmi_rendered"] == {"dims": 2, "datums": 1, "fcf": 2}
    assert detected["pmi_warnings"] == []


def test_drawing_unmatched_diameter_warns(demo, registry):
    registry.call("set_part_pmi", {
        "project": "demo", "part_id": "flange",
        "pmi": {"dims": [{"id": "d1", "kind": "diameter", "target": 5.0,
                          "plus": 0.1, "minus": 0.1}]}})
    result = registry.call("generate_drawing", {
        "project": "demo", "part_id": "flange", "format": "svg"})
    assert "error" not in result, result
    detected = result["detected"]
    assert detected["pmi_rendered"] == {"dims": 0, "datums": 0, "fcf": 0}
    assert len(detected["pmi_warnings"]) == 1
    assert "d1" in detected["pmi_warnings"][0]
    assert "5" in detected["pmi_warnings"][0]


def test_drawing_without_pmi_unchanged(demo, registry):
    result = registry.call("generate_drawing", {
        "project": "demo", "part_id": "flange", "format": "svg"})
    assert "error" not in result, result
    assert "pmi_rendered" not in result["detected"]
    assert "pmi_warnings" not in result["detected"]
