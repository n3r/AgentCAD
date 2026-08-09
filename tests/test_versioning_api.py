"""Versioning tool pack + route pack (PRD-001 slice 3).

Covers registration (including the no-git degradation), argument validation,
the HTTP surface (registry passthroughs — ``merge_conflict`` arrives as an
``{"error": …}`` body at HTTP 200, like /api/tools/*), the two events, and
undo across a merge.
"""

from __future__ import annotations

import shutil

import pytest
from fastapi.testclient import TestClient

from agentcad.core import locks
from agentcad.core.branches import pinned_tree_var
from agentcad.core.service import AgentCADService, EventBus
from agentcad.core.tools import build_registry
from agentcad.server.app import create_app

from .conftest import BOX_SCRIPT

_GIT = [
    pytest.mark.integration,
    pytest.mark.portability,
    pytest.mark.skipif(shutil.which("git") is None, reason="git not found on PATH"),
]

pytestmark = _GIT

BOX_V2_SCRIPT = BOX_SCRIPT.replace(
    "Box(p.size, p.size, p.size)", "Box(p.size, p.size, p.size * 2)"
)
BOX_V3_SCRIPT = BOX_SCRIPT.replace(
    "Box(p.size, p.size, p.size)", "Box(p.size, p.size, p.size * 3)"
)

VERSIONING_TOOLS = [
    "branch_create", "branch_list", "branch_switch", "branch_delete",
    "version_tag", "list_versions", "merge_branch", "resolve_merge",
    "merge_abort", "merge_status",
]


@pytest.fixture(autouse=True)
def _reset_context():
    cid = locks.client_id_var.set("local")
    pin = pinned_tree_var.set(None)
    yield
    locks.client_id_var.reset(cid)
    pinned_tree_var.reset(pin)


@pytest.fixture
def stack(kernel, tmp_path):
    bus = EventBus()
    service = AgentCADService(tmp_path / "projects", kernel, bus)
    registry = build_registry(service)
    return service, registry


@pytest.fixture
def demo(stack):
    service, registry = stack
    assert "error" not in registry.call("create_project", {"name": "demo"})
    assert "error" not in registry.call(
        "create_part", {"project": "demo", "part_id": "box", "script": BOX_SCRIPT})
    return service, registry


@pytest.fixture
def client(stack):
    service, registry = stack
    app = create_app(service, registry, extra_allowed_hosts={"testserver"})
    return service, registry, TestClient(app, base_url="http://127.0.0.1")


# --------------------------------------------------------- 1. registration


def test_every_versioning_tool_is_registered(demo):
    _service, registry = demo
    names = {tool.name for tool in registry.list()}
    assert set(VERSIONING_TOOLS) <= names
    for name in VERSIONING_TOOLS:
        tool = registry.get(name)
        assert tool.input_schema["type"] == "object"
        assert "project" in tool.input_schema["properties"]
        assert "project" in tool.input_schema["required"]
        assert tool.description


def test_tool_descriptions_state_the_ours_theirs_convention(demo):
    _service, registry = demo
    description = registry.get("merge_branch").description
    assert "ours" in description and "theirs" in description
    assert "target" in description and "source" in description
    assert "resolve_merge" in registry.get("merge_branch").description


def test_argument_validation(demo):
    _service, registry = demo
    assert registry.call("branch_create", {"project": "demo", "name": 3})[
        "error"]["type"] == "invalid_arguments"
    assert registry.call("merge_branch", {"project": "demo"})[
        "error"]["type"] == "invalid_arguments"
    assert registry.call("resolve_merge", {"project": "demo", "choices": []})[
        "error"]["type"] == "invalid_arguments"
    assert registry.call("branch_create", {"project": "demo", "name": "x", "junk": 1})[
        "error"]["type"] == "invalid_arguments"


def test_without_git_the_pack_registers_nothing(kernel, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "agentcad.core.history.ProjectHistory.available", lambda self: False
    )
    service = AgentCADService(tmp_path / "projects", kernel, EventBus())
    registry = build_registry(service)
    names = {tool.name for tool in registry.list()}
    assert not (set(VERSIONING_TOOLS) & names)
    assert getattr(service, "branches", None) is None
    assert getattr(service, "merges", None) is None
    assert service.store.branch_resolver is None
    assert service.store.lock_key("anything") == "anything"


# --------------------------------------------------------------- 2. routes


def test_branch_and_version_routes(client):
    service, _registry, http = client
    assert http.post("/api/projects", json={"name": "demo"}).status_code == 201
    assert http.post("/api/projects/demo/parts",
                     json={"id": "box", "script": BOX_SCRIPT}).status_code == 201

    listing = http.get("/api/projects/demo/branches").json()
    assert listing["current"] == "master" and listing["default"] == "master"
    assert listing["you"]

    created = http.post("/api/projects/demo/branches",
                        json={"name": "feat", "from": "master"}).json()
    assert created["created"] == "feat"

    switched = http.post("/api/projects/demo/branches/switch",
                         json={"name": "feat"}).json()
    assert switched["branch"] == "feat"
    assert switched["project"]["name"] == "demo"

    tagged = http.post("/api/projects/demo/versions",
                       json={"name": "v1", "message": "shipped"}).json()
    assert tagged["tag"] == "v1"
    versions = http.get("/api/projects/demo/versions").json()["versions"]
    assert [v["name"] for v in versions] == ["v1"]

    assert http.post("/api/projects/demo/branches/switch",
                     json={"name": "master"}).json()["branch"] == "master"
    deleted = http.delete("/api/projects/demo/branches/feat").json()
    assert deleted["deleted"] == "feat"


def test_a_nested_branch_name_can_be_deleted_over_rest(client):
    """Branch names may contain '/', so the DELETE route takes the rest of
    the path — a single segment would 404 on 'feat/x'."""
    _service, _registry, http = client
    assert http.post("/api/projects", json={"name": "demo"}).status_code == 201
    assert http.post("/api/projects/demo/parts",
                     json={"id": "box", "script": BOX_SCRIPT}).status_code == 201
    created = http.post("/api/projects/demo/branches", json={"name": "feat/x"})
    assert created.json()["created"] == "feat/x"

    response = http.delete("/api/projects/demo/branches/feat/x")
    assert response.status_code == 200, response.text
    assert response.json()["deleted"] == "feat/x"
    assert [b["name"] for b in
            http.get("/api/projects/demo/branches").json()["branches"]] == ["master"]

    # ...and the name is still whitelisted before it can reach git.
    assert http.delete("/api/projects/demo/branches/--help").status_code == 422


def test_unknown_body_keys_are_ignored_and_nulls_are_not_forwarded(client):
    _service, _registry, http = client
    assert http.post("/api/projects", json={"name": "demo"}).status_code == 201
    assert http.post("/api/projects/demo/parts",
                     json={"id": "box", "script": BOX_SCRIPT}).status_code == 201
    response = http.post(
        "/api/projects/demo/branches",
        json={"name": "feat", "from": None, "evil": "rm -rf", "project": "other"},
    )
    assert response.status_code == 200
    assert response.json()["created"] == "feat"


def test_merge_routes_return_conflicts_at_http_200(client):
    service, registry, http = client
    assert http.post("/api/projects", json={"name": "demo"}).status_code == 201
    assert http.post("/api/projects/demo/parts",
                     json={"id": "box", "script": BOX_SCRIPT}).status_code == 201
    service.branches.create("demo", "feat")

    locks.set_client_id("agent_a")
    service.branches.switch("demo", "feat")
    registry.call("update_part_script",
                  {"project": "demo", "part_id": "box", "script": BOX_V2_SCRIPT})
    locks.set_client_id("local")
    registry.call("update_part_script",
                  {"project": "demo", "part_id": "box", "script": BOX_V3_SCRIPT})

    assert http.get("/api/projects/demo/merge").json()["merge"] is None
    response = http.post("/api/projects/demo/merge",
                         json={"source": "feat", "target": "master"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["error"]["type"] == "merge_conflict"
    assert payload["error"]["details"]["conflicts"][0]["path"] == "parts/box.py"

    staged = http.get("/api/projects/demo/merge").json()["merge"]
    assert staged["source"] == "feat"

    resolved = http.post("/api/projects/demo/merge/resolve",
                         json={"choices": {"parts/box.py": {"take": "theirs"}}})
    assert resolved.status_code == 200
    assert "error" not in resolved.json(), resolved.json()
    assert resolved.json()["validation"]["ok"] is True

    assert http.post("/api/projects/demo/merge/abort").json()["aborted"] is False


def test_route_error_mapping(client):
    _service, _registry, http = client
    assert http.post("/api/projects", json={"name": "demo"}).status_code == 201
    assert http.get("/api/projects/ghost/branches").status_code == 404
    bad = http.post("/api/projects/demo/branches", json={"name": "BAD"})
    assert bad.status_code == 422


# --------------------------------------------------------------- 3. events


def test_branch_changed_and_merge_completed_events(demo):
    service, registry = demo
    queue = service.bus.subscribe()
    try:
        service.branches.create("demo", "feat")
        locks.set_client_id("agent_a")
        service.branches.switch("demo", "feat")
        registry.call("update_part_script",
                      {"project": "demo", "part_id": "box", "script": BOX_V2_SCRIPT})
        locks.set_client_id("local")
        result = registry.call("merge_branch", {"project": "demo", "source": "feat"})
        assert "error" not in result, result

        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
    finally:
        service.bus.unsubscribe(queue)

    switched = [e for e in events if e["type"] == "branch_changed"]
    assert switched and switched[-1] == {
        "type": "branch_changed", "project": "demo",
        "client": "agent_a", "branch": "feat",
    }
    completed = [e for e in events if e["type"] == "merge_completed"]
    assert len(completed) == 1
    event = completed[0]
    assert event["project"] == "demo"
    assert event["source"] == "feat" and event["target"] == "master"
    assert event["commit"]
    assert "validation" in event


def test_undo_after_a_fast_forward_restores_the_pre_merge_target(demo):
    """A fast-forward moves the target onto the source's head, whose FIRST
    PARENT is the previous commit on the SOURCE branch — a state the target
    never had. Undo must return the target to where it was."""
    service, registry = demo
    canonical = service.store.canonical_path_of("demo")
    service.branches.create("demo", "feat")
    locks.set_client_id("agent_a")
    service.branches.switch("demo", "feat")
    registry.call("update_part_script",
                  {"project": "demo", "part_id": "box", "script": BOX_V2_SCRIPT})
    registry.call("update_part_script",
                  {"project": "demo", "part_id": "box", "script": BOX_V3_SCRIPT})

    locks.set_client_id("local")
    before = (canonical / "parts" / "box.py").read_bytes()
    head_before = service.history.head(canonical)

    merged = registry.call("merge_branch", {"project": "demo", "source": "feat"})
    assert "error" not in merged, merged
    assert merged["fast_forward"] is True
    assert merged["previous"] == head_before
    assert (canonical / "parts" / "box.py").read_text() == BOX_V3_SCRIPT

    undone = registry.call("undo", {"project": "demo"})
    assert "error" not in undone, undone
    assert (canonical / "parts" / "box.py").read_bytes() == before

    redone = registry.call("redo", {"project": "demo"})
    assert "error" not in redone, redone
    assert (canonical / "parts" / "box.py").read_text() == BOX_V3_SCRIPT


def test_undo_after_a_merge_restores_the_pre_merge_target(demo):
    service, registry = demo
    canonical = service.store.canonical_path_of("demo")
    service.branches.create("demo", "feat")
    locks.set_client_id("agent_a")
    service.branches.switch("demo", "feat")
    registry.call("update_part_script",
                  {"project": "demo", "part_id": "box", "script": BOX_V2_SCRIPT})

    locks.set_client_id("local")
    registry.call("create_part",
                  {"project": "demo", "part_id": "pin", "script": BOX_V3_SCRIPT})
    before = (canonical / "parts" / "box.py").read_bytes()
    head_before = service.history.head(canonical)

    merged = registry.call("merge_branch", {"project": "demo", "source": "feat"})
    assert "error" not in merged, merged
    assert (canonical / "parts" / "box.py").read_bytes() != before
    assert service.undo_cursor.status("demo")["undo"][0].startswith("merge feat")

    undone = registry.call("undo", {"project": "demo"})
    assert "error" not in undone, undone
    assert (canonical / "parts" / "box.py").read_bytes() == before
    assert service.history.head(canonical) != head_before  # linear restore commit
