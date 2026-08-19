"""PRD-014 Drawings v2, Slice 2 — the deterministic PDF backend (FR11, FR12).

The whole point of this slice is byte-stable vector PDF output produced with no
new dependency, from the SAME display list the SVG backend renders. These tests
are the FR12 tripwire (also run by PRD-004's geometry-CI determinism stage):
regenerate twice → identical sha256 for **both** formats, and a `project_restore`
reproduces the original bytes (AC2). The structural half of AC6 parses the PDF
with a tiny in-test validator — no PDF library is imported.
"""

from __future__ import annotations

import hashlib
import re
import shutil

import pytest

from agentcad.core.tools import build_registry
from agentcad.kernel.handlers._pdf import _K

from .conftest import FLANGE_SCRIPT, make_test_service

#: A fixed version identity so the title block never varies with git state —
#: the same pin the geometry-CI determinism stage passes to both mirror trees.
FIXED_VERSION = {"ref": "-", "date": "-"}

#: A part's PMI section (tolerances, one datum, two feature control frames),
#: inlined so this file does not depend on another test module's fixture.
FULL_PMI = {
    "dims": [
        {"id": "d1", "kind": "linear", "target": "width",
         "plus": 0.1, "minus": 0.1},
        {"id": "d2", "kind": "diameter", "target": 9, "plus": 0.05,
         "minus": 0.1, "note": "bolt holes"},
    ],
    "datums": [{"id": "A", "face": "bottom"}],
    "fcf": [
        {"id": "f1", "type": "flatness", "tol_mm": 0.05,
         "note": "mounting face"},
        {"id": "f2", "type": "position", "tol_mm": 0.2, "datums": ["A"]},
    ],
}


@pytest.fixture
def demo(kernel, tmp_path):
    service = make_test_service(tmp_path / "projects", kernel)
    service.create_project("demo")
    service.create_part("demo", "flange", script=FLANGE_SCRIPT)
    return service


def _gen(demo, registry, fmt, **extra):
    """Generate a drawing and return the bytes written for it."""
    result = registry.call("generate_drawing", {
        "project": "demo", "part_id": "flange", "format": fmt,
        "version": FIXED_VERSION, **extra})
    assert "error" not in result, result
    suffix = f"_{extra['config']}" if extra.get("config") else ""
    data = (demo.store.exports_dir("demo") /
            f"flange{suffix}_drawing.{fmt}").read_bytes()
    return result, data


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate_pdf(data: bytes, w_mm: float, h_mm: float) -> None:
    """A tiny structural PDF validator (AC6 machine half) — NOT a PDF library.

    Header, a single page, one pages tree, a well-formed `xref`/`startxref`/
    `%%EOF`, and a `/MediaBox` matching the sheet size in points.
    """
    assert data.startswith(b"%PDF-"), data[:8]
    assert data.rstrip().endswith(b"%%EOF")
    # Exactly one page object (`/Type /Page` but not `/Pages`), one pages tree.
    assert len(re.findall(rb"/Type\s*/Page(?![a-zA-Z])", data)) == 1
    assert len(re.findall(rb"/Type\s*/Pages\b", data)) == 1
    # A cross-reference section and a startxref offset that lands on it.
    assert b"\nxref\n" in data
    m = re.search(rb"startxref\s+(\d+)\s+%%EOF", data)
    assert m, "startxref/%%EOF trailer missing"
    assert data[int(m.group(1)):int(m.group(1)) + 4] == b"xref"
    # MediaBox = [0 0 W_pt H_pt], within rounding of the sheet size in points.
    box = re.search(
        rb"/MediaBox\s*\[\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*\]",
        data)
    assert box, "no /MediaBox"
    x0, y0, x1, y1 = (float(box.group(i)) for i in range(1, 5))
    assert x0 == 0.0 and y0 == 0.0
    assert abs(x1 - w_mm * _K) < 0.05, (x1, w_mm * _K)
    assert abs(y1 - h_mm * _K) < 0.05, (y1, h_mm * _K)


# --------------------------------------------------------- FR12 determinism ---

def test_pdf_and_svg_are_byte_deterministic(demo):
    """FR12: two renders of the same part at the same state are byte-identical
    for BOTH the PDF and the SVG backend (a fixed version pin removes the only
    git-derived variation)."""
    registry = build_registry(demo)

    _r1, pdf1 = _gen(demo, registry, "pdf")
    _r2, pdf2 = _gen(demo, registry, "pdf")
    assert pdf1.startswith(b"%PDF-")
    assert _sha(pdf1) == _sha(pdf2)

    _s1, svg1 = _gen(demo, registry, "svg")
    _s2, svg2 = _gen(demo, registry, "svg")
    assert _sha(svg1) == _sha(svg2)


def test_a_configuration_pdf_is_deterministic_too(demo):
    """The determinism holds for a configuration + dim-table sheet, which
    exercises the table primitives on the PDF path."""
    from .conftest import THREE_SIZE_CONFIGS

    demo.store.update_part_entry("demo", "flange", configs=THREE_SIZE_CONFIGS)
    registry = build_registry(demo)
    _r1, a = _gen(demo, registry, "pdf", config="l", dim_table=True)
    _r2, b = _gen(demo, registry, "pdf", config="l", dim_table=True)
    assert _sha(a) == _sha(b)
    _validate_pdf(a, 420.0, 297.0)


# ------------------------------------------------------- AC6 structural parse --

def test_pdf_is_structurally_valid_and_reported(demo):
    registry = build_registry(demo)
    result, data = _gen(demo, registry, "pdf")
    _validate_pdf(data, 420.0, 297.0)
    assert result["path"].endswith("flange_drawing.pdf")
    assert result["sheet"] == "iso_a3"
    assert result["size_bytes"] == len(data)
    # The FR13 result skeleton is format-agnostic.
    for key in ("path", "size_bytes", "sheet", "scale", "views", "sections",
                "detected", "warnings"):
        assert key in result, key


@pytest.mark.parametrize("sheet,w,h", [
    ("iso_a4", 297.0, 210.0),
    ("iso_a3", 420.0, 297.0),
    ("ansi_b", 431.8, 279.4),
])
def test_each_sheet_mediabox_matches_its_size(demo, sheet, w, h):
    registry = build_registry(demo)
    _result, data = _gen(demo, registry, "pdf", sheet=sheet)
    _validate_pdf(data, w, h)


# -------------------------------------------------------------- AC2 restore ----

@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_restore_reproduces_the_original_pdf_and_svg_bytes(demo):
    """AC2/FR12: snapshot a state, render (fixed version), mutate a parameter
    so the geometry — and the bytes — change, then `restore` the snapshot and
    regenerate → the original sha256 for BOTH formats."""
    registry = build_registry(demo)
    path = demo.store.path_of("demo")
    head = demo.history.snapshot(path, "init")
    assert head, "snapshot should have committed"

    svg_before = _sha(_gen(demo, registry, "svg")[1])
    pdf_before = _sha(_gen(demo, registry, "pdf")[1])

    # Mutate the OD: a bigger flange is a different sheet, so the bytes move.
    demo.set_params("demo", "flange", {"outer_d": 180.0})
    assert _sha(_gen(demo, registry, "svg")[1]) != svg_before
    assert _sha(_gen(demo, registry, "pdf")[1]) != pdf_before

    # Restore the snapshot: the original manifest (and its params) comes back.
    demo.history.restore(path, head)
    assert _sha(_gen(demo, registry, "svg")[1]) == svg_before
    assert _sha(_gen(demo, registry, "pdf")[1]) == pdf_before


# ----------------------------------------------------------- PMI in the PDF ----

def test_pmi_renders_in_the_pdf(demo):
    """PMI renders on the PDF path as it does in SVG (FR11): the same
    `pmi_rendered` counts, and a PMI sheet is strictly larger than the bare one
    (more content-stream operators). Robust across the glyph-encoding limits of
    the base-14 Helvetica text path."""
    registry = build_registry(demo)

    bare_result, bare = _gen(demo, registry, "pdf")
    assert "pmi_rendered" not in bare_result["detected"]

    ok = registry.call("set_part_pmi", {
        "project": "demo", "part_id": "flange", "pmi": FULL_PMI})
    assert "error" not in ok, ok

    pmi_result, withpmi = _gen(demo, registry, "pdf")
    assert pmi_result["detected"]["pmi_rendered"] == {
        "dims": 2, "datums": 1, "fcf": 2}
    assert pmi_result["detected"]["pmi_warnings"] == []
    assert len(withpmi) > len(bare)
    _validate_pdf(withpmi, 420.0, 297.0)


# --------------------------------------------------------------- the route ----

def test_the_pdf_route_streams_the_bytes(demo):
    """`GET …/drawing.pdf` regenerates server-side and streams
    `application/pdf` — the same bytes the tool wrote (FR11)."""
    from fastapi.testclient import TestClient

    from agentcad.server.app import create_app

    registry = build_registry(demo)
    app = create_app(demo, registry, extra_allowed_hosts={"testserver"})
    http = TestClient(app, base_url="http://127.0.0.1")

    response = http.get("/api/projects/demo/parts/flange/drawing.pdf")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF-")
    _validate_pdf(response.content, 420.0, 297.0)
    on_disk = (demo.store.exports_dir("demo") / "flange_drawing.pdf"
               ).read_bytes()
    assert response.content == on_disk


def test_the_pdf_route_validates_the_configuration_name(demo):
    """Same `CONFIG_RE.fullmatch` gate the SVG route applies — a bad name is a
    422 in the route, before the tool is ever reached."""
    from fastapi.testclient import TestClient

    from agentcad.server.app import create_app

    registry = build_registry(demo)
    app = create_app(demo, registry, extra_allowed_hosts={"testserver"})
    http = TestClient(app, base_url="http://127.0.0.1")

    for bad in ("../../etc/passwd", "M", "-m", "x" * 33):
        response = http.get(
            f"/api/projects/demo/parts/flange/drawing.pdf?config={bad}")
        assert response.status_code == 422, (bad, response.text)
        assert "configuration name" in response.json()["error"]["message"]
