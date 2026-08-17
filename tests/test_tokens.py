"""PRD-005a slice 4: agent bearer tokens, `whoami`, and the token routes.

FR3/FR4/FR11 and AC4's second half. A token is the credential an agent holds,
so the adversarial half is what matters: the secret must be returned exactly
once, revocation must bite on the very next call, expiry must be real, and
minting one must not be reachable from the credential it would mint.
"""

from __future__ import annotations

import time

import pytest

from .conftest import ADMIN_PASSWORD, make_test_service

GOOD = {"handle": "nikita", "password": ADMIN_PASSWORD}


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------- whoami


def test_a_token_authenticates_and_whoami_answers(hosted):
    client, store = hosted
    bearer = store.add_token("ci", role="member")
    r = client.post("/api/tools/whoami", json={}, headers=_bearer(bearer))
    assert r.status_code == 200, r.text
    assert r.json() == {"principal": "agent:ci", "kind": "agent",
                        "role": "member", "mode": "hosted"}


def test_whoami_answers_for_a_signed_in_person_too(hosted):
    """The composed principal is what every downstream surface renders, so
    `whoami` must report the same string `locks.current_client_id()` carries —
    not a bare handle."""
    client, _ = hosted
    client.post("/api/auth/login", json=GOOD)
    body = client.post("/api/tools/whoami", json={},
                       headers={"X-Agent-Id": "browser:7f3a1b2c"}).json()
    assert body == {"principal": "user:nikita/browser:7f3a1b2c", "kind": "user",
                    "role": "admin", "mode": "hosted"}


def test_whoami_is_listed_in_the_hosted_tool_surface(hosted):
    """The positive control for the local-mode absence below: without it, a
    `whoami` that failed to register anywhere would pass that test happily."""
    client, _ = hosted
    client.post("/api/auth/login", json=GOOD)
    names = {t["name"] for t in client.get("/api/tools").json()["tools"]}
    assert "whoami" in names


def test_whoami_is_not_registered_in_local_mode(kernel, tmp_path):
    """The FEM precedent: a pack registers a tool only when it can run. In
    local mode there is no principal to report, and a `whoami` answering
    "local" would be a tool whose only content is that the feature is off."""
    from agentcad.core.tools import build_registry
    from agentcad.server import security as security_module

    security_module.install(None)
    service = make_test_service(tmp_path / "projects", kernel)
    names = {t.name for t in build_registry(service).list()}
    assert "whoami" not in names


# ----------------------------------------------------------- the credential


def test_revocation_takes_effect_on_the_next_call(hosted):
    client, store = hosted
    bearer = store.add_token("ci")
    assert client.get("/api/projects", headers=_bearer(bearer)).status_code == 200
    store.revoke_token(store.list_tokens()[0]["id"])
    assert client.get("/api/projects", headers=_bearer(bearer)).status_code == 401


def test_an_expired_token_stops_authenticating(hosted, monkeypatch):
    client, store = hosted
    bearer = store.add_token("nightly", ttl_days=1)
    assert client.get("/api/projects", headers=_bearer(bearer)).status_code == 200
    monkeypatch.setattr("agentcad.core.authstore._now",
                        lambda: time.time() + 2 * 86400)
    assert client.get("/api/projects", headers=_bearer(bearer)).status_code == 401


def test_a_forged_bearer_is_401_and_never_falls_back_to_the_cookie(hosted):
    """A present-but-invalid bearer must not quietly become the browser
    session that happens to ride the same request: "my token stopped working"
    is the answer the holder needs, and a confused deputy is the alternative."""
    client, _ = hosted
    client.post("/api/auth/login", json=GOOD)
    assert client.get("/api/projects").status_code == 200
    forged = "acad_deadbeef_" + "x" * 43
    assert client.get("/api/projects", headers=_bearer(forged)).status_code == 401


def test_a_token_is_origin_exempt_because_a_browser_cannot_attach_one(hosted):
    """The CSRF rule covers cookie-authenticated writes. A bearer is exempt by
    design (FR18) — and this is the test that keeps the exemption honest."""
    client, store = hosted
    bearer = store.add_token("ci")
    r = client.post("/api/projects", json={"name": "demo"},
                    headers={**_bearer(bearer), "Origin": "https://evil.example"})
    assert r.status_code == 201, r.text


# -------------------------------------------------------------- the routes


def test_the_secret_is_returned_exactly_once(hosted):
    client, _ = hosted
    client.post("/api/auth/login", json=GOOD)
    created = client.post("/api/auth/tokens", json={"name": "ci"})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["token"].startswith("acad_")
    listed = client.get("/api/auth/tokens").json()["tokens"]
    assert [row["name"] for row in listed] == ["ci"]
    assert "token" not in listed[0] and "digest" not in listed[0]
    # The response body of the LIST is where a digest would leak in bulk.
    assert "digest" not in client.get("/api/auth/tokens").text


def test_a_minted_token_actually_works_and_can_be_revoked_over_http(hosted):
    client, _ = hosted
    client.post("/api/auth/login", json=GOOD)
    created = client.post("/api/auth/tokens",
                          json={"name": "ci", "ttl_days": 30}).json()
    assert client.get("/api/projects",
                      headers=_bearer(created["token"])).status_code == 200
    assert client.delete(f"/api/auth/tokens/{created['id']}").status_code == 200
    assert client.get("/api/projects",
                      headers=_bearer(created["token"])).status_code == 401


def test_a_member_may_not_mint_or_list_tokens(hosted):
    client, store = hosted
    client.post(f"/api/auth/enrol/{store.add_user('anya')}",
                json={"password": "hunter2hunter2"})
    for method, path in (("GET", "/api/auth/tokens"),
                         ("POST", "/api/auth/tokens"),
                         ("DELETE", "/api/auth/tokens/deadbeef")):
        r = client.request(method, path, json={"name": "mine"})
        assert r.status_code == 403, f"{method} {path}"
        assert r.json()["error"]["type"] == "AuthzError"


def test_an_admin_token_may_not_mint_another_token(hosted):
    """Design Decision 14: minting credentials from the same authenticated
    surface those credentials unlock is the privilege-escalation shape this
    design avoids while there is no audit log. A token drives the product; a
    signed-in human manages credentials."""
    client, store = hosted
    bearer = store.add_token("root", role="admin")
    r = client.post("/api/auth/tokens", json={"name": "second"},
                    headers=_bearer(bearer))
    assert r.status_code == 403
    assert r.json()["error"]["type"] == "AuthzError"


def test_a_bad_token_name_is_refused_rather_than_truncated(hosted):
    """`agent:<name>` composes into the 64-character client identity that
    `locks.check_client_id` refuses to truncate."""
    client, _ = hosted
    client.post("/api/auth/login", json=GOOD)
    r = client.post("/api/auth/tokens", json={"name": "Not A Name"})
    assert r.status_code == 422
    assert r.json()["error"]["type"] == "ValidationError"


def test_revoking_an_unknown_token_is_404(hosted):
    client, _ = hosted
    client.post("/api/auth/login", json=GOOD)
    assert client.delete("/api/auth/tokens/deadbeef").status_code == 404


def test_the_token_routes_are_not_public(hosted):
    """They are not on the nine-entry allowlist, so default deny covers them
    with no action by anybody — asserted rather than assumed."""
    client, _ = hosted
    for method, path in (("GET", "/api/auth/tokens"),
                         ("POST", "/api/auth/tokens"),
                         ("DELETE", "/api/auth/tokens/deadbeef")):
        assert client.request(method, path, json={}).status_code == 401


def test_a_token_holds_no_claim_and_blocks_nobody(hosted):
    """AC10's other half, over HTTP: an `agent:` principal is classified
    `agent` by `actor_kind`, so PRD-008's human-only claims neither protect it
    nor are broken by it."""
    client, store = hosted
    client.post("/api/auth/login", json=GOOD)
    client.post("/api/projects", json={"name": "demo"})
    bearer = store.add_token("ci")
    body = client.post("/api/projects/demo/presence",
                       json={"part_id": "box", "claim": True},
                       headers=_bearer(bearer)).json()
    assert body["you"] == "agent:ci"
    assert body["claims"] == {}


@pytest.mark.parametrize("payload", [
    {"name": "ci", "role": "admin"},
    {"name": "ci", "role": "member"},
])
def test_a_token_carries_the_role_it_was_minted_with(hosted, payload):
    client, _ = hosted
    client.post("/api/auth/login", json=GOOD)
    created = client.post("/api/auth/tokens", json=payload).json()
    body = client.post("/api/tools/whoami", json={},
                       headers=_bearer(created["token"])).json()
    assert body["role"] == payload["role"]
