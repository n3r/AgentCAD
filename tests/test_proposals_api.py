"""Change proposals: tool pack, route pack and events (PRD-002 slice 2).

The surface only — the lifecycle itself is ``tests/test_proposals.py``. Three
sections, mirroring ``tests/test_versioning_api.py``: 1. registration (schemas,
the description contract, argument validation, the no-git degradation and the
pack's load order) · 2. routes (registry passthroughs, where ``merge_conflict``
is the single error type that arrives at HTTP 200) · 3. events
(``proposal_changed`` on the bus and over a real WebSocket).
"""

from __future__ import annotations

import base64
import pkgutil
import shutil

import pytest
from fastapi.testclient import TestClient

import agentcad.core as core_pkg
from agentcad.core import locks
from agentcad.core.branches import pinned_tree_var
from agentcad.core.proposals import ProposalManager
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

PROPOSAL_TOOLS = [
    "proposal_create", "proposal_list", "proposal_get", "proposal_update",
    "proposal_review", "proposal_merge", "proposal_packet", "proposal_render",
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
    service = AgentCADService(tmp_path / "projects", kernel, EventBus())
    registry = build_registry(service)
    return service, registry


@pytest.fixture
def demo(stack):
    service, registry = stack
    assert "error" not in registry.call("create_project", {"name": "demo"})
    assert "error" not in registry.call(
        "create_part", {"project": "demo", "part_id": "box", "script": BOX_SCRIPT})
    service.branches.create("demo", "feat")
    return service, registry


@pytest.fixture
def client(demo):
    service, registry = demo
    app = create_app(service, registry, extra_allowed_hosts={"testserver"})
    return service, registry, TestClient(app, base_url="http://127.0.0.1")


def _create(registry, **kwargs) -> dict:
    args = {"project": "demo", "source": "feat", "title": "Thinner wall"}
    args.update(kwargs)
    result = registry.call("proposal_create", args)
    assert "error" not in result, result
    return result


# --------------------------------------------------------- 1. registration


def test_every_proposal_tool_is_registered(demo):
    _service, registry = demo
    names = {tool.name for tool in registry.list()}
    assert set(PROPOSAL_TOOLS) <= names
    for name in PROPOSAL_TOOLS:
        tool = registry.get(name)
        assert tool.input_schema["type"] == "object"
        assert "project" in tool.input_schema["properties"]
        assert "project" in tool.input_schema["required"]
        assert tool.description


def test_the_pack_installs_the_service_seams(demo):
    service, _registry = demo
    assert isinstance(getattr(service, "proposals", None), ProposalManager)
    assert service.gate_providers == []


def test_tool_descriptions_state_the_conventions(demo):
    """old = target = ours, new = source = theirs (the PRD-001 convention,
    restated), the follow-up tool for a conflict, and the exact reach of
    allow_invalid."""
    _service, registry = demo
    for name in ("proposal_create", "proposal_merge"):
        description = registry.get(name).description.lower()
        assert "old" in description and "new" in description
        assert "target" in description and "source" in description

    merge = registry.get("proposal_merge").description
    assert "resolve_merge" in merge
    assert "allow_invalid" in merge
    # ...and that it reaches the kernel gate ONLY — never the approvals policy.
    assert "approval" in merge


def test_argument_validation(demo):
    _service, registry = demo
    assert registry.call("proposal_create", {"project": "demo"})[
        "error"]["type"] == "invalid_arguments"
    assert registry.call("proposal_get", {"project": "demo", "id": 1})[
        "error"]["type"] == "invalid_arguments"
    assert registry.call(
        "proposal_create",
        {"project": "demo", "source": "feat", "title": "x", "junk": 1},
    )["error"]["type"] == "invalid_arguments"


def test_without_git_the_pack_registers_nothing(kernel, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "agentcad.core.history.ProjectHistory.available", lambda self: False
    )
    service = AgentCADService(tmp_path / "projects", kernel, EventBus())
    registry = build_registry(service)
    names = {tool.name for tool in registry.list()}
    assert not (set(PROPOSAL_TOOLS) & names)
    assert getattr(service, "proposals", None) is None
    assert getattr(service, "gate_providers", None) is None

    app = create_app(service, registry, extra_allowed_hosts={"testserver"})
    http = TestClient(app, base_url="http://127.0.0.1")
    assert http.get("/api/projects/demo/proposals").status_code == 404


def test_the_pack_is_imported_before_the_versioning_pack(demo):
    """``_load_tool_packs`` walks ``pkgutil.iter_modules`` alphabetically, so
    ``service.branches`` does not exist when this pack's ``register()`` runs —
    which is why every branch access is lazy."""
    packs = [info.name for info in pkgutil.iter_modules(core_pkg.__path__)
             if info.name.startswith("tools_")]
    assert packs.index("tools_proposals") < packs.index("tools_versioning")


def test_the_manager_takes_service_branches_lazily(demo):
    """Constructing a manager against a service with no ``branches`` must
    work; only a call that needs git may fail, and it says so."""
    service, registry = demo
    manager = ProposalManager(_NoBranches(service))
    with pytest.raises(Exception) as excinfo:
        manager.create("demo", "feat", title="x")
    assert "git" in str(excinfo.value)
    # ...and the real registry, freshly built, serves a create straight away.
    assert _create(registry)["proposal"]["id"] == "1"


class _NoBranches:
    """A service that never grew ``branches`` (the register()-time state)."""

    def __init__(self, service) -> None:
        self.store = service.store
        self.history = service.history
        self.bus = service.bus


# --------------------------------------------------------------- 2. routes


def test_the_proposal_routes_round_trip(client):
    _service, _registry, http = client

    created = http.post("/api/projects/demo/proposals",
                        json={"source": "feat", "title": "Thinner wall",
                              "description": "mass budget"})
    assert created.status_code == 200, created.text
    payload = created.json()
    assert payload["proposal"]["id"] == "1"
    assert payload["proposal"]["state"] == "open"
    assert payload["proposal"]["target"] == "master"
    assert payload["proposal"]["author"] == "browser"
    assert payload["packet"] is None
    assert [g["name"] for g in payload["gates"]][:2] == ["state", "approvals"]

    listing = http.get("/api/projects/demo/proposals").json()
    assert [p["id"] for p in listing["proposals"]] == ["1"]
    assert listing["counts"]["open"] == 1
    assert http.get("/api/projects/demo/proposals",
                    params={"state": "closed"}).json()["proposals"] == []

    detail = http.get("/api/projects/demo/proposals/1").json()
    assert detail["proposal"]["title"] == "Thinner wall"
    assert [e["action"] for e in detail["audit"]] == ["created"]
    assert detail["packet"] is None

    patched = http.patch("/api/projects/demo/proposals/1",
                         json={"title": "Thin the wall to 1.6"})
    assert patched.status_code == 200
    assert patched.json()["proposal"]["title"] == "Thin the wall to 1.6"

    reviewed = http.post("/api/projects/demo/proposals/1/review",
                         json={"verdict": "comment", "summary": "reading it"})
    assert reviewed.status_code == 200
    assert reviewed.json()["proposal"]["reviews"][-1]["actor_kind"] == "human"

    closed = http.patch("/api/projects/demo/proposals/1",
                        json={"state": "closed"})
    assert closed.json()["proposal"]["state"] == "closed"


def test_unknown_body_keys_are_ignored_and_nulls_are_not_forwarded(client):
    _service, _registry, http = client
    response = http.post(
        "/api/projects/demo/proposals",
        json={"source": "feat", "title": "Thinner wall", "description": None,
              "evil": "rm -rf", "project": "other"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["proposal"]["project"] == "demo"
    assert response.json()["proposal"]["description"] == ""


def test_a_body_without_a_content_length_is_still_read(client):
    """A chunked request carries no ``content-length``: reading the header
    instead of the body turned a real body into "no arguments at all" — a
    review with no verdict rather than a 422."""
    _service, registry, http = client
    pid = _create(registry)["proposal"]["id"]

    def chunks():
        yield b'{"verdict": "approve", '
        yield b'"summary": "the wall thickness argument holds"}'

    response = http.post(
        f"/api/projects/demo/proposals/{pid}/review", content=chunks(),
        headers={"Content-Type": "application/json"})

    assert "content-length" not in response.request.headers
    assert response.status_code == 200, response.text
    assert response.json()["proposal"]["state"] == "approved"
    assert response.json()["proposal"]["reviews"][-1]["summary"].startswith(
        "the wall")


def test_route_error_mapping(client):
    _service, _registry, http = client
    assert http.get("/api/projects/demo/proposals/9").status_code == 404
    assert http.post("/api/projects/demo/proposals",
                     json={"source": "ghost", "title": "x"}).status_code == 404

    assert http.post("/api/projects/demo/proposals",
                     json={"source": "feat", "title": "x"}).status_code == 200
    duplicate = http.post("/api/projects/demo/proposals",
                          json={"source": "feat", "title": "x"})
    assert duplicate.status_code == 409, duplicate.text
    assert duplicate.json()["error"]["details"]["existing_id"] == "1"

    assert http.patch("/api/projects/demo/proposals/1",
                      json={"state": "approved"}).status_code == 422
    assert http.post("/api/projects/demo/proposals/1/review",
                     json={"verdict": "lgtm"}).status_code == 422
    # invalid_arguments is an HTTP error, not a 200 body nobody inspects
    bad = http.post("/api/projects/demo/proposals/1/review",
                    json={"verdict": 3})
    assert bad.status_code == 422, bad.text
    assert bad.json()["error"]["message"]


def test_a_red_gate_is_a_409_and_a_merge_conflict_is_a_200_body(client):
    """The one deliberate 200-with-an-error-body, exactly as for merge_branch —
    the UI renders the conflict list instead of an error page."""
    service, registry, http = client
    locks.set_client_id("agent_a")
    service.branches.switch("demo", "feat")
    assert "error" not in registry.call(
        "update_part_script",
        {"project": "demo", "part_id": "box", "script": BOX_V2_SCRIPT})
    locks.set_client_id("browser")
    assert "error" not in registry.call(
        "update_part_script",
        {"project": "demo", "part_id": "box", "script": BOX_V3_SCRIPT})

    locks.set_client_id("chat:main")
    assert _create(registry)["proposal"]["state"] == "open"
    locks.set_client_id("local")

    # zero approvals: the policy gate refuses before anything is merged
    blocked = http.post("/api/projects/demo/proposals/1/merge", json={})
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["error"]["details"]["failing"] == "approvals"
    assert registry.call("merge_status", {"project": "demo"})["merge"] is None

    approved = http.post("/api/projects/demo/proposals/1/review",
                         json={"verdict": "approve"})
    assert approved.json()["proposal"]["state"] == "approved"

    conflicted = http.post("/api/projects/demo/proposals/1/merge",
                           json={"allow_invalid": False})
    assert conflicted.status_code == 200, conflicted.text
    error = conflicted.json()["error"]
    assert error["type"] == "merge_conflict"
    assert error["details"]["conflicts"][0]["path"] == "parts/box.py"
    assert error["details"]["proposal"] == "1"
    assert http.get("/api/projects/demo/proposals/1").json()[
        "proposal"]["state"] == "approved"


# --------------------------------------- 2b. the packet, render and asset routes


@pytest.fixture
def reviewable(client):
    """A proposal whose source branch really changes the box, so the packet
    has a script diff, metric deltas, renders and a geometric diff."""
    service, registry, http = client
    locks.set_client_id("agent_a")
    service.branches.switch("demo", "feat")
    assert "error" not in registry.call(
        "update_part_script",
        {"project": "demo", "part_id": "box", "script": BOX_V2_SCRIPT})
    locks.set_client_id("chat:main")
    pid = _create(registry)["proposal"]["id"]
    locks.set_client_id("browser")
    return service, registry, http, pid


@pytest.mark.slow
def test_proposal_packet_and_render_tools(reviewable):
    _service, registry, _http, pid = reviewable
    packet = registry.call("proposal_packet", {"project": "demo", "id": pid})
    assert "error" not in packet, packet
    assert packet["parts"][0]["part"] == "box"
    assert packet["parts"][0]["renders"]["new"].endswith("/render/new/box")

    image = registry.call("proposal_render",
                          {"project": "demo", "id": pid, "side": "new",
                           "part": "box"})
    assert "error" not in image, image
    # top level, so mcp_server._tool_result / chat lift it into image content
    assert base64.b64decode(image["png_base64"])[:4] == b"\x89PNG"
    assert (image["width"], image["height"]) == (640, 480)
    assert image["side"] == "new" and image["part"] == "box"

    assert registry.call("proposal_render",
                         {"project": "demo", "id": pid, "side": "sideways"}
                         )["error"]["type"] == "validation_error"


@pytest.mark.slow
def test_the_packet_route_regenerates_only_when_asked(reviewable):
    _service, _registry, http, pid = reviewable
    first = http.get(f"/api/projects/demo/proposals/{pid}/packet")
    assert first.status_code == 200, first.text
    assert first.json()["stale"] is False
    again = http.get(f"/api/projects/demo/proposals/{pid}/packet")
    assert again.json()["generated"] == first.json()["generated"]
    regenerated = http.get(f"/api/projects/demo/proposals/{pid}/packet",
                           params={"regenerate": 1})
    assert regenerated.status_code == 200
    assert regenerated.json()["parts"][0]["geom_diff"]["available"] is True

    detail = http.get(f"/api/projects/demo/proposals/{pid}").json()
    assert detail["packet"] == {"generated": regenerated.json()["generated"],
                                "stale": False, "ok": True, "frozen": False}


@pytest.mark.slow
def test_the_render_and_diff_asset_routes_serve_bytes(reviewable):
    _service, _registry, http, pid = reviewable
    packet = http.get(f"/api/projects/demo/proposals/{pid}/packet").json()

    png = http.get(f"/api/projects/demo/proposals/{pid}/render/old/box")
    assert png.status_code == 200, png.text
    assert png.headers["content-type"] == "image/png"
    assert png.headers["cache-control"] == "no-store"
    assert png.content[:4] == b"\x89PNG"

    assert packet["parts"][0]["geom_diff"]["added_mesh"] == (
        f"/api/projects/demo/proposals/{pid}/diff/box/added.acm")
    mesh = http.get(f"/api/projects/demo/proposals/{pid}/diff/box/added.acm")
    assert mesh.status_code == 200, mesh.text
    assert mesh.headers["content-type"] == "application/octet-stream"
    assert mesh.content[:4] == b"ACM1"

    # nothing was removed, so there is no removed mesh to serve
    assert packet["parts"][0]["geom_diff"]["removed_mesh"] is None
    assert http.get(
        f"/api/projects/demo/proposals/{pid}/diff/box/removed.acm"
    ).status_code == 404
    assert http.get(
        f"/api/projects/demo/proposals/{pid}/diff/box/sideways.acm"
    ).status_code == 422
    assert http.get(
        f"/api/projects/demo/proposals/{pid}/render/new/ghost"
    ).status_code == 404


# --------------------------------------------------------------- 3. events


def test_proposal_changed_is_published_for_every_action(demo):
    service, registry = demo
    queue = service.bus.subscribe()
    try:
        locks.set_client_id("chat:main")
        _create(registry)
        assert "error" not in registry.call(
            "proposal_update", {"project": "demo", "id": "1", "title": "t"})
        locks.set_client_id("browser")
        assert "error" not in registry.call(
            "proposal_review",
            {"project": "demo", "id": "1", "verdict": "approve"})
        merged = registry.call("proposal_merge", {"project": "demo", "id": "1"})
        assert "error" not in merged, merged

        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
    finally:
        service.bus.unsubscribe(queue)

    published = [e for e in events if e["type"] == "proposal_changed"]
    assert [e["reason"] for e in published] == [
        "created", "updated", "review", "merged"]
    for event in published:
        assert set(event) == {"type", "project", "id", "state", "reason"}
        assert event["project"] == "demo" and event["id"] == "1"
    assert published[-1]["state"] == "merged"


def test_proposal_changed_does_not_snapshot_the_project(demo):
    """It is not ``project_changed``, so ``_snapshot_on_event`` must ignore
    it — a proposal is not a change to the model."""
    service, registry = demo
    canonical = service.store.canonical_path_of("demo")
    before = len(service.history.log(canonical, limit=100))

    locks.set_client_id("chat:main")
    _create(registry)
    registry.call("proposal_review",
                  {"project": "demo", "id": "1", "verdict": "comment"})

    assert len(service.history.log(canonical, limit=100)) == before


def test_a_second_client_sees_proposal_changed_live(client):
    service, _registry, http = client
    with http.websocket_connect("/ws") as ws:
        created = http.post("/api/projects/demo/proposals",
                            json={"source": "feat", "title": "Thinner wall"})
        assert created.status_code == 200, created.text
        seen = []
        for _ in range(10):
            event = ws.receive_json()
            seen.append(event)
            if event["type"] == "proposal_changed":
                break
    assert seen[-1] == {"type": "proposal_changed", "project": "demo",
                        "id": "1", "state": "open", "reason": "created"}
    assert service is not None
