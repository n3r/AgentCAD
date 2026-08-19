"""PRD-014 Drawings v2, Slice 4 — centerlines, center marks, hole tables.

FR8 (center marks in EVERY view, not today's top-view-only; coaxial runs seen
edge-on in a side view get a CHAIN centerline) and FR9 (a hole table in the
sheet's ``table_zone``: from PRD-010 hole metadata when the part has it — tag,
X, Y from a view datum, and the standard designation — else the detected
diameter groups, marked ``detected``).

The pure geometry helpers (projection, center-mark cross, run centerline) are
unit-tested directly; the table and the two fallbacks are asserted through the
real tool + kernel, where the shape is genuinely projected.
"""

from __future__ import annotations

import hashlib
import re

import pytest

from agentcad.core.tools import build_registry
from agentcad.kernel.handlers import drawing as D

from .conftest import make_test_service

# --- test parts ------------------------------------------------------------

# A tapped M5 (1 instance) + a bolt circle of 8 M6 clearance holes: two
# records, so the hole table is built FROM METADATA.
PLATE = '''\
from build123d import Box
from agentcad.toolkit import holes, patterns

PARAMS = {"t": {"default": 12.0, "min": 6.0, "max": 30.0, "unit": "mm",
                "description": "plate thickness"}}

def build(p):
    part = Box(120, 120, p.t)
    part, _r, _w = holes.tapped(part, [(0, 0)], "M5", depth=8)
    part, _r, _w = holes.clearance(part, patterns.bolt_circle(45, 8), "M6")
    return part
'''

# The same bolt circle, hand-cut: no toolkit call, so NO records anywhere and
# the table falls back to the detected diameter group.
HANDCUT = '''\
from build123d import *

PARAMS = {"t": {"default": 12.0, "min": 6.0, "max": 30.0, "unit": "mm",
                "description": "plate thickness"}}

def build(p):
    with BuildPart() as part:
        Box(120, 120, p.t)
        with PolarLocations(radius=45, count=8):
            Hole(radius=6.6 / 2)
    return part.part
'''

# A linear row of three holes drilled from the top face (axis +Z). In the TOP
# view they are circles (center marks); in the FRONT/RIGHT views they are seen
# edge-on and share a row — the coaxial-run centerline case.
ROW = '''\
from build123d import Box
from agentcad.toolkit import holes

PARAMS = {"t": {"default": 12.0, "min": 6.0, "max": 30.0, "unit": "mm",
                "description": "plate thickness"}}

def build(p):
    part = Box(120, 40, p.t)
    part, _r, _w = holes.drill(part, [(-40, 0), (0, 0), (40, 0)], 6.0)
    return part
'''

# Holes on the TOP face (axis Z) plus one tapped hole in the FRONT face
# (axis Y). The front-face hole is a circle in the FRONT view, so a center
# mark lands there — proving detection is no longer top-view-only.
SIDEHOLES = '''\
from build123d import Box
from agentcad.toolkit import holes

PARAMS = {"t": {"default": 40.0, "min": 20.0, "max": 60.0, "unit": "mm",
                "description": "block height"}}

def build(p):
    part = Box(80, 60, p.t)
    part, _r, _w = holes.drill(part, [(-20, 0), (0, 0), (20, 0)], 6.0)
    part, _r2, _w2 = holes.tapped(part, [(0, 0)], "M6", depth=10, plane="front")
    return part
'''

FIXED_VERSION = {"ref": "-", "date": "-"}

# THIN center-mark line (solid #111, 0.25, NO dasharray) vs the CHAIN
# centerline (same but dashed 4 1 1 1).
_THIN_LINE = 'stroke="#111" stroke-width="0.25" fill="none"'
_CHAIN = 'stroke-dasharray="4 1 1 1"'


@pytest.fixture
def demo(kernel, tmp_path):
    service = make_test_service(tmp_path / "projects", kernel)
    service.create_project("demo")
    return service, build_registry(service)


def _draw(demo, part_id, script, **kwargs):
    service, registry = demo
    service.create_part("demo", part_id, script=script)
    result = registry.call("generate_drawing",
                           {"project": "demo", "part_id": part_id, **kwargs})
    assert "error" not in result, result
    svg = (service.store.exports_dir("demo")
           / f"{part_id}_drawing.svg").read_text(encoding="utf-8")
    return result, svg


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---- FR9: hole table FROM METADATA ---------------------------------------


@pytest.mark.integration
def test_ac4_hole_table_from_metadata_prints_designation_and_tags(demo):
    """**AC4 (with metadata).** A part with PRD-010 records gets a hole table
    whose rows carry the standard designation and a per-hole tag; every hole
    instance appears and ``from_metadata`` is true."""
    result, svg = _draw(demo, "plate", PLATE, hole_table=True)

    table = result["hole_table"]
    assert table["from_metadata"] is True
    rows = table["rows"]

    # One tapped M5 (A1) + eight M6 clearance (B1..B8) = nine holes, all tabled.
    assert len(rows) == 9
    tags = {r["tag"] for r in rows}
    assert "A1" in tags and "B1" in tags and "B8" in tags

    tapped = [r for r in rows if r["designation"] == "M5×0.8 - 6H ↧8"]
    assert len(tapped) == 1 and tapped[0]["tag"] == "A1"
    assert sum(1 for r in rows if r["designation"] == "⌀6.6") == 8

    # Every row is machine-checkable: a tag, a datum X/Y, a designation.
    for r in rows:
        assert isinstance(r["tag"], str)
        assert isinstance(r["x_mm"], (int, float))
        assert isinstance(r["y_mm"], (int, float))
        assert "detected" not in r

    # The table and the cross-referencing tags are drawn on the sheet.
    assert "HOLE TABLE" in svg
    assert ">A1<" in svg and ">B1<" in svg


@pytest.mark.integration
def test_the_hole_table_datum_is_the_top_view_lower_left(demo):
    """The datum is documented as the top-view projected bbox lower-left, so
    every X/Y is a non-negative offset into the part."""
    result, _svg = _draw(demo, "plate", PLATE, hole_table=True)
    table = result["hole_table"]
    assert "lower-left" in table["datum"]
    for r in table["rows"]:
        assert r["x_mm"] >= -1e-6 and r["y_mm"] >= -1e-6


# ---- FR9: DETECTED fallback ----------------------------------------------


@pytest.mark.integration
def test_ac4_hole_table_detected_fallback_has_diameters_only(demo):
    """**AC4 (without metadata).** A hand-cut part has no records, so the table
    falls back to the detected diameter group: diameters only, each row marked
    ``detected``, and no fabricated designation."""
    result, svg = _draw(demo, "handcut", HANDCUT, hole_table=True)

    table = result["hole_table"]
    assert table["from_metadata"] is False
    rows = table["rows"]
    assert len(rows) == 8                       # the eight detected circles
    for r in rows:
        assert r["detected"] is True
        assert r["diameter_mm"] == pytest.approx(6.6, abs=0.02)
        assert "designation" not in r           # nothing fabricated
    assert "HOLE TABLE (detected)" in svg


# ---- FR8: center marks in every view + coaxial centerline ----------------


@pytest.mark.integration
def test_fr8_center_marks_appear_beyond_the_top_view(demo):
    """Center marks are drawn at every detected circle in EVERY view. The
    three top-face holes mark the top view (3 crosses); the front-face hole
    marks the FRONT view (1 cross) — more THIN crosses than a top-view-only
    detector could ever draw."""
    _result, svg = _draw(demo, "sideholes", SIDEHOLES,
                         views=["top", "front", "right"])

    # Each cross is two THIN lines. Top view: 3 holes; front view: 1 hole.
    thin_lines = svg.count(_THIN_LINE)
    assert thin_lines >= 8, thin_lines          # 4 crosses × 2 lines
    # A top-view-only detector would draw only the three top crosses (6 lines).
    assert thin_lines > 6


@pytest.mark.integration
def test_fr8_a_coaxial_run_gets_a_chain_centerline_in_a_side_view(demo):
    """A row of holes sharing an axis, seen edge-on in a side view, gets a thin
    CHAIN centerline spanning the run. The part has no sections, so a CHAIN
    dash in the SVG can only be that centerline."""
    _result, svg = _draw(demo, "row", ROW, views=["top", "front", "right"])
    assert _CHAIN in svg

    # …and not when the run is only ever seen face-on (top view alone).
    _result2, svg_top = _draw(demo, "row2", ROW, views=["top"])
    assert _CHAIN not in svg_top


# ---- determinism ----------------------------------------------------------


@pytest.mark.integration
def test_a_hole_table_sheet_is_byte_stable_svg_and_pdf(demo):
    """Two renders of the metadata part (fixed version) are byte-identical for
    both backends — center marks, centerlines and table rows are all sorted and
    every coordinate goes through ``fmt`` (FR12)."""
    service, registry = demo
    service.create_part("demo", "plate", script=PLATE)

    def _bytes(fmt):
        r = registry.call("generate_drawing", {
            "project": "demo", "part_id": "plate", "format": fmt,
            "hole_table": True, "version": FIXED_VERSION})
        assert "error" not in r, r
        return (service.store.exports_dir("demo")
                / f"plate_drawing.{fmt}").read_bytes()

    assert _sha(_bytes("svg")) == _sha(_bytes("svg"))
    pdf = _bytes("pdf")
    assert pdf.startswith(b"%PDF-")
    assert _sha(pdf) == _sha(_bytes("pdf"))


# ---- pure helpers ---------------------------------------------------------


def test_project_to_view_matches_the_orthographic_conventions():
    """The analytic point projection agrees with the view conventions the
    handler already relies on: top is identity (X, Y), front is (X, Z), right
    is (Y, Z)."""
    p = (3.0, 5.0, 7.0)
    assert D._project_to_view("top", p) == pytest.approx((3.0, 5.0))
    assert D._project_to_view("front", p) == pytest.approx((3.0, 7.0))
    assert D._project_to_view("right", p) == pytest.approx((5.0, 7.0))


def test_center_mark_is_two_thin_crossing_lines():
    els = D._center_mark(10.0, 20.0, arm=1.5)
    assert len(els) == 2
    assert all(e.style is D.Style.THIN for e in els)
    # centred on the point
    for e in els:
        assert (e.x1 + e.x2) / 2 == pytest.approx(10.0)
        assert (e.y1 + e.y2) / 2 == pytest.approx(20.0)


def test_axis_is_edge_on_when_perpendicular_to_the_view_direction():
    # A +Z axis is face-on in the top view and edge-on in front/right.
    assert D._edge_on("top", (0.0, 0.0, 1.0)) is False
    assert D._edge_on("front", (0.0, 0.0, 1.0)) is True
    assert D._edge_on("right", (0.0, 0.0, 1.0)) is True
