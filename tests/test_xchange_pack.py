"""PRD-017 slice 4 — the `tools_xchange` pack: wrappers, routing, fidelity.

The kernel-backed half (``tests/test_interop_gltf.py`` is the pure one): real
builds, the real registry, the real routes. What it holds down:

* the pack captures the **final** ``export_assembly`` — the one
  ``tools_structure`` installed — so PRD-013 expansion is still in force under
  the interop wrapper (a pattern exports N nodes, not one);
* ``gltf``/``glb`` are written server-side from the mesh cache and are
  **byte-identical** across two exports (AC3, the PRD-014 sha idiom);
* ``step`` on a part with PMI routes to the AP242 handler, ``pmi: false`` opts
  out, and a part without PMI is delegated **unchanged**;
* the in-place schema mutation is visible through ``build_registry`` *and*
  ``GET /api/tools`` — with a handler that can actually take the arguments it
  advertises;
* every export result carries ``fidelity`` (FR12), delegated paths included.
"""

import json
import struct
from hashlib import sha256
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentcad.core.model import ValidationError
from agentcad.core.tools import build_registry
from agentcad.core.tools_xchange import (ASSEMBLY_FORMATS, PART_FORMATS,
                                         _WRAPPED)
from agentcad.server.app import create_app

from .conftest import BOX_SCRIPT, make_test_service

# A cube: six planar faces (datums, a linear dim), no cylinder — so the PMI
# fixture stays to what `core/pmi.py` can map onto it.
CUBE_PMI = {
    "dims": [{"id": "h", "kind": "linear", "target": "height",
              "plus": 0.1, "minus": 0.1}],
    "datums": [{"id": "A", "face": "top"}],
    "fcf": [{"id": "flat", "type": "flatness", "tol_mm": 0.02, "datums": []}],
}


@pytest.fixture
def svc(kernel, tmp_path):
    service = make_test_service(tmp_path / "projects", kernel)
    service.create_project("demo")
    service.create_part("demo", "box", script=BOX_SCRIPT)
    return service


@pytest.fixture
def registry(svc):
    """`build_registry` is what installs the pack — the wrappers exist only
    after it has run, exactly as in the real server."""
    return build_registry(svc)


@pytest.fixture
def client(svc, registry):
    app = create_app(svc, registry, extra_allowed_hosts={"testserver"})
    return TestClient(app, base_url="http://127.0.0.1")


def sha(path) -> str:
    return sha256(Path(path).read_bytes()).hexdigest()


def two_boxes(svc):
    svc.set_assembly("demo", [
        {"id": "a", "part": "box", "position": [0, 0, 0]},
        {"id": "b", "part": "box", "position": [20, 0, 0],
         "rotation_deg": [0, 0, 90], "color": "#ff0000"},
    ])


def glb_document(path) -> dict:
    blob = Path(path).read_bytes()
    assert blob[:4] == b"glTF"
    length, kind = struct.unpack_from("<II", blob, 12)
    assert kind == 0x4E4F534A
    return json.loads(blob[20:20 + length].decode("utf-8"))


# ------------------------------------------------------------- wiring


def test_the_pack_wraps_the_final_expanded_export_assembly(svc, registry):
    """Load order is the whole reason this file is called `tools_xchange`:
    `tools_structure` REPLACES `export_assembly`, so a pack sorting before it
    would be thrown away."""
    assert getattr(svc.export_part, _WRAPPED, False) is True
    assert getattr(svc.export_assembly, _WRAPPED, False) is True
    # the captured original is tools_structure's expanded version, not the core
    assert getattr(svc.export_assembly.__wrapped__,
                   "_agentcad_structure_wrapped", False) is True
    assert getattr(svc._resolved_instances,
                   "_agentcad_structure_wrapped", False) is True


def test_registering_twice_is_idempotent(svc, registry):
    from agentcad.core import tools_xchange

    before = svc.export_part
    tools_xchange.register(build_registry(svc), svc)
    assert svc.export_part is before
    assert svc.export_part("demo", "box", "stl")["size_bytes"] > 100


# ------------------------------------------------------------- schemas


def test_the_export_schemas_are_mutated_in_place(registry):
    part = registry.get("export_part").input_schema["properties"]
    assert part["format"]["enum"] == list(PART_FORMATS)
    assert part["pmi"]["type"] == "boolean"
    assert part["metadata"]["type"] == "object"
    assembly = registry.get("export_assembly").input_schema["properties"]
    assert assembly["format"]["enum"] == list(ASSEMBLY_FORMATS)
    # Slice 5 owns `structured` and assembly `3mf`; an enum entry is a promise,
    # so neither is advertised before it runs.
    assert "structured" not in assembly
    assert "3mf" not in assembly["format"]["enum"]
    # `import_cad_file` belongs to the import pack, not this one.
    assert registry.get("import_cad_file") is not None


def test_the_mutation_is_visible_over_the_tool_api(client):
    tools = {t["name"]: t for t in client.get("/api/tools").json()["tools"]}
    schema = tools["export_part"]["input_schema"]["properties"]
    assert schema["format"]["enum"] == list(PART_FORMATS)
    assert "pmi" in schema and "metadata" in schema
    assert "glb" in tools["export_part"]["description"]
    assert tools["export_assembly"]["input_schema"]["properties"]["format"][
        "enum"] == list(ASSEMBLY_FORMATS)


def test_the_handler_accepts_every_argument_the_schema_advertises(registry):
    """A schema that advertises `pmi` over a handler that cannot take the
    keyword is a TypeError at call time — the two move together."""
    result = registry.call("export_part", {
        "project": "demo", "part_id": "box", "format": "stl",
        "tolerance": 0.05, "pmi": False, "metadata": {"Title": "unused"}})
    assert "error" not in result
    assert result["path"].endswith("box.stl")


def test_an_unknown_format_refuses_like_the_service_always_did(svc, registry):
    with pytest.raises(ValidationError) as exc:
        svc.export_part("demo", "box", "obj")
    assert exc.value.details["known"] == list(PART_FORMATS)
    payload = registry.call("export_part", {"project": "demo",
                                            "part_id": "box", "format": "obj"})
    assert payload["error"]["type"] == "validation_error"


# ------------------------------------------------------------ glTF / GLB


@pytest.mark.parametrize("fmt", ["gltf", "glb"])
def test_a_part_exports_to_gltf_server_side(svc, registry, fmt):
    result = svc.export_part("demo", "box", fmt)
    out = Path(result["path"])
    assert out.name == f"box.{fmt}"
    assert result["size_bytes"] == out.stat().st_size > 500
    assert result["fidelity"] == {"geometry": "mesh", "colors": "per_instance",
                                  "parametric": "none"}
    if fmt == "glb":
        doc = glb_document(out)
    else:
        doc = json.loads(out.read_text())
        assert doc["buffers"][0]["uri"].startswith("data:")
    assert doc["asset"]["extras"] == {"source_up_axis": "+Z",
                                      "converted_to": "+Y"}
    assert len(doc["nodes"]) == 2 and doc["nodes"][1]["name"] == "box"
    # the default material (al6061 -> metal/aluminum) reaches the file
    pbr = doc["materials"][0]["pbrMetallicRoughness"]
    assert pbr["metallicFactor"] == 0.9


def test_a_glb_export_is_byte_identical_across_two_runs(svc, registry):
    """AC3's machine half at the tool layer: same state, same bytes."""
    first = sha(registry.call("export_part", {"project": "demo",
                                              "part_id": "box",
                                              "format": "glb"})["path"])
    second = sha(registry.call("export_part", {"project": "demo",
                                               "part_id": "box",
                                               "format": "glb"})["path"])
    assert first == second
    two_boxes(svc)
    asm_first = sha(svc.export_assembly("demo", "glb")["path"])
    asm_second = sha(svc.export_assembly("demo", "glb")["path"])
    assert asm_first == asm_second
    assert asm_first != first


def test_an_assembly_deduplicates_meshes_and_keeps_instance_colours(svc,
                                                                    registry):
    two_boxes(svc)
    result = svc.export_assembly("demo", "glb")
    assert Path(result["path"]).name == "assembly.glb"
    assert result["fidelity"] == {"geometry": "mesh", "colors": "per_instance",
                                  "parametric": "none"}
    doc = glb_document(result["path"])
    assert len(doc["bufferViews"]) == 3       # one part, one set of buffers
    assert len(doc["nodes"]) == 3             # root + two instances
    assert [n["name"] for n in doc["nodes"][1:]] == ["a", "b"]
    # instance `b`'s author-set colour wins over the material category
    factors = [m["pbrMetallicRoughness"]["baseColorFactor"]
               for m in doc["materials"]]
    assert [1.0, 0.0, 0.0, 1.0] in factors    # #ff0000, linear-exact
    assert len(doc["materials"]) == 2
    assert doc["nodes"][2]["rotation"] == pytest.approx(
        [0.0, 0.0, 0.707107, 0.707107], abs=1e-6)
    assert doc["nodes"][2]["translation"] == [20.0, 0.0, 0.0]


def test_prd013_expansion_is_still_in_force_under_the_wrapper(svc, registry):
    """A pattern is ONE authored instance and three exported nodes — proof the
    captured method is `tools_structure`'s, not the core one."""
    svc.set_assembly("demo", [{"id": "a", "part": "box",
                               "position": [0, 0, 0]}])
    registry.call("set_pattern", {
        "project": "demo", "instance": "a",
        "pattern": {"kind": "linear", "count": 3, "step_mm": 15}})
    doc = glb_document(svc.export_assembly("demo", "glb")["path"])
    assert len(doc["nodes"]) == 4             # root + three members
    assert len(doc["meshes"]) == 1            # one part, one mesh
    assert [n["name"] for n in doc["nodes"][1:]] == ["a[0]", "a[1]", "a[2]"]


def test_an_empty_assembly_refuses_like_the_other_formats(svc, registry):
    with pytest.raises(ValidationError) as exc:
        svc.export_assembly("demo", "glb")
    assert "no instances" in exc.value.message


def test_an_errored_instance_is_reported_not_silently_dropped(svc, registry):
    two_boxes(svc)
    svc.create_part("demo", "broken", script=BOX_SCRIPT)
    svc.set_assembly("demo", [
        {"id": "a", "part": "box", "position": [0, 0, 0]},
        {"id": "bad", "part": "broken", "position": [0, 0, 30]},
    ])
    svc.update_part("demo", "broken",
                    script='PARAMS = {}\ndef build(p):\n    raise RuntimeError("x")\n')
    result = svc.export_assembly("demo", "glb")
    assert result["fidelity"]["instances_skipped"] == [
        {"id": "bad", "reason": "error"}]
    assert len(glb_document(result["path"])["nodes"]) == 2


# ------------------------------------------------------- delegated formats


@pytest.mark.parametrize("fmt,expected", [
    ("step", {"geometry": "brep", "pmi": "none", "parametric": "none"}),
    ("stl", {"geometry": "mesh", "parametric": "none"}),
    ("3mf", {"geometry": "mesh", "colors": "none", "metadata": "none",
             "parametric": "none"}),
])
def test_delegated_part_exports_are_unchanged_but_carry_fidelity(
        svc, registry, fmt, expected):
    """The captured original is called with the same arguments and its result
    is returned as-is — `fidelity` is the only key that appears."""
    plain = type(svc).export_part(svc, "demo", "box", fmt)   # pre-wrap path
    before = sha(plain["path"])
    wrapped = svc.export_part("demo", "box", fmt)
    assert set(wrapped) == set(plain) | {"fidelity"}
    assert wrapped["path"] == plain["path"]
    assert wrapped["fidelity"] == expected
    if fmt == "stl":
        # Byte-identical, not merely equivalent. STEP's header carries a
        # write timestamp and 3MF mints random UUIDs (spec §4), so only STL
        # can be hashed — for those two the result shape is the assertion.
        assert wrapped["size_bytes"] == plain["size_bytes"]
        assert sha(wrapped["path"]) == before


@pytest.mark.parametrize("fmt,expected", [
    ("step", {"geometry": "brep", "pmi": "none", "parametric": "none"}),
    ("stl", {"geometry": "mesh", "parametric": "none"}),
])
def test_delegated_assembly_exports_are_unchanged_but_carry_fidelity(
        svc, registry, fmt, expected):
    two_boxes(svc)
    result = svc.export_assembly("demo", fmt)
    assert Path(result["path"]).name == f"assembly.{fmt}"
    assert result["fidelity"] == expected
    assert result["size_bytes"] > 100


def test_an_unsupported_assembly_format_names_what_is_supported(svc, registry):
    two_boxes(svc)
    with pytest.raises(ValidationError) as exc:
        svc.export_assembly("demo", "3mf")     # slice 5
    assert exc.value.message == (
        "assembly export supports formats: step, stl, gltf, glb")


# -------------------------------------------------------------- STEP + PMI


def test_a_part_with_pmi_exports_ap242_through_the_tool(svc, registry):
    registry.call("set_part_pmi", {"project": "demo", "part_id": "box",
                                   "pmi": CUBE_PMI})
    result = registry.call("export_part", {"project": "demo", "part_id": "box",
                                           "format": "step"})
    assert result["pmi_attached"] == {"dims": 1, "datums": 1, "fcf": 1}
    assert "AP242" in result["schema"]
    assert "AP242" in Path(result["path"]).read_text()[:8192]
    assert result["fidelity"] == {
        "geometry": "brep", "pmi": "attached", "pmi_skipped": [],
        "pmi_notes": [], "parametric": "none"}


def test_pmi_false_opts_back_out_to_the_plain_export(svc, registry):
    registry.call("set_part_pmi", {"project": "demo", "part_id": "box",
                                   "pmi": CUBE_PMI})
    result = registry.call("export_part", {"project": "demo", "part_id": "box",
                                           "format": "step", "pmi": False})
    assert result["fidelity"] == {"geometry": "brep", "pmi": "opted_out",
                                  "parametric": "none"}
    assert "pmi_attached" not in result
    assert "AP242" not in Path(result["path"]).read_text()[:8192]


def test_an_empty_pmi_section_is_not_pmi(svc, registry):
    registry.call("set_part_pmi", {"project": "demo", "part_id": "box",
                                   "pmi": {"dims": [], "datums": [],
                                           "fcf": []}})
    result = svc.export_part("demo", "box", "step")
    assert result["fidelity"]["pmi"] == "none"


def test_a_reference_part_exports_its_pmi_from_the_source_file(svc, registry):
    """A reference (imported) part has no script — `source_path` is the other
    way into the handler, the same two sources `_shape_item` resolves."""
    step = Path(type(svc).export_part(svc, "demo", "box", "step")["path"])
    imports = svc.store.imports_dir("demo", write=True)
    (imports / "cube.step").write_bytes(step.read_bytes())
    svc.create_part("demo", "cube", kind="reference", source="cube.step")
    registry.call("set_part_pmi", {"project": "demo", "part_id": "cube",
                                   "pmi": CUBE_PMI})
    result = svc.export_part("demo", "cube", "step")
    assert result["pmi_attached"] == {"dims": 1, "datums": 1, "fcf": 1}
    assert result["fidelity"]["pmi"] == "attached"
    assert Path(result["path"]).name == "cube.step"


def test_a_skipped_pmi_entry_reaches_the_fidelity_report(svc, registry):
    """FR3/AC6: a diameter dim on a cube has no cylindrical face to land on.
    The export still succeeds and says what it dropped."""
    pmi = dict(CUBE_PMI, dims=CUBE_PMI["dims"] + [
        {"id": "bore", "kind": "diameter", "target": 10.0,
         "plus": 0.05, "minus": 0.05}])
    registry.call("set_part_pmi", {"project": "demo", "part_id": "box",
                                   "pmi": pmi})
    result = svc.export_part("demo", "box", "step")
    assert [row["id"] for row in result["fidelity"]["pmi_skipped"]] == ["bore"]
    assert result["fidelity"]["pmi"] == "attached"


# ------------------------------------------------------------------ routes


def test_the_export_routes_go_through_the_wrappers(client, svc):
    response = client.post("/api/projects/demo/parts/box/export",
                           json={"format": "glb"})
    assert response.status_code == 200
    body = response.json()
    assert body["fidelity"]["geometry"] == "mesh"
    assert Path(body["path"]).name == "box.glb"

    two_boxes(svc)
    response = client.post("/api/projects/demo/export", json={"format": "gltf"})
    assert response.status_code == 200
    assert response.json()["fidelity"]["colors"] == "per_instance"


def test_every_export_path_reports_fidelity(svc, registry):
    """FR12 in one assertion: nothing leaves this pack without it."""
    two_boxes(svc)
    for fmt in PART_FORMATS:
        assert "fidelity" in svc.export_part("demo", "box", fmt)
        assert "parametric" in svc.export_part("demo", "box", fmt)["fidelity"]
    for fmt in ASSEMBLY_FORMATS:
        assert "fidelity" in svc.export_assembly("demo", fmt)
