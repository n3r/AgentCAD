"""PRD-017 Interop pack acceptance — AC1–AC7.

One test (or a small, named pair) per criterion, graded against the shipped
surface: the real service, the real registry, the real kernel, the files on
disk. Where a slice suite already proves the same claim case by case
(`tests/test_interop_pmi.py`, `test_interop_import*.py`, `test_interop_gltf.py`,
`test_interop_3mf.py`, `test_interop_step_asm.py`, `test_interop_usd.py`,
`test_xchange_pack.py`), this file restates it *compactly* rather than
duplicating the list — the house rule from `tests/test_prd026_acceptance.py`.

| AC | Test |
|---|---|
| AC1 | `test_ac1_a_toleranced_part_round_trips_through_ap242` |
| AC2 | `test_ac2_a_multi_product_step_lands_deduplicated_placed_and_named`, `test_ac2_holds_at_scale_fourteen_products_and_fortyone_occurrences`, `test_ac2_structured_false_is_still_one_blob` |
| AC3 | `test_ac3_an_assembly_glb_is_structurally_valid_and_byte_stable` |
| AC4 | `test_ac4_a_3mf_is_conformant_millimetre_and_carries_metadata_and_colours` |
| AC5 | `test_ac5_usd_is_offered_exactly_when_it_can_run`, `test_ac5_a_stage_exports_and_reopens` |
| AC6 | `test_ac6_every_interop_result_reports_fidelity`, `test_ac6_an_unmappable_pmi_entry_is_reported_not_dropped` |
| AC7 | `test_ac7_the_legacy_import_and_export_surfaces_are_unchanged`, `test_ac7_the_import_guards_still_fire`, `test_ac7_the_full_suite_count_is_cited` |

**The three manual halves are evidence-graded, not stubbed.** Each AC that
names a human check says here where that evidence lives, because a test that
pretended to run FreeCAD would be worse than no test:

* **AC1 — FreeCAD's AP242 viewer.** The automated half is the round trip
  below: our own XCAF reader re-opens the written file and every dim, datum
  and FCF comes back with millimetre values. Opening the same file in
  FreeCAD's AP242 viewer is a per-release manual check, recorded in
  `docs/user-guide.md` ("Per-release manual interop checks").
* **AC3 — the vendored Three.js loader.** The automated half is the
  structural validation and the byte-identical sha below. The browser half
  was run with Playwright + the installed Chrome in slice 6 (changelog
  `0316-interop-frontend.md`: preview render, structured landing, GLB export
  toast; screenshots in the session scratchpad, referenced from the PR).
* **AC4 — PrusaSlicer.** The automated half is the OPC/XML conformance, the
  `millimeter` unit, the metadata and the lib3mf re-read below. Opening the
  file in the PrusaSlicer/Bambu/Orca lineage is the same per-release manual
  check as AC1's viewer.
"""

from __future__ import annotations

import re
import struct
import zipfile
from hashlib import sha256
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentcad.core import tools_xchange, usd_export
from agentcad.core.imports import (MAX_IMPORT_BYTES, SUPPORTED_EXTS,
                                   safe_import_name)
from agentcad.core.model import ValidationError
from agentcad.core.tools import build_registry
from agentcad.server import routes_import
from agentcad.server.app import create_app

from .conftest import BOX_SCRIPT, make_test_service
# The fixture authoring helpers and the file readers live with the slice suites
# that own them; importing beats a second, slightly different copy here.
from .test_interop_3mf import (ALUMINUM, STEEL, TWO_BOX, lib3mf_objects,
                               metadata_of, model_xml, object_colors)
from .test_interop_gltf import parse_glb
from .test_interop_import_kernel import (SCALED_OCCURRENCES, SCALED_PRODUCTS,
                                         make_assembly_step,
                                         make_scaled_assembly_step)
from .test_interop_pmi import BORED_BLOCK

REPO = Path(__file__).resolve().parents[1]
CHANGELOG = REPO / "docs" / "changelog"

#: AC1's part: two toleranced dims (one linear, one diameter), one datum and a
#: flatness frame — the PRD's "dims + datum + flatness FCF", authored the way a
#: user does it, through `set_part_pmi` — **plus** one perpendicularity frame
#: that references the datum.
#:
#: That last frame is not decoration. A datum nothing references is written to
#: the file (`#437 = DATUM('','',#4,.F.,'A')`, measured) but OCCT's *reader*
#: only materializes datum labels reachable from a geometric tolerance's datum
#: system, so `read_step_pmi` answers `datums: []` for it. Grading "every PMI
#: entity survives the round trip" therefore needs the datum to be referenced —
#: which is what a datum is for.
TOLERANCED_PMI = {
    "dims": [
        {"id": "h", "kind": "linear", "target": "height",
         "plus": 0.2, "minus": 0.05},
        {"id": "bore", "kind": "diameter", "target": 10.0,
         "plus": 0.05, "minus": 0.02},
    ],
    "datums": [{"id": "A", "face": "top"}],
    "fcf": [
        {"id": "flat", "type": "flatness", "tol_mm": 0.05, "datums": []},
        {"id": "perp", "type": "perpendicularity", "tol_mm": 0.1,
         "datums": ["A"]},
    ],
}

#: The seven occurrence names the multi-product fixture authors (3 products —
#: Bracket, Pin, Ball — 7 leaf occurrences, one nested level).
OCCURRENCES = {
    "bracket_1", "ball_1", "ball_2",
    "pinpair_1_pin_1", "pinpair_1_pin_2",
    "pinpair_2_pin_1", "pinpair_2_pin_2",
}


# ------------------------------------------------------------------ fixtures


@pytest.fixture
def svc(kernel, tmp_path):
    service = make_test_service(tmp_path / "projects", kernel)
    service.create_project("demo")
    service.create_part("demo", "box", script=BOX_SCRIPT)
    return service


@pytest.fixture
def registry(svc):
    """`build_registry` is what installs `tools_xchange` — the wrappers and the
    mutated schemas exist only after it has run, exactly as in the server."""
    return build_registry(svc)


@pytest.fixture(scope="module")
def assembly_step(kernel, tmp_path_factory):
    """The nested, coloured, 3-product / 7-occurrence STEP, authored in-suite
    through the real kernel (no binary blobs in the repo)."""
    return make_assembly_step(kernel, tmp_path_factory.mktemp("prd017_source"))


@pytest.fixture(scope="module")
def imported(kernel, tmp_path_factory, assembly_step):
    """One structured landing and one forced-flat landing of the SAME file.

    Module-scoped because the structured half registers three reference parts
    and builds all three: AC2 and AC6 read the same two results rather than
    paying for the import twice.
    """
    service = make_test_service(
        tmp_path_factory.mktemp("prd017_import") / "projects", kernel)
    service.create_project("demo")
    registry = build_registry(service)
    structured = registry.call("import_cad_file", {
        "project": "demo", "source": str(assembly_step), "structured": True})
    assert "error" not in structured, structured
    flat = registry.call("import_cad_file", {
        "project": "demo", "source": str(assembly_step), "structured": False,
        "part_id": "whole"})
    assert "error" not in flat, flat
    return service, structured, flat


def _by_id(rows):
    return {row["id"]: row for row in rows}


def sha(path) -> str:
    return sha256(Path(path).read_bytes()).hexdigest()


def two_boxes(service):
    service.set_assembly("demo", [
        {"id": "a", "part": "box", "position": [0, 0, 0]},
        {"id": "b", "part": "box", "position": [20, 0, 0],
         "rotation_deg": [0, 0, 90], "color": "#ff0000"},
    ])


# ============================================================ AC1


def test_ac1_a_toleranced_part_round_trips_through_ap242(svc, registry):
    """**AC1** — a part toleranced through `set_part_pmi` (two dims, a datum,
    a flatness frame) exports STEP AP242 and our own XCAF reader gets every
    entity back, in **millimetres**.

    The unit assertion is the load-bearing one: a dimension-less XCAF document
    mints METRE units for tolerance measures, and the resulting file is
    perfectly valid AP242 with every tolerance 1000× too large — a schema check
    would never catch it (`tests/test_interop_pmi.py` holds the trap down case
    by case). FreeCAD's viewer is the manual half; see the module docstring.
    """
    svc.create_part("demo", "block", script=BORED_BLOCK)
    assert "error" not in registry.call("set_part_pmi", {
        "project": "demo", "part_id": "block", "pmi": TOLERANCED_PMI})

    result = registry.call("export_part", {"project": "demo",
                                           "part_id": "block",
                                           "format": "step"})
    assert "error" not in result, result
    assert result["pmi_attached"] == {"dims": 2, "datums": 1, "fcf": 2}
    assert result["pmi_skipped"] == []
    # The schema is asserted on the BYTES: an AP214 fallback is a valid STEP
    # file with zero PMI in it.
    assert "AP242" in result["schema"]
    assert "AP242" in Path(result["path"]).read_text()[:8192]

    read = svc.kernel.request("read_step_pmi", {"path": result["path"]})
    assert "AP242" in read["schema"]

    dims = {entry["type"]: entry for entry in read["dims"]}
    assert set(dims) == {"size_thickness", "size_diameter"}
    height = dims["size_thickness"]
    assert height["value"] == pytest.approx(20.0, abs=1e-6)      # mm, not m
    assert (height["plus"], height["minus"]) == pytest.approx((0.2, 0.05))
    bore = dims["size_diameter"]
    assert bore["value"] == pytest.approx(10.0, abs=1e-6)
    assert (bore["plus"], bore["minus"]) == pytest.approx((0.05, 0.02))
    assert bore["targets"][0]["kind"] == "cylinder"

    # Datums come back by NAME — a two-datum frame reads back as three datum
    # labels, so a count would prove nothing.
    assert read["datums"] == ["A"]
    fcf = {entry["type"]: entry for entry in read["fcf"]}
    assert set(fcf) == {"flatness", "perpendicularity"}
    assert fcf["flatness"]["value"] == pytest.approx(0.05, abs=1e-9)
    assert fcf["perpendicularity"]["value"] == pytest.approx(0.1, abs=1e-9)
    # ...and the frame still carries its datum reference: without
    # `DatumObject.SetPosition` this list is silently empty and the frame is
    # dropped outright.
    assert fcf["perpendicularity"]["datums"] == ["A"]


# ============================================================ AC2


def test_ac2_a_multi_product_step_lands_deduplicated_placed_and_named(
        imported):
    """**AC2** — `structured: true` on the multi-product fixture yields exactly
    the deduplicated part set, every occurrence as a placed instance with its
    **composed** transform, and names derived from the product labels.

    The pose spot check is the spike's own case: `pin_2`'s local (30,0,0)
    through a sub-assembly placed at (0,50,10) and rotated 90° about Z lands at
    (0,80,10) with a two-axis orientation — intrinsic XYZ decomposes that
    Rz(90)·Ry(90) to [-90,0,90] where extrinsic would answer [0,90,90], so the
    rotation triple pins the house convention and not merely "it moved".
    """
    _service, result, _flat = imported

    # 3 unique products out of 7 occurrences: the dedup IS the criterion.
    parts = _by_id(result["parts"])
    assert sorted(parts) == ["ball", "bracket", "pin"]
    assert sorted(p["label"] for p in result["parts"]) == ["Ball", "Bracket",
                                                           "Pin"]
    for part in result["parts"]:
        assert part["kind"] == "reference"
        assert part["source"].endswith(".brep")
        assert part["status"]["state"] == "ok", part["status"]
        assert part["source_label"] in ("Ball", "Bracket", "Pin")

    instances = _by_id(result["instances"])
    assert set(instances) == OCCURRENCES
    assert sum(1 for i in result["instances"] if i["part"] == "pin") == 4
    assert result["tree"]["counts"] == {"products": 3, "occurrences": 7}

    assert instances["pinpair_2_pin_2"]["position"] == pytest.approx(
        [0, 80, 10], abs=1e-6)
    assert instances["pinpair_2_pin_2"]["rotation_deg"] == pytest.approx(
        [-90, 0, 90], abs=1e-6)
    assert instances["bracket_1"]["position"] == pytest.approx([0, 0, 0])
    assert instances["ball_2"]["position"] == pytest.approx([-5, -5, 40])
    # ball_2 carries the file's per-occurrence colour override; ball_1 the
    # product colour.
    assert instances["ball_1"]["color"] != instances["ball_2"]["color"]


def test_ac2_holds_at_scale_fourteen_products_and_fortyone_occurrences(
        kernel, tmp_path):
    """**AC2** at an order of magnitude past the 3/7 fixture.

    The small assembly cannot express the failures that only appear with
    breadth: a dedup that is really "one part per occurrence" survives 7
    occurrences of 3 products by luck far more easily than 41 of 14, and the
    path-qualified naming has to keep 15 same-named components (`c0`..`c4`
    under three clusters) apart rather than five.

    Measured at ~0.3 s for the landing, so it is an ordinary test, not a slow
    one — the 14 reference builds are the cost and they are cheap boxes.
    """
    source = make_scaled_assembly_step(kernel, tmp_path)
    service = make_test_service(tmp_path / "projects", kernel)
    service.create_project("demo")
    registry = build_registry(service)
    result = registry.call("import_cad_file", {"project": "demo",
                                               "source": str(source)})
    assert "error" not in result, result

    assert result["tree"]["counts"] == {"products": SCALED_PRODUCTS,
                                        "occurrences": SCALED_OCCURRENCES}
    # dedup: 41 occurrences, 14 parts, and every instance points at one of them
    parts = _by_id(result["parts"])
    assert len(parts) == SCALED_PRODUCTS
    assert sorted(parts) == [f"part{i:02d}" for i in range(SCALED_PRODUCTS)]
    instances = _by_id(result["instances"])
    assert len(instances) == SCALED_OCCURRENCES
    assert {i["part"] for i in result["instances"]} <= set(parts)
    # the three clusters share five products: 15 occurrences, 5 parts
    clustered = [i for i in result["instances"] if i["id"].startswith("cluster_")]
    assert len(clustered) == 15
    assert len({i["part"] for i in clustered}) == 5
    # ...and 15 same-named components stayed distinct (path qualification)
    assert len(instances) == len(result["instances"])

    # Spot-checked pose: `c2`'s local (20,0,0) through `cluster_2`, which sits
    # at (0,20,0) rotated 90 deg about Z — Rz(90)·(20,0,0) + (0,20,0).
    placed = instances["cluster_2_c2"]
    assert placed["position"] == pytest.approx([0, 40, 0], abs=1e-6)
    assert placed["rotation_deg"] == pytest.approx([0, 0, 90], abs=1e-6)
    assert placed["part"] == "part02"

    # and the landed project is a real assembly, every member built
    assembly = service.get_assembly("demo")
    assert len(assembly["instances"]) == SCALED_OCCURRENCES
    assert all(i["state"] == "ok" for i in assembly["instances"])


def test_ac2_structured_false_is_still_one_blob(imported):
    """**AC2**, the other half: `structured: false` forces today's behaviour —
    one reference part for the whole file, no instances, no tree."""
    _service, _structured, flat = imported
    assert "parts" not in flat and "tree" not in flat
    assert flat["part"]["id"] == "whole"
    assert flat["part"]["kind"] == "reference"
    assert flat["part"]["source"] == "assembly.step"
    assert flat["fidelity"]["structure"] == "flat"


# ============================================================ AC3


def _accessor_bytes(document: dict, binary: bytes, index: int) -> bytes:
    accessor = document["accessors"][index]
    view = document["bufferViews"][accessor["bufferView"]]
    start = view["byteOffset"] + accessor.get("byteOffset", 0)
    sizes = {5126: 4, 5125: 4, 5123: 2, 5121: 1}
    counts = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}
    span = (accessor["count"] * counts[accessor["type"]]
            * sizes[accessor["componentType"]])
    return binary[start:start + span]


def _validate_gltf(document: dict, binary: bytes) -> None:
    """Structural glTF 2.0 validation, written independently of the writer.

    Not a schema validator (there is no dependency for one, by design): the
    checks are the ones that make a file *loadable* — the asset version, every
    index in range, and every accessor's byte range inside its buffer view
    inside the buffer. A viewer rejects the file if any of them is wrong.

    The two that read the BUFFER and not merely the JSON are the ones worth
    naming, because they are the two a viewer actually crashes on and the two
    an all-JSON check cannot see:

    * every triangle index is decoded and compared against the vertex count of
      the primitive's own POSITION accessor (an out-of-range index is an
      out-of-bounds read in the loader);
    * every POSITION accessor's declared ``min``/``max`` is checked against the
      float32 values in the buffer — the JSON is rounded to six decimals and
      the buffer is not, so a naive round can put the declared bound *inside*
      the data.
    """
    assert document["asset"]["version"] == "2.0"
    assert document["asset"]["extras"] == {"source_up_axis": "+Z",
                                           "converted_to": "+Y"}
    buffers = document["buffers"]
    assert len(buffers) == 1 and buffers[0]["byteLength"] <= len(binary)

    for view in document["bufferViews"]:
        assert view["buffer"] == 0
        assert view["byteOffset"] % 4 == 0, "buffer views stay 4-byte aligned"
        assert view["byteOffset"] + view["byteLength"] <= buffers[0]["byteLength"]

    sizes = {5126: 4, 5125: 4, 5123: 2, 5121: 1}
    counts = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}
    for accessor in document["accessors"]:
        view = document["bufferViews"][accessor["bufferView"]]
        span = (accessor["count"] * counts[accessor["type"]]
                * sizes[accessor["componentType"]])
        assert accessor.get("byteOffset", 0) + span <= view["byteLength"]

    assert document["scenes"][document["scene"]]["nodes"] == [0]
    for node in document["nodes"]:
        for child in node.get("children", []):
            assert 0 <= child < len(document["nodes"])
        if "mesh" in node:
            assert 0 <= node["mesh"] < len(document["meshes"])
    for mesh in document["meshes"]:
        for primitive in mesh["primitives"]:
            assert primitive["mode"] == 4                      # TRIANGLES
            assert 0 <= primitive["material"] < len(document["materials"])
            for index in (*primitive["attributes"].values(),
                          primitive["indices"]):
                assert 0 <= index < len(document["accessors"])

            position = document["accessors"][primitive["attributes"]["POSITION"]]
            vertices = position["count"]

            # --- indices, decoded from the buffer
            indices_accessor = document["accessors"][primitive["indices"]]
            assert indices_accessor["componentType"] == 5125   # UNSIGNED_INT
            raw = _accessor_bytes(document, binary, primitive["indices"])
            assert len(raw) == indices_accessor["count"] * 4
            decoded = struct.unpack(f"<{indices_accessor['count']}I", raw)
            assert indices_accessor["count"] % 3 == 0, "triangles"
            assert decoded, "a primitive with no indices draws nothing"
            assert max(decoded) < vertices, (
                f"index {max(decoded)} >= vertex count {vertices}")

            # --- min/max, checked against the float32 values they claim to
            # bound (component-wise, over every vertex)
            points = struct.unpack(
                f"<{vertices * 3}f",
                _accessor_bytes(document, binary,
                                primitive["attributes"]["POSITION"]))
            for axis in range(3):
                column = points[axis::3]
                assert position["min"][axis] <= min(column), (
                    "declared min is above the smallest value in the buffer")
                assert position["max"][axis] >= max(column), (
                    "declared max is below the largest value in the buffer")


def test_ac3_an_assembly_glb_is_structurally_valid_and_byte_stable(svc,
                                                                   registry):
    """**AC3** — the placed assembly exports a GLB that passes structural
    validation with per-instance colours and poses matching `get_assembly`, and
    two exports of one state are **byte-identical**.

    The Three.js half is evidence-graded, not stubbed: slice 6 drove the
    vendored loader in the installed Chrome with Playwright (changelog 0316).
    """
    two_boxes(svc)
    assembly = svc.get_assembly("demo")
    first = registry.call("export_assembly", {"project": "demo",
                                              "format": "glb"})
    assert "error" not in first, first
    # Read the BYTES now: both exports write `exports/assembly.glb`, so a sha
    # taken after the second export would be the second file's hash compared
    # with itself — a determinism assertion that cannot fail.
    first_bytes = Path(first["path"]).read_bytes()
    document, binary = parse_glb(first_bytes)
    _validate_gltf(document, binary)

    # One part, two instances: the mesh data is emitted ONCE (8 screws are 1
    # mesh and 8 nodes) and the up-axis conversion is one root node.
    assert len(document["nodes"]) == 3
    assert len(document["bufferViews"]) == 3
    root, *placed = document["nodes"]
    assert root["children"] == [1, 2]
    assert [node["name"] for node in placed] == ["a", "b"]

    # Poses match `get_assembly`'s own numbers, in the authored Z-up frame
    # under the root node.
    poses = {entry["id"]: entry for entry in assembly["instances"]}
    for node in placed:
        assert node["translation"] == pytest.approx(
            poses[node["name"]]["position"])
    assert placed[1]["rotation"] == pytest.approx(
        [0.0, 0.0, 0.707107, 0.707107], abs=1e-6)     # 90° about Z

    # Instance `b`'s author-set #ff0000 reaches the file as LINEAR — writing
    # sRGB straight through is the classic silent-darkening bug.
    factors = [m["pbrMetallicRoughness"]["baseColorFactor"]
               for m in document["materials"]]
    assert [1.0, 0.0, 0.0, 1.0] in factors
    assert len(document["materials"]) == 2

    second = registry.call("export_assembly", {"project": "demo",
                                               "format": "glb"})
    assert second["path"] == first["path"], "same state, same target path"
    assert sha256(first_bytes).hexdigest() == sha(second["path"])


# ============================================================ AC4


def test_ac4_a_3mf_is_conformant_millimetre_and_carries_metadata_and_colours(
        svc, registry):
    """**AC4** — the 3MF is a conformant OPC package that declares millimetres
    and carries Title + the BOM part number, and a multi-solid part carries
    per-solid colours that the Consortium's own library reads back.

    PrusaSlicer opening it is the per-release manual check (module docstring);
    what is machine-checkable is that the file is a *3MF*, not merely something
    our own reader likes — hence the OPC parts, the core-namespace root, and
    the lib3mf re-read.
    """
    svc.create_part("demo", "duo", script=TWO_BOX)
    assert "error" not in registry.call("set_bom_fields", {
        "project": "demo", "part_id": "duo", "part_number": "AC-0017"})
    assert "error" not in registry.call("set_solid_materials", {
        "project": "demo", "part_id": "duo",
        "materials": {"body": "al6061", "lid": "steel_a36"}})

    result = registry.call("export_part", {"project": "demo",
                                           "part_id": "duo", "format": "3mf"})
    assert "error" not in result, result
    path = Path(result["path"])

    with zipfile.ZipFile(path) as package:
        assert {"3D/3dmodel.model", "[Content_Types].xml",
                "_rels/.rels"} <= set(package.namelist())
        assert "3dmanufacturing-3dmodel+xml" in \
            package.read("[Content_Types].xml").decode()

    root = model_xml(path)
    assert root.tag.endswith("}model")
    assert root.get("unit") == "millimeter"          # FR4, in the file

    metadata = metadata_of(root)
    assert metadata["Title"] == "duo"
    assert metadata["Designer"] == "AgentCAD"
    assert metadata["PartNumber"] == "AC-0017"       # from `set_bom_fields`
    assert result["metadata_stamped"] == ["Title", "Designer", "PartNumber"]

    # Per-solid colours, resolved through the pid/pindex wiring rather than
    # merely present somewhere in the package.
    assert result["colors"] == "per_solid"
    assert object_colors(root) == {"body": ALUMINUM, "lid": STEEL}
    read_back = lib3mf_objects(path)
    assert [obj["name"] for obj in read_back] == ["body", "lid"]
    assert [obj["color"] for obj in read_back] == [ALUMINUM, STEEL]
    assert all(obj["part_number"] == "AC-0017" for obj in read_back)


# ============================================================ AC5


def _format_enums(registry) -> dict:
    return {name: registry.get(name).input_schema["properties"]["format"]["enum"]
            for name in ("export_part", "export_assembly")}


@pytest.mark.parametrize("available", [True, False])
def test_ac5_usd_is_offered_exactly_when_it_can_run(svc, monkeypatch,
                                                    available):
    """**AC5** — `usd` is in the format enums iff the extra is installed, over
    the registry *and* over `GET /api/tools`; without it a `usd` request is the
    ordinary unknown-format `validation_error`, not a special "install this"
    path (the FEM gating rule: an agent never sees a format that cannot run).
    """
    monkeypatch.setattr(usd_export, "usd_available", lambda: available)
    registry = build_registry(svc)

    base = list(tools_xchange.BASE_PART_FORMATS)
    assert _format_enums(registry)["export_part"] == (
        base + ["usd"] if available else base)
    assert ("usd" in registry.get("export_assembly").description) is available

    client = TestClient(create_app(svc, registry,
                                   extra_allowed_hosts={"testserver"}),
                        base_url="http://127.0.0.1")
    tools = {t["name"]: t for t in client.get("/api/tools").json()["tools"]}
    for name in ("export_part", "export_assembly"):
        enum = tools[name]["input_schema"]["properties"]["format"]["enum"]
        assert ("usd" in enum) is available, name

    if not available:
        with pytest.raises(ValidationError) as exc:
            svc.export_part("demo", "box", "usd")
        assert exc.value.details["known"] == base


def test_ac5_a_stage_exports_and_reopens(svc, registry):
    """**AC5**, the positive half: with `pxr` present the export writes a
    `.usda` stage that re-opens and composes — declared millimetres and Z-up,
    one prototype per unique part, one prim per instance."""
    pytest.importorskip("pxr")
    from pxr import Usd, UsdGeom

    two_boxes(svc)
    result = registry.call("export_assembly", {"project": "demo",
                                               "format": "usd"})
    assert "error" not in result, result
    out = Path(result["path"])
    assert out.name == "assembly.usda"

    stage = Usd.Stage.Open(str(out))
    assert UsdGeom.GetStageMetersPerUnit(stage) == pytest.approx(0.001)
    assert UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.z
    meshes = [p for p in stage.Traverse() if p.IsA(UsdGeom.Mesh)]
    assert sorted(p.GetName() for p in meshes) == ["a", "b"]
    # Two instances of one part: the points are authored once, in an abstract
    # library a traversal (and a renderer) never sees.
    library = stage.GetPrimAtPath("/AgentCAD/Meshes")
    assert library.IsAbstract()
    assert len([p for p in library.GetAllChildren()
                if p.IsA(UsdGeom.Mesh)]) == 1


# ============================================================ AC6


def test_ac6_every_interop_result_reports_fidelity(svc, registry, imported):
    """**AC6** — every interop result carries a `fidelity` block, on exports
    and imports alike, and `parametric: "none"` is on all of them: no neutral
    format carries parametric intent, and the product says so in the result.

    An axis the format cannot express is **absent** rather than `"none"` —
    "STL has no PMI" is not news, "your STEP dropped a datum" is.
    """
    two_boxes(svc)
    for fmt in tools_xchange.part_formats():
        fidelity = svc.export_part("demo", "box", fmt)["fidelity"]
        assert fidelity["parametric"] == "none", fmt
        assert fidelity["geometry"] == ("brep" if fmt == "step" else "mesh")
        assert ("pmi" in fidelity) is (fmt == "step"), fmt
    for fmt in tools_xchange.assembly_formats():
        assert svc.export_assembly("demo", fmt)["fidelity"][
            "parametric"] == "none", fmt

    # glTF and 3MF name what they *did* carry, not only what they dropped.
    assert svc.export_part("demo", "box", "gltf")["fidelity"] == {
        "geometry": "mesh", "colors": "per_instance", "parametric": "none"}
    assert svc.export_part("demo", "box", "3mf")["fidelity"] == {
        "geometry": "mesh", "colors": "none", "metadata": "attached",
        "parametric": "none"}
    # A structured STEP assembly reports its structure and per-instance
    # colours, and carries no `pmi` axis at all (the AP242 PMI writer is the
    # single-part path; `pmi: "none"` here would read as "yours was dropped").
    structured_step = svc.export_assembly("demo", "step", structured=True)
    assert structured_step["fidelity"] == {
        "geometry": "brep", "structure": "tree", "colors": "per_instance",
        "parametric": "none"}

    # Imports: the tree read and the single blob, both stating the same axes.
    _service, structured, flat = imported
    assert structured["fidelity"] == {
        "geometry": "brep", "structure": "tree", "colors": "per_instance",
        "pmi": "not_read", "parametric": "none"}
    assert flat["fidelity"] == {
        "geometry": "brep", "structure": "flat", "colors": "none",
        "pmi": "not_read", "parametric": "none"}


def test_ac6_an_unmappable_pmi_entry_is_reported_not_dropped(svc, registry):
    """**AC6**'s named path (FR3): an entry the writer cannot map is listed in
    `fidelity.pmi_skipped` with a reason, the rest of the PMI still attaches,
    and the export still succeeds.

    A diameter dim on a cube is the honest instance of it — there is no
    cylindrical face for it to land on, and the alternative to reporting that
    is a file whose caller believes their bore tolerance travelled.
    """
    pmi = {
        "dims": [
            {"id": "h", "kind": "linear", "target": "height",
             "plus": 0.1, "minus": 0.1},
            {"id": "bore", "kind": "diameter", "target": 10.0,
             "plus": 0.05, "minus": 0.05},
        ],
        "datums": [{"id": "A", "face": "top"}],
        "fcf": [{"id": "flat", "type": "flatness", "tol_mm": 0.02,
                 "datums": []}],
    }
    assert "error" not in registry.call("set_part_pmi", {
        "project": "demo", "part_id": "box", "pmi": pmi})

    result = registry.call("export_part", {"project": "demo",
                                           "part_id": "box", "format": "step"})
    assert "error" not in result, result
    fidelity = result["fidelity"]
    assert fidelity["pmi"] == "attached"
    assert [row["id"] for row in fidelity["pmi_skipped"]] == ["bore"]
    assert fidelity["pmi_skipped"][0]["reason"].startswith("no_cylindrical_face")
    assert result["pmi_attached"] == {"dims": 1, "datums": 1, "fcf": 1}
    assert Path(result["path"]).is_file()

    # `pmi: false` is the opt-out, and it says so rather than reporting "none".
    opted = registry.call("export_part", {"project": "demo", "part_id": "box",
                                          "format": "step", "pmi": False})
    assert opted["fidelity"] == {"geometry": "brep", "pmi": "opted_out",
                                 "parametric": "none"}


# ============================================================ AC7


def test_ac7_the_legacy_import_and_export_surfaces_are_unchanged(svc, registry,
                                                                 imported):
    """**AC7** — existing flat imports and STEP/STL exports behave exactly as
    they did; `fidelity` is the only key that appears.

    Thin on purpose: the real evidence for "nothing regressed" is the full
    suite (cited by `test_ac7_the_full_suite_count_is_cited` below). What is
    worth pinning here is the *shape* of the two surfaces an existing caller
    reads — the flat import's result keys and a delegated export's bytes.
    """
    _service, _structured, flat = imported
    assert set(flat) == {"part", "imported", "warnings", "fidelity"}
    assert set(flat["imported"]) == {"source", "n_solids", "is_valid",
                                     "mesh_only", "warnings"}

    for fmt in ("step", "stl"):
        plain = type(svc).export_part(svc, "demo", "box", fmt)   # pre-wrap
        wrapped = svc.export_part("demo", "box", fmt)
        assert set(wrapped) == set(plain) | {"fidelity"}
        assert wrapped["path"] == plain["path"]
    # STL is byte-comparable (a STEP header carries a write timestamp), so the
    # delegation is asserted on the bytes and not merely on the shape.
    assert sha(svc.export_part("demo", "box", "stl")["path"]) == sha(
        type(svc).export_part(svc, "demo", "box", "stl")["path"])

    two_boxes(svc)
    for fmt in ("step", "stl"):
        result = svc.export_assembly("demo", fmt)
        assert Path(result["path"]).name == f"assembly.{fmt}"
        assert result["size_bytes"] > 100


def test_ac7_the_import_guards_still_fire(svc, registry, monkeypatch):
    """**AC7** — the 100 MB cap and the extension gate are untouched by the
    structured path: both refuse before anything is written."""
    assert MAX_IMPORT_BYTES == 100 * 1024 * 1024
    # The CAD formats stay supported; the set may grow for other intake paths
    # (PRD-018 added image/PDF), but an unsupported extension still refuses.
    assert {".step", ".stp", ".brep", ".stl"} <= SUPPORTED_EXTS
    with pytest.raises(ValidationError):
        safe_import_name("model.obj")

    client = TestClient(create_app(svc, registry,
                                   extra_allowed_hosts={"testserver"}),
                        base_url="http://127.0.0.1")
    assert client.post("/api/projects/demo/imports?filename=model.obj",
                       content=b"solid\n").status_code == 422
    # The size guard, exercised against a lowered ceiling rather than 100 MB of
    # RAM: the route reads its limit from its own module global.
    monkeypatch.setattr(routes_import, "MAX_IMPORT_BYTES", 16)
    response = client.post("/api/projects/demo/imports?filename=big.step",
                           content=b"x" * 64)
    assert response.status_code == 422
    assert "100 MB" in response.json()["error"]["message"]
    assert list(svc.store.imports_dir("demo").iterdir()) == []


def test_ac7_the_full_suite_count_is_cited():
    """**AC7** — "full suite green" is a claim about a *run*; the evidence is a
    `make test` count on the record in the newest changelog entry (the PRD-004
    AC10 / PRD-012 AC8 / PRD-026 AC7 precedent). Recomputing the number here
    would mean running the full suite from inside itself, and `--collect-only`
    counts cases, not what `make test` reports.
    """
    latest = max(CHANGELOG.glob("0[0-9][0-9][0-9]-*.md"))
    text = latest.read_text(encoding="utf-8")
    assert "make test" in text, \
        f"{latest.name} is the newest changelog entry and cites no `make test`"
    assert re.search(r"\b\d{3,6}\s+passed\b", text.replace(",", "")), \
        f"{latest.name} does not cite a `make test` suite count"
