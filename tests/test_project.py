import json

import pytest

from agentcad.core.model import (
    ConflictError,
    InstanceSpec,
    NotFoundError,
    ValidationError,
)
from agentcad.core.project import ProjectStore
from agentcad.core.templates import DEFAULT_PART_SCRIPT

pytestmark = pytest.mark.portability


@pytest.fixture
def store(tmp_path):
    return ProjectStore(tmp_path / "projects")


def test_create_and_list(store):
    store.create("demo")
    projects = store.list_projects()
    assert [p["name"] for p in projects] == ["demo"]
    assert projects[0]["n_parts"] == 0


def test_create_duplicate_conflicts(store):
    store.create("demo")
    with pytest.raises(ConflictError):
        store.create("demo")


def test_invalid_project_name(store):
    for bad in ("Demo", "1abc", "a b", "x" * 41, ""):
        with pytest.raises(ValidationError):
            store.create(bad)


def test_add_part_writes_script_and_manifest(store):
    store.create("demo")
    store.add_part("demo", "plate", "Plate", "al6061", DEFAULT_PART_SCRIPT)
    assert store.part_ids("demo") == ["plate"]
    assert store.read_script("demo", "plate") == DEFAULT_PART_SCRIPT
    record = store.get_part("demo", "plate")
    assert record.label == "Plate"
    assert record.material == "al6061"


def test_add_part_unknown_material(store):
    store.create("demo")
    with pytest.raises(ValidationError):
        store.add_part("demo", "plate", "Plate", "unobtanium", "x = 1")


def test_params_update_roundtrip(store):
    store.create("demo")
    store.add_part("demo", "plate", "Plate", "al6061", DEFAULT_PART_SCRIPT)
    store.update_part_entry("demo", "plate", params={"length": 100.0})
    assert store.get_part("demo", "plate").params == {"length": 100.0}


def test_set_instances_validates_part_refs(store):
    store.create("demo")
    store.add_part("demo", "plate", "Plate", "al6061", DEFAULT_PART_SCRIPT)
    with pytest.raises(ValidationError):
        store.set_instances(
            "demo", [InstanceSpec(id="i1", part="ghost")]
        )
    store.set_instances(
        "demo",
        [InstanceSpec(id="i1", part="plate", position=[0, 0, 10])],
    )
    instances = store.instances("demo")
    assert instances[0].position == [0.0, 0.0, 10.0]


def test_duplicate_instance_ids_rejected(store):
    store.create("demo")
    store.add_part("demo", "plate", "Plate", "al6061", DEFAULT_PART_SCRIPT)
    with pytest.raises(ValidationError):
        store.set_instances(
            "demo",
            [InstanceSpec(id="i1", part="plate"), InstanceSpec(id="i1", part="plate")],
        )


def test_remove_instanced_part_conflicts(store):
    store.create("demo")
    store.add_part("demo", "plate", "Plate", "al6061", DEFAULT_PART_SCRIPT)
    store.set_instances("demo", [InstanceSpec(id="i1", part="plate")])
    with pytest.raises(ConflictError):
        store.remove_part("demo", "plate")
    store.set_instances("demo", [])
    store.remove_part("demo", "plate")
    assert store.part_ids("demo") == []
    with pytest.raises(NotFoundError):
        store.read_script("demo", "plate")


def test_atomic_manifest_write_preserves_original(store, monkeypatch):
    store.create("demo")
    path = store.path_of("demo") / "project.json"
    original = path.read_text(encoding="utf-8")

    import os as os_module

    def broken_replace(src, dst):
        raise OSError("simulated crash")

    monkeypatch.setattr("agentcad.core.project.os.replace", broken_replace)
    with pytest.raises(OSError):
        store.add_part("demo", "plate", "Plate", "al6061", "x = 1")
    monkeypatch.undo()
    assert json.loads(path.read_text(encoding="utf-8")) == json.loads(original)


def test_open_external_project(store, tmp_path):
    ext = tmp_path / "elsewhere" / "rocket"
    (ext / "parts").mkdir(parents=True)
    (ext / "project.json").write_text(
        json.dumps({"schema_version": 1, "name": "rocket", "parts": []}),
        encoding="utf-8",
    )
    name = store.open(ext)
    assert name == "rocket"
    assert any(p["name"] == "rocket" for p in store.list_projects())


def test_open_non_project_dir_fails(store, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValidationError):
        store.open(empty)


def test_open_name_collision_conflicts(store, tmp_path):
    store.create("demo")
    ext = tmp_path / "other-demo"
    ext.mkdir()
    (ext / "project.json").write_text(json.dumps({"name": "demo", "parts": []}), encoding="utf-8")
    with pytest.raises(ConflictError):
        store.open(ext)
