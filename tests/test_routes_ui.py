"""PRD-026 slice 5 — ``POST /api/ui/events`` (design Decision 7).

The browser's UX telemetry lane: the shell posts fire-and-forget when a dialog
opens, a dialog is submitted, or the palette runs something, and the server
re-publishes it on the bus so agents (and other browsers) can see what a human
just did.

It is the smallest possible surface on purpose — three event types, three
optional string keys, 80 characters each — because it accepts input from a
page and re-broadcasts it to every connected client. Everything outside the
allow-list is a 422, and the route is member-only in hosted mode (it is not in
``PUBLIC_PATHS``, and nothing here adds it).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agentcad.core.tools import build_registry
from agentcad.server.app import create_app

from .conftest import ADMIN_PASSWORD, make_test_service

TYPES = ["dialog_opened", "dialog_submitted", "palette_executed"]


@pytest.fixture
def client(kernel, tmp_path):
    service = make_test_service(tmp_path / "projects", kernel)
    app = create_app(service, build_registry(service),
                     extra_allowed_hosts={"testserver"})
    return service, TestClient(app, base_url="http://127.0.0.1")


# ------------------------------------------------------------- the happy path

@pytest.mark.parametrize("kind", TYPES)
def test_each_type_is_accepted_and_republished(client, kind):
    service, http = client
    q = service.bus.subscribe()
    response = http.post("/api/ui/events", json={"type": kind, "view": "settings"})
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert q.get_nowait() == {"type": kind, "view": "settings",
                              "by": "browser", "client": None}


def test_all_three_payload_keys_ride_along_and_the_client_id_is_the_header(client):
    service, http = client
    q = service.bus.subscribe()
    body = {"type": "palette_executed", "view": "palette",
            "action": "part.new", "tool": "create_part"}
    assert http.post("/api/ui/events", json=body,
                     headers={"X-Agent-Id": "browser:7f3a"}).status_code == 200
    assert q.get_nowait() == {
        "type": "palette_executed",
        "view": "palette",
        "action": "part.new",
        "tool": "create_part",
        "by": "browser",
        "client": "browser:7f3a",
    }


def test_the_payload_keys_are_all_optional(client):
    service, http = client
    q = service.bus.subscribe()
    assert http.post("/api/ui/events",
                     json={"type": "dialog_opened"}).status_code == 200
    assert q.get_nowait() == {"type": "dialog_opened", "by": "browser",
                              "client": None}


def test_eighty_characters_is_allowed(client):
    _, http = client
    assert http.post("/api/ui/events",
                     json={"type": "dialog_opened",
                           "view": "v" * 80}).status_code == 200


# -------------------------------------------------------------------- the 422s

@pytest.mark.parametrize("body", [
    {},                                                  # no type at all
    {"type": "dialog_closed"},                           # not in the allow-list
    {"type": "ui_open"},                                 # not the agent's event
    {"type": None},
    {"type": 3},
    {"type": "dialog_opened", "project": "demo"},        # extra key
    {"type": "dialog_opened", "by": "agent"},            # cannot forge `by`
    {"type": "dialog_opened", "client": "someone-else"},  # nor `client`
    {"type": "dialog_opened", "view": "v" * 81},         # one over
    {"type": "dialog_opened", "view": 7},                # not a string
    {"type": "dialog_opened", "tool": None},             # nor a null
])
def test_anything_outside_the_allow_list_is_422(client, body):
    service, http = client
    q = service.bus.subscribe()
    response = http.post("/api/ui/events", json=body)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["type"] == "ValidationError"
    assert q.qsize() == 0, "a refused event must publish nothing"


@pytest.mark.parametrize("body", ["a string", 3, ["dialog_opened"], None])
def test_a_body_that_is_not_an_object_is_422(client, body):
    _, http = client
    assert http.post("/api/ui/events", json=body).status_code == 422


def test_an_absent_body_is_422_rather_than_a_500(client):
    _, http = client
    assert http.post("/api/ui/events").status_code == 422


# ------------------------------------------------------------- hosted mode

def test_the_route_is_member_only_in_hosted_mode(hosted):
    """Default-deny: a new pack is private with no action by its author. The
    anonymous surface is `PUBLIC_PATHS` and this is not in it — asserted here
    against the running app, and by set-equality in
    `tests/test_hosted_surface.py`."""
    http, _ = hosted
    body = {"type": "dialog_opened", "view": "settings"}

    anonymous = http.post("/api/ui/events", json=body)
    assert anonymous.status_code != 200
    assert anonymous.status_code == 401

    http.post("/api/auth/login", json={"handle": "nikita",
                                       "password": ADMIN_PASSWORD})
    member = http.post("/api/ui/events", json=body)
    assert member.status_code == 200
    assert member.json() == {"ok": True}
