"""PRD-005 FR1: OIDC sign-in, against an in-process identity provider.

The IdP is a second FastAPI app — discovery, JWKS, `/authorize`, `/token`,
its own RS256 key — reached through an `httpx.MockTransport` that hands each
request to its `TestClient`. **No network, no thread, no port**: the whole
round trip is two ASGI apps in one process, which is what makes the negative
cases (a rotated key, an `alg: none` token, a token that expired an hour ago)
cheap enough to assert one by one.

`agentcad.core.oidc.TRANSPORT` is the seam, and it is the only production code
that knows tests exist — the same module-level indirection as
`authstore._now`.
"""

from __future__ import annotations

import base64
import time
import urllib.parse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from agentcad.core import oidc as oidc_mod

from .conftest import ADMIN_HANDLE, ADMIN_PASSWORD, login

ISSUER = "https://idp.test"
CLIENT_ID = "agentcad-hosted"
CLIENT_SECRET = "s3cret-value"
EMAIL = "nikita@example.com"


# ------------------------------------------------------------------- the IdP

def _b64u_uint(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


class MockIdP:
    """A minimal but honest OpenID provider: PKCE S256 enforced, client
    authentication enforced, one signing key at a time (rotatable)."""

    def __init__(self) -> None:
        self.keys: dict[str, rsa.RSAPrivateKey] = {}
        self.active = ""
        #: `None` publishes every key it holds; a set publishes only those —
        #: which is how the "signed by a key the JWKS does not carry" case is
        #: staged without the provider erroring on its own missing key.
        self.publish: set[str] | None = None
        self.rotate("key-1")
        self.codes: dict[str, dict] = {}
        #: Knobs the negative tests turn.
        self.nonce_override: str | None = None
        self.alg_none = False
        self.expired = False
        self.email_verified = True
        self.email = EMAIL
        self.app = self._build()

    def rotate(self, kid: str, *, keep_old: bool = True) -> None:
        if not keep_old:
            self.keys.clear()
        self.keys[kid] = rsa.generate_private_key(public_exponent=65537,
                                                  key_size=2048)
        self.active = kid

    def _pem(self, kid: str) -> bytes:
        return self.keys[kid].private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption())

    def _build(self) -> FastAPI:
        app = FastAPI()

        @app.get("/.well-known/openid-configuration")
        def discovery():
            return {
                "issuer": ISSUER,
                "authorization_endpoint": f"{ISSUER}/authorize",
                "token_endpoint": f"{ISSUER}/token",
                "jwks_uri": f"{ISSUER}/jwks.json",
                "response_types_supported": ["code"],
                "id_token_signing_alg_values_supported": ["RS256"],
                "code_challenge_methods_supported": ["S256"],
            }

        @app.get("/jwks.json")
        def jwks():
            return {"keys": [
                {"kty": "RSA", "use": "sig", "alg": "RS256", "kid": kid,
                 "n": _b64u_uint(key.public_key().public_numbers().n),
                 "e": _b64u_uint(key.public_key().public_numbers().e)}
                for kid, key in self.keys.items()
                if self.publish is None or kid in self.publish]}

        @app.get("/authorize")
        def authorize(request: Request):
            query = dict(request.query_params)
            code = f"code-{len(self.codes)}-{time.time_ns()}"
            self.codes[code] = {
                "challenge": query.get("code_challenge"),
                "method": query.get("code_challenge_method"),
                "nonce": query.get("nonce"),
                "redirect_uri": query.get("redirect_uri"),
            }
            # A real IdP 302s back to redirect_uri; the test plays the browser
            # itself, so the params it would carry are returned as JSON.
            return {"code": code, "state": query.get("state")}

        @app.post("/token")
        async def token(request: Request):
            form = dict(await request.form())
            row = self.codes.pop(form.get("code", ""), None)
            if row is None:
                return JSONResponse({"error": "invalid_grant"}, 400)
            if not self._authenticated(request, form):
                return JSONResponse({"error": "invalid_client"}, 401)
            if row["method"] != "S256":
                return JSONResponse({"error": "invalid_request",
                                     "error_description": "PKCE required"}, 400)
            verifier = form.get("code_verifier", "")
            expect = oidc_mod._b64u(
                __import__("hashlib").sha256(verifier.encode()).digest())
            if row["challenge"] != expect:
                return JSONResponse({"error": "invalid_grant",
                                     "error_description": "PKCE mismatch"}, 400)
            if form.get("redirect_uri") != row["redirect_uri"]:
                return JSONResponse({"error": "invalid_grant"}, 400)
            return {"access_token": "at-1", "token_type": "Bearer",
                    "expires_in": 300, "id_token": self.id_token(row["nonce"])}

        return app

    def _authenticated(self, request: Request, form: dict) -> bool:
        header = request.headers.get("authorization") or ""
        if header.lower().startswith("basic "):
            raw = base64.b64decode(header.split(" ", 1)[1]).decode()
            user, _, secret = raw.partition(":")
            return (urllib.parse.unquote(user) == CLIENT_ID
                    and urllib.parse.unquote(secret) == CLIENT_SECRET)
        return (form.get("client_id") == CLIENT_ID
                and form.get("client_secret") == CLIENT_SECRET)

    def id_token(self, nonce: str | None) -> str:
        now = int(time.time())
        claims = {
            "iss": ISSUER, "sub": "idp-user-42", "aud": CLIENT_ID,
            "iat": now - (4000 if self.expired else 0),
            "exp": now - 3600 if self.expired else now + 300,
            "nonce": self.nonce_override if self.nonce_override is not None
            else nonce,
            "email": self.email, "email_verified": self.email_verified,
            "name": "Nikita Fedorov",
        }
        if self.alg_none:
            # `alg: none` — the one thing an ID-token verifier may never take.
            return jwt.encode(claims, key="", algorithm="none")
        return jwt.encode(claims, self._pem(self.active), algorithm="RS256",
                          headers={"kid": self.active})


@pytest.fixture
def idp(monkeypatch):
    """The provider, wired into `oidc.TRANSPORT` for the whole test."""
    provider = MockIdP()
    client = TestClient(provider.app, base_url=ISSUER)

    def handler(request: httpx.Request) -> httpx.Response:
        answer = client.request(
            request.method, str(request.url), content=request.content,
            headers={k: v for k, v in request.headers.items()
                     if k.lower() not in {"host", "content-length"}})
        return httpx.Response(answer.status_code, content=answer.content,
                              headers={"content-type":
                                       answer.headers.get("content-type",
                                                          "application/json")})

    monkeypatch.setattr(oidc_mod, "TRANSPORT", httpx.MockTransport(handler))
    yield provider


DOCUMENT = {
    "enabled": True,
    "issuer": ISSUER,
    "client_id": CLIENT_ID,
    "client_secret": CLIENT_SECRET,
    "label": "Example SSO",
    "allowed_email_domains": ["example.com"],
    "email_handles": {EMAIL: ADMIN_HANDLE},
}


def configure(store, **overrides) -> dict:
    document = {**DOCUMENT, **overrides}
    store.write_oidc(document)
    return document


# ------------------------------------------------------------------- helpers

def start(client) -> tuple[str, str]:
    """Follow `oidc/login` to the provider and play the browser hop.

    Returns `(code, state)` — what the IdP would put in the callback URL.
    """
    response = client.get("/api/auth/oidc/login", follow_redirects=False)
    assert response.status_code == 302, response.text
    return hop(response.headers["location"])


def hop(authorization_url: str) -> tuple[str, str]:
    with httpx.Client(transport=oidc_mod.TRANSPORT) as browser:
        answer = browser.get(authorization_url).json()
    return answer["code"], answer["state"]


def callback(client, code, state):
    return client.get("/api/auth/oidc/callback",
                      params={"code": code, "state": state},
                      follow_redirects=False)


# ------------------------------------------------------------- the happy path

def test_the_routes_404_until_a_provider_is_configured(hosted_client):
    """404, not 501: on an instance with no `oidc.json` the route genuinely
    does not exist — the `_config()` precedent for local mode."""
    for path in ("/api/auth/oidc/login", "/api/auth/oidc/callback"):
        response = hosted_client.get(path, follow_redirects=False)
        assert response.status_code == 404, path
        assert response.json()["error"]["type"] == "NotFoundError"


def test_the_full_code_and_pkce_round_trip_lands_a_session(hosted, idp):
    client, store = hosted
    configure(store)

    redirect = client.get("/api/auth/oidc/login", follow_redirects=False)
    assert redirect.status_code == 302
    query = dict(urllib.parse.parse_qsl(
        urllib.parse.urlparse(redirect.headers["location"]).query))
    # PKCE, and S256 — never `plain`, which is no protection at all.
    assert query["code_challenge_method"] == "S256"
    assert len(query["code_challenge"]) >= 43
    assert query["response_type"] == "code"
    assert query["client_id"] == CLIENT_ID
    assert query["redirect_uri"].endswith("/api/auth/oidc/callback")
    assert "openid" in query["scope"]
    # The verifier itself never leaves the server.
    assert "code_verifier" not in query

    code, state = hop(redirect.headers["location"])
    answer = callback(client, code, state)
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["principal"].startswith(f"user:{ADMIN_HANDLE}")
    assert body["via"] == "oidc" and body["link"] == "auto_linked"
    assert "agentcad_session" in answer.cookies or client.cookies.get(
        "agentcad_session")

    # The cookie is a real session: the same one `/auth/login` mints.
    assert client.get("/api/auth/session").json()["principal"].startswith(
        f"user:{ADMIN_HANDLE}")
    # ...and the identity is now linked, so the email map is no longer needed.
    assert store.find_oidc(ADMIN_HANDLE)["subject"] == "idp-user-42"


def test_a_linked_identity_signs_in_without_the_email_map(hosted, idp):
    client, store = hosted
    configure(store, email_handles={})
    store.link_oidc(ADMIN_HANDLE, ISSUER, "idp-user-42", email=EMAIL)

    code, state = start(client)
    assert callback(client, code, state).json()["link"] == "linked"


def test_the_browser_is_redirected_into_the_app_and_curl_is_not(hosted, idp):
    """A person lands on the callback by following the provider; showing them
    JSON would be the `enrol_page` mistake."""
    client, store = hosted
    configure(store)
    code, state = start(client)
    answer = client.get("/api/auth/oidc/callback",
                        params={"code": code, "state": state},
                        headers={"accept": "text/html"},
                        follow_redirects=False)
    assert answer.status_code == 303
    assert answer.headers["location"] == "/"
    assert "agentcad_session=" in answer.headers.get("set-cookie", "")


# ---------------------------------------------------------------- negatives

def test_a_bad_state_is_refused(hosted, idp):
    client, store = hosted
    configure(store)
    code, _state = start(client)
    answer = callback(client, code, "attacker-chosen-state")
    assert answer.status_code == 401
    assert client.get("/api/auth/session").status_code == 401


def test_a_callback_in_a_browser_that_did_not_start_the_flow_is_refused(
        hosted, idp):
    """**Login CSRF.** The attacker starts a sign-in here, authenticates at the
    provider as themselves, and feeds the resulting `code`+`state` to a
    victim's browser — which would otherwise be silently signed in *as the
    attacker* and go on working inside the attacker's account. `state` alone
    cannot stop this (it proves the flow started here, not that it started in
    this browser); the flow cookie can.
    """
    client, store = hosted
    configure(store)
    code, state = start(client)                  # the attacker's flow
    stolen = client.cookies.get(oidc_mod.FLOW_COOKIE)
    assert stolen                                # it really is set

    client.cookies.clear()                       # ...the victim's browser
    answer = callback(client, code, state)
    assert answer.status_code == 401
    assert client.get("/api/auth/session").status_code == 401

    # And the state was spent, so even the attacker cannot finish it now.
    client.cookies.set(oidc_mod.FLOW_COOKIE, stolen)
    assert callback(client, code, state).status_code == 401


def test_the_flow_cookie_is_httponly_scoped_and_cleared(hosted, idp):
    client, store = hosted
    configure(store)
    redirect = client.get("/api/auth/oidc/login", follow_redirects=False)
    cookie = redirect.headers["set-cookie"]
    assert oidc_mod.FLOW_COOKIE in cookie
    assert "HttpOnly" in cookie                  # script cannot read the binding
    assert "SameSite=lax" in cookie.replace("Lax", "lax")
    assert "Path=/api/auth/oidc" in cookie       # it goes nowhere else

    code, state = hop(redirect.headers["location"])
    done = callback(client, code, state)
    assert done.status_code == 200
    # Spent, so it is cleared rather than left in the browser.
    assert f"{oidc_mod.FLOW_COOKIE}=" in done.headers.get("set-cookie", "")
    assert not client.cookies.get(oidc_mod.FLOW_COOKIE)


def test_a_state_is_single_use(hosted, idp):
    """A replayed callback finds nothing — which is what makes an intercepted
    code useless on its own."""
    client, store = hosted
    configure(store)
    code, state = start(client)
    assert callback(client, code, state).status_code == 200
    client.cookies.clear()
    assert callback(client, code, state).status_code == 401


def test_a_wrong_nonce_is_refused(hosted, idp):
    """The one check pyjwt cannot make: it binds the token to *this* browser's
    authorization request."""
    client, store = hosted
    configure(store)
    idp.nonce_override = "a-nonce-from-another-flow"
    code, state = start(client)
    answer = callback(client, code, state)
    assert answer.status_code == 401
    assert client.get("/api/auth/session").status_code == 401


def test_an_unsigned_token_is_refused(hosted, idp):
    """`alg: none`. The allowlist is passed to `jwt.decode` verbatim and is
    also checked against the header first, so this dies twice."""
    client, store = hosted
    configure(store)
    idp.alg_none = True
    code, state = start(client)
    answer = callback(client, code, state)
    assert answer.status_code == 401
    assert "none" in answer.json()["error"]["message"]
    assert store.find_oidc(ADMIN_HANDLE) is None


def test_an_expired_token_is_refused(hosted, idp):
    client, store = hosted
    configure(store)
    idp.expired = True
    code, state = start(client)
    answer = callback(client, code, state)
    assert answer.status_code == 401
    assert "ExpiredSignatureError" in answer.json()["error"]["message"]


def test_a_token_signed_by_a_key_the_idp_never_published_is_refused(hosted, idp):
    """The signature check itself, with the JWKS honest: the IdP signs with a
    key it then drops from its own JWKS."""
    client, store = hosted
    configure(store)
    idp.rotate("ghost-key")
    idp.publish = {"key-1"}         # signs with ghost-key, advertises key-1
    code, state = start(client)
    assert callback(client, code, state).status_code == 401
    assert store.find_oidc(ADMIN_HANDLE) is None


def test_an_unverified_email_never_links(hosted, idp):
    client, store = hosted
    configure(store)
    idp.email_verified = False
    code, state = start(client)
    assert callback(client, code, state).status_code == 401
    assert store.find_oidc(ADMIN_HANDLE) is None


def test_an_unmapped_email_is_refused_and_creates_no_account(hosted, idp):
    """Closed registration, which is the whole linking policy: an identity the
    instance does not know signs in nothing and creates nothing."""
    client, store = hosted
    configure(store, email_handles={})
    before = [row["handle"] for row in store.list_users()]

    code, state = start(client)
    answer = callback(client, code, state)
    assert answer.status_code == 401
    assert answer.json()["error"]["message"] == oidc_mod.UNLINKED
    assert [row["handle"] for row in store.list_users()] == before
    assert client.get("/api/auth/session").status_code == 401


def test_an_email_outside_the_allowed_domains_is_refused(hosted, idp):
    client, store = hosted
    configure(store, allowed_email_domains=["corp.example"],
              email_handles={"nikita@example.com": ADMIN_HANDLE})
    code, state = start(client)
    assert callback(client, code, state).status_code == 401
    assert store.find_oidc(ADMIN_HANDLE) is None


def test_a_disabled_account_cannot_be_signed_in_by_sso(hosted, idp):
    """`admin user disable` must stop every door, not the password one."""
    client, store = hosted
    configure(store)
    store.link_oidc(ADMIN_HANDLE, ISSUER, "idp-user-42", email=EMAIL)
    store.disable_user(ADMIN_HANDLE)
    code, state = start(client)
    assert callback(client, code, state).status_code == 401


def test_an_invited_handle_is_enrolled_by_its_first_sso_sign_in(hosted, idp):
    """The one place a *disabled* account is opened: invited (so it has no
    password at all) **and** named by the admin's own email map. Two admin
    acts, plus a verified email."""
    client, store = hosted
    store.add_user("anya")                       # invited, never enrolled
    configure(store, email_handles={"anya@example.com": "anya"})
    idp.email = "anya@example.com"

    code, state = start(client)
    assert callback(client, code, state).status_code == 200
    assert store.get_user("anya")["disabled"] is False
    assert store.find_oidc("anya")["subject"] == "idp-user-42"
    # It still has no password: SSO enrolled it, it did not set one.
    assert store.verify_password("anya", "anything-at-all") is False


def test_a_revoked_account_is_not_re_opened_by_the_email_map(hosted, idp):
    """The mirror of the test above, and the reason `enrolled` is read: a
    disabled account that *was* enrolled is revoked, not invited."""
    client, store = hosted
    store.enrol(store.add_user("anya"), "hunter2hunter2")
    store.disable_user("anya")
    configure(store, email_handles={"anya@example.com": "anya"})
    idp.email = "anya@example.com"
    code, state = start(client)
    assert callback(client, code, state).status_code == 401
    assert store.get_user("anya")["disabled"] is True


def test_the_map_cannot_steal_an_account_that_is_already_linked(hosted, idp):
    """`email_handles` is admin-authored, but it must not be able to re-point a
    handle that already carries somebody else's identity."""
    client, store = hosted
    store.link_oidc(ADMIN_HANDLE, ISSUER, "somebody-else", email="other@x.test")
    configure(store)
    code, state = start(client)
    assert callback(client, code, state).status_code == 401
    assert store.find_oidc(ADMIN_HANDLE)["subject"] == "somebody-else"


# ------------------------------------------------------------ JWKS rollover

def test_a_rotated_signing_key_is_picked_up_without_a_restart(hosted, idp,
                                                              monkeypatch):
    """Key rollover: the IdP publishes a new key beside the old one and starts
    signing with it. The cached JWKS misses the new `kid`, refetches once, and
    the sign-in lands — the reason `PyJWKClient` is not used is that this fetch
    has to go through httpx, not urllib."""
    client, store = hosted
    configure(store)
    code, state = start(client)
    assert callback(client, code, state).status_code == 200
    client.cookies.clear()

    idp.rotate("key-2")                      # both keys published, key-2 signs
    # The refresh is bounded to once per JWKS_MIN_REFRESH_S; move the clock
    # rather than sleep.
    later = time.time() + oidc_mod.JWKS_MIN_REFRESH_S + 1
    monkeypatch.setattr(oidc_mod, "_now", lambda: later)

    code, state = start(client)
    assert callback(client, code, state).status_code == 200
    assert client.get("/api/auth/session").status_code == 200


def test_an_unknown_kid_does_not_refetch_more_than_once_per_window(hosted, idp):
    """The bound on the refresh: a stranger posting junk `kid`s must not turn
    our callback into a load generator against the provider."""
    client, store = hosted
    configure(store)
    code, state = start(client)
    assert callback(client, code, state).status_code == 200

    fetches = {"n": 0}

    def counting(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/jwks.json":
            fetches["n"] += 1
        answer = TestClient(idp.app, base_url=ISSUER).request(
            request.method, str(request.url), content=request.content,
            headers={k: v for k, v in request.headers.items()
                     if k.lower() not in {"host", "content-length"}})
        return httpx.Response(answer.status_code, content=answer.content,
                              headers={"content-type": "application/json"})

    oidc_mod.TRANSPORT = httpx.MockTransport(counting)
    idp.rotate("key-3")
    for _ in range(3):
        code, state = start(client)
        callback(client, code, state)
    assert fetches["n"] == 0, "the window was not honoured"


# ------------------------------------------------------------ explicit link

def test_a_signed_in_person_can_link_an_identity_explicitly(hosted, idp):
    """Path (a) of the linking policy: no email map at all, the person says so
    themselves — through a **POST**, so the guard's cross-origin rule covers
    it."""
    client, store = hosted
    configure(store, email_handles={})
    login(client)

    begun = client.post("/api/auth/oidc/link")
    assert begun.status_code == 200, begun.text
    code, state = hop(begun.json()["authorization_url"])
    answer = callback(client, code, state)
    assert answer.status_code == 200
    assert answer.json() == {"handle": ADMIN_HANDLE, "linked": True,
                             "issuer": ISSUER, "subject": "idp-user-42",
                             "email": EMAIL}
    assert store.find_oidc(ADMIN_HANDLE)["subject"] == "idp-user-42"

    # ...and it now signs in on its own.
    client.cookies.clear()
    code, state = start(client)
    assert callback(client, code, state).status_code == 200

    # Unlinking is the same person's to do.
    assert client.request("DELETE", "/api/auth/oidc/link").json()["unlinked"] is True
    assert store.find_oidc(ADMIN_HANDLE) is None


def test_a_link_finished_after_signing_out_is_refused(hosted, idp):
    """The session is re-read at the callback, not trusted from the pending
    record: a browser that signed out between starting a link and finishing it
    must not bind the identity to the handle it used to hold."""
    client, store = hosted
    configure(store, email_handles={})
    login(client)
    begun = client.post("/api/auth/oidc/link")
    code, state = hop(begun.json()["authorization_url"])

    client.cookies.delete("agentcad_session")          # signed out mid-flow
    answer = callback(client, code, state)
    assert answer.status_code == 401
    assert store.find_oidc(ADMIN_HANDLE) is None


def test_linking_requires_a_session(hosted, idp):
    client, store = hosted
    configure(store)
    assert client.post("/api/auth/oidc/link").status_code == 401


def test_an_identity_cannot_be_linked_to_two_accounts(hosted, idp):
    client, store = hosted
    configure(store, email_handles={})
    store.enrol(store.add_user("anya"), "hunter2hunter2")
    store.link_oidc("anya", ISSUER, "idp-user-42", email=EMAIL)

    login(client)
    begun = client.post("/api/auth/oidc/link")
    code, state = hop(begun.json()["authorization_url"])
    answer = callback(client, code, state)
    assert answer.status_code == 409
    assert store.find_oidc(ADMIN_HANDLE) is None


# ------------------------------------------------------- config + local mode

def test_a_malformed_document_names_the_field(hosted):
    _client, store = hosted
    store.write_oidc({"issuer": "not-a-url", "client_id": "x",
                      "client_secret": "y"})
    from agentcad.core.model import ValidationError

    with pytest.raises(ValidationError) as caught:
        oidc_mod.OidcConfig.from_document(store.read_oidc(), "http://testserver")
    assert caught.value.details["field"] == "issuer"


def test_a_disabled_document_is_no_provider_at_all(hosted):
    client, store = hosted
    configure(store, enabled=False)
    assert client.get("/api/auth/oidc/login",
                      follow_redirects=False).status_code == 404


def test_editing_the_document_takes_effect_without_a_restart(hosted, idp):
    """The client is cached per fingerprint, so an admin editing `oidc.json`
    through `docker compose exec` is live on the next request."""
    client, store = hosted
    configure(store, client_id="wrong-client")
    code, state = start(client)
    assert callback(client, code, state).status_code == 401   # bad client auth

    configure(store)                                    # fixed, no restart
    code, state = start(client)
    assert callback(client, code, state).status_code == 200


def test_password_sign_in_is_untouched(hosted, idp):
    """AC7's local half for this slice: adding SSO changed nothing about the
    door that was already there."""
    client, store = hosted
    configure(store)
    answer = client.post("/api/auth/login",
                         json={"handle": ADMIN_HANDLE, "password": ADMIN_PASSWORD})
    assert answer.status_code == 200
    assert answer.json()["principal"].startswith(f"user:{ADMIN_HANDLE}")


def test_the_oidc_routes_are_not_mounted_in_local_mode(kernel, tmp_path):
    """Local mode has no accounts at all — 404, exactly like `/auth/login`.
    Byte-identically what a local instance was before this slice (AC7)."""
    from agentcad.core.tools import build_registry
    from agentcad.server.app import create_app

    from .conftest import make_test_service

    service = make_test_service(tmp_path / "projects", kernel)
    app = create_app(service, build_registry(service),
                     extra_allowed_hosts={"testserver"})
    local = TestClient(app, base_url="http://127.0.0.1")
    assert local.get("/api/auth/oidc/login",
                     follow_redirects=False).status_code == 404
    assert local.post("/api/auth/oidc/link").status_code == 404
    assert local.post("/api/auth/passkey/login/begin").status_code == 404
