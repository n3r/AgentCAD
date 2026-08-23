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
    # Slice 5 landed both: an enum entry is a promise, and these two now run
    # (`tests/test_interop_step_asm.py` holds the promise down).
    assert "3mf" in assembly["format"]["enum"]
    assert assembly["structured"]["type"] == "boolean"
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
    # `3mf` left this list in slice 5: a part 3MF is no longer delegated, it
    # goes through `export_3mf_rich` (tests/test_interop_3mf.py).
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
        svc.export_assembly("demo", "obj")
    # The supported list is live: it names usd exactly when the extra is
    # installed, so the expectation is derived, not hardcoded.
    from agentcad.core.tools_xchange import assembly_formats
    assert exc.value.message == (
        "assembly export supports formats: " + ", ".join(assembly_formats()))


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


# --------------------------------------------- refusals reach the caller


def _empty_acm() -> bytes:
    """An ACM1 buffer with a valid header and zero triangles — what a build
    that produced no geometry leaves in the cache."""
    return struct.pack("<4sIIII", b"ACM1", 0, 0, 0, 0)


def _blank_the_mesh(svc, instance_id="a"):
    """Overwrite one built instance's cached mesh with an empty one."""
    entry = next(i for i in svc.get_assembly("demo")["instances"]
                 if i["id"] == instance_id)
    path = svc.store.cache_dir("demo") / f"{entry['mesh_key']}.acm"
    assert path.is_file(), path
    path.write_bytes(_empty_acm())
    return path


def test_an_empty_mesh_instance_is_skipped_not_fatal(svc, registry):
    """One degenerate member must not cost the caller the other N.

    `parse_acm` refusing mid-export used to fail the whole assembly; the
    contract that already says what did not make it into the file is
    `fidelity.instances_skipped`, so an empty mesh is a row in it.
    """
    # Two DIFFERENT parts: meshes are deduplicated by `mesh_key`, so two
    # instances of one part share one cache entry and blanking it blanks both.
    svc.create_part("demo", "slab", script=BOX_SCRIPT.replace(
        "p.size, p.size, p.size", "p.size, p.size, p.size * 3"))
    svc.set_assembly("demo", [
        {"id": "a", "part": "box", "position": [0, 0, 0]},
        {"id": "b", "part": "slab", "position": [20, 0, 0]},
    ])
    svc.get_assembly("demo")
    _blank_the_mesh(svc, "a")

    result = svc.export_assembly("demo", "glb")
    assert result["fidelity"]["instances_skipped"] == [
        {"id": "a", "reason": "empty_mesh"}]
    doc = glb_document(result["path"])
    assert [n["name"] for n in doc["nodes"][1:]] == ["b"]


def test_an_empty_part_export_is_a_clean_validation_error(svc, registry):
    """One part, nothing to write: a refusal that says so, and — the blocker —
    a 4xx envelope rather than a 500. `GltfError` used to be a bare
    `ValueError` and escaped both `ToolRegistry.call` and FastAPI's `AppError`
    handler."""
    svc.export_part("demo", "box", "glb")        # build + cache
    key = svc.mesh_info("demo", "box")["key"]
    (svc.store.cache_dir("demo") / f"{key}.acm").write_bytes(_empty_acm())

    with pytest.raises(ValidationError) as exc:
        svc.export_part("demo", "box", "glb")
    assert "nothing to tessellate" in exc.value.message
    payload = registry.call("export_part", {"project": "demo",
                                            "part_id": "box", "format": "glb"})
    assert payload["error"]["type"] == "validation_error"


def test_a_corrupt_mesh_buffer_refuses_as_422_over_the_route(svc, registry,
                                                             client):
    """The same blocker at the HTTP edge: a malformed buffer is the caller's
    request, so it is a 422 with an `{"error": ...}` body, never a 500."""
    svc.export_part("demo", "box", "gltf")
    key = svc.mesh_info("demo", "box")["key"]
    (svc.store.cache_dir("demo") / f"{key}.acm").write_bytes(b"NOPE" + b"\0" * 40)
    response = client.post("/api/projects/demo/parts/box/export",
                           json={"format": "gltf"})
    assert response.status_code == 422, response.text
    assert response.json()["error"]["type"] == "ValidationError"


def test_the_staging_name_is_random_per_write(svc, registry):
    """Changelog 0181, applied here: a fixed `.<stem>.tmp<suffix>` is one
    staging name per target path, so two exports of one part opened the SAME
    file, interleaved their bytes into it, and each `os.replace`d the mixture
    into place."""
    from agentcad.core.tools_xchange import _staging_name

    target = Path("/tmp/exports/assembly.glb")
    names = {_staging_name(target) for _ in range(20)}
    assert len(names) == 20
    for name in names:
        assert name.startswith(".assembly.") and name.endswith(".tmp.glb")
    # nothing is left behind by a successful export either
    svc.export_part("demo", "box", "glb")
    assert not [p for p in svc.store.exports_dir("demo").iterdir()
                if p.name.endswith(".tmp") or ".tmp." in p.name]


# ------------------------------------------------------- 3MF metadata guard


def test_metadata_carrying_a_control_character_is_refused(svc, registry):
    """lib3mf takes a C string: a NUL truncates the value silently at the
    library boundary, so `Title\\0evil` is stamped as `Title` and the caller is
    told the metadata was attached."""
    for bad in ("Widget\x00evil", "Widget\x01", "\x1bTitle"):
        result = registry.call("export_part", {
            "project": "demo", "part_id": "box", "format": "3mf",
            "metadata": {"title": bad}})
        assert result["error"]["type"] == "validation_error", bad
        assert "control character" in result["error"]["message"]
    # ordinary text (including legal XML whitespace) still passes
    ok = registry.call("export_part", {
        "project": "demo", "part_id": "box", "format": "3mf",
        "metadata": {"title": "Widget\n2"}})
    assert "error" not in ok, ok


# ---------------------------------------------- a malformed manifest `pmi`


def _write_pmi(svc, part_id, pmi):
    """Put a raw value in the part's loose `pmi` key, bypassing validation —
    a hand edit, a bad merge, or an older tool."""
    manifest = svc.store.manifest("demo")
    next(e for e in manifest["parts"] if e["id"] == part_id)["pmi"] = pmi
    svc.store.save_manifest("demo", manifest)


@pytest.mark.parametrize("bad", [
    {"dims": [{"id": "h", "target": "height", "plus": 0.1, "minus": 0.1}]},
    {"dims": [{"id": "h", "kind": "linear", "target": "sideways",
               "plus": 0.1, "minus": 0.1}]},
    {"datums": [{"id": "A", "face": "sideways"}]},
    {"fcf": [{"id": "f", "type": "roundness", "tol_mm": 0.1, "datums": []}]},
])
def test_a_malformed_manifest_pmi_refuses_before_the_kernel(svc, registry, bad):
    """`pmi` is a schema-tolerant LOOSE key, so anything can end up there. It
    used to be handed straight to the worker, where a missing `kind` was a
    `KeyError` inside `map_pmi`: a 502 blaming the kernel for a manifest the
    user can fix. Validated on read, it is a 422 naming the part."""
    _write_pmi(svc, "box", bad)
    result = registry.call("export_part", {"project": "demo",
                                           "part_id": "box", "format": "step"})
    assert result["error"]["type"] == "validation_error", result
    assert "box" in result["error"]["message"]
    assert "malformed pmi" in result["error"]["message"]


def test_the_kernel_maps_a_malformed_entry_to_a_skipped_row(svc, registry):
    """Defence in depth: with the server-side validation bypassed entirely
    (the kernel handler called directly), `map_pmi` answers with a
    `malformed_entry` ROW rather than a KeyError."""
    step = svc.store.exports_dir("demo") / "direct.step"
    result = svc.kernel.request("export_step_pmi", {
        "script": BOX_SCRIPT, "params": {}, "name": "box",
        "out_path": str(step),
        "pmi": {"dims": [{"id": "h"}, "not-an-object"],
                "datums": [{"id": "A", "face": "nowhere"}],
                "fcf": [{"id": "f", "type": "flatness", "tol_mm": "wat",
                         "datums": []}]},
    })
    reasons = {row["id"]: row["reason"] for row in result["pmi_skipped"]}
    assert set(reasons) == {"h", "dims[1]", "A", "f"}
    assert all(r.startswith("malformed_entry") for r in reasons.values()), \
        reasons
    assert result["pmi_attached"] == {"dims": 0, "datums": 0, "fcf": 0}
    assert Path(result["path"]).is_file()


def test_a_part_whose_every_pmi_entry_is_skipped_reports_pmi_none(svc,
                                                                  registry):
    """`fidelity.pmi` is a claim about the FILE. A part whose entries were all
    skipped exports a perfectly good AP242 file with no PMI in it, and saying
    "attached" there is the one lie `fidelity` exists to prevent."""
    # a diameter dim and a cylindricity frame on a CUBE: no cylindrical face
    registry.call("set_part_pmi", {
        "project": "demo", "part_id": "box",
        "pmi": {"dims": [{"id": "bore", "kind": "diameter", "target": 10.0,
                          "plus": 0.05, "minus": 0.05}],
                "fcf": [{"id": "cyl", "type": "cylindricity", "tol_mm": 0.02,
                         "datums": []}]}})
    result = svc.export_part("demo", "box", "step")
    assert result["pmi_attached"] == {"dims": 0, "datums": 0, "fcf": 0}
    assert result["fidelity"]["pmi"] == "none"
    assert sorted(r["id"] for r in result["fidelity"]["pmi_skipped"]) == \
        ["bore", "cyl"]


def test_every_export_path_reports_fidelity(svc, registry):
    """FR12 in one assertion: nothing leaves this pack without it."""
    two_boxes(svc)
    for fmt in PART_FORMATS:
        assert "fidelity" in svc.export_part("demo", "box", fmt)
        assert "parametric" in svc.export_part("demo", "box", fmt)["fidelity"]
    for fmt in ASSEMBLY_FORMATS:
        assert "fidelity" in svc.export_assembly("demo", fmt)
