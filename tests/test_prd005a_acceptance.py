"""PRD-005a acceptance — hosted core, AC1–AC11.

One test per criterion, each naming it in its docstring. The criteria are
graded against the real guard, the real store, the shipped catalog and the
shipped deployment artefacts — never against a stub built for the occasion.

Three of them need a note before you read them:

* **AC3 is partly a browser session, and a test cannot be one.** Its
  *mechanism* half — single-use enrolment, a second member receiving the edit
  over the WebSocket, and `user:<handle>` reaching claims, presence, comment
  authorship and history — is driven here for real. Its *visual* half (the
  sign-in view, the identity chip, a lock chip during an edit) was **never
  rendered by a browser**: three sessions found no connected Chrome
  (`list_connected_browsers` → `[]`). What is gradeable is the evidence — the
  frontend module exists and calls the routes it claims to, and the changelogs
  record what was and was not driven. `test_ac3_the_browser_half_is_recorded_
  as_unverified` asserts the record says so, which is the opposite of a pass
  and is deliberately worded that way.
* **AC8 is a container**, so the suite grades the artefacts (which
  `tests/test_deploy_config.py` does in depth, with no Docker daemon) plus the
  recorded run. The `docker compose` runs themselves are in changelogs 0194
  and 0197.
* **AC9 is "nothing changed"**, which no single test can assert. What is
  asserted here is the *shape* of the claim: the local path is the same code
  path (`security=None` short-circuits before anything hosted exists), the
  hosted route packs are inert without a config, and an MCP registration with
  no `AGENTCAD_TOKEN` sends exactly the headers it sent before. The full-suite
  count is the evidence, and `test_ac9_the_full_suite_count_is_cited` checks it
  is on the record.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import sys
import time
from unittest import mock
from pathlib import Path

import pytest

from agentcad.core import appmode, locks
from agentcad.core.appmode import ModeError
from agentcad.server import security

from .conftest import (ADMIN_HANDLE, ADMIN_PASSWORD, BOX_SCRIPT,
                       flatten_routes, login, make_test_service)

REPO = Path(__file__).resolve().parents[1]
CHANGELOG = REPO / "docs" / "changelog"
FRONTEND = REPO / "frontend"
PRD_NAME = "PRD-005a-hosted-core.md"


def _find_prd() -> Path:
    """Locate the PRD wherever it currently lives.

    A PRD moves from `in-progress/` to `completed/` at **merge**, not when the
    build finishes, so a test that hard-codes one directory is red for the
    whole review window. PRD-010's close-out hit exactly that (changelog
    0164); this is its fix, copied from `test_prd011_acceptance.py`.
    """
    prd_root = REPO / "docs" / "prd"
    for stage in ("in-progress", "completed", "pending"):
        candidate = prd_root / stage / PRD_NAME
        if candidate.is_file():
            return candidate
    found = sorted(prd_root.rglob(PRD_NAME))
    assert found, f"{PRD_NAME} is not anywhere under {prd_root}"
    return found[0]


PRD = _find_prd()


def _second_member(store, handle="anya", password="another good password"):
    store.enrol(store.add_user(handle), password)
    return handle, password


def _client_for(app, base="http://testserver"):
    from fastapi.testclient import TestClient

    return TestClient(app, base_url=base)


# =================================================================== AC1


def test_ac1_hosted_refuses_to_start_without_its_settings_and_local_refuses_a_public_bind():
    """**AC1** — `AGENTCAD_MODE=hosted` without `AGENTCAD_PUBLIC_ORIGIN` (or
    without a resolvable secret) refuses to start with an error naming the
    missing setting; `agentcad serve --host 0.0.0.0` in `local` mode is
    refused.
    """
    with pytest.raises(ModeError) as missing_origin:
        appmode.resolve_mode({"AGENTCAD_MODE": "hosted"})
    assert "AGENTCAD_PUBLIC_ORIGIN" in str(missing_origin.value)

    with pytest.raises(ModeError) as bad_mode:
        appmode.resolve_mode({"AGENTCAD_MODE": "Hosted"})
    assert "AGENTCAD_MODE" in str(bad_mode.value)

    with pytest.raises(ModeError) as relative:
        appmode.resolve_mode({"AGENTCAD_MODE": "hosted",
                              "AGENTCAD_PUBLIC_ORIGIN": "cad.example.com",
                              "AGENTCAD_SECRET_KEY": "k" * 32})
    assert "AGENTCAD_PUBLIC_ORIGIN" in str(relative.value)

    # The interlock, both directions.
    local = appmode.resolve_mode({})
    assert local.hosted is False
    with pytest.raises(ModeError) as bind:
        appmode.check_bind(local, "0.0.0.0")
    assert "AGENTCAD_MODE=hosted" in str(bind.value)
    appmode.check_bind(local, "127.0.0.1")            # loopback is fine

    hosted = appmode.resolve_mode({"AGENTCAD_MODE": "hosted",
                                   "AGENTCAD_PUBLIC_ORIGIN": "https://cad.example.com",
                                   "AGENTCAD_SECRET_KEY": "k" * 32})
    assert hosted.hosted and hosted.origin_host == "cad.example.com"
    appmode.check_bind(hosted, "0.0.0.0")


def test_ac1_the_cli_refuses_rather_than_downgrading(tmp_path, monkeypatch):
    """AC1's other half, at the surface an operator actually types: a
    misconfigured hosted server must not fall back to serving an
    unauthenticated API."""
    from agentcad import cli

    monkeypatch.setenv("AGENTCAD_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("AGENTCAD_MODE", "hosted")
    monkeypatch.delenv("AGENTCAD_PUBLIC_ORIGIN", raising=False)
    with pytest.raises(SystemExit) as exit_code:
        cli._resolve_mode_or_exit()
    assert exit_code.value.code == 2


# =================================================================== AC2


def test_ac2_every_route_is_401_anonymously_except_the_enumerated_surface(
        hosted_client, hosted_app):
    """**AC2** — in hosted mode every route of the fully-mounted app answers
    `401` to an unauthenticated request except an enumerated set, and the
    enumeration is by **equality**, so a route joining the public surface fails
    it.

    The walk is `conftest.flatten_routes`, not `[r.path for r in app.routes]`:
    FastAPI leaves each `include_router` opaque, so the naive walk sees 23 of
    ~83 routes and would stay green while a whole pack went public.
    """
    from .test_hosted_surface import EXPECTED_PUBLIC, NOT_YET_BUILT

    assert NOT_YET_BUILT == set(), "the public surface is not finished"
    routes = {(m, p) for m, p in flatten_routes(hosted_app)
              if m not in {"HEAD", "OPTIONS", "WS"}}
    assert len(routes) > 60, len(routes)
    assert {(m, p) for m, p in routes if security.is_public(p)} == EXPECTED_PUBLIC

    template = re.compile(r"\{[^}]*\}")
    checked = 0
    for method, path in sorted(routes):
        if security.is_public(path):
            continue
        response = hosted_client.request(method, template.sub("demo", path))
        assert response.status_code == 401, f"{method} {path}"
        checked += 1
    assert checked >= 60, checked

    # Default deny is what makes the enumeration cheap to keep true: a pack
    # that does not exist yet is already private.
    assert security.is_public("/api/a-pack-nobody-has-written-yet") is False
    assert security.is_public("/ws") is False


def test_ac2_the_websocket_is_not_anonymous(hosted_client):
    """The event bus fans every project event to every subscriber, so the
    socket is authenticated too — AC2 says "every route **and** the WebSocket"."""
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with hosted_client.websocket_connect("/ws"):
            pass


# =================================================================== AC3


def test_ac3_an_enrolment_url_is_single_use_and_signs_the_browser_in(hosted):
    """**AC3, first half** — an admin-minted enrolment URL sets a password,
    signs the browser in, and a second use `404`s."""
    client, store = hosted
    token = store.add_user("dana")

    peek = client.get(f"/api/auth/enrol/{token}")
    assert peek.status_code == 200 and peek.json()["handle"] == "dana"
    # Reading it never spends it.
    assert client.get(f"/api/auth/enrol/{token}").status_code == 200

    set_password = client.post(f"/api/auth/enrol/{token}",
                               json={"password": "a good long password"})
    assert set_password.status_code == 200, set_password.text
    assert set_password.json()["principal"] == "user:dana"
    assert "agentcad_session" in set_password.cookies

    replay = client.post(f"/api/auth/enrol/{token}",
                         json={"password": "someone else's password"})
    assert replay.status_code == 404
    assert client.get(f"/api/auth/enrol/{token}").status_code == 404


@pytest.mark.slow
def test_ac3_a_second_member_receives_the_edit_and_attribution_is_the_handle(
        hosted):
    """**AC3, second half** — the member edits a part and a second member
    receives the change over the WebSocket; presence, the claim holder, the
    comment author and the history author all read `user:<handle>`.

    Two signed-in clients on one app, and a real kernel build — the point is
    that the composed principal reaches the machinery, not that a string
    formats.
    """
    client, store = hosted
    handle, _password = _second_member(store)
    login(client)
    assert client.post("/api/projects", json={"name": "demo"}).status_code in (200, 201)

    watcher = _client_for(client.app)
    watcher.cookies.set("agentcad_session", store.create_session(handle, "browser:aaaaaaaa"))

    with watcher.websocket_connect("/ws") as ws:
        created = client.post("/api/projects/demo/parts",
                              json={"id": "box", "script": BOX_SCRIPT},
                              headers={"X-Agent-Id": "browser:7f3a1b2c"})
        assert created.status_code == 201, created.text
        seen = None
        for _ in range(40):
            event = ws.receive_json()
            if event.get("type") == "project_changed":
                seen = event
                break
        assert seen is not None and seen["project"] == "demo"

    # Attribution, everywhere it is rendered. A claim is taken through the
    # presence heartbeat — the one door PRD-008 opens for it.
    me = f"user:{ADMIN_HANDLE}/browser:7f3a1b2c"
    beat = client.post("/api/projects/demo/presence",
                       json={"part_id": "box", "claim": True},
                       headers={"X-Agent-Id": "browser:7f3a1b2c"})
    assert beat.status_code == 200, beat.text
    roster = beat.json()
    assert roster["claims"]["box"]["holder"] == me
    assert roster["claims"]["box"]["holder_kind"] == "human"
    assert me in {row["id"] for row in roster["clients"]}
    assert roster["you"] == me

    thread = client.post("/api/projects/demo/comments",
                         json={"body": "looks right", "anchor": {"kind": "part",
                                                                 "part": "box"}},
                         headers={"X-Agent-Id": "browser:7f3a1b2c"})
    assert thread.status_code in (200, 201), thread.text
    authored = json.dumps(thread.json())
    assert f"user:{ADMIN_HANDLE}/browser:7f3a1b2c" in authored

    # History attribution. The hosted fixture's service has `on_publish = None`
    # (no synchronous git snapshot), so the assertion is on the trailer builder
    # every snapshot goes through, under the principal the guard just set —
    # and the end-to-end version of it is on the record: changelog 0190 shows a
    # real hosted server writing `Client: user:nikita/browser:7f3a1b2c`.
    from agentcad.core import history as history_module

    token = locks.client_id_var.set(me)
    try:
        assert history_module.with_client_trailer("edit box").endswith(
            f"Client: {me}\n")
        assert history_module.author_of(
            history_module.with_client_trailer("edit box")) == me
    finally:
        locks.client_id_var.reset(token)


def test_ac3_the_browser_half_is_recorded_as_unverified():
    """**AC3, the half a test cannot be** — the sign-in view, the identity chip
    and a lock chip under a real edit were never rendered by a browser: no
    Chrome extension was connected in any of the three sessions that tried.

    This asserts the *record says so*, which is the opposite of a pass. Delete
    this test when someone with a browser closes it — and update the PRD in the
    same commit, because the two must never disagree.
    """
    # Normalised: these sentences wrap, and a line break must not be able to
    # hide the one admission this test exists to keep in the document.
    prd = " ".join(PRD.read_text(encoding="utf-8").replace("**", "").split())
    assert "graded as evidence" in prd
    assert "never rendered by a browser" in prd
    assert "list_connected_browsers" in prd
    for entry in ("0190-prd-005a-enrolment-login-signin.md",
                  "0194-prd-005a-deployment.md"):
        text = (CHANGELOG / entry).read_text(encoding="utf-8")
        assert "browser" in text.lower()
        assert "not" in text.lower()

    # What a test CAN grade: the surface those sessions would have driven is
    # here, and calls the routes it claims to.
    auth_js = (FRONTEND / "js" / "auth.js").read_text(encoding="utf-8")
    index = (FRONTEND / "index.html").read_text(encoding="utf-8")
    assert "auth-view" in index and "auth-chip" in index
    for call in ("/api/auth/session", "/api/auth/login", "/api/auth/enrol"):
        assert call in auth_js or call in (
            FRONTEND / "js" / "api.js").read_text(encoding="utf-8"), call


# =================================================================== AC4


def test_ac4_logout_and_revocation_take_effect_on_the_next_request(hosted):
    """**AC4** — logout invalidates the session on the next request; a revoked
    bearer token `401`s on the next call; an expired one likewise."""
    client, store = hosted
    signed_in = client.post("/api/auth/login",
                            json={"handle": ADMIN_HANDLE,
                                  "password": ADMIN_PASSWORD})
    assert signed_in.status_code == 200, signed_in.text
    assert client.get("/api/projects").status_code == 200
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/projects").status_code == 401
    client.cookies.clear()

    secret = store.add_token("ci")
    bearer = {"Authorization": f"Bearer {secret}"}
    assert client.get("/api/projects", headers=bearer).status_code == 200
    store.revoke_token(secret.split("_", 2)[1])
    assert client.get("/api/projects", headers=bearer).status_code == 401

    expiring = store.add_token("short", ttl_days=1)
    assert client.get("/api/projects",
                      headers={"Authorization": f"Bearer {expiring}"}).status_code == 200
    from agentcad.core import authstore as store_module
    real_now = store_module._now
    try:
        store_module._now = lambda: real_now() + 2 * 86400
        assert client.get("/api/projects",
                          headers={"Authorization": f"Bearer {expiring}"}
                          ).status_code == 401
    finally:
        store_module._now = real_now


# =================================================================== AC5


def test_ac5_login_is_rate_limited_and_its_failures_are_indistinguishable(hosted):
    """**AC5** — login is rate-limited per account and per address, returning
    `429` with `details.retry_after_s`; a wrong password and an unknown handle
    return identical bodies and take comparable time; no route ever returns a
    password or token digest."""
    client, _store = hosted

    wrong = client.post("/api/auth/login",
                        json={"handle": ADMIN_HANDLE, "password": "not it"})
    unknown = client.post("/api/auth/login",
                          json={"handle": "nobody", "password": "not it"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()

    def _best(handle):
        """`min` of several runs, the stable statistic on a parallel host —
        `test_auth_routes.py`'s idiom, and the lesson this branch learned the
        hard way: a single wall-clock sample under an 8-way suite measures the
        scheduler, not the code."""
        samples = []
        for _ in range(3):
            start = time.perf_counter()
            client.post("/api/auth/login", json={"handle": handle,
                                                 "password": "still not it"})
            samples.append(time.perf_counter() - start)
        return min(samples)

    # Comparable, not equal: the dummy scrypt is what makes "no such account"
    # cost what "wrong password" costs. Both halves hash, so load moves them
    # together; a skipped hash would be ~0 ms against ~63 ms and blows the
    # window wide open.
    known, missing = _best(ADMIN_HANDLE), _best("nobody-at-all")
    assert 0.2 < (missing + 1e-4) / (known + 1e-4) < 5.0, (known, missing)

    limited = None
    for _ in range(30):
        response = client.post("/api/auth/login",
                               json={"handle": ADMIN_HANDLE, "password": "no"})
        if response.status_code == 429:
            limited = response
            break
    assert limited is not None, "login was never rate limited"
    assert limited.json()["error"]["details"]["retry_after_s"] > 0

    # Nothing anywhere returns stored secret material.
    for response in (wrong, unknown, limited):
        body = response.text
        for leak in ("digest", "scrypt", "salt", "password_hash"):
            assert leak not in body, (leak, body)


def test_ac5_no_route_returns_a_digest(hosted):
    """AC5's last clause, on the routes that hold the material."""
    client, store = hosted
    login(client)
    created = client.post("/api/auth/tokens", json={"name": "ci"})
    assert created.status_code == 201, created.text
    secret = created.json()["token"]      # shown here and nowhere else
    assert secret.startswith("acad_")

    listed = client.get("/api/auth/tokens")
    assert listed.status_code == 200
    assert secret not in listed.text
    for leak in ("digest", "salt", "password"):
        assert leak not in listed.text, leak

    users = client.get("/api/auth/users")
    assert users.status_code == 200
    for leak in ("digest", "salt", "scrypt"):
        assert leak not in users.text, leak

    # And the store really is holding only a digest.
    raw = (store.root / "tokens.json").read_text(encoding="utf-8")
    assert secret.split("_", 2)[2] not in raw


# =================================================================== AC6


def test_ac6_the_public_catalog_serves_public_scope_and_hides_private(
        hosted_with_private):
    """**AC6** — the public catalog surface serves packages and preview PNGs
    from a `scope: "public"` index anonymously, and returns an
    indistinguishable `404` for a package carried only by a `scope: "private"`
    index."""
    client, private_name = hosted_with_private
    assert not client.cookies

    listed = client.get("/api/public/packages")
    assert listed.status_code == 200
    names = {p["name"] for p in listed.json()["packages"]}
    assert "din625" in names and private_name not in names

    preview = client.get("/api/public/packages/din625/versions/1.0.0/preview"
                         "?path=previews/ball_bearing_iso.png")
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "image/png"
    assert preview.content[:8] == b"\x89PNG\r\n\x1a\n"

    hidden = client.get(f"/api/public/packages/{private_name}")
    absent = client.get("/api/public/packages/no-such-package-anywhere")
    assert hidden.status_code == absent.status_code == 404
    assert hidden.json() == absent.json()

    # Not disabled — not anonymously readable. A member still finds it.
    login(client)
    hits = client.get("/api/packages/search").json()["hits"]
    assert private_name in {h["name"] for h in hits}


# =================================================================== AC7


def test_ac7_the_whole_public_surface_makes_zero_kernel_calls(
        hosted_with_catalog, kernel_counter):
    """**AC7** — exercising every public route with the kernel instrumented
    produces **zero** kernel requests.

    With the positive control below, because `calls == 0` passes just as
    happily with a broken counter — the shape of green the PRD-011 review kept
    finding.
    """
    from .test_hosted_surface import EXPECTED_PUBLIC

    client = hosted_with_catalog
    before = kernel_counter.calls
    served = 0
    for method, path in sorted(EXPECTED_PUBLIC):
        concrete = (path.replace("{name}", "din625")
                        .replace("{version}", "1.0.0")
                        .replace("{token}", "not-a-real-token"))
        if concrete.endswith("/preview"):
            concrete += "?path=previews/ball_bearing_iso.png"
        served += client.request(method, concrete).status_code == 200
    for asset in ("/js/api.js", "/css/app.css", "/"):
        served += client.get(asset).status_code == 200
    assert kernel_counter.calls == before, kernel_counter.seen
    # Seven of the twelve really answered; the rest cannot be 200s (a login
    # with no body, an enrolment token that does not exist).
    assert served >= 7, served


def test_ac7_the_kernel_counter_is_not_broken(hosted_client, kernel_counter):
    """The positive control for the test above."""
    login(hosted_client)
    hosted_client.post("/api/projects", json={"name": "demo"})
    before = kernel_counter.calls
    built = hosted_client.post("/api/projects/demo/parts",
                               json={"id": "box", "script": BOX_SCRIPT})
    assert built.status_code == 201, built.text
    assert kernel_counter.calls > before


# =================================================================== AC8


def test_ac8_the_deployment_artefacts_are_the_ones_that_were_run():
    """**AC8** — `docker compose up` on a clean host serves the UI on the
    configured origin, `/api/health` reports `{"mode": "hosted"}`, an admin can
    be created through `docker compose exec`, and projects plus identities
    survive `down`/`up`.

    A container is not a unit test. `tests/test_deploy_config.py` grades the
    artefacts in depth with no Docker daemon; this grades that the artefacts
    exist, carry the four things the recorded run depended on, and that the run
    is on the record.
    """
    compose = (REPO / "compose.yaml").read_text(encoding="utf-8")
    dockerfile = (REPO / "Dockerfile").read_text(encoding="utf-8")

    assert "AGENTCAD_MODE: hosted" in compose
    assert "/data" in compose and "healthcheck" in compose
    # The trap that made a healthy container report unhealthy: the probe must
    # present the configured Host, not `127.0.0.1`.
    assert "AGENTCAD_PUBLIC_ORIGIN" in compose.split("healthcheck")[1]
    assert "'Host'" in compose or '"Host"' in compose
    # git is not optional: core/history.py shells out to it.
    assert re.search(r"\bgit\b", dockerfile)

    import yaml

    smoke_path = REPO / ".github" / "workflows" / "deploy-smoke.yml"
    smoke = smoke_path.read_text(encoding="utf-8")
    triggers = yaml.safe_load(smoke)[True]      # YAML 1.1 reads bare `on:` as True
    assert "pull_request" not in triggers, \
        "the multi-GB image build must not be on the PR path"
    assert set(triggers) == {"push", "schedule", "workflow_dispatch"}, triggers
    for step in ("docker compose build", "docker compose up -d",
                 "docker compose exec", "docker compose down"):
        assert step in smoke, step
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "docker compose -f compose.yaml config --quiet" in ci

    recorded = (CHANGELOG / "0194-prd-005a-deployment.md").read_text(
        encoding="utf-8")
    assert "docker compose up -d" in recorded and "healthy" in recorded
    assert '{"status":"ok","mode":"hosted"}' in recorded


# =================================================================== AC9


def test_ac9_local_mode_is_the_same_code_path(tmp_path, kernel, monkeypatch):
    """**AC9** — local mode is unchanged: the middleware behaves exactly as
    before, the hosted route packs are inert, and an existing MCP registration
    keeps working unmodified.

    "Unchanged" is a property of the diff — `create_app(security=None)` runs
    the pre-005a branch — so what is asserted is that nothing hosted is
    reachable or consulted without a config.
    """
    from agentcad.core.tools import build_registry
    from agentcad.server.app import create_app

    monkeypatch.setenv("AGENTCAD_CONFIG", str(tmp_path / "cfg" / "config.json"))
    service = make_test_service(tmp_path / "projects", kernel)
    app = create_app(service, build_registry(service),
                     extra_allowed_hosts={"testserver"})
    client = _client_for(app)

    # No guard: an anonymous request is served, as it always was.
    assert client.get("/api/projects").status_code == 200
    assert client.get("/api/health").json()["status"] == "ok"
    # The full health body, which hosted mode trims.
    body = client.get("/api/health").json()
    assert "version" in body and "kernel" in body   # the full body, untrimmed
    assert "mode" not in body                       # which is a hosted-only key
    # The auth pack is mounted but inert.
    assert client.get("/api/auth/session").status_code == 404
    assert client.post("/api/auth/login",
                       json={"handle": "x", "password": "y"}).status_code == 404
    # `whoami` does not exist where it cannot run.
    assert client.post("/api/tools/whoami", json={}).json()["error"]["type"] \
        == "unknown_tool"
    # The guard short-circuits on `None` before anything else.
    assert security.guard(None, object()) is None

    # An existing MCP registration: no token, no Authorization header.
    monkeypatch.delenv("AGENTCAD_TOKEN", raising=False)
    from agentcad.agent import mcp_server

    assert "Authorization" not in mcp_server._client_headers()


def test_ac9_the_full_suite_count_is_cited():
    """**AC9's** evidence half — "the full suite is green" is a claim about a
    *run*, so the check is that a count is on the record in the close-out
    changelog (the PRD-004 AC10 / PRD-008 AC9 / PRD-009 AC6 / PRD-010 AC8 /
    PRD-011 AC8 precedent).

    It stays an evidence check deliberately: recomputing the number would mean
    running the full suite from inside the full suite, and `--collect-only`
    counts *cases*, which is not what `make test` reports.
    """
    entry = CHANGELOG / "0197-prd-005a-docs-and-acceptance.md"
    assert entry.is_file(), "the PRD-005a close-out changelog entry is missing"
    text = entry.read_text(encoding="utf-8")
    assert "make test" in text and "passed" in text
    assert any(token.isdigit() and len(token) >= 4
               for token in text.replace(",", " ").split()), \
        "the close-out entry does not cite a suite count"

    latest = max(CHANGELOG.glob("0[0-9][0-9][0-9]-*.md"))
    if latest != entry:
        recent = latest.read_text(encoding="utf-8")
        assert "make test" in recent and "passed" in recent, (
            f"{latest.name} is the newest changelog entry and cites no suite "
            "count; every entry that lands work must cite one")


# =================================================================== AC10


def test_ac10_claims_are_human_only_under_composed_principals():
    """**AC10** — two signed-in members are classified `human`: one takes a
    per-part claim, the other is refused with the claim conflict, and a
    token-bearing agent is neither blocked by that claim nor able to take one.

    PRD-008's claim semantics must be identical under composed principals to
    what they are under `browser:<nonce>` identities today — which is exactly
    what the two-line `actor_kind` change buys, and what its absence would
    silently cost.

    Graded on the real `ClaimRegistry`, which is what the criterion asks for
    ("test, on the `tests/test_claims.py` fixtures"). The *signed-in* half —
    that a real session really does compose to one of these strings before the
    registry sees it — is
    `test_ac3_a_second_member_receives_the_edit_and_attribution_is_the_handle`,
    and changelog 0190 has it against a live hosted server.
    """
    from agentcad.core.locks import ClaimRegistry
    from agentcad.core.model import ConflictError
    from agentcad.core.proposals import actor_kind

    claims = ClaimRegistry()
    nikita = "user:nikita/browser:7f3a1b2c"
    anya = "user:anya/browser:aaaaaaaa"
    agent = "agent:ci"

    assert actor_kind(nikita) == "human"
    assert actor_kind(anya) == "human"
    assert actor_kind(agent) == "agent"
    # Local mode's classification is byte-identical to what it always was.
    assert actor_kind("browser:7f3a1b2c") == "human"
    assert actor_kind("claude-code") == "agent"

    taken = claims.acquire("demo", "box", nikita)
    assert taken is not None and taken["holder_kind"] == "human"

    # A second human's acquire returns the STANDING claim, and their write is
    # refused — exactly as for a bare `browser:<nonce>` identity today.
    assert claims.acquire("demo", "box", anya)["holder"] == nikita
    with pytest.raises(ConflictError):
        claims.check("demo", "box", anya)

    # The agent is neither blocked by it nor able to take one.
    assert claims.check("demo", "box", agent) is None
    assert claims.acquire("demo", "box", agent) is None
    assert claims.get("demo", "box")["holder"] == nikita


# =================================================================== AC11


def test_ac11_a_bearer_token_drives_the_api_and_whoami_answers(hosted):
    """**AC11** — an MCP client configured with `AGENTCAD_URL` +
    `AGENTCAD_TOKEN` against a hosted instance lists tools and calls one
    successfully; `whoami` returns `agent:<name>` and the role; clearing the
    token makes the same call `401`."""
    client, store = hosted
    secret = store.add_token("ci")
    bearer = {"Authorization": f"Bearer {secret}"}

    tools = client.get("/api/tools", headers=bearer)
    assert tools.status_code == 200
    listed = json.dumps(tools.json())
    assert "whoami" in listed and "create_project" in listed

    who = client.post("/api/tools/whoami", json={}, headers=bearer)
    assert who.status_code == 200, who.text
    assert who.json() == {"principal": "agent:ci", "kind": "agent",
                          "role": "member", "mode": "hosted"}

    # Clearing the token is the whole difference.
    assert client.post("/api/tools/whoami", json={}).status_code == 401
    assert client.get("/api/tools").status_code == 401


def test_ac11_the_mcp_proxy_carries_the_token_and_refuses_a_remote_autostart(
        monkeypatch):
    """AC11's client half, without standing up a second process: the proxy
    attaches the bearer and — the part that matters operationally — does not
    spawn a *local* server because a *remote* one is unreachable."""
    from agentcad.agent import mcp_server

    monkeypatch.setenv("AGENTCAD_TOKEN", "acad_deadbeef_secret")
    assert mcp_server._client_headers()["Authorization"] == \
        "Bearer acad_deadbeef_secret"
    monkeypatch.setenv("AGENTCAD_TOKEN", "   ")
    assert "Authorization" not in mcp_server._client_headers()

    assert mcp_server._may_autostart("http://127.0.0.1:8630") is True
    assert mcp_server._may_autostart("http://localhost:8630") is True
    assert mcp_server._may_autostart("https://cad.example.com") is False
    # A host comparison, not a prefix one.
    assert mcp_server._may_autostart("http://127.0.0.1.evil.example") is False


# ==================================================== the PRD's own record


def test_the_prd_records_its_status_and_its_residuals():
    """The PRD is the record a reviewer reads first, so the decisions and the
    gaps have to be findable there — not only in a spec nobody opens."""
    text = PRD.read_text(encoding="utf-8")
    assert any(f"**Status:** {state}" in text
               for state in ("implemented", "completed")), \
        "the PRD's status is not a post-implementation one"
    for ac in [f"AC{n}" for n in range(1, 12)]:
        assert ac in text, ac
    for needle in (
            "open_project",          # the FR19 residual, named not papered over
            "core/tools.py",         # ...and why it is still open
            "proxy",                 # the compose profile that was never run
            "deploy-smoke.yml",      # the workflow that has never executed
            "an account is a shell",
    ):
        assert needle.lower() in text.replace("**", "").lower(), \
            f"the PRD does not record {needle!r}"


def test_the_trust_sentence_is_in_all_four_places():
    """FR17: the trust statement is not a footnote in one file. If a reader can
    reach an account without meeting it, the sentence has failed."""
    places = {
        "docs/deployment.md": (REPO / "docs" / "deployment.md").read_text(
            encoding="utf-8"),
        "compose.yaml": (REPO / "compose.yaml").read_text(encoding="utf-8"),
    }
    for name, text in places.items():
        lowered = text.lower()
        assert "arbitrary python" in lowered, name
        assert "shell" in lowered, name

    from agentcad.cli import TRUST_SENTENCE

    assert "arbitrary python" in TRUST_SENTENCE.lower()
    # The admin CLI's help, through the real parser — `agentcad admin --help`
    # and `agentcad admin user add --help` are two of FR17's four places.
    from agentcad.cli import main

    for argv in (["admin", "--help"], ["admin", "user", "--help"],
                 ["admin", "user", "add", "--help"],
                 ["admin", "token", "add", "--help"]):
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed), pytest.raises(SystemExit):
            with mock.patch.object(sys, "argv", ["agentcad", *argv]):
                main()
        assert "arbitrary Python" in printed.getvalue(), (argv, printed.getvalue())


def test_the_roadmap_link_resolves_to_the_prd_where_it_actually_lives():
    roadmap = (REPO / "docs" / "roadmap.md").read_text(encoding="utf-8")
    row = next(line for line in roadmap.splitlines()
               if line.startswith("| [005a]"))
    match = re.search(r"\((prd/[^)]+\.md)\)", row)
    assert match, f"the roadmap row for PRD-005a carries no link: {row}"
    assert (REPO / "docs" / match.group(1)).is_file(), \
        f"the roadmap link {match.group(1)} does not resolve"


def test_the_agents_guide_carries_the_hosted_gotchas():
    """A gotcha nobody can find is a gotcha nobody avoids — and these are the
    ones that cost this feature real time."""
    guide = (REPO / "AGENTS.md").read_text(encoding="utf-8")
    assert "## Hosted-core gotchas (PRD-005a" in guide
    for needle in (
            "actor_kind",            # or every hosted person loses their claims
            "install(cfg)",          # ...before build_registry
            "open_project",          # the residual, at its real strength
            "startswith",            # the PUBLIC_PREFIXES rule
            "n=2^15",                # the scrypt argument, stated not hidden
            "Host",                  # the healthcheck trap
    ):
        assert needle in guide, needle
