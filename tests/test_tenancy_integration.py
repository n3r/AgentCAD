"""PRD-005 slice 4 — tenancy where it meets the running service.

Slices 1 and 2 shipped the model, the ladder and the sync server with their
seams left open; this suite is about what happens once they are wired: a
request resolves a tenant, the store roots under it, the ladder is enforced at
three choke points, and two orgs that both own a project called ``widget``
cannot see, lock, address or hear each other.

The one property every test here is really about is the *negative* one, so it
is stated once: **without a tenant, nothing in this file changes anything.**
The broad proof is the rest of the suite (`test_service.py`, `test_locks.py`,
`test_presence.py` and every route test run unchanged with the wrappers
installable); the narrow proof is `test_local_mode_*` below, which installs
every wrapper and then asserts each one is its own identity function.
"""

from __future__ import annotations

import importlib
import json
import queue

import pytest

from agentcad.core import audit, authz, locks, tenancy, tenancy_wiring
from agentcad.core.tenancy import TenancyStore

from tests.conftest import HOSTED_ORIGIN, login, make_test_service

ACME, GLOBEX, WS = "acme", "globex", "main"


@pytest.fixture(autouse=True)
def _restore_tenant():
    """Undo any tenant a test leaves set, for every test in this file.

    `tenancy.tenant_var` is a ContextVar with a module-level default, and a
    test that sets one at its top level leaks it into the rest of the worker —
    the `_restore_client_identity` problem, and the same fix.
    """
    token = tenancy.tenant_var.set(None)
    try:
        yield
    finally:
        tenancy.tenant_var.reset(token)


# ------------------------------------------------------------------ fixtures

@pytest.fixture
def tenanted(kernel, tmp_path, monkeypatch):
    """A hosted app with tenancy wired, two orgs and four principals.

    Returns a small context object rather than a tuple: the tests need the
    app, the service, both identity documents and a client per person, and
    six positional values would be unreadable.

    Roles: `nikita` is an org admin of acme, `anya` an editor, `vee` a viewer,
    `bob` an editor in globex and a member of nothing else.
    """
    from fastapi.testclient import TestClient

    from agentcad.core.appmode import AppMode
    from agentcad.core.authstore import AuthStore
    from agentcad.core.tools import build_registry
    from agentcad.server import security as security_module
    from agentcad.server.app import create_app
    from agentcad.server.security import SecurityConfig

    monkeypatch.setenv("AGENTCAD_CONFIG", str(tmp_path / "cfg" / "config.json"))
    service = make_test_service(tmp_path / "projects", kernel)

    store = AuthStore(tmp_path / "auth")
    for handle, role in (("nikita", "admin"), ("anya", "member"),
                         ("vee", "member"), ("bob", "member")):
        store.enrol(store.add_user(handle, role=role), f"pw for {handle} 1234")

    orgs = TenancyStore(tmp_path / "auth")
    orgs.create_org(ACME, "Acme Robotics")
    orgs.create_workspace(ACME, WS, "Mechanical")
    orgs.add_member(ACME, "nikita", "admin")
    orgs.add_member(ACME, "anya", "edit")
    orgs.add_member(ACME, "vee", "view")
    orgs.create_org(GLOBEX, "Globex")
    orgs.create_workspace(GLOBEX, WS)
    orgs.add_member(GLOBEX, "bob", "edit")

    cfg = SecurityConfig(mode=AppMode("hosted", HOSTED_ORIGIN, b"k" * 32),
                         store=store)
    security_module.install(cfg)
    registry = build_registry(service)
    tenancy_wiring.install(service, registry)
    app = create_app(service, registry, extra_allowed_hosts={"testserver"},
                     security=cfg)

    class Context:
        pass

    ctx = Context()
    ctx.app, ctx.service, ctx.store, ctx.orgs, ctx.cfg = (
        app, service, store, orgs, cfg)
    ctx.registry, ctx.projects = registry, tmp_path / "projects"

    def as_(handle):
        client = TestClient(app, base_url=HOSTED_ORIGIN)
        client.agentcad_store = store
        client.agentcad_service = service
        return login(client, handle)

    ctx.as_ = as_
    try:
        yield ctx
    finally:
        security_module.install(None)
        tenancy_wiring.uninstall(service, registry)


def tool(client, name, **args):
    return client.post(f"/api/tools/{name}", json=args).json()


def make_project(client, name):
    """Create *name* through the API and answer the response."""
    return client.post("/api/projects", json={"name": name})


# ------------------------------------------------------- storage rooting (FR5)

def test_projects_land_under_the_tenant_root(tenanted):
    anya, bob = tenanted.as_("anya"), tenanted.as_("bob")
    assert make_project(anya, "widget").status_code == 201
    assert make_project(bob, "widget").status_code == 201

    root = tenanted.projects
    assert (root / "orgs" / ACME / WS / "widget" / "project.json").is_file()
    assert (root / "orgs" / GLOBEX / WS / "widget" / "project.json").is_file()
    # The flat root stays empty: nothing tenanted may land beside it.
    assert sorted(p.name for p in root.iterdir()) == ["orgs"]


def test_tenant_root_is_made_on_the_first_write_and_not_on_a_read(tenanted):
    anya = tenanted.as_("anya")
    assert anya.get("/api/projects").json() == {"projects": []}
    assert not (tenanted.projects / "orgs").exists(), \
        "a read must not materialise a tenant root"
    make_project(anya, "widget")
    assert (tenanted.projects / "orgs" / ACME / WS).is_dir()


def test_list_projects_shows_only_this_tenants(tenanted):
    anya, bob = tenanted.as_("anya"), tenanted.as_("bob")
    make_project(anya, "widget")
    make_project(anya, "bracket")
    make_project(bob, "widget")
    assert [p["name"] for p in anya.get("/api/projects").json()["projects"]] \
        == ["bracket", "widget"]
    assert [p["name"] for p in bob.get("/api/projects").json()["projects"]] \
        == ["widget"]


def test_same_name_two_orgs_are_two_projects(tenanted):
    anya, bob = tenanted.as_("anya"), tenanted.as_("bob")
    make_project(anya, "widget")
    make_project(bob, "widget")
    anya.put("/api/projects/widget/assembly",
             json={"instances": [{"id": "a", "part": "p"}]})
    # Globex's widget is untouched by Acme's write.
    assert bob.get("/api/projects/widget").json()["assembly"]["instances"] == []


def test_a_project_of_another_org_is_simply_not_found(tenanted):
    anya, bob = tenanted.as_("anya"), tenanted.as_("bob")
    make_project(anya, "secret_prototype")
    answer = bob.get("/api/projects/secret_prototype")
    assert answer.status_code == 404
    assert tool(bob, "get_project", project="secret_prototype")["error"][
        "type"] == "notfound_error"


# ------------------------------------------------------- tenant resolution

def test_header_selects_the_workspace(tenanted):
    nikita = tenanted.as_("nikita")
    made = nikita.post("/api/projects", json={"name": "widget"},
                       headers={"X-Agentcad-Workspace": f"{ACME}/{WS}"})
    assert made.status_code == 201
    assert (tenanted.projects / "orgs" / ACME / WS / "widget").is_dir()


@pytest.mark.parametrize("raw", ["acme", "acme/main/extra", "ACME/main",
                                 "acme/", "/main", "acme/../etc"])
def test_a_malformed_workspace_header_is_a_400(tenanted, raw):
    answer = tenanted.as_("anya").get(
        "/api/projects", headers={"X-Agentcad-Workspace": raw})
    assert answer.status_code == 400
    assert answer.json()["error"]["type"] == "ValidationError"


def test_a_workspace_the_principal_cannot_see_is_a_name_free_404(tenanted):
    answer = tenanted.as_("anya").get(
        "/api/projects", headers={"X-Agentcad-Workspace": f"{GLOBEX}/{WS}"})
    assert answer.status_code == 404
    body = answer.json()
    assert body["error"]["message"] == "no such workspace"
    # No existence oracle: the refusal names neither the org nor the workspace,
    # and reads identically for one that does not exist at all.
    assert GLOBEX not in json.dumps(body)
    missing = tenanted.as_("anya").get(
        "/api/projects", headers={"X-Agentcad-Workspace": "nowhere/main"})
    assert missing.status_code == 404
    assert missing.json() == body


def test_a_sole_membership_needs_no_header(tenanted):
    """bob belongs to one org with one workspace, so his requests are placed."""
    assert make_project(tenanted.as_("bob"), "widget").status_code == 201
    assert (tenanted.projects / "orgs" / GLOBEX / WS / "widget").is_dir()


def test_a_token_scope_selects_the_tenant_and_the_header_cannot_move_it(
        tenanted, monkeypatch):
    """The scope slice 5 adds is read defensively — and it wins.

    `AuthStore.resolve_token` does not carry a scope yet (slice 5 adds the
    field), so the row is faked here. That is the point of the test: the guard
    must read the field the moment it appears, and must not be redirected by a
    header once it has.
    """
    from fastapi.testclient import TestClient

    monkeypatch.setattr(
        tenanted.store, "resolve_token",
        lambda presented: (None if presented != "tok" else
                           {"name": "ci", "role": "member",
                            "scope": {"org": GLOBEX, "workspace": WS}}))
    # Two projects of the same name, one per org, and a grant on globex's.
    make_project(tenanted.as_("anya"), "widget")
    make_project(tenanted.as_("bob"), "widget")
    make_project(tenanted.as_("bob"), "other")
    tenanted.orgs.grant_role(GLOBEX, WS, "widget", "agent:ci", "edit")

    client = TestClient(tenanted.app, base_url=HOSTED_ORIGIN)
    headers = {"Authorization": "Bearer tok"}
    # The scope places the request in globex; the grant authorizes `widget`.
    assert client.get("/api/projects/widget", headers=headers).status_code == 200
    assert client.put("/api/projects/widget/assembly", json={"instances": []},
                      headers=headers).status_code == 200
    # An agent has NO org default, so the sibling project is a 403 naming the
    # rung — not a 404, because the workspace itself is addressable.
    denied = client.get("/api/projects/other", headers=headers)
    assert denied.status_code == 403
    assert denied.json()["error"]["details"]["principal_role"] is None
    # A header naming acme cannot move a globex-scoped token: acme's widget
    # has an instance and globex's has none, so the answer names the org.
    tenanted.as_("anya").put(
        "/api/projects/widget/assembly",
        json={"instances": [{"id": "a", "part": "p"}]})
    moved = client.get("/api/projects/widget",
                       headers={**headers,
                                "X-Agentcad-Workspace": f"{ACME}/{WS}"})
    assert moved.status_code == 200
    assert moved.json()["assembly"]["instances"] == []


def test_an_unscoped_token_resolves_no_tenant(tenanted, monkeypatch):
    """Conservative on purpose: a token that inherited a tenant would widen
    with every membership change (`tenancy.handle_of`)."""
    from fastapi.testclient import TestClient

    monkeypatch.setattr(
        tenanted.store, "resolve_token",
        lambda presented: ({"name": "ci", "role": "member"}
                           if presented == "tok" else None))
    client = TestClient(tenanted.app, base_url=HOSTED_ORIGIN)
    made = client.post("/api/projects", json={"name": "widget"},
                       headers={"Authorization": "Bearer tok"})
    assert made.status_code == 201
    assert (tenanted.projects / "widget").is_dir(), \
        "no tenant means the untenanted root, exactly as PRD-005a behaved"


def test_no_orgs_at_all_is_a_prd_005a_instance(hosted, tmp_path):
    """A hosted instance with an empty orgs document resolves no tenant."""
    client, _store = hosted
    tenancy_wiring.install(client.agentcad_service)
    try:
        login(client)
        assert client.post("/api/projects",
                           json={"name": "widget"}).status_code == 201
        service = client.agentcad_service
        assert (service.store.root / "widget" / "project.json").is_file()
    finally:
        tenancy_wiring.uninstall(client.agentcad_service)


# ---------------------------------------------------------- the ladder (AC2)

def test_view_reads_and_cannot_write(tenanted):
    anya, vee = tenanted.as_("anya"), tenanted.as_("vee")
    make_project(anya, "widget")

    assert vee.get("/api/projects/widget").status_code == 200
    refused = vee.put("/api/projects/widget/assembly", json={"instances": []})
    assert refused.status_code == 403
    error = refused.json()["error"]
    assert error["type"] == "PermissionError"
    assert error["details"]["required"] == "edit"
    assert error["details"]["principal_role"] == "view"


def test_a_grant_takes_effect_on_the_next_request(tenanted):
    """AC2: view -> edit -> revoked, with no restart and no new app."""
    anya, vee = tenanted.as_("anya"), tenanted.as_("vee")
    make_project(anya, "widget")
    body = {"instances": []}

    assert vee.put("/api/projects/widget/assembly", json=body).status_code == 403

    # A SECOND store object, the way `docker compose exec agentcad admin`
    # would be a second process: the guard's own store re-stats and sees it.
    admin = TenancyStore(tenanted.store.root)
    admin.grant_role(ACME, WS, "widget", "user:vee", "edit")
    assert vee.put("/api/projects/widget/assembly", json=body).status_code == 200

    admin.revoke_role(ACME, WS, "widget", "user:vee")
    assert vee.put("/api/projects/widget/assembly", json=body).status_code == 403


def test_org_admin_outranks_a_project_override(tenanted):
    anya = tenanted.as_("anya")
    make_project(anya, "widget")
    TenancyStore(tenanted.store.root).grant_role(
        ACME, WS, "widget", "user:nikita", "view")
    nikita = tenanted.as_("nikita")
    assert nikita.put("/api/projects/widget/assembly",
                      json={"instances": []}).status_code == 200


def test_a_viewer_cannot_create_a_project(tenanted):
    """`ProjectStore.create` is the write the write guard cannot see."""
    refused = make_project(tenanted.as_("vee"), "widget")
    assert refused.status_code == 403
    assert refused.json()["error"]["details"]["required"] == "edit"
    assert not (tenanted.projects / "orgs" / ACME / WS / "widget").exists()


def test_a_principal_with_no_role_on_a_visible_project_gets_403_not_404(
        tenanted, monkeypatch):
    """The boundary, stated: a workspace you cannot see is a name-free 404; a
    project you can address but hold nothing on is a 403 that names the rung."""
    from fastapi.testclient import TestClient

    monkeypatch.setattr(
        tenanted.store, "resolve_token",
        lambda presented: ({"name": "ci", "role": "member",
                            "scope": {"org": ACME, "workspace": WS}}
                           if presented == "tok" else None))
    make_project(tenanted.as_("anya"), "widget")
    client = TestClient(tenanted.app, base_url=HOSTED_ORIGIN)
    answer = client.get("/api/projects/widget",
                        headers={"Authorization": "Bearer tok"})
    assert answer.status_code == 403
    assert answer.json()["error"]["details"] == {
        "required": "view", "project": "widget", "principal_role": None}


# ------------------------------------------------------------- the tool floor

def test_the_tool_registry_enforces_the_floor(tenanted):
    anya, vee = tenanted.as_("anya"), tenanted.as_("vee")
    make_project(anya, "widget")

    assert tool(vee, "get_assembly", project="widget")["instances"] == []
    refused = tool(vee, "set_assembly", project="widget", instances=[])
    assert refused["error"]["type"] == "permission_error"
    assert refused["error"]["details"]["required"] == "edit"


def test_a_tool_cannot_reach_another_orgs_project(tenanted):
    anya, bob = tenanted.as_("anya"), tenanted.as_("bob")
    make_project(anya, "widget")
    make_project(bob, "widget")
    tool(bob, "set_assembly", project="widget",
         instances=[{"id": "b", "part": "p"}])
    assert tool(anya, "get_assembly", project="widget")["instances"] == []


def test_open_project_is_refused_while_a_tenant_is_set(tenanted, tmp_path):
    refused = tool(tenanted.as_("nikita"), "open_project", path=str(tmp_path))
    assert refused["error"]["type"] == "permission_error"
    assert "workspace" in refused["error"]["message"]


def test_every_registered_tool_is_classified(tenanted):
    """The read-only set is curated, so it must not rot in either direction."""
    names = {t.name for t in tenanted.registry.list()}
    stale = (tenancy_wiring.READ_ONLY_TOOLS | tenancy_wiring.COMMENT_TOOLS
             | tenancy_wiring.TENANT_FORBIDDEN) - names
    assert stale == set(), f"classified tools that no longer exist: {stale}"
    assert not (tenancy_wiring.READ_ONLY_TOOLS
                & tenancy_wiring.COMMENT_TOOLS)
    for name in names:
        assert tenancy_wiring.floor_of(name) in authz.ROLE_ORDER


def test_a_new_reading_tool_defaults_closed_and_is_noticed(tenanted):
    """Everything unclassified is `edit`, which is the safe default — and the
    shape a *read* is most likely to be missed in is `get_`/`list_`/`find_`,
    so those are enumerated rather than trusted."""
    reads = {t.name for t in tenanted.registry.list()
             if t.name.split("_")[0] in {"get", "list", "find", "search"}}
    # `get_turn` reads a lock, `search_parts` reads an index: all of them are
    # reads, and none of them may quietly become `edit` by being forgotten.
    assert {n for n in reads if tenancy_wiring.floor_of(n) != "view"} == set()
    assert tenancy_wiring.floor_of("a_tool_nobody_has_written_yet") == "edit"


# --------------------------------------------------------- qualified keys

def test_turn_locks_do_not_collide_across_orgs(tenanted):
    anya, bob = tenanted.as_("anya"), tenanted.as_("bob")
    make_project(anya, "widget")
    make_project(bob, "widget")

    held = tool(anya, "acquire_turn", project="widget")
    assert held["holder"].startswith("user:anya")
    # Globex's widget is a different lock: bob takes his own turn happily.
    assert tool(bob, "acquire_turn", project="widget")["holder"].startswith(
        "user:bob")
    keys = set(tenanted.service.turnlock._held)
    assert keys == {f"{ACME}/{WS}/widget", f"{GLOBEX}/{WS}/widget"}


def test_lock_key_is_qualified_only_under_a_tenant(tenanted):
    store = tenanted.service.store
    make_project(tenanted.as_("anya"), "widget")
    with tenancy.tenant_scope((ACME, WS)):
        assert store.lock_key("widget") == f"{ACME}/{WS}/widget"
    # Untenanted, the key is the bare project name — byte-for-byte today's.
    # (`lock_key` resolves the working tree, so the project has to exist in
    # the root it is asked about.)
    store.create("legacy")
    assert store.lock_key("legacy") == "legacy"


def test_the_turn_is_still_held_against_a_second_client_in_the_same_tenant(
        tenanted):
    """Qualification must not weaken the lock it re-keys."""
    anya, vee = tenanted.as_("anya"), tenanted.as_("vee")
    make_project(anya, "widget")
    TenancyStore(tenanted.store.root).grant_role(
        ACME, WS, "widget", "user:vee", "edit")
    assert tool(anya, "acquire_turn", project="widget")["holder"]
    refused = vee.put("/api/projects/widget/assembly", json={"instances": []})
    assert refused.status_code == 409


# ----------------------------------------------------------------- events

def test_events_are_stamped_and_filtered_by_tenant(tenanted):
    bus = tenanted.service.bus
    with tenancy.tenant_scope((ACME, WS)):
        acme_queue = bus.subscribe()
    with tenancy.tenant_scope((GLOBEX, WS)):
        globex_queue = bus.subscribe()
    plain_queue = bus.subscribe()

    with tenancy.tenant_scope((ACME, WS)):
        bus.publish({"type": "project_changed", "project": "widget"})
    bus.publish({"type": "ui_open", "view": "parts"})

    assert [e["type"] for e in _drain(acme_queue)] == ["project_changed",
                                                       "ui_open"]
    assert [e["type"] for e in _drain(globex_queue)] == ["ui_open"]
    # An untenanted subscriber on a hosted instance hears no tenant's events.
    assert [e["type"] for e in _drain(plain_queue)] == ["ui_open"]


def test_the_stamp_is_the_wire_spelling(tenanted):
    bus = tenanted.service.bus
    q = bus.subscribe()
    with tenancy.tenant_scope((ACME, WS)):
        bus.publish({"type": "project_changed", "project": "widget"})
    assert _drain(q) == []
    with tenancy.tenant_scope((ACME, WS)):
        seen = bus.subscribe()
        # The acting principal rides alongside the tenant (PRD-005:
        # "project_changed gains the acting principal") — the wire spelling of
        # `locks.current_client_id()`, stamped in the same publish wrapper.
        token = locks.client_id_var.set("user:anya/browser:1")
        try:
            bus.publish({"type": "project_changed", "project": "widget"})
        finally:
            locks.client_id_var.reset(token)
    assert _drain(seen) == [{"type": "project_changed", "project": "widget",
                             "tenant": f"{ACME}/{WS}",
                             "client": "user:anya/browser:1"}]


def test_a_websocket_hears_its_own_org_only(tenanted):
    anya, bob = tenanted.as_("anya"), tenanted.as_("bob")
    make_project(anya, "widget")
    make_project(bob, "widget")
    with anya.websocket_connect("/ws") as socket:
        # Globex first: if the filter leaked, this is the event that arrives.
        bob.put("/api/projects/widget/assembly",
                json={"instances": [{"id": "b", "part": "p"}]})
        anya.put("/api/projects/widget/assembly", json={"instances": []})
        event = socket.receive_json()
        assert event["tenant"] == f"{ACME}/{WS}"
        assert event["type"] == "project_changed"


def test_the_websocket_sentinel_is_never_filtered(tenanted):
    """`app.py` wakes a disconnected client by putting a non-dict sentinel on
    its queue; a filter that dropped it would hang the socket's shutdown."""
    from agentcad.server.app import _WS_STOP, _wake_websocket_event_waiter

    with tenancy.tenant_scope((ACME, WS)):
        q = tenanted.service.bus.subscribe()
    _wake_websocket_event_waiter(q)
    assert q.get_nowait() is _WS_STOP


def _drain(q: queue.Queue) -> list:
    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except queue.Empty:
            return out


# ------------------------------------------------------- the route matcher

def test_project_routes_are_matched_and_literals_are_not(tenanted):
    from agentcad.server import security

    app = tenanted.app
    assert security.project_of(app, "/api/projects/widget") == "widget"
    assert security.project_of(
        app, "/api/projects/widget/parts/plate/mesh") == "widget"
    assert security.project_of(app, "/api/projects/widget/comments/7/resolve") \
        == "widget"
    # `POST /api/projects/open` is a literal route, not a project called
    # `open`; the literal set is consulted first for exactly this reason.
    assert security.project_of(app, "/api/projects/open") is None
    assert security.project_of(app, "/api/projects") is None
    assert security.project_of(app, "/api/tools/get_part") is None
    assert security.project_of(app, "/api/health") is None
    # The git routes carry their own org/ws and their own floor.
    assert security.project_of(
        app, f"/git/{ACME}/{WS}/widget.git/info/refs") is None


def test_the_matcher_covers_every_pack_not_just_app_py(tenanted):
    """FastAPI does not flatten `include_router`: a naive `app.routes` walk
    sees 23 routes of 83, and the floor would silently miss every pack."""
    from agentcad.server import security

    # Routes that exist only inside mounted packs.
    for path in ("/api/projects/widget/presence",
                 "/api/projects/widget/releases/r1",
                 "/api/projects/widget/skills/basics/trust"):
        assert security.project_of(tenanted.app, path) == "widget", path


# ------------------------------------------------------------- sync seams

def test_the_sync_seams_are_wired(tenanted):
    from agentcad.server import routes_sync

    assert routes_sync.require_role is not None
    assert routes_sync.resolve_project is not None

    make_project(tenanted.as_("anya"), "widget")
    expected = tenanted.projects / "orgs" / ACME / WS / "widget"
    assert routes_sync.resolve_project(ACME, WS, "widget") == expected
    with pytest.raises(Exception):
        routes_sync.resolve_project(GLOBEX, WS, "widget")


def test_the_sync_floor_asks_the_roles_document(tenanted):
    from agentcad.core.model import AuthError
    from agentcad.server import routes_sync, security

    make_project(tenanted.as_("anya"), "widget")
    with pytest.raises(AuthError):
        routes_sync.require_role("view", ACME, WS, "widget")   # anonymous

    principal = security.Principal(kind="user", name="vee", role="member")
    token = security._principal_var.set(principal)
    try:
        routes_sync.require_role("view", ACME, WS, "widget")   # a viewer clones
        with pytest.raises(authz.PermissionError):
            routes_sync.require_role("edit", ACME, WS, "widget")
        with pytest.raises(authz.PermissionError):
            routes_sync.require_role("view", GLOBEX, WS, "widget")
    finally:
        security._principal_var.reset(token)


def test_uninstall_puts_the_module_level_seams_back(tenanted):
    from agentcad.core import tools_versioning
    from agentcad.server import routes_sync

    tenancy_wiring.uninstall(tenanted.service, tenanted.registry)
    assert routes_sync.require_role is None
    assert routes_sync.resolve_project is None
    assert not hasattr(tools_versioning.install_write_guard,
                       "_agentcad_inner")
    store = tenanted.service.store
    assert store.root_resolver is None
    assert not hasattr(store.lock_key, "_agentcad_inner")
    assert not hasattr(store.create, "_agentcad_inner")
    store.create("legacy")
    assert store.lock_key("legacy") == "legacy"
    assert (tenanted.projects / "legacy").is_dir(), "back to the flat root"


def test_a_registry_rebuild_keeps_the_write_floor(tenanted):
    """`tools_versioning.install_write_guard` REPLACES the guard (PRD-008's
    lesson); the wiring re-installs from the same seam."""
    from agentcad.core import tools_versioning

    anya, vee = tenanted.as_("anya"), tenanted.as_("vee")
    make_project(anya, "widget")
    tools_versioning.install_write_guard(tenanted.service)
    assert vee.put("/api/projects/widget/assembly",
                   json={"instances": []}).status_code == 403


# --------------------------------------------------------------- local mode

def test_local_mode_wrappers_are_identity(kernel, tmp_path):
    """Every wrapper installed, no tenant anywhere: nothing changes.

    The narrow half of AC7. The broad half is the rest of the suite, which
    runs unchanged.
    """
    from agentcad.core.tools import build_registry

    service = make_test_service(tmp_path / "projects", kernel)
    registry = build_registry(service)
    tenancy_wiring.install(service, registry, config=lambda: None)
    try:
        service.create_project("widget")
        assert (tmp_path / "projects" / "widget" / "project.json").is_file()
        assert service.store.lock_key("widget") == "widget"
        assert service.store._root() == service.store.root

        q = service.bus.subscribe()
        service.bus.publish({"type": "project_changed", "project": "widget"})
        assert _drain(q) == [{"type": "project_changed", "project": "widget"}]

        assert registry.call("get_assembly",
                             {"project": "widget"})["instances"] == []
        assert "error" not in registry.call(
            "set_assembly", {"project": "widget", "instances": []})
        locks.set_client_id("local")
        if service.store.write_guard is not None:
            service.store.write_guard("widget")      # no tenant: no refusal
    finally:
        tenancy_wiring.uninstall(service, registry)


def test_the_store_seam_is_absent_by_default():
    """`root_resolver` is `None` on a store nobody wired — the property that
    makes local mode a fact about the diff rather than a test."""
    from agentcad.core.project import ProjectStore
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        store = ProjectStore(directory)
        assert store.root_resolver is None
        assert store._root() == store.root


def test_a_broken_resolver_falls_back_to_the_root(tmp_path):
    from agentcad.core.project import ProjectStore

    store = ProjectStore(tmp_path / "projects")

    def angry():
        raise RuntimeError("no")

    store.root_resolver = angry
    assert store._root() == store.root
    store.root_resolver = lambda: None
    assert store._root() == store.root


def test_a_refusal_never_takes_a_claim(tenanted):
    """PRD-008's claim guard calls its previous guard first, so whichever of
    the two lands outermost, a viewer is refused before a claim is taken."""
    from tests.conftest import BOX_SCRIPT

    anya, vee = tenanted.as_("anya"), tenanted.as_("vee")
    make_project(anya, "widget")
    assert anya.post("/api/projects/widget/parts",
                     json={"id": "plate", "script": BOX_SCRIPT}).status_code \
        == 201

    refused = vee.patch("/api/projects/widget/parts/plate/params",
                        json={"size": 12})
    assert refused.status_code == 403
    assert refused.json()["error"]["details"]["required"] == "edit"
    roster = vee.get("/api/projects/widget/presence").json()
    assert not (roster.get("claims") or {}), "a refused write took a claim"


def test_the_claim_guard_is_not_installed_twice(tenanted):
    """`ensure_claim_guard` reads its marker off whatever `write_guard` is;
    the authz wrapper carries it, or every claim check would run twice."""
    from agentcad.core.presence import ensure_claim_guard

    guard = tenanted.service.store.write_guard
    ensure_claim_guard(tenanted.service)
    assert tenanted.service.store.write_guard is guard


# ------------------------------------------------ the wire envelope (FR6)

_ROUTE_PACKS = (
    "routes_comments", "routes_configs", "routes_specs", "routes_checks",
    "routes_packages", "routes_proposals", "routes_versioning",
)


def _http_status(exc) -> int:
    """The status ``app.py`` would give *exc*, by its own isinstance walk."""
    from agentcad.server.app import _ERROR_STATUS

    return next((code for cls, code in _ERROR_STATUS.items()
                 if isinstance(exc, cls)), 400)


@pytest.mark.parametrize("pack", _ROUTE_PACKS)
def test_a_route_packs_result_maps_a_tenancy_refusal_to_its_own_status(pack):
    """FR6, per pack family: a route pack calls ``registry.call`` and hands the
    payload to its ``_result``. With the floor wrapper installed a viewer gets a
    ``permission_error``/``auth_error`` envelope, and ``_result`` must raise the
    class ``app.py`` maps to 403/401 — not the 422 an unmapped type fell
    through to.
    """
    from agentcad.core.model import AuthError

    module = importlib.import_module(f"agentcad.server.{pack}")
    details = {"required": "edit", "project": "widget", "principal_role": "view"}

    with pytest.raises(authz.PermissionError) as perm:
        module._result({"error": {"type": "permission_error",
                                  "message": "needs edit", "details": details}})
    assert perm.value.details == details          # {required, project, role}
    assert _http_status(perm.value) == 403

    with pytest.raises(AuthError) as auth:
        module._result({"error": {"type": "auth_error",
                                  "message": "no credential", "details": {}}})
    assert _http_status(auth.value) == 401


def test_a_viewer_post_to_a_route_pack_is_a_403_permission_error(tenanted):
    """The same, end to end over HTTP: `add_comment` is comment-floored and
    `vee` holds only `view`, so the comments route answers 403 with the rung
    named — the real path the `_result` map above is unit-tested against."""
    anya, vee = tenanted.as_("anya"), tenanted.as_("vee")
    make_project(anya, "widget")
    resp = vee.post("/api/projects/widget/comments",
                    json={"body": "no", "anchor": {"kind": "part", "part": "x"}})
    assert resp.status_code == 403
    error = resp.json()["error"]
    assert error["type"] == "PermissionError"
    assert error["details"]["required"] == "comment"
    assert error["details"]["project"] == "widget"


# ---------------------------------------------- the audit tap's floor (F7)

def test_the_audit_tap_records_by_the_floor_not_a_name_heuristic(tenanted):
    """A view-floored tool whose NAME is not a read prefix (`run_specs`) writes
    NO row; an edit-floored tool writes exactly one. The old prefix heuristic
    (`audit.is_mutating_tool`) logged `run_specs`/`branch_list`/`run_checks`/
    `export_bom` as actions — the tap now asks the floor table instead."""
    anya = tenanted.as_("anya")
    make_project(anya, "widget")
    log = audit.for_auth_store(tenanted.store)

    with tenancy.tenant_scope((ACME, WS)):
        token = locks.client_id_var.set("user:anya")     # an org editor
        try:
            tenanted.registry.call("run_specs", {"project": "widget"})
            tenanted.registry.call(
                "set_assembly", {"project": "widget", "instances": []})
        finally:
            locks.client_id_var.reset(token)

    # The floor table and `_is_mutating` agree: `run_specs` is a read.
    assert tenancy_wiring.floor_of("run_specs") == "view"
    assert tenancy_wiring._is_mutating("run_specs") is False
    assert log.query(ACME, action="run_specs") == []
    assert len(log.query(ACME, action="set_assembly")) == 1


# --------------------------------------- the package tools' floor (A#6)

@pytest.mark.parametrize("name", ("validate_package", "package_from_step"))
def test_a_viewer_cannot_reach_a_package_filesystem_primitive(tenanted, name):
    """`validate_package`/`package_from_step` take a caller-supplied host path
    and write/scaffold at `dest` — a filesystem primitive, so `edit`, never the
    viewer rung they used to sit at."""
    make_project(tenanted.as_("anya"), "widget")
    assert tenancy_wiring.floor_of(name) == "edit"
    refused = tool(tenanted.as_("vee"), name, project="widget")
    assert refused["error"]["type"] == "permission_error", (name, refused)
    assert refused["error"]["details"]["required"] == "edit"
