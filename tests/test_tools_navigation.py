"""PRD-027 slice 4 — bulk operations and grouped undo (FR5, design §4).

Three layers, bottom up:

* ``ProjectStore.remove_parts`` — the multi-part delete: per-item validation,
  ONE ``save_manifest``, scripts unlinked after it, and (with ``force``) the
  referencing instances dropped in the same write.
* ``navigation.BulkExecutor`` — the six ops, per-item partial success,
  op-level argument refusals *before any write*, and the single
  ``project_changed`` publish that makes a bulk gesture **one** undo step.
* the ``bulk_part_op`` tool — the agent surface, its schema, and AC4: six
  parts, one material change, one history entry, one ``undo`` that puts all
  six back.

The load-bearing assertions here are the counting ones — one save, one
``project_changed``, one undo entry — because every one of them is a thing
that silently degrades into N of itself the moment the implementation
composes per-part calls.
"""

import json
import shutil

import pytest

from agentcad.core import locks
from agentcad.core.model import ConflictError, NotFoundError, ValidationError
from agentcad.core.navigation import (
    MAX_BULK,
    MAX_BULK_EXPORT,
    OPS,
    BulkExecutor,
)
from agentcad.core.service import AgentCADService, EventBus
from agentcad.core.tools import build_registry

from .conftest import BOX_SCRIPT, make_test_service


# --------------------------------------------------------------- fixtures

def _add(service, proj, part_id, **kw):
    """A part straight into the manifest — no build, no publish."""
    return service.store.add_part(proj, part_id, kw.pop("label", part_id),
                                  kw.pop("material", "al6061"), BOX_SCRIPT)


@pytest.fixture
def demo(kernel, tmp_path):
    """Six unbuilt parts `a`..`f` plus a registry."""
    service = make_test_service(tmp_path / "projects", kernel)
    service.create_project("demo")
    for part_id in ("a", "b", "c", "d", "e", "f"):
        _add(service, "demo", part_id)
    return service, build_registry(service)


def manifest_path(service, proj="demo"):
    return service.store.canonical_path_of(proj) / "project.json"


def raw(service, proj="demo"):
    return json.loads(manifest_path(service, proj).read_text(encoding="utf-8"))


def drain(q):
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    return events


def counting_save(service):
    """Replace ``save_manifest`` with a counter; returns the list of calls."""
    saves = []
    real = service.store.save_manifest

    def save(proj, manifest):
        saves.append(proj)
        return real(proj, manifest)

    service.store.save_manifest = save
    return saves


def place(service, inst_id, part, proj="demo", **extra):
    """Append one assembly instance by editing the manifest directly."""
    manifest = service.store.manifest(proj)
    manifest["assembly"]["instances"].append(
        {"id": inst_id, "part": part, "position": [0, 0, 0],
         "rotation_deg": [0, 0, 0], **extra})
    service.store.save_manifest(proj, manifest)


# --------------------------------------------------------- store.remove_parts

def test_remove_parts_removes_many_in_one_save(demo):
    service, _ = demo
    saves = counting_save(service)
    result = service.store.remove_parts("demo", ["a", "b"])
    assert result["removed"] == ["a", "b"] and result["errors"] == {}
    assert len(saves) == 1                       # one RMW = one undo step
    assert [p["id"] for p in raw(service)["parts"]] == ["c", "d", "e", "f"]
    for part_id in ("a", "b"):
        assert not service.store.script_path("demo", part_id).exists()
    assert service.store.script_path("demo", "c").is_file()


def test_remove_parts_deduplicates_keeping_order(demo):
    service, _ = demo
    result = service.store.remove_parts("demo", ["b", "a", "b"])
    assert result["removed"] == ["b", "a"]


def test_remove_parts_reports_a_missing_id_per_item(demo):
    service, _ = demo
    result = service.store.remove_parts("demo", ["a", "ghost"])
    assert result["removed"] == ["a"]
    assert result["errors"]["ghost"]["type"] == "notfound_error"
    assert "ghost" in result["errors"]["ghost"]["message"]


def test_remove_parts_refuses_a_used_part_per_item_with_the_instances(demo):
    service, _ = demo
    place(service, "i1", "a")
    result = service.store.remove_parts("demo", ["a", "b"])
    assert result["removed"] == ["b"]
    error = result["errors"]["a"]
    assert error["type"] == "conflict_error"
    assert error["details"]["instances"] == ["i1"]
    assert [p["id"] for p in raw(service)["parts"]] == ["a", "c", "d", "e", "f"]


def test_remove_parts_force_drops_the_instances_in_the_same_write(demo):
    service, _ = demo
    place(service, "i1", "a")
    place(service, "i2", "a")
    place(service, "i3", "b")
    saves = counting_save(service)
    result = service.store.remove_parts("demo", ["a"], force=True)
    assert result["removed"] == ["a"]
    assert result["instances_removed"] == {"a": ["i1", "i2"]}
    assert len(saves) == 1                       # parts AND instances, one write
    body = raw(service)
    assert [i["id"] for i in body["assembly"]["instances"]] == ["i3"]
    assert [p["id"] for p in body["parts"]] == ["b", "c", "d", "e", "f"]


def test_remove_parts_force_refuses_when_a_survivor_mates_to_a_dropped_one(demo):
    """The store's own invariant: a dangling mate makes the assembly unreadable."""
    service, _ = demo
    place(service, "anchor", "a")
    place(service, "hanger", "b",
          mate={"to_instance": "anchor", "from_connector": "x",
                "to_connector": "y"})
    before = manifest_path(service).read_bytes()
    result = service.store.remove_parts("demo", ["a"], force=True)
    assert result["removed"] == []
    error = result["errors"]["a"]
    assert error["type"] == "conflict_error"
    assert error["details"]["instances"] == ["anchor"]
    assert error["details"]["referenced_by"] == ["hanger"]
    assert manifest_path(service).read_bytes() == before


def test_remove_parts_force_drops_both_sides_of_a_mate_together(demo):
    service, _ = demo
    place(service, "anchor", "a")
    place(service, "hanger", "b",
          mate={"to_instance": "anchor", "from_connector": "x",
                "to_connector": "y"})
    result = service.store.remove_parts("demo", ["a", "b"], force=True)
    assert result["removed"] == ["a", "b"]
    assert raw(service)["assembly"]["instances"] == []


def test_remove_parts_force_refuses_when_the_interface_exports_the_instance(demo):
    service, _ = demo
    place(service, "i1", "a")
    manifest = service.store.manifest("demo")
    manifest["assembly"]["interface"] = {"top": {"instance": "i1",
                                                 "connector": "c"}}
    service.store.save_manifest("demo", manifest)
    result = service.store.remove_parts("demo", ["a"], force=True)
    assert result["removed"] == []
    assert result["errors"]["a"]["details"]["referenced_by"] == \
        ["interface:top"]


def test_remove_parts_writes_nothing_when_every_id_errors(demo):
    service, _ = demo
    before = manifest_path(service).read_bytes()
    saves = counting_save(service)
    result = service.store.remove_parts("demo", ["ghost", "phantom"])
    assert result["removed"] == [] and len(result["errors"]) == 2
    assert saves == []
    assert manifest_path(service).read_bytes() == before


def test_remove_parts_refuses_a_non_list(demo):
    service, _ = demo
    with pytest.raises(ValidationError):
        service.store.remove_parts("demo", "a")


# ------------------------------------------------------- executor: plumbing

def test_ops_are_the_documented_six():
    assert OPS == ("material", "tag", "untag", "folder", "export", "delete")


def test_an_unknown_op_lists_the_ops(demo):
    service, _ = demo
    with pytest.raises(ValidationError) as exc:
        BulkExecutor(service).run("demo", ["a"], "rename", {})
    for op in OPS:
        assert op in exc.value.message


def test_ids_are_deduplicated_keeping_order(demo):
    service, _ = demo
    result = BulkExecutor(service).run(
        "demo", ["b", "a", "b"], "folder", {"folder": "X"})
    assert [row["id"] for row in result["results"]] == ["b", "a"]
    assert result["applied"] == 2


def test_an_empty_id_list_refuses(demo):
    service, _ = demo
    with pytest.raises(ValidationError):
        BulkExecutor(service).run("demo", [], "folder", {"folder": None})


def test_more_than_max_bulk_refuses(demo):
    service, _ = demo
    ids = [f"p{n}" for n in range(MAX_BULK + 1)]
    with pytest.raises(ValidationError) as exc:
        BulkExecutor(service).run("demo", ids, "folder", {"folder": None})
    assert str(MAX_BULK) in exc.value.message


def test_part_ids_must_be_strings(demo):
    service, _ = demo
    with pytest.raises(ValidationError):
        BulkExecutor(service).run("demo", ["a", 7], "folder", {"folder": None})


# ------------------------------------------------------- executor: material

def test_bulk_material_is_one_write_and_one_project_changed(demo):
    service, _ = demo
    q = service.bus.subscribe()
    saves = counting_save(service)
    result = BulkExecutor(service).run(
        "demo", ["a", "b", "c", "d", "e", "f"], "material",
        {"material": "steel_a36"})
    assert result["op"] == "material" and result["ok"] is True
    assert result["applied"] == 6
    assert result["undo_label"] == "bulk material ×6"
    assert len(saves) == 1
    changed = [e for e in drain(q) if e["type"] == "project_changed"]
    assert len(changed) == 1                       # N publishes would be N undos
    assert changed[0]["reason"] == "bulk material ×6"
    assert "part" not in changed[0]                # the label must read "bulk …"
    for part_id in ("a", "b", "c", "d", "e", "f"):
        assert service.store.get_part("demo", part_id).material == "steel_a36"


def test_bulk_material_rebuilds_each_part_after_the_publish(demo):
    service, _ = demo
    calls = []
    real = service.rebuild_after_write

    def spy(proj, part_id):
        calls.append(part_id)
        return real(proj, part_id)

    service.rebuild_after_write = spy
    result = BulkExecutor(service).run("demo", ["a", "b"], "material",
                                       {"material": "steel_a36"})
    assert calls == ["a", "b"]
    assert all(row["rebuilt"] is True for row in result["results"])


def test_bulk_material_refuses_an_unknown_material_before_any_write(demo):
    service, _ = demo
    before = manifest_path(service).read_bytes()
    q = service.bus.subscribe()
    with pytest.raises(ValidationError) as exc:
        BulkExecutor(service).run("demo", ["a", "b"], "material",
                                  {"material": "unobtainium"})
    assert "unobtainium" in exc.value.message
    assert manifest_path(service).read_bytes() == before
    assert drain(q) == []


def test_bulk_material_needs_the_material_key(demo):
    service, _ = demo
    with pytest.raises(ValidationError):
        BulkExecutor(service).run("demo", ["a"], "material", {})


def test_bulk_partial_success_applies_the_rest(demo):
    service, _ = demo
    result = BulkExecutor(service).run(
        "demo", ["a", "b", "c", "d", "e", "ghost"], "material",
        {"material": "steel_a36"})
    assert result["ok"] is False
    assert result["applied"] == 5
    assert result["undo_label"] == "bulk material ×5"
    rows = {row["id"]: row for row in result["results"]}
    assert rows["ghost"]["ok"] is False
    assert rows["ghost"]["error"]["type"] == "notfound_error"
    assert all(rows[p]["ok"] for p in ("a", "b", "c", "d", "e"))
    assert service.store.get_part("demo", "a").material == "steel_a36"
    assert service.store.get_part("demo", "f").material == "al6061"


def test_every_id_unknown_writes_and_publishes_nothing(demo):
    service, _ = demo
    q = service.bus.subscribe()
    before = manifest_path(service).read_bytes()
    result = BulkExecutor(service).run("demo", ["ghost"], "folder",
                                       {"folder": "X"})
    assert result["ok"] is False and result["applied"] == 0
    assert result["undo_label"] is None
    assert drain(q) == []
    assert manifest_path(service).read_bytes() == before


# ------------------------------------------------------ executor: tag/untag

def test_bulk_tag_adds_normalized_and_is_idempotent(demo):
    service, _ = demo
    executor = BulkExecutor(service)
    executor.run("demo", ["a", "b"], "tag", {"tags": ["M5", " Printed "]})
    assert service.store.get_part("demo", "a").tags == ["m5", "printed"]
    executor.run("demo", ["a"], "tag", {"tags": ["m5"]})
    assert service.store.get_part("demo", "a").tags == ["m5", "printed"]


def test_bulk_tag_keeps_existing_tags(demo):
    service, _ = demo
    service.store.update_part_meta("demo", "a", tags=["keep"])
    BulkExecutor(service).run("demo", ["a"], "tag", {"tags": ["new"]})
    assert service.store.get_part("demo", "a").tags == ["keep", "new"]


def test_bulk_untag_removes_only_the_named_tags_and_is_idempotent(demo):
    service, _ = demo
    service.store.update_part_meta("demo", "a", tags=["m5", "printed"])
    executor = BulkExecutor(service)
    executor.run("demo", ["a"], "untag", {"tags": ["M5"]})
    assert service.store.get_part("demo", "a").tags == ["printed"]
    executor.run("demo", ["a"], "untag", {"tags": ["m5"]})
    assert service.store.get_part("demo", "a").tags == ["printed"]


def test_bulk_untag_can_clear_the_key(demo):
    service, _ = demo
    service.store.update_part_meta("demo", "a", tags=["m5"])
    BulkExecutor(service).run("demo", ["a"], "untag", {"tags": ["m5"]})
    entry = next(p for p in raw(service)["parts"] if p["id"] == "a")
    assert "tags" not in entry


def test_bulk_tag_refuses_an_invalid_tag_before_any_write(demo):
    service, _ = demo
    before = manifest_path(service).read_bytes()
    with pytest.raises(ValidationError):
        BulkExecutor(service).run("demo", ["a"], "tag", {"tags": ["bad tag"]})
    assert manifest_path(service).read_bytes() == before


def test_bulk_tag_needs_a_non_empty_tag_list(demo):
    service, _ = demo
    for args in ({}, {"tags": []}):
        with pytest.raises(ValidationError):
            BulkExecutor(service).run("demo", ["a"], "tag", args)


def test_bulk_tag_over_the_cap_is_a_per_item_error(demo):
    service, _ = demo
    service.store.update_part_meta(
        "demo", "a", tags=[f"t{n}" for n in range(32)])
    result = BulkExecutor(service).run("demo", ["a", "b"], "tag",
                                       {"tags": ["extra"]})
    rows = {row["id"]: row for row in result["results"]}
    assert rows["a"]["ok"] is False
    assert rows["a"]["error"]["type"] == "validation_error"
    assert rows["b"]["ok"] is True
    assert result["applied"] == 1
    assert service.store.get_part("demo", "b").tags == ["extra"]


# ---------------------------------------------------------- executor: folder

def test_bulk_folder_sets_the_folder_verbatim(demo):
    service, _ = demo
    result = BulkExecutor(service).run("demo", ["a", "b"], "folder",
                                       {"folder": "Chassis/Left side"})
    assert result["undo_label"] == "bulk folder ×2"
    assert service.store.get_part("demo", "a").folder == "Chassis/Left side"


def test_bulk_folder_null_is_root(demo):
    service, _ = demo
    service.store.update_part_meta("demo", "a", folder="Chassis")
    BulkExecutor(service).run("demo", ["a"], "folder", {"folder": None})
    assert service.store.get_part("demo", "a").folder is None
    entry = next(p for p in raw(service)["parts"] if p["id"] == "a")
    assert "folder" not in entry


def test_bulk_folder_requires_the_key(demo):
    """``folder: null`` MEANS root, so an omitted key cannot double as it."""
    service, _ = demo
    with pytest.raises(ValidationError):
        BulkExecutor(service).run("demo", ["a"], "folder", {})


def test_bulk_folder_refuses_a_bad_folder_before_any_write(demo):
    service, _ = demo
    before = manifest_path(service).read_bytes()
    with pytest.raises(ValidationError):
        BulkExecutor(service).run("demo", ["a"], "folder", {"folder": " bad"})
    assert manifest_path(service).read_bytes() == before


# --------------------------------------------------------- executor: events

def test_parts_meta_changed_follows_project_changed(demo):
    service, _ = demo
    q = service.bus.subscribe()
    BulkExecutor(service).run("demo", ["a", "b", "ghost"], "folder",
                              {"folder": "Rack"})
    events = drain(q)
    assert [e["type"] for e in events] == ["project_changed",
                                           "parts_meta_changed"]
    assert events[1]["project"] == "demo"
    assert events[1]["part_ids"] == ["a", "b"]     # only what was written
    assert events[1]["fields"] == ["folder"]


@pytest.mark.parametrize("op,args,fields", [
    ("material", {"material": "steel_a36"}, ["material"]),
    ("tag", {"tags": ["x"]}, ["tags"]),
    ("untag", {"tags": ["x"]}, ["tags"]),
    ("folder", {"folder": "R"}, ["folder"]),
])
def test_every_meta_op_names_its_field(demo, op, args, fields):
    service, _ = demo
    q = service.bus.subscribe()
    BulkExecutor(service).run("demo", ["a"], op, args)
    meta = [e for e in drain(q) if e["type"] == "parts_meta_changed"]
    assert [e["fields"] for e in meta] == [fields]


# ---------------------------------------------------------- executor: claims

def test_a_claim_held_part_refuses_the_whole_bulk(demo):
    """Ruling 5: partial success is for per-item validity, not for colleagues."""
    service, _ = demo

    def guard(proj):
        if locks.current_write_part() == "b":
            raise ConflictError("part 'b' is held by another client")

    service.store.write_guard = guard
    q = service.bus.subscribe()
    before = manifest_path(service).read_bytes()
    with pytest.raises(ConflictError):
        BulkExecutor(service).run("demo", ["a", "b", "c"], "folder",
                                  {"folder": "Rack"})
    assert manifest_path(service).read_bytes() == before
    assert drain(q) == []


# ---------------------------------------------------------- executor: delete

def test_bulk_delete_is_one_publish_with_no_part(demo):
    service, _ = demo
    q = service.bus.subscribe()
    saves = counting_save(service)
    result = BulkExecutor(service).run("demo", ["a", "b"], "delete", {})
    assert result["ok"] is True and result["applied"] == 2
    assert result["undo_label"] == "bulk delete ×2"
    assert len(saves) == 1
    events = drain(q)
    assert [e["type"] for e in events] == ["project_changed"]
    assert "part" not in events[0]
    assert events[0]["reason"] == "bulk delete ×2"
    assert [p["id"] for p in raw(service)["parts"]] == ["c", "d", "e", "f"]


def test_bulk_delete_evicts_the_build_state(demo):
    service, _ = demo
    service.get_part("demo", "a")                   # builds -> _status entry
    key = service._status_key("demo", "a")
    assert key in service._status
    service._config_status[(key[0], "a", "m")] = {"state": "ok"}
    BulkExecutor(service).run("demo", ["a"], "delete", {})
    assert key not in service._status
    assert not [k for k in service._config_status if k[:2] == key]


def test_bulk_delete_refuses_a_used_part_and_force_removes_the_instance(demo):
    service, _ = demo
    place(service, "i1", "a")
    executor = BulkExecutor(service)
    refused = executor.run("demo", ["a"], "delete", {})
    assert refused["ok"] is False and refused["applied"] == 0
    assert refused["undo_label"] is None
    row = refused["results"][0]
    assert row["error"]["type"] == "conflict_error"
    assert row["error"]["details"]["instances"] == ["i1"]

    forced = executor.run("demo", ["a"], "delete", {"force": True})
    assert forced["ok"] is True and forced["applied"] == 1
    assert forced["results"][0]["instances_removed"] == ["i1"]
    assert raw(service)["assembly"]["instances"] == []


def test_bulk_delete_force_must_be_a_boolean(demo):
    service, _ = demo
    with pytest.raises(ValidationError):
        BulkExecutor(service).run("demo", ["a"], "delete", {"force": "yes"})


def test_bulk_delete_publishes_no_parts_meta_changed(demo):
    service, _ = demo
    q = service.bus.subscribe()
    BulkExecutor(service).run("demo", ["a"], "delete", {})
    assert [e["type"] for e in drain(q)] == ["project_changed"]


# ---------------------------------------------------------- executor: export

def test_bulk_export_is_bounded(demo):
    service, _ = demo
    ids = [f"p{n}" for n in range(MAX_BULK_EXPORT + 1)]
    with pytest.raises(ValidationError) as exc:
        BulkExecutor(service).run("demo", ids, "export", {"format": "step"})
    assert str(MAX_BULK_EXPORT) in exc.value.message


def test_bulk_export_refuses_an_unknown_format_before_any_kernel_call(demo):
    service, _ = demo
    with pytest.raises(ValidationError):
        BulkExecutor(service).run("demo", ["a"], "export", {"format": "obj"})


def test_bulk_export_needs_a_format(demo):
    service, _ = demo
    with pytest.raises(ValidationError):
        BulkExecutor(service).run("demo", ["a"], "export", {})


@pytest.mark.integration
def test_bulk_export_returns_paths_and_publishes_nothing(demo):
    service, _ = demo
    q = service.bus.subscribe()
    result = BulkExecutor(service).run("demo", ["a", "b", "ghost"], "export",
                                       {"format": "step"})
    assert result["op"] == "export"
    assert result["undo_label"] is None            # not a mutation, no undo
    assert result["applied"] == 2 and result["ok"] is False
    rows = {row["id"]: row for row in result["results"]}
    assert rows["a"]["path"].endswith("a.step")
    assert rows["ghost"]["error"]["type"] == "notfound_error"
    assert [e["type"] for e in drain(q)] == []


# --------------------------------------------------------------- the tool

def test_bulk_part_op_is_registered_with_the_documented_schema(demo):
    _, registry = demo
    tool = registry.get("bulk_part_op")
    assert tool is not None
    props = tool.input_schema["properties"]
    assert tool.input_schema["required"] == ["project", "part_ids", "op"]
    assert props["part_ids"]["type"] == "array"
    assert props["part_ids"]["items"]["type"] == "string"
    assert props["args"]["type"] == "object"       # never a JSON type LIST
    for op in OPS:
        assert op in tool.description


def test_the_tool_answers_the_documented_payload(demo):
    _, registry = demo
    result = registry.call("bulk_part_op", {
        "project": "demo", "part_ids": ["a", "b"], "op": "folder",
        "args": {"folder": "Rack"}})
    assert set(result) == {"op", "ok", "applied", "results", "undo_label"}


def test_the_tool_defaults_args_to_empty(demo):
    _, registry = demo
    result = registry.call("bulk_part_op", {
        "project": "demo", "part_ids": ["a"], "op": "delete"})
    assert result["ok"] is True


def test_the_tool_returns_a_refusal_envelope(demo):
    _, registry = demo
    result = registry.call("bulk_part_op", {
        "project": "demo", "part_ids": ["a"], "op": "rename", "args": {}})
    assert result["error"]["type"] == "validation_error"


def test_the_tool_404s_an_unknown_project(demo):
    _, registry = demo
    result = registry.call("bulk_part_op", {
        "project": "nope", "part_ids": ["a"], "op": "delete", "args": {}})
    assert result["error"]["type"] == "notfound_error"


# ------------------------------------------------------------------- AC4

@pytest.mark.integration
@pytest.mark.skipif(shutil.which("git") is None, reason="git not found on PATH")
def test_ac4_a_bulk_material_change_is_one_undo_step(kernel, tmp_path):
    """Six parts, one material change, ONE history entry, one undo."""
    bus = EventBus()
    service = AgentCADService(tmp_path / "projects", kernel, bus)
    registry = build_registry(service)
    assert "error" not in registry.call("create_project", {"name": "demo"})
    parts = ["a", "b", "c", "d", "e", "f"]
    for part_id in parts:
        _add(service, "demo", part_id)
    # One baseline snapshot, so `undo` has a state to go back to.
    assert "error" not in registry.call(
        "set_part_meta", {"project": "demo", "part_id": "a",
                          "folder": "Baseline"})

    before = registry.call("get_history", {"project": "demo"})
    assert before.get("available") is True
    depth = len(before["undo"])

    q = bus.subscribe()
    result = registry.call("bulk_part_op", {
        "project": "demo", "part_ids": parts, "op": "material",
        "args": {"material": "steel_a36"}})
    assert result["ok"] is True and result["applied"] == 6
    assert result["undo_label"] == "bulk material ×6"
    # The rebuilds publish rebuild_* only — never a second project_changed.
    assert len([e for e in drain(q) if e["type"] == "project_changed"]) == 1

    after = registry.call("get_history", {"project": "demo"})
    assert len(after["undo"]) == depth + 1
    assert after["undo"][0] == "project_changed (bulk material ×6)"

    undone = registry.call("undo", {"project": "demo"})
    assert "error" not in undone, undone
    for part_id in parts:
        assert service.store.get_part("demo", part_id).material == "al6061"


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("git") is None, reason="git not found on PATH")
def test_a_bulk_delete_is_one_undo_step_that_restores_the_instance(kernel,
                                                                   tmp_path):
    bus = EventBus()
    service = AgentCADService(tmp_path / "projects", kernel, bus)
    registry = build_registry(service)
    registry.call("create_project", {"name": "demo"})
    for part_id in ("a", "b"):
        _add(service, "demo", part_id)
    place(service, "i1", "a")
    registry.call("set_part_meta", {"project": "demo", "part_id": "b",
                                    "folder": "Baseline"})
    depth = len(registry.call("get_history", {"project": "demo"})["undo"])

    result = registry.call("bulk_part_op", {
        "project": "demo", "part_ids": ["a"], "op": "delete",
        "args": {"force": True}})
    assert result["ok"] is True
    history = registry.call("get_history", {"project": "demo"})
    assert len(history["undo"]) == depth + 1
    assert history["undo"][0] == "project_changed (bulk delete ×1)"

    assert "error" not in registry.call("undo", {"project": "demo"})
    assert service.store.get_part("demo", "a").id == "a"
    assert [i.id for i in service.store.instances("demo")] == ["i1"]


def test_missing_part_raises_notfound_outside_a_bulk(demo):
    """`remove_parts` is per-item; the single-part path still raises."""
    service, _ = demo
    with pytest.raises(NotFoundError):
        service.store.remove_part("demo", "ghost")


# ============================== C2/M16: a refusal must survive serialization
#
# `json.dumps(..., allow_nan=False)` is what Starlette serializes a response
# with, so a NaN echoed into `details` — nested one level down inside a list or
# an object a caller sent, where no schema type check looks — turned an honest
# 200 refusal envelope into an HTTP 500.

NAN = float("nan")
INF = float("inf")

NAN_CALLS = [
    ("set_part_meta", {"project": "demo", "part_id": "a", "tags": [NAN]}),
    ("set_part_meta", {"project": "demo", "part_id": "a", "tags": ["ok", INF]}),
    ("bulk_part_op", {"project": "demo", "part_ids": [NAN], "op": "tag",
                      "args": {"tags": ["x"]}}),
    ("bulk_part_op", {"project": "demo", "part_ids": ["a"], "op": "material",
                      "args": {"material": NAN}}),
    ("bulk_part_op", {"project": "demo", "part_ids": ["a"], "op": "folder",
                      "args": {"folder": NAN}}),
    ("bulk_part_op", {"project": "demo", "part_ids": ["a"], "op": "tag",
                      "args": {"tags": [NAN]}}),
    ("bulk_part_op", {"project": "demo", "part_ids": ["a"], "op": "delete",
                      "args": {"force": NAN}}),
    ("bulk_part_op", {"project": "demo", "part_ids": ["a"], "op": "export",
                      "args": {"format": "step", "tolerance": NAN}}),
    ("bulk_part_op", {"project": "demo", "part_ids": ["a"], "op": "export",
                      "args": {"format": "step", "tolerance": INF}}),
    ("bulk_part_op", {"project": "demo", "part_ids": ["a"], "op": "export",
                      "args": {"format": "step", "tolerance": 0}}),
    ("bulk_part_op", {"project": "demo", "part_ids": ["a"], "op": "export",
                      "args": {"format": "step", "tolerance": -1.0}}),
    ("search_parts", {"project": "demo", "query": "", "filters": {"tag": INF}}),
    ("search_parts", {"project": "demo", "query": "",
                      "filters": {"folder": [NAN]}}),
]


@pytest.mark.parametrize("name,args", NAN_CALLS,
                         ids=[f"{n}-{i}" for i, (n, _) in enumerate(NAN_CALLS)])
def test_a_non_finite_number_is_a_refusal_not_a_500(demo, name, args):
    _, registry = demo
    result = registry.call(name, args)
    assert result.get("error", {}).get("type") == "validation_error", result
    # The exact serialization Starlette performs on the way out.
    json.dumps(result, allow_nan=False)


def test_a_refusal_caps_the_value_it_echoes(demo):
    """M16: an echo is an amplifier — a caller who sends 1 MB of tag must not
    get 1 MB back in the message AND another megabyte in `details`."""
    _, registry = demo
    huge = "x" * 1_000_000
    result = registry.call("set_part_meta", {"project": "demo", "part_id": "a",
                                             "tags": [huge]})
    error = result["error"]
    assert error["type"] == "validation_error"
    assert len(json.dumps(error)) < 2_000
    assert len(error["details"]["tag"]) <= 201


def test_a_refusal_caps_the_object_it_echoes(demo):
    """The same cap one level down: `{"args": args}` is a caller's object, and
    a bulk refusal echoed the whole thing back."""
    _, registry = demo
    result = registry.call("bulk_part_op", {
        "project": "demo", "part_ids": ["a"], "op": "material",
        "args": {"material": ["u" * 1_000_000] * 50}})
    assert result["error"]["type"] == "validation_error"
    assert len(json.dumps(result["error"])) < 6_000
    # capped by ITEMS as well as by characters
    echoed = result["error"]["details"]["args"]["material"]
    assert len(echoed) == 21 and echoed[-1] == "\u2026 and 30 more"


def test_safe_caps_a_value_an_id_list_and_a_non_finite_float():
    """M16/C2 at the unit: the one helper both modules echo through."""
    from agentcad.core.navigation import _safe

    assert _safe("x" * 1000) == "x" * 200 + "\u2026"
    capped = _safe([f"p{i}" for i in range(400)])
    assert len(capped) == 21 and capped[-1] == "\u2026 and 380 more"
    assert _safe(float("nan")) == "nan"
    assert _safe(float("inf")) == "inf"
    assert _safe({"tags": [float("-inf")]}) == {"tags": ["-inf"]}
    assert _safe(5) == 5 and _safe(True) is True and _safe(None) is None


# ================================ X1: a bulk delete respects a colleague's claim

def test_bulk_delete_refuses_the_whole_gesture_when_a_part_is_claimed(demo):
    """X1: `remove_parts` saves the manifest outside any `write_scope`, so the
    guard `save_manifest` calls saw no part and a PRD-008 claim never refused —
    a bulk delete walked straight through another human's held part. Ruling 5
    is the rule for every op: a colleague refuses the WHOLE call, before any
    write."""
    service, _ = demo

    def guard(proj):
        if locks.current_write_part() == "b":
            raise ConflictError("b is claimed by someone else")

    service.store.write_guard = guard
    before = manifest_path(service).read_bytes()
    with pytest.raises(ConflictError):
        BulkExecutor(service).run("demo", ["a", "b"], "delete", {})
    assert manifest_path(service).read_bytes() == before
    assert {p["id"] for p in service.store.manifest("demo")["parts"]} \
        >= {"a", "b"}
    assert service.store.script_path("demo", "a").is_file()


def test_a_claim_on_an_id_that_is_not_in_the_project_does_not_refuse(demo):
    """An unknown id is a per-item row, not a claim: it holds nothing."""
    service, _ = demo

    def guard(proj):
        if locks.current_write_part() == "ghost":
            raise ConflictError("ghost is claimed")

    service.store.write_guard = guard
    out = BulkExecutor(service).run("demo", ["a", "ghost"], "delete", {})
    assert out["applied"] == 1
    assert [row["ok"] for row in out["results"]] == [True, False]


# ============ X6: a row's `ok` is about the WRITE, never about the rebuild

BROKEN_SCRIPT = '''\
from build123d import *

PARAMS = {}

def build(p):
    raise RuntimeError("boom")
'''


def test_a_failed_rebuild_does_not_unsay_a_material_write(demo):
    """X6: the write landed and undo covers it, so the row is `ok: true`; the
    build outcome is `rebuilt: false` plus the build error. Flipping `ok` said
    "this part was not changed" about a part that WAS changed — and the bulk
    bar derived its applied count from that flag."""
    service, _ = demo
    service.store.add_part("demo", "bad", "bad", "al6061", BROKEN_SCRIPT)

    result = BulkExecutor(service).run("demo", ["a", "bad"], "material",
                                       {"material": "steel_a36"})
    rows = {row["id"]: row for row in result["results"]}
    assert rows["a"]["ok"] is True and rows["a"]["rebuilt"] is True
    assert rows["bad"]["ok"] is True, rows["bad"]
    assert rows["bad"]["rebuilt"] is False
    assert rows["bad"]["error"], "the build error is still on the row"
    # the gesture landed, whole: two parts written, one undo step
    assert result["applied"] == 2
    assert result["ok"] is True
    assert service.store.get_part("demo", "bad").material == "steel_a36"
