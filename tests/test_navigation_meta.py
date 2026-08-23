"""PRD-027 slice 1 — navigation metadata (FR1, design §1 and §5).

Covers the folder/tag grammar (`core/navigation.py`), the manifest round-trip
for `PartRecord.folder`/`tags` and `InstanceSpec.folder`, the store's two meta
writes (`update_part_meta`, the single-RMW `update_parts_meta`), the
`set_part_meta` tool with its `project_changed` → `parts_meta_changed` event
pair, and the instance side: every `InstanceSpec(` construction site carries
`folder` through, and the gizmo PATCH accepts one on a mate-driven instance.
"""

import json
import shutil

import pytest

from agentcad.core import locks, mates
from agentcad.core.model import (
    InstanceSpec,
    NotFoundError,
    PartRecord,
    ValidationError,
)
from agentcad.core.navigation import (
    MAX_FOLDER_DEPTH,
    MAX_TAGS,
    folder_matches,
    normalize_folder,
    normalize_tags,
)
from agentcad.core.project import ProjectStore
from agentcad.core.service import AgentCADService, EventBus
from agentcad.core.tools import build_registry

from .conftest import BOX_SCRIPT, make_test_service

# Two parts with real connectors, so a mate resolves and `get_assembly` (which
# the instance PATCH returns) succeeds.
PLATE_WITH_CONNECTOR = '''\
from build123d import *

PARAMS = {"t": {"default": 10.0, "min": 1.0, "max": 50.0}}

def build(p):
    with BuildPart() as part:
        Box(40, 40, p.t)
    return part.part

def connectors(p, part):
    return {"top": {"type": "rigid", "location": ((0, 0, p.t / 2), (0, 0, 0))}}
'''

PIN_WITH_CONNECTOR = '''\
from build123d import *

PARAMS = {"h": {"default": 15.0, "min": 1.0, "max": 50.0}}

def build(p):
    with BuildPart() as part:
        Cylinder(radius=3, height=p.h, align=(Align.CENTER, Align.CENTER, Align.MIN))
    return part.part

def connectors(p, part):
    return {"base": {"type": "rigid", "location": ((0, 0, 0), (0, 0, 0))}}
'''


@pytest.fixture
def demo(kernel, tmp_path):
    """A two-part project plus a registry (so the pack-wrapped `set_assembly`
    from `tools_structure` is the one under test)."""
    service = make_test_service(tmp_path / "projects", kernel)
    service.create_project("demo")
    for part_id in ("cube", "pin"):
        service.create_part("demo", part_id, script=BOX_SCRIPT)
    registry = build_registry(service)
    return service, registry


def manifest_path(service, proj="demo"):
    return service.store.canonical_path_of(proj) / "project.json"


def raw_part(service, part_id, proj="demo"):
    raw = json.loads(manifest_path(service, proj).read_text(encoding="utf-8"))
    return next(p for p in raw["parts"] if p["id"] == part_id)


# --------------------------------------------------------- folder grammar

@pytest.mark.parametrize("value", [
    "Pistons",
    "chassis/left side",
    "a/b/c",
    "A1",
    "x.y-z_w",
    "0",
    "a" * 40,
    "/".join(["seg"] * MAX_FOLDER_DEPTH),
])
def test_valid_folder_is_stored_verbatim(value):
    assert normalize_folder(value) == value


@pytest.mark.parametrize("value", [None, ""])
def test_empty_folder_is_root(value):
    assert normalize_folder(value) is None


@pytest.mark.parametrize("value", [
    "..",            # traversal
    "a/..",
    "a//b",          # empty segment
    "/a",            # leading slash
    "a/",            # trailing slash
    "\\a",           # backslash
    "a\nb",          # control char
    "a\tb",
    " a",            # leading space
    "a ",            # trailing space
    "a/ b",
    "a/b ",
    ".hidden",       # segment must start alphanumeric
    "-x",
    "_x",
    "a" * 41,        # segment too long
    "/".join(["s"] * (MAX_FOLDER_DEPTH + 1)),
    5,
    True,
    ["a"],
])
def test_invalid_folder_is_refused(value):
    with pytest.raises(ValidationError):
        normalize_folder(value)


def test_folder_error_names_the_value():
    with pytest.raises(ValidationError) as exc:
        normalize_folder("bad//path")
    assert exc.value.details.get("folder") == "bad//path"


# ------------------------------------------------------------ tag grammar

def test_tags_are_stripped_lowercased_and_deduped_first_seen():
    assert normalize_tags([" Printed ", "printed", "M5", "m5", "bracket"]) == [
        "printed", "m5", "bracket",
    ]


def test_empty_tag_list_is_empty():
    assert normalize_tags([]) == []


@pytest.mark.parametrize("value", [
    ["m 5"],        # space inside
    ["#tag"],
    ["a:b"],
    ["-x"],         # must start alphanumeric
    ["_x"],
    [""],
    ["   "],
    ["x" * 33],     # too long
    [5],
    [None],
    ["ok", "bad tag"],
    "notalist",
    None,
    {"a": 1},
])
def test_invalid_tags_are_refused(value):
    with pytest.raises(ValidationError):
        normalize_tags(value)


def test_tag_error_names_the_bad_tag():
    with pytest.raises(ValidationError) as exc:
        normalize_tags(["fine", "NOT OK"])
    assert "NOT OK" in exc.value.message or \
        exc.value.details.get("tag") == "NOT OK"


def test_tag_count_is_capped():
    ok = [f"t{i}" for i in range(MAX_TAGS)]
    assert normalize_tags(ok) == ok
    with pytest.raises(ValidationError):
        normalize_tags(ok + ["overflow"])


# --------------------------------------------------------- folder_matches

@pytest.mark.parametrize("folder,query,expected", [
    ("a/b", "a", True),
    ("a/b", "A/B", True),
    ("a/b", "a/b", True),
    ("a/b", "a/bc", False),      # segment prefix, not string prefix
    ("a/bc", "a/b", False),
    ("a/b", "b", False),
    ("a/b", "a/b/c", False),
    ("a/b", "a/", True),         # a trailing slash is the same query
    ("a/b", "", True),           # the empty prefix matches every folder
    (None, "", True),
    (None, "a", False),
    ("Chassis/Left Side", "chassis", True),
])
def test_folder_matches_is_case_insensitive_segment_prefix(folder, query,
                                                           expected):
    assert folder_matches(folder, query) is expected


@pytest.mark.parametrize("query", [None, 5, ["a"], True])
def test_folder_matches_refuses_a_non_string_query(query):
    # Strict about the QUERY: returning False would silently empty a result
    # set and read as "no matches" rather than "wrong type".
    with pytest.raises(ValidationError):
        folder_matches("a/b", query)


@pytest.mark.parametrize("folder", [5, ["a"], {"x": 1}, True])
def test_folder_matches_is_total_over_a_corrupt_stored_folder(folder):
    # Total over the stored VALUE: a hand-edited/merged manifest must not 500
    # the scan. A non-string folder reads as root.
    assert folder_matches(folder, "a") is False
    assert folder_matches(folder, "") is True


# ---------------------------------------------------- manifest round-trip

def test_bare_part_record_serializes_without_the_new_keys():
    data = PartRecord(id="a", label="A", material="al6061").to_manifest()
    assert "folder" not in data and "tags" not in data


def test_bare_instance_serializes_without_folder():
    assert "folder" not in InstanceSpec(id="i1", part="a").to_manifest()


def test_no_op_meta_write_is_byte_identical(demo):
    service, _ = demo
    path = manifest_path(service)
    before = path.read_bytes()
    service.store.update_part_meta("demo", "cube", tags=[])
    assert path.read_bytes() == before


def test_update_part_meta_round_trips_through_a_fresh_store(demo, tmp_path):
    service, _ = demo
    record = service.store.update_part_meta(
        "demo", "cube", folder="Frames/Left Side", tags=["M5", " Printed "])
    assert record.folder == "Frames/Left Side"
    assert record.tags == ["m5", "printed"]

    fresh = ProjectStore(tmp_path / "projects")
    got = fresh.get_part("demo", "cube")
    assert got.folder == "Frames/Left Side"
    assert got.tags == ["m5", "printed"]
    entry = raw_part(service, "cube")
    assert entry["folder"] == "Frames/Left Side"
    assert entry["tags"] == ["m5", "printed"]


def test_update_part_meta_clears_by_popping_the_keys(demo):
    service, _ = demo
    service.store.update_part_meta("demo", "cube", folder="A", tags=["x"])
    service.store.update_part_meta("demo", "cube", folder=None, tags=[])
    entry = raw_part(service, "cube")
    assert "folder" not in entry and "tags" not in entry
    record = service.store.get_part("demo", "cube")
    assert record.folder is None and record.tags == []


def test_update_part_meta_omitted_fields_are_unchanged(demo):
    service, _ = demo
    service.store.update_part_meta("demo", "cube", folder="A", tags=["x"])
    service.store.update_part_meta("demo", "cube", tags=["y"])
    record = service.store.get_part("demo", "cube")
    assert record.folder == "A" and record.tags == ["y"]


def test_update_part_meta_unknown_part(demo):
    service, _ = demo
    with pytest.raises(NotFoundError):
        service.store.update_part_meta("demo", "ghost", folder="A")


def test_update_part_meta_runs_in_the_parts_write_scope(demo):
    service, _ = demo
    seen = []
    service.store.write_guard = lambda proj: seen.append(
        locks.current_write_part())
    service.store.update_part_meta("demo", "cube", folder="A")
    # ONE guard call, and it names the part (save_manifest runs inside the
    # scope, exactly like update_part_entry) — so a PRD-008 claim refuses.
    assert seen == ["cube"]


# --------------------------------------------------- bulk manifest writes

def test_update_parts_meta_is_one_save_and_one_guard_call_per_id(demo):
    service, _ = demo
    scopes = []
    service.store.write_guard = lambda proj: scopes.append(
        locks.current_write_part())
    saves = []
    real_save = service.store.save_manifest

    def counting_save(proj, manifest):
        saves.append(proj)
        return real_save(proj, manifest)

    service.store.save_manifest = counting_save
    records = service.store.update_parts_meta("demo", {
        "cube": {"folder": "A", "tags": ["X"]},
        "pin": {"material": "steel_1018", "folder": None},
    })
    assert [r.id for r in records] == ["cube", "pin"]
    assert records[0].folder == "A" and records[0].tags == ["x"]
    assert records[1].material == "steel_1018" and records[1].folder is None
    assert len(saves) == 1                      # ONE manifest RMW = one undo step
    assert scopes == ["cube", "pin", None]      # one scoped guard call per id


def test_update_parts_meta_missing_id_writes_nothing(demo):
    service, _ = demo
    path = manifest_path(service)
    before = path.read_bytes()
    with pytest.raises(NotFoundError) as exc:
        service.store.update_parts_meta("demo", {
            "cube": {"folder": "A"},
            "ghost": {"tags": ["x"]},
        })
    assert "ghost" in exc.value.message
    assert path.read_bytes() == before


def test_update_parts_meta_bad_value_writes_nothing(demo):
    service, _ = demo
    path = manifest_path(service)
    before = path.read_bytes()
    with pytest.raises(ValidationError):
        service.store.update_parts_meta("demo", {
            "cube": {"folder": "A"},
            "pin": {"tags": ["not ok"]},
        })
    assert path.read_bytes() == before
    with pytest.raises(ValidationError):
        service.store.update_parts_meta("demo", {
            "cube": {"material": "unobtanium"},
        })
    assert path.read_bytes() == before


@pytest.mark.parametrize("edit", [
    {"tag": ["x"]},          # typo for tags
    {"folders": "A"},        # typo for folder
    {"folder": "A", "label": "Nope"},   # a real part field, but not ours
    {"params": {"size": 5}},
])
def test_update_parts_meta_refuses_an_unknown_edit_key(demo, edit):
    """A typo'd key must not be a silent no-op — for a bulk op that means N
    parts reported changed and none touched."""
    service, _ = demo
    path = manifest_path(service)
    before = path.read_bytes()
    with pytest.raises(ValidationError) as exc:
        service.store.update_parts_meta("demo", {"cube": edit})
    assert exc.value.details.get("allowed") == ["folder", "material", "tags"]
    assert path.read_bytes() == before


def test_update_parts_meta_refuses_a_non_object_edit(demo):
    service, _ = demo
    with pytest.raises(ValidationError):
        service.store.update_parts_meta("demo", {"cube": ["folder", "A"]})
    with pytest.raises(ValidationError):
        service.store.update_parts_meta("demo", [{"cube": {}}])


def test_update_parts_meta_documents_its_serialization_precondition():
    """IMPORTANT-1: the method is an unserialized RMW and says so, naming the
    locks the caller (slice 4) must hold and their order."""
    doc = ProjectStore.update_parts_meta.__doc__
    assert "PRECONDITION" in doc
    assert "manifest_scope(service.store, proj), service._lock" in doc
    # ...and the material obligation (IMPORTANT-2)
    assert "rebuild_after_write" in doc and "_cache_key" in doc


def test_update_parts_meta_refuses_the_whole_bulk_when_the_guard_refuses(demo):
    """A claimed part (PRD-008) refuses the whole op — ruling 5."""
    service, _ = demo
    from agentcad.core.model import ConflictError

    def guard(proj):
        if locks.current_write_part() == "pin":
            raise ConflictError("pin is claimed")

    service.store.write_guard = guard
    path = manifest_path(service)
    before = path.read_bytes()
    with pytest.raises(ConflictError):
        service.store.update_parts_meta("demo", {
            "cube": {"folder": "A"}, "pin": {"folder": "B"}})
    assert path.read_bytes() == before


# ------------------------------------------------------- service exposure

def test_get_project_and_get_part_expose_folder_and_tags(demo):
    service, _ = demo
    service.store.update_part_meta("demo", "cube", folder="Frames", tags=["M5"])
    project = service.get_project("demo")
    by_id = {p["id"]: p for p in project["parts"]}
    assert by_id["cube"]["folder"] == "Frames"
    assert by_id["cube"]["tags"] == ["m5"]
    assert by_id["pin"]["folder"] is None
    assert by_id["pin"]["tags"] == []
    detail = service.get_part("demo", "cube")
    assert detail["folder"] == "Frames" and detail["tags"] == ["m5"]


# ------------------------------------------------------- set_part_meta tool

def test_set_part_meta_publishes_project_changed_then_parts_meta_changed(demo):
    service, registry = demo
    q = service.bus.subscribe()
    result = registry.call("set_part_meta", {
        "project": "demo", "part_id": "cube",
        "folder": "Frames", "tags": ["M5", "printed"]})
    assert result == {"id": "cube", "folder": "Frames",
                      "tags": ["m5", "printed"]}
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    assert [e["type"] for e in events] == [
        "project_changed", "parts_meta_changed"]
    assert events[0]["project"] == "demo"
    assert events[0]["part"] == "cube"
    assert events[0]["reason"] == "meta"
    assert events[1]["project"] == "demo"
    assert events[1]["part_ids"] == ["cube"]
    assert sorted(events[1]["fields"]) == ["folder", "tags"]


def test_set_part_meta_omitted_and_null_semantics(demo):
    service, registry = demo
    registry.call("set_part_meta", {"project": "demo", "part_id": "cube",
                                    "folder": "A", "tags": ["x"]})
    # tags only: folder unchanged
    out = registry.call("set_part_meta", {"project": "demo", "part_id": "cube",
                                          "tags": ["y"]})
    assert out == {"id": "cube", "folder": "A", "tags": ["y"]}
    # explicit null folder = root, tags untouched
    out = registry.call("set_part_meta", {"project": "demo", "part_id": "cube",
                                          "folder": None})
    assert out == {"id": "cube", "folder": None, "tags": ["y"]}
    # "" is root too (the schema declares a plain string type)
    registry.call("set_part_meta", {"project": "demo", "part_id": "cube",
                                    "folder": "B"})
    out = registry.call("set_part_meta", {"project": "demo", "part_id": "cube",
                                          "folder": ""})
    assert out["folder"] is None
    # [] clears
    out = registry.call("set_part_meta", {"project": "demo", "part_id": "cube",
                                          "tags": []})
    assert out["tags"] == []


def test_set_part_meta_with_neither_field_writes_and_publishes_nothing(demo):
    service, registry = demo
    service.store.update_part_meta("demo", "cube", folder="A", tags=["x"])
    path = manifest_path(service)
    before = path.read_bytes()
    q = service.bus.subscribe()
    out = registry.call("set_part_meta", {"project": "demo",
                                          "part_id": "cube"})
    assert out == {"id": "cube", "folder": "A", "tags": ["x"]}
    assert path.read_bytes() == before
    assert q.empty()
    # ...but it is still honest about an unknown part
    missing = registry.call("set_part_meta", {"project": "demo",
                                              "part_id": "ghost"})
    assert missing["error"]["type"] == "notfound_error"


def test_set_part_meta_fields_reports_only_what_was_written(demo):
    service, registry = demo
    q = service.bus.subscribe()
    registry.call("set_part_meta", {"project": "demo", "part_id": "cube",
                                    "tags": ["x"]})
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    assert events[1]["fields"] == ["tags"]


def test_set_part_meta_refusals_are_error_envelopes(demo):
    _, registry = demo
    bad_folder = registry.call("set_part_meta", {
        "project": "demo", "part_id": "cube", "folder": "../etc"})
    assert bad_folder["error"]["type"] == "validation_error"
    bad_tag = registry.call("set_part_meta", {
        "project": "demo", "part_id": "cube", "tags": ["not ok"]})
    assert bad_tag["error"]["type"] == "validation_error"
    missing = registry.call("set_part_meta", {
        "project": "demo", "part_id": "ghost", "folder": "A"})
    assert missing["error"]["type"] == "notfound_error"


def test_navigation_pack_registers_no_gate_provider(demo):
    _, registry = demo
    assert registry.get("set_part_meta") is not None
    # `tools_navigation` sorts at `nav`, BEFORE `tools_proposals`, whose
    # `service.gate_providers = []` is unconditional — a provider registered
    # here would be silently discarded (the `tools_run_checks` load-order
    # trap in AGENTS.md).
    import ast
    import pathlib

    import agentcad.core.tools_navigation as pack
    tree = ast.parse(
        pathlib.Path(pack.__file__).read_text(encoding="utf-8"))
    touched = [n for n in ast.walk(tree)
               if isinstance(n, ast.Attribute) and n.attr == "gate_providers"]
    assert touched == []


@pytest.mark.skipif(shutil.which("git") is None,
                    reason="git not found on PATH")
def test_set_part_meta_is_one_undo_step(kernel, tmp_path):
    bus = EventBus()
    service = AgentCADService(tmp_path / "projects", kernel, bus)
    registry = build_registry(service)
    assert "error" not in registry.call("create_project", {"name": "demo"})
    assert "error" not in registry.call(
        "create_part", {"project": "demo", "part_id": "cube",
                        "script": BOX_SCRIPT})
    before = registry.call("get_history", {"project": "demo"})
    assert before.get("available") is True
    depth = len(before["undo"])
    assert "error" not in registry.call("set_part_meta", {
        "project": "demo", "part_id": "cube", "folder": "Frames",
        "tags": ["m5"]})
    after = registry.call("get_history", {"project": "demo"})
    assert len(after["undo"]) == depth + 1
    assert "error" not in registry.call("undo", {"project": "demo"})
    record = service.store.get_part("demo", "cube")
    assert record.folder is None and record.tags == []


# ------------------------------------------------------------- instances

def test_set_instances_validates_and_round_trips_folder(demo):
    service, _ = demo
    service.store.set_instances("demo", [
        InstanceSpec(id="i1", part="cube", folder="Frames/Left")])
    assert service.store.instances("demo")[0].folder == "Frames/Left"
    with pytest.raises(ValidationError):
        service.store.set_instances("demo", [
            InstanceSpec(id="i1", part="cube", folder="..")])


def test_core_set_assembly_carries_folder(kernel, tmp_path):
    service = make_test_service(tmp_path / "projects", kernel)
    service.create_project("demo")
    service.create_part("demo", "cube", script=BOX_SCRIPT)
    # the CORE set_assembly (no tool packs loaded, so it is unwrapped)
    service.set_assembly("demo", [
        {"id": "i1", "part": "cube", "folder": "Frames"}])
    assert service.store.instances("demo")[0].folder == "Frames"


def test_pack_wrapped_set_assembly_carries_folder(demo):
    service, _ = demo
    service.set_assembly("demo", [
        {"id": "i1", "part": "cube", "folder": "Frames",
         "pattern": {"kind": "linear", "count": 2, "step_mm": 10}}])
    got = service.store.instances("demo")[0]
    assert got.folder == "Frames" and got.pattern["count"] == 2


def test_pattern_members_inherit_the_base_folder(demo):
    service, _ = demo
    service.store.set_instances("demo", [InstanceSpec(
        id="bolt", part="cube", folder="Fasteners",
        pattern={"kind": "linear", "count": 3, "step_mm": 10})])
    flat, _warnings = mates.expand(service, "demo",
                                   service.store.instances("demo"))
    assert [i.id for i in flat] == ["bolt[0]", "bolt[1]", "bolt[2]"]
    assert all(i.folder == "Fasteners" for i in flat)


def test_gizmo_patch_keeps_folder_and_accepts_one(demo):
    from fastapi.testclient import TestClient

    from agentcad.server.app import create_app

    service, registry = demo
    app = create_app(service, registry, extra_allowed_hosts={"testserver"})
    client = TestClient(app, base_url="http://127.0.0.1")
    service.set_assembly("demo", [
        {"id": "i1", "part": "cube", "folder": "Frames"}])
    r = client.patch("/api/projects/demo/assembly/instances/i1",
                     json={"position": [1, 2, 3]})
    assert r.status_code == 200
    got = service.store.instances("demo")[0]
    assert got.folder == "Frames" and got.position == [1.0, 2.0, 3.0]
    r = client.patch("/api/projects/demo/assembly/instances/i1",
                     json={"folder": "Frames/Left"})
    assert r.status_code == 200
    assert service.store.instances("demo")[0].folder == "Frames/Left"
    r = client.patch("/api/projects/demo/assembly/instances/i1",
                     json={"folder": None})
    assert r.status_code == 200
    assert service.store.instances("demo")[0].folder is None
    r = client.patch("/api/projects/demo/assembly/instances/i1",
                     json={"folder": "../etc"})
    assert r.status_code == 422


@pytest.mark.parametrize("body", [["folder"], "position", 5, True])
def test_gizmo_patch_refuses_a_non_object_body(demo, body):
    """`"key" in body` on a JSON string is a SUBSTRING test — a body of
    "position" used to take the mate-refusal branch, and `["folder"]` used to
    subscript a list."""
    from fastapi.testclient import TestClient

    from agentcad.server.app import create_app

    service, registry = demo
    app = create_app(service, registry, extra_allowed_hosts={"testserver"})
    client = TestClient(app, base_url="http://127.0.0.1")
    service.set_assembly("demo", [{"id": "i1", "part": "cube",
                                   "folder": "Frames"}])
    r = client.patch("/api/projects/demo/assembly/instances/i1", json=body)
    assert r.status_code == 422, r.text
    assert "JSON object" in r.text
    # refused before any write
    assert service.store.instances("demo")[0].folder == "Frames"


def test_gizmo_patch_with_an_absent_body_is_a_no_op(demo):
    """The shared reader maps a genuinely absent body to `{}`; every write
    below it is key-guarded, so that is a no-op and not a wipe."""
    from fastapi.testclient import TestClient

    from agentcad.server.app import create_app

    service, registry = demo
    app = create_app(service, registry, extra_allowed_hosts={"testserver"})
    client = TestClient(app, base_url="http://127.0.0.1")
    service.set_assembly("demo", [{"id": "i1", "part": "cube",
                                   "folder": "Frames", "color": "#00ff00"}])
    r = client.patch("/api/projects/demo/assembly/instances/i1")
    assert r.status_code == 200, r.text
    got = service.store.instances("demo")[0]
    assert got.folder == "Frames" and got.color == "#00ff00"


def test_patch_folder_on_a_mate_driven_instance_is_allowed(kernel, tmp_path):
    from fastapi.testclient import TestClient

    from agentcad.server.app import create_app

    service = make_test_service(tmp_path / "projects", kernel)
    service.create_project("demo")
    service.create_part("demo", "plate", script=PLATE_WITH_CONNECTOR)
    service.create_part("demo", "pin", script=PIN_WITH_CONNECTOR)
    registry = build_registry(service)
    app = create_app(service, registry, extra_allowed_hosts={"testserver"})
    client = TestClient(app, base_url="http://127.0.0.1")
    service.set_assembly("demo", [
        {"id": "plate1", "part": "plate"},
        {"id": "pin1", "part": "pin", "folder": "Fasteners",
         "mate": {"connector": "base", "to_instance": "plate1",
                  "to_connector": "top"}},
    ])
    # organizing a mated instance is not a transform (§5)
    r = client.patch("/api/projects/demo/assembly/instances/pin1",
                     json={"folder": "Fasteners/M5"})
    assert r.status_code == 200, r.text
    assert {i.id: i.folder for i in service.store.instances("demo")}["pin1"] \
        == "Fasteners/M5"
    # so is recoloring it — this WIDENS the old refusal, deliberately (§5)
    r = client.patch("/api/projects/demo/assembly/instances/pin1",
                     json={"color": "#ff0000"})
    assert r.status_code == 200, r.text
    # a transform still refuses
    for body in ({"position": [1, 2, 3]}, {"rotation_deg": [0, 0, 90]},
                 {"folder": "X", "position": [1, 2, 3]}):
        r = client.patch("/api/projects/demo/assembly/instances/pin1",
                         json=body)
        assert r.status_code == 409
    # and the refused body wrote nothing
    assert {i.id: i.folder for i in service.store.instances("demo")}["pin1"] \
        == "Fasteners/M5"


# ------------------------------------------- C1: the meta write is serialized

def test_update_part_meta_documents_its_serialization_precondition():
    """C1: the single-part write is the same unserialized RMW its bulk sibling
    is, and it now says so — naming the locks and their order."""
    doc = ProjectStore.update_part_meta.__doc__
    assert "PRECONDITION" in doc
    assert "manifest_scope(service.store, proj), service._lock" in doc


def test_set_part_meta_does_not_lose_a_concurrent_write(demo):
    """C1: `set_part_meta` used to be an unserialized read-modify-write, so a
    `set_params` landing inside its window was silently clobbered — both
    callers were told "ok" and one of the two writes was simply gone.

    Each thread verifies its OWN last write immediately after the call
    returns. Under the fix that can never fail: a competing writer can only
    save a manifest it read *after* this write landed. Without it, one thread's
    stale snapshot overwrites the other's row.
    """
    import threading

    service, registry = demo
    lost: list[str] = []
    sizes = (11.0, 12.0)

    def tag_cube():
        for i in range(200):
            tag = f"t{i}"
            out = registry.call("set_part_meta",
                                {"project": "demo", "part_id": "cube",
                                 "tags": [tag]})
            assert "error" not in out, out
            if raw_part(service, "cube").get("tags") != [tag]:
                lost.append(f"cube tags {tag}")

    def param_pin():
        for i in range(50):
            size = sizes[i % 2]
            service.set_params("demo", "pin", {"size": size})
            if raw_part(service, "pin").get("params", {}).get("size") != size:
                lost.append(f"pin size {size}")

    threads = [threading.Thread(target=tag_cube),
               threading.Thread(target=param_pin)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=180)
    assert not any(t.is_alive() for t in threads)
    # The manifest is still one parseable document...
    doc = json.loads(manifest_path(service).read_text(encoding="utf-8"))
    assert {p["id"] for p in doc["parts"]} == {"cube", "pin"}
    # ...and nothing either thread wrote was silently dropped.
    assert lost == []
