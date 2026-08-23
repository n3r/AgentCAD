"""`server/routes_skills.py` — the human path over the skill library (PRD-029).

Reads go through the registry (so a browser preview logs `skill_loaded` like
every other surface); the three writes are human-only, because a skill is agent
instructions and no agent surface may approve them.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agentcad.core.service import EventBus
from agentcad.core.tools import build_registry
from agentcad.server.app import create_app

from .conftest import make_test_service
from .test_skills_library import write_skill
from .test_skills_tools import _UnusedKernel, _drain

PROJECT = "routeproj"


@pytest.fixture
def stack(tmp_path):
    bus = EventBus()
    service = make_test_service(tmp_path / "projects", _UnusedKernel(), bus)
    service.create_project(PROJECT)
    app = create_app(service, build_registry(service),
                     extra_allowed_hosts={"testserver"})
    client = TestClient(app, base_url="http://127.0.0.1")
    return service, client, bus


def project_skill(service, name="house-rules", **kwargs):
    root = service.store.path_of(PROJECT) / "skills"
    root.mkdir(parents=True, exist_ok=True)
    return write_skill(root, name, **kwargs)


# ----------------------------------------------------------------- the index

def test_the_index_carries_skills_hidden_and_trust(stack):
    service, client, _ = stack
    project_skill(service)

    body = client.get(f"/api/projects/{PROJECT}/skills").json()
    assert set(body) == {"skills", "hidden", "trust"}
    entry = next(e for e in body["skills"] if e["name"] == "house-rules")
    assert entry["layer"] == "project" and entry["trusted"] is False
    assert "snap-fits" in [e["name"] for e in body["skills"]]
    assert body["trust"] == {"version": 1, "trusted": {}, "disabled": []}


def test_an_unknown_project_is_404(stack):
    _, client, _ = stack
    assert client.get("/api/projects/nope/skills").status_code == 404


# --------------------------------------------------------------- the content

def test_reading_a_core_skill_returns_content_and_logs_the_load(stack):
    _, client, bus = stack
    queue = bus.subscribe()

    body = client.get(f"/api/projects/{PROJECT}/skills/snap-fits").json()
    assert body["layer"] == "core" and body["chars"] == len(body["content"])
    assert body["provenance"]["path"] is None

    event = next(e for e in _drain(queue) if e["type"] == "skill_loaded")
    assert event["client"] == "browser" and event["project"] == PROJECT


def test_an_unknown_or_malformed_name_is_404(stack):
    _, client, _ = stack
    base = f"/api/projects/{PROJECT}/skills"
    assert client.get(f"{base}/no-such-skill").status_code == 404
    # Refused before it reaches the library: a name is a slug, always.
    assert client.get(f"{base}/Bad_Name").status_code == 404
    assert client.post(f"{base}/Bad_Name/trust").status_code == 404


def test_an_untrusted_project_skill_is_422_until_a_human_trusts_it(stack):
    service, client, _ = stack
    project_skill(service, body="Ours.\n")
    url = f"/api/projects/{PROJECT}/skills/house-rules"

    refusal = client.get(url)
    assert refusal.status_code == 422
    assert refusal.json()["error"]["details"]["reason"] == "skill_untrusted"

    assert client.post(f"{url}/trust").status_code == 200
    assert "Ours." in client.get(url).json()["content"]


def test_an_asset_reads_through_the_same_route(stack, tmp_path):
    service, client, _ = stack
    from agentcad.core.skills import SkillLibrary

    core = tmp_path / "core-skills"
    write_skill(core, "widgets")
    snippet = core / "widgets" / "snippets" / "x.py"
    snippet.parent.mkdir(parents=True)
    snippet.write_text("PARAMS = {}\n", encoding="utf-8")
    service.skills = SkillLibrary(service.store, core_dir=core)

    url = f"/api/projects/{PROJECT}/skills/widgets"
    assert client.get(url, params={"asset": "snippets/x.py"}).json()[
        "content"] == "PARAMS = {}\n"
    assert client.get(url, params={"asset": "../../etc/passwd"}
                      ).status_code in (404, 422)


# ------------------------------------------------------------- human-only

def test_trust_and_untrust_from_a_browser_client(stack):
    service, client, bus = stack
    project_skill(service)
    url = f"/api/projects/{PROJECT}/skills/house-rules"
    queue = bus.subscribe()

    entry = client.post(f"{url}/trust").json()
    assert entry["name"] == "house-rules" and entry["trusted"] is True
    assert {"type": "skills_changed", "project": PROJECT} in _drain(queue)
    index = client.get(f"/api/projects/{PROJECT}/skills").json()
    assert next(e for e in index["skills"]
                if e["name"] == "house-rules")["trusted"] is True
    assert index["trust"]["trusted"]["house-rules"]

    assert client.post(f"{url}/untrust").json()["trusted"] is False
    assert client.get(url).status_code == 422


def test_an_agent_client_cannot_trust_a_skill(stack):
    service, client, _ = stack
    project_skill(service)
    url = f"/api/projects/{PROJECT}/skills/house-rules"

    for path in (f"{url}/trust", f"{url}/untrust"):
        refused = client.post(path, headers={"X-Agent-Id": "mcp"})
        assert refused.status_code == 403, path
    refused = client.patch(f"{url}/enabled", json={"enabled": False},
                           headers={"X-Agent-Id": "chat"})
    assert refused.status_code == 403
    # And nothing landed.
    body = client.get(f"/api/projects/{PROJECT}/skills").json()
    assert body["trust"] == {"version": 1, "trusted": {}, "disabled": []}


def test_disabling_a_skill_hides_it_from_the_index(stack):
    _, client, bus = stack
    url = f"/api/projects/{PROJECT}/skills/snap-fits/enabled"
    queue = bus.subscribe()

    entry = client.patch(url, json={"enabled": False}).json()
    assert entry["enabled"] is False
    assert {"type": "skills_changed", "project": PROJECT} in _drain(queue)

    body = client.get(f"/api/projects/{PROJECT}/skills").json()
    assert "snap-fits" not in [e["name"] for e in body["skills"]]
    hidden = next(e for e in body["hidden"] if e["name"] == "snap-fits")
    assert hidden["reason"] == "disabled"
    assert body["trust"]["disabled"] == ["snap-fits"]
    assert client.get(f"/api/projects/{PROJECT}/skills/snap-fits"
                      ).status_code == 422

    assert client.patch(url, json={"enabled": True}).json()["enabled"] is True
    assert client.get(f"/api/projects/{PROJECT}/skills/snap-fits"
                      ).status_code == 200


def test_the_enabled_body_is_strict(stack):
    _, client, _ = stack
    url = f"/api/projects/{PROJECT}/skills/snap-fits/enabled"
    assert client.patch(url, json={}).status_code == 422
    assert client.patch(url, json={"enabled": "false"}).status_code == 422
    assert client.patch(url, json=[1, 2]).status_code == 422
    assert client.patch(url, content=b"").status_code == 422


def test_trusting_an_unknown_skill_is_404(stack):
    _, client, _ = stack
    assert client.post(
        f"/api/projects/{PROJECT}/skills/no-such-skill/trust"
    ).status_code == 404
    assert client.patch(
        f"/api/projects/{PROJECT}/skills/no-such-skill/enabled",
        json={"enabled": False}).status_code == 404


# ------------------------------------------------------------------ hosted

def test_the_skill_routes_are_member_only(hosted_client):
    """Nothing anonymous — the surface equality test in
    `test_hosted_surface.py` is the other half of this assertion."""
    from .conftest import login

    service = hosted_client.agentcad_service
    service.create_project(PROJECT)
    base = f"/api/projects/{PROJECT}/skills"

    for method, path in (("GET", base), ("GET", f"{base}/snap-fits"),
                         ("POST", f"{base}/snap-fits/trust"),
                         ("POST", f"{base}/snap-fits/untrust"),
                         ("PATCH", f"{base}/snap-fits/enabled")):
        assert hosted_client.request(method, path).status_code == 401, path

    login(hosted_client)
    body = hosted_client.get(base).json()
    assert "snap-fits" in [e["name"] for e in body["skills"]]
    # A signed-in person is a human, so the writes are theirs to make.
    assert hosted_client.patch(f"{base}/snap-fits/enabled",
                               json={"enabled": False}).status_code == 200
