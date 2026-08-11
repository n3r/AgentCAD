"""PRD-008 slice 6: presence — the registry, the heartbeat, browser identity.

Four sections: 1. the registry · 2. the heartbeat routes · 3. the event ·
4. the seams (identity, mentions).

The one thing worth stating before reading: **presence arrives over HTTP, not
over the WebSocket** (design Decision 13). ``/ws`` lives in ``server/app.py``,
a core the extension-point contract forbids editing; it carries no client
identity, because ``set_client_id`` is HTTP middleware only; and its Host guard
is HTTP middleware too. So the tests below drive presence with ordinary
requests, and the assertion that matters most is that the heartbeat *response*
carries the whole roster: ``presence_changed`` is an optimization, and a client
that misses every event still converges within one heartbeat. That is also why
there is no reaper thread to test — expiry is computed on read, which
:func:`test_expiry_is_lazy_and_starts_no_thread` pins.
"""

from __future__ import annotations

import queue
import re
import shutil
import threading

import pytest
from fastapi.testclient import TestClient

from agentcad.core import locks, presence
from agentcad.core.model import ValidationError
from agentcad.core.presence import PRESENCE_TTL_S, PresenceRegistry
from agentcad.core.service import AgentCADService, EventBus
from agentcad.core.tools import build_registry
from agentcad.server.app import create_app

from .conftest import BOX_SCRIPT

_GIT = pytest.mark.skipif(shutil.which("git") is None,
                          reason="git not found on PATH")


class Clock:
    """An injected clock — presence takes one, so no test has to monkeypatch
    the global ``time`` module out from under a running kernel process."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture(autouse=True)
def _reset_identity():
    token = locks.client_id_var.set("browser")
    yield
    locks.client_id_var.reset(token)


@pytest.fixture
def demo(kernel, tmp_path):
    service = AgentCADService(tmp_path / "projects", kernel, EventBus())
    registry = build_registry(service)
    assert "error" not in registry.call("create_project", {"name": "demo"})
    assert "error" not in registry.call(
        "create_part",
        {"project": "demo", "part_id": "box", "script": BOX_SCRIPT})
    return service, registry


@pytest.fixture
def client(demo):
    service, registry = demo
    app = create_app(service, registry, extra_allowed_hosts={"testserver"})
    return service, registry, TestClient(app, base_url="http://127.0.0.1")


def _beat(client, who: str, **body) -> dict:
    response = client.post("/api/projects/demo/presence", json=body,
                           headers={"X-Agent-Id": who})
    assert response.status_code == 200, response.text
    return response.json()


def _drain(subscription) -> list[dict]:
    events = []
    while True:
        try:
            events.append(subscription.get_nowait())
        except queue.Empty:
            return events


def _ids(payload: dict) -> list[str]:
    return sorted(c["id"] for c in payload["clients"])


# ------------------------------------------------------------ 1. the registry


def test_roster_join_leave_and_ttl_expiry():
    clock = Clock()
    reg = PresenceRegistry(clock=clock)

    reg.touch("demo", "browser:aaaaaaaa", project="demo", surface="viewport")
    reg.touch("demo", "chat:main", project="demo", surface="editor")
    assert sorted(c["id"] for c in reg.roster("demo")) == [
        "browser:aaaaaaaa", "chat:main"]

    reg.leave("demo", "chat:main")
    assert [c["id"] for c in reg.roster("demo")] == ["browser:aaaaaaaa"]

    # Unrefreshed entries expire on their own; a refreshed one does not.
    clock.advance(PRESENCE_TTL_S / 2)
    reg.touch("demo", "browser:aaaaaaaa", project="demo", surface="viewport")
    clock.advance(PRESENCE_TTL_S / 2 + 1)
    assert [c["id"] for c in reg.roster("demo")] == ["browser:aaaaaaaa"]
    clock.advance(PRESENCE_TTL_S)
    assert reg.roster("demo") == []


def test_expiry_is_lazy_and_starts_no_thread():
    """No reaper thread (design Decision 13): a stale entry is dropped by the
    read that notices it, so the server owns no new lifecycle."""
    clock = Clock()
    reg = PresenceRegistry(clock=clock)
    before = threading.active_count()

    reg.touch("demo", "browser:aaaaaaaa", project="demo")
    clock.advance(PRESENCE_TTL_S * 10)
    assert threading.active_count() == before
    # Still in the dict — nothing has looked yet — and gone once something has.
    assert reg._clients                                  # noqa: SLF001
    assert reg.roster("demo") == []
    assert not reg._clients                              # noqa: SLF001


def test_the_roster_is_keyed_by_lock_key_so_branches_do_not_mix():
    reg = PresenceRegistry(clock=Clock())
    reg.touch("demo", "browser:a", project="demo")
    reg.touch("/trees/feature", "browser:b", project="demo")

    assert [c["id"] for c in reg.roster("demo")] == ["browser:a"]
    assert [c["id"] for c in reg.roster("/trees/feature")] == ["browser:b"]


def test_kind_is_derived_and_never_taken_from_the_client():
    reg = PresenceRegistry(clock=Clock())
    reg.touch("demo", "browser:7f3a1b2c", project="demo")
    reg.touch("demo", "chat:main", project="demo")

    kinds = {c["id"]: c["kind"] for c in reg.roster("demo")}
    assert kinds == {"browser:7f3a1b2c": "human", "chat:main": "agent"}


def test_a_label_is_display_only_capped_and_defaulted():
    reg = PresenceRegistry(clock=Clock())
    reg.touch("demo", "browser:a", project="demo", label="x" * 200)
    reg.touch("demo", "chat:main", project="demo")

    labels = {c["id"]: c["label"] for c in reg.roster("demo")}
    assert len(labels["browser:a"]) == presence.MAX_LABEL_CHARS
    # No label: the identity itself, never an invented display name.
    assert labels["chat:main"] == "chat:main"


def test_an_unknown_surface_is_a_validation_error():
    reg = PresenceRegistry(clock=Clock())
    with pytest.raises(ValidationError) as excinfo:
        reg.touch("demo", "browser:a", project="demo", surface="telepathy")
    assert set(excinfo.value.details["known"]) == set(presence.SURFACES)


def test_touch_reports_whether_the_roster_actually_changed():
    """``presence_changed`` is an optimization, so it must not fire on the
    15-second heartbeat five idle clients send."""
    clock = Clock()
    reg = PresenceRegistry(clock=clock)

    assert reg.touch("demo", "browser:a", project="demo",
                     surface="viewport")[1] is True          # a join
    clock.advance(1)
    assert reg.touch("demo", "browser:a", project="demo",
                     surface="viewport")[1] is False         # a no-op beat
    assert reg.touch("demo", "browser:a", project="demo",
                     surface="editor")[1] is True            # a focus change
    assert reg.leave("demo", "browser:a") is True
    assert reg.leave("demo", "browser:a") is False


def test_mention_ids_span_branches_and_expire():
    """``CommentManager._present_ids`` calls this and nothing else: a mention
    of a present client is plausible, on whichever branch that client is."""
    clock = Clock()
    reg = PresenceRegistry(clock=clock)
    reg.touch("demo", "browser:a", project="demo")
    reg.touch("/trees/feature", "cad-bot", project="demo")
    reg.touch("other", "browser:c", project="other")

    assert reg.mention_ids("demo") == {"browser:a", "cad-bot"}
    clock.advance(PRESENCE_TTL_S + 1)
    assert reg.mention_ids("demo") == set()


def test_the_token_bucket_refills_at_one_per_second():
    clock = Clock()
    bucket = presence.TokenBucket(clock=clock)
    assert [bucket.take("browser:a") for _ in range(7)] == (
        [True] * 5 + [False, False])
    # Another identity has its own bucket.
    assert bucket.take("browser:b") is True
    clock.advance(2.0)
    assert [bucket.take("browser:a") for _ in range(3)] == [True, True, False]


# ------------------------------------------------------ 2. the heartbeat route


def test_post_registers_and_get_only_reads(client):
    service, _registry, http = client

    payload = _beat(http, "browser:aaaa", surface="viewport", part_id="box")
    assert payload["you"] == "browser:aaaa"
    assert payload["ttl_s"] == PRESENCE_TTL_S
    assert payload["heartbeat_s"] == presence.PRESENCE_HEARTBEAT_S
    assert payload["clients"][0]["focus"] == {"part_id": "box",
                                              "surface": "viewport"}
    assert payload["claims"] == {}

    # GET answers with the same roster and registers nobody.
    seen = http.get("/api/projects/demo/presence",
                    headers={"X-Agent-Id": "browser:bbbb"}).json()
    assert _ids(seen) == ["browser:aaaa"]
    assert seen["you"] == "browser:bbbb"
    assert _ids(_beat(http, "browser:cccc")) == ["browser:aaaa", "browser:cccc"]


def test_leave_removes_the_client(client):
    _service, _registry, http = client
    _beat(http, "browser:aaaa")
    _beat(http, "browser:bbbb")

    payload = _beat(http, "browser:bbbb", leave=True)
    assert _ids(payload) == ["browser:aaaa"]
    assert _ids(_beat(http, "browser:aaaa")) == ["browser:aaaa"]


def test_over_rate_heartbeats_answer_200_with_throttled(client):
    _service, _registry, http = client
    _beat(http, "browser:aaaa")  # the join, outside the burst assertions below

    payloads = [_beat(http, "browser:bbbb") for _ in range(8)]
    assert any(p.get("throttled") for p in payloads), \
        "a burst must be throttled, never a red toast"
    # Throttled or not, the answer is always the full roster: the response is
    # the mechanism, so a throttled client still converges.
    assert all(_ids(p) for p in payloads)
    assert all("browser:bbbb" in _ids(p) for p in payloads)


def test_a_bad_surface_is_422_and_an_unknown_project_is_404(client):
    _service, _registry, http = client

    bad = http.post("/api/projects/demo/presence", json={"surface": "aura"})
    assert bad.status_code == 422
    assert set(bad.json()["error"]["details"]["known"]) == set(presence.SURFACES)

    missing = http.post("/api/projects/nope/presence", json={})
    assert missing.status_code == 404


def test_presence_is_never_persisted(client, tmp_path):
    service, _registry, http = client
    _beat(http, "browser:aaaa", surface="editor", part_id="box", label="Ana")

    root = service.store.canonical_path_of("demo")
    blobs = [p for p in root.rglob("*") if p.is_file()
             and b"browser:aaaa" in p.read_bytes()]
    assert blobs == []
    # A fresh registry knows nobody: presence is a fact about *now*.
    assert PresenceRegistry().roster("demo") == []


# --------------------------------------------------------------- 3. the event


def test_presence_changed_fires_on_join_and_focus_but_not_on_a_noop(client):
    service, _registry, http = client
    subscription = service.bus.subscribe()

    _beat(http, "browser:aaaa", surface="viewport")
    joined = [e for e in _drain(subscription) if e["type"] == "presence_changed"]
    assert len(joined) == 1
    assert joined[0]["project"] == "demo"
    assert [c["id"] for c in joined[0]["clients"]] == ["browser:aaaa"]

    _beat(http, "browser:aaaa", surface="viewport")
    assert [e for e in _drain(subscription)
            if e["type"] == "presence_changed"] == []

    _beat(http, "browser:aaaa", surface="editor")
    moved = [e for e in _drain(subscription) if e["type"] == "presence_changed"]
    assert len(moved) == 1
    assert moved[0]["clients"][0]["focus"]["surface"] == "editor"

    _beat(http, "browser:aaaa", leave=True)
    left = [e for e in _drain(subscription) if e["type"] == "presence_changed"]
    assert len(left) == 1 and left[0]["clients"] == []

    service.bus.unsubscribe(subscription)


def test_a_comment_mutation_still_publishes_nothing_about_presence(client):
    """Presence must not leak into the model's event stream."""
    service, registry, http = client
    _beat(http, "browser:aaaa")
    subscription = service.bus.subscribe()

    assert "error" not in registry.call("add_comment", {
        "project": "demo", "body": "check this boss",
        "anchor": {"kind": "part", "part": "box"}})
    types = {e["type"] for e in _drain(subscription)}
    assert types == {"comment_changed"}

    service.bus.unsubscribe(subscription)


# --------------------------------------------------------------- 4. the seams


@_GIT
def test_a_freshly_minted_browser_identity_lands_on_the_default_branch(client):
    """R6: the identity change gives every existing user a client id that has
    no ``checkouts.json`` row. The first-run path must be a clean default."""
    _service, _registry, http = client
    fresh = {"X-Agent-Id": "browser:0badc0de"}

    branches = http.get("/api/projects/demo/branches", headers=fresh).json()
    assert branches["current"] == branches["default"]
    assert branches["you"] == "browser:0badc0de"

    # …and a write under that identity lands, on that branch.
    written = http.put("/api/projects/demo/parts/box",
                       json={"script": BOX_SCRIPT.replace("10.0", "11.0")},
                       headers=fresh)
    assert written.status_code == 200, written.text
    assert http.get("/api/projects/demo/branches",
                    headers=fresh).json()["current"] == branches["default"]


def test_a_present_client_id_is_a_plausible_mention(client):
    """The one seam ``CommentManager`` asks presence for: an id that is neither
    ``browser*`` nor ``chat*`` is mentionable exactly while it is here."""
    service, registry, http = client
    _beat(http, "cad-bot", label="Fixture bot")

    thread = registry.call("add_comment", {
        "project": "demo", "body": "@cad-bot please re-run this",
        "anchor": {"kind": "part", "part": "box"}})["thread"]
    assert thread["comments"][0]["mentions"] == ["cad-bot"]

    service.presence.leave(service.store.lock_key("demo"), "cad-bot")
    gone = registry.call("add_comment", {
        "project": "demo", "body": "@cad-bot are you there",
        "anchor": {"kind": "part", "part": "box"}})["thread"]
    assert gone["comments"][0]["mentions"] == []


def test_the_registry_is_installed_on_the_service_by_the_route_pack(client):
    service, _registry, _http = client
    assert isinstance(service.presence, PresenceRegistry)
    # Idempotent: building a second app must not swap the roster out from
    # under the first one's clients.
    same = service.presence
    create_app(service, _registry, extra_allowed_hosts={"testserver"})
    assert service.presence is same


def test_the_browser_mints_and_sends_a_per_profile_identity(client):
    """The frontend half of R6, asserted the way ``test_server.py`` asserts the
    theme bootstrap: over the served asset."""
    _service, _registry, http = client
    source = http.get("/js/api.js").text

    assert '"agentcad.client_id"' in source and "localStorage" in source
    assert re.search(r'"browser:"\s*\+', source), "identity must be browser:<hex>"
    # The four hand-rolled fetches bypass request(), so each needs the header
    # of its own or half the app would speak under a different identity.
    assert source.count("X-Agent-Id") == 5


def test_presence_js_heartbeats_and_leaves_on_pagehide(client):
    _service, _registry, http = client
    source = http.get("/js/presence.js").text

    assert "sendBeacon" in source and "pagehide" in source
    assert "presence" in http.get("/js/main.js").text
