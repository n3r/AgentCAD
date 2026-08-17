"""PRD-005a slice 3: enrolment, login, sessions and the admin routes.

FR5-FR8 and AC3-AC5. The adversarial half is the point: an expired session
accepted, a logout that does not revoke, a timing or body difference between
"no such handle" and "wrong password", a member reaching an admin route, a
digest in a response.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from .conftest import ADMIN_HANDLE, ADMIN_PASSWORD, HOSTED_ORIGIN

ORIGIN = HOSTED_ORIGIN
GOOD = {"handle": "nikita", "password": ADMIN_PASSWORD}


def _fresh(client):
    """A second browser against the same app — no cookies carried over."""
    return TestClient(client.app, base_url=ORIGIN)


# ------------------------------------------------------------------- login


def test_login_sets_a_session_cookie_and_session_reads_back(hosted):
    client, _ = hosted
    r = client.post("/api/auth/login", json=GOOD)
    assert r.status_code == 200
    assert "agentcad_session" in r.cookies
    assert client.get("/api/auth/session").json() == {
        "principal": "user:nikita", "kind": "user",
        "role": "admin", "mode": "hosted"}


def test_the_session_cookie_is_httponly_lax_and_scoped_to_the_site(hosted):
    """`HttpOnly` keeps XSS from reading it; `SameSite=Lax` is the first CSRF
    layer (the Origin check is the second); `Path=/` because the whole API is
    behind it."""
    client, _ = hosted
    header = client.post("/api/auth/login", json=GOOD).headers["set-cookie"]
    assert "HttpOnly" in header
    # RFC 6265bis makes the attribute value case-insensitive; Starlette emits
    # it lowercase.
    assert "samesite=lax" in header.lower()
    assert "Path=/" in header
    # http:// origin in the test fixture, so no Secure — a Secure cookie on a
    # plain-http staging box is never sent back, which reads as "login does
    # nothing". The https case is covered in test_appmode.
    assert "Secure" not in header


def test_logout_revokes_immediately(hosted):
    client, _ = hosted
    client.post("/api/auth/login", json=GOOD)
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/session").status_code == 401


def test_logout_revokes_server_side_not_just_in_the_browser(hosted):
    """The negation: clearing the cookie and calling it revocation. A stolen
    cookie replayed after logout must be dead, which is why the store is the
    authority and these are not JWTs."""
    client, _ = hosted
    stolen = client.post("/api/auth/login", json=GOOD).cookies["agentcad_session"]
    client.post("/api/auth/logout")

    attacker = _fresh(client)
    attacker.cookies.set("agentcad_session", stolen)
    assert attacker.get("/api/auth/session").status_code == 401


def test_logout_of_one_browser_leaves_the_other_signed_in(hosted):
    client, _ = hosted
    other = _fresh(client)
    client.post("/api/auth/login", json=GOOD)
    other.post("/api/auth/login", json=GOOD)
    client.post("/api/auth/logout")
    assert client.get("/api/auth/session").status_code == 401
    assert other.get("/api/auth/session").status_code == 200


def test_logout_without_a_session_is_401_rather_than_a_500(hosted):
    """Logout is NOT on the public allowlist — the anonymous surface is
    exactly nine entries and logout is not one of them — so an already
    signed-out client gets the ordinary 401. What matters is that it is a
    clean refusal and not a traceback: the frontend treats 401 on logout as
    "already signed out"."""
    client, _ = hosted
    r = client.post("/api/auth/logout")
    assert r.status_code == 401
    assert r.json()["error"]["type"] == "AuthError"


def test_session_is_401_before_login(hosted):
    client, _ = hosted
    assert client.get("/api/auth/session").status_code == 401


def test_an_expired_session_is_not_accepted(hosted, monkeypatch):
    from agentcad.core import authstore as authstore_mod

    client, _ = hosted
    client.post("/api/auth/login", json=GOOD)
    assert client.get("/api/auth/session").status_code == 200
    monkeypatch.setattr(
        authstore_mod, "_now",
        lambda: time.time() + authstore_mod.ABSOLUTE_SESSION_S + 60)
    assert client.get("/api/auth/session").status_code == 401


def test_a_disabled_account_cannot_log_in(hosted):
    client, store = hosted
    store.disable_user("nikita")
    assert client.post("/api/auth/login", json=GOOD).status_code == 401


def test_an_unenrolled_account_cannot_log_in(hosted):
    client, store = hosted
    store.add_user("anya")                      # created disabled, no password
    r = client.post("/api/auth/login",
                    json={"handle": "anya", "password": "hunter2hunter2"})
    assert r.status_code == 401


# ------------------------------------------------- indistinguishability


def test_unknown_handle_and_wrong_password_are_indistinguishable(hosted):
    client, _ = hosted
    a = client.post("/api/auth/login", json={"handle": "nikita", "password": "no"})
    b = client.post("/api/auth/login", json={"handle": "ghost", "password": "no"})
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


def test_the_two_failures_also_take_comparable_time(hosted):
    """AC5's other half. A cheap unknown-handle path is a user-enumeration
    oracle even when the bodies match; `min` of several runs is the stable
    statistic on a parallel test host."""
    client, _ = hosted

    def best(handle: str) -> float:
        samples = []
        for _ in range(3):
            # A fresh client each time: the rate limiter must not be what we
            # end up measuring.
            fresh = _fresh(client)
            start = time.perf_counter()
            assert fresh.post("/api/auth/login",
                              json={"handle": handle,
                                    "password": "wrong password"}
                              ).status_code in (401, 429)
            samples.append(time.perf_counter() - start)
        return min(samples)

    known, unknown = best("nikita"), best("ghost")
    assert 0.4 < unknown / known < 2.5, (known, unknown)


@pytest.mark.parametrize("body", [
    {}, {"handle": "nikita"}, {"password": "x"},
    {"handle": None, "password": None},
    {"handle": ["nikita"], "password": {"a": 1}},
    {"handle": "n" * 5000, "password": "x" * 5000},
])
def test_a_malformed_login_body_is_refused_without_a_traceback(hosted, body):
    client, _ = hosted
    r = client.post("/api/auth/login", json=body)
    assert r.status_code in (401, 422), r.text
    assert "Traceback" not in r.text


# ------------------------------------------------------------ rate limiting


def test_login_is_rate_limited_with_retry_after(hosted):
    client, _ = hosted
    codes = [client.post("/api/auth/login",
                         json={"handle": "nikita", "password": "no"}).status_code
             for _ in range(25)]
    assert 429 in codes
    r = client.post("/api/auth/login", json={"handle": "nikita", "password": "no"})
    assert r.json()["error"]["details"]["retry_after_s"] > 0
    assert r.json()["error"]["type"] == "RateLimitedError"


def test_the_rate_limit_is_taken_before_the_password_is_hashed(hosted,
                                                               monkeypatch):
    """A flood must not buy 63 ms of scrypt per request — the limiter is the
    cheap door, so it goes first.

    **Counted, not timed, and that is the whole lesson of this test's
    history.** It was `elapsed < 0.03`, which is a claim about the *machine*:
    it passes standalone in 3.6 s and failed at 0.083 s in an 8-way co-loaded
    suite. Rewriting it as a ratio against the same run's own scrypt failed
    too, at 0.067 s throttled against 0.076 s hashing — on a loaded box the
    ASGI round trip alone can cost more than the KDF, so **no** wall-clock
    formulation of this property is stable (the `test_sketch_bench` /
    `test_sketch_drag` flake class, changelogs 0186 and 0195).

    The property is structural — "the throttled path never reaches the KDF" —
    so it is asserted structurally, by counting `hashlib.scrypt` calls. That
    is deterministic under any load, and strictly stronger: a wall clock could
    be fooled by a fast machine, a counter cannot.
    """
    import hashlib

    client, _ = hosted
    body = {"handle": "nikita", "password": "no"}
    calls: list[int] = []
    real_scrypt = hashlib.scrypt

    def counting(*args, **kwargs):
        calls.append(1)
        return real_scrypt(*args, **kwargs)

    monkeypatch.setattr(hashlib, "scrypt", counting)

    # A wrong password inside the burst: a 401 that DOES pay the KDF. Without
    # this half, "zero hashes" would also pass if login stopped hashing at all.
    first = client.post("/api/auth/login", json=body)
    assert first.status_code == 401, first.text
    assert calls, "a wrong password did not reach scrypt at all"

    for _ in range(6):
        client.post("/api/auth/login", json=body)
    before = len(calls)
    throttled = client.post("/api/auth/login", json=body)

    assert throttled.status_code == 429
    assert len(calls) == before, (
        f"a throttled login still called scrypt {len(calls) - before} time(s) "
        "— the limiter is not in front of the KDF")


def test_a_throttled_handle_does_not_lock_out_a_different_one(hosted):
    """Per-handle buckets, or one attacker denies service to everybody."""
    client, store = hosted
    store.enrol(store.add_user("anya"), "hunter2hunter2")
    for _ in range(8):
        client.post("/api/auth/login", json={"handle": "nikita", "password": "no"})
    r = client.post("/api/auth/login",
                    json={"handle": "anya", "password": "hunter2hunter2"})
    assert r.status_code == 200


def test_a_third_party_cannot_lock_a_known_handle_out(hosted):
    """Review finding M3: the per-attempt bucket is keyed on `(handle,
    address)`, so a stranger cannot deny the account's owner.

    Keyed on the handle alone this was a **permanent** lockout primitive:
    `TokenBucket.take` does not consume on refusal, so anyone willing to spend
    0.5 req/s against a handle held its bucket empty forever, and handles are
    public — presence rosters, comment authors and history trailers all carry
    them. Measured before the fix: the victim, with the correct password, was
    refused in 6 of 6 rounds over 12 s.

    Both halves are asserted. The attacker must still be throttled, or the fix
    would be "delete the limit".
    """
    app = hosted[0].app
    attacker = TestClient(app, base_url=HOSTED_ORIGIN, client=("203.0.113.7", 4444))
    victim = TestClient(app, base_url=HOSTED_ORIGIN, client=("198.51.100.9", 4444))

    guesses = [attacker.post("/api/auth/login",
                             json={"handle": ADMIN_HANDLE, "password": "no"}
                             ).status_code for _ in range(10)]
    assert 429 in guesses, "the attacker's own address is not throttled"
    assert victim.post("/api/auth/login", json=GOOD).status_code == 200


# --- M3 round 2: the address behind the recommended reverse proxy -------------
#
# `request.client.host` is the RAW socket peer. Behind the nginx/Caddy proxy the
# deployment guide prescribes, that peer is the proxy for every internet client,
# so `(handle, address)` collapses back to per-handle and the round-1 lockout
# reopens. The fix is uvicorn's `ProxyHeadersMiddleware`, bounded to the trusted
# proxy, resolving the real client from `X-Forwarded-For` — which
# `cli._uvicorn_proxy_kwargs` turns on in hosted mode. These tests wrap the app
# in that exact middleware so the simulation is the real thing, not a mock.

PROXY = "127.0.0.1"


def _behind(app_or_client, trusted=PROXY):
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    app = getattr(app_or_client, "app", app_or_client)
    return ProxyHeadersMiddleware(app, trusted_hosts=trusted)


def _client(app, peer, xff=None):
    c = TestClient(app, base_url=HOSTED_ORIGIN, client=(peer, 5555))
    if xff is not None:
        c.headers.update({"X-Forwarded-For": xff})
    return c


def test_without_forwarded_for_the_proxy_collapses_the_bucket(hosted):
    """The regression this whole round exists for. Behind the proxy with **no**
    `X-Forwarded-For` — the docs as they were — every client is `127.0.0.1`, so
    an attacker locks the victim out. This asserts the collapse so the doc
    requirement to forward the header has a test behind it."""
    proxied = _behind(hosted[0])
    attacker = _client(proxied, PROXY)          # no XFF: arrives as the proxy
    victim = _client(proxied, PROXY)
    for _ in range(9):
        attacker.post("/api/auth/login", json={"handle": ADMIN_HANDLE, "password": "no"})
    assert victim.post("/api/auth/login", json=GOOD).status_code == 429


def test_behind_a_proxy_the_bucket_keys_on_the_real_client(hosted):
    """The fix. With the proxy forwarding `X-Forwarded-For`, two clients at the
    same socket peer (the proxy) get DIFFERENT buckets — the attacker's flood
    does not touch the victim, who signs in with the correct password."""
    proxied = _behind(hosted[0])
    attacker = _client(proxied, PROXY, xff="203.0.113.7")
    victim = _client(proxied, PROXY, xff="198.51.100.9")
    guesses = [attacker.post("/api/auth/login",
                             json={"handle": ADMIN_HANDLE, "password": "no"}
                             ).status_code for _ in range(10)]
    assert 429 in guesses, "the attacker's real address is not throttled"
    assert victim.post("/api/auth/login", json=GOOD).status_code == 200


def test_a_client_cannot_forge_its_forwarded_address_across_the_proxy(hosted):
    """The spoof guard. nginx APPENDS the real peer, so the trusted header is
    `<forged>, <real-ip>`; uvicorn takes the rightmost untrusted hop, which is
    the real attacker. Forging the victim's address to frame them does not put
    the attacker in the victim's bucket."""
    proxied = _behind(hosted[0])
    # The attacker prepends the victim's address; the proxy appends the real one.
    attacker = _client(proxied, PROXY, xff="198.51.100.9, 203.0.113.7")
    victim = _client(proxied, PROXY, xff="198.51.100.9")
    for _ in range(9):
        attacker.post("/api/auth/login", json={"handle": ADMIN_HANDLE, "password": "no"})
    assert victim.post("/api/auth/login", json=GOOD).status_code == 200


def test_direct_bind_ignores_a_clients_x_forwarded_for(hosted):
    """The direct-bind spoof guard. With no proxy middleware (local/direct mode
    runs uvicorn with `proxy_headers` off), a client's own `X-Forwarded-For` is
    ignored and the address is the socket peer — so an attacker cannot forge the
    victim's address to lock them out."""
    app = hosted[0].app                          # raw app, no proxy middleware
    attacker = _client(app, "203.0.113.7", xff="198.51.100.9")   # forging
    victim = _client(app, "198.51.100.9")
    for _ in range(9):
        attacker.post("/api/auth/login", json={"handle": ADMIN_HANDLE, "password": "no"})
    assert victim.post("/api/auth/login", json=GOOD).status_code == 200


def test_the_correct_password_still_wins_after_a_throttle_lapses(hosted, monkeypatch):
    from agentcad.server import security as security_mod

    client, _ = hosted
    for _ in range(8):
        client.post("/api/auth/login", json={"handle": "nikita", "password": "no"})
    assert client.post("/api/auth/login", json=GOOD).status_code == 429
    # Move the bucket's clock rather than sleeping five seconds.
    bucket = client.agentcad_config.login_rate
    monkeypatch.setattr(bucket, "_clock",
                        lambda: time.time() + 10 * security_mod.LOGIN_BURST
                        / security_mod.LOGIN_RATE_PER_S)
    assert client.post("/api/auth/login", json=GOOD).status_code == 200


# ---------------------------------------------------------------- enrolment


def test_enrolment_is_public_single_use_and_signs_you_in(hosted):
    client, store = hosted
    token = store.add_user("anya")
    r = client.post(f"/api/auth/enrol/{token}", json={"password": "hunter2hunter2"})
    assert r.status_code == 200
    assert client.get("/api/auth/session").json()["principal"] == "user:anya"
    fresh = _fresh(client)
    assert fresh.post(f"/api/auth/enrol/{token}",
                      json={"password": "again"}).status_code == 404


def test_a_recovery_enrolment_kills_the_sessions_it_is_recovering_from(hosted):
    """Review finding M4, through the route.

    The recovery path is `agentcad admin enrol <handle>` for an account that
    already has a password — run precisely when one was stolen. The attacker's
    cookie must die with the password, and the person doing the recovery must
    end up signed in.
    """
    client, store = hosted
    store.enrol(store.add_user("anya"), "hunter2hunter2")
    attacker = _fresh(client)
    attacker.cookies.set("agentcad_session",
                         store.create_session("anya", device=None))
    assert attacker.get("/api/auth/session").status_code == 200

    recovery = _fresh(client)
    r = recovery.post(f"/api/auth/enrol/{store.mint_enrolment('anya')}",
                      json={"password": "a brand new password"})
    assert r.status_code == 200

    assert attacker.get("/api/auth/session").status_code == 401
    assert recovery.get("/api/auth/session").json()["principal"] == "user:anya"


def test_enrolment_needs_no_credential(hosted):
    """FR14: the enrol prefix is public, or an invitee could never use it."""
    client, store = hosted
    token = store.add_user("anya")
    assert client.get(f"/api/auth/enrol/{token}").json()["handle"] == "anya"


@pytest.mark.parametrize("token", ["", "x", "not-a-token", "../../etc/passwd",
                                   "a" * 500])
def test_a_bad_enrolment_token_is_404_and_says_nothing_else(hosted, token):
    client, _ = hosted
    r = client.get(f"/api/auth/enrol/{token}")
    assert r.status_code in (401, 404)
    assert "anya" not in r.text and "nikita" not in r.text


def test_an_expired_enrolment_token_is_refused(hosted, monkeypatch):
    from agentcad.core import authstore as authstore_mod

    client, store = hosted
    token = store.add_user("anya")
    monkeypatch.setattr(authstore_mod, "_now",
                        lambda: time.time() + authstore_mod.ENROL_TTL_S + 60)
    assert client.post(f"/api/auth/enrol/{token}",
                       json={"password": "hunter2hunter2"}).status_code == 404


def test_a_short_password_is_refused_at_enrolment(hosted):
    client, store = hosted
    token = store.add_user("anya")
    r = client.post(f"/api/auth/enrol/{token}", json={"password": "short"})
    assert r.status_code == 422
    # ...and the token is NOT spent by a refused attempt.
    assert client.post(f"/api/auth/enrol/{token}",
                       json={"password": "hunter2hunter2"}).status_code == 200


def test_a_browser_opening_the_enrol_url_gets_the_app_not_json(hosted):
    """The admin CLI prints this URL and a human pastes it into a browser. A
    page of JSON would be the end of that flow, so the same path serves
    `index.html` to a client that asks for HTML and JSON to everything else."""
    client, store = hosted
    token = store.add_user("anya")
    r = client.get(f"/api/auth/enrol/{token}",
                   headers={"Accept": "text/html,application/xhtml+xml"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "<html" in r.text.lower()


# ------------------------------------------------------------ admin routes


def _enrol_member(client, store, handle="anya"):
    token = store.add_user(handle)
    client.post(f"/api/auth/enrol/{token}", json={"password": "hunter2hunter2"})
    return handle


def test_member_is_403_on_admin_routes(hosted):
    client, store = hosted
    _enrol_member(client, store)                  # role="member"
    r = client.post("/api/auth/users", json={"handle": "mallory"})
    assert r.status_code == 403
    assert r.json()["error"]["type"] == "AuthzError"


def test_a_member_cannot_even_list_users(hosted):
    client, store = hosted
    _enrol_member(client, store)
    assert client.get("/api/auth/users").status_code == 403


def test_an_admin_can_mint_an_account_and_gets_an_enrol_url(hosted):
    client, store = hosted
    client.post("/api/auth/login", json=GOOD)
    r = client.post("/api/auth/users", json={"handle": "anya"})
    assert r.status_code == 201, r.text
    url = r.json()["enrol_url"]
    assert url.startswith(f"{ORIGIN}/api/auth/enrol/")
    fresh = _fresh(client)
    token = url.rsplit("/", 1)[1]
    assert fresh.post(f"/api/auth/enrol/{token}",
                      json={"password": "hunter2hunter2"}).status_code == 200


def test_an_admin_route_refuses_a_bearer_even_with_the_admin_role(hosted):
    """Minting credentials from the same authenticated HTTP surface those
    credentials unlock is a privilege-escalation shape (design Decision 14).
    A token can drive the product; only a signed-in person manages accounts."""
    client, store = hosted
    bearer = store.add_token("ci", role="admin")
    r = client.get("/api/auth/users",
                   headers={"Authorization": f"Bearer {bearer}"})
    assert r.status_code == 403
    assert r.json()["error"]["type"] == "AuthzError"


def test_a_bad_handle_is_refused_by_the_admin_route(hosted):
    client, _ = hosted
    client.post("/api/auth/login", json=GOOD)
    assert client.post("/api/auth/users",
                       json={"handle": "Nikita"}).status_code == 422


def test_creating_an_existing_handle_is_a_conflict_not_a_reset(hosted):
    client, _ = hosted
    client.post("/api/auth/login", json=GOOD)
    assert client.post("/api/auth/users",
                       json={"handle": "nikita"}).status_code == 409
    # ...and the incumbent's password still works.
    assert _fresh(client).post("/api/auth/login", json=GOOD).status_code == 200


def test_no_route_ever_returns_a_digest(hosted):
    client, _ = hosted
    client.post("/api/auth/login", json=GOOD)
    body = client.get("/api/auth/users").text
    assert "digest" not in body and "salt" not in body
    assert ADMIN_PASSWORD not in body
    assert "scrypt" not in body


def test_no_auth_response_ever_echoes_the_password(hosted):
    client, _ = hosted
    for response in (client.post("/api/auth/login", json=GOOD),
                     client.post("/api/auth/login",
                                 json={"handle": "nikita", "password": "wrong-xyzzy"})):
        assert ADMIN_PASSWORD not in response.text
        assert "wrong-xyzzy" not in response.text
