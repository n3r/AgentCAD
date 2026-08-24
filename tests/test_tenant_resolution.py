"""PRD-005 slice 8 — tenant resolution precedence, and the header-less paths.

`security.resolve_tenant` resolves a request's ``(org, workspace)`` from four
sources, in precedence order **token scope > header > ``?workspace=`` query >
memberships default**. The header is what an API client and a switched browser
send; the *query* is the same selection for the three surfaces a header cannot
ride — an ``<img src>`` GET, a ``sendBeacon`` and a WebSocket — and it is the
one this file is about (the header rungs are exercised in
``test_tenancy_integration``). A query is only a *selection*: a scoped token
above still wins, and membership is still checked, so it can neither move a
token nor reach a workspace the principal cannot see.

The second half is the thumbnail warmer (PRD-027 FR4 meets PRD-005 tenancy): a
system subscriber that must hear a tenanted ``rebuild_finished`` a plain
subscriber would drop, and render it under that event's own tenant.
"""

from __future__ import annotations

import json
import pathlib
import queue
from types import SimpleNamespace

import pytest

from tests.conftest import HOSTED_ORIGIN, login, make_test_service


@pytest.fixture(autouse=True)
def _restore_tenant():
    """Undo any tenant a test leaves set (the `test_tenancy_integration` fix)."""
    from agentcad.core import tenancy

    token = tenancy.tenant_var.set(None)
    try:
        yield
    finally:
        tenancy.tenant_var.reset(token)


@pytest.fixture
def app_ctx(kernel, tmp_path, monkeypatch):
    """A hosted app, tenancy wired. `nikita` admins acme (workspaces lab+main,
    so his default is the alphabetically-first `lab`); `bob` edits globex/main
    and nothing else."""
    from fastapi.testclient import TestClient

    from agentcad.core import tenancy_wiring
    from agentcad.core.appmode import AppMode
    from agentcad.core.authstore import AuthStore
    from agentcad.core.tenancy import TenancyStore
    from agentcad.core.tools import build_registry
    from agentcad.server import security as security_module
    from agentcad.server.app import create_app
    from agentcad.server.security import SecurityConfig

    monkeypatch.setenv("AGENTCAD_CONFIG", str(tmp_path / "cfg" / "config.json"))
    service = make_test_service(tmp_path / "projects", kernel)

    store = AuthStore(tmp_path / "auth")
    for handle, role in (("nikita", "admin"), ("bob", "member")):
        store.enrol(store.add_user(handle, role=role), f"pw for {handle} 1234")

    orgs = TenancyStore(tmp_path / "auth")
    orgs.create_org("acme", "Acme Robotics")
    orgs.create_workspace("acme", "lab", "Lab")
    orgs.create_workspace("acme", "main", "Mechanical")
    orgs.add_member("acme", "nikita", "admin")
    orgs.create_org("globex", "Globex")
    orgs.create_workspace("globex", "main")
    orgs.add_member("globex", "bob", "edit")

    cfg = SecurityConfig(mode=AppMode("hosted", HOSTED_ORIGIN, b"k" * 32),
                         store=store)
    security_module.install(cfg)
    registry = build_registry(service)
    tenancy_wiring.install(service, registry)
    app = create_app(service, registry, extra_allowed_hosts={"testserver"},
                     security=cfg)

    def as_(handle):
        client = TestClient(app, base_url=HOSTED_ORIGIN)
        client.agentcad_store = store
        return login(client, handle)

    ctx = SimpleNamespace(app=app, service=service, store=store, orgs=orgs,
                          cfg=cfg, registry=registry, as_=as_,
                          projects=tmp_path / "projects")
    try:
        yield ctx
    finally:
        security_module.install(None)
        tenancy_wiring.uninstall(service, registry)


# ----------------------------------------------------- the query fallback

def test_query_param_selects_the_workspace(app_ctx):
    """nikita's default is `acme/lab` (first of two); `?workspace=acme/main`
    places the write in main instead."""
    made = app_ctx.as_("nikita").post("/api/projects?workspace=acme/main",
                                      json={"name": "widget"})
    assert made.status_code == 201
    assert (app_ctx.projects / "orgs" / "acme" / "main" / "widget").is_dir()
    assert not (app_ctx.projects / "orgs" / "acme" / "lab").exists()


def test_the_header_beats_the_query_param(app_ctx):
    """Both present: the header wins, the query is the fallback for its absence."""
    made = app_ctx.as_("nikita").post(
        "/api/projects?workspace=acme/main", json={"name": "widget"},
        headers={"X-Agentcad-Workspace": "acme/lab"})
    assert made.status_code == 201
    assert (app_ctx.projects / "orgs" / "acme" / "lab" / "widget").is_dir()
    assert not (app_ctx.projects / "orgs" / "acme" / "main").exists()


@pytest.mark.parametrize("raw", ["acme", "acme/main/extra", "ACME/main",
                                 "acme/", "/main"])
def test_a_malformed_query_param_is_a_400(app_ctx, raw):
    answer = app_ctx.as_("nikita").get(f"/api/projects?workspace={raw}")
    assert answer.status_code == 400
    assert answer.json()["error"]["type"] == "ValidationError"


def test_a_query_param_workspace_you_cannot_see_is_a_name_free_404(app_ctx):
    """bob is a member of globex only; a query naming acme is a name-free 404,
    identical to one naming a workspace that does not exist at all."""
    denied = app_ctx.as_("bob").get("/api/projects?workspace=acme/main")
    assert denied.status_code == 404
    assert denied.json()["error"]["message"] == "no such workspace"
    assert "acme" not in json.dumps(denied.json())
    missing = app_ctx.as_("bob").get("/api/projects?workspace=nowhere/main")
    assert missing.status_code == 404
    assert missing.json() == denied.json()


def test_a_query_param_cannot_move_a_scoped_token(app_ctx, monkeypatch):
    """A scoped token places the request; a `?workspace=` naming somewhere else
    cannot redirect it. The token is scoped to `acme/lab` and holds a grant
    there; a query pointing at `acme/main` (where the agent has nothing) is
    ignored, so the read succeeds and returns lab's widget."""
    from fastapi.testclient import TestClient

    monkeypatch.setattr(
        app_ctx.store, "resolve_token",
        lambda presented: ({"name": "ci", "role": "member",
                            "scope": {"org": "acme", "workspace": "lab"}}
                           if presented == "tok" else None))

    # `widget` exists ONLY in lab, and the agent is granted there.
    nikita = app_ctx.as_("nikita")
    assert nikita.post("/api/projects?workspace=acme/lab",
                       json={"name": "widget"}).status_code == 201
    app_ctx.orgs.grant_role("acme", "lab", "widget", "agent:ci", "edit")

    client = TestClient(app_ctx.app, base_url=HOSTED_ORIGIN)
    # The query names `acme/main`, where no widget exists and the agent holds
    # nothing. It is ignored: the scope resolves `acme/lab` and the read of
    # lab's widget succeeds. Had the query moved the scope, this would be a miss.
    got = client.get("/api/projects/widget?workspace=acme/main",
                     headers={"Authorization": "Bearer tok"})
    assert got.status_code == 200, got.text     # the scope (lab) won the query
    assert (app_ctx.projects / "orgs" / "acme" / "main").exists() is False


# ------------------------------------------------- the thumbnail warmer

def test_the_thumbnail_warmer_is_a_system_subscriber(app_ctx):
    """A tenanted `rebuild_finished` a plain (untenanted) subscriber drops still
    reaches the warmer — the pre-warm was dead on every hosted instance with
    orgs until it subscribed through the wrapper's unfiltered seam (Lens A #5)."""
    from agentcad.core import thumbnails

    service = app_ctx.service
    plain = service.bus.subscribe()             # untenanted -> filtered
    warmer = thumbnails.ThumbnailWarmer(service)
    warmer.start()
    try:
        service.bus.publish({"type": "rebuild_finished", "project": "widget",
                             "cache_key": "a" * 32, "tenant": "acme/main"})
        warmer.drain()
    finally:
        warmer.stop()

    with pytest.raises(queue.Empty):
        plain.get_nowait()                       # the plain subscriber never saw it
    # The warmer did, and processed it (no mesh under acme/main here).
    assert warmer.stats["skipped_missing"] == 1


def test_the_warmer_renders_under_the_events_tenant(app_ctx, tmp_path,
                                                    monkeypatch):
    """The event's `tenant` is carried into a `tenant_scope` around the render,
    so `cache_dir` resolves the workspace that built the key — not the
    untenanted root."""
    from agentcad.core import tenancy, thumbnails

    seen = {}

    def fake_render(cache, key, write=True):
        seen["tenant"] = tenancy.current_tenant()
        return b"PNG"

    monkeypatch.setattr(app_ctx.service.store, "cache_dir",
                        lambda proj: tmp_path)
    monkeypatch.setattr(thumbnails, "mesh_for_key",
                        lambda cache, key: tmp_path / "mesh.acm")
    monkeypatch.setattr(thumbnails, "may_write", lambda service, proj: True)
    monkeypatch.setattr(thumbnails, "render_part_thumb", fake_render)

    warmer = thumbnails.ThumbnailWarmer(app_ctx.service)
    warmer._render_one("widget", "a" * 32, "acme/main")
    assert seen["tenant"] == ("acme", "main")
    assert warmer.stats["rendered"] == 1

    # A missing/malformed stamp resolves as no tenant, never a crash.
    warmer._render_one("widget", "b" * 32, None)
    assert seen["tenant"] is None
    warmer._render_one("widget", "c" * 32, "not-a-stamp")
    assert seen["tenant"] is None
