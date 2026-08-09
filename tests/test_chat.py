"""ChatEngine unit tests: scripted FakeAnthropic conversation, no network.

The fake client scripts a two-round conversation: the first response asks for
the ``create_project`` tool, the second ends the turn with text. Assertions
cover real tool execution through the registry, event ordering on the bus,
history growth, and the ChatUnavailable error when no key is configured.
"""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from agentcad.agent.chat import ChatEngine, ChatUnavailable
from agentcad.core.model import ValidationError
from agentcad.core.service import AgentCADService, EventBus
from agentcad.core.tools import Tool, build_registry, schema
from agentcad.server.app import create_app

PROJECT = "chatproj"


class _UnusedKernel:
    """The chat test never rebuilds geometry; any kernel use is a bug."""

    alive = True

    def request(self, *args, **kwargs):  # pragma: no cover — guard
        raise AssertionError("kernel must not be used by this test")


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


@pytest.fixture()
def stack(tmp_path):
    bus = EventBus()
    service = AgentCADService(tmp_path / "projects", _UnusedKernel(), bus)
    registry = build_registry(service)
    return service, registry, bus


def _drain(q):
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    return events


def test_chat_turn_executes_tools_and_publishes_events(stack):
    service, registry, bus = stack
    fake = FakeAnthropic(
        [
            _response(
                [
                    _text("I'll create the project now."),
                    _tool_use("tu_1", "create_project", {"name": PROJECT}),
                ],
                stop_reason="tool_use",
            ),
            _response(
                [_text("Created the project. Summary: one new empty project.")],
                stop_reason="end_turn",
            ),
        ]
    )
    engine = ChatEngine(
        registry, bus, api_key="test-key", client_factory=lambda: fake
    )
    queue = bus.subscribe()
    assert engine.available
    assert engine.history(PROJECT) == []

    async def main():
        info = await engine.start_turn(PROJECT, "create a project called chatproj")
        assert isinstance(info["turn_id"], str) and info["turn_id"]
        task = engine._tasks[info["turn_id"]]
        await asyncio.wait_for(task, timeout=10)
        return info["turn_id"]

    turn_id = asyncio.run(main())

    # The registry really created the project on disk.
    names = [p["name"] for p in service.list_projects()]
    assert PROJECT in names

    # Events arrived, tagged with the project, in order.
    events = _drain(queue)
    types_seq = [e["type"] for e in events if e["type"].startswith("chat_")]
    assert "chat_delta" in types_seq
    assert types_seq.index("chat_tool_call") < types_seq.index("chat_done")
    assert types_seq.index("chat_tool_call") < types_seq.index("chat_tool_result")
    assert all(
        e["project"] == PROJECT for e in events if e["type"].startswith("chat_")
    )
    tool_call = next(e for e in events if e["type"] == "chat_tool_call")
    assert tool_call["name"] == "create_project"
    assert tool_call["args"] == {"name": PROJECT}
    tool_result = next(e for e in events if e["type"] == "chat_tool_result")
    assert tool_result["ok"] is True
    done = next(e for e in events if e["type"] == "chat_done")
    assert done["turn_id"] == turn_id

    # History grew: user msg, assistant tool_use, tool_result, final assistant.
    history = engine.history(PROJECT)
    assert len(history) == 4
    assert [m["role"] for m in history] == ["user", "assistant", "user", "assistant"]
    assert history[0]["content"] == "create a project called chatproj"
    assert history[2]["content"][0]["type"] == "tool_result"
    assert history[2]["content"][0]["tool_use_id"] == "tu_1"

    # The API request carried the full registry tool surface and the contract.
    first_call = fake.messages.calls[0]
    assert len(first_call["tools"]) >= 25  # 17 core + v2 packs
    assert {"name", "description", "input_schema"} <= set(first_call["tools"][0])
    assert "part_template" in first_call["system"]
    assert first_call["model"] == "claude-sonnet-5"
    assert first_call["max_tokens"] == 4096
    # Second round resent the prior turns (loop keeps context).
    assert len(fake.messages.calls[1]["messages"]) == 3

    # clear_history empties the transcript.
    engine.clear_history(PROJECT)
    assert engine.history(PROJECT) == []


def test_chat_unavailable_without_api_key(stack, monkeypatch):
    _service, registry, bus = stack
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    engine = ChatEngine(registry, bus)
    assert engine.available is False
    # ChatUnavailable is a ValidationError so the server maps it to HTTP 422.
    assert issubclass(ChatUnavailable, ValidationError)

    async def main():
        with pytest.raises(ChatUnavailable):
            await engine.start_turn(PROJECT, "hello")

    asyncio.run(main())
    assert engine.history(PROJECT) == []


def test_concurrent_turns_serialize_and_history_stays_paired(stack):
    service, registry, bus = stack
    # Two turns, each: one tool_use round then an end_turn. If turns
    # interleaved, the second user message would land between a tool_use
    # assistant message and its tool_result.
    fake = FakeAnthropic(
        [
            _response(
                [_tool_use("t1", "list_projects", {})], "tool_use"
            ),
            _response([_text("first done")], "end_turn"),
            _response(
                [_tool_use("t2", "list_projects", {})], "tool_use"
            ),
            _response([_text("second done")], "end_turn"),
        ]
    )
    engine = ChatEngine(
        registry, bus, api_key="test-key", client_factory=lambda: fake
    )

    async def scenario():
        await engine.start_turn(PROJECT, "first message")
        await engine.start_turn(PROJECT, "second message")
        for _ in range(200):
            if not engine._tasks:
                break
            await asyncio.sleep(0.01)

    asyncio.run(scenario())

    history = engine.history(PROJECT)
    # Validate the tool_use/tool_result pairing invariant over the transcript.
    for i, msg in enumerate(history):
        if msg["role"] != "assistant":
            continue
        tool_ids = [
            b["id"] for b in msg["content"]
            if isinstance(b, dict) and b.get("type") == "tool_use"
        ]
        if not tool_ids:
            continue
        nxt = history[i + 1]
        assert nxt["role"] == "user"
        result_ids = [
            b.get("tool_use_id") for b in nxt["content"]
            if isinstance(b, dict) and b.get("type") == "tool_result"
        ]
        assert result_ids == tool_ids
    # Both user messages present, in order, and both turns completed.
    user_texts = [m["content"] for m in history if m["role"] == "user"
                  and isinstance(m["content"], str)]
    assert user_texts == ["first message", "second message"]


def test_tool_result_with_png_becomes_image_content(stack):
    _service, registry, bus = stack
    registry.register(Tool(
        "fake_render", "returns a png_base64 payload", schema({}, []),
        lambda: {"path": "renders/box_iso.png", "view": "iso",
                 "png_base64": "QUJDRA=="},
    ))
    fake = FakeAnthropic([
        _response([_tool_use("tu_img", "fake_render", {})], "tool_use"),
        _response([_text("here is the render")], "end_turn"),
    ])
    engine = ChatEngine(
        registry, bus, api_key="test-key", client_factory=lambda: fake
    )
    queue = bus.subscribe()

    async def main():
        info = await engine.start_turn(PROJECT, "show me the part")
        await asyncio.wait_for(engine._tasks[info["turn_id"]], timeout=10)

    asyncio.run(main())

    # History: the tool_result content is a two-block list — image, then text.
    history = engine.history(PROJECT)
    tool_result = history[2]["content"][0]
    assert tool_result["type"] == "tool_result"
    assert tool_result["tool_use_id"] == "tu_img"
    blocks = tool_result["content"]
    assert isinstance(blocks, list) and len(blocks) == 2
    image, text = blocks
    assert image["type"] == "image"
    assert image["source"] == {
        "type": "base64", "media_type": "image/png", "data": "QUJDRA==",
    }
    assert text["type"] == "text"
    text_payload = json.loads(text["text"])
    assert "png_base64" not in text_payload
    assert text_payload["view"] == "iso"

    # The bus event must not carry the base64 payload.
    events = _drain(queue)
    result_event = next(e for e in events if e["type"] == "chat_tool_result")
    assert result_event["ok"] is True
    assert "QUJDRA" not in result_event["result"]
    event_payload = json.loads(result_event["result"])
    assert event_payload["png_base64"] == "<image omitted>"
    assert event_payload["view"] == "iso"


def test_failed_turn_repairs_dangling_tool_use(stack):
    service, registry, bus = stack

    class ExplodingMessages:
        def __init__(self):
            self.calls = 0

        async def create(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return _response(
                    [_tool_use("t1", "list_projects", {})], "tool_use"
                )
            raise RuntimeError("api exploded mid-turn")

    fake = SimpleNamespace(messages=ExplodingMessages())
    engine = ChatEngine(
        registry, bus, api_key="test-key", client_factory=lambda: fake
    )

    async def scenario():
        await engine.start_turn(PROJECT, "hello")
        for _ in range(200):
            if not engine._tasks:
                break
            await asyncio.sleep(0.01)

    asyncio.run(scenario())
    history = engine.history(PROJECT)
    # The dangling tool_use from the crashed turn must have matching results.
    last_assistant = max(
        i for i, m in enumerate(history) if m["role"] == "assistant"
    )
    tool_ids = [
        b["id"] for b in history[last_assistant]["content"]
        if isinstance(b, dict) and b.get("type") == "tool_use"
    ]
    if tool_ids:
        nxt = history[last_assistant + 1]
        result_ids = [
            b.get("tool_use_id") for b in nxt["content"]
            if isinstance(b, dict) and b.get("type") == "tool_result"
        ]
        assert result_ids == tool_ids


# ------------------------------------------------------------------ sessions


def _assert_tool_pairing(history):
    """Every assistant tool_use must be immediately followed by a user message
    whose tool_results match the tool_use ids, in order."""
    for i, msg in enumerate(history):
        if msg["role"] != "assistant":
            continue
        tool_ids = [
            b["id"] for b in msg["content"]
            if isinstance(b, dict) and b.get("type") == "tool_use"
        ]
        if not tool_ids:
            continue
        nxt = history[i + 1]
        assert nxt["role"] == "user"
        result_ids = [
            b.get("tool_use_id") for b in nxt["content"]
            if isinstance(b, dict) and b.get("type") == "tool_result"
        ]
        assert result_ids == tool_ids


def _history_tool_use_ids(history):
    return [
        b["id"]
        for m in history if m["role"] == "assistant"
        for b in m["content"]
        if isinstance(b, dict) and b.get("type") == "tool_use"
    ]


def test_two_sessions_keep_independent_histories_and_tagged_events(stack):
    _service, registry, bus = stack
    # Three sequential turns interleaved across two sessions on one project.
    fake = FakeAnthropic(
        [
            _response([_tool_use("a1", "list_projects", {})], "tool_use"),
            _response([_text("a first done")], "end_turn"),
            _response([_tool_use("b1", "list_projects", {})], "tool_use"),
            _response([_text("b first done")], "end_turn"),
            _response([_tool_use("a2", "list_projects", {})], "tool_use"),
            _response([_text("a second done")], "end_turn"),
        ]
    )
    engine = ChatEngine(
        registry, bus, api_key="test-key", client_factory=lambda: fake
    )
    queue = bus.subscribe()

    async def run_turn(session, message):
        info = await engine.start_turn(PROJECT, message, session=session)
        await asyncio.wait_for(engine._tasks[info["turn_id"]], timeout=10)

    async def main():
        await run_turn("a", "a: first")
        await run_turn("b", "b: first")
        await run_turn("a", "a: second")

    asyncio.run(main())

    hist_a = engine.history(PROJECT, "a")
    hist_b = engine.history(PROJECT, "b")
    # Independent transcripts: each session has only its own user messages.
    assert [m["content"] for m in hist_a
            if m["role"] == "user" and isinstance(m["content"], str)] \
        == ["a: first", "a: second"]
    assert [m["content"] for m in hist_b
            if m["role"] == "user" and isinstance(m["content"], str)] \
        == ["b: first"]
    # Each history pairs exactly its own tool_use ids — no cross-contamination.
    assert _history_tool_use_ids(hist_a) == ["a1", "a2"]
    assert _history_tool_use_ids(hist_b) == ["b1"]
    _assert_tool_pairing(hist_a)
    _assert_tool_pairing(hist_b)
    # The default "main" session was never touched.
    assert engine.history(PROJECT) == []

    # Every chat_* event is tagged with the session that produced it.
    events = [e for e in _drain(queue) if e["type"].startswith("chat_")]
    assert events and all("session" in e for e in events)
    by_text = {e["text"]: e["session"] for e in events if e["type"] == "chat_delta"}
    assert by_text["a first done"] == "a"
    assert by_text["b first done"] == "b"
    assert by_text["a second done"] == "a"
    assert [e["session"] for e in events if e["type"] == "chat_done"] \
        == ["a", "b", "a"]

    # clear_history is per-session.
    engine.clear_history(PROJECT, "a")
    assert engine.history(PROJECT, "a") == []
    assert len(engine.history(PROJECT, "b")) == 4


class _RendezvousMessages:
    """create() blocks until two turns are inside it simultaneously, then
    both finish. Completes without error only if cross-session turns really
    run concurrently; a shared lock would time the first one out."""

    def __init__(self):
        self.calls = []
        self._both_inside = asyncio.Event()
        self._entered = 0

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        self._entered += 1
        if self._entered >= 2:
            self._both_inside.set()
        await asyncio.wait_for(self._both_inside.wait(), timeout=5)
        return _response([_text("done")], "end_turn")


def test_cross_session_turns_run_concurrently(stack):
    _service, registry, bus = stack
    fake = SimpleNamespace(messages=_RendezvousMessages())
    engine = ChatEngine(
        registry, bus, api_key="test-key", client_factory=lambda: fake
    )
    queue = bus.subscribe()

    async def main():
        info_a = await engine.start_turn(PROJECT, "hello from a", session="a")
        info_b = await engine.start_turn(PROJECT, "hello from b", session="b")
        tasks = [engine._tasks[info_a["turn_id"]], engine._tasks[info_b["turn_id"]]]
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=10)

    asyncio.run(main())

    # Both turns finished with the scripted text — neither hit the 5 s timeout
    # that per-project (rather than per-session) serialization would cause.
    events = [e for e in _drain(queue) if e["type"].startswith("chat_")]
    deltas = [e for e in events if e["type"] == "chat_delta"]
    assert all("[chat error]" not in e["text"] for e in deltas)
    assert sorted(e["session"] for e in deltas) == ["a", "b"]
    for session in ("a", "b"):
        history = engine.history(PROJECT, session)
        assert history[-1]["role"] == "assistant"
        assert history[-1]["content"][0]["text"] == "done"


def test_same_session_turns_still_serialize(stack):
    _service, registry, bus = stack
    fake = FakeAnthropic(
        [
            _response([_tool_use("t1", "list_projects", {})], "tool_use"),
            _response([_text("first done")], "end_turn"),
            _response([_tool_use("t2", "list_projects", {})], "tool_use"),
            _response([_text("second done")], "end_turn"),
        ]
    )
    engine = ChatEngine(
        registry, bus, api_key="test-key", client_factory=lambda: fake
    )

    async def scenario():
        await engine.start_turn(PROJECT, "first message", session="rev")
        await engine.start_turn(PROJECT, "second message", session="rev")
        for _ in range(200):
            if not engine._tasks:
                break
            await asyncio.sleep(0.01)

    asyncio.run(scenario())

    history = engine.history(PROJECT, "rev")
    _assert_tool_pairing(history)
    user_texts = [m["content"] for m in history
                  if m["role"] == "user" and isinstance(m["content"], str)]
    assert user_texts == ["first message", "second message"]


def test_session_turn_stamps_scoped_chat_identity(stack):
    # A non-main session's tool calls run under "chat:<session>" so its turn
    # lock is attributable; the "main" session keeps plain "chat" (pinned by
    # tests/test_locks.py::test_chat_tools_run_under_chat_identity).
    service, registry, bus = stack
    assert "error" not in registry.call("create_project", {"name": PROJECT})
    fake = FakeAnthropic(
        [
            _response([_tool_use("t1", "acquire_turn", {"project": PROJECT})],
                      "tool_use"),
            _response([_text("locked")], "end_turn"),
        ]
    )
    engine = ChatEngine(
        registry, bus, api_key="test-key", client_factory=lambda: fake
    )

    async def main():
        info = await engine.start_turn(
            PROJECT, "take the turn", session="reviewer"
        )
        await asyncio.wait_for(engine._tasks[info["turn_id"]], timeout=10)

    asyncio.run(main())

    lock = service.turnlock.get(PROJECT)
    assert lock is not None and lock["holder"] == "chat:reviewer"


# -------------------------------------------------------------- chat routes


def _chat_client(tmp_path, responses):
    bus = EventBus()
    service = AgentCADService(tmp_path / "projects", _UnusedKernel(), bus)
    registry = build_registry(service)
    engine = ChatEngine(
        registry, bus, api_key="test-key",
        client_factory=lambda: FakeAnthropic(responses),
    )
    app = create_app(
        service, registry, chat_engine=engine,
        extra_allowed_hosts={"testserver"},
    )
    return TestClient(app, base_url="http://127.0.0.1"), engine


def test_chat_routes_validate_and_thread_session(tmp_path):
    client, engine = _chat_client(
        tmp_path, [_response([_text("ok")], "end_turn")]
    )
    with client:
        for bad in ("Reviewer", "has space", "a" * 33, "", "sesh/1", None):
            resp = client.post(
                "/api/chat",
                json={"project": "p", "message": "hi", "session": bad},
            )
            assert resp.status_code == 422, bad
        assert client.get(
            "/api/chat/history", params={"project": "p", "session": "BAD!"}
        ).status_code == 422
        assert client.delete(
            "/api/chat/history", params={"project": "p", "session": "BAD!"}
        ).status_code == 422

        # History payloads carry the session; unknown session is just empty.
        resp = client.get(
            "/api/chat/history", params={"project": "p", "session": "reviewer"}
        )
        assert resp.status_code == 200
        assert resp.json() == {"messages": [], "session": "reviewer"}
        resp = client.get("/api/chat/history", params={"project": "p"})
        assert resp.json() == {"messages": [], "session": "main"}

        # A valid session id starts the turn in that session.
        resp = client.post(
            "/api/chat",
            json={"project": "p", "message": "hi", "session": "reviewer"},
        )
        assert resp.status_code == 200
        assert resp.json()["turn_id"]
        for _ in range(200):
            if engine.history("p", "reviewer"):
                break
            time.sleep(0.01)
        assert engine.history("p", "reviewer")[0]["content"] == "hi"
        assert engine.history("p") == []
