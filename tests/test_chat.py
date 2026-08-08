"""ChatEngine unit tests: scripted FakeAnthropic conversation, no network.

The fake client scripts a two-round conversation: the first response asks for
the ``create_project`` tool, the second ends the turn with text. Assertions
cover real tool execution through the registry, event ordering on the bus,
history growth, and the ChatUnavailable error when no key is configured.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agentcad.agent.chat import ChatEngine, ChatUnavailable
from agentcad.core.model import ValidationError
from agentcad.core.service import AgentCADService, EventBus
from agentcad.core.tools import build_registry

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
    assert len(first_call["tools"]) == 17
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
