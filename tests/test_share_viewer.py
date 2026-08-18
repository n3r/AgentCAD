"""PRD-007 slice 3: the kernel-free anonymous viewer.

A logged-out visitor renders a pinned part with **zero** kernel calls (AC7,
proved with a positive control), a revoked/expired/unknown token is one
indistinguishable 404 (AC6), and the anonymous surface grew by exactly the
viewer routes (the enumeration test in ``test_hosted_surface.py``). The embed
page opts into cross-origin framing; the main app does not.
"""

from __future__ import annotations

import time

import pytest

from .conftest import BOX_SCRIPT, login


def _publish(client, **settings):
    login(client)
    client.post("/api/projects", json={"name": "demo"})
    assert client.post("/api/projects/demo/parts",
                       json={"id": "box", "script": BOX_SCRIPT}).status_code == 201
    assert client.post("/api/projects/demo/versions",
                       json={"name": "v1"}).status_code in (200, 201)
    body = dict(project="demo", part_id="box", ref="v1", customizer=True,
                exports=["step"])
    body.update(settings)
    r = client.post("/api/share", json=body)
    assert r.status_code == 201, r.text
    token = r.json()["url"].rsplit("/s/", 1)[1]
    pub_id = r.json()["pub_id"]
    client.cookies.clear()                    # the visitor is anonymous
    return token, pub_id


VIEWER_ROUTES = [
    "/s/{t}", "/embed/{t}", "/s/{t}/model", "/s/{t}/params", "/s/{t}/script",
]


def test_the_page_opens_for_a_logged_out_visitor(hosted):
    client, _ = hosted
    token, _ = _publish(client)
    r = client.get(f"/s/{token}")
    assert r.status_code == 200
    assert "set-cookie" not in {k.lower() for k in r.headers}
    assert r.headers.get("referrer-policy") == "no-referrer"
    assert "text/html" in r.headers["content-type"]


def test_model_returns_attribution_and_metrics(hosted):
    client, _ = hosted
    token, _ = _publish(client)
    body = client.get(f"/s/{token}/model").json()
    assert body["attribution"]["project"] == "demo"
    assert body["attribution"]["part_id"] == "box"
    assert body["attribution"]["ref"]["name"] == "v1"
    assert body["settings"]["customizer"] is True
    assert body["default_variant_key"]
    assert body["metrics"] and body["metrics"]["mass_g"] > 0


def test_mesh_serves_the_default_key_and_404s_an_absent_key_without_building(
        hosted, kernel_counter):
    client, _ = hosted
    token, _ = _publish(client)
    key = client.get(f"/s/{token}/model").json()["default_variant_key"]

    before = kernel_counter.calls
    good = client.get(f"/s/{token}/mesh/{key}")
    assert good.status_code == 200
    assert good.headers["content-type"] == "application/octet-stream"
    assert good.content[:4] == b"ACM1" or len(good.content) > 0

    # An absent key is a 404 and NEVER a build.
    absent = client.get(f"/s/{token}/mesh/" + "0" * 32)
    assert absent.status_code == 404
    assert kernel_counter.calls == before, kernel_counter.seen


def test_params_returns_the_typed_spec(hosted):
    client, _ = hosted
    token, _ = _publish(client)
    spec = client.get(f"/s/{token}/params").json()["params_spec"]
    assert "size" in spec
    assert spec["size"]["min"] == 1.0 and spec["size"]["max"] == 100.0


def test_script_is_gated_on_show_script(hosted):
    client, _ = hosted
    # off by default
    token_off, _ = _publish(client)
    assert client.get(f"/s/{token_off}/script").status_code == 404

    # Publish a second link on the same instance with the script visible.
    login(client)
    r = client.post("/api/share", json={
        "project": "demo", "part_id": "box", "ref": "v1", "show_script": True})
    token_on = r.json()["url"].rsplit("/s/", 1)[1]
    client.cookies.clear()
    served = client.get(f"/s/{token_on}/script")
    assert served.status_code == 200
    assert "def build(p)" in served.text


def test_revoked_expired_and_unknown_are_indistinguishable_on_every_route(hosted):
    client, _ = hosted
    token, pub_id = _publish(client)

    # Revoke this link.
    login(client)
    assert client.request("DELETE", f"/api/share/{pub_id}").status_code == 200
    client.cookies.clear()

    # An expired link, crafted directly (the API only accepts positive expiry).
    rec = client.agentcad_service.publications.get(pub_id)
    _, expired = client.agentcad_service.publications.create(
        share_scope="part", project="demo", part_id="box", ref=rec["ref"],
        script_sha=rec["script_sha"],
        settings={"customizer": True, "exports": [], "show_script": False,
                  "expires": int(time.time()) - 1, "config": None},
        created_by="nikita", default_variant_key=rec["default_variant_key"])

    unknown = "shr_deadbeef_" + "x" * 43
    for template in VIEWER_ROUTES + ["/s/{t}/mesh/" + "0" * 32]:
        bodies = {}
        for label, tok in (("revoked", token), ("expired", expired),
                           ("unknown", unknown)):
            r = client.get(template.format(t=tok))
            assert r.status_code == 404, (template, label)
            bodies[label] = r.json()
        assert bodies["revoked"] == bodies["expired"] == bodies["unknown"], template


def test_no_viewer_route_reaches_the_kernel_with_a_positive_control(
        hosted, kernel_counter):
    client, _ = hosted
    token, _ = _publish(client)
    key = client.get(f"/s/{token}/model").json()["default_variant_key"]

    before = kernel_counter.calls
    for path in ([r.format(t=token) for r in VIEWER_ROUTES]
                 + [f"/s/{token}/mesh/{key}"]):
        client.get(path)
    assert kernel_counter.calls == before, kernel_counter.seen

    # Positive control: the counter is not simply stuck — a real authored build
    # of a NOVEL script (a different default, so a fresh cache key) moves it, so
    # the zero above means something.
    login(client)
    novel = BOX_SCRIPT.replace("10.0", "13.0")
    assert client.post("/api/projects/demo/parts",
                       json={"id": "box2", "script": novel}).status_code == 201
    assert kernel_counter.calls > before


def test_the_embed_page_is_framable_and_the_main_app_is_not(hosted):
    client, _ = hosted
    token, _ = _publish(client)
    embed = client.get(f"/embed/{token}")
    assert embed.status_code == 200
    assert embed.headers["content-security-policy"] == "frame-ancestors *"

    # A non-share hosted response carries frame-ancestors 'none'.
    health = client.get("/api/health")
    assert health.headers.get("content-security-policy") == "frame-ancestors 'none'"


def test_a_share_page_load_bumps_only_the_view_counter(hosted):
    client, _ = hosted
    token, pub_id = _publish(client)
    client.get(f"/s/{token}")
    client.get(f"/s/{token}/model")           # an asset fetch must not count
    rec = client.agentcad_service.publications.get(pub_id)
    assert rec["counters"]["views"] == 1
    assert rec["counters"]["rebuilds"] == 0 and rec["counters"]["downloads"] == 0
