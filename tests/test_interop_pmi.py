"""PRD-017 slice 1 — STEP AP242 PMI export (FR1, FR3).

Every fixture is authored through the real kernel from a build123d script
string (the ``test_reference.py::_make_step`` idiom) — no binary blobs in the
repo. The round trip matches PMI entries by **(type, value, tolerance,
target)**: entry identity does not survive OCCT's writer (a dimension labelled
"BORE_H7" reads back as 'diameter'), and a two-datum FCF reads back as three
datum labels, so datums are compared by name.

The assertions here are the machine half of AC1/AC6 and the standing guard on
the six spike traps: an AP214 fallback (trap 2) shows up as a missing
FILE_SCHEMA, a dropped datum reference (trap 3) as an empty ``datums`` list, a
metre-unit document (trap 4) as a tolerance 1000× too large.
"""

import re

import pytest

from agentcad.kernel.handlers import _pmi_map

# A block with a through bore: planar faces on all six sides for datums and
# linear dims, one cylindrical face for the diameter dim and cylindricity.
BORED_BLOCK = '''\
from build123d import *

PARAMS = {"bore": {"default": 10.0, "min": 2.0, "max": 30.0, "unit": "mm"}}

def build(p):
    return Box(40, 30, 20) - Cylinder(p.bore / 2, 40)
'''

# The same block, returned with a placement on it: `.wrapped` carries a
# non-identity TopLoc_Location, which is trap 1's trigger.
PLACED_BLOCK = '''\
from build123d import *

PARAMS = {"dx": {"default": 50.0, "min": 0.0, "max": 100.0, "unit": "mm"}}

def build(p):
    return Pos(p.dx, 0, 0) * (Box(40, 30, 20) - Cylinder(5, 40))
'''

PLAIN_BLOCK = '''\
from build123d import *

PARAMS = {"s": {"default": 20.0, "min": 5.0, "max": 50.0, "unit": "mm"}}

def build(p):
    return Box(40, 30, p.s)
'''

# A turned part: planar top and bottom, no planar side at all.
TURNED_PART = '''\
from build123d import *

PARAMS = {"d": {"default": 20.0, "min": 5.0, "max": 60.0, "unit": "mm"}}

def build(p):
    return Cylinder(p.d / 2, 30)
'''

FULL_PMI = {
    "dims": [
        {"id": "h", "kind": "linear", "target": "height",
         "plus": 0.2, "minus": 0.05},
        {"id": "bore", "kind": "diameter", "target": 10.0,
         "plus": 0.05, "minus": 0.02},
    ],
    "datums": [{"id": "A", "face": "top"}, {"id": "B", "face": "left"}],
    "fcf": [
        {"id": "flat", "type": "flatness", "tol_mm": 0.05, "datums": []},
        {"id": "pos", "type": "position", "tol_mm": 0.2, "datums": ["A", "B"]},
        {"id": "perp", "type": "perpendicularity", "tol_mm": 0.1,
         "datums": ["A"]},
        {"id": "par", "type": "parallelism", "tol_mm": 0.15, "datums": ["A"]},
        {"id": "cyl", "type": "cylindricity", "tol_mm": 0.03, "datums": []},
    ],
}


def _export(kernel, out_path, script=BORED_BLOCK, pmi=None, params=None):
    return kernel.request("export_step_pmi", {
        "script": script, "params": params or {}, "pmi": pmi or FULL_PMI,
        "out_path": str(out_path), "name": "TolerancedBlock",
    })


def _by_type(entries):
    return {e["type"]: e for e in entries}


# --------------------------------------------------------------- round trip


def test_ap242_pmi_round_trip(kernel, tmp_path):
    out = tmp_path / "block.step"
    result = _export(kernel, out)

    assert result["pmi_attached"] == {"dims": 2, "datums": 2, "fcf": 5}
    assert result["pmi_skipped"] == []
    assert result["size_bytes"] > 1000
    # trap 2: an AP214 file is valid STEP with zero PMI, so the schema is
    # asserted on the bytes, not inferred from a successful write.
    assert "AP242" in result["schema"]
    assert "AP242" in out.read_text()[:8192]

    read = kernel.request("read_step_pmi", {"path": str(out)})
    assert "AP242" in read["schema"]

    dims = _by_type(read["dims"])
    assert set(dims) == {"size_thickness", "size_diameter"}
    # trap 4: values are MILLIMETRES. A dimension-less document would report
    # the tolerances 1000x larger (0.05 mm -> 50.0).
    height = dims["size_thickness"]
    assert height["value"] == pytest.approx(20.0, abs=1e-6)
    assert height["plus"] == pytest.approx(0.2, abs=1e-9)
    assert height["minus"] == pytest.approx(0.05, abs=1e-9)
    assert height["targets"][0]["kind"] == "plane"
    bore = dims["size_diameter"]
    assert bore["value"] == pytest.approx(10.0, abs=1e-6)
    assert bore["plus"] == pytest.approx(0.05, abs=1e-9)
    assert bore["minus"] == pytest.approx(0.02, abs=1e-9)
    assert bore["targets"][0]["kind"] == "cylinder"
    assert bore["targets"][0]["diameter"] == pytest.approx(10.0, abs=1e-6)

    # datum NAMES, never label counts (a two-datum FCF reads back as three)
    assert read["datums"] == ["A", "B"]

    fcf = _by_type(read["fcf"])
    assert set(fcf) == {"flatness", "position", "perpendicularity",
                        "parallelism", "cylindricity"}
    assert fcf["flatness"]["value"] == pytest.approx(0.05, abs=1e-9)
    assert fcf["perpendicularity"]["value"] == pytest.approx(0.1, abs=1e-9)
    assert fcf["parallelism"]["value"] == pytest.approx(0.15, abs=1e-9)
    assert fcf["cylindricity"]["value"] == pytest.approx(0.03, abs=1e-9)
    assert fcf["cylindricity"]["targets"][0]["kind"] == "cylinder"
    # trap 3: without DatumObject.SetPosition these lists are silently empty
    # and perpendicularity/parallelism/position are dropped outright.
    assert fcf["position"]["datums"] == ["A", "B"]
    assert fcf["position"]["value"] == pytest.approx(0.2, abs=1e-9)
    assert fcf["position"]["zone"] == "diameter"
    assert fcf["perpendicularity"]["datums"] == ["A"]
    assert fcf["parallelism"]["datums"] == ["A"]


def test_datum_faces_target_the_matching_side(kernel, tmp_path):
    """A datum's face selector resolves deterministically: top = +Z, left = -X
    (``core/pmi.py``'s box-face semantics)."""
    out = tmp_path / "datums.step"
    pmi = {"dims": FULL_PMI["dims"][:1], "datums": FULL_PMI["datums"],
           "fcf": [{"id": "perp", "type": "perpendicularity", "tol_mm": 0.1,
                    "datums": ["A"]},
                   {"id": "par", "type": "parallelism", "tol_mm": 0.1,
                    "datums": ["B"]}]}
    _export(kernel, out, pmi=pmi)
    read = kernel.request("read_step_pmi", {"path": str(out)})
    fcf = _by_type(read["fcf"])
    # the FCF rides its first datum's face: A = top (+Z at z = 10),
    # B = left (-X at x = -20)
    assert fcf["perpendicularity"]["targets"][0]["center"] == pytest.approx(
        [0.0, 0.0, 10.0], abs=1e-6)
    assert fcf["parallelism"]["targets"][0]["center"] == pytest.approx(
        [-20.0, 0.0, 0.0], abs=1e-6)


def test_a_placed_part_keeps_both_its_pmi_and_its_placement(kernel, tmp_path):
    """Trap 1: ``AddShape`` on a located shape makes a reference label whose
    sub-shape labels are all null, so every datum silently fails. De-locating
    fixes that — but *dropping* the location (the spike's recipe, whose
    fixtures all sat at the origin) would move the part, so it is baked into
    the geometry instead."""
    out = tmp_path / "placed.step"
    result = _export(kernel, out, script=PLACED_BLOCK)
    assert result["pmi_attached"] == {"dims": 2, "datums": 2, "fcf": 5}
    assert result["pmi_skipped"] == []

    read = kernel.request("read_step_pmi", {"path": str(out)})
    assert read["datums"] == ["A", "B"]
    fcf = _by_type(read["fcf"])
    # datum A is the top face: still at x = +50, not slid back to the origin
    assert fcf["perpendicularity"]["targets"][0]["center"] == pytest.approx(
        [50.0, 0.0, 10.0], abs=1e-6)
    dims = _by_type(read["dims"])
    assert dims["size_thickness"]["value"] == pytest.approx(20.0, abs=1e-6)


# ------------------------------------------------------------- pmi_skipped


def test_diameter_dim_without_a_cylindrical_face_is_skipped(kernel, tmp_path):
    """AC6 seed: an unmappable entry is reported, never silently dropped —
    and the export still succeeds."""
    out = tmp_path / "plain.step"
    pmi = {
        "dims": [
            {"id": "h", "kind": "linear", "target": "height",
             "plus": 0.2, "minus": 0.05},
            {"id": "bore", "kind": "diameter", "target": 10.0,
             "plus": 0.05, "minus": 0.02},
        ],
        "datums": [], "fcf": [],
    }
    result = _export(kernel, out, script=PLAIN_BLOCK, pmi=pmi)
    assert result["pmi_attached"]["dims"] == 1
    assert [row["id"] for row in result["pmi_skipped"]] == ["bore"]
    assert result["pmi_skipped"][0]["reason"].startswith("no_cylindrical_face")
    assert out.is_file()

    read = kernel.request("read_step_pmi", {"path": str(out)})
    assert [d["type"] for d in read["dims"]] == ["size_thickness"]


def test_cylindricity_without_a_cylindrical_face_is_skipped(kernel, tmp_path):
    out = tmp_path / "plain_cyl.step"
    pmi = {"dims": [{"id": "h", "kind": "linear", "target": "height",
                     "plus": 0.1, "minus": 0.1}],
           "datums": [],
           "fcf": [{"id": "cyl", "type": "cylindricity", "tol_mm": 0.02,
                    "datums": []}]}
    result = _export(kernel, out, script=PLAIN_BLOCK, pmi=pmi)
    assert result["pmi_attached"]["fcf"] == 0
    assert result["pmi_skipped"] == [
        {"id": "cyl", "reason": result["pmi_skipped"][0]["reason"]}]
    assert result["pmi_skipped"][0]["reason"].startswith("no_cylindrical_face")


def test_a_datum_with_no_planar_face_is_skipped_and_its_references_noted(
        kernel, tmp_path):
    """A turned part has no planar left face, so datum B cannot be placed —
    and the frame that referenced it says so instead of quietly becoming a
    one-datum frame."""
    out = tmp_path / "turned.step"
    pmi = {"dims": [{"id": "od", "kind": "diameter", "target": 20.0,
                     "plus": 0.05, "minus": 0.05}],
           "datums": [{"id": "A", "face": "top"}, {"id": "B", "face": "left"}],
           "fcf": [{"id": "perp", "type": "perpendicularity", "tol_mm": 0.1,
                    "datums": ["A", "B"]}]}
    result = _export(kernel, out, script=TURNED_PART, pmi=pmi)
    assert result["pmi_attached"] == {"dims": 1, "datums": 1, "fcf": 1}
    assert [row["id"] for row in result["pmi_skipped"]] == ["B"]
    assert result["pmi_skipped"][0]["reason"].startswith("no_planar_face")
    assert any("'perp'" in note and "B" in note
               for note in result["pmi_notes"]), result["pmi_notes"]

    read = kernel.request("read_step_pmi", {"path": str(out)})
    assert read["datums"] == ["A"]
    assert _by_type(read["fcf"])["perpendicularity"]["datums"] == ["A"]


# ------------------------------------------------------- trap 4: the units


def test_fcf_only_pmi_gets_an_auxiliary_dimension_and_stays_in_mm(kernel,
                                                                  tmp_path):
    """A document with no DIMENSION mints METRE units for every tolerance
    measure — 0.05 mm reads back as 50.0. One untoleranced overall-size
    dimension pins the model's millimetres, and the substitution is reported."""
    out = tmp_path / "fcfonly.step"
    pmi = {"dims": [], "datums": [{"id": "A", "face": "top"}],
           "fcf": [{"id": "flat", "type": "flatness", "tol_mm": 0.05,
                    "datums": []},
                   {"id": "perp", "type": "perpendicularity", "tol_mm": 0.08,
                    "datums": ["A"]}]}
    result = _export(kernel, out, pmi=pmi)

    # the units assertion first: it is the one that catches the x1000, and a
    # schema check never would (the METRE file is perfectly valid AP242)
    read = kernel.request("read_step_pmi", {"path": str(out)})
    fcf = _by_type(read["fcf"])
    assert fcf["flatness"]["value"] == pytest.approx(0.05, abs=1e-9)
    assert fcf["perpendicularity"]["value"] == pytest.approx(0.08, abs=1e-9)
    assert fcf["perpendicularity"]["datums"] == ["A"]

    assert result["pmi_attached"] == {"dims": 1, "datums": 1, "fcf": 2}
    assert result["pmi_notes"] == [_pmi_map.AUX_DIM_NOTE]
    assert len(read["dims"]) == 1
    aux = read["dims"][0]
    assert aux["value"] == pytest.approx(20.0, abs=1e-6)   # the Z extent
    assert (aux["plus"], aux["minus"]) == (0.0, 0.0)       # untoleranced


# ------------------------------------------------------ trap 5: the signs


def test_lower_tolerance_is_written_negative(kernel, tmp_path):
    """``SetLowerTolValue`` takes a positive MAGNITUDE — the writer negates it.
    Handing it our model's ``minus`` signed would write a standards-incorrect
    file, and the reader returns the magnitude either way, so only the file
    text catches this one."""
    out = tmp_path / "signs.step"
    _export(kernel, out, pmi={"dims": [FULL_PMI["dims"][0]],
                              "datums": [], "fcf": []})
    text = out.read_text()
    measures = dict(re.findall(r"#(\d+) = MEASURE_WITH_UNIT\(([-+0-9.E]+),",
                               text))
    lower, upper = re.search(r"TOLERANCE_VALUE\(#(\d+),#(\d+)\)", text).groups()
    assert float(measures[lower]) == pytest.approx(-0.05, abs=1e-9)
    assert float(measures[upper]) == pytest.approx(0.2, abs=1e-9)


# ------------------------------------------- the plain export path is intact


def test_plain_step_export_is_unaffected_by_a_pmi_export(kernel, tmp_path):
    """``write.step.schema`` is a process-wide Interface_Static: left at
    AP242DIS it would silently re-schema every later ``b3d.export_step`` in the
    warm worker. The PMI writer restores it."""
    pmi_out = tmp_path / "with_pmi.step"
    _export(kernel, pmi_out)

    plain = tmp_path / "plain.step"
    result = kernel.request("export", {
        "script": PLAIN_BLOCK, "params": {}, "format": "step",
        "out_path": str(plain)})
    assert result["size_bytes"] > 1000
    header = plain.read_text()[:8192]
    assert header.startswith("ISO-10303-21")
    assert "AP242" not in header


# ---------------------------------------------------- trap 6: the blocklist


@pytest.mark.parametrize("name,targets", [
    ("Size_WithPath", 1),
    ("Size_WithPath", 2),
    ("Location_WithPath", 1),
    ("Location_Oriented", 2),
    ("Size_Angular", 1),
    ("Location_Angular", 1),
])
def test_blocked_dimension_types_refuse(name, targets):
    """These three segfault ``STEPCAFControl_Writer::Transfer`` (exit 139, no
    Python exception) and the angular pair round-trips in mismatched units.
    The refusal is asserted; the crash is never invoked."""
    with pytest.raises(_pmi_map.PmiRefusal) as exc:
        _pmi_map.dimension_type(name, targets)
    assert name in exc.value.reason


def test_unknown_dimension_type_refuses():
    with pytest.raises(_pmi_map.PmiRefusal):
        _pmi_map.dimension_type("Size_NotAThing")


def test_a_blocked_type_becomes_a_skipped_row_and_never_reaches_the_writer(
        monkeypatch, tmp_path):
    """Unit level, no kernel subprocess: a mapping whose dimension type is
    blocklisted yields a ``pmi_skipped`` row and leaves the XCAF document with
    zero dimension labels, so the writer is never handed one."""
    from build123d import Box
    from OCP.TDF import TDF_LabelSequence
    from OCP.XCAFDoc import XCAFDoc_DocumentTool

    monkeypatch.setitem(_pmi_map.DIM_TYPE_BY_KIND, "linear", "Size_WithPath")
    doc = _pmi_map.new_document()
    pmi = {"dims": [{"id": "h", "kind": "linear", "target": "height",
                     "plus": 0.1, "minus": 0.1}],
           "datums": [], "fcf": []}
    mapped = _pmi_map.map_pmi(doc, Box(40, 30, 20), pmi)

    assert mapped["attached"]["dims"] == 0
    assert [row["id"] for row in mapped["skipped"]] == ["h"]
    assert mapped["skipped"][0]["reason"].startswith(
        "blocked_dimension_type: Size_WithPath")
    labels = TDF_LabelSequence()
    XCAFDoc_DocumentTool.DimTolTool_s(doc.Main()).GetDimensionLabels(labels)
    assert labels.Length() == 0


# ------------------------------------------------- non-finite PMI values


@pytest.mark.parametrize("value", [float("nan"), float("inf"),
                                   float("-inf")])
def test_a_non_finite_pmi_value_is_a_skipped_row_never_a_written_nan(value):
    """`core/pmi.validate_pmi` compares against 0, and EVERY comparison with a
    NaN is False — so `plus < 0`, `plus == 0` and `tol <= 0` all wave one
    through. It then reached `SetValue`, OCCT wrote the literal `NAN` into the
    STEP file, and the export reported the entry as *attached*: a toleranced
    part whose tolerance no consumer can read, described as fine.

    Asserted where it can be seen: zero dimension and zero tolerance labels in
    the document, so the writer is never handed one.
    """
    from build123d import Box
    from OCP.TDF import TDF_LabelSequence
    from OCP.XCAFDoc import XCAFDoc_DocumentTool

    doc = _pmi_map.new_document()
    pmi = {
        "dims": [{"id": "h", "kind": "linear", "target": "height",
                  "plus": value, "minus": 0.1}],
        "datums": [{"id": "A", "face": "top"}],
        "fcf": [{"id": "flat", "type": "flatness", "tol_mm": value,
                 "datums": []}],
    }
    mapped = _pmi_map.map_pmi(doc, Box(40, 30, 20), pmi)

    assert mapped["attached"]["dims"] == 1        # the auxiliary unit dim only
    assert mapped["attached"]["fcf"] == 0
    assert sorted(row["id"] for row in mapped["skipped"]) == ["flat", "h"]
    for row in mapped["skipped"]:
        assert row["reason"].startswith("non_finite_value:"), row

    tool = XCAFDoc_DocumentTool.DimTolTool_s(doc.Main())
    tolerances = TDF_LabelSequence()
    tool.GetGeomToleranceLabels(tolerances)
    assert tolerances.Length() == 0
    dims = TDF_LabelSequence()
    tool.GetDimensionLabels(dims)
    assert dims.Length() == 1                     # only the auxiliary one


def test_a_non_finite_value_never_reaches_the_written_file(kernel, tmp_path):
    """End to end through the worker: the file exists, it is AP242, and the
    string `NAN` is nowhere in it."""
    out = tmp_path / "nan.step"
    result = _export(kernel, out, pmi={
        "dims": [{"id": "h", "kind": "linear", "target": "height",
                  "plus": 0.1, "minus": 0.1},
                 {"id": "bad", "kind": "linear", "target": "width",
                  "plus": float("nan"), "minus": 0.1}],
        "datums": [], "fcf": []})
    assert [row["id"] for row in result["pmi_skipped"]] == ["bad"]
    assert result["pmi_attached"]["dims"] == 1
    text = out.read_text(errors="replace")
    assert "AP242" in text[:8192]
    assert "NAN" not in text.upper()


def test_mesh_reference_refuses_pmi_export(kernel, tmp_path):
    """An STL reference part is a welded mesh face — no B-rep faces to hang
    PMI on. The refusal is explicit, not an empty PMI section."""
    from agentcad.kernel.client import KernelError

    stl = tmp_path / "blob.stl"
    kernel.request("export", {"script": PLAIN_BLOCK, "params": {},
                              "format": "stl", "out_path": str(stl)})
    with pytest.raises(KernelError) as exc:
        kernel.request("export_step_pmi", {
            "source_path": str(stl), "pmi": FULL_PMI,
            "out_path": str(tmp_path / "nope.step")})
    assert "mesh-only" in str(exc.value)


def test_reference_part_exports_pmi(kernel, tmp_path):
    """A reference (imported) part has no script — the source path is the
    other way in, the same two sources ``worker._item_shape`` resolves."""
    source = tmp_path / "source.step"
    kernel.request("export", {"script": BORED_BLOCK, "params": {},
                              "format": "step", "out_path": str(source)})
    out = tmp_path / "ref_pmi.step"
    result = kernel.request("export_step_pmi", {
        "source_path": str(source), "pmi": FULL_PMI, "out_path": str(out)})
    assert result["pmi_attached"] == {"dims": 2, "datums": 2, "fcf": 5}
    read = kernel.request("read_step_pmi", {"path": str(out)})
    assert read["datums"] == ["A", "B"]
