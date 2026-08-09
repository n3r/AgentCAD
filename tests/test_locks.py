"""Multi-user turn-locking: TurnLock semantics, enforcement at the
ProjectStore choke point, identity threading (HTTP header, chat executor),
and lock_changed events.

Identities are simulated with ``locks.set_client_id`` — contextvars work
directly in the test thread, so each call site sees the identity set last.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from agentcad.core import locks
from agentcad.core.locks import set_client_id
from agentcad.core.service import EventBus
from agentcad.core.tools import build_registry
from agentcad.server.app import create_app

from .conftest import BOX_SCRIPT, make_test_service


@pytest.fixture(autouse=True)
def _reset_identity():
    """Pin the test thread's identity to the default and restore it after,
    so one test's set_client_id can never leak into the next."""
    token = locks.client_id_var.set("local")
    yield
    locks.client_id_var.reset(token)


@pytest.fixture
def stack(kernel, tmp_path):
    bus = EventBus()
    service = make_test_service(tmp_path / "projects", kernel, bus)
    registry = build_registry(service)
    return service, registry, bus


@pytest.fixture
def demo(stack):
    service, registry, bus = stack
    assert "error" not in registry.call("create_project", {"name": "demo"})
    part = registry.call(
        "create_part", {"project": "demo", "part_id": "box", "script": BOX_SCRIPT}
    )
    assert "error" not in part
    return service, registry, bus


def _drain(q):
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    return events


# ------------------------------------------------------ 1. backward compat


def test_no_lock_everything_works_as_before(demo):
    service, registry, _bus = demo
    result = registry.call(
        "set_params", {"project": "demo", "part_id": "box", "values": {"size": 20.0}}
    )
    assert result["ok"] is True
    assert result["metrics"]["volume_mm3"] == pytest.approx(8000.0, rel=1e-6)

    updated = registry.call(
        "update_part_script",
        {"project": "demo", "part_id": "box", "script": BOX_SCRIPT},
    )
    assert updated["ok"] is True

    assembly = service.set_assembly(
        "demo", [{"id": "a", "part": "box", "position": [0, 0, 0]}]
    )
    assert len(assembly["instances"]) == 1


# ----------------------------------------------- 2. writes blocked by lock


def test_other_clients_writes_conflict_while_held(demo):
    _service, registry, _bus = demo
    set_client_id("agent_a")
    acquired = registry.call("acquire_turn", {"project": "demo"})
    assert acquired["holder"] == "agent_a"
    assert acquired["you"] == "agent_a"
    assert acquired["expires_at"] > time.time()

    set_client_id("agent_b")
    blocked = registry.call(
        "update_part_script",
        {"project": "demo", "part_id": "box", "script": BOX_SCRIPT},
    )
    assert blocked["error"]["type"] == "conflict_error"
    assert "agent_a" in blocked["error"]["message"]
    assert blocked["error"]["details"]["holder"] == "agent_a"

    set_client_id("agent_a")
    own = registry.call(
        "set_params", {"project": "demo", "part_id": "box", "values": {"size": 15.0}}
    )
    assert own["ok"] is True


# --------------------------------------- 3. pack mutations hit the choke


def test_pack_mutations_covered_by_store_choke(demo):
    service, registry, _bus = demo
    # Setup while unlocked: two instances so set_mate has a valid target.
    service.set_assembly(
        "demo",
        [
            {"id": "a", "part": "box", "position": [0, 0, 0]},
            {"id": "b", "part": "box", "position": [30, 0, 0]},
        ],
    )

    set_client_id("agent_a")
    registry.call("acquire_turn", {"project": "demo"})

    set_client_id("agent_b")
    mate = registry.call(
        "set_mate",
        {
            "project": "demo",
            "instance": "b",
            "connector": "c1",
            "to_instance": "a",
            "to_connector": "c2",
        },
    )
    assert mate["error"]["type"] == "conflict_error"
    assert "agent_a" in mate["error"]["message"]

    materials = registry.call(
        "set_project_materials", {"project": "demo", "materials": {}}
    )
    assert materials["error"]["type"] == "conflict_error"
    assert "agent_a" in materials["error"]["message"]


# --------------------------------------------- 4. get_turn / release rules


def test_get_turn_and_release_semantics(demo):
    _service, registry, _bus = demo
    set_client_id("agent_a")
    registry.call("acquire_turn", {"project": "demo", "ttl_s": 60})

    info = registry.call("get_turn", {"project": "demo"})
    assert info["lock"]["holder"] == "agent_a"
    assert info["lock"]["expires_at"] > time.time()
    assert info["you"] == "agent_a"

    set_client_id("agent_b")
    denied = registry.call("release_turn", {"project": "demo"})
    assert denied["error"]["type"] == "conflict_error"
    assert "agent_a" in denied["error"]["message"]

    set_client_id("agent_a")
    assert registry.call("release_turn", {"project": "demo"})["released"] is True
    assert registry.call("get_turn", {"project": "demo"})["lock"] is None
    # Releasing an unheld lock is a no-op, not an error.
    assert registry.call("release_turn", {"project": "demo"})["released"] is False

    set_client_id("agent_b")
    assert registry.call("acquire_turn", {"project": "demo"})["holder"] == "agent_b"


# ------------------------------------------------------- 5. TTL and expiry


def test_expired_lock_frees_writes_and_steals(demo, monkeypatch):
    _service, registry, _bus = demo
    set_client_id("agent_a")
    real_now = time.time()
    acquired = registry.call("acquire_turn", {"project": "demo", "ttl_s": 1})
    # ttl_s clamps up to the 5 s minimum (and would clamp down to 3600).
    assert acquired["expires_at"] == pytest.approx(real_now + 5.0, abs=2.0)

    set_client_id("agent_b")
    blocked = registry.call("set_assembly", {"project": "demo", "instances": []})
    assert blocked["error"]["type"] == "conflict_error"

    # Jump only the locks module's clock past expiry; the rest of the
    # process (kernel heartbeats, caches) keeps real time.
    monkeypatch.setattr(locks, "time", SimpleNamespace(time=lambda: real_now + 60.0))

    freed = registry.call("set_assembly", {"project": "demo", "instances": []})
    assert "error" not in freed
    stolen = registry.call("acquire_turn", {"project": "demo"})
    assert stolen["holder"] == "agent_b"


# ---------------------------------------------------- 6. lock_changed events


def test_lock_changed_events_on_acquire_and_release(demo):
    service, registry, bus = demo
    q = bus.subscribe()
    set_client_id("agent_a")
    registry.call("acquire_turn", {"project": "demo"})
    registry.call("release_turn", {"project": "demo"})
    lock_events = [e for e in _drain(q) if e["type"] == "lock_changed"]
    # The lock's own key travels with the event (see the branch-aware test
    # below); on the default branch it is the project name.
    branch = "master" if getattr(service, "branches", None) else None
    assert lock_events == [
        {"type": "lock_changed", "project": "demo", "key": "demo",
         "branch": branch, "holder": "agent_a"},
        {"type": "lock_changed", "project": "demo", "key": "demo",
         "branch": branch, "holder": None},
    ]


@pytest.mark.integration
@pytest.mark.portability
@pytest.mark.skipif(shutil.which("git") is None, reason="git not found on PATH")
def test_lock_changed_names_the_branch_whose_turn_moved(kernel, tmp_path):
    """A turn is per branch (lock_key is the caller's working tree), so the
    event must say which one — a client on master must not light its badge
    because an agent took the turn on 'feat'."""
    from agentcad.core.service import AgentCADService

    bus = EventBus()
    service = AgentCADService(tmp_path / "projects", kernel, bus)
    registry = build_registry(service)
    assert "error" not in registry.call("create_project", {"name": "demo"})
    assert "error" not in registry.call(
        "create_part", {"project": "demo", "part_id": "box", "script": BOX_SCRIPT})
    service.branches.create("demo", "feat")

    q = bus.subscribe()
    set_client_id("agent_a")
    service.branches.switch("demo", "feat")
    assert "error" not in registry.call("acquire_turn", {"project": "demo"})
    event = [e for e in _drain(q) if e["type"] == "lock_changed"][-1]

    assert event["holder"] == "agent_a"
    assert event["branch"] == "feat"
    assert event["key"] == service.store.lock_key("demo") != "demo"

    # ...while the same call on the default branch names master and keys on
    # the project, so the two never look like one lock.
    set_client_id("agent_b")
    assert "error" not in registry.call("acquire_turn", {"project": "demo"})
    other = [e for e in _drain(q) if e["type"] == "lock_changed"][-1]
    assert other["branch"] == "master" and other["key"] == "demo"


# --------------------------------------------------- 7. HTTP identity header


def test_http_identity_header_and_conflict_shapes(kernel, tmp_path):
    service = make_test_service(tmp_path / "projects", kernel)
    app = create_app(
        service, build_registry(service), extra_allowed_hosts={"testserver"}
    )
    client = TestClient(app, base_url="http://127.0.0.1")
    assert client.post("/api/projects", json={"name": "demo"}).status_code == 201
    created = client.post(
        "/api/projects/demo/parts", json={"id": "box", "script": BOX_SCRIPT}
    )
    assert created.status_code == 201

    acquired = client.post(
        "/api/tools/acquire_turn",
        json={"project": "demo"},
        headers={"X-Agent-Id": "alice"},
    ).json()
    assert acquired["holder"] == "alice"
    assert acquired["you"] == "alice"

    # Raw REST write without the header = browser identity -> HTTP 409.
    resp = client.patch("/api/projects/demo/parts/box/params", json={"size": 20.0})
    assert resp.status_code == 409
    body = resp.json()
    assert body["error"]["type"] == "ConflictError"
    assert "alice" in body["error"]["message"]

    # Tool passthrough errors stay HTTP 200 with an {"error": ...} payload.
    resp = client.post(
        "/api/tools/set_params",
        json={"project": "demo", "part_id": "box", "values": {"size": 20.0}},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["error"]["type"] == "conflict_error"
    assert "alice" in payload["error"]["message"]

    # Sync endpoints run in Starlette's threadpool: the identity contextvar
    # set in async middleware must propagate there too (DELETE is a sync def).
    assert client.delete("/api/projects/demo/parts/box").status_code == 409
    resp = client.delete(
        "/api/projects/demo/parts/box", headers={"X-Agent-Id": "alice"}
    )
    assert resp.status_code == 200


# -------------------------------------------------------- 8. chat identity


def _text(text):
    return SimpleNamespace(type="text", text=text)


def _tool_use(id, name, input):
    return SimpleNamespace(type="tool_use", id=id, name=name, input=input)


def _response(blocks, stop_reason):
    return SimpleNamespace(content=blocks, stop_reason=stop_reason)


class FakeMessages:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("FakeAnthropic ran out of scripted responses")
        return self._responses.pop(0)


class FakeAnthropic:
    def __init__(self, responses):
        self.messages = FakeMessages(responses)


def test_chat_tools_run_under_chat_identity(demo):
    from agentcad.agent.chat import ChatEngine

    service, registry, bus = demo
    fake = FakeAnthropic(
        [
            _response([_tool_use("t1", "acquire_turn", {"project": "demo"})],
                      "tool_use"),
            _response(
                [_tool_use("t2", "set_assembly",
                           {"project": "demo", "instances": []})],
                "tool_use",
            ),
            _response([_text("locked, edited")], "end_turn"),
        ]
    )
    engine = ChatEngine(registry, bus, api_key="test-key",
                        client_factory=lambda: fake)

    async def main():
        info = await engine.start_turn("demo", "take the turn and edit")
        await asyncio.wait_for(engine._tasks[info["turn_id"]], timeout=10)

    asyncio.run(main())

    # The lock the chat turn acquired is recorded under the "chat" identity.
    lock = service.turnlock.get("demo")
    assert lock is not None and lock["holder"] == "chat"

    # Chat's own write while holding the lock succeeded (tool_result of t2).
    history = engine.history("demo")
    t2_result = next(
        json.loads(b["content"])
        for m in history if m["role"] == "user" and isinstance(m["content"], list)
        for b in m["content"]
        if b.get("type") == "tool_result" and b.get("tool_use_id") == "t2"
    )
    assert "error" not in t2_result

    # Everyone else's writes now conflict, naming "chat" as the holder.
    set_client_id("agent_b")
    blocked = registry.call("set_assembly", {"project": "demo", "instances": []})
    assert blocked["error"]["type"] == "conflict_error"
    assert "chat" in blocked["error"]["message"]
