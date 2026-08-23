"""Structured CAD import, server half (PRD-017 FR8–FR10).

The kernel half (`tests/test_interop_import_kernel.py`) proves the XCAF walk;
this proves the *landing*: which parts and instances a file becomes, what the
auto-detect decides when the caller says nothing, and what survives in the
manifest afterwards.

Fixture files are authored in-suite through the real kernel (no binary blobs).
Four of them, because the interesting decisions are all about telling them
apart:

* ``assembly.step`` — the kernel suite's nested, coloured, 3-product /
  7-occurrence assembly (imported through its authoring helper, which this
  module must not duplicate or edit).
* ``widget.step`` — AgentCAD's OWN export of a two-solid part. OCCT reads it
  back as two anonymous occurrences of two products both called ``SOLID``, so
  a naive ">1 occurrence" auto-detect would explode one re-imported widget
  into two parts and two instances. It must stay flat.
* ``solo.step`` — one solid, one occurrence: FR9's floor.
* ``blob.stl`` — a mesh, which has no product tree at all.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agentcad.core.manifest_merge import merge_manifests
from agentcad.core.tools import build_registry
from agentcad.server import security as security_module
from agentcad.server.app import create_app

from .conftest import BOX_SCRIPT, login, make_test_service
from .test_interop_import_kernel import make_assembly_step

pytestmark = pytest.mark.portability

SOLO_SCRIPT = (
    "import build123d as b3d\n"
    "PARAMS = {}\n"
    "def build(p):\n"
    "    return b3d.Solid.make_box(10, 10, 10)\n"
)

#: Two solids in one compound — what `export_part(format="step")` writes for a
#: multi-solid part, and the file the auto-detect must NOT structure.
COMPOUND_SCRIPT = (
    "from build123d import *\n"
    "PARAMS = {}\n"
    "def build(p):\n"
    "    a = Solid.make_box(10, 10, 10)\n"
    "    b = Solid.make_box(10, 10, 10).moved(Location((20, 0, 0)))\n"
    "    return Compound(children=[a, b])\n"
)

#: The seven occurrence names the fixture authors, sanitized to instance ids
#: (they are already `[a-z0-9_]`, so the sanitizer is the identity here).
OCCURRENCES = {
    "bracket_1", "ball_1", "ball_2",
    "pinpair_1_pin_1", "pinpair_1_pin_2",
    "pinpair_2_pin_1", "pinpair_2_pin_2",
}

IMPORT_FIDELITY_KEYS = {"geometry", "structure", "colors", "pmi", "parametric"}


# ------------------------------------------------------------------ fixtures


@pytest.fixture(scope="module")
def sources(kernel, tmp_path_factory):
    """The four fixture files, authored once for the whole module."""
    root = tmp_path_factory.mktemp("interop_import_sources")
    files = {"assembly": make_assembly_step(kernel, root)}
    for name, script, fmt in (("solo", SOLO_SCRIPT, "step"),
                              ("compound", COMPOUND_SCRIPT, "step"),
                              ("blob", BOX_SCRIPT, "stl")):
        out = root / f"{name}.{fmt}"
        kernel.request("export", {"script": script, "params": {},
                                  "format": fmt, "out_path": str(out)})
        files[name] = out
    return files


def _project(kernel, tmp_path, name="demo"):
    """A fresh service + registry with one empty project."""
    service = make_test_service(tmp_path / "projects", kernel)
    service.create_project(name)
    return service, build_registry(service)


def _import(registry, **args):
    result = registry.call("import_cad_file", {"project": "demo", **args})
    assert "error" not in result, result
    return result


def _by_id(rows):
    return {row["id"]: row for row in rows}


@pytest.fixture(scope="module")
def structured(kernel, tmp_path_factory, sources):
    """One structured import of the assembly fixture, shared by the read-only
    assertions (it builds three reference parts through the kernel)."""
    service, registry = _project(kernel, tmp_path_factory.mktemp("structured"))
    result = _import(registry, source=str(sources["assembly"]))
    return service, result


# ------------------------------------------------------- AC2: the landing


def test_structured_import_lands_one_part_per_unique_product(structured):
    """7 occurrences of 3 products land as 3 parts — the dedup is the point."""
    _service, result = structured
    parts = _by_id(result["parts"])
    assert sorted(parts) == ["ball", "bracket", "pin"]
    # ids come from the product names; the ORIGINAL name is kept as the label
    assert sorted(p["label"] for p in result["parts"]) == \
        ["Ball", "Bracket", "Pin"]
    for part in result["parts"]:
        assert part["kind"] == "reference"
        assert part["source"].endswith(".brep")
        assert part["status"]["state"] == "ok", part["status"]
        assert part["metrics"]["n_solids"] == 1


def test_structured_import_lands_every_occurrence_as_an_instance(structured):
    _service, result = structured
    instances = _by_id(result["instances"])
    assert set(instances) == OCCURRENCES
    assert {i["part"] for i in result["instances"]} == {"ball", "bracket", "pin"}
    # four pins, two balls, one bracket — the occurrence count, not the product
    assert sum(1 for i in result["instances"] if i["part"] == "pin") == 4


def test_structured_instances_carry_the_composed_pose(structured):
    """The spike's (0,80,10) case: `pin_2`'s local (30,0,0) through a
    sub-assembly placed at (0,50,10) and rotated 90 deg about Z. A flattened
    instance has to carry the COMPOSED transform or it lands in the wrong
    place, and the two-axis rotation pins the intrinsic-XYZ convention."""
    _service, result = structured
    instances = _by_id(result["instances"])
    assert instances["pinpair_2_pin_2"]["position"] == \
        pytest.approx([0, 80, 10], abs=1e-6)
    assert instances["pinpair_2_pin_2"]["rotation_deg"] == \
        pytest.approx([-90, 0, 90], abs=1e-6)
    assert instances["bracket_1"]["position"] == pytest.approx([0, 0, 0])
    assert instances["ball_2"]["position"] == pytest.approx([-5, -5, 40])


def test_structured_instances_carry_colors_including_the_override(structured):
    _service, result = structured
    instances = _by_id(result["instances"])
    # ball_1 inherits the product colour, ball_2 carries its own override
    assert instances["ball_1"]["color"] != instances["ball_2"]["color"]
    assert instances["ball_1"]["color"].startswith("#")
    assert instances["pinpair_1_pin_1"]["color"] == \
        instances["pinpair_2_pin_2"]["color"]


def test_structured_result_carries_the_tree_and_the_product_mapping(structured):
    _service, result = structured
    tree = result["tree"]
    assert tree["counts"] == {"products": 3, "occurrences": 7}
    assert len(tree["tree"]) == 1 and tree["tree"][0]["name"] == "TopAssembly"
    for product in tree["products"]:
        # what the file called it, what it became, and where the geometry went
        assert product["part_id"] in ("ball", "bracket", "pin")
        assert product["file"].endswith(".brep")
        assert "/" not in product["file"]


def test_structured_import_is_a_usable_assembly(structured):
    """Not just rows in a manifest: the landed project reads back as a built
    assembly through the normal path."""
    service, _result = structured
    assembly = service.get_assembly("demo")
    assert len(assembly["instances"]) == 7
    assert all(i["state"] == "ok" for i in assembly["instances"])
    assert assembly["total_mass_g"] > 0


def test_the_instance_batch_is_one_write_and_one_event(kernel, tmp_path,
                                                       sources):
    """Undo granularity, stated as a test. Each `create_part` is its own
    manifest write and its own `project_changed` (the existing path, one undo
    step per part); everything after them — the provenance keys and all seven
    instances — lands under a SINGLE trailing event, so the batch is one more
    undo step and never one per occurrence."""
    service, registry = _project(kernel, tmp_path)
    events: list[dict] = []
    inner = service.bus.publish

    def spy(event):
        events.append(event)
        return inner(event)

    service.bus.publish = spy
    _import(registry, source=str(sources["assembly"]))
    changes = [e for e in events if e.get("type") == "project_changed"]
    assert [e.get("part") for e in changes] == ["bracket", "pin", "ball", None]


# ------------------------------------------------------------- provenance


def test_provenance_loose_keys_survive_the_manifest_round_trip(structured):
    """`source_label`/`import_source` are loose keys (design §7), so the
    assertion that matters is that they are on disk and that `PartRecord`
    still loads the entry that carries them."""
    service, result = structured
    manifest = service.store.manifest("demo")   # re-read from disk
    entries = _by_id(manifest["parts"])
    assert entries["bracket"]["source_label"] == "Bracket"
    assert entries["bracket"]["import_source"] == "assembly.step"
    assert entries["ball"]["source_label"] == "Ball"
    # no timestamp, no absolute path anywhere in the provenance
    assert "/" not in entries["pin"]["import_source"]
    # the record loads (unknown keys are ignored, never a schema error)
    assert service.store.get_part("demo", "bracket").kind == "reference"
    assert len(service.get_project("demo")["parts"]) == 3
    # and the result reports the same pair the manifest holds
    assert _by_id(result["parts"])["pin"]["source_label"] == "Pin"


def test_provenance_keys_merge_per_field_like_every_other_part_scalar():
    """They ride the part-entry merge: a disjoint edit is clean, a divergent
    one conflicts at its own key rather than on the whole entry."""
    def manifest(**over):
        entry = {"id": "bracket", "label": "Bracket", "material": "al6061",
                 "params": {}, "kind": "reference", "source": "a.brep",
                 "source_label": "Bracket", "import_source": "assembly.step"}
        entry.update(over)
        return {"schema_version": 1, "name": "p", "units": "mm",
                "parts": [entry], "assembly": {"instances": []}}

    base, ours, theirs = manifest(), manifest(label="Frame"), manifest(
        source_label="Bracket Weldment")
    merged, conflicts = merge_manifests(base, ours, theirs)
    assert conflicts == []
    assert merged["parts"][0]["label"] == "Frame"
    assert merged["parts"][0]["source_label"] == "Bracket Weldment"

    base, ours, theirs = (manifest(), manifest(source_label="A"),
                          manifest(source_label="B"))
    merged, conflicts = merge_manifests(base, ours, theirs)
    assert [c["key"] for c in conflicts] == ["parts.bracket.source_label"]

    # an OLD manifest — no loose keys at all — merges exactly as it always did
    def bare():
        doc = manifest()
        doc["parts"][0].pop("source_label")
        doc["parts"][0].pop("import_source")
        return doc

    merged, conflicts = merge_manifests(bare(), bare(), bare())
    assert conflicts == []
    assert "source_label" not in merged["parts"][0]


# ------------------------------------------------------- the auto-detect


def test_agentcad_own_multi_solid_export_stays_flat(kernel, tmp_path, sources):
    """A two-solid part exported by us and re-imported is ONE part with two
    solids — today's behaviour, byte for byte. OCCT presents it as two
    anonymous occurrences, so this is the case that makes the auto-detect a
    judgement about *names* rather than a count."""
    _service, registry = _project(kernel, tmp_path)
    result = _import(registry, source=str(sources["compound"]),
                     part_id="widget")
    assert set(result) == {"part", "imported", "warnings", "fidelity"}
    assert result["part"]["metrics"]["n_solids"] == 2
    assert result["part"]["metrics"]["volume_mm3"] == pytest.approx(2000.0,
                                                                   rel=1e-3)
    assert result["fidelity"]["structure"] == "flat"


def test_single_product_file_is_flat_by_default(kernel, tmp_path, sources):
    service, registry = _project(kernel, tmp_path)
    result = _import(registry, source=str(sources["solo"]), part_id="solo")
    assert result["part"]["id"] == "solo"
    assert result["imported"]["mesh_only"] is False
    assert service.store.instances("demo") == []


def test_single_product_file_honours_an_explicit_structured_true(
        kernel, tmp_path, sources):
    """FR9: `structured: true` on a one-product file is honoured — 1 part, 1
    instance — rather than refused for having nothing to flatten."""
    _service, registry = _project(kernel, tmp_path)
    result = _import(registry, source=str(sources["solo"]), structured=True)
    assert len(result["parts"]) == 1
    assert len(result["instances"]) == 1
    assert result["instances"][0]["part"] == result["parts"][0]["id"]


def test_structured_false_forces_one_blob(kernel, tmp_path, sources):
    """The whole assembly as a single reference part: no instances, no tree."""
    service, registry = _project(kernel, tmp_path)
    result = _import(registry, source=str(sources["assembly"]),
                     structured=False, part_id="whole")
    assert "parts" not in result and result["part"]["id"] == "whole"
    assert service.store.instances("demo") == []
    assert service.get_project("demo")["parts"][0]["source"] == "assembly.step"
    assert result["fidelity"]["structure"] == "flat"


def test_an_unreadable_step_falls_back_to_flat_with_the_reason(
        kernel, tmp_path):
    """Auto-detection is a convenience, not the import: a file the walk cannot
    read still lands the way it always did, and says why."""
    service, registry = _project(kernel, tmp_path)
    junk = service.store.imports_dir("demo", write=True) / "junk.step"
    junk.write_bytes(b"this is not a STEP file\n" * 40)
    result = registry.call("import_cad_file", {
        "project": "demo", "source": "junk.step", "part_id": "junk"})
    assert "error" not in result, result
    assert any("could not inspect" in w for w in result["warnings"]), result
    assert result["part"]["id"] == "junk"


# ------------------------------------------------------------- id policy


def test_part_id_is_required_only_for_a_flat_import(kernel, tmp_path, sources):
    _service, registry = _project(kernel, tmp_path)
    result = registry.call("import_cad_file", {
        "project": "demo", "source": str(sources["solo"])})
    assert result["error"]["type"] == "validation_error"
    assert "part_id" in result["error"]["message"]
    # ...while the structured landing derives its own and needs none
    assert "error" not in registry.call("import_cad_file", {
        "project": "demo", "source": str(sources["assembly"])})


def test_a_part_id_passed_to_a_structured_import_is_reported_as_ignored(
        kernel, tmp_path, sources):
    """The browser's flow always sends one. Silently dropping it would be the
    dishonest option; refusing would break the old caller."""
    _service, registry = _project(kernel, tmp_path)
    result = _import(registry, source=str(sources["assembly"]),
                     part_id="whole", label="Whole")
    assert sorted(p["id"] for p in result["parts"]) == ["ball", "bracket",
                                                        "pin"]
    assert any("part_id, label ignored" in w for w in result["warnings"]), \
        result["warnings"]


def test_prefix_is_prepended_to_the_generated_part_ids(kernel, tmp_path,
                                                       sources):
    _service, registry = _project(kernel, tmp_path)
    result = _import(registry, source=str(sources["assembly"]), prefix="v2")
    assert sorted(p["id"] for p in result["parts"]) == ["v2_ball", "v2_bracket",
                                                        "v2_pin"]
    assert {i["part"] for i in result["instances"]} == {"v2_ball", "v2_bracket",
                                                        "v2_pin"}
    # the label is still the file's own name for the product
    assert sorted(p["label"] for p in result["parts"]) == ["Ball", "Bracket",
                                                           "Pin"]


def test_products_that_share_a_name_are_suffixed_within_one_import(
        kernel, tmp_path, sources):
    """Forced structured on the compound file: two products both called
    ``SOLID`` and two OCCT placeholder occurrence names (`=>[0:1:1:2]`). The
    ids collide inside a single import, and a placeholder is not a name — the
    instance falls back to its product's slug and is numbered from there."""
    _service, registry = _project(kernel, tmp_path)
    result = _import(registry, source=str(sources["compound"]),
                     structured=True)
    assert sorted(p["id"] for p in result["parts"]) == ["solid", "solid_2"]
    assert sorted(i["id"] for i in result["instances"]) == ["solid", "solid_2"]
    assert result["instances"][1]["position"] == pytest.approx([20, 0, 0])


def test_an_unusable_prefix_is_refused(kernel, tmp_path, sources):
    _service, registry = _project(kernel, tmp_path)
    result = registry.call("import_cad_file", {
        "project": "demo", "source": str(sources["assembly"]), "prefix": "!!"})
    assert result["error"]["type"] == "validation_error"
    assert "prefix" in result["error"]["message"]


def test_importing_the_same_file_twice_suffixes_deterministically(
        kernel, tmp_path, sources):
    """`create_part` raises on a duplicate id, so the ids are resolved against
    the project's EXISTING parts before anything is written — a re-import
    lands beside the first set rather than failing halfway through it."""
    service, registry = _project(kernel, tmp_path)
    first = _import(registry, source=str(sources["assembly"]))
    second = _import(registry, source=str(sources["assembly"]))
    assert sorted(p["id"] for p in first["parts"]) == ["ball", "bracket", "pin"]
    assert sorted(p["id"] for p in second["parts"]) == ["ball_2", "bracket_2",
                                                        "pin_2"]
    assert any("suffixed" in w for w in second["warnings"]), second["warnings"]
    # instances too, and the second set points at the second set of parts
    assert {i["id"] for i in second["instances"]} == {
        f"{name}_2" for name in OCCURRENCES}
    assert {i["part"] for i in second["instances"]} == {"ball_2", "bracket_2",
                                                        "pin_2"}
    assert len(service.store.instances("demo")) == 14
    # the products still point at the SAME .brep files (identical bytes,
    # deterministic names) — a re-import writes no new geometry
    assert {p["file"] for p in first["tree"]["products"]} == \
        {p["file"] for p in second["tree"]["products"]}


def test_the_provenance_of_a_re_import_names_the_same_source(kernel, tmp_path,
                                                             sources):
    service, registry = _project(kernel, tmp_path)
    _import(registry, source=str(sources["assembly"]))
    _import(registry, source=str(sources["assembly"]), prefix="v2")
    entries = _by_id(service.store.manifest("demo")["parts"])
    assert entries["v2_pin"]["source_label"] == "Pin"
    assert entries["v2_pin"]["import_source"] == "assembly.step"


# ------------------------------------------------------------------- STL


def test_stl_cannot_be_imported_structurally(kernel, tmp_path, sources):
    """A mesh has no product tree. The refusal is the honest answer to an
    explicit `structured: true`; omitting it imports the file flat."""
    _service, registry = _project(kernel, tmp_path)
    result = registry.call("import_cad_file", {
        "project": "demo", "source": str(sources["blob"]), "structured": True,
        "part_id": "blob"})
    assert result["error"]["type"] == "validation_error"
    assert ".stl" in result["error"]["message"]
    assert result["error"]["details"]["supported"] == [".step", ".stp"]
    # nothing was created by the refusal
    assert registry.call("get_project", {"project": "demo"})["parts"] == []


def test_stl_import_is_flat_and_says_it_is_a_mesh(kernel, tmp_path, sources):
    _service, registry = _project(kernel, tmp_path)
    result = _import(registry, source=str(sources["blob"]), part_id="blob")
    assert result["imported"]["mesh_only"] is True
    assert result["fidelity"] == {"geometry": "mesh", "structure": "flat",
                                  "colors": "none", "pmi": "not_read",
                                  "parametric": "none"}


# -------------------------------------------------------------- fidelity


def test_every_import_result_carries_a_fidelity_block(structured, kernel,
                                                      tmp_path, sources):
    """FR12: the translation is stated, never guessed at."""
    _service, result = structured
    assert result["fidelity"] == {"geometry": "brep", "structure": "tree",
                                  "colors": "per_instance", "pmi": "not_read",
                                  "parametric": "none"}
    _svc, registry = _project(kernel, tmp_path)
    flat = _import(registry, source=str(sources["solo"]), part_id="solo")
    assert set(flat["fidelity"]) == IMPORT_FIDELITY_KEYS
    assert flat["fidelity"]["geometry"] == "brep"
    assert flat["fidelity"]["structure"] == "flat"
    # the honesty line is on every path
    assert flat["fidelity"]["parametric"] == "none"


# --------------------------------------------------------- preview route


@pytest.fixture
def client(kernel, tmp_path):
    service, registry = _project(kernel, tmp_path)
    app = create_app(service, registry, extra_allowed_hosts={"testserver"})
    return service, TestClient(app, base_url="http://127.0.0.1")


def _upload(client, path, name=None):
    response = client.post(f"/api/projects/demo/imports?filename={name or path.name}",
                           content=path.read_bytes())
    assert response.status_code == 200, response.text
    return response.json()["source"]


def test_preview_returns_the_product_tree_and_writes_nothing(client, sources):
    service, http = client
    name = _upload(http, sources["assembly"])
    before = sorted(p.name for p in service.store.imports_dir("demo").iterdir())

    response = http.post(f"/api/projects/demo/imports/{name}/preview")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["counts"] == {"products": 3, "occurrences": 7}
    assert sorted(p["name"] for p in payload["products"]) == ["Ball", "Bracket",
                                                              "Pin"]
    assert len(payload["occurrences"]) == 7
    assert "tree" in payload and "warnings" in payload
    # read-only: no .brep materialization, no parts, no instances
    assert sorted(p.name for p in
                  service.store.imports_dir("demo").iterdir()) == before
    assert service.get_project("demo")["parts"] == []


def test_preview_404s_for_a_file_that_is_not_there(client):
    _service, http = client
    response = http.post("/api/projects/demo/imports/absent.step/preview")
    assert response.status_code == 404
    assert response.json()["error"]["type"] == "NotFoundError"


def test_preview_422s_for_a_format_with_no_product_tree(client, sources):
    _service, http = client
    name = _upload(http, sources["blob"])
    response = http.post(f"/api/projects/demo/imports/{name}/preview")
    assert response.status_code == 422, response.text
    assert response.json()["error"]["type"] == "ValidationError"
    # ...and an unusable filename is refused before anything else
    assert http.post("/api/projects/demo/imports/notcad.txt/preview"
                     ).status_code == 422


def test_preview_502s_when_the_walk_cannot_read_the_file(client):
    """A kernel-class failure keeps the worker's own error type (the
    `routes_drawing` rule), rather than being renamed a bad request."""
    service, http = client
    junk = service.store.imports_dir("demo", write=True) / "junk.step"
    junk.write_bytes(b"this is not a STEP file\n" * 40)
    response = http.post("/api/projects/demo/imports/junk.step/preview")
    assert response.status_code == 502, response.text
    assert response.json()["error"]["type"] in ("contract_error",
                                                "kernel_error")


# ------------------------------------------------------------ hosted mode


def test_the_preview_route_is_not_anonymously_reachable(hosted_client):
    """Default-deny covers a new route with no action by its author — which is
    the property worth a test, since the pack's own module cannot open it."""
    assert security_module.is_public(
        "/api/projects/demo/imports/a.step/preview") is False
    response = hosted_client.post(
        "/api/projects/demo/imports/a.step/preview")
    assert response.status_code == 401
    assert response.json()["error"]["type"] == "AuthError"


def test_the_hosted_host_path_guard_covers_the_structured_path_too(
        hosted, sources, kernel_counter):
    """FR19's refusal is about reading the SERVER's disk, and it has to fire
    before the auto-detect asks the kernel to walk that path."""
    client, _store = hosted
    login(client)
    client.post("/api/projects", json={"name": "demo"})
    before = kernel_counter.calls
    response = client.post("/api/tools/import_cad_file",
                           json={"project": "demo",
                                 "source": str(sources["assembly"])})
    assert response.status_code == 200, response.text
    assert response.json()["error"]["type"] == "authz_error"
    # not one kernel call: the refusal is before the read, not after
    assert kernel_counter.calls == before
    assert client.get("/api/projects/demo").json()["parts"] == []
