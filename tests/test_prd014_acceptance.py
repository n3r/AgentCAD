"""PRD-014 Drawings v2 — consolidated acceptance suite (AC1–AC7).

One place that grades the PRD's *acceptance criteria* against the shipped
surface, driving the real ``generate_drawing`` tool through the real kernel on
the bundled **construction gusset** (``examples/construction/gusset_plate``) —
the part AC1 names. The slice suites (``test_drawings_v2.py``,
``test_drawings_pdf.py``, ``test_drawings_sections.py``,
``test_drawings_holes_table.py``, ``test_drawings_tabulate.py``) already grade
the mechanics; this file grades the *criteria*, once each.

What is machine-checked here vs. graded elsewhere:

* **AC1** (machine) — the A3 gusset sheet: frame, populated title block
  (material + mass + scale + version ref), a sectioned view labelled ``A-A``
  with hatching, center marks on the bolt holes. The browser-screenshot half
  of AC1 is the controller's.
* **AC2** (machine) — byte-stability: regenerate twice ⇒ identical sha256 for
  SVG **and** PDF (fixed ``version`` pin neutralises git identity); and, when
  git is on PATH, mutate-a-param → ``restore`` → original bytes.
* **AC4** (machine) — hole table *with* PRD-010 metadata (the gusset's drilled
  records ⇒ designations + per-hole tags) and *without* (a hand-cut plate ⇒
  detected diameters only).
* **AC5** (partial) — FR10 config tabulation IS built: the config table exists
  with letter variables. The ``get_bom`` cross-check half is **deferred to
  PRD-015** (skipped, on the record).
* **AC6** (machine) — the PDF opens under a strict structural parse (one
  ``/Type /Page``, ``/MediaBox``, ``%%EOF``, page count 1); the SVG is
  well-formed XML. The browser "zero console errors" half is the controller's.
* **AC7** (machine, structural) — an existing ``generate_drawing`` call with no
  new arguments still yields the four view labels + detected diameters, wrapped
  in the default ``iso_a3`` sheet, with the FR13 result shape unchanged. A
  structural assertion rather than a golden fixture (a full-sheet golden with a
  live title block is fragile; the determinism guarantee is graded by AC2).
* **AC3** (deferred) — assembly balloons + on-sheet BOM need PRD-015 (skipped).

The gusset is opened from a **copy** of the bundled example (never the source
tree), the house rule for example-driven tests.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from agentcad.core.materials import DEFAULT_MATERIAL
from agentcad.core.tools import build_registry
from agentcad.kernel.handlers._pdf import _K

from .conftest import (
    FLANGE_SCRIPT,
    THREE_SIZE_CONFIGS,
    make_test_service,
)

pytestmark = [pytest.mark.integration, pytest.mark.slow]

REPO = Path(__file__).resolve().parents[1]
EXAMPLES = REPO / "examples"

#: The part AC1 names — a truss gusset with a drilled bolt group (PRD-010
#: records) and a plate profile that a mid-thickness XY cut sections cleanly.
GUSSET = "gusset_plate"

#: A version identity pinned so the title block never varies with git state —
#: the same neutralisation the geometry-CI determinism stage passes to a tree
#: and its git-stripped mirror. AC1 uses a legible ref to prove the field
#: renders; AC2 uses "-"/"-" for the byte comparison.
NAMED_VERSION = {"ref": "WP-7", "date": "2026-01-02"}
FIXED_VERSION = {"ref": "-", "date": "-"}

#: A center mark is two of these THIN crossing lines (see
#: ``test_drawings_holes_table``); the gusset's twelve bolt holes ⇒ 24 in the
#: top view alone.
_THIN_LINE = 'stroke="#111" stroke-width="0.25" fill="none"'
#: The HATCH / DIM blue — section hatching renders as strokes of this colour.
_HATCH_BLUE = 'stroke="#1a56db"'

#: The preferred scale ladder (FR1); the reported scale must be one of these.
_SCALE_LADDER = {"100:1", "50:1", "20:1", "10:1", "5:1", "2:1", "1:1",
                 "1:2", "1:5", "1:10", "1:20", "1:50", "1:100", "1:200"}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate_pdf(data: bytes, w_mm: float, h_mm: float) -> None:
    """A tiny structural PDF validator (AC6 machine half) — NOT a PDF library.

    Header, exactly one page object, one pages tree, a well-formed
    ``xref``/``startxref``/``%%EOF``, and a ``/MediaBox`` matching the sheet
    size in points. Mirrors ``test_drawings_pdf._validate_pdf`` so the criterion
    is graded by the same structural bar the slice test uses.
    """
    assert data.startswith(b"%PDF-"), data[:8]
    assert data.rstrip().endswith(b"%%EOF")
    # Exactly one page (`/Type /Page` but not `/Pages`) ⇒ page count 1.
    assert len(re.findall(rb"/Type\s*/Page(?![a-zA-Z])", data)) == 1
    assert len(re.findall(rb"/Type\s*/Pages\b", data)) == 1
    assert b"\nxref\n" in data
    m = re.search(rb"startxref\s+(\d+)\s+%%EOF", data)
    assert m, "startxref/%%EOF trailer missing"
    assert data[int(m.group(1)):int(m.group(1)) + 4] == b"xref"
    box = re.search(
        rb"/MediaBox\s*\[\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*\]",
        data)
    assert box, "no /MediaBox"
    x0, y0, x1, y1 = (float(box.group(i)) for i in range(1, 5))
    assert x0 == 0.0 and y0 == 0.0
    assert abs(x1 - w_mm * _K) < 0.05, (x1, w_mm * _K)
    assert abs(y1 - h_mm * _K) < 0.05, (y1, h_mm * _K)


# ----------------------------------------------------------------- fixtures --


@pytest.fixture
def gusset(kernel, tmp_path):
    """The bundled construction example, opened from a COPY (never the source),
    with real git history so AC2's restore path is reachable."""
    service = make_test_service(tmp_path / "projects", kernel)
    dest = tmp_path / "copies" / "construction"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(EXAMPLES / "construction", dest,
                    ignore=shutil.ignore_patterns(".cache", "exports"))
    name = service.open_project(str(dest))["name"]
    service.history.snapshot(dest, "import the construction example")
    return service, name, build_registry(service)


def _drawing_bytes(service, name, part_id, fmt) -> bytes:
    return (service.store.exports_dir(name) /
            f"{part_id}_drawing.{fmt}").read_bytes()


def _svg(service, name, part_id) -> str:
    return _drawing_bytes(service, name, part_id, "svg").decode("utf-8")


# =================================================================== AC1 =====


def test_ac1_the_gusset_a3_sheet_has_frame_titleblock_section_and_center_marks(
        gusset):
    """**AC1** — the construction gusset produces an A3 ISO sheet with a frame,
    a populated title block (material, mass, scale, version ref), a sectioned
    view labelled ``A-A`` with hatching, and center marks on the bolt holes.

    Every element is asserted on the one SVG. The section is an XY cut at
    mid-plate-thickness, which crosses the plate and its holes so the body
    hatches; the version ref is pinned to a legible token to prove the cell
    renders. (The browser-preview screenshot half of AC1 is the controller's.)
    """
    service, name, registry = gusset
    result = registry.call("generate_drawing", {
        "project": name, "part_id": GUSSET, "sheet": "iso_a3",
        "sections": [{"plane": "xy", "offset_mm": 5.0}],
        "hole_table": True, "version": NAMED_VERSION})
    assert "error" not in result, result
    assert result["sheet"] == "iso_a3"

    svg = _svg(service, name, GUSSET)

    # --- the frame: iso_a3 is 420x297, and the sheet frame is a FRAME-styled box
    assert 'viewBox="0 0 420 297"' in svg
    assert 'width="420mm"' in svg and 'height="297mm"' in svg

    # --- the populated title block: material, mass, scale, version ref ---------
    assert "steel_a36" in svg                       # the gusset's material
    assert (" kg<" in svg) or (" g<" in svg)        # a rendered mass, not an em dash
    scale = result["scale"]
    assert scale in _SCALE_LADDER
    assert f"scale {scale}" in svg                  # the chosen scale is printed
    assert "rev WP-7" in svg                         # the pinned version ref renders

    # --- a sectioned view labelled A-A with hatching --------------------------
    assert len(result["sections"]) == 1
    section = result["sections"][0]
    assert section["label"] == "A-A"
    assert section["plane"] == "xy"
    assert not section.get("empty")
    assert section["bodies"] >= 1                    # a hatched body, not a blank cut
    assert "A-A" in svg                              # the section label renders
    assert _HATCH_BLUE in svg                        # hatch geometry renders

    # --- center marks on the bolt holes ---------------------------------------
    # The gusset drills a twelve-bolt group; each detected circle in the top
    # view gets a two-line cross. Twenty-four THIN lines is more than any
    # non-hole furniture on this sheet could account for.
    assert svg.count(_THIN_LINE) >= 24, svg.count(_THIN_LINE)

    # --- the whole sheet still parses with all of that spliced in -------------
    ET.fromstring(svg)


# =================================================================== AC2 =====


def test_ac2_regenerating_the_sheet_twice_is_byte_identical_svg_and_pdf(gusset):
    """**AC2, first half** — regenerating the gusset sheet twice at the same
    project state yields identical sha256 for the SVG **and** the PDF. A fixed
    ``version`` pin removes the only git-derived variation, so any residual
    difference would be an unsorted OCCT iteration order or a stray timestamp.
    """
    service, name, registry = gusset
    args = {"project": name, "part_id": GUSSET, "sheet": "iso_a3",
            "sections": [{"plane": "xy", "offset_mm": 5.0}],
            "hole_table": True, "version": FIXED_VERSION}

    def _render(fmt: str) -> str:
        out = registry.call("generate_drawing", {**args, "format": fmt})
        assert "error" not in out, out
        return _sha(_drawing_bytes(service, name, GUSSET, fmt))

    assert _render("svg") == _render("svg")
    assert _render("pdf") == _render("pdf")


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_ac2_restore_reproduces_the_original_bytes(gusset):
    """**AC2, second half** — snapshot a state, render it (pinned version),
    mutate a parameter so the geometry and the bytes move, then ``restore`` the
    snapshot and regenerate → the original sha256 for BOTH formats. This is the
    proposal/CI guarantee: the drawing is a compiled artifact of the version.
    """
    service, name, registry = gusset
    path = service.store.path_of(name)
    # The fixture already committed the imported example, so HEAD *is* the
    # snapshot to restore to; a fresh snapshot here would find nothing to commit.
    head = service.history.head(path)
    assert head, "the imported example should have a committed HEAD"

    args = {"project": name, "part_id": GUSSET, "sheet": "iso_a3",
            "sections": [{"plane": "xy", "offset_mm": 5.0}],
            "version": FIXED_VERSION}

    def _render(fmt: str) -> str:
        out = registry.call("generate_drawing", {**args, "format": fmt})
        assert "error" not in out, out
        return _sha(_drawing_bytes(service, name, GUSSET, fmt))

    svg_before, pdf_before = _render("svg"), _render("pdf")

    # A thicker plate is a different sheet, so the bytes must move.
    service.set_params(name, GUSSET, {"plate_t": 18.0})
    assert _render("svg") != svg_before
    assert _render("pdf") != pdf_before

    # Restore the snapshot: the original params — and bytes — come back.
    service.history.restore(path, head)
    assert _render("svg") == svg_before
    assert _render("pdf") == pdf_before


# =================================================================== AC4 =====

# A hand-cut plate: bolt holes carved with a raw ``Hole`` (no toolkit call), so
# there are NO PRD-010 records anywhere and the hole table must fall back to the
# detected diameter group.
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


def test_ac4_hole_table_with_prd010_metadata_prints_designations_and_tags(
        gusset):
    """**AC4, with metadata** — the gusset's drilled records (PRD-010) drive a
    hole table whose rows carry a standard designation and a per-hole tag, with
    ``from_metadata`` true and every bolt tabled. Graded at acceptance level on
    the gusset itself (the slice test grades a tapped/clearance plate), so the
    two do not merely duplicate.
    """
    service, name, registry = gusset
    result = registry.call("generate_drawing", {
        "project": name, "part_id": GUSSET, "hole_table": True,
        "version": NAMED_VERSION})
    assert "error" not in result, result

    table = result["hole_table"]
    assert table["from_metadata"] is True
    rows = table["rows"]
    assert len(rows) == 12                           # the twelve-bolt group
    for row in rows:
        assert isinstance(row["tag"], str) and row["tag"]
        assert isinstance(row["x_mm"], (int, float))
        assert isinstance(row["y_mm"], (int, float))
        assert row["designation"].startswith("⌀")   # ⌀ + the diameter
        assert "detected" not in row                 # nothing fabricated as a guess
    # The ⌀18 bolt-hole designation is the diameter measured from the records.
    assert all(r["designation"] == "⌀18" for r in rows)

    svg = _svg(service, name, GUSSET)
    assert "HOLE TABLE" in svg
    assert ">A1<" in svg                             # the cross-referencing tag


def test_ac4_hole_table_without_metadata_is_detected_diameters_only(
        kernel, tmp_path):
    """**AC4, without metadata** — a hand-cut plate has no records, so the table
    falls back to the detected diameter group: diameters only, each row marked
    ``detected``, and no fabricated designation.
    """
    service = make_test_service(tmp_path / "projects", kernel)
    service.create_project("hc")
    service.create_part("hc", "handcut", script=HANDCUT)
    registry = build_registry(service)

    result = registry.call("generate_drawing", {
        "project": "hc", "part_id": "handcut", "hole_table": True,
        "version": NAMED_VERSION})
    assert "error" not in result, result

    table = result["hole_table"]
    assert table["from_metadata"] is False
    rows = table["rows"]
    assert len(rows) == 8                            # the eight detected circles
    for row in rows:
        assert row["detected"] is True
        assert row["diameter_mm"] == pytest.approx(6.6, abs=0.02)
        assert "designation" not in row
    svg = (service.store.exports_dir("hc") /
           "handcut_drawing.svg").read_text(encoding="utf-8")
    assert "HOLE TABLE (detected)" in svg


# =================================================================== AC5 =====


def test_ac5_a_three_config_family_tabulates_letter_dims_and_a_config_table(
        kernel, tmp_path):
    """**AC5, the built half (FR10)** — a three-config flange with
    ``tabulate: true`` renders letter variables (A/B/C = overall X/Y/Z) and a
    config table with one row per member, each value measured from that
    configuration's built shape plus a per-config mass.

    The other half of AC5 — cross-checking the table against ``get_bom`` — is
    deferred to PRD-015 (see the skipped placeholder below).
    """
    service = make_test_service(tmp_path / "projects", kernel)
    service.create_project("fam")
    service.store.add_part("fam", "flange", "Flange", DEFAULT_MATERIAL,
                           FLANGE_SCRIPT)
    service.store.update_part_entry("fam", "flange", configs=THREE_SIZE_CONFIGS)
    registry = build_registry(service)

    result = registry.call("generate_drawing", {
        "project": "fam", "part_id": "flange", "tabulate": True,
        "version": NAMED_VERSION})
    assert "error" not in result, result

    table = result["config_table"]
    assert [v["letter"] for v in table["variables"]] == ["A", "B", "C"]
    assert [r["config"] for r in table["rows"]] == ["s", "m", "l"]
    assert all(r["ok"] for r in table["rows"])
    letters = [v["letter"] for v in table["variables"]]
    for row in table["rows"]:
        assert set(row["values"]) == set(letters)
        assert all(row["values"][L] is not None for L in letters)
        assert row["mass"]
    # Measured, not echoed: X/Y are the outer diameter, Z the thickness.
    vals = {r["config"]: r["values"] for r in table["rows"]}
    assert [vals[n]["A"] for n in ("s", "m", "l")] == [100.0, 140.0, 200.0]
    assert {vals[n]["C"] for n in ("s", "m", "l")} == {14.0}

    svg = (service.store.exports_dir("fam") /
           "flange_drawing.svg").read_text(encoding="utf-8")
    for cell in (">config<", ">A<", ">B<", ">C<", ">mass<"):
        assert svg.count(cell) >= 1, cell


@pytest.mark.skip(reason="FR5 BOM cross-check requires PRD-015 (BOM); deferred")
def test_ac5_config_table_values_match_get_bom():
    """**AC5, the deferred half** — the config table's values matching a
    ``get_bom`` output is on the record but cannot be graded until PRD-015 lands
    ``get_bom``. (The tabulation half is graded above.)"""


# =================================================================== AC6 =====


def test_ac6_the_pdf_opens_under_a_strict_parse_and_the_svg_is_well_formed(
        gusset):
    """**AC6** — the gusset PDF opens under a strict structural parse (one
    ``/Type /Page``, a ``/MediaBox`` matching iso_a3, a well-formed
    ``xref``/``%%EOF``; page count 1), and the SVG is well-formed XML.

    The browser "SVG preview with zero console errors" half is the controller's
    (evidence-graded); this asserts the machine half of both artifacts.
    """
    service, name, registry = gusset
    args = {"project": name, "part_id": GUSSET, "sheet": "iso_a3",
            "sections": [{"plane": "xy", "offset_mm": 5.0}],
            "hole_table": True, "version": FIXED_VERSION}

    pdf_result = registry.call("generate_drawing", {**args, "format": "pdf"})
    assert "error" not in pdf_result, pdf_result
    pdf = _drawing_bytes(service, name, GUSSET, "pdf")
    _validate_pdf(pdf, 420.0, 297.0)                 # iso_a3, one page
    assert pdf_result["size_bytes"] == len(pdf)
    assert pdf_result["path"].endswith("gusset_plate_drawing.pdf")

    svg_result = registry.call("generate_drawing", {**args, "format": "svg"})
    assert "error" not in svg_result, svg_result
    svg = _svg(service, name, GUSSET)
    root = ET.fromstring(svg)                         # well-formed XML
    assert root.tag.endswith("svg")


# =================================================================== AC7 =====


def test_ac7_an_existing_call_with_no_new_args_is_unchanged_plus_the_wrapper(
        kernel, tmp_path):
    """**AC7** — an existing ``generate_drawing`` call with no new arguments
    still produces the four projected view labels and the detected diameters,
    now wrapped in the default ``iso_a3`` sheet, with the FR13 result shape
    unchanged.

    A structural assertion rather than a golden fixture: a full-sheet golden
    carrying a live title block is fragile against any cosmetic sheet tweak,
    while the *byte-stability* it would test is graded directly by AC2. Graded
    on the historical flange (the part today's drawings were written against).
    """
    service = make_test_service(tmp_path / "projects", kernel)
    service.create_project("legacy")
    service.create_part("legacy", "flange", script=FLANGE_SCRIPT)
    registry = build_registry(service)

    # The pre-v2 call shape: nothing but project + part_id.
    result = registry.call("generate_drawing", {
        "project": "legacy", "part_id": "flange"})
    assert "error" not in result, result

    # The default sheet wrapper, and the unchanged core outputs.
    assert result["sheet"] == "iso_a3"
    assert result["views"] == ["top", "front", "right", "iso"]
    assert result["sections"] == []                  # none requested
    # The FR13 result skeleton is present and stable.
    for key in ("path", "size_bytes", "sheet", "scale", "views", "sections",
                "detected", "warnings"):
        assert key in result, key
    assert isinstance(result["warnings"], list)

    detected = result["detected"]
    # The bolt circle is still detected (eight holes), and the OD is measured.
    assert any(g["count"] == 8 for g in detected["hole_groups"])
    assert any(abs(d - 140.0) < 0.05 for d in detected["diameters_mm"]), \
        detected["diameters_mm"]

    svg = (service.store.exports_dir("legacy") /
           "flange_drawing.svg").read_text(encoding="utf-8")
    for label in ("TOP", "FRONT", "RIGHT", "ISO"):
        assert label in svg
    # iso_a3 preserves the pre-v2 420x297 sheet exactly.
    assert 'viewBox="0 0 420 297"' in svg


# =================================================================== AC3 =====


@pytest.mark.skip(reason="FR4/FR5 assembly balloons + on-sheet BOM require "
                         "PRD-015 (BOM); deferred")
def test_ac3_assembly_sheet_balloons_match_the_bom_rows():
    """**AC3, deferred** — the assembly sheet showing one balloon per BOM line
    with numbers matching the on-sheet BOM table, and the tool result's
    ``balloons`` mapping every BOM item, is on the record but requires PRD-015.

    The design spec (Decision 12) defers FR4/FR5 with PRD-015: kernel handlers
    cannot reach ``service``/mate resolution, and balloons/BOM need
    ``get_bom``. A ``part_id``-omitted request returns a PRD-015 warning today,
    never a blank assembly sheet — this criterion is graded when PRD-015 lands.
    """
