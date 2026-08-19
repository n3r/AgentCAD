"""PRD-014 Drawings v2, Slice 1 — the standards wrapper foundation.

Covers the central float formatter (determinism keystone), the sheet-template
table + uniform auto-scale (FR1), the data-driven title block (FR2), the
`drawing` manifest section + its tools (Decision 4), the deterministic version
ref/date service seam (Decision 5), and the FR13 result skeleton.

The flange family draws for real through the tool + kernel — the title block
and the auto-scale are only worth testing against a genuinely projected shape.
"""

from __future__ import annotations

import shutil
import xml.etree.ElementTree as ET

import pytest

from agentcad.core.materials import DEFAULT_MATERIAL
from agentcad.core.tools import build_registry
from agentcad.kernel.handlers._draw_primitives import fmt
from agentcad.kernel.handlers._sheets import SHEETS

from .conftest import make_test_service

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
    service = make_test_service(tmp_path / "projects", kernel)
    service.create_project("demo")
    service.create_part("demo", "flange", script=FLANGE)
    return service


def _svg(service, name="flange_drawing.svg") -> str:
    return (service.store.exports_dir("demo") / name).read_text(encoding="utf-8")


# --------------------------------------------------------------- fmt() ------

def test_fmt_is_canonical_and_deterministic():
    # The one locked canonical form: no trailing zeros/dot, never -0, round
    # half-even to 3 dp.
    assert fmt(-0.0) == "0"
    assert fmt(0.0) == "0"
    assert fmt(1.0) == "1"
    assert fmt(1.5) == "1.5"
    assert fmt(140.0) == "140"
    assert fmt(1.2345) == "1.234"     # dropped 5 ties to the even 4
    assert fmt(2.5005) == "2.5" or fmt(2.5005) == "2.501"  # (tie handling)
    # Never locale/scientific; a big and a tiny value stay decimal.
    assert fmt(1234567.0) == "1234567"
    assert fmt(0.0004) == "0"          # below the 3 dp quantum
    # Deterministic: same input -> byte-identical output, every call.
    assert fmt(1.0 / 3.0) == fmt(1.0 / 3.0) == "0.333"


# ---------------------------------------------------- default sheet parity --

def test_default_iso_a3_keeps_the_four_labels_and_detected_diameters(demo):
    registry = build_registry(demo)
    result = registry.call("generate_drawing", {
        "project": "demo", "part_id": "flange", "format": "svg"})
    assert "error" not in result, result
    assert result["sheet"] == "iso_a3"
    svg = _svg(demo)
    for label in ("TOP", "FRONT", "RIGHT", "ISO"):
        assert label in svg
    detected = result["detected"]
    assert any(g["count"] == 8 for g in detected["hole_groups"])
    assert 140.0 in detected["diameters_mm"] or 139.99 in detected["diameters_mm"]
    # iso_a3 preserves the pre-v2 420x297 sheet.
    assert 'viewBox="0 0 420 297"' in svg
    assert 'width="420mm"' in svg and 'height="297mm"' in svg


# ------------------------------------------------------- every sheet format --

@pytest.mark.parametrize("sheet", sorted(SHEETS))
def test_each_sheet_format_sets_its_own_viewbox_and_echoes(demo, sheet):
    registry = build_registry(demo)
    out = registry.call("generate_drawing", {
        "project": "demo", "part_id": "flange", "sheet": sheet})
    assert "error" not in out, out
    assert out["sheet"] == sheet
    tpl = SHEETS[sheet]
    svg = _svg(demo)
    assert f'viewBox="0 0 {fmt(tpl.w_mm)} {fmt(tpl.h_mm)}"' in svg
    assert f'width="{fmt(tpl.w_mm)}mm"' in svg
    assert f'height="{fmt(tpl.h_mm)}mm"' in svg


def test_an_unknown_sheet_is_a_validation_error(demo):
    registry = build_registry(demo)
    out = registry.call("generate_drawing", {
        "project": "demo", "part_id": "flange", "sheet": "iso_a9"})
    assert out["error"]["type"] == "validation_error"
    assert "iso_a9" in out["error"]["message"]


# --------------------------------------------------------- uniform auto-scale

def test_auto_scale_is_a_standard_ratio_reported_and_printed(demo):
    registry = build_registry(demo)
    result = registry.call("generate_drawing", {
        "project": "demo", "part_id": "flange"})
    assert "error" not in result, result
    scale = result["scale"]
    # A preferred-ladder ratio, printed engineering-style.
    assert scale in {"100:1", "50:1", "20:1", "10:1", "5:1", "2:1", "1:1",
                     "1:2", "1:5", "1:10", "1:20", "1:50", "1:100", "1:200"}
    # ...and the same string is in the title block.
    assert f"scale {scale}" in _svg(demo)


def test_a_scale_override_that_overflows_warns(demo):
    registry = build_registry(demo)
    # 100:1 blows a 140 mm flange far past any sheet — honored, but warned.
    result = registry.call("generate_drawing", {
        "project": "demo", "part_id": "flange", "scale": 100.0})
    assert "error" not in result, result
    assert result["scale"] == "100:1"
    assert any("overflow" in w for w in result["warnings"]), result["warnings"]


# ------------------------------------------------------ data-driven title block

def test_title_block_renders_all_fr2_fields(demo):
    registry = build_registry(demo)
    set_out = registry.call("set_drawing_fields", {
        "project": "demo", "fields": {
            "company": "Acme Works", "author": "N. Fedorov",
            "project_code": "PRJ-42", "approved_by": "QA Lead",
            "notes": "prototype only"}})
    assert "error" not in set_out, set_out

    result = registry.call("generate_drawing", {
        "project": "demo", "part_id": "flange", "sheet": "iso_a3"})
    assert "error" not in result, result
    svg = _svg(demo)
    # material + units + sheet + scale + version ref.
    assert DEFAULT_MATERIAL in svg              # 'al6061'
    assert "mm" in svg
    assert "iso_a3" in svg
    assert f"scale {result['scale']}" in svg
    # version ref: no repo here -> a working-tree content hash.
    assert "wt-" in svg
    # mass rendered as a readable string, not an em dash.
    assert (" g<" in svg) or (" kg<" in svg)
    # every drawing field that was set.
    for value in ("Acme Works", "N. Fedorov", "PRJ-42", "QA Lead",
                  "prototype only"):
        assert value in svg, value
    # the SVG still parses with all that text spliced in.
    ET.fromstring(svg)


# -------------------------------------------------- drawing-fields tools -----

def test_set_drawing_fields_validates_and_round_trips(demo):
    registry = build_registry(demo)

    # unknown key -> validation_error naming it.
    bad = registry.call("set_drawing_fields", {
        "project": "demo", "fields": {"vendor": "x"}})
    assert bad["error"]["type"] == "validation_error"
    assert "vendor" in bad["error"]["message"]

    # a control character -> refused.
    ctrl = registry.call("set_drawing_fields", {
        "project": "demo", "fields": {"company": "bad\x07name"}})
    assert ctrl["error"]["type"] == "validation_error"

    # a good write round-trips through get.
    ok = registry.call("set_drawing_fields", {
        "project": "demo", "fields": {"company": "Acme", "author": "Ada"}})
    assert "error" not in ok, ok
    got = registry.call("get_drawing_fields", {"project": "demo"})
    assert got["drawing"]["company"] == "Acme"
    assert got["drawing"]["author"] == "Ada"
    # unset fields are present but empty.
    assert got["drawing"]["notes"] == ""

    # an empty string clears one field; the section survives with the rest.
    cleared = registry.call("set_drawing_fields", {
        "project": "demo", "fields": {"company": ""}})
    assert "error" not in cleared, cleared
    assert cleared["drawing"]["company"] == ""
    assert registry.call("get_drawing_fields",
                         {"project": "demo"})["drawing"]["author"] == "Ada"

    # clearing the last field omits the section entirely from the manifest.
    registry.call("set_drawing_fields", {
        "project": "demo", "fields": {"author": ""}})
    assert "drawing" not in demo.store.manifest("demo")


# --------------------------------------------- _drawing_version service seam --

def test_drawing_version_no_repo_is_a_content_hash_and_deterministic(demo):
    from agentcad.core.tools_drawing import _drawing_version

    v1 = _drawing_version(demo, "demo")
    v2 = _drawing_version(demo, "demo")
    assert v1 == v2                                # determinism
    assert v1["date"] == "-"
    assert v1["ref"].startswith("wt-")
    assert len(v1["ref"]) == len("wt-") + 7        # 'wt-' + 7 hex

    # a change to authored state changes the ref (same-state determinism is not
    # a coincidence of an unchanging hash).
    demo.create_part("demo", "second", script=FLANGE)
    assert _drawing_version(demo, "demo")["ref"] != v1["ref"]


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_drawing_version_uses_head_and_then_a_tag(demo):
    from agentcad.core.tools_drawing import _drawing_version

    path = demo.store.path_of("demo")
    head = demo.history.snapshot(path, "init")     # creates repo + first commit
    assert head, "snapshot should have committed"

    v = _drawing_version(demo, "demo")
    assert v["ref"] == head[:7]
    # the date is the commit date (ISO YYYY-MM-DD), never a wall clock we cannot
    # reproduce — just assert the shape.
    assert len(v["date"]) == 10 and v["date"][4] == "-" and v["date"] != "-"

    # a tag pointing at HEAD wins over the raw sha.
    demo.history._run(path, "tag", "v1.0")
    assert _drawing_version(demo, "demo")["ref"] == "v1.0"


# ------------------------------------------------------ FR13 result skeleton --

def test_result_carries_the_full_slice1_contract(demo):
    registry = build_registry(demo)
    result = registry.call("generate_drawing", {
        "project": "demo", "part_id": "flange"})
    assert "error" not in result, result
    for key in ("path", "size_bytes", "sheet", "scale", "views", "sections",
                "detected", "warnings"):
        assert key in result, key
    assert result["sections"] == []                # filled in Slice 3
    assert result["views"] == ["top", "front", "right", "iso"]
    assert isinstance(result["warnings"], list)
    assert "pmi_rendered" not in result["detected"]  # no PMI on this part


# ------------------------------------------ version override (FR12 keystone) --

def test_version_override_pins_the_title_block_and_is_byte_stable(demo):
    """The `version` override pins the title-block identity instead of deriving
    it from git — the geometry-CI determinism stage's fixed-date path. Two runs
    with the same override are byte-identical, and the override wins over the
    git/content-hash version. (Regression: a project and its git-stripped
    determinism mirror rendered different version cells and diverged.)"""
    registry = build_registry(demo)
    fixed = {"ref": "-", "date": "-"}
    r1 = registry.call("generate_drawing", {
        "project": "demo", "part_id": "flange", "version": fixed})
    a = _svg(demo)
    r2 = registry.call("generate_drawing", {
        "project": "demo", "part_id": "flange", "version": fixed})
    b = _svg(demo)
    assert "error" not in r1 and "error" not in r2
    assert a == b                                   # byte-stable under a pin
    assert "rev -   -" in a                          # the pinned identity renders
    # and the pin actually overrides the derived (wt-…) version
    derived = registry.call("generate_drawing", {
        "project": "demo", "part_id": "flange"})
    assert "error" not in derived
    assert "wt-" in _svg(demo)                        # default derives a content ref
