"""PRD-005 FR1: WebAuthn passkeys, driven through the real routes.

The relying party is duo-labs `webauthn` (the `agentcad[cloud]` extra); the
authenticator is a ~70-line **virtual ES256 passkey** built from `cryptography`
+ `cbor2` — the PRD-005 spike's harness (§B3), which is why `soft-webauthn` is
not a dependency here: it pins `fido2<...`, which drags `cryptography` back to
44 and conflicts with both `webauthn>=3` and this repo's own 50.

Every ceremony below goes through the HTTP routes, not the library: the point
is that `POST /api/auth/passkey/login/complete` ends with the *same session
cookie* `POST /api/auth/login` sets, and that the four failures a real
deployment meets (wrong origin, replayed challenge, cloned authenticator,
tampered signature) are refused by the running server.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import struct
import time

import pytest

from .conftest import ADMIN_HANDLE, ADMIN_PASSWORD, HOSTED_ORIGIN, login

cbor2 = pytest.importorskip("cbor2", reason="needs agentcad[cloud]")
webauthn = pytest.importorskip("webauthn", reason="needs agentcad[cloud]")

from cryptography.hazmat.primitives import hashes            # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec     # noqa: E402

RP_ID = "testserver"                     # `AppMode.origin_host` of HOSTED_ORIGIN
ORIGIN = HOSTED_ORIGIN                   # "http://testserver"

FLAG_UP, FLAG_UV, FLAG_BE, FLAG_BS, FLAG_AT = 0x01, 0x04, 0x08, 0x10, 0x40


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def b64u_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class VirtualAuthenticator:
    """A software passkey: ES256 (P-256), ``fmt: "none"`` attestation."""

    def __init__(self, user_handle: bytes = b"nikita") -> None:
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.cred_id = os.urandom(32)
        self.aaguid = b"\x00" * 16
        self.sign_count = 0
        self.user_handle = user_handle
        self.rp_id = RP_ID
        self.origin = ORIGIN

    # -- COSE ---------------------------------------------------------------
    def cose_key(self) -> bytes:
        nums = self.key.public_key().public_numbers()
        return cbor2.dumps({
            1: 2,                                   # kty: EC2
            3: -7,                                  # alg: ES256
            -1: 1,                                  # crv: P-256
            -2: nums.x.to_bytes(32, "big"),
            -3: nums.y.to_bytes(32, "big"),
        })

    def _auth_data(self, *, attested: bool) -> bytes:
        flags = FLAG_UP | FLAG_UV | FLAG_BE | FLAG_BS
        if attested:
            flags |= FLAG_AT
        data = (hashlib.sha256(self.rp_id.encode()).digest()
                + bytes([flags]) + struct.pack(">I", self.sign_count))
        if attested:
            data += (self.aaguid + struct.pack(">H", len(self.cred_id))
                     + self.cred_id + self.cose_key())
        return data

    # -- ceremonies ---------------------------------------------------------
    def create(self, challenge: bytes) -> dict:
        client_data = json.dumps({
            "type": "webauthn.create", "challenge": b64u(challenge),
            "origin": self.origin, "crossOrigin": False,
        }, separators=(",", ":")).encode()
        attestation = cbor2.dumps({
            "fmt": "none", "attStmt": {},
            "authData": self._auth_data(attested=True),
        })
        return {
            "id": b64u(self.cred_id), "rawId": b64u(self.cred_id),
            "type": "public-key",
            "response": {"clientDataJSON": b64u(client_data),
                         "attestationObject": b64u(attestation),
                         "transports": ["internal", "hybrid"]},
            "clientExtensionResults": {},
        }

    def get(self, challenge: bytes) -> dict:
        self.sign_count += 1
        client_data = json.dumps({
            "type": "webauthn.get", "challenge": b64u(challenge),
            "origin": self.origin, "crossOrigin": False,
        }, separators=(",", ":")).encode()
        auth_data = self._auth_data(attested=False)
        signature = self.key.sign(
            auth_data + hashlib.sha256(client_data).digest(),
            ec.ECDSA(hashes.SHA256()))
        return {
            "id": b64u(self.cred_id), "rawId": b64u(self.cred_id),
            "type": "public-key",
            "response": {"clientDataJSON": b64u(client_data),
                         "authenticatorData": b64u(auth_data),
                         "signature": b64u(signature),
                         "userHandle": b64u(self.user_handle)},
            "clientExtensionResults": {},
        }


# ------------------------------------------------------------------- helpers

def register(client, authenticator, label=None):
    """The full registration ceremony through the routes."""
    body = {"label": label} if label else {}
    begun = client.post("/api/auth/passkey/register/begin", json=body)
    assert begun.status_code == 200, begun.text
    options = begun.json()
    credential = authenticator.create(b64u_decode(options["challenge"]))
    return client.post("/api/auth/passkey/register/complete",
                       json={"credential": credential})


def authenticate(client, authenticator, handle=None):
    """The full authentication ceremony through the routes."""
    begun = client.post("/api/auth/passkey/login/begin",
                        json={"handle": handle} if handle else {})
    assert begun.status_code == 200, begun.text
    assertion = authenticator.get(b64u_decode(begun.json()["challenge"]))
    return client.post("/api/auth/passkey/login/complete",
                       json={"credential": assertion})


@pytest.fixture
def enrolled(hosted):
    """A signed-in admin with one registered passkey, and a signed-out client."""
    client, store = hosted
    login(client)
    authenticator = VirtualAuthenticator()
    assert register(client, authenticator, label="yubikey").status_code == 200
    client.cookies.clear()
    return client, store, authenticator


# ---------------------------------------------------------------- happy path

def test_a_registration_round_trip_stores_a_credential(hosted):
    client, store = hosted
    login(client)
    authenticator = VirtualAuthenticator()

    begun = client.post("/api/auth/passkey/register/begin", json={})
    options = begun.json()
    assert options["rp"]["id"] == RP_ID
    assert options["user"]["name"] == ADMIN_HANDLE
    # Discoverable, or the usernameless sign-in below has nothing to offer.
    assert options["authenticatorSelection"]["residentKey"] == "preferred"

    credential = authenticator.create(b64u_decode(options["challenge"]))
    done = client.post("/api/auth/passkey/register/complete",
                       json={"credential": credential})
    assert done.status_code == 200, done.text
    assert done.json()["handle"] == ADMIN_HANDLE
    # A public key is never echoed back by a listing route.
    assert "public_key" not in done.json()

    stored = store.get_passkeys(ADMIN_HANDLE)
    assert len(stored) == 1
    assert stored[0]["id"] == b64u(authenticator.cred_id)
    assert stored[0]["transports"] == ["internal", "hybrid"]
    assert stored[0]["backed_up"] is True


def test_a_usernameless_sign_in_lands_the_same_session_cookie(enrolled):
    """The discoverable-credential flow: nobody types a handle, and what comes
    back is the ordinary session — not a second kind of one."""
    client, _store, authenticator = enrolled

    answer = authenticate(client, authenticator)
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["principal"].startswith(f"user:{ADMIN_HANDLE}")
    assert body["via"] == "passkey"
    assert body["role"] == "admin"

    session = client.get("/api/auth/session")
    assert session.status_code == 200
    assert session.json()["principal"].startswith(f"user:{ADMIN_HANDLE}")
    # Server-side revocation works on it exactly as on a password session.
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/session").status_code == 401


def test_login_begin_never_offers_stored_credentials(enrolled):
    """Even for a member WITH a passkey, `login/begin` returns no
    `allowCredentials` (Lens A #10): a populated list leaks both that the handle
    exists and its credential ids on an anonymous endpoint. The
    discoverable-credential flow needs no hint — the credential resolves its own
    owner by id in `login/complete` — so the sign-in still works with none."""
    client, _store, authenticator = enrolled
    begun = client.post("/api/auth/passkey/login/begin",
                        json={"handle": ADMIN_HANDLE})
    assert begun.json().get("allowCredentials") in (None, [])
    assert authenticate(client, authenticator,
                        handle=ADMIN_HANDLE).status_code == 200


def test_login_begin_is_identical_for_every_handle(enrolled):
    """`allowCredentials` populated for members and empty for strangers is a
    user-enumeration oracle on an anonymous endpoint. All three cases return the
    same (empty) shape — an unknown handle, a member with no passkey, AND a
    member WITH one (Lens A #10). The earlier test compared only the first two,
    which trivially matched and hid the real leak: the member *with* a passkey."""
    client, store, _authenticator = enrolled          # nikita HAS a passkey
    store.enrol(store.add_user("anya"), "hunter2hunter2")   # member, no passkey

    def begin(handle):
        return client.post("/api/auth/passkey/login/begin",
                           json={"handle": handle}).json()

    stranger = begin("nobody-here")
    no_passkey = begin("anya")
    with_passkey = begin(ADMIN_HANDLE)
    for shape in (stranger, no_passkey, with_passkey):
        assert shape.get("allowCredentials") in (None, [])
    assert (with_passkey.get("allowCredentials")
            == no_passkey.get("allowCredentials")
            == stranger.get("allowCredentials"))


def test_the_sign_count_advances_and_is_stored(enrolled):
    client, store, authenticator = enrolled
    assert authenticate(client, authenticator).status_code == 200
    first = store.get_passkeys(ADMIN_HANDLE)[0]["sign_count"]
    client.cookies.clear()
    assert authenticate(client, authenticator).status_code == 200
    assert store.get_passkeys(ADMIN_HANDLE)[0]["sign_count"] > first


def test_a_passkey_can_be_listed_and_revoked(enrolled):
    client, store, authenticator = enrolled
    assert authenticate(client, authenticator).status_code == 200

    listed = client.get("/api/auth/passkeys").json()["passkeys"]
    assert [row["label"] for row in listed] == ["yubikey"]
    # A credential list must not hand out key material it does not need to.
    assert "public_key" not in listed[0]

    removed = client.delete(f"/api/auth/passkeys/{listed[0]['id']}")
    assert removed.status_code == 200 and removed.json()["removed"] is True
    assert store.get_passkeys(ADMIN_HANDLE) == []
    assert client.delete(f"/api/auth/passkeys/{listed[0]['id']}"
                         ).status_code == 404

    # ...and the revoked credential no longer signs anybody in.
    client.cookies.clear()
    assert authenticate(client, authenticator).status_code == 401


# ---------------------------------------------------------------- negatives

def test_an_assertion_from_a_different_origin_is_refused(enrolled):
    """The phishing case: the ceremony happened on a site that is not us."""
    client, _store, authenticator = enrolled
    authenticator.origin = "https://evil.example.com"
    answer = authenticate(client, authenticator)
    assert answer.status_code == 401
    assert answer.json()["error"]["message"] == "sign-in failed"
    assert client.get("/api/auth/session").status_code == 401


def test_an_assertion_for_a_different_relying_party_is_refused(enrolled):
    """The other half of the same protection: the rpIdHash inside authData."""
    client, _store, authenticator = enrolled
    authenticator.rp_id = "evil.example.com"
    assert authenticate(client, authenticator).status_code == 401


def test_a_replayed_assertion_is_refused(enrolled):
    """Challenges are single-use, so a captured assertion is worth nothing."""
    client, _store, authenticator = enrolled
    begun = client.post("/api/auth/passkey/login/begin", json={})
    assertion = authenticator.get(b64u_decode(begun.json()["challenge"]))

    assert client.post("/api/auth/passkey/login/complete",
                       json={"credential": assertion}).status_code == 200
    client.cookies.clear()
    replay = client.post("/api/auth/passkey/login/complete",
                         json={"credential": assertion})
    assert replay.status_code == 401
    assert client.get("/api/auth/session").status_code == 401


def test_a_challenge_the_server_never_minted_is_refused(enrolled):
    client, _store, authenticator = enrolled
    stale = authenticator.get(os.urandom(32))
    assert client.post("/api/auth/passkey/login/complete",
                       json={"credential": stale}).status_code == 401


def test_a_sign_count_regression_is_refused(enrolled):
    """A cloned authenticator: the same key, a counter that went backwards."""
    client, store, authenticator = enrolled
    authenticator.sign_count = 50
    assert authenticate(client, authenticator).status_code == 200
    assert store.get_passkeys(ADMIN_HANDLE)[0]["sign_count"] == 51

    client.cookies.clear()
    clone = VirtualAuthenticator()
    clone.key, clone.cred_id = authenticator.key, authenticator.cred_id
    clone.sign_count = 0                       # the clone starts from zero
    answer = authenticate(client, clone)
    assert answer.status_code == 401
    # The stored counter did not move backwards either.
    assert store.get_passkeys(ADMIN_HANDLE)[0]["sign_count"] == 51


def test_a_tampered_signature_is_refused(enrolled):
    client, _store, authenticator = enrolled
    begun = client.post("/api/auth/passkey/login/begin", json={})
    assertion = authenticator.get(b64u_decode(begun.json()["challenge"]))
    raw = b64u_decode(assertion["response"]["signature"])
    assertion["response"]["signature"] = b64u(raw[:-1] + bytes([raw[-1] ^ 0xFF]))
    answer = client.post("/api/auth/passkey/login/complete",
                         json={"credential": assertion})
    assert answer.status_code == 401


def test_an_unknown_credential_signs_nobody_in(enrolled):
    """A perfectly valid ceremony from an authenticator this instance has
    never seen."""
    client, _store, _authenticator = enrolled
    stranger = VirtualAuthenticator()
    assert authenticate(client, stranger).status_code == 401


def test_a_disabled_account_cannot_sign_in_with_its_passkey(enrolled):
    """`admin user disable` closes every door — the user row is the
    authority, exactly as it is for a password."""
    client, store, authenticator = enrolled
    store.disable_user(ADMIN_HANDLE)
    assert authenticate(client, authenticator).status_code == 401


def test_the_same_credential_cannot_be_registered_twice(hosted):
    client, _store = hosted
    login(client)
    authenticator = VirtualAuthenticator()
    assert register(client, authenticator).status_code == 200
    assert register(client, authenticator).status_code == 409


def test_registration_needs_a_session(hosted):
    """Anonymous registration would be "add a credential to somebody's
    account" — the exact reason the register routes are NOT public."""
    client, _store = hosted
    for path in ("/api/auth/passkey/register/begin",
                 "/api/auth/passkey/register/complete"):
        answer = client.post(path, json={})
        assert answer.status_code == 401, path
        assert answer.json()["error"]["type"] == "AuthError"


def test_a_bearer_token_cannot_register_a_passkey(hosted):
    """Decision 14's shape: a token drives the product, a human manages
    credentials."""
    client, store = hosted
    token = store.add_token("ci", role="admin")
    client.cookies.clear()
    answer = client.post("/api/auth/passkey/register/begin", json={},
                         headers={"Authorization": f"Bearer {token}"})
    assert answer.status_code == 403
    assert answer.json()["error"]["type"] == "AuthzError"


def test_a_credential_signs_in_its_own_owner_whatever_handle_was_named(hosted):
    """A credential resolves to **the account that registered it**, never to
    the handle the request suggested: the id is the identity, and the `handle`
    field of `login/begin` is a hint for the browser's picker."""
    client, store = hosted
    store.enrol(store.add_user("anya"), "hunter2hunter2")
    login(client, "anya", "hunter2hunter2")
    authenticator = VirtualAuthenticator(user_handle=b"anya")
    assert register(client, authenticator).status_code == 200
    client.cookies.clear()

    begun = client.post("/api/auth/passkey/login/begin",
                        json={"handle": ADMIN_HANDLE})   # nikita has no passkey
    assertion = authenticator.get(b64u_decode(begun.json()["challenge"]))
    answer = client.post("/api/auth/passkey/login/complete",
                         json={"credential": assertion})
    assert answer.status_code == 200
    assert answer.json()["principal"].startswith("user:anya")
    assert answer.json()["role"] == "member"        # NOT the admin's role


def test_the_named_handle_neither_binds_nor_leaks(enrolled):
    """`login/begin` naming a handle that really has a passkey (nikita) returns
    no `allowCredentials` (Lens A #10) AND does not bind the challenge to that
    handle: a *different* account's credential completes it and signs in ITS OWN
    owner. Binding is gone on purpose — the credential id is the identity, so the
    named handle is only an (inert) browser-picker hint. This replaces the old
    handle-first binding: with no `allowCredentials` there is nothing to bind,
    and nothing to enumerate."""
    client, store, _nikita = enrolled                 # nikita has a passkey
    store.enrol(store.add_user("anya"), "hunter2hunter2")
    login(client, "anya", "hunter2hunter2")
    anya = VirtualAuthenticator(user_handle=b"anya")
    assert register(client, anya).status_code == 200
    client.cookies.clear()

    begun = client.post("/api/auth/passkey/login/begin",
                        json={"handle": ADMIN_HANDLE})   # nikita really has one
    assert begun.json().get("allowCredentials") in (None, [])   # no leak
    assertion = anya.get(b64u_decode(begun.json()["challenge"]))
    answer = client.post("/api/auth/passkey/login/complete",
                         json={"credential": assertion})
    assert answer.status_code == 200                    # anya's own credential
    assert answer.json()["principal"].startswith("user:anya")


# ------------------------------------------------------------- extra gating

def test_the_passkey_routes_answer_501_without_the_extra(hosted, monkeypatch):
    """The `[fem]` precedent: without `agentcad[cloud]` the ceremonies answer
    501 and say what to install. Local password accounts are unaffected —
    asserted below in the same test, because "SSO is optional" is the claim."""
    from agentcad.server import routes_auth

    client, _store = hosted
    login(client)
    monkeypatch.setattr(routes_auth, "passkeys_available", lambda: False)

    for path in ("/api/auth/passkey/register/begin",
                 "/api/auth/passkey/register/complete",
                 "/api/auth/passkey/login/begin",
                 "/api/auth/passkey/login/complete"):
        answer = client.post(path, json={})
        assert answer.status_code == 501, path
        assert answer.json()["error"]["type"] == "PasskeysUnavailable"
        assert "agentcad[cloud]" in answer.json()["error"]["message"]

    # The read/revoke routes are pure `users.json` and keep working, so an
    # instance that dropped the extra can still revoke what it registered.
    assert client.get("/api/auth/passkeys").status_code == 200

    client.cookies.clear()
    assert client.post("/api/auth/login",
                       json={"handle": ADMIN_HANDLE,
                             "password": ADMIN_PASSWORD}).status_code == 200


def test_the_availability_check_does_not_import_webauthn(monkeypatch):
    """~105 ms of interpreter start-up (spike B1), which is why this is
    `find_spec` and the import lives inside the handlers."""
    import importlib.util

    from agentcad.server import routes_auth

    seen = {}
    real = importlib.util.find_spec

    def watched(name, *args, **kwargs):
        seen["name"] = name
        return real(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", watched)
    assert routes_auth.passkeys_available() is True
    assert seen["name"] == "webauthn"


# ------------------------------------------------ DoS / oracle hardening

def test_a_passkey_without_user_verification_is_refused(enrolled):
    """The 2FA ruling (Lens A #11): a passkey here is possession AND user
    verification, so `login/begin` asks for UV and `login/complete` enforces it.
    An assertion that clears the UV flag — a stolen roaming key with no PIN — is
    refused even though its signature is otherwise valid."""
    client, _store, authenticator = enrolled
    original = authenticator._auth_data

    def no_uv(*, attested):
        data = bytearray(original(attested=attested))
        data[32] &= ~FLAG_UV                 # clear "user verified" in the flags
        return bytes(data)

    authenticator._auth_data = no_uv
    answer = authenticate(client, authenticator)
    assert answer.status_code == 401
    assert answer.json()["error"]["message"] == "sign-in failed"


def test_passkey_login_complete_is_rate_limited(enrolled):
    """`login/complete` was unthrottled — 200 anonymous posts minted 200 durable
    audit rows for 200 401s (Lens A #12). It now spends the same per-address
    bucket the password door and `login/begin` do, charged BEFORE any work, so a
    flood is bounded to the burst and then 429s (and the throttled attempts, cut
    before `_refused`, write no audit row)."""
    client, _store, _auth = enrolled
    junk = {"id": "AA", "rawId": "AA", "type": "public-key",
            "response": {"clientDataJSON": "e30",          # b64u of "{}"
                         "authenticatorData": "AA", "signature": "AA"}}
    statuses = [client.post("/api/auth/passkey/login/complete",
                            json={"credential": junk}).status_code
                for _ in range(25)]
    assert 429 in statuses, statuses          # the flood is throttled
    assert 200 not in statuses                # and never a session
    assert set(statuses) <= {401, 429}


def test_challenge_store_is_single_use_ttld_and_capped():
    """`_ChallengeStore` on its own (Lens A #13's other half): single-use, TTL'd
    on the MONOTONIC clock, and size-capped by dropping the oldest quarter."""
    from agentcad.server.routes_auth import _ChallengeStore

    store = _ChallengeStore(ttl_s=0.05, cap=4)
    store.put("k", {"purpose": "login"})
    assert store.take("k")["purpose"] == "login"      # present
    assert store.take("k") is None                    # single use

    store.put("slow", {"purpose": "login"})
    time.sleep(0.06)
    assert store.take("slow") is None                 # expired by TTL

    for i in range(4):
        store.put(f"c{i}", {"purpose": "login"})
    store.put("c4", {"purpose": "login"})             # over cap: drops the oldest
    assert store.take("c0") is None
    assert store.take("c4") is not None


@pytest.fixture
def small_cap_hosted(kernel, tmp_path, monkeypatch):
    """A hosted app whose passkey challenge stores hold only 3 entries.

    Constructed here rather than via the `hosted` fixture because the cap is a
    default argument bound at import — the only way to shrink it is to replace
    the class before `build_router` constructs the two stores."""
    from fastapi.testclient import TestClient

    from agentcad.core.appmode import AppMode
    from agentcad.core.authstore import AuthStore
    from agentcad.core.tools import build_registry
    from agentcad.server import routes_auth
    from agentcad.server import security as security_module
    from agentcad.server.app import create_app
    from agentcad.server.security import SecurityConfig

    class _SmallStore(routes_auth._ChallengeStore):
        def __init__(self, ttl_s=routes_auth.PASSKEY_CHALLENGE_TTL_S, cap=3):
            super().__init__(ttl_s=ttl_s, cap=cap)

    monkeypatch.setattr(routes_auth, "_ChallengeStore", _SmallStore)
    monkeypatch.setenv("AGENTCAD_CONFIG", str(tmp_path / "cfg" / "config.json"))

    from .conftest import make_test_service
    service = make_test_service(tmp_path / "projects", kernel)
    store = AuthStore(tmp_path / "auth")
    store.enrol(store.add_user(ADMIN_HANDLE, role="admin"), ADMIN_PASSWORD)
    cfg = SecurityConfig(mode=AppMode("hosted", HOSTED_ORIGIN, b"k" * 32),
                         store=store)
    security_module.install(cfg)
    app = create_app(service, build_registry(service),
                     extra_allowed_hosts={"testserver"}, security=cfg)
    client = TestClient(app, base_url=HOSTED_ORIGIN)
    client.agentcad_store = store
    try:
        yield client, store
    finally:
        security_module.install(None)


def test_a_register_flood_cannot_evict_a_login_challenge(small_cap_hosted):
    """The store split (Lens A #13): a signed-in member spamming
    `register/begin` fills only the REGISTER store. A login challenge minted
    meanwhile survives in the separate LOGIN store and still signs in — where a
    single shared, register-floodable store would have evicted it (the cap is 3
    here, so 5 register begins would overflow one shared store)."""
    client, _store = small_cap_hosted
    login(client)
    authenticator = VirtualAuthenticator()
    assert register(client, authenticator).status_code == 200
    client.cookies.clear()

    # An anonymous login challenge, then a flood of register ceremonies as the
    # signed-in admin, then complete the ORIGINAL login.
    begun = client.post("/api/auth/passkey/login/begin", json={})
    challenge = begun.json()["challenge"]

    login(client)
    for _ in range(5):                        # > cap: overflows one shared store
        assert client.post("/api/auth/passkey/register/begin",
                           json={}).status_code == 200
    client.cookies.clear()

    assertion = authenticator.get(b64u_decode(challenge))
    answer = client.post("/api/auth/passkey/login/complete",
                         json={"credential": assertion})
    assert answer.status_code == 200, answer.text     # the login challenge lived
    assert answer.json()["via"] == "passkey"


# -------------------------------------------------- the store, on its own

def test_the_user_row_is_schema_tolerant(hosted):
    """A `users.json` written before PRD-005 has neither field, and that is the
    ordinary case on every upgraded instance — never an exception."""
    _client, store = hosted
    assert store.get_passkeys(ADMIN_HANDLE) == []
    assert store.find_oidc(ADMIN_HANDLE) is None
    assert store.find_by_passkey("not-a-credential") is None
    assert store.find_by_oidc("https://idp.test", "nobody") is None
    # ...and junk in the fields reads as "nothing there", not as a 500.
    store._set_user_field(ADMIN_HANDLE, "passkeys", "not-a-list")
    store._set_user_field(ADMIN_HANDLE, "oidc", ["not", "an", "object"])
    assert store.get_passkeys(ADMIN_HANDLE) == []
    assert store.find_oidc(ADMIN_HANDLE) is None


def test_a_passkey_never_reshapes_the_existing_row(hosted):
    """AC7's storage half: registering a credential adds a key and touches
    nothing else."""
    import json as _json

    _client, store = hosted
    before = _json.loads((store.root / "users.json").read_text())[ADMIN_HANDLE]
    store.add_passkey(ADMIN_HANDLE, credential_id="abc", public_key="def",
                      sign_count=3, label="x")
    after = _json.loads((store.root / "users.json").read_text())[ADMIN_HANDLE]
    assert {k: v for k, v in after.items() if k != "passkeys"} == before
    assert store.find_by_passkey("abc")["handle"] == ADMIN_HANDLE
