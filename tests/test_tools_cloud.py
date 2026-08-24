"""PRD-005 slice 5: scoped agent tokens as tools, and the audit surface (FR3/FR12).

Three things are proved here and they are not the same thing:

* **the record** — a token scope is additive, and an unscoped token is
  byte-for-byte 005a's (`resolve_token` still answers two keys, `list_tokens`
  still answers six);
* **the reach** — AC5: a token scoped to project A works on A, is refused on
  B, and stops working on its very next request when revoked;
* **the surface** — the tools exist only in hosted mode, mint only for a
  person, and every mutation of theirs leaves an audit row.
"""

from __future__ import annotations

import pytest

from agentcad.core import audit as audit_mod
from agentcad.core.authstore import (
    check_token_scope, scope_allows,
)
from agentcad.core.model import ValidationError
from agentcad.core.tenancy import TenancyStore

from .conftest import ADMIN_PASSWORD, make_test_service

GOOD = {"handle": "nikita", "password": ADMIN_PASSWORD}


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def org(hosted):
    """The `hosted` app plus a tenancy: org `acme`, workspace `main`, two
    projects. `nikita` (005a's enrolled admin) is the org admin."""
    client, store = hosted
    tenants = TenancyStore(store.root)
    tenants.create_org("acme", label="Acme Robotics", admin="nikita")
    tenants.create_workspace("acme", "main")
    tenants.add_project("acme", "main", "widget")
    tenants.add_project("acme", "main", "bracket")
    client.post("/api/auth/login", json=GOOD)
    return client, store, tenants


def _tool(client, tool, /, headers=None, **args):
    """POST one tool call. `client` and `tool` are positional-ONLY so that a
    tool argument called `name` (which `create_agent_token` has) lands in
    `**args` instead of colliding with the helper's own parameter."""
    return client.post(f"/api/tools/{tool}", json=args,
                       headers=headers or {}).json()


def _log(store):
    return audit_mod.for_auth_store(store)


# ------------------------------------------------------ the record (authstore)


def test_an_unscoped_token_is_byte_for_byte_what_005a_wrote(hosted):
    """The whole promise of an additive schema, asserted by key set: a legacy
    token's row, its `resolve_token` answer and its `list_tokens` row are
    unchanged, so nothing that consumed them has to learn about scopes."""
    _client, store = hosted
    bearer = store.add_token("legacy", role="member")
    assert store.resolve_token(bearer) == {"name": "legacy", "role": "member"}
    assert set(store.list_tokens()[0]) == {
        "id", "name", "role", "created", "expires", "revoked"}
    assert store.scope_for_principal("legacy") is None
    assert scope_allows(None, "acme", "main", "anything") is True


def test_a_scoped_token_carries_its_scope_through_resolution(hosted):
    _client, store = hosted
    scope = {"org": "acme", "workspace": "main",
             "projects": ["widget"], "role": "edit"}
    bearer = store.add_token("ci", scope=scope)
    resolved = store.resolve_token(bearer)
    assert resolved["name"] == "ci"
    assert resolved["scope"] == {"org": "acme", "workspace": "main",
                                 "projects": ["main/widget"], "role": "edit"}
    assert store.scope_for_principal("ci") == resolved["scope"]
    assert "scope" in store.list_tokens()[0]


def test_scope_normalizes_to_qualified_projects_sorted_and_deduped():
    scope = check_token_scope({"org": "acme", "workspace": "main",
                               "projects": ["b", "main/a", "b"],
                               "role": "view"})
    assert scope == {"org": "acme", "workspace": "main",
                     "projects": ["main/a", "main/b"], "role": "view"}


def test_an_unqualified_project_needs_a_workspace():
    with pytest.raises(ValidationError):
        check_token_scope({"org": "acme", "projects": ["widget"],
                           "role": "view"})
    assert check_token_scope({"org": "acme", "projects": ["main/widget"],
                              "role": "view"})["projects"] == ["main/widget"]


@pytest.mark.parametrize("scope", [
    {"org": "acme", "projects": [], "role": "view", "workspace": "main"},
    {"org": "acme", "projects": ["widget"], "role": "owner",
     "workspace": "main"},
    {"org": "Acme", "projects": ["main/widget"], "role": "view"},
    {"org": "acme", "projects": ["main/../etc"], "role": "view"},
    {"org": "acme", "projects": ["main/widget"], "role": "view", "extra": 1},
    "not an object",
])
def test_a_malformed_scope_is_refused(scope):
    with pytest.raises(ValidationError):
        check_token_scope(scope)


def test_an_empty_project_list_is_not_the_whole_org():
    """A credential whose reach is decided by an omission widens every time a
    project is added; the refusal message says so."""
    with pytest.raises(ValidationError) as excinfo:
        check_token_scope({"org": "acme", "projects": [], "role": "admin"})
    assert "whole org" in excinfo.value.message


def test_scope_allows_answers_the_three_questions():
    scope = check_token_scope({"org": "acme", "workspace": "main",
                               "projects": ["widget"], "role": "edit"})
    assert scope_allows(scope, "acme", "main", "widget")
    assert not scope_allows(scope, "acme", "main", "bracket")
    assert not scope_allows(scope, "initech", "main", "widget")
    assert scope_allows(scope, "acme")                  # org-level question
    assert scope_allows(scope, "acme", None, "widget")  # workspace unresolved
    assert not scope_allows({"org": "acme"}, "acme")    # malformed: denies


def test_a_malformed_stored_scope_reads_as_unscoped(hosted, tmp_path):
    """`_scope_row`'s tolerance: a hand-edited or half-written scope must not
    make `resolve_token` raise on the request path."""
    _client, store = hosted
    bearer = store.add_token("ci")
    doc = store._read("tokens.json", fresh=True)         # noqa: SLF001
    token_id = next(iter(doc))
    store._write("tokens.json",                          # noqa: SLF001
                 {token_id: {**doc[token_id], "scope": "junk"}})
    assert store.resolve_token(bearer) == {"name": "ci", "role": "member"}


def test_two_live_scoped_tokens_of_one_name_resolve_to_no_scope(hosted):
    """They are two scopes for the one principal `agent:ci`; answering with
    either would be a guess."""
    _client, store = hosted
    scope = {"org": "acme", "workspace": "main", "projects": ["widget"],
             "role": "edit"}
    store.add_token("ci", scope=scope)
    store.add_token("ci", scope={**scope, "projects": ["bracket"]})
    assert store.scope_for_principal("ci") is None
    assert len(store.live_tokens_named("ci")) == 2


def test_revoking_a_token_takes_it_out_of_the_live_set(hosted):
    _client, store = hosted
    store.add_token("ci", scope={"org": "acme", "workspace": "main",
                                 "projects": ["widget"], "role": "edit"})
    store.revoke_token(store.list_tokens()[0]["id"])
    assert store.live_tokens_named("ci") == []
    assert store.scope_for_principal("ci") is None


def test_the_scope_role_vocabulary_matches_tenancys(hosted):
    from agentcad.core import authstore, tenancy

    assert authstore.SCOPE_ROLES == tenancy.ROLES


# --------------------------------------------------------- registration


def test_the_cloud_tools_are_not_registered_in_local_mode(kernel, tmp_path):
    """The FEM precedent, `whoami`'s shape: a pack registers a tool only when
    it can run, and none of these can run without a hosted configuration."""
    from agentcad.core.tools import build_registry
    from agentcad.server import security as security_module

    security_module.install(None)
    service = make_test_service(tmp_path / "projects", kernel)
    names = {tool.name for tool in build_registry(service).list()}
    assert not names & {"create_agent_token", "revoke_agent_token",
                        "grant_role", "revoke_role", "list_members",
                        "sync_status"}


def test_the_cloud_tools_are_registered_in_hosted_mode(hosted):
    """The positive control: without it the absence test above passes on a
    pack that failed to register anywhere."""
    client, _store = hosted
    client.post("/api/auth/login", json=GOOD)
    names = {tool["name"] for tool in client.get("/api/tools").json()["tools"]}
    assert {"create_agent_token", "revoke_agent_token", "grant_role",
            "revoke_role", "list_members", "sync_status"} <= names


# ---------------------------------------------------------------- whoami


def test_whoami_is_unchanged_on_an_instance_with_no_orgs(hosted):
    """No tenancy document, no extra keys — 005a's four, by equality. This is
    the same "absence of a tenant is the old behaviour" rule the rest of
    PRD-005 is built on, and it is why `test_tokens.py` still passes."""
    client, _store = hosted
    client.post("/api/auth/login", json=GOOD)
    assert client.post("/api/tools/whoami", json={}).json() == {
        "principal": "user:nikita", "kind": "user", "role": "admin",
        "mode": "hosted"}


def test_whoami_reports_a_persons_org_workspace_and_roles(org):
    client, _store, tenants = org
    tenants.grant_role("acme", "main", "bracket", "user:nikita", "view")
    body = _tool(client, "whoami")
    assert body["principal"] == "user:nikita"
    assert body["kind"] == "user"
    assert body["org"] == "acme" and body["workspace"] == "main"
    assert body["orgs"] == ["acme"]
    # An org ADMIN is admin on every project regardless of the override —
    # `authz.role_of`'s step 1, rendered here rather than re-derived.
    assert body["roles"] == {"widget": "admin", "bracket": "admin"}
    assert body["scope"] is None


def test_whoami_reports_an_agents_scope_and_only_its_own_projects(org):
    client, store, _tenants = org
    minted = _tool(client, "create_agent_token", name="ci", org="acme",
                   workspace="main", projects=["widget"], role="edit")
    body = _tool(client, "whoami", headers=_bearer(minted["token"]))
    assert body["principal"] == "agent:ci"
    assert body["kind"] == "agent"
    assert body["org"] == "acme" and body["workspace"] == "main"
    assert body["orgs"] == ["acme"]      # from the scope; an agent is no member
    assert body["roles"] == {"widget": "edit"}
    assert body["scope"] == {"org": "acme", "workspace": "main",
                             "projects": ["main/widget"], "role": "edit"}
    assert store.scope_for_principal("ci") == body["scope"]


def test_whoami_keeps_005as_four_keys_when_it_extends_them(org):
    body = _tool(org[0], "whoami")
    assert {"principal", "kind", "role", "mode"} <= set(body)
    assert body["mode"] == "hosted"


# ------------------------------------------------------------ minting


def test_a_person_who_is_an_org_admin_mints_a_scoped_token(org):
    client, store, tenants = org
    body = _tool(client, "create_agent_token", name="ci", org="acme",
                 workspace="main", projects=["widget"], role="edit",
                 ttl_days=7)
    assert body["principal"] == "agent:ci"
    assert body["token"].startswith("acad_")
    assert body["scope"]["projects"] == ["main/widget"]
    assert body["granted"] == ["main/widget"]
    assert body["note"] == "this is the only time the token is shown"
    assert body["expires"] > 0
    # The scope is also a GRANT: that is what makes the token reach anything,
    # and what makes revoking it remove the reach.
    assert tenants.project_roles("acme", "main", "widget") == {"agent:ci": "edit"}
    assert store.resolve_token(body["token"])["scope"] == body["scope"]


def test_the_secret_is_returned_once_and_stored_only_as_a_digest(org):
    client, store, _tenants = org
    body = _tool(client, "create_agent_token", name="ci", org="acme",
                 workspace="main", projects=["widget"], role="edit")
    listed = [row for row in store.list_tokens() if row["id"] == body["id"]]
    assert listed and "token" not in listed[0] and "digest" not in listed[0]
    raw = (store.root / "tokens.json").read_text()
    assert body["token"].split("_", 2)[2] not in raw


def test_a_token_cannot_mint_a_token(org):
    """005a's Decision 14 survives as a DERIVED property: `authz.role_of` gives
    an agent no org default (a token is never an org member), so a bearer
    cannot reach `admin` at org level however its instance role is spelled."""
    client, store, _tenants = org
    minted = _tool(client, "create_agent_token", name="ci", org="acme",
                   workspace="main", projects=["widget"], role="admin")
    refused = _tool(client, "create_agent_token",
                    headers=_bearer(minted["token"]),
                    name="ci2", org="acme", workspace="main",
                    projects=["widget"], role="admin")
    assert refused["error"]["type"] == "permission_error"
    assert refused["error"]["details"]["required"] == "admin"
    # Even an *instance*-admin bearer (005a's role) is refused: the two role
    # systems are different questions and this one is the tenant's.
    admin_bearer = store.add_token("root", role="admin")
    refused = _tool(client, "create_agent_token", headers=_bearer(admin_bearer),
                    name="ci3", org="acme", workspace="main",
                    projects=["widget"], role="view")
    assert refused["error"]["type"] == "permission_error"


def test_a_member_who_is_not_an_org_admin_cannot_mint(org):
    client, store, tenants = org
    store.enrol(store.add_user("anya"), "correct horse battery")
    tenants.add_member("acme", "anya", "edit")
    client.cookies.clear()
    client.post("/api/auth/login", json={"handle": "anya",
                                         "password": "correct horse battery"})
    refused = _tool(client, "create_agent_token", name="ci", org="acme",
                    workspace="main", projects=["widget"], role="view")
    assert refused["error"]["type"] == "permission_error"
    assert refused["error"]["details"]["principal_role"] == "edit"


def test_a_second_live_token_of_the_same_name_is_refused(org):
    client, _store, _tenants = org
    _tool(client, "create_agent_token", name="ci", org="acme",
          workspace="main", projects=["widget"], role="edit")
    again = _tool(client, "create_agent_token", name="ci", org="acme",
                  workspace="main", projects=["bracket"], role="edit")
    assert again["error"]["type"] == "validation_error"
    assert "union" in again["error"]["message"]


def test_minting_for_an_unknown_project_writes_nothing(org):
    client, store, tenants = org
    refused = _tool(client, "create_agent_token", name="ci", org="acme",
                    workspace="main", projects=["widget", "ghost"],
                    role="edit")
    assert refused["error"]["type"] == "notfound_error"
    assert store.list_tokens() == []
    assert tenants.project_roles("acme", "main", "widget") == {}


def test_a_mint_can_leave_the_tenant_to_the_request(org):
    """`org` is how a caller *names* a tenant the request has not resolved. Once
    one is resolved (slice 4's `security.resolve_tenant` — a single-org
    instance needs no header at all), an absent argument means "here"."""
    client, _store, tenants = org
    body = _tool(client, "create_agent_token", name="ci", org="",
                 projects=["main/widget"], role="edit")
    assert body["scope"]["org"] == "acme"
    assert tenants.project_roles("acme", "main", "widget") == {"agent:ci": "edit"}


def test_minting_leaves_an_audit_row(org):
    client, store, _tenants = org
    _tool(client, "create_agent_token", name="ci", org="acme",
          workspace="main", projects=["widget"], role="edit")
    rows = _log(store).query("acme", action="create_agent_token")
    assert len(rows) == 1
    assert rows[0]["principal"] == "user:nikita"
    assert rows[0]["outcome"] == "ok"
    assert rows[0]["args_digest"]


# ------------------------------------------------------------ revocation


def test_revoking_a_scoped_token_drops_its_grants(org):
    client, store, tenants = org
    minted = _tool(client, "create_agent_token", name="ci", org="acme",
                   workspace="main", projects=["widget"], role="edit")
    body = _tool(client, "revoke_agent_token", token_id=minted["id"])
    assert body["revoked"] is True
    assert body["grants_revoked"] == ["main/widget"]
    assert tenants.project_roles("acme", "main", "widget") == {}
    assert store.resolve_token(minted["token"]) is None
    assert _log(store).query("acme", action="revoke_agent_token")


def test_revoking_one_of_two_tokens_keeps_the_others_grants(org):
    """Two live credentials speaking as one principal: the mint refuses to
    create the situation, but a store written before that rule (or by the
    instance-admin CLI) can hold it, and revoking one must not disarm the
    other."""
    client, store, tenants = org
    minted = _tool(client, "create_agent_token", name="ci", org="acme",
                   workspace="main", projects=["widget"], role="edit")
    store.add_token("ci", scope={"org": "acme", "workspace": "main",
                                 "projects": ["widget"], "role": "edit"})
    body = _tool(client, "revoke_agent_token", token_id=minted["id"])
    assert body["grants_revoked"] == []
    assert tenants.project_roles("acme", "main", "widget") == {"agent:ci": "edit"}


def test_an_unscoped_token_is_not_revocable_from_the_tenant_surface(org):
    client, store, _tenants = org
    store.add_token("legacy")
    token_id = store.list_tokens()[0]["id"]
    refused = _tool(client, "revoke_agent_token", token_id=token_id)
    assert refused["error"]["type"] == "validation_error"
    assert "instance-wide" in refused["error"]["message"]
    assert store.get_token(token_id)["revoked"] is False


def test_revoking_an_unknown_token_is_a_not_found(org):
    assert _tool(org[0], "revoke_agent_token",
                 token_id="deadbeef")["error"]["type"] == "notfound_error"


# ----------------------------------------------------------------- roles


def test_grant_and_revoke_role_write_through_the_tenancy_store(org):
    client, store, tenants = org
    granted = _tool(client, "grant_role", project="widget", principal="anya",
                    role="edit", org="acme", workspace="main")
    assert granted["principal"] == "user:anya"        # a bare handle is a person
    assert tenants.project_roles("acme", "main", "widget") == {"user:anya": "edit"}
    revoked = _tool(client, "revoke_role", project="widget", principal="anya",
                    org="acme", workspace="main")
    assert revoked["revoked"] is True
    assert "org default" in revoked["note"]
    assert tenants.project_roles("acme", "main", "widget") == {}
    actions = {row["action"] for row in _log(store).query("acme")}
    assert {"grant_role", "revoke_role"} <= actions


def test_granting_requires_admin_on_the_project(org):
    client, store, tenants = org
    store.enrol(store.add_user("anya"), "correct horse battery")
    tenants.add_member("acme", "anya", "edit")
    client.cookies.clear()
    client.post("/api/auth/login", json={"handle": "anya",
                                         "password": "correct horse battery"})
    refused = _tool(client, "grant_role", project="widget",
                    principal="anya", role="admin", org="acme",
                    workspace="main")
    assert refused["error"]["type"] == "permission_error"
    assert tenants.project_roles("acme", "main", "widget") == {}


def test_a_grant_is_recorded_against_its_project(org):
    client, store, _tenants = org
    _tool(client, "grant_role", project="widget", principal="anya",
          role="edit", org="acme", workspace="main")
    rows = _log(store).query("acme", project="widget")
    assert [row["action"] for row in rows] == ["grant_role"]


# ------------------------------------------------------------- membership


def test_list_members_shows_the_org_and_its_tokens_to_an_admin(org):
    client, _store, tenants = org
    tenants.add_member("acme", "anya", "view")
    _tool(client, "create_agent_token", name="ci", org="acme",
          workspace="main", projects=["widget"], role="edit")
    body = _tool(client, "list_members", org="acme")
    assert body["members"] == [{"handle": "anya", "role": "view"},
                               {"handle": "nikita", "role": "admin"}]
    assert [ws["id"] for ws in body["workspaces"]] == ["main"]
    assert [row["name"] for row in body["tokens"]] == ["ci"]
    assert "digest" not in body["tokens"][0]


def test_a_viewer_sees_members_but_not_the_token_inventory(org):
    client, store, tenants = org
    store.enrol(store.add_user("anya"), "correct horse battery")
    tenants.add_member("acme", "anya", "view")
    _tool(client, "create_agent_token", name="ci", org="acme",
          workspace="main", projects=["widget"], role="edit")
    client.cookies.clear()
    client.post("/api/auth/login", json={"handle": "anya",
                                         "password": "correct horse battery"})
    body = _tool(client, "list_members", org="acme")
    assert {row["handle"] for row in body["members"]} == {"anya", "nikita"}
    assert "tokens" not in body


def test_a_non_member_gets_a_permission_error_not_an_existence_oracle(org):
    client, store, _tenants = org
    store.enrol(store.add_user("mallory"), "correct horse battery")
    client.cookies.clear()
    client.post("/api/auth/login", json={"handle": "mallory",
                                         "password": "correct horse battery"})
    refused = _tool(client, "list_members", org="acme")
    assert refused["error"]["type"] == "permission_error"
    # **The same answer, byte for byte**, for an org that does not exist at
    # all: a 404 here would tell a stranger which orgs are real, which is the
    # existence oracle FR5 forbids and the reason `authz.require`'s own message
    # never says whether a project exists.
    missing = _tool(client, "list_members", org="ghost")
    assert missing["error"]["type"] == "permission_error"
    assert missing["error"]["details"] == {"required": "view", "project": None,
                                           "principal_role": None}
    assert refused["error"]["details"] == missing["error"]["details"]


# -------------------------------------------------------------- sync stub


def test_sync_status_is_a_documented_stub(org):
    body = _tool(org[0], "sync_status", project="widget", org="acme",
                 workspace="main")
    assert body["remote"] is None
    assert "slice 6" in body["note"]
    assert body["project"] == "widget" and body["org"] == "acme"


def test_sync_status_needs_view_on_the_project(org):
    client, store, _tenants = org
    store.enrol(store.add_user("mallory"), "correct horse battery")
    client.cookies.clear()
    client.post("/api/auth/login", json={"handle": "mallory",
                                         "password": "correct horse battery"})
    refused = _tool(client, "sync_status", project="widget", org="acme",
                    workspace="main")
    assert refused["error"]["type"] == "permission_error"


def test_a_tool_may_not_name_a_tenant_the_request_is_not_in(hosted):
    """Once slice 4 resolves a tenant per request, naming a different one in
    the arguments is a refusal — not an override. Simulated here by setting the
    ContextVar in-process, which is exactly what `security.guard` will do."""
    from agentcad.core.tenancy import TenancyStore, tenant_scope
    from agentcad.core.tools import build_registry
    from agentcad.server import security as sec

    client, store = hosted
    tenants = TenancyStore(store.root)
    tenants.create_org("acme", admin="nikita")
    tenants.create_workspace("acme", "main")
    tenants.add_project("acme", "main", "widget")
    registry = build_registry(client.agentcad_service)
    who = sec.Principal(kind="user", name="nikita", role="admin", via="cookie")
    token = sec._principal_var.set(who)                   # noqa: SLF001
    try:
        with tenant_scope(("acme", "main")):
            allowed = registry.call("sync_status", {"project": "widget"})
            refused = registry.call("sync_status", {"project": "widget",
                                                    "org": "initech"})
    finally:
        sec._principal_var.reset(token)                   # noqa: SLF001
    assert allowed["org"] == "acme"
    assert refused["error"]["type"] == "permission_error"


# ------------------------------------------------------------------- AC5


def test_ac5_a_scoped_token_edits_a_is_refused_on_b_and_dies_on_revocation(org):
    """AC5, end to end over HTTP.

    The refusal on B is `authz`'s, reached through a tool that checks its own
    floor. The *general* every-tool enforcement is slice 4's registry wrapper;
    what is asserted here is the decision (`authz.require`) and one real tool
    surface that consumes it, so the wrapper has nothing left to invent.
    """
    from agentcad.core.authz import PermissionDeniedError, require

    client, store, tenants = org
    minted = _tool(client, "create_agent_token", name="ci", org="acme",
                   workspace="main", projects=["widget"], role="edit")
    bearer = _bearer(minted["token"])

    # A: the token is an editor, and the tool surface answers.
    assert require(tenants, "edit", "agent:ci", "acme", "main", "widget") == "edit"
    allowed = _tool(client, "sync_status", headers=bearer, project="widget",
                    org="acme", workspace="main")
    assert allowed["project"] == "widget" and "error" not in allowed

    # B: same token, same org, a project it was not scoped to.
    with pytest.raises(PermissionDeniedError) as excinfo:
        require(tenants, "view", "agent:ci", "acme", "main", "bracket")
    assert excinfo.value.details["required"] == "view"
    assert excinfo.value.details["principal_role"] is None
    refused = _tool(client, "sync_status", headers=bearer, project="bracket",
                    org="acme", workspace="main")
    assert refused["error"]["type"] == "permission_error"
    assert not scope_allows(store.resolve_token(minted["token"])["scope"],
                            "acme", "main", "bracket")

    # Revocation bites on the very next request — the store is the authority,
    # which is the whole reason these are not JWTs.
    assert client.get("/api/projects", headers=bearer).status_code == 200
    _tool(client, "revoke_agent_token", token_id=minted["id"])
    assert client.get("/api/projects", headers=bearer).status_code == 401
    assert client.post("/api/tools/whoami", json={},
                       headers=bearer).status_code == 401


# ------------------------------------------------------- the audit route


def test_auth_events_are_recorded_against_the_instance(hosted):
    client, store = hosted
    client.post("/api/auth/login", json=GOOD)
    client.post("/api/auth/login", json={"handle": "nikita",
                                         "password": "wrong password"})
    client.post("/api/auth/logout")
    rows = _log(store).query(audit_mod.INSTANCE_ORG)
    got = [(row["action"], row["principal"], row["outcome"]) for row in rows]
    assert ("login", "user:nikita", "ok") in got
    assert ("login", "user:nikita", "failed") in got
    assert ("logout", "user:nikita", "ok") in got


def test_a_failed_sign_in_for_an_unknown_handle_records_the_claim(hosted):
    client, store = hosted
    client.post("/api/auth/login", json={"handle": "ghost",
                                         "password": "correct horse battery"})
    rows = _log(store).query(audit_mod.INSTANCE_ORG, action="login")
    assert [(r["principal"], r["outcome"]) for r in rows] == [
        ("user:ghost", "failed")]


def test_token_mint_and_revoke_through_the_005a_routes_are_recorded(hosted):
    client, store = hosted
    client.post("/api/auth/login", json=GOOD)
    minted = client.post("/api/auth/tokens", json={"name": "ci"}).json()
    client.delete(f"/api/auth/tokens/{minted['id']}")
    actions = [row["action"]
               for row in _log(store).query(audit_mod.INSTANCE_ORG)]
    assert "token_add" in actions and "token_revoke" in actions
    # The secret is minted in the handler, never in the arguments — and no row
    # anywhere carries it.
    rows = _log(store).query(audit_mod.INSTANCE_ORG, limit=50)
    assert all(minted["token"] not in str(row) for row in rows)


def test_the_audit_route_is_for_an_administrator(hosted):
    client, store = hosted
    store.enrol(store.add_user("anya"), "correct horse battery")
    client.post("/api/auth/login", json={"handle": "anya",
                                         "password": "correct horse battery"})
    assert client.get("/api/auth/audit").status_code == 403
    client.cookies.clear()
    assert client.get("/api/auth/audit").status_code == 401
    bearer = store.add_token("root", role="admin")
    assert client.get("/api/auth/audit",
                      headers=_bearer(bearer)).status_code == 403


def test_the_audit_route_answers_the_instance_log_by_default(hosted):
    client, _store = hosted
    client.post("/api/auth/login", json=GOOD)
    body = client.get("/api/auth/audit").json()
    assert body["org"] == audit_mod.INSTANCE_ORG
    assert body["rows"][-1]["action"] == "login"
    assert body["total"] >= 1
    assert body["limit"] == audit_mod.DEFAULT_LIMIT and body["offset"] == 0


def test_the_audit_route_filters_and_paginates(org):
    client, _store, _tenants = org
    _tool(client, "grant_role", project="widget", principal="anya",
          role="edit", org="acme", workspace="main")
    _tool(client, "grant_role", project="bracket", principal="anya",
          role="edit", org="acme", workspace="main")
    body = client.get("/api/auth/audit",
                      params={"org": "acme", "project": "widget"}).json()
    assert [row["project"] for row in body["rows"]] == ["widget"]
    page = client.get("/api/auth/audit",
                      params={"org": "acme", "action": "grant_role",
                              "limit": 1}).json()
    assert len(page["rows"]) == 1 and page["next_offset"] == 1
    assert client.get("/api/auth/audit",
                      params={"org": "acme", "principal": "user:nikita",
                              "since": "1d"}).json()["rows"]
    assert client.get("/api/auth/audit",
                      params={"org": "acme", "since": "yesterday"}
                      ).status_code == 422
    assert client.get("/api/auth/audit",
                      params={"org": "acme", "limit": "lots"}
                      ).status_code == 422


def test_the_audit_route_does_not_create_a_database_for_a_guessed_org(hosted):
    client, store = hosted
    client.post("/api/auth/login", json=GOOD)
    body = client.get("/api/auth/audit", params={"org": "ghost"}).json()
    assert body == {"org": "ghost", "rows": [], "limit": 200, "offset": 0,
                    "total": 0, "next_offset": None}
    assert "ghost" not in _log(store).orgs()


def test_an_org_admin_reads_their_own_orgs_log_and_no_other(org):
    client, store, tenants = org
    tenants.create_org("initech", admin="mallory")
    store.enrol(store.add_user("mallory"), "correct horse battery")
    tenants.add_member("acme", "mallory", "edit")
    _tool(client, "grant_role", project="widget", principal="anya",
          role="edit", org="acme", workspace="main")
    client.cookies.clear()
    client.post("/api/auth/login", json={"handle": "mallory",
                                         "password": "correct horse battery"})
    # An org admin of `initech`, a mere editor in `acme`.
    assert client.get("/api/auth/audit",
                      params={"org": "initech"}).status_code == 200
    assert client.get("/api/auth/audit",
                      params={"org": "acme"}).status_code == 403
    # And never the instance-wide log, which is the operator's.
    assert client.get("/api/auth/audit").status_code == 403


def test_reading_the_audit_log_is_itself_audited(hosted):
    client, store = hosted
    client.post("/api/auth/login", json=GOOD)
    client.get("/api/auth/audit")
    rows = _log(store).query(audit_mod.INSTANCE_ORG, action="audit_query")
    assert len(rows) == 1 and rows[0]["principal"] == "user:nikita"
