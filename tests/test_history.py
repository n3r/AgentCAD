import queue
import threading

import pytest

from agentcad.core.history import HistoryManager
from agentcad.core.model import ConflictError
from agentcad.core.project import ProjectStore
from agentcad.core.service import EventBus


@pytest.fixture
def store(tmp_path):
    store = ProjectStore(tmp_path / "projects")
    store.create("p")
    store.add_part("p", "box", "box", "al6061", "SCRIPT_V1")
    return store


@pytest.fixture
def history(store):
    restored = []
    hm = HistoryManager(store, EventBus(), threading.RLock(), restored.append)
    hm.restored = restored  # test hook
    return hm


def test_undo_restores_script_and_manifest(store, history):
    history.checkpoint("p", "Edit script of box")
    store.write_script("p", "box", "SCRIPT_V2")
    info = history.undo("p")
    assert info["label"] == "Edit script of box"
    assert store.read_script("p", "box") == "SCRIPT_V1"
    assert history.restored == ["p"]


def test_redo_and_redo_cleared_by_new_checkpoint(store, history):
    history.checkpoint("p", "Edit script of box")
    store.write_script("p", "box", "SCRIPT_V2")
    history.undo("p")
    info = history.redo("p")
    assert info["label"] == "Edit script of box"
    assert store.read_script("p", "box") == "SCRIPT_V2"
    history.undo("p")                    # populate the redo stack again
    history.checkpoint("p", "another")   # any new action clears it
    store.write_script("p", "box", "SCRIPT_V3")
    with pytest.raises(ConflictError):
        history.redo("p")


def test_undo_removes_files_created_after_snapshot(store, history):
    history.checkpoint("p", "Add part cube")
    store.add_part("p", "cube", "cube", "al6061", "CUBE")
    history.undo("p")
    assert store.part_ids("p") == ["box"]
    assert not store.script_path("p", "cube").is_file()
    history.redo("p")
    assert store.part_ids("p") == ["box", "cube"]
    assert store.script_path("p", "cube").read_text() == "CUBE"


def test_noop_checkpoints_are_skipped(store, history):
    history.checkpoint("p", "Edit script of box")
    store.write_script("p", "box", "SCRIPT_V2")
    history.checkpoint("p", "failed op")   # op writes nothing
    info = history.undo("p")               # skips the no-op entry
    assert info["label"] == "Edit script of box"
    assert store.read_script("p", "box") == "SCRIPT_V1"


def test_empty_stacks_raise_conflict(store, history):
    with pytest.raises(ConflictError):
        history.undo("p")
    with pytest.raises(ConflictError):
        history.redo("p")


def test_stack_is_bounded(store, history):
    for i in range(60):
        history.checkpoint("p", f"c{i}")
        store.write_script("p", "box", f"SCRIPT_{i}")
    status = history.status("p")
    assert len(status["undo"]) == 50
    assert status["undo"][0] == "c59"      # newest first


def test_undo_publishes_project_changed(store, history):
    q = history.bus.subscribe()
    history.checkpoint("p", "x")
    store.write_script("p", "box", "SCRIPT_V2")
    history.undo("p")
    events = []
    while True:
        try:
            events.append(q.get_nowait())
        except queue.Empty:
            break
    assert {"type": "project_changed", "project": "p"} in events


# ---------------------------------------------------------- service level

from agentcad.core.service import AgentCADService  # noqa: E402

from .conftest import BOX_SCRIPT  # noqa: E402


@pytest.fixture
def service(kernel, tmp_path):
    return AgentCADService(tmp_path / "projects", kernel, EventBus())


@pytest.fixture
def demo(service):
    service.create_project("demo")
    service.create_part("demo", "box", script=BOX_SCRIPT)
    return service


def test_undo_param_change_hits_mesh_cache(demo, monkeypatch):
    demo.set_params("demo", "box", {"size": 20.0})
    calls = {"build": 0}
    original = demo.kernel.request

    def counting(method, params, timeout_s=None, affinity=None):
        if method == "build":
            calls["build"] += 1
        return original(method, params, timeout_s=timeout_s, affinity=affinity)

    monkeypatch.setattr(demo.kernel, "request", counting)
    info = demo.history.undo("demo")
    assert info["label"] == "Change params of box"
    part = demo.get_part("demo", "box")
    assert part["params"] == {}
    assert part["metrics"]["volume_mm3"] == pytest.approx(1000.0, rel=1e-6)
    assert calls["build"] == 0  # restored state rebuilds from the .acm cache

    demo.history.redo("demo")
    part = demo.get_part("demo", "box")
    assert part["params"] == {"size": 20.0}
    assert part["metrics"]["volume_mm3"] == pytest.approx(8000.0, rel=1e-6)
    assert calls["build"] == 0


def test_undo_script_edit(demo):
    demo.update_part("demo", "box", script=BOX_SCRIPT.replace("10.0", "12.0"))
    demo.history.undo("demo")
    assert demo.get_part("demo", "box")["script"] == BOX_SCRIPT


def test_undo_delete_part_restores_script_file(demo):
    demo.delete_part("demo", "box")
    assert demo.store.part_ids("demo") == []
    info = demo.history.undo("demo")
    assert info["label"] == "Delete part box"
    part = demo.get_part("demo", "box")
    assert part["script"] == BOX_SCRIPT
    assert part["metrics"]["volume_mm3"] == pytest.approx(1000.0, rel=1e-6)


def test_undo_create_part(demo):
    demo.create_part("demo", "cube", script=BOX_SCRIPT)
    demo.history.undo("demo")
    assert demo.store.part_ids("demo") == ["box"]
    assert not demo.store.script_path("demo", "cube").is_file()


def test_undo_assembly_edit(demo):
    demo.set_assembly("demo", [
        {"id": "b1", "part": "box", "position": [0, 0, 0], "rotation_deg": [0, 0, 0]},
    ])
    demo.set_assembly("demo", [
        {"id": "b1", "part": "box", "position": [5, 0, 0], "rotation_deg": [0, 0, 0]},
    ])
    info = demo.history.undo("demo")
    assert info["label"] == "Edit assembly"
    assert demo.store.instances("demo")[0].position == [0.0, 0.0, 0.0]


def test_failed_mutation_leaves_no_undo_step(demo):
    demo.set_params("demo", "box", {"size": 20.0})
    with pytest.raises(Exception):
        demo.set_assembly("demo", [
            {"id": "x", "part": "ghost", "position": [0, 0, 0], "rotation_deg": [0, 0, 0]},
        ])
    info = demo.history.undo("demo")   # skips the failed set_assembly checkpoint
    assert info["label"] == "Change params of box"
    assert demo.get_part("demo", "box")["params"] == {}


# ------------------------------------------------------------- HTTP layer

from fastapi.testclient import TestClient  # noqa: E402

from agentcad.core.tools import build_registry  # noqa: E402
from agentcad.server.app import create_app  # noqa: E402


@pytest.fixture
def client(demo):
    app = create_app(
        demo, build_registry(demo), extra_allowed_hosts={"testserver"}
    )
    return TestClient(app, base_url="http://127.0.0.1")


def test_undo_instance_move_via_route(client, demo):
    demo.set_assembly("demo", [
        {"id": "b1", "part": "box", "position": [0, 0, 0], "rotation_deg": [0, 0, 0]},
    ])
    r = client.patch(
        "/api/projects/demo/assembly/instances/b1",
        json={"position": [7.0, 0.0, 0.0], "rotation_deg": [0.0, 0.0, 0.0]},
    )
    assert r.status_code == 200
    info = demo.history.undo("demo")
    assert info["label"] == "Move b1"
    assert demo.store.instances("demo")[0].position == [0.0, 0.0, 0.0]


def test_undo_redo_routes(client):
    r = client.patch("/api/projects/demo/parts/box/params", json={"size": 20.0})
    assert r.json()["ok"] is True

    r = client.post("/api/projects/demo/undo")
    assert r.status_code == 200
    body = r.json()
    assert body["undone"] == "Change params of box"
    assert body["project"]["parts"][0]["params"] == {}
    assert body["history"]["redo"] == 1

    hist = client.get("/api/projects/demo/history").json()
    assert hist["redo"] == ["Change params of box"]

    r = client.post("/api/projects/demo/redo")
    assert r.status_code == 200
    assert r.json()["redone"] == "Change params of box"
    assert r.json()["project"]["parts"][0]["params"] == {"size": 20.0}


def test_undo_route_conflict_when_empty(client):
    client.post("/api/projects/demo/undo")        # undo "Add part box"
    r = client.post("/api/projects/demo/undo")    # nothing left
    assert r.status_code == 409
    assert r.json()["error"]["type"] == "ConflictError"


def test_undo_tools(demo):
    registry = build_registry(demo)
    demo.set_params("demo", "box", {"size": 20.0})
    result = registry.call("undo", {"project": "demo"})
    assert result["undone"] == "Change params of box"
    assert registry.call("get_history", {"project": "demo"})["redo"] == [
        "Change params of box"
    ]
    assert registry.call("redo", {"project": "demo"})["redone"] == (
        "Change params of box"
    )
    demo.history.undo("demo")
    demo.history.undo("demo")
    assert "error" in registry.call("undo", {"project": "demo"})
