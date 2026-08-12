"""PRD-008 slice 5: mentions, ``notifications.jsonl`` and AC4.

``@<identity>`` in a comment body delivers a notification to that identity —
*if* it names a plausible one. ``@todo`` and ``@nobody`` stay plain text and
deliver nothing, because silently minting notifications for prose is noise.

Sections: 1. the mention scanner (pure) · 2. the append-only log and the
unread arithmetic · 3. the tool and the two routes · 4. **AC4** end to end
over the WebSocket.

The bus is a **broadcast**: every ``/ws`` client receives every ``notification``
event and filters on ``to``. That is honest for a single-user, 127.0.0.1-only
server with no authentication, and per-principal delivery is PRD-005 — the
tests assert the broadcast rather than pretending it is targeted.
"""

from __future__ import annotations

import json
import queue

import pytest
from fastapi.testclient import TestClient

from agentcad.core import locks
from agentcad.core.comments import (
    CommentManager,
    parse_mentions,
    plausible_mention,
)
from agentcad.core.model import ValidationError
from agentcad.core.service import AgentCADService, EventBus
from agentcad.core.tools import build_registry
from agentcad.server.app import create_app

from .conftest import BOX_SCRIPT


class _NoKernel:
    """Nothing in a mention's life builds anything."""

    def request(self, op, params, timeout_s=None):
        raise AssertionError(f"a mention called the kernel: {op}")


@pytest.fixture(autouse=True)
def _reset_identity():
    token = locks.client_id_var.set("browser")
    yield
    locks.client_id_var.reset(token)


@pytest.fixture
def demo(tmp_path):
    service = AgentCADService(tmp_path / "projects", _NoKernel(), EventBus())
    registry = build_registry(service)
    assert "error" not in registry.call("create_project", {"name": "demo"})
    # Written straight into the manifest: this module never builds.
    service.store.add_part("demo", "box", "Box", "al6061", BOX_SCRIPT)
    return service, registry


@pytest.fixture
def client(demo):
    service, registry = demo
    app = create_app(service, registry, extra_allowed_hosts={"testserver"})
    return service, registry, TestClient(app, base_url="http://127.0.0.1")


def _anchor(**fields) -> dict:
    anchor = {"kind": "part", "part": "box"}
    anchor.update(fields)
    return anchor


def _post(registry, body: str, **args) -> dict:
    payload = {"project": "demo", "anchor": _anchor(), "body": body}
    payload.update(args)
    result = registry.call("add_comment", payload)
    assert "error" not in result, result
    return result["thread"]


def _as(identity: str):
    return locks.client_id_var.set(identity)


def _drain(subscription) -> list[dict]:
    events = []
    while True:
        try:
            events.append(subscription.get_nowait())
        except queue.Empty:
            return events


# ------------------------------------------------- 1. the mention scanner


def test_the_scanner_finds_handles_and_keeps_their_order():
    assert parse_mentions("ping @chat:main and @browser:7f3a about this") == [
        "chat:main", "browser:7f3a"]
    assert parse_mentions("@chat:main @chat:main") == ["chat:main"]  # deduped
    assert parse_mentions("no handles here") == []


def test_an_email_address_is_not_a_mention():
    """The lookbehind is the whole point: ``a@b.com`` is an address, and
    ``@@chat`` is not a handle either."""
    assert parse_mentions("mail me at nobody@example.com") == []
    assert parse_mentions("@@chat:main") == []


def test_only_plausible_identities_are_mentions():
    for good in ("browser", "browser:7f3a", "chat", "chat:main", "chat:a_b-1"):
        assert plausible_mention(good), good
    for bad in ("todo", "nobody", "chat:MAIN", "chat:", "browser:",
                "chat:way-too-long-a-session-id-for-the-regex-to-accept-here",
                ""):
        assert not plausible_mention(bad), bad


def test_a_present_client_id_is_mentionable(demo):
    """The presence registry (slice 6) is the third authority; without it the
    two static families are all there is."""
    assert not plausible_mention("mcp-bot")
    assert plausible_mention("mcp-bot", present={"mcp-bot"})


def test_an_implausible_handle_stays_plain_text(demo):
    service, registry = demo
    thread = _post(registry, "@todo fix this and ask @nobody about @chat:main")

    comment = thread["comments"][0]
    assert comment["mentions"] == ["chat:main"]
    # The body is stored verbatim — a mention is never edited out of it.
    assert "@todo" in comment["body"] and "@nobody" in comment["body"]
    assert [n["to"] for n in service.comments.notifications("demo")] == [
        "chat:main"]


# ---------------------------------------- 2. the log and the unread arithmetic


def test_a_mention_appends_one_line_per_recipient(demo):
    service, registry = demo
    thread = _post(registry, "@chat:main @chat:build please look")

    records = service.comments.notifications("demo")
    assert [(r["kind"], r["to"], r["seq"]) for r in records] == [
        ("mention", "chat:main", 1), ("mention", "chat:build", 2)]
    assert records[0]["thread"] == thread["id"]
    assert records[0]["comment"] == "1"
    assert records[0]["from"] == "browser"
    assert records[0]["project"] == "demo"
    assert records[0]["ts"].endswith("Z")


def test_a_self_mention_notifies_nobody(demo):
    service, registry = demo
    _post(registry, "note to self: @browser should fix this")

    assert service.comments.notifications("demo") == []


def test_a_reply_delivers_its_own_mentions(demo):
    service, registry = demo
    _post(registry, "opening this")
    token = _as("chat:main")
    try:
        registry.call("add_comment", {"project": "demo", "thread": "1",
                                      "body": "over to you @browser"})
    finally:
        locks.client_id_var.reset(token)

    records = service.comments.notifications("demo")
    assert [(r["to"], r["from"], r["comment"]) for r in records] == [
        ("browser", "chat:main", "2")]


def test_an_edit_delivers_only_the_newly_mentioned(demo):
    """A typo'd handle must be fixable, and a fixed comment must not
    re-deliver to everyone it already notified."""
    service, registry = demo
    _post(registry, "@chat:main have a look")
    service.comments.edit_comment("demo", "1", "1",
                                  "@chat:main @chat:build have a look")

    records = service.comments.notifications("demo")
    assert [r["to"] for r in records] == ["chat:main", "chat:build"]
    assert service.comments.get("demo", "1")["comments"][0]["mentions"] == [
        "chat:main", "chat:build"]


def test_a_removed_and_re_added_mention_is_not_delivered_twice(demo):
    """"Already delivered" is a fact about the *log*, not about the body.

    ``already`` used to read the comment's CURRENT ``mentions`` — so an edit
    that dropped a handle wiped the only record that it had been delivered,
    and putting it back rang the same person again. The delivery record is the
    thread's audit log, which is append-only and cannot be edited away.
    """
    service, _registry = demo
    _post(demo[1], "@chat:main have a look")
    service.comments.edit_comment("demo", "1", "1", "never mind")
    service.comments.edit_comment("demo", "1", "1", "@chat:main have a look")

    records = service.comments.notifications("demo")
    assert [r["to"] for r in records] == ["chat:main"]


def test_a_comment_may_not_mention_more_people_than_it_may_attach(demo):
    """A cap, for the same reason attachments have one: one comment is one
    mutation, and one mutation must not mint an unbounded number of records
    and WebSocket frames. The handles stay in the body — nothing is rewritten;
    the *comment* is refused so the author can see and fix it."""
    from agentcad.core.comments import MAX_MENTIONS

    service, registry = demo
    body = " ".join(f"@chat:s{n}" for n in range(MAX_MENTIONS + 1))
    refused = registry.call("add_comment", {"project": "demo",
                                            "anchor": _anchor(), "body": body})
    assert refused["error"]["type"] == "validation_error", refused
    assert refused["error"]["details"] == {"max": MAX_MENTIONS,
                                           "given": MAX_MENTIONS + 1}
    assert service.comments.list("demo")["threads"] == []
    assert service.comments.notifications("demo") == []

    # Exactly at the cap still goes through.
    ok = " ".join(f"@chat:s{n}" for n in range(MAX_MENTIONS))
    assert "error" not in registry.call(
        "add_comment", {"project": "demo", "anchor": _anchor(), "body": ok})
    assert len(service.comments.notifications("demo")) == MAX_MENTIONS


def test_the_notification_sequence_is_counted_incrementally(demo):
    """``seq`` used to be ``len(read_text().splitlines()) + 1`` on every
    append — quadratic in the log, on the path a single comment can drive
    ``MAX_MENTIONS`` times. It is now carried forward, and must still be the
    line number a full re-count would give."""
    service, registry = demo
    for n in range(6):
        _post(registry, f"@chat:main @chat:build number {n}")
    records = service.comments.notifications("demo")
    assert [r["seq"] for r in records] == list(range(1, len(records) + 1))

    path = service.comments.store.notifications_path("demo")
    lines = [line for line in path.read_text().splitlines() if line.strip()]
    assert len(lines) == len(records)


def test_the_log_is_append_only_across_a_read_and_a_new_mention(demo):
    service, registry = demo
    _post(registry, "@chat:main one")
    path = service.comments.store.notifications_path("demo")
    first = path.read_bytes()

    token = _as("chat:main")
    try:
        service.comments.mark_read("demo")
    finally:
        locks.client_id_var.reset(token)
    second = path.read_bytes()
    _post(registry, "@chat:main two")
    third = path.read_bytes()

    assert second.startswith(first) and len(second) > len(first)
    assert third.startswith(second) and len(third) > len(second)
    kinds = [json.loads(line)["kind"] for line in third.splitlines() if line]
    assert kinds == ["mention", "read", "mention"]


def test_unread_is_mentions_minus_every_read_line(demo):
    service, registry = demo
    _post(registry, "@chat:main one")
    _post(registry, "@chat:main two")
    _post(registry, "@chat:build not yours")

    token = _as("chat:main")
    try:
        mine = service.comments.list_notifications()
        assert [r["to"] for r in mine["notifications"]] == ["chat:main",
                                                            "chat:main"]
        assert mine["unread"] == 2

        first = mine["notifications"][0]["seq"]
        marked = service.comments.mark_read("demo", [first])
        assert marked["marked"] == [1]
        assert marked["unread"] == 1
        assert service.comments.list_notifications(unread=True)["unread"] == 1

        assert service.comments.mark_read("demo")["unread"] == 0
        assert service.comments.list_notifications(unread=True)[
            "notifications"] == []
        # Marking read twice writes nothing new and stays at zero.
        assert service.comments.mark_read("demo")["marked"] == []
    finally:
        locks.client_id_var.reset(token)

    token = _as("chat:build")
    try:
        # One identity's read cursor is not another's.
        assert service.comments.list_notifications()["unread"] == 1
    finally:
        locks.client_id_var.reset(token)


def test_a_read_line_names_only_this_identitys_notifications(demo):
    service, registry = demo
    _post(registry, "@chat:main mine")
    _post(registry, "@chat:build theirs")

    token = _as("chat:main")
    try:
        with pytest.raises(ValidationError):
            service.comments.mark_read("demo", [2])  # chat:build's seq
        with pytest.raises(ValidationError):
            service.comments.mark_read("demo", ["1"])
    finally:
        locks.client_id_var.reset(token)

    token = _as("chat:build")
    try:
        assert service.comments.list_notifications()["unread"] == 1
    finally:
        locks.client_id_var.reset(token)


def test_notifications_span_projects_and_filter_to_one(demo):
    service, registry = demo
    assert "error" not in registry.call("create_project", {"name": "other"})
    service.store.add_part("other", "box", "Box", "al6061", BOX_SCRIPT)
    _post(registry, "@chat:main here")
    registry.call("add_comment", {"project": "other", "anchor": _anchor(),
                                  "body": "@chat:main and there"})

    token = _as("chat:main")
    try:
        every = service.comments.list_notifications()
        assert [r["project"] for r in every["notifications"]] == ["demo",
                                                                  "other"]
        assert every["unread"] == 2
        one = service.comments.list_notifications(project="demo")
        assert [r["project"] for r in one["notifications"]] == ["demo"]
        assert one["unread"] == 1
    finally:
        locks.client_id_var.reset(token)


def test_a_mention_is_audited_on_the_thread(demo):
    service, registry = demo
    _post(registry, "@chat:main look")

    audit = service.comments.audit("demo", "1")
    assert [e["action"] for e in audit] == ["created", "mentioned"]
    assert audit[-1]["details"] == {"comment": "1", "to": ["chat:main"]}


# -------------------------------------------------- 3. the tool and the routes


def test_the_pack_registers_list_notifications(demo):
    _service, registry = demo
    tool = registry.get("list_notifications")

    assert tool is not None
    assert set(tool.input_schema["properties"]) == {"project", "unread"}
    assert tool.input_schema["required"] == []
    described = tool.description.lower()
    assert "broadcast" in described  # every client sees the event
    assert "calling identity" in described


def test_list_notifications_answers_for_the_calling_identity_only(demo):
    _service, registry = demo
    _post(registry, "@chat:main yours")

    assert registry.call("list_notifications", {})["notifications"] == []
    token = _as("chat:main")
    try:
        mine = registry.call("list_notifications", {"project": "demo"})
        assert [r["thread"] for r in mine["notifications"]] == ["1"]
        assert mine["unread"] == 1
        assert mine["notifications"][0]["read"] is False
    finally:
        locks.client_id_var.reset(token)


def test_the_two_routes_round_trip(client):
    _service, _registry, http = client
    http.post("/api/projects/demo/comments",
              json={"anchor": _anchor(), "body": "@chat:main have a look"})
    headers = {"X-Agent-Id": "chat:main"}

    listed = http.get("/api/projects/demo/notifications", headers=headers)
    assert listed.status_code == 200, listed.text
    assert listed.json()["unread"] == 1
    assert listed.json()["notifications"][0]["to"] == "chat:main"

    # Somebody else's drawer is empty — the route answers per identity.
    assert http.get("/api/projects/demo/notifications").json()["unread"] == 0

    read = http.post("/api/projects/demo/notifications/read", json={},
                     headers=headers)
    assert read.status_code == 200, read.text
    assert read.json() == {"marked": [1], "unread": 0}
    assert http.get("/api/projects/demo/notifications?unread=true",
                    headers=headers).json()["notifications"] == []


def test_the_read_route_maps_a_bad_id_to_422(client):
    _service, _registry, http = client
    http.post("/api/projects/demo/comments",
              json={"anchor": _anchor(), "body": "@chat:main look"})

    refused = http.post("/api/projects/demo/notifications/read",
                        json={"ids": [99]}, headers={"X-Agent-Id": "chat:main"})
    assert refused.status_code == 422
    assert http.get("/api/projects/nope/notifications").status_code == 404


# ----------------------------------------------------------------- 4. AC4


def test_ac4_a_mention_delivers_an_event_and_a_listable_unread_record(client):
    """AC4, end to end: a browser posts ``@chat:main``; a WebSocket client
    sees the ``notification`` event, ``list_notifications`` under
    ``X-Agent-Id: chat:main`` returns one unread record, and after
    ``POST …/read`` it returns none."""
    _service, _registry, http = client

    with http.websocket_connect("/ws") as ws:
        posted = http.post(
            "/api/projects/demo/comments",
            json={"anchor": _anchor(), "body": "@chat:main can you fillet this?"})
        assert posted.status_code == 200, posted.text
        events = [ws.receive_json(), ws.receive_json()]

    assert [e["type"] for e in events] == ["comment_changed", "notification"]
    notification = events[1]
    assert notification["to"] == "chat:main"
    assert notification["from"] == "browser"
    assert notification["project"] == "demo"
    assert notification["thread"] == "1"
    assert notification["comment"] == "1"
    assert notification["ts"].endswith("Z")

    headers = {"X-Agent-Id": "chat:main"}
    unread = http.get("/api/projects/demo/notifications?unread=true",
                      headers=headers).json()
    assert unread["unread"] == 1
    assert unread["notifications"][0]["thread"] == "1"

    assert http.post("/api/projects/demo/notifications/read", json={},
                     headers=headers).status_code == 200
    after = http.get("/api/projects/demo/notifications?unread=true",
                     headers=headers).json()
    assert after == {"notifications": [], "unread": 0}


def test_a_comment_without_a_mention_publishes_only_comment_changed(demo):
    service, registry = demo
    subscription = service.bus.subscribe()

    _post(registry, "no handles in this one")

    assert [e["type"] for e in _drain(subscription)] == ["comment_changed"]
