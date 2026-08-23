"""PRD-017 slice 5 — structured STEP assembly export (FR2).

`export_assembly {format: "step", structured: true}` writes a real product
tree: one XCAF product per unique part, one occurrence per instance, names and
colours on both, AP242. The round trip is asserted with **our own importer**
(`inspect_cad_tree`, slice 2) — the export mirrors that walk, so reading the
file back through it is the honest end-to-end check, and any drift between the
two halves shows up as a failed count rather than as a silent flattening in
somebody's CAD.

`structured: false` stays the default, and today's fused compound is unchanged:
a STEP header carries a write timestamp, so that half is asserted on result
shape and geometry (`inspect_cad_tree` sees ONE product), not on bytes.

The two traps the export owns are pinned here as tests, not as prose: a
single-solid product must be added as a `TopoDS_Solid` (as a compound, OCCT
drops per-occurrence colour overrides), and a genuinely multi-solid product's
colours land on sub-shape labels, where our own reader reports them as absent.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentcad.core.interop_colors import color_for
from agentcad.core.model import ValidationError
from agentcad.core.tools import build_registry
from agentcad.core.tools_xchange import ASSEMBLY_FORMATS, _structured_items
from agentcad.server.app import create_app

from .conftest import BOX_SCRIPT, make_test_service

PIN = '''\
import build123d as b3d
PARAMS = {"height": {"default": 12.0, "min": 2.0, "max": 40.0, "unit": "mm"}}
def build(p):
    return b3d.Cylinder(2.0, p.height)
'''

TWO_BOX = '''\
import build123d as b3d
PARAMS = {}
SOLID_LABELS = ["body", "lid"]
def build(p):
    return b3d.Compound(children=[
        b3d.Box(10, 10, 10),
        b3d.Box(10, 10, 10).moved(b3d.Location((30, 0, 0)))])
'''


@pytest.fixture
def svc(kernel, tmp_path):
    service = make_test_service(tmp_path / "projects", kernel)
    service.create_project("demo")
    service.create_part("demo", "box", label="Base Plate", script=BOX_SCRIPT)
    service.create_part("demo", "pin", label="Locator Pin", script=PIN)
    return service


@pytest.fixture
def registry(svc):
    return build_registry(svc)


@pytest.fixture
def client(svc, registry):
    app = create_app(svc, registry, extra_allowed_hosts={"testserver"})
    return TestClient(app, base_url="http://127.0.0.1")


def three_instances(svc):
    """Two parts, three instances, one of them rotated and recoloured."""
    svc.set_assembly("demo", [
        {"id": "base", "part": "box", "position": [0, 0, 0]},
        {"id": "pin_a", "part": "pin", "position": [30, 0, 0],
         "rotation_deg": [0, 0, 90], "color": "#00ff00"},
        {"id": "pin_b", "part": "pin", "position": [0, 50, 10]},
    ])


def read_back(svc, path):
    return svc.kernel.request("inspect_cad_tree", {"source_path": str(path)})


# ------------------------------------------------------------- the round trip


def test_a_structured_export_reads_back_as_the_tree_it_was(svc, registry):
    three_instances(svc)
    result = svc.export_assembly("demo", "step", structured=True)
    assert Path(result["path"]).name == "assembly.step"
    # Two parts, three instances: the product count is the PART count (dedup by
    # part identity), the occurrence count is the instance count.
    assert result["products"] == 2
    assert result["occurrences"] == 3
    assert "AP242" in result["schema"]

    tree = read_back(svc, result["path"])
    assert tree["counts"] == {"products": 2, "occurrences": 3}
    # Product names are the parts' LABELS — what a human named them.
    assert sorted(p["name"] for p in tree["products"]) == ["Base Plate",
                                                           "Locator Pin"]
    occurrences = {o["name"]: o for o in tree["occurrences"]}
    assert set(occurrences) == {"base", "pin_a", "pin_b"}
    assert occurrences["pin_a"]["position"] == pytest.approx([30, 0, 0])
    assert occurrences["pin_a"]["rotation_deg"] == pytest.approx([0, 0, 90])
    assert occurrences["pin_b"]["position"] == pytest.approx([0, 50, 10])
    assert occurrences["base"]["rotation_deg"] == pytest.approx([0, 0, 0])
    # both pins are occurrences of ONE product
    assert occurrences["pin_a"]["product_index"] == \
        occurrences["pin_b"]["product_index"]
    assert occurrences["base"]["product_index"] != \
        occurrences["pin_a"]["product_index"]


def test_colours_survive_per_product_and_per_occurrence(svc, registry):
    three_instances(svc)
    result = svc.export_assembly("demo", "step", structured=True)
    tree = read_back(svc, result["path"])
    material = color_for(svc.store.get_part("demo", "pin"))
    assert {p["name"]: p["color"] for p in tree["products"]} == {
        "Base Plate": color_for(svc.store.get_part("demo", "box")),
        "Locator Pin": material,
    }
    colors = {o["name"]: o["color"] for o in tree["occurrences"]}
    # `pin_a`'s author-set colour overrides its product's; `pin_b` inherits.
    assert colors["pin_a"] == "#00ff00"
    assert colors["pin_b"] == material


def test_the_fidelity_says_tree_and_per_instance(svc, registry):
    three_instances(svc)
    result = svc.export_assembly("demo", "step", structured=True)
    # No `pmi` axis: the AP242 PMI writer is the single-part path, and claiming
    # `pmi: "none"` here would read as "your PMI was dropped".
    assert result["fidelity"] == {"geometry": "brep", "structure": "tree",
                                  "colors": "per_instance",
                                  "parametric": "none"}


def test_a_structured_export_is_written_atomically(svc, registry):
    three_instances(svc)
    result = svc.export_assembly("demo", "step", structured=True)
    exports = svc.store.exports_dir("demo")
    assert [p.name for p in exports.iterdir()] == ["assembly.step"]
    assert Path(result["path"]).stat().st_size == result["size_bytes"]


# -------------------------------------------------------- the flat default


def test_structured_defaults_to_false_and_the_fused_export_is_unchanged(
        svc, registry):
    """FR2 is opt-in: without the flag this is the same call the fused path
    always made. A STEP header carries a write timestamp, so the assertion is
    the result SHAPE (slice 4's idiom) plus what the file actually says."""
    three_instances(svc)
    plain = type(svc).export_assembly(svc, "demo", "step")   # pre-wrap path
    flat = svc.export_assembly("demo", "step")
    assert set(flat) == set(plain) | {"fidelity"}
    assert flat["path"] == plain["path"]
    assert flat["fidelity"] == {"geometry": "brep", "pmi": "none",
                                "parametric": "none"}
    assert "products" not in flat and "occurrences" not in flat


def test_the_fused_export_carries_no_identity_which_is_the_point_of_fr2(
        svc, registry):
    """What `structured: true` actually buys, measured rather than asserted in
    prose. OCCT does write NAUOs for a compound, so the *shape* of the tree is
    similar — but the fused file's products are called ``COMPOUND``, its
    occurrences are XCAF label paths, and nothing carries a colour."""
    three_instances(svc)
    fused = read_back(svc, svc.export_assembly("demo", "step")["path"])
    assert {p["name"] for p in fused["products"]} == {"COMPOUND"}
    assert all(o["color"] is None for o in fused["occurrences"])
    assert all(o["name"].startswith("=>[") for o in fused["occurrences"])

    tree = read_back(svc, svc.export_assembly(
        "demo", "step", structured=True)["path"])
    assert {p["name"] for p in tree["products"]} == {"Base Plate",
                                                     "Locator Pin"}
    assert {o["name"] for o in tree["occurrences"]} == {"base", "pin_a",
                                                        "pin_b"}
    assert all(o["color"] for o in tree["occurrences"])


def test_structured_is_step_only(svc, registry):
    three_instances(svc)
    for fmt in ("stl", "3mf", "glb"):
        with pytest.raises(ValidationError) as exc:
            svc.export_assembly("demo", fmt, structured=True)
        assert "STEP only" in exc.value.message


def test_an_empty_assembly_refuses_before_it_writes(svc, registry):
    with pytest.raises(ValidationError) as exc:
        svc.export_assembly("demo", "step", structured=True)
    assert "no instances" in exc.value.message
    assert not list(svc.store.exports_dir("demo").iterdir())


# ------------------------------------------------------------ the tool surface


def test_the_schema_advertises_structured_and_3mf(registry, client):
    schema = registry.get("export_assembly").input_schema["properties"]
    assert schema["format"]["enum"] == list(ASSEMBLY_FORMATS)
    assert "3mf" in schema["format"]["enum"]
    assert schema["structured"]["type"] == "boolean"

    tools = {t["name"]: t for t in client.get("/api/tools").json()["tools"]}
    served = tools["export_assembly"]["input_schema"]["properties"]
    assert served["format"]["enum"] == list(ASSEMBLY_FORMATS)
    assert "structured" in served
    assert "product tree" in tools["export_assembly"]["description"]


def test_the_handler_takes_the_argument_the_schema_advertises(svc, registry):
    """A schema that advertises `structured` over the old two-argument lambda
    is a TypeError at call time — the two move together."""
    three_instances(svc)
    result = registry.call("export_assembly", {"project": "demo",
                                               "format": "step",
                                               "structured": True})
    assert "error" not in result, result
    assert result["products"] == 2 and result["occurrences"] == 3
    plain = registry.call("export_assembly", {"project": "demo",
                                              "format": "step"})
    assert "products" not in plain


# ------------------------------------------------ PRD-013 expansion feeds it


def test_a_pattern_exports_count_occurrences_of_one_product(svc, registry):
    """The item list comes from `_resolved_instances`, so PRD-013's expansion
    is in force: one authored instance with a linear pattern is three
    occurrences of ONE product, exactly as the fused and glTF paths see it."""
    svc.set_assembly("demo", [{"id": "p", "part": "pin",
                               "position": [0, 0, 0]}])
    registry.call("set_pattern", {
        "project": "demo", "instance": "p",
        "pattern": {"kind": "linear", "count": 3, "step_mm": 15}})
    result = svc.export_assembly("demo", "step", structured=True)
    assert (result["products"], result["occurrences"]) == (1, 3)

    tree = read_back(svc, result["path"])
    assert tree["counts"] == {"products": 1, "occurrences": 3}
    assert sorted(o["name"] for o in tree["occurrences"]) == ["p[0]", "p[1]",
                                                              "p[2]"]
    assert sorted(o["position"][0] for o in tree["occurrences"]) == \
        pytest.approx([0.0, 15.0, 30.0])


def test_the_item_list_carries_source_identity_not_meshes(svc, registry):
    """`get_assembly` would have been the easy seam and the wrong one: a
    product needs a script or a source file, not a `mesh_key`."""
    three_instances(svc)
    items = _structured_items(svc, "demo")
    assert [i["name"] for i in items] == ["base", "pin_a", "pin_b"]
    assert {i["part_id"] for i in items} == {"demo/box", "demo/pin"}
    assert all(i["source_kind"] == "script" and i["script"] for i in items)
    assert all("mesh_key" not in i for i in items)
    assert items[1]["color"] == "#00ff00"


# ----------------------------------------------------------------- the traps


def test_a_reference_part_becomes_a_product_from_its_source_file(
        svc, registry, tmp_path):
    """The other shape source: an imported part has no script, so its item
    carries `source_path`. It must still be one product with a name."""
    step = tmp_path / "widget.step"
    svc.kernel.request("export", {
        "script": PIN, "params": {}, "format": "step", "out_path": str(step)})
    assert "error" not in registry.call(
        "import_cad_file", {"project": "demo", "source": str(step),
                            "part_id": "widget", "label": "Widget"})
    svc.set_assembly("demo", [
        {"id": "w1", "part": "widget", "position": [0, 0, 0]},
        {"id": "w2", "part": "widget", "position": [0, 40, 0]},
    ])
    items = _structured_items(svc, "demo")
    assert {i["source_kind"] for i in items} == {"reference"}
    assert all(i["source_path"].endswith(".step") for i in items)

    result = svc.export_assembly("demo", "step", structured=True)
    assert (result["products"], result["occurrences"]) == (1, 2)
    tree = read_back(svc, result["path"])
    assert [p["name"] for p in tree["products"]] == ["Widget"]
    assert [o["name"] for o in tree["occurrences"]] == ["w1", "w2"]


def test_a_multi_solid_product_keeps_its_solids_but_loses_the_colour_read(
        svc, registry):
    """Honest coverage of a measured OCCT limitation (module docstring): a
    multi-solid product's colours are written PER SOLID, so on re-read they sit
    on sub-shape labels and our walk reports the product as uncoloured. The
    structure — one product, one occurrence per instance — is unaffected."""
    svc.create_part("demo", "duo", script=TWO_BOX)
    svc.set_assembly("demo", [
        {"id": "d1", "part": "duo", "position": [0, 0, 0]},
        {"id": "d2", "part": "duo", "position": [0, 60, 0], "color": "#00ff00"},
    ])
    result = svc.export_assembly("demo", "step", structured=True)
    assert (result["products"], result["occurrences"]) == (1, 2)
    tree = read_back(svc, result["path"])
    assert tree["counts"] == {"products": 1, "occurrences": 2}
    assert [o["name"] for o in tree["occurrences"]] == ["d1", "d2"]
    assert [o["color"] for o in tree["occurrences"]] == [None, None]
    # the colour IS in the file, one styled item per solid
    assert "COLOUR_RGB" in Path(result["path"]).read_text(errors="replace")


def test_a_single_solid_product_is_written_as_a_solid(svc, registry):
    """The unwrap that makes the per-occurrence override above work for the
    ordinary case: every build123d part is a single-solid Compound, and as a
    compound the override is silently dropped by the writer."""
    svc.set_assembly("demo", [
        {"id": "a", "part": "box", "position": [0, 0, 0], "color": "#123456"},
        {"id": "b", "part": "box", "position": [40, 0, 0], "color": "#abcdef"},
    ])
    tree = read_back(svc, svc.export_assembly(
        "demo", "step", structured=True)["path"])
    assert [o["color"] for o in tree["occurrences"]] == ["#123456", "#abcdef"]
    assert tree["counts"] == {"products": 1, "occurrences": 2}
