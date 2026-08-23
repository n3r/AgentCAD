"""PRD-014 Drawings v2, Slice 1 — the standards wrapper foundation.

Covers the central float formatter (determinism keystone), the sheet-template
table + uniform auto-scale (FR1), the data-driven title block (FR2), the
`drawing` manifest section + its tools (Decision 4), the deterministic version
ref/date service seam (Decision 5), and the FR13 result skeleton.

The flange family draws for real through the tool + kernel — the title block
and the auto-scale are only worth testing against a genuinely projected shape.
"""

from __future__ import annotations

import re
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


# ------------------------------------------ exact bounds + real primitives ---
#
# `_view_bounds` used to sample six points per edge (exact for a line, wrong
# for a circle) and `_edge_prim` discretised every non-closed-circle edge into
# up to 256 points. Both are fixed (changelog 0307): bounds come from each
# edge's own bounding box, a LINE draws as a two-point polyline and an open
# CIRCLE as a real arc.

#: A dead-prismatic part: every projected edge in every view is a LINE.
BLOCK = '''\
from build123d import *

PARAMS = {
    "w": {"default": 60.0, "min": 10.0, "max": 200.0, "unit": "mm", "description": "width"},
    "d": {"default": 40.0, "min": 10.0, "max": 200.0, "unit": "mm", "description": "depth"},
    "h": {"default": 10.0, "min": 2.0,  "max": 100.0, "unit": "mm", "description": "height"},
}

def build(p):
    with BuildPart() as part:
        Box(p.w, p.d, p.h)
    return part.part
'''

#: A part whose top-view silhouette extreme is a PARTIAL arc: a 60 x 40 block
#: with an R15 lug centred on the +Y edge, so exactly half the cylinder sticks
#: out. Top-view extents: 60 wide, 20 + 15 + 20 = 55 deep.
LUG = '''\
from build123d import *

PARAMS = {
    "r": {"default": 15.0, "min": 2.0, "max": 100.0, "unit": "mm", "description": "lug radius"},
}

def build(p):
    with BuildPart() as part:
        Box(60, 40, 10)
        with Locations((0, 20, 0)):
            Cylinder(radius=p.r, height=10)
    return part.part
'''

_DIM_TEXT = re.compile(r'fill="#1a56db"[^>]*>([^<]+)</text>')
_ARC_PATH = re.compile(
    r'<path d="M (-?[\d.]+) (-?[\d.]+) A (-?[\d.]+) (-?[\d.]+) 0 (\d) (\d) '
    r'(-?[\d.]+) (-?[\d.]+)"')


@pytest.fixture
def shapes(kernel, tmp_path):
    service = make_test_service(tmp_path / "projects", kernel)
    service.create_project("demo")
    service.create_part("demo", "block", script=BLOCK)
    service.create_part("demo", "lug", script=LUG)
    return service


def test_a_curved_silhouette_is_dimensioned_at_its_true_extent(demo):
    """The Ø140 flange's plan view used to be dimensioned 132.641 — the six
    point sampler missed the circle's own extremes. Exact per-edge bounding
    boxes put the silhouette back."""
    registry = build_registry(demo)
    result = registry.call("generate_drawing", {
        "project": "demo", "part_id": "flange", "views": ["top", "front"]})
    assert "error" not in result, result
    svg = _svg(demo)
    dims = _DIM_TEXT.findall(svg)
    # top view: 140 x 140; front view: 140 x 14. Three "140"s, no undersize.
    assert dims.count("140") == 3, dims
    assert "14" in dims, dims
    assert "132.64" not in svg
    assert "133.14" not in svg


def test_a_prismatic_part_draws_two_point_paths_not_tessellated_lines(shapes):
    """Every projected edge of a box is a LINE, so every outline path on the
    sheet is exactly ``M x y L x y`` — no 256-point discretisation of a
    dead-straight edge."""
    registry = build_registry(shapes)
    result = registry.call("generate_drawing", {
        "project": "demo", "part_id": "block"})
    assert "error" not in result, result
    svg = (shapes.store.exports_dir("demo") / "block_drawing.svg").read_text(
        encoding="utf-8")
    chunks = re.findall(r'<path d="M ([^"]+)"', svg)
    assert chunks, "the sheet drew no outline paths"
    assert all(chunk.count(" L ") == 1 for chunk in chunks), \
        max(chunks, key=lambda c: c.count(" L "))[:200]
    # a box has no circles, and the sheet is small because nothing is sampled
    assert "<circle" not in svg
    assert result["size_bytes"] < 20000, result["size_bytes"]


def test_a_partial_arc_silhouette_renders_as_an_arc_the_right_way_round(shapes):
    """The lug's outer half-circle is a real ``A`` segment, oriented so it
    bulges AWAY from the block, and the plan view is dimensioned 60 x 55."""
    registry = build_registry(shapes)
    result = registry.call("generate_drawing", {
        "project": "demo", "part_id": "lug", "views": ["top"]})
    assert "error" not in result, result
    svg = (shapes.store.exports_dir("demo") / "lug_drawing.svg").read_text(
        encoding="utf-8")
    dims = _DIM_TEXT.findall(svg)
    assert "60" in dims and "55" in dims, dims

    arcs = _ARC_PATH.findall(svg)
    assert arcs, "the lug's half-round silhouette did not render as an arc"
    scale = 1.0 / float(result["scale"].split(":")[1]) \
        if result["scale"].startswith("1:") else float(
            result["scale"].split(":")[0])
    semis = []
    for x0, y0, rx, _ry, large, sweep, x1, y1 in arcs:
        x0, y0, r, x1, y1 = (float(x0), float(y0), float(rx), float(x1),
                             float(y1))
        if abs(r - 15.0 * scale) > 0.01:
            continue                       # not the lug's own rim
        semis.append((x0, y0, r, large, sweep, x1, y1))
    assert semis, f"no R{15.0 * scale} arc on the sheet: {arcs}"
    for x0, y0, r, large, sweep, x1, y1 in semis:
        # Both endpoints sit on the block's +Y edge => the same sheet y, and
        # they are a full diameter apart: the segment IS the half circle.
        assert y0 == pytest.approx(y1, abs=1e-3), (x0, y0, x1, y1)
        assert abs(x1 - x0) == pytest.approx(2 * r, abs=1e-3), (x0, x1, r)
        # Left endpoint first, `large-arc 0`, `sweep 1`: in SVG's y-down frame
        # that is the clockwise half, i.e. the one that bulges UP the sheet
        # (+Y in the model, away from the block). Flip either the y-negation or
        # the ordering in `_arc_angles` and this pair changes.
        assert x1 > x0, (x0, x1)
        assert (large, sweep) == ("0", "1"), (large, sweep)


def test_dxf_carries_native_lines_and_arcs(shapes):
    """DXF used to be lwpolylines for everything but a closed circle."""
    import ezdxf

    registry = build_registry(shapes)
    assert "error" not in registry.call("generate_drawing", {
        "project": "demo", "part_id": "block", "format": "dxf"})
    block = list(ezdxf.readfile(str(
        shapes.store.exports_dir("demo") / "block_drawing.dxf")).modelspace())
    assert any(e.dxftype() == "LINE" for e in block), \
        sorted({e.dxftype() for e in block})
    assert not any(e.dxftype() == "LWPOLYLINE" for e in block), \
        sorted({e.dxftype() for e in block})

    assert "error" not in registry.call("generate_drawing", {
        "project": "demo", "part_id": "lug", "format": "dxf"})
    lug = list(ezdxf.readfile(str(
        shapes.store.exports_dir("demo") / "lug_drawing.dxf")).modelspace())
    types = sorted({e.dxftype() for e in lug})
    assert "ARC" in types, types
    assert "LINE" in types, types


def test_a_detail_view_clips_a_straight_edge_analytically(shapes):
    """`_clip_edges_to_circle` sampled every edge on a 0.4 mm grid, boundary
    included. A LINE is now clipped by solving the segment/circle quadratic, so
    a detail on a prismatic part is exact two-point runs whose ends sit ON the
    detail circle rather than on the nearest sample."""
    registry = build_registry(shapes)
    result = registry.call("generate_drawing", {
        "project": "demo", "part_id": "block", "views": ["top"],
        "details": [{"view": "top", "center_mm": [30, 20], "radius_mm": 8,
                     "scale": 2}]})
    assert "error" not in result, result
    assert result["details"][0]["clipped"] is True
    svg = (shapes.store.exports_dir("demo") / "block_drawing.svg").read_text(
        encoding="utf-8")
    chunks = re.findall(r'<path d="M ([^"]+)"', svg)
    assert chunks
    assert all(chunk.count(" L ") == 1 for chunk in chunks), \
        max(chunks, key=lambda c: c.count(" L "))[:200]


def test_clip_segment_to_circle_is_exact():
    """The pure quadratic behind the detail-view clip (no kernel involved)."""
    from agentcad.kernel.handlers.drawing import _clip_segment_to_circle

    # A chord straight through the centre: clipped to the two intersections.
    run = _clip_segment_to_circle(-10.0, 0.0, 10.0, 0.0, 0.0, 0.0, 3.0)
    assert run == [pytest.approx((-3.0, 0.0)), pytest.approx((3.0, 0.0))]
    # An endpoint already inside stays put; only the outside end is trimmed.
    run = _clip_segment_to_circle(0.0, 0.0, 10.0, 0.0, 0.0, 0.0, 3.0)
    assert run == [pytest.approx((0.0, 0.0)), pytest.approx((3.0, 0.0))]
    # Wholly inside: unchanged.
    assert _clip_segment_to_circle(-1.0, 0.0, 1.0, 0.0, 0.0, 0.0, 3.0) == \
        [pytest.approx((-1.0, 0.0)), pytest.approx((1.0, 0.0))]
    # A miss, a tangent touch and a zero-length segment are all "no run".
    assert _clip_segment_to_circle(-10.0, 5.0, 10.0, 5.0, 0.0, 0.0, 3.0) is None
    assert _clip_segment_to_circle(-10.0, 3.0, 10.0, 3.0, 0.0, 0.0, 3.0) is None
    assert _clip_segment_to_circle(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 3.0) is None
    # Entirely outside but on the line through the circle: still no run.
    assert _clip_segment_to_circle(5.0, 0.0, 9.0, 0.0, 0.0, 0.0, 3.0) is None
