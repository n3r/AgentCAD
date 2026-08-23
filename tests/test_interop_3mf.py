"""PRD-017 slice 5 — 3MF v2: per-solid names/colours + model metadata (FR4/FR5).

The machine half of AC4. Three levels of evidence, deliberately:

* **OPC/XML conformance** — unzip the package and assert the 3MF core spec's
  own structure (the three parts, `unit="millimeter"`, `<metadata>` elements,
  a `<basematerials>` group per coloured object with the object's `pid`/
  `pindex` pointing at it). A file that only *our* reader likes is not a 3MF.
* **lib3mf re-read** — the venv's own Consortium library (the one build123d's
  `Mesher` is a thin wrapper over) reads the file back and the colours come out
  as authored. PrusaSlicer opening it stays a manual per-release check.
* **the legacy path is untouched** — the worker's plain `export` handler still
  writes the nameless, colourless 3MF it always did; slice 5 added a handler,
  it replaced none.

Determinism is asserted **modulo UUIDs**: lib3mf mints a fresh `p:UUID` per
object per write (spike D.2), so byte equality is not available and never will
be — what is asserted is that everything else is stable, which is what makes
`CreationDate` a *version* date and not a wall clock.
"""

from __future__ import annotations

import os
import shutil
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

from agentcad.core.interop_colors import color_for
from agentcad.core.materials import MATERIALS
from agentcad.core.model import ValidationError
from agentcad.core.tools import build_registry
from agentcad.core.tools_drawing import _drawing_version

from .conftest import BOX_SCRIPT, make_test_service

CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
PRODUCTION_NS = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
NS = {"m": CORE_NS}

#: Two disjoint solids with declared labels — the `solid_materials` vocabulary
#: (`get_metrics(...).solids[*].label`) is the one `solid_colors` is keyed by.
TWO_BOX = '''\
import build123d as b3d
PARAMS = {"size": {"default": 10.0, "min": 5.0, "max": 20.0, "unit": "mm"}}
SOLID_LABELS = ["body", "lid"]
def build(p):
    a = b3d.Box(p.size, p.size, p.size)
    b = b3d.Box(p.size, p.size, p.size).moved(b3d.Location((p.size * 2, 0, 0)))
    return b3d.Compound(children=[a, b])
'''

ALUMINUM = color_for(None, solid_material="al6061")
STEEL = color_for(None, solid_material="steel_a36")


@pytest.fixture
def svc(kernel, tmp_path):
    service = make_test_service(tmp_path / "projects", kernel)
    service.create_project("demo")
    service.create_part("demo", "box", script=BOX_SCRIPT)
    service.create_part("demo", "duo", script=TWO_BOX)
    return service


@pytest.fixture
def registry(svc):
    return build_registry(svc)


# ------------------------------------------------------------------ helpers


def model_xml(path) -> ET.Element:
    with zipfile.ZipFile(path) as package:
        return ET.fromstring(package.read("3D/3dmodel.model"))


def mesh_objects(root: ET.Element) -> list[dict]:
    """The model's mesh objects. build123d adds a `<components>` object beside
    every mesh (its own "not sure this is required"), so an object element is
    not by itself an object we wrote."""
    return [dict(obj.attrib) for obj in root.findall(".//m:object", NS)
            if obj.find("m:mesh", NS) is not None]


def base_materials(root: ET.Element) -> dict[str, list[str]]:
    """``{group id: [displaycolor, ...]}``."""
    return {group.get("id"): [base.get("displaycolor")
                              for base in group.findall("m:base", NS)]
            for group in root.findall(".//m:basematerials", NS)}


def object_colors(root: ET.Element) -> dict[str, str | None]:
    """``{object name: "#rrggbb"}`` resolved through the pid/pindex wiring —
    the assertion that the colour is *reachable*, not merely present."""
    groups = base_materials(root)
    out: dict[str, str | None] = {}
    for obj in mesh_objects(root):
        pid, pindex = obj.get("pid"), obj.get("pindex")
        if pid is None or pindex is None:
            out[obj.get("name")] = None
            continue
        display = groups[pid][int(pindex)]
        out[obj.get("name")] = "#" + display[1:7].lower()
    return out


def metadata_of(root: ET.Element) -> dict[str, str]:
    """``{name: value}``; the custom-namespace PartNumber's prefix is stripped
    (lib3mf writes it as ``customXMLNS0:PartNumber``)."""
    return {(md.get("name") or "").split(":")[-1]: md.text
            for md in root.findall("m:metadata", NS)}


def lib3mf_objects(path) -> list[dict]:
    """Re-read through the venv's own lib3mf — the same library build123d's
    `Mesher` wraps, driven directly so the assertion is about the FILE."""
    from lib3mf import Lib3MF

    wrapper = Lib3MF.Wrapper(
        os.path.join(os.path.dirname(Lib3MF.__file__), "lib3mf"))
    model = wrapper.CreateModel()
    model.QueryReader("3mf").ReadFromFile(str(path))

    palette: dict[int, dict[int, str]] = {}
    groups = model.GetBaseMaterialGroups()
    while groups.MoveNext():
        group = groups.GetCurrentBaseMaterialGroup()
        entries = {}
        for property_id in group.GetAllPropertyIDs():
            r, g, b, _a = wrapper.ColorToFloatRGBA(
                group.GetDisplayColor(property_id))
            entries[property_id] = "#%02x%02x%02x" % tuple(
                round(c * 255.0) for c in (r, g, b))
        palette[group.GetResourceID()] = entries

    out = []
    meshes = model.GetMeshObjects()
    while meshes.MoveNext():
        mesh = meshes.GetCurrentMeshObject()
        resource_id, property_id, has_property = mesh.GetObjectLevelProperty()
        out.append({
            "name": mesh.GetName(),
            "part_number": mesh.GetPartNumber(),
            "triangles": mesh.GetTriangleCount(),
            "color": (palette.get(resource_id, {}).get(property_id)
                      if has_property else None),
        })
    return out


def stable_xml(path) -> bytes:
    """The model XML with every production-extension UUID removed — the only
    part of a 3MF that CAN be compared across two writes."""
    root = model_xml(path)
    prefix = f"{{{PRODUCTION_NS}}}"
    for element in root.iter():
        for key in [k for k in element.attrib if k.startswith(prefix)]:
            del element.attrib[key]
    return ET.tostring(root)


# ------------------------------------------------------- OPC / XML conformance


def test_the_package_is_a_conformant_opc_container(svc, registry):
    result = svc.export_part("demo", "duo", "3mf")
    path = Path(result["path"])
    assert path.name == "duo.3mf"
    with zipfile.ZipFile(path) as package:
        names = set(package.namelist())
        assert {"3D/3dmodel.model", "[Content_Types].xml",
                "_rels/.rels"} <= names
        content_types = package.read("[Content_Types].xml").decode()
        rels = package.read("_rels/.rels").decode()
    assert "3dmanufacturing-3dmodel+xml" in content_types
    assert "/3D/3dmodel.model" in rels
    root = model_xml(path)
    assert root.tag == f"{{{CORE_NS}}}model"
    # FR4: millimetres, explicitly, in the file — not by convention.
    assert root.get("unit") == "millimeter"


def test_every_solid_is_its_own_named_object(svc, registry):
    """The spike's D.1 trap, held down: `add_shape(Part)` drops names and
    colours, so the handler decomposes to solids first. Two solids in, two
    named objects out — with the script's own SOLID_LABELS as the names."""
    result = svc.export_part("demo", "duo", "3mf")
    assert result["objects"] == 2
    objects = mesh_objects(model_xml(result["path"]))
    assert [obj.get("name") for obj in objects] == ["body", "lid"]
    assert all(obj.get("type") == "model" for obj in objects)
    # every object we wrote is also built (a resource nobody builds is invisible)
    root = model_xml(result["path"])
    built = {item.get("objectid")
             for item in root.findall(".//m:build/m:item", NS)}
    assert built == {obj["id"] for obj in objects}


def test_a_single_solid_part_is_named_after_the_part(svc, registry):
    result = svc.export_part("demo", "box", "3mf")
    assert result["objects"] == 1
    assert [obj.get("name") for obj in mesh_objects(model_xml(result["path"]))
            ] == ["box"]


# ------------------------------------------------------------------ colours


def test_per_solid_materials_become_per_object_basematerials(svc, registry):
    registry.call("set_solid_materials", {
        "project": "demo", "part_id": "duo",
        "materials": {"body": "al6061", "lid": "steel_a36"}})
    result = svc.export_part("demo", "duo", "3mf")
    assert result["colors"] == "per_solid"
    root = model_xml(result["path"])
    # the wiring, not just the presence: each object's pid/pindex resolves to
    # ITS own material colour.
    assert object_colors(root) == {"body": ALUMINUM, "lid": STEEL}
    assert ALUMINUM != STEEL
    for obj in mesh_objects(root):
        assert obj["pid"] in base_materials(root)


def test_the_colours_round_trip_through_lib3mf(svc, registry):
    registry.call("set_solid_materials", {
        "project": "demo", "part_id": "duo",
        "materials": {"body": "al6061", "lid": "steel_a36"}})
    path = svc.export_part("demo", "duo", "3mf")["path"]
    read_back = lib3mf_objects(path)
    assert [obj["name"] for obj in read_back] == ["body", "lid"]
    assert [obj["color"] for obj in read_back] == [ALUMINUM, STEEL]
    assert all(obj["triangles"] > 0 for obj in read_back)


def test_a_solid_with_no_material_takes_the_parts_own_colour(svc, registry):
    """A mixed part must not print half-uncoloured: solids the author left
    alone fall back to the part's own material colour."""
    registry.call("set_solid_materials", {
        "project": "demo", "part_id": "duo", "materials": {"lid": "steel_a36"}})
    root = model_xml(svc.export_part("demo", "duo", "3mf")["path"])
    assert object_colors(root) == {"body": ALUMINUM, "lid": STEEL}


def test_a_part_with_no_per_solid_materials_is_written_uncoloured(svc,
                                                                  registry):
    """`colors: "none"` is a real answer, not a failure: a uniform part carries
    no colour claim, so the slicer's own default applies — and the metadata is
    stamped all the same."""
    result = svc.export_part("demo", "box", "3mf")
    assert result["colors"] == "none"
    root = model_xml(result["path"])
    assert base_materials(root) == {}
    assert mesh_objects(root)[0].get("pid") is None
    assert metadata_of(root)["Title"] == "box"
    assert result["fidelity"] == {"geometry": "mesh", "colors": "none",
                                  "metadata": "attached", "parametric": "none"}


def test_an_index_keyed_colour_matches_like_a_density_does(svc, registry):
    registry.call("set_solid_materials", {
        "project": "demo", "part_id": "duo", "materials": {"1": "steel_a36"}})
    root = model_xml(svc.export_part("demo", "duo", "3mf")["path"])
    assert object_colors(root)["lid"] == STEEL


# ----------------------------------------------------------------- metadata


def test_metadata_is_stamped_from_the_parts_own_identity(svc, registry):
    registry.call("set_bom_fields", {"project": "demo", "part_id": "duo",
                                     "part_number": "AC-0042"})
    result = svc.export_part("demo", "duo", "3mf")
    assert result["metadata_stamped"] == ["Title", "Designer", "PartNumber"]
    metadata = metadata_of(model_xml(result["path"]))
    assert metadata["Title"] == "duo"
    assert metadata["Designer"] == "AgentCAD"
    # FR5: the BOM part number reaches BOTH the model metadata and the 3MF
    # core `partnumber=` attribute of every object.
    assert metadata["PartNumber"] == "AC-0042"
    assert {obj.get("partnumber")
            for obj in mesh_objects(model_xml(result["path"]))} == {"AC-0042"}
    assert result["fidelity"]["metadata"] == "attached"


def test_explicit_metadata_overrides_the_derived_defaults(svc, registry):
    registry.call("set_bom_fields", {"project": "demo", "part_id": "box",
                                     "part_number": "AC-1"})
    result = registry.call("export_part", {
        "project": "demo", "part_id": "box", "format": "3mf",
        "metadata": {"title": "Locator Bracket", "designer": "N. Fedorov",
                     "description": "spec §4", "creation_date": "2026-08-01"}})
    assert "error" not in result, result
    metadata = metadata_of(model_xml(result["path"]))
    assert metadata == {"Title": "Locator Bracket", "Designer": "N. Fedorov",
                        "Description": "spec §4", "CreationDate": "2026-08-01",
                        "PartNumber": "AC-1"}


def test_the_3mf_metadata_spellings_are_accepted_too(svc, registry):
    result = registry.call("export_part", {
        "project": "demo", "part_id": "box", "format": "3mf",
        "metadata": {"Title": "Cased", "PartNumber": "AC-9"}})
    assert "error" not in result, result
    metadata = metadata_of(model_xml(result["path"]))
    assert metadata["Title"] == "Cased" and metadata["PartNumber"] == "AC-9"


def test_an_unknown_metadata_key_is_refused_by_the_tool_layer(svc, registry):
    with pytest.raises(ValidationError) as exc:
        svc.export_part("demo", "box", "3mf", metadata={"author": "nobody"})
    assert exc.value.details["known"] == [
        "title", "designer", "description", "creation_date", "part_number"]
    payload = registry.call("export_part", {
        "project": "demo", "part_id": "box", "format": "3mf",
        "metadata": {"author": "nobody"}})
    assert payload["error"]["type"] == "validation_error"


def test_no_repo_means_no_creation_date_rather_than_a_placeholder(svc,
                                                                  registry):
    """`_drawing_version` answers `"-"` with no history; `"-"` is not a date,
    so the field is omitted instead of being stamped with a lie."""
    assert _drawing_version(svc, "demo")["date"] == "-"
    result = svc.export_part("demo", "box", "3mf")
    assert "CreationDate" not in result["metadata_stamped"]
    assert "CreationDate" not in metadata_of(model_xml(result["path"]))


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_the_creation_date_is_the_version_date_never_a_wall_clock(svc,
                                                                 registry):
    """AC4's honesty clause: the date in the file is PRD-014's resolved version
    date — the same one a drawing's title block prints for this state."""
    svc.history.snapshot(svc.store.path_of("demo"), "init")
    version = _drawing_version(svc, "demo")
    assert version["date"] != "-"
    result = svc.export_part("demo", "box", "3mf")
    assert result["metadata_stamped"] == ["Title", "Designer", "CreationDate"]
    assert metadata_of(model_xml(result["path"]))["CreationDate"] == \
        version["date"]


# -------------------------------------------------------------- determinism


def test_two_exports_of_one_state_differ_only_in_uuids(svc, registry):
    """Spec §4: 3MF is NOT byte-deterministic — lib3mf mints ~9 fresh
    `p:UUID`s per write. Everything else must still be stable, or a
    `CreationDate` would not be the only moving part."""
    registry.call("set_solid_materials", {
        "project": "demo", "part_id": "duo", "materials": {"body": "al6061"}})
    first = Path(svc.export_part("demo", "duo", "3mf")["path"])
    kept = first.with_name("first.3mf")
    shutil.copyfile(first, kept)
    second = Path(svc.export_part("demo", "duo", "3mf")["path"])

    assert stable_xml(kept) == stable_xml(second)
    uuids = {value for element in model_xml(second).iter()
             for key, value in element.attrib.items()
             if key.startswith(f"{{{PRODUCTION_NS}}}")}
    assert len(uuids) >= 4
    assert kept.read_bytes() != second.read_bytes()   # documented, not a bug


# ------------------------------------------------------------------ assembly


def test_an_assembly_exports_one_coloured_object_per_instance(svc, registry):
    svc.set_assembly("demo", [
        {"id": "a", "part": "box", "position": [0, 0, 0]},
        {"id": "b", "part": "box", "position": [20, 0, 0],
         "rotation_deg": [0, 0, 90], "color": "#ff0000"},
    ])
    result = svc.export_assembly("demo", "3mf")
    assert Path(result["path"]).name == "assembly.3mf"
    assert result["objects"] == 2
    root = model_xml(result["path"])
    assert object_colors(root) == {"a": color_for(
        svc.store.get_part("demo", "box")), "b": "#ff0000"}
    assert metadata_of(root)["Title"] == "demo"
    assert result["fidelity"] == {"geometry": "mesh", "colors": "per_instance",
                                  "metadata": "attached", "parametric": "none"}


def test_an_assembly_3mf_bakes_the_instance_transforms(svc, registry):
    """One object per instance with its placement baked in — a 3MF build item
    carries no transform here, so the geometry must already be placed."""
    svc.set_assembly("demo", [
        {"id": "a", "part": "box", "position": [0, 0, 0]},
        {"id": "far", "part": "box", "position": [100, 0, 0]},
    ])
    path = svc.export_part("demo", "box", "3mf")["path"]   # unplaced reference
    single = lib3mf_objects(path)[0]["triangles"]
    objects = lib3mf_objects(svc.export_assembly("demo", "3mf")["path"])
    assert [obj["name"] for obj in objects] == ["a", "far"]
    assert [obj["triangles"] for obj in objects] == [single, single]

    root = model_xml(svc.store.exports_dir("demo") / "assembly.3mf")
    xs = []
    for obj in root.findall(".//m:object", NS):
        vertices = obj.findall("m:mesh/m:vertices/m:vertex", NS)
        if vertices:
            xs.append(max(float(v.get("x")) for v in vertices))
    assert max(xs) > 90.0        # the far instance is where it was placed


def test_an_empty_assembly_refuses_before_it_writes(svc, registry):
    with pytest.raises(ValidationError) as exc:
        svc.export_assembly("demo", "3mf")
    assert "no instances" in exc.value.message


# --------------------------------------------------------------- the legacy


def test_the_plain_kernel_export_handler_is_untouched(svc, tmp_path):
    """Slice 5 added a handler; it replaced none. The worker's own `export`
    still writes the nameless, colourless 3MF it always did — which is exactly
    why `export_3mf_rich` had to exist."""
    out = tmp_path / "legacy.3mf"
    result = svc.kernel.request("export", {
        "script": TWO_BOX, "params": {}, "format": "3mf",
        "out_path": str(out), "tolerance": 0.05})
    assert set(result) == {"path", "size_bytes"}
    root = model_xml(out)
    assert root.get("unit") == "millimeter"
    objects = mesh_objects(root)
    assert len(objects) == 2
    assert [obj.get("name") for obj in objects] == [None, None]
    assert base_materials(root) == {}


def test_a_reference_part_exports_from_its_source_file(svc, registry,
                                                       tmp_path):
    """The other shape source: a reference part has no script, so the handler
    is handed its imported file. Its solids have no declared labels, so they
    fall back to the `solid_<i>` naming `get_metrics` reports."""
    step = tmp_path / "widget.step"
    svc.kernel.request("export", {
        "script": TWO_BOX, "params": {}, "format": "step",
        "out_path": str(step), "tolerance": 0.05})
    assert "error" not in registry.call(
        "import_cad_file", {"project": "demo", "source": str(step),
                            "part_id": "widget"})

    result = svc.export_part("demo", "widget", "3mf")
    assert result["objects"] == 2
    root = model_xml(result["path"])
    assert [obj.get("name") for obj in mesh_objects(root)] == ["solid_0",
                                                              "solid_1"]
    assert metadata_of(root)["Title"] == "widget"


def test_al6061_and_steel_are_the_colours_the_map_says():
    """The colour a 3MF gets is `interop_colors`', not a second table here."""
    assert MATERIALS["al6061"].category == "metal"
    assert ALUMINUM == "#c9ccd1" and STEEL == "#8d949c"
