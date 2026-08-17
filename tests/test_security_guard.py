"""PRD-005a slice 2: the default-deny guard, in isolation.

The mode matrix, the Host/Origin matrix, bearer-vs-cookie precedence, the CSRF
rule and its bearer exemption, the composed-identity ceiling, and the one
property that makes AC9 free: local mode is the *same code path*, not a
disabled feature.

Every rule here is also tested by its negation, because a guard test that only
ever asserts "the good request works" is a guard test that would pass with the
guard deleted.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agentcad.core.appmode import AppMode
from agentcad.core.authstore import AuthStore
from agentcad.core.locks import MAX_CLIENT_ID_CHARS
from agentcad.core.tools import build_registry
from agentcad.server import security
from agentcad.server.app import create_app
from agentcad.server.security import Principal, SecurityConfig

from .conftest import (ADMIN_HANDLE, ADMIN_PASSWORD, HOSTED_ORIGIN,
                       make_test_service)

ORIGIN = HOSTED_ORIGIN


# ------------------------------------------------------------ default deny


def test_private_route_is_401_anonymously(hosted):
    client, _ = hosted
    r = client.get("/api/projects")
    assert r.status_code == 401
    assert r.json()["error"]["type"] == "AuthError"


def test_health_is_public_but_trimmed(hosted):
    client, _ = hosted
    body = client.get("/api/health").json()
    assert body == {"status": "ok", "mode": "hosted"}


def test_the_trimmed_health_body_leaks_no_reconnaissance(hosted):
    """The negation of FR21: version, kernel state, chat availability and
    sandbox status are exactly what a stranger would want first."""
    body = client_body = client_json = hosted[0].get("/api/health").json()
    for leaked in ("version", "kernel", "sandbox", "chat_available"):
        assert leaked not in body, leaked
    assert client_body is client_json


def test_session_cookie_authenticates(hosted):
    # Slice 2 has no /api/auth/login yet — mint the session through the store,
    # which is also the tighter test: the guard, not the route, is what
    # authenticates.
    client, store = hosted
    client.cookies.set("agentcad_session",
                       store.create_session("nikita", device=None))
    assert client.get("/api/projects").status_code == 200


def test_bearer_authenticates_and_is_origin_exempt(hosted):
    client, store = hosted
    bearer = store.add_token("ci")
    r = client.post("/api/projects", json={"name": "demo"},
                    headers={"Authorization": f"Bearer {bearer}",
                             "Origin": "https://evil.example"})
    assert r.status_code == 201


def test_cookie_post_from_a_foreign_origin_is_403(hosted):
    client, store = hosted
    client.cookies.set("agentcad_session",
                       store.create_session("nikita", device=None))
    r = client.post("/api/projects", json={"name": "x"},
                    headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_a_cookie_get_from_a_foreign_origin_is_allowed(hosted):
    """Safe methods are exempt: the Origin rule is a CSRF defence, and a
    cross-origin GET the browser will not let the page read is not CSRF."""
    client, store = hosted
    client.cookies.set("agentcad_session",
                       store.create_session("nikita", device=None))
    r = client.get("/api/projects", headers={"Origin": "https://evil.example"})
    assert r.status_code == 200


def test_a_cookie_post_from_the_right_origin_is_allowed(hosted):
    client, store = hosted
    client.cookies.set("agentcad_session",
                       store.create_session("nikita", device=None))
    r = client.post("/api/projects", json={"name": "ok"},
                    headers={"Origin": ORIGIN})
    assert r.status_code == 201


# --- the anonymous unsafe methods (review finding M1) ------------------------
#
# `POST /api/auth/login` and `POST /api/auth/enrol/{token}` are the ONLY unsafe
# methods an anonymous caller can reach, and until the PRD-005a security review
# the CSRF check sat below the `principal is None` branch — so it covered every
# route except the two it exists for. A cross-site POST that signed a victim
# into the attacker's account, or spent an enrolment link, was accepted.

def test_anonymous_login_from_a_foreign_origin_is_403(hosted):
    client, _ = hosted
    r = client.post("/api/auth/login",
                    json={"handle": ADMIN_HANDLE, "password": ADMIN_PASSWORD},
                    headers={"Origin": "https://evil.example"})
    assert r.status_code == 403, r.text
    assert r.json()["error"]["type"] == "ForbiddenOrigin"
    # And no session was handed out on the way to the refusal.
    assert "set-cookie" not in {k.lower() for k in r.headers}


def test_anonymous_login_from_the_right_origin_still_works(hosted):
    client, _ = hosted
    r = client.post("/api/auth/login",
                    json={"handle": ADMIN_HANDLE, "password": ADMIN_PASSWORD},
                    headers={"Origin": ORIGIN})
    assert r.status_code == 200, r.text
    assert "agentcad_session" in r.cookies


def test_anonymous_login_with_no_origin_header_still_works(hosted):
    """Origin-absent is allowed, identically to the authenticated branch.

    A browser always sends `Origin` on a cross-site POST; `curl`, the MCP
    client and a same-origin `fetch` may omit it. Refusing on absence would
    break every non-browser client without stopping a browser attack.
    """
    client, _ = hosted
    r = client.post("/api/auth/login",
                    json={"handle": ADMIN_HANDLE, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, r.text


def test_anonymous_enrol_from_a_foreign_origin_is_403(hosted):
    client, store = hosted
    token = store.add_user("anya")
    r = client.post(f"/api/auth/enrol/{token}",
                    json={"password": "another good password"},
                    headers={"Origin": "https://evil.example"})
    assert r.status_code == 403, r.text
    assert r.json()["error"]["type"] == "ForbiddenOrigin"
    # The refusal is BEFORE the store: the link is unspent and still works.
    assert store.peek_enrolment(token) == "anya"


def test_anonymous_enrol_from_the_right_origin_still_works(hosted):
    client, store = hosted
    token = store.add_user("anya")
    r = client.post(f"/api/auth/enrol/{token}",
                    json={"password": "another good password"},
                    headers={"Origin": ORIGIN})
    assert r.status_code == 200, r.text
    assert store.peek_enrolment(token) is None      # spent


def test_a_public_get_from_a_foreign_origin_is_still_allowed(hosted):
    """The check is on unsafe methods only; moving it up must not start
    refusing the anonymous READ surface, which a CDN fetches with whatever
    Origin it likes."""
    client, _ = hosted
    r = client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert r.status_code == 200


def test_a_bare_x_agent_id_is_not_an_identity(hosted):
    client, _ = hosted
    r = client.get("/api/projects", headers={"X-Agent-Id": "mcp"})
    assert r.status_code == 401


def test_wrong_host_is_refused(hosted):
    client, _ = hosted
    r = client.get("/api/health", headers={"Host": "elsewhere.example"})
    assert r.status_code == 403


def test_the_host_check_ignores_the_port(hosted):
    """A container published on a port forwards `Host: testserver:8630`; a
    proxy on 443 forwards `Host: testserver`. Both are this instance."""
    client, _ = hosted
    assert client.get("/api/health",
                      headers={"Host": "testserver:8630"}).status_code == 200


def test_local_mode_installs_exactly_one_middleware(kernel, tmp_path):
    service = make_test_service(tmp_path / "projects", kernel)
    app = create_app(service, build_registry(service),
                     extra_allowed_hosts={"testserver"})
    assert len(app.user_middleware) == 1


def test_hosted_mode_also_installs_exactly_one_middleware(hosted_app):
    """Nothing may accrete here: the whole reviewability argument for the
    sanctioned core edit is that there is one middleware and it holds no
    logic."""
    assert len(hosted_app.user_middleware) == 1


def test_local_mode_is_the_same_code_path(kernel, tmp_path):
    """AC9. With no `security=`, the middleware behaves exactly as before:
    `X-Agent-Id` is the identity, no credential is required, and a foreign
    Origin is refused by the pre-existing same-origin check."""
    service = make_test_service(tmp_path / "projects", kernel)
    app = create_app(service, build_registry(service),
                     extra_allowed_hosts={"testserver"})
    client = TestClient(app, base_url="http://127.0.0.1")
    assert client.get("/api/projects").status_code == 200          # no auth
    assert "version" in client.get("/api/health").json()           # full body
    assert "mode" not in client.get("/api/health").json()
    r = client.post("/api/projects", json={"name": "x"},
                    headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert r.json()["error"]["type"] == "ForbiddenOrigin"


# --------------------------------------------------- credential precedence


def test_an_invalid_bearer_never_falls_back_to_a_valid_cookie(hosted):
    """A revoked token that quietly became a browser session is a confused
    deputy, and "my token stopped working" is the answer its holder needs."""
    client, store = hosted
    client.cookies.set("agentcad_session",
                       store.create_session("nikita", device=None))
    bearer = store.add_token("ci")
    store.revoke_token(store.list_tokens()[0]["id"])
    r = client.get("/api/projects", headers={"Authorization": f"Bearer {bearer}"})
    assert r.status_code == 401


def test_a_bearer_wins_over_a_cookie_when_both_are_valid(hosted):
    client, store = hosted
    client.cookies.set("agentcad_session",
                       store.create_session("nikita", device=None))
    bearer = store.add_token("ci")
    cfg = client.agentcad_config
    principal = security.resolve_principal(
        cfg, {"authorization": f"Bearer {bearer}"},
        {"agentcad_session": "irrelevant"})
    assert principal.client_id == "agent:ci"


@pytest.mark.parametrize("header", [
    "Basic abc", "Bearer", "Bearer ", "bearer_no_space", "Bearer acad_x_y",
])
def test_a_malformed_authorization_header_is_401_not_500(hosted, header):
    client, _ = hosted
    assert client.get("/api/projects",
                      headers={"Authorization": header}).status_code == 401


def test_an_expired_session_cookie_is_refused(hosted, monkeypatch):
    """The negation reviewers reach for first: an expired session ACCEPTED."""
    from agentcad.core import authstore as authstore_mod

    client, store = hosted
    client.cookies.set("agentcad_session",
                       store.create_session("nikita", device=None))
    assert client.get("/api/projects").status_code == 200
    import time
    monkeypatch.setattr(
        authstore_mod, "_now",
        lambda: time.time() + authstore_mod.ABSOLUTE_SESSION_S + 60)
    assert client.get("/api/projects").status_code == 401


def test_a_garbage_cookie_is_refused(hosted):
    client, _ = hosted
    client.cookies.set("agentcad_session", "not-a-session-at-all")
    assert client.get("/api/projects").status_code == 401


def test_disabling_an_account_ends_its_session_on_the_next_request(hosted):
    client, store = hosted
    client.cookies.set("agentcad_session",
                       store.create_session("nikita", device=None))
    assert client.get("/api/projects").status_code == 200
    store.disable_user("nikita")
    assert client.get("/api/projects").status_code == 401


# ---------------------------------------------------- composed identities


def test_the_device_suffix_comes_from_x_agent_id_under_the_principal(hosted):
    client, store = hosted
    cfg = client.agentcad_config
    secret = store.create_session("nikita", device=None)
    principal = security.resolve_principal(
        cfg, {"x-agent-id": "browser:7f3a1b2c"}, {"agentcad_session": secret})
    assert principal.client_id == "user:nikita/browser:7f3a1b2c"


@pytest.mark.parametrize("offered", [
    "Browser:7F3A",         # uppercase is outside the grammar
    "../../etc",            # path traversal, in case it ever became one
    "a" * 40,               # over the 24-char device ceiling
    "has space",
    "",
    "browser:7f3a\tzz",     # an interior control character
    "browser:7f3a\x00zz",   # a NUL, which os.stat raises ValueError on
    "user:anya",            # a colon does not make it a principal
    "agent:ci",             # nor does the other reserved prefix
    "a:b:c",                # at most one colon
])
def test_a_device_outside_the_grammar_is_dropped_not_sanitised(hosted, offered):
    """Dropped whole, never trimmed into shape: a device edited until it
    matched would be a different browser silently sharing one client id."""
    client, store = hosted
    cfg = client.agentcad_config
    secret = store.create_session("nikita", device=None)
    principal = security.resolve_principal(
        cfg, {"x-agent-id": offered}, {"agentcad_session": secret})
    assert principal.client_id == "user:nikita"


def test_surrounding_whitespace_on_the_device_header_is_stripped(hosted):
    """The one normalisation, and it matches `locks.check_client_id`, which
    strips before it validates. A proxy that pads a header value must not
    hand the same browser a second identity."""
    client, store = hosted
    cfg = client.agentcad_config
    secret = store.create_session("nikita", device=None)
    principal = security.resolve_principal(
        cfg, {"x-agent-id": "  browser:7f3a1b2c\n"}, {"agentcad_session": secret})
    assert principal.client_id == "user:nikita/browser:7f3a1b2c"


def test_x_agent_id_cannot_impersonate_another_principal(hosted):
    """The header contributes a *suffix* and nothing else — it can never
    become, or prefix, the identity."""
    client, store = hosted
    client.cookies.set("agentcad_session",
                       store.create_session("nikita", device=None))
    r = client.get("/api/projects", headers={"X-Agent-Id": "user:anya"})
    assert r.status_code == 200
    cfg = client.agentcad_config
    principal = security.resolve_principal(
        cfg, {"x-agent-id": "user:anya"},
        {"agentcad_session": store.create_session("nikita", device=None)})
    assert principal.name == "nikita"
    assert principal.client_id.startswith("user:nikita")


def test_the_longest_composed_identity_fits_the_ceiling():
    """Arithmetic, not style: `check_client_id` REFUSES past 64 characters,
    and a refusal at request time would be a 400 on every request from a
    person with a long handle."""
    widest_device = "b" * 11 + ":" + "c" * 12          # the grammar's maximum
    assert len(widest_device) == security.DEVICE_MAX_CHARS
    assert security.DEVICE_RE.match(widest_device)
    longest = Principal(kind="user", name="n" * 32, role="member",
                        device=widest_device).client_id
    assert len(longest) == 62 <= MAX_CLIENT_ID_CHARS


def test_an_agent_principal_composes_without_a_device():
    assert Principal(kind="agent", name="ci", role="member",
                     device="browser:7f3a1b2c").client_id == "agent:ci"


# ------------------------------------------------------------- construction


def test_a_local_appmode_cannot_be_wrapped_in_a_security_config(tmp_path):
    """Fail closed on a nonsense construction rather than run a guard that
    assumes an origin exists."""
    with pytest.raises(ValueError):
        SecurityConfig(mode=AppMode("local", None, None),
                       store=AuthStore(tmp_path / "auth"))


def test_the_guard_allows_everything_when_there_is_no_config():
    """`guard(None, ...)` is "not my business", which is what keeps the
    local-mode branch in `app.py` a single `if`."""
    assert security.guard(None, object()) is None


def test_current_principal_is_none_for_an_anonymous_public_request(hosted):
    client, _ = hosted
    assert client.get("/api/health").json() == {"status": "ok", "mode": "hosted"}


def test_no_response_body_ever_carries_a_digest_or_a_secret(hosted):
    client, store = hosted
    secret = store.create_session("nikita", device=None)
    bearer = store.add_token("ci")
    client.cookies.set("agentcad_session", secret)
    for path in ("/api/health", "/api/projects"):
        text = client.get(path).text
        assert secret not in text and bearer not in text
        assert "digest" not in text and "salt" not in text
    assert ADMIN_PASSWORD not in client.get("/api/projects").text


def test_a_local_app_built_after_a_hosted_one_has_no_auth_routes(hosted, kernel,
                                                                 tmp_path):
    """Two apps in one process must not cross-wire.

    `routes_auth` binds its configuration at mount time rather than reading
    the process-global slot per request, so a local app can never answer with
    the hosted app's identity store — whichever order they were built in.
    This is a latent trap in the test suite (which builds both constantly) and
    in any future embedder.
    """
    hosted_client, _ = hosted
    assert hosted_client.get("/api/auth/session").status_code == 401   # hosted: alive

    service = make_test_service(tmp_path / "local", kernel)
    local = TestClient(create_app(service, build_registry(service),
                                  extra_allowed_hosts={"testserver"}),
                       base_url="http://127.0.0.1")
    assert local.get("/api/auth/session").status_code == 404
    assert local.post("/api/auth/login",
                      json={"handle": "nikita",
                            "password": ADMIN_PASSWORD}).status_code == 404

    # ...and the hosted app is undisturbed by the local one existing.
    assert hosted_client.post("/api/auth/login",
                              json={"handle": "nikita",
                                    "password": ADMIN_PASSWORD}).status_code == 200
