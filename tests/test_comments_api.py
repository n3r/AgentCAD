"""PRD-008 slice 3: the comment tool pack, the route pack and ``comment_changed``.

This module pins the *surface*, not the domain logic — storage and lifecycle
are ``tests/test_comments.py``, resolution is ``tests/test_anchors*.py``. Five
sections: 1. registration and **load order** · 2. the tools · 3.
``comment_changed`` · 4. the routes · 5. the MCP passthrough.

The load-order section is the one thing about this pack that is not obvious
from reading it. ``tools._load_tool_packs`` walks ``pkgutil.iter_modules``
alphabetically, so ``tools_comments`` (``c``) loads *second*, before
``tools_proposals`` (``p``), ``tools_run_checks`` (``r``) and
``tools_versioning`` (``v``): ``service.proposals``, ``service.branches`` and
``service.gate_providers`` do **not** exist when ``register()`` runs, and
anything this pack assigned that a later pack assigns unconditionally would be
silently thrown away. Both halves are asserted here, because the failure they
prevent is invisible — no error, no warning.
"""

from __future__ import annotations

import pkgutil
import queue
import shutil

import pytest
from fastapi.testclient import TestClient

import agentcad.core as core_pkg
from agentcad.core import locks
from agentcad.core.comments import CommentManager
from agentcad.core.service import AgentCADService, EventBus
from agentcad.core.tools import build_registry
from agentcad.core.tools_comments import register as register_comments
from agentcad.server.app import create_app

from .conftest import BOX_SCRIPT

_TOOLS = ("list_comments", "add_comment", "resolve_thread", "reopen_thread")

_GIT = [
    pytest.mark.integration,
    pytest.mark.portability,
    pytest.mark.skipif(shutil.which("git") is None, reason="git not found on PATH"),
]


def _needs_git(fn):
    for mark in _GIT:
        fn = mark(fn)
    return fn


@pytest.fixture(autouse=True)
def _reset_identity():
    token = locks.client_id_var.set("browser")
    yield
    locks.client_id_var.reset(token)


@pytest.fixture
def demo(kernel, tmp_path):
    """The real service (history and the bus live) — this file is about what
    the surface publishes and persists."""
    service = AgentCADService(tmp_path / "projects", kernel, EventBus())
    registry = build_registry(service)
    assert "error" not in registry.call("create_project", {"name": "demo"})
    assert "error" not in registry.call(
        "create_part",
        {"project": "demo", "part_id": "box", "script": BOX_SCRIPT})
    assert "error" not in registry.call(
        "set_assembly",
        {"project": "demo", "instances": [{"id": "box_1", "part": "box"}]})
    return service, registry


@pytest.fixture
def client(demo):
    service, registry = demo
    app = create_app(service, registry, extra_allowed_hosts={"testserver"})
    return service, registry, TestClient(app, base_url="http://127.0.0.1")


def _drain(subscription) -> list[dict]:
    events = []
    while True:
        try:
            events.append(subscription.get_nowait())
        except queue.Empty:
            return events


def _add(registry, **args) -> dict:
    payload = {"project": "demo", "body": "this boss needs a fillet"}
    payload.update(args)
    return registry.call("add_comment", payload)


def _anchor(**fields) -> dict:
    anchor = {"kind": "part", "part": "box"}
    anchor.update(fields)
    return anchor


# ------------------------------------------- 1. registration and load order


def test_the_pack_installs_the_manager_and_exactly_five_tools(demo):
    service, registry = demo

    assert isinstance(service.comments, CommentManager)
    assert service.comments.service is service
    for name in (*_TOOLS, "list_notifications"):
        assert registry.get(name) is not None, name
    # FR7 freezes the agent surface at five: marking a notification read is a
    # route, not a sixth tool.
    assert not [t.name for t in registry.list()
                if ("comment" in t.name or "notification" in t.name)
                and t.name not in (*_TOOLS, "list_notifications")]


def test_the_pack_adds_no_gate_provider(demo):
    """Threads inform; verdicts decide (design Decision 20). An open or
    orphaned thread must never block a merge."""
    service, _registry = demo
    names = [getattr(p, "__name__", "") for p in service.gate_providers]

    assert "comments" not in names
    assert "threads" not in names


def test_the_pack_loads_before_proposals_versioning_and_checks():
    """The ordinal half of the load-order claim, against the real package."""
    packs = [i.name for i in pkgutil.iter_modules(core_pkg.__path__)
             if i.name.startswith("tools_")]

    assert packs == sorted(packs)  # pkgutil hands them over alphabetically
    assert packs.index("tools_comments") < packs.index("tools_proposals")
    assert packs.index("tools_comments") < packs.index("tools_run_checks")
    assert packs.index("tools_comments") < packs.index("tools_versioning")


def test_register_captures_nothing_a_later_pack_installs(kernel, tmp_path):
    """The destructive half: at ``c`` there is no ``service.proposals``, no
    ``service.branches`` and no ``service.gate_providers`` yet, and
    ``tools_proposals`` assigns the last one **unconditionally**. A pack that
    read any of them in ``register()`` would capture ``None`` forever."""
    from .conftest import make_test_service

    service = make_test_service(tmp_path / "projects", kernel)
    for seam in ("proposals", "branches", "merges", "gate_providers"):
        assert not hasattr(service, seam), seam

    class _Registry:
        def __init__(self):
            self.tools = {}

        def register(self, tool):
            self.tools[tool.name] = tool

    registry = _Registry()
    register_comments(registry, service)  # must not raise on the bare service

    assert set(registry.tools) == {*_TOOLS, "list_notifications"}
    assert not hasattr(service, "gate_providers")
    # And the manager works with those seams absent: ``service.branches`` is
    # read inside each call, and its absence degrades the anchor's provenance
    # to "" rather than raising.
    service.store.create("bare")
    assert service.comments.list("bare")["threads"] == []


def test_the_pack_registers_without_git(kernel, tmp_path, monkeypatch):
    """Unlike proposals and versioning, comments do NOT self-disable: a
    comment needs no history. Only tier-2 line remapping and proposal_hunk
    anchors degrade, and they degrade by saying so."""
    service = AgentCADService(tmp_path / "projects", kernel, EventBus())
    monkeypatch.setattr(service.history, "available", lambda: False)
    registry = build_registry(service)

    assert registry.get("proposal_create") is None  # the git-gated packs did
    assert registry.get("branch_list") is None      # self-disable
    for name in _TOOLS:
        assert registry.get(name) is not None, name
    assert "error" not in registry.call("create_project", {"name": "nogit"})
    opened = registry.call(
        "add_comment",
        {"project": "nogit", "anchor": {"kind": "part", "part": "box"},
         "body": "x"})
    # The part does not exist, so this is a validation_error about the PART —
    # not about git, and not an exception.
    assert opened["error"]["type"] == "validation_error"
    assert "box" in opened["error"]["message"]


def test_the_schemas_are_whitelisted(demo):
    _service, registry = demo
    schemas = {name: registry.get(name).input_schema for name in _TOOLS}

    assert set(schemas["list_comments"]["properties"]) == {
        "project", "part_id", "state", "kind", "branch", "anchor_status",
        "proposal", "resolve_anchors"}
    assert schemas["list_comments"]["required"] == ["project"]
    assert set(schemas["add_comment"]["properties"]) == {
        "project", "anchor", "thread", "body", "attachments"}
    assert schemas["add_comment"]["required"] == ["project", "body"]
    for name in ("resolve_thread", "reopen_thread"):
        assert set(schemas[name]["properties"]) == {"project", "thread"}
        assert schemas[name]["required"] == ["project", "thread"]
    types = {k: v["type"]
             for k, v in schemas["add_comment"]["properties"].items()}
    assert types == {"project": "string", "anchor": "object",
                     "thread": "string", "body": "string",
                     "attachments": "array"}


def test_the_descriptions_state_the_honest_anchor_contract(demo):
    """An agent must not have to discover the four states, the orphan rate or
    the "never render the stored ordinal" rule by being wrong once.

    The face-match rate moved from "two times in three, 0 mis-pins" to "about
    half, 2 in 2 693" when the spike was re-measured against a stricter
    ground-truth oracle (changelog 0123), and then grew a SECOND rate: that
    sweep only ever perturbs a parameter, so it says nothing about the class an
    agent actually hits when it deletes a feature (4 mis-pins in 327 destroyed
    faces, changelog 0125). A tool description that still promised the old rate
    would be the *worst* place for an old number to survive, so this asserts
    both — and, more importantly, that the description tells an agent a
    cut-away face can still re-pin.
    """
    _service, registry = demo
    listing = registry.get("list_comments").description.lower()

    for status in ("ok", "moved", "orphaned", "unverified"):
        assert status in listing
    assert "resolution.face_index" in listing  # never the stored ordinal
    assert "about half" in listing             # the measured face-match rate
    assert "2 693" in listing                  # ...over a stated sample
    assert "327" in listing                    # the deletion class's sample
    assert "cut-away face can still re-pin" in listing
    assert "face_info" in listing              # what to do when it matters
    assert "never rebuilds" in listing or "never builds" in listing
    assert "not authentication" in listing     # actor_kind is bookkeeping

    adding = registry.get("add_comment").description
    for kind in ("part", "face", "param", "script_range", "instance"):
        assert kind in adding
    assert "exports/" in adding
    assert "exactly one" in adding.lower()


# --------------------------------------------------------------- 2. the tools


def test_add_comment_opens_a_thread_and_replies_to_one(demo):
    _service, registry = demo
    opened = _add(registry, anchor=_anchor())
    assert "error" not in opened, opened
    thread = opened["thread"]
    assert thread["id"] == "1"
    assert thread["state"] == "open"
    assert thread["anchor"]["kind"] == "part"
    assert thread["resolution"]["status"] == "ok"

    replied = _add(registry, thread="1", body="on it")
    assert [c["body"] for c in replied["thread"]["comments"]] == [
        "this boss needs a fillet", "on it"]


def test_add_comment_takes_exactly_one_of_anchor_and_thread(demo):
    _service, registry = demo
    _add(registry, anchor=_anchor())

    for args in ({}, {"anchor": _anchor(), "thread": "1"}):
        refused = _add(registry, **args)
        assert refused["error"]["type"] == "validation_error", refused
        assert "exactly one" in refused["error"]["message"]
        assert refused["error"]["details"]["required"] == ["anchor", "thread"]


def test_add_comment_refuses_an_attachment_outside_exports(demo):
    """AC9 at the tool layer: no path disclosure through comments."""
    _service, registry = demo
    refused = _add(registry, anchor=_anchor(),
                   attachments=["../../etc/passwd"])

    assert refused["error"]["type"] == "validation_error"
    assert "exports" in refused["error"]["message"]


def test_list_comments_filters_counts_and_can_skip_resolution(demo):
    _service, registry = demo
    _add(registry, anchor=_anchor())
    _add(registry, anchor=_anchor(kind="param", param="size"))
    assert "error" not in registry.call(
        "resolve_thread", {"project": "demo", "thread": "2"})

    everything = registry.call("list_comments", {"project": "demo"})
    assert [t["id"] for t in everything["threads"]] == ["1", "2"]
    assert everything["counts"] == {"open": 1, "resolved": 1, "orphaned": 0}
    assert everything["threads"][0]["resolution"]["against"]["branch"] is not None

    filtered = registry.call("list_comments",
                             {"project": "demo", "kind": "param"})
    assert [t["id"] for t in filtered["threads"]] == ["2"]
    assert filtered["counts"] == everything["counts"]  # whole project

    cheap = registry.call("list_comments",
                          {"project": "demo", "resolve_anchors": False})
    assert "resolution" not in cheap["threads"][0]
    assert "orphaned" not in cheap["counts"]  # nothing was looked at

    bad = registry.call("list_comments", {"project": "demo", "state": "closed"})
    assert bad["error"]["type"] == "validation_error"
    assert bad["error"]["details"]["allowed"] == ["open", "resolved"]


def test_list_comments_treats_a_null_resolve_anchors_as_omitted(demo):
    """The registry's convention: ``null`` on an optional argument means
    "omitted", so a client sending a uniform payload must still get the
    default (``true``), never ``false``."""
    _service, registry = demo
    _add(registry, anchor=_anchor())

    listed = registry.call("list_comments",
                           {"project": "demo", "resolve_anchors": None})
    assert listed["threads"][0]["resolution"]["status"] == "ok"


def test_resolve_and_reopen_return_the_post_state_and_are_idempotent(demo):
    _service, registry = demo
    _add(registry, anchor=_anchor())

    resolved = registry.call("resolve_thread",
                             {"project": "demo", "thread": "1"})
    assert resolved["thread"]["state"] == "resolved"
    assert resolved["thread"]["resolved"]["actor"] == "browser"
    assert registry.call("resolve_thread",
                         {"project": "demo", "thread": "1"}) == resolved

    reopened = registry.call("reopen_thread", {"project": "demo", "thread": "1"})
    assert reopened["thread"]["state"] == "open"
    assert reopened["thread"]["resolved"] is None


def test_unknown_projects_and_threads_are_errors_not_empty_payloads(demo):
    _service, registry = demo

    assert registry.call("list_comments", {"project": "nope"})["error"][
        "type"] == "notfound_error"
    assert _add(registry, project="nope", anchor=_anchor())["error"][
        "type"] == "notfound_error"
    assert registry.call("resolve_thread",
                         {"project": "demo", "thread": "9"})["error"][
        "type"] == "notfound_error"


# ----------------------------------------------------- 3. comment_changed


def test_every_mutation_publishes_comment_changed(demo):
    service, registry = demo
    subscription = service.bus.subscribe()

    _add(registry, anchor=_anchor())
    _add(registry, thread="1", body="on it")
    registry.call("resolve_thread", {"project": "demo", "thread": "1"})
    registry.call("reopen_thread", {"project": "demo", "thread": "1"})
    service.comments.edit_comment("demo", "1", "2", "on it, actually")
    service.comments.delete_comment("demo", "1", "2")

    events = [e for e in _drain(subscription) if e["type"] == "comment_changed"]
    assert [e["action"] for e in events] == [
        "created", "replied", "resolved", "reopened", "comment_edited",
        "comment_deleted"]
    assert {e["project"] for e in events} == {"demo"}
    assert {e["thread"] for e in events} == {"1"}
    assert {e["part"] for e in events} == {"box"}
    assert [e["state"] for e in events] == [
        "open", "open", "resolved", "open", "open", "open"]


def test_an_idempotent_no_op_publishes_nothing(demo):
    """A no-op is not an event: five clients re-resolving a resolved thread
    must not be five events."""
    service, registry = demo
    _add(registry, anchor=_anchor())
    registry.call("resolve_thread", {"project": "demo", "thread": "1"})
    subscription = service.bus.subscribe()

    registry.call("resolve_thread", {"project": "demo", "thread": "1"})

    assert _drain(subscription) == []


def test_comment_changed_is_never_project_changed(demo):
    """A comment is not a model change: the event must not reach
    ``service._snapshot_on_event`` as one."""
    service, registry = demo
    subscription = service.bus.subscribe()

    _add(registry, anchor=_anchor())

    assert [e["type"] for e in _drain(subscription)] == ["comment_changed"]


@_needs_git
def test_a_mutation_creates_no_history_commit(demo):
    service, registry = demo
    path = service.store.path_of("demo")
    before = service.history.head(path)

    _add(registry, anchor=_anchor())
    registry.call("resolve_thread", {"project": "demo", "thread": "1"})

    assert service.history.head(path) == before


def test_a_second_client_sees_comment_changed_on_the_websocket(client):
    _service, _registry, http = client
    with http.websocket_connect("/ws") as ws:
        response = http.post("/api/projects/demo/comments",
                             json={"anchor": _anchor(), "body": "fillet this"})
        assert response.status_code == 200, response.text
        event = ws.receive_json()

    assert event == {"type": "comment_changed", "project": "demo",
                     "thread": "1", "state": "open", "action": "created",
                     "part": "box"}


# -------------------------------------------------------------- 4. the routes


def test_the_eight_endpoints_round_trip(client):
    _service, _registry, http = client

    created = http.post("/api/projects/demo/comments",
                        json={"anchor": _anchor(), "body": "fillet this"})
    assert created.status_code == 200, created.text
    assert created.json()["thread"]["id"] == "1"

    listed = http.get("/api/projects/demo/comments")
    assert listed.status_code == 200
    assert [t["id"] for t in listed.json()["threads"]] == ["1"]
    assert listed.json()["counts"]["open"] == 1

    got = http.get("/api/projects/demo/comments/1")
    assert got.status_code == 200
    assert got.json()["thread"]["comments"][0]["body"] == "fillet this"

    replied = http.post("/api/projects/demo/comments",
                        json={"thread": "1", "body": "on it"})
    assert replied.status_code == 200, replied.text

    resolved = http.post("/api/projects/demo/comments/1/resolve")
    assert resolved.status_code == 200
    assert resolved.json()["thread"]["state"] == "resolved"
    reopened = http.post("/api/projects/demo/comments/1/reopen")
    assert reopened.status_code == 200
    assert reopened.json()["thread"]["state"] == "open"

    edited = http.patch("/api/projects/demo/comments/1/comments/2",
                        json={"body": "on it, actually"})
    assert edited.status_code == 200, edited.text
    assert edited.json()["thread"]["comments"][1]["body"] == "on it, actually"

    deleted = http.delete("/api/projects/demo/comments/1/comments/2")
    assert deleted.status_code == 200
    assert deleted.json()["thread"]["comments"][1]["deleted"] is True

    audit = http.get("/api/projects/demo/comments/1/audit")
    assert audit.status_code == 200
    assert [e["action"] for e in audit.json()["audit"]] == [
        "created", "replied", "resolved", "reopened", "comment_edited",
        "comment_deleted"]


def test_the_list_route_forwards_its_filters(client):
    _service, _registry, http = client
    http.post("/api/projects/demo/comments",
              json={"anchor": _anchor(), "body": "a"})
    http.post("/api/projects/demo/comments",
              json={"anchor": _anchor(kind="param", param="size"), "body": "b"})

    assert [t["id"] for t in http.get(
        "/api/projects/demo/comments?kind=param").json()["threads"]] == ["2"]
    assert [t["id"] for t in http.get(
        "/api/projects/demo/comments?part_id=box&state=open"
    ).json()["threads"]] == ["1", "2"]
    cheap = http.get("/api/projects/demo/comments?resolve_anchors=false").json()
    assert "resolution" not in cheap["threads"][0]
    assert http.get(
        "/api/projects/demo/comments?state=closed").status_code == 422


def test_the_routes_map_errors_to_404_422(client):
    _service, _registry, http = client
    http.post("/api/projects/demo/comments",
              json={"anchor": _anchor(), "body": "a"})

    assert http.get("/api/projects/nope/comments").status_code == 404
    assert http.get("/api/projects/demo/comments/9").status_code == 404
    # An id reaches the store from a REST path segment, so it is whitelisted
    # (^[1-9][0-9]*$) before it touches the filesystem: anything else is a
    # 404, never a path.
    for bad in ("abc", "007", "0", "1%2F..%2F1"):
        assert http.get(
            f"/api/projects/demo/comments/{bad}").status_code == 404, bad
    assert http.post("/api/projects/demo/comments/9/resolve").status_code == 404
    assert http.patch("/api/projects/demo/comments/1/comments/9",
                      json={"body": "x"}).status_code == 404
    assert http.delete(
        "/api/projects/demo/comments/1/comments/1").status_code == 422
    both = http.post("/api/projects/demo/comments",
                     json={"anchor": _anchor(), "thread": "1", "body": "x"})
    assert both.status_code == 422
    assert "exactly one" in both.json()["error"]["message"]


def test_the_create_route_rejects_an_attachment_outside_exports(client):
    """AC9 at the API layer."""
    _service, _registry, http = client
    refused = http.post(
        "/api/projects/demo/comments",
        json={"anchor": _anchor(), "body": "a",
              "attachments": ["/etc/passwd"]})

    assert refused.status_code == 422
    assert "exports" in refused.json()["error"]["message"]


def test_the_route_bodies_are_whitelisted(client):
    """Never ``**body``: an unknown key is ignored, not forwarded into the
    registry's ``unexpected argument`` refusal."""
    _service, _registry, http = client
    created = http.post(
        "/api/projects/demo/comments",
        json={"anchor": _anchor(), "body": "a", "state": "resolved",
              "author": "somebody-else", "id": "99"})

    assert created.status_code == 200, created.text
    thread = created.json()["thread"]
    assert thread["id"] == "1"
    assert thread["state"] == "open"
    assert thread["author"] == "browser"


def test_an_empty_body_is_a_422_not_a_500(client):
    """The bytes are read, not the content-length header."""
    _service, _registry, http = client
    assert http.post("/api/projects/demo/comments").status_code == 422


def test_the_identity_header_reaches_the_thread(client):
    _service, _registry, http = client
    created = http.post("/api/projects/demo/comments",
                        json={"anchor": _anchor(), "body": "a"},
                        headers={"X-Agent-Id": "chat:main"})

    thread = created.json()["thread"]
    assert thread["author"] == "chat:main"
    assert thread["author_kind"] == "agent"  # bookkeeping, not authentication


# -------------------------------------------------------- 5. the MCP surface


def test_the_tools_are_listed_and_callable_over_http(client):
    _service, _registry, http = client
    names = {t["name"] for t in http.get("/api/tools").json()["tools"]}
    assert set(_TOOLS) <= names

    opened = http.post("/api/tools/add_comment",
                       json={"project": "demo", "anchor": _anchor(),
                             "body": "over http"})
    assert opened.status_code == 200, opened.text
    assert opened.json()["thread"]["id"] == "1"
    listed = http.post("/api/tools/list_comments", json={"project": "demo"})
    assert [t["id"] for t in listed.json()["threads"]] == ["1"]
