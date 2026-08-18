"""PRD-007 slice 5: the slim viewer/customizer bundle and the owner dialog.

The browser-facing ACs (AC1 the logged-out page, AC10 the embed on a second
origin) are graded as evidence when no Chrome extension is available (the
PRD-005a AC3 precedent). These tests cover what CAN be asserted headless: the
self-contained shell is served for both `/s/` and `/embed/`, it reuses the
viewport's `parseACM` (design "Surfaces"), and it contacts no external host so
it is CSP-clean and embeddable.
"""

from __future__ import annotations

import re

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
    client.cookies.clear()
    return token


def test_the_share_shell_is_served_for_both_pages(hosted):
    client, _ = hosted
    token = _publish(client)
    for path in (f"/s/{token}", f"/embed/{token}"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert "text/html" in r.headers["content-type"]
        assert 'id="share-viewport"' in r.text
        assert "/js/share.js" in r.text
        assert "set-cookie" not in {k.lower() for k in r.headers}


def test_the_shell_contacts_no_external_host(hosted):
    """CSP-clean: every asset is a same-origin `/js` or `/vendor` reference —
    an embed the CSP `frame-ancestors *` allows must not then be blocked
    fetching a CDN."""
    client, _ = hosted
    token = _publish(client)
    html = client.get(f"/s/{token}").text
    # No absolute http(s) URLs to any host.
    assert not re.search(r"https?://[^\"'\s)]+", html), html


def test_the_bundle_reuses_the_viewport_parse_acm(hosted):
    """The slim viewer re-exports `parseACM` from `viewport.js` and `share.js`
    imports it — the reuse the design's Surfaces section names."""
    client, _ = hosted
    slim = client.get("/js/share-viewport.js")
    assert slim.status_code == 200
    assert "parseACM" in slim.text
    assert 'from "./viewport.js"' in slim.text

    controller = client.get("/js/share.js")
    assert controller.status_code == 200
    assert "share-viewport.js" in controller.text
    # It drives the customizer routes.
    assert "/variant" in controller.text
    assert "/download/" in controller.text


def test_the_owner_bundle_calls_the_share_api(hosted):
    client, _ = hosted
    login(client)
    js = client.get("/js/share-links.js").text
    assert "shareCreate" in js and "shareList" in js and "shareRevoke" in js
    api = client.get("/js/api.js").text
    assert "/api/share" in api
