"""PRD-014 Drawings v2, Slice 3 — section & detail views (FR6, FR7).

Sections cut the already-built shape **in the drawing handler** (no second
kernel round-trip): each solid body is sectioned separately, its outer + inner
wires become closed 2D loops, and the composition draws the outline plus a
per-body ``Hatch`` with alternating 45°/135° angles, an ``A-A`` label, and
cutting-plane marks on the parent view. Details clip the parent view's
already-computed projection to a circle and magnify it — a pure 2D op. Bad
specs are ``validation_error`` naming the offending entry, and the whole thing
stays byte-deterministic (SVG **and** PDF) under a fixed version pin.

The display-list-level assertions (the two-body alternating hatch) build the
shape in-process via ``worker.build_shape`` and call ``_build_display_list``
directly; the contract-level assertions drive the real tool through the
registry.
"""

from __future__ import annotations

import hashlib

import pytest

from agentcad.core.tools import build_registry
from agentcad.kernel.handlers import drawing as draw
from agentcad.kernel.handlers._draw_primitives import Hatch, Polyline, Text
from agentcad.kernel.worker import build_shape

from .conftest import FLANGE_SCRIPT, make_test_service

#: A fixed title-block version so bytes never vary with git state.
FIXED_VERSION = {"ref": "-", "date": "-"}

BOX_SCRIPT = '''\
from build123d import *

PARAMS = {"size": {"default": 30.0, "min": 5.0, "max": 100.0, "unit": "mm",
                   "description": "cube edge"}}

def build(p):
    return Box(p.size, p.size, p.size)
'''

# Two separated solid bodies straddling x=0; an xz cut (y=0) crosses both.
TWO_BODY_SCRIPT = '''\
from build123d import *

PARAMS = {"gap": {"default": 60.0, "min": 30.0, "max": 120.0, "unit": "mm",
                  "description": "centre-to-centre gap"}}

def build(p):
    a = Box(20, 20, 10).moved(Location((-p.gap / 2, 0, 0)))
    b = Box(20, 20, 10).moved(Location((p.gap / 2, 0, 0)))
    return Compound(children=[a, b])
'''


@pytest.fixture
def demo(kernel, tmp_path):
    service = make_test_service(tmp_path / "projects", kernel)
    service.create_project("demo")
    service.create_part("demo", "box", script=BOX_SCRIPT)
    service.create_part("demo", "twobody", script=TWO_BODY_SCRIPT)
    service.create_part("demo", "flange", script=FLANGE_SCRIPT)
    return service


def _svg(service, name) -> str:
    return (service.store.exports_dir("demo") / name).read_text(encoding="utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hatches(dl):
    return [p for p in dl if isinstance(p, Hatch)]


# --------------------------------------------------------------- FR6 sections --

def test_a_box_section_draws_a_hatched_view_and_reports_one_body(demo):
    registry = build_registry(demo)
    result = registry.call("generate_drawing", {
        "project": "demo", "part_id": "box", "format": "svg",
        "sections": [{"plane": "xz", "offset_mm": 0}]})
    assert "error" not in result, result
    assert len(result["sections"]) == 1
    entry = result["sections"][0]
    assert entry["label"] == "A-A"
    assert entry["plane"] == "xz"
    assert entry["bodies"] >= 1
    assert not entry.get("empty")
    svg = _svg(demo, "box_drawing.svg")
    # the A-A label renders, and the section hatch renders as HATCH-styled lines.
    assert "A-A" in svg
    assert 'stroke="#1a56db"' in svg          # the HATCH/DIM blue


def test_two_bodies_alternate_hatch_angles(demo):
    """A multi-solid section hatches per body with alternating 45°/135° — the
    display-list contract, asserted directly on the primitives."""
    shape, _v, _w = build_shape(TWO_BODY_SCRIPT, {})
    detected: dict = {"label": "twobody"}
    dl, _W, _H, meta = draw._build_display_list(
        shape, ["top", "front", "right", "iso"], detected,
        sections=[{"plane": "xz", "offset_mm": 0.0, "label": None}])
    hatches = _hatches(dl)
    assert len(hatches) == 2, [h.angle for h in hatches]
    assert sorted(h.angle for h in hatches) == [45.0, 135.0]
    assert meta["sections"][0]["bodies"] == 2


def test_a_plane_that_misses_warns_and_draws_an_empty_view(demo):
    registry = build_registry(demo)
    result = registry.call("generate_drawing", {
        "project": "demo", "part_id": "box", "format": "svg",
        "sections": [{"plane": "xz", "offset_mm": 500.0}]})
    assert "error" not in result, result           # not an error
    entry = result["sections"][0]
    assert entry["empty"] is True
    assert entry["bodies"] == 0
    assert any("A-A" in w and "misses" in w for w in result["warnings"]), \
        result["warnings"]
    # the sheet is NOT blank — the empty section view is still labelled.
    assert "A-A" in _svg(demo, "box_drawing.svg")


def test_a_malformed_section_spec_is_a_validation_error_naming_the_entry(demo):
    registry = build_registry(demo)
    bad_plane = registry.call("generate_drawing", {
        "project": "demo", "part_id": "box",
        "sections": [{"plane": "xy", "offset_mm": 0},
                     {"plane": "diagonal", "offset_mm": 0}]})
    assert bad_plane["error"]["type"] == "validation_error"
    assert "section[1]" in bad_plane["error"]["message"]

    bad_offset = registry.call("generate_drawing", {
        "project": "demo", "part_id": "box",
        "sections": [{"plane": "xy", "offset_mm": "middle"}]})
    assert bad_offset["error"]["type"] == "validation_error"
    assert "section[0]" in bad_offset["error"]["message"]
    assert "offset_mm" in bad_offset["error"]["message"]


def test_multiple_sections_get_sequential_labels(demo):
    registry = build_registry(demo)
    result = registry.call("generate_drawing", {
        "project": "demo", "part_id": "box", "format": "svg",
        "sections": [{"plane": "xz", "offset_mm": 0},
                     {"plane": "yz", "offset_mm": 0}]})
    assert "error" not in result, result
    assert [s["label"] for s in result["sections"]] == ["A-A", "B-B"]


# ---------------------------------------------------------------- FR7 details --

def test_a_detail_view_magnifies_and_labels_with_its_scale(demo):
    registry = build_registry(demo)
    result = registry.call("generate_drawing", {
        "project": "demo", "part_id": "flange", "format": "svg",
        "details": [{"view": "top", "center_mm": [0, 0], "radius_mm": 15,
                     "scale": 2}]})
    assert "error" not in result, result
    assert len(result["details"]) == 1
    d = result["details"][0]
    assert d["label"] == "A" and d["view"] == "top" and d["scale"] == 2.0
    assert d["clipped"] is True
    svg = _svg(demo, "flange_drawing.svg")
    assert "A (2:1)" in svg                         # the magnified-view label


def test_detail_view_appears_on_the_display_list_as_a_circle_and_clip(demo):
    """The parent circle + the magnified clip are on the list (checked at the
    primitive level so the magnification is not just the parent circle)."""
    shape, _v, _w = build_shape(FLANGE_SCRIPT, {})
    detected: dict = {"label": "flange"}
    dl, _W, _H, meta = draw._build_display_list(
        shape, ["top", "front", "right", "iso"], detected,
        details=[{"view": "top", "center_mm": [0.0, 0.0], "radius_mm": 15.0,
                  "scale": 2.0}])
    assert meta["details"][0]["clipped"] is True
    # the magnified label carries the ratio, and clipped edges are polylines.
    assert any(isinstance(p, Text) and p.s == "A (2:1)" for p in dl)
    assert any(isinstance(p, Polyline) for p in dl)


@pytest.mark.parametrize("bad,needle", [
    ({"view": "nope", "center_mm": [0, 0], "radius_mm": 5, "scale": 2},
     "detail[0] view"),
    ({"view": "top", "center_mm": [0], "radius_mm": 5, "scale": 2},
     "detail[0] center_mm"),
    ({"view": "top", "center_mm": [0, 0], "radius_mm": 0, "scale": 2},
     "detail[0] radius_mm"),
    ({"view": "top", "center_mm": [0, 0], "radius_mm": 5, "scale": -1},
     "detail[0] scale"),
])
def test_a_malformed_detail_spec_is_a_validation_error(demo, bad, needle):
    registry = build_registry(demo)
    result = registry.call("generate_drawing", {
        "project": "demo", "part_id": "flange", "details": [bad]})
    assert result["error"]["type"] == "validation_error"
    assert needle in result["error"]["message"]


# --------------------------------------------------------- FR12 determinism ----

def test_a_section_and_detail_sheet_is_byte_stable_svg_and_pdf(demo):
    """Two renders of a part with a section AND a detail are byte-identical for
    both formats — the section wire extraction and the hatch lines are sorted /
    anchored so nothing leaks OCCT iteration order (FR12)."""
    registry = build_registry(demo)
    args = {"project": "demo", "part_id": "flange", "version": FIXED_VERSION,
            "sections": [{"plane": "xz", "offset_mm": 0}],
            "details": [{"view": "top", "center_mm": [59, 0], "radius_mm": 12,
                         "scale": 2}]}

    def _bytes(fmt):
        r = registry.call("generate_drawing", {**args, "format": fmt})
        assert "error" not in r, r
        return (demo.store.exports_dir("demo") /
                f"flange_drawing.{fmt}").read_bytes()

    assert _sha(_bytes("svg")) == _sha(_bytes("svg"))
    pdf = _bytes("pdf")
    assert pdf.startswith(b"%PDF-")
    assert _sha(pdf) == _sha(_bytes("pdf"))


# --------------------------------------------- HTTP route forwards the surface

def test_the_get_routes_forward_sheet_views_and_sections(demo):
    """Regression (PRD-014 slice 6 gap): the SVG/PDF preview GET routes must
    forward sheet / views / sections / scale, not just config / dim_table — the
    browser preview sends them (`sections` as JSON, `views` as CSV) and the GET
    step regenerates the file, so a route that dropped them served the wrong
    sheet. A malformed `sections` JSON is a 422 here, before the tool."""
    import json as _json

    from fastapi.testclient import TestClient

    from agentcad.server.app import create_app

    registry = build_registry(demo)
    app = create_app(demo, registry, extra_allowed_hosts={"testserver"})
    http = TestClient(app, base_url="http://127.0.0.1")

    r = http.get("/api/projects/demo/parts/box/drawing.svg", params={
        "sheet": "ansi_a", "views": "top,front",
        "sections": _json.dumps([{"plane": "xz", "offset_mm": 0}])})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/svg+xml"
    body = r.text
    assert "A-A" in body                     # the section rode through
    assert 'width="279.4mm"' in body         # the ansi_a sheet, not iso_a3

    # the PDF twin honors sheet too
    p = http.get("/api/projects/demo/parts/box/drawing.pdf",
                 params={"sheet": "iso_a4"})
    assert p.status_code == 200 and p.content.startswith(b"%PDF-")

    # a malformed sections JSON is a 422 (the tool never sees it)
    bad = http.get("/api/projects/demo/parts/box/drawing.svg",
                   params={"sections": "{not json"})
    assert bad.status_code == 422

    # a bare GET (no new params) is unchanged
    plain = http.get("/api/projects/demo/parts/box/drawing.svg")
    assert plain.status_code == 200
