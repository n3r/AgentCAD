"""PRD-026 slice 5 — the ``ui_open`` tool pack (design Decision 7).

Three things this proves, because all three are the tool's contract rather
than its implementation:

1. **It is registered, unconditionally, and reachable over HTTP.** The MCP
   server mirrors ``GET /api/tools`` 1:1 (``agentcad/agent/mcp_server.py``),
   so a tool that lists there lists in MCP with no further wiring.
2. **It is capability-honest.** ``ui_open`` is a *broadcast* on the event bus
   — it cannot know whether anything acted on it, so it reports how many
   clients the publish reached (``delivered_to``) and says plainly when that
   is zero, the ``tools_history`` ``available: False`` precedent.
3. **It refuses like every other tool**: an ``AppError`` subclass raised from
   the handler, which ``ToolRegistry.call`` turns into the
   ``{"error": {"type": "validation_error", …}}`` payload an agent reads —
   and a ``200`` with that payload over ``POST /api/tools/ui_open``, never a
   4xx (the hosted-mode rule: a tool refusal is a payload, not a status).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agentcad.core import tools_ui
from agentcad.core.model import ValidationError
from agentcad.core.service import EventBus
from agentcad.core.tools import build_registry
from agentcad.server.app import create_app

from .conftest import make_test_service


@pytest.fixture(autouse=True)
def _fresh_bucket():
    """The token bucket is module-level (one per process, by design), so it
    leaks across tests unless every test starts from a full one."""
    tools_ui._reset_bucket()
    yield
    tools_ui._reset_bucket()


@pytest.fixture
def service(kernel, tmp_path):
    return make_test_service(tmp_path / "projects", kernel)


@pytest.fixture
def registry(service):
    return build_registry(service)


# ----------------------------------------------------------- registration

def test_ui_open_is_registered_with_a_view_and_an_optional_args_object(registry):
    tool = registry.get("ui_open")
    assert tool is not None
    schema = tool.input_schema
    assert schema["required"] == ["view"]
    assert set(schema["properties"]) == {"view", "args"}
    assert schema["properties"]["view"]["type"] == "string"
    assert schema["properties"]["args"]["type"] == "object"


def test_the_pack_registers_no_gate_provider(service):
    """The `tools_run_checks` load-order trap: `tools_ui` sorts AFTER
    `tools_proposals`, whose unconditional `gate_providers = []` would wipe a
    list a later pack appended to. It appends nothing — assert it, so a future
    edit that reaches for the seam has to notice."""
    from agentcad.core.tools import ToolRegistry

    service.gate_providers = ["sentinel"]
    tools_ui.register(ToolRegistry(), service)
    assert service.gate_providers == ["sentinel"]


def test_it_is_listed_over_http_which_is_what_mcp_mirrors(service, registry):
    app = create_app(service, registry, extra_allowed_hosts={"testserver"})
    client = TestClient(app, base_url="http://127.0.0.1")
    tools = {t["name"]: t for t in client.get("/api/tools").json()["tools"]}
    assert "ui_open" in tools
    assert tools["ui_open"]["input_schema"]["required"] == ["view"]


# -------------------------------------------------------------- refusals

@pytest.mark.parametrize("view", [
    "",                 # empty
    "Dialog",           # uppercase
    "9lives",           # leading digit
    "has space",
    "under_score",      # underscore is not in the class
    "a" * 41,           # one over the 40-char cap
])
def test_a_view_outside_the_pattern_is_a_validation_error(registry, view):
    result = registry.call("ui_open", {"view": view})
    assert result["error"]["type"] == "validation_error"
    assert "view" in result["error"]["message"]


def test_a_missing_view_is_the_registry_own_argument_refusal(registry):
    assert registry.call("ui_open", {})["error"]["type"] == "invalid_arguments"


def test_non_object_args_is_refused(registry, service):
    """Two guards, deliberately: the declared schema (so the refusal is
    already in `GET /api/tools`) and the handler's own check, which is what a
    non-HTTP caller meets."""
    envelope = registry.call("ui_open", {"view": "settings", "args": [1, 2]})
    assert envelope["error"]["type"] == "invalid_arguments"

    with pytest.raises(ValidationError):
        registry.get("ui_open").handler(view="settings", args=[1, 2])
    assert service.bus.subscriber_count() == 0


def test_args_over_4096_bytes_is_refused(registry):
    result = registry.call("ui_open", {"view": "settings",
                                       "args": {"blob": "x" * 5000}})
    error = result["error"]
    assert error["type"] == "validation_error"
    assert "4096" in error["message"]
    assert error["details"]["limit"] == 4096
    assert error["details"]["bytes"] > 4096


def test_args_just_under_the_limit_is_accepted(registry):
    payload = {"blob": "x" * 4000}
    assert registry.call("ui_open", {"view": "settings",
                                     "args": payload})["ok"] is True


def test_a_refusal_publishes_nothing(registry, service):
    q = service.bus.subscribe()
    registry.call("ui_open", {"view": "NOPE"})
    assert q.qsize() == 0


def test_a_refusal_over_http_is_a_200_with_an_error_payload(service, registry):
    app = create_app(service, registry, extra_allowed_hosts={"testserver"})
    client = TestClient(app, base_url="http://127.0.0.1")
    response = client.post("/api/tools/ui_open", json={"view": "NOPE"})
    assert response.status_code == 200
    assert response.json()["error"]["type"] == "validation_error"


# ------------------------------------------------------ the happy publish

def test_delivered_to_is_zero_and_says_so_when_no_browser_is_connected(registry):
    assert registry.call("ui_open", {"view": "settings"}) == {
        "ok": True,
        "view": "settings",
        "args": {},
        "delivered_to": 0,
        "note": "no browser is connected; nothing will open",
    }


def test_one_subscriber_is_one_delivery_and_the_event_shape_is_this(
        registry, service):
    q = service.bus.subscribe()
    result = registry.call("ui_open", {"view": "part-settings",
                                       "args": {"part_id": "plate"}})
    assert result == {
        "ok": True,
        "view": "part-settings",
        "args": {"part_id": "plate"},
        "delivered_to": 1,
        "note": "published to 1 connected client(s)",
    }
    assert q.get_nowait() == {
        "type": "ui_open",
        "view": "part-settings",
        "args": {"part_id": "plate"},
        "by": "agent",
    }


def test_two_subscribers_are_two_deliveries(registry, service):
    service.bus.subscribe()
    service.bus.subscribe()
    result = registry.call("ui_open", {"view": "settings"})
    assert result["delivered_to"] == 2
    assert result["note"] == "published to 2 connected client(s)"


def test_subscriber_count_tracks_subscribe_and_unsubscribe():
    bus = EventBus()
    assert bus.subscriber_count() == 0
    a = bus.subscribe()
    b = bus.subscribe()
    assert bus.subscriber_count() == 2
    bus.unsubscribe(a)
    assert bus.subscriber_count() == 1
    bus.unsubscribe(b)
    assert bus.subscriber_count() == 0


# ------------------------------------------------------------ rate limit

def test_the_eleventh_open_in_the_window_is_refused_and_recovers(
        registry, service, monkeypatch):
    clock = {"t": 1_000.0}
    monkeypatch.setattr(tools_ui, "_now", lambda: clock["t"])
    tools_ui._reset_bucket()
    q = service.bus.subscribe()

    for i in range(10):
        assert registry.call("ui_open", {"view": "settings"})["ok"] is True, i
    assert q.qsize() == 10

    refused = registry.call("ui_open", {"view": "settings"})
    assert refused["error"] == {
        "type": "validation_error",
        "message": "ui_open rate limit: 10 per 10 s",
        "details": {"retry_after_s": 1.0},
    }
    assert q.qsize() == 10, "a refused open must publish nothing"

    # 10 per 10 s refills at one token a second.
    clock["t"] += 1.0
    assert registry.call("ui_open", {"view": "settings"})["ok"] is True
    assert q.qsize() == 11

    # And the whole window back gives the whole bucket back, never more.
    clock["t"] += 60.0
    for _ in range(10):
        assert registry.call("ui_open", {"view": "settings"})["ok"] is True
    assert registry.call("ui_open", {"view": "settings"})["error"]["type"] == \
        "validation_error"


def test_the_bucket_is_per_process_not_per_registry(service, monkeypatch):
    """Two registries over one process share the limit — that is the point:
    the thing being protected is the browser, and there is one of it."""
    clock = {"t": 5.0}
    monkeypatch.setattr(tools_ui, "_now", lambda: clock["t"])
    tools_ui._reset_bucket()
    a, b = build_registry(service), build_registry(service)
    for _ in range(10):
        assert a.call("ui_open", {"view": "settings"})["ok"] is True
    assert b.call("ui_open", {"view": "settings"})["error"]["type"] == \
        "validation_error"
