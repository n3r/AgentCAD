"""PRD-007 slice 2: the management API and the pin.

An authenticated member turns a part at a version into a token; the default
variant is pre-warmed; the token is shown once; revoke is immediate; and the
pin is a copy, so a later owner edit cannot move a live link (AC8's store-level
half — the viewer proof is slice 3).
"""

from __future__ import annotations

from .conftest import BOX_SCRIPT, login


def _setup(client):
    login(client)
    client.post("/api/projects", json={"name": "demo"})
    assert client.post("/api/projects/demo/parts",
                       json={"id": "box", "script": BOX_SCRIPT}).status_code == 201
    assert client.post("/api/projects/demo/versions",
                       json={"name": "v1"}).status_code in (200, 201)


def _token(url: str) -> str:
    return url.rsplit("/s/", 1)[1]


def test_share_create_returns_a_url_and_pub_id(hosted):
    client, _ = hosted
    _setup(client)
    r = client.post("/api/share", json={
        "project": "demo", "scope": "part", "part_id": "box",
        "ref": "v1", "customizer": True, "exports": ["step"]})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["pub_id"] and len(body["pub_id"]) == 8
    token = _token(body["url"])
    assert token.startswith("shr_")
    # The record was pinned with a warmed default variant.
    rec = client.agentcad_service.publications.get(body["pub_id"])
    assert rec["ref"]["kind"] == "tag" and rec["ref"]["name"] == "v1"
    assert rec["script_sha"].startswith("sha256:")
    assert rec["default_variant_key"]


def test_share_list_shows_the_link_without_the_token(hosted):
    client, _ = hosted
    _setup(client)
    created = client.post("/api/share", json={
        "project": "demo", "part_id": "box", "ref": "v1"}).json()
    listed = client.get("/api/share", params={"project": "demo"}).json()["links"]
    assert len(listed) == 1
    row = listed[0]
    assert row["pub_id"] == created["pub_id"]
    assert row["counters"] == {"views": 0, "rebuilds": 0, "downloads": 0}
    assert "token_digest" not in row
    assert _token(created["url"]).split("_", 2)[2] not in repr(listed)


def test_share_revoke_is_immediate(hosted):
    client, _ = hosted
    _setup(client)
    created = client.post("/api/share", json={
        "project": "demo", "part_id": "box", "ref": "v1"}).json()
    token = _token(created["url"])
    store = client.agentcad_service.publications
    assert store.resolve(token) is not None            # live before

    r = client.request("DELETE", f"/api/share/{created['pub_id']}")
    assert r.status_code == 200 and r.json()["revoked"] is True
    assert store.resolve(token) is None                # dead after, no restart


def test_publishing_a_branch_ref_without_a_tag_auto_tags(hosted):
    """FR3: omitting the ref pins the current head by auto-tagging an immutable
    version, so the link does not follow the branch."""
    client, _ = hosted
    _setup(client)
    r = client.post("/api/share", json={
        "project": "demo", "part_id": "box", "customizer": True})
    assert r.status_code == 201, r.text
    rec = client.agentcad_service.publications.get(r.json()["pub_id"])
    assert rec["ref"]["kind"] == "tag"
    assert rec["ref"]["name"].startswith("share-")
    assert rec["ref"]["commit"]


def test_publishing_an_unknown_part_is_not_found(hosted):
    client, _ = hosted
    _setup(client)
    r = client.post("/api/share", json={
        "project": "demo", "part_id": "nope", "ref": "v1"})
    assert r.status_code == 404, r.text
    assert "not" in r.json()["error"]["type"].lower()


def test_an_unknown_export_format_is_rejected(hosted):
    client, _ = hosted
    _setup(client)
    r = client.post("/api/share", json={
        "project": "demo", "part_id": "box", "ref": "v1",
        "exports": ["dwg"]})
    assert r.status_code == 422, r.text


def test_an_anonymous_publish_is_401(hosted):
    client, _ = hosted
    _setup(client)                            # sets a cookie...
    client.cookies.clear()                    # ...now drop it: anonymous again
    r = client.post("/api/share", json={
        "project": "demo", "part_id": "box", "ref": "v1"})
    assert r.status_code == 401, r.text


def test_a_later_owner_edit_does_not_change_the_pin(hosted):
    """The pin is a copy: rewriting the owner's working part changes neither
    the stored commit nor the copied script bytes (AC8, store-level)."""
    client, _ = hosted
    _setup(client)
    created = client.post("/api/share", json={
        "project": "demo", "part_id": "box", "ref": "v1",
        "customizer": True}).json()
    rec_before = client.agentcad_service.publications.get(created["pub_id"])
    pinned_bytes = client.agentcad_service.share_builder.script_text(
        rec_before["script_sha"])

    # The owner edits the working part.
    edited = BOX_SCRIPT.replace("p.size, p.size, p.size", "p.size*2, p.size, p.size")
    assert client.put("/api/projects/demo/parts/box",
                      json={"script": edited}).status_code == 200

    rec_after = client.agentcad_service.publications.get(created["pub_id"])
    assert rec_after["script_sha"] == rec_before["script_sha"]
    assert client.agentcad_service.share_builder.script_text(
        rec_after["script_sha"]) == pinned_bytes
    assert "p.size*2" not in pinned_bytes


def test_the_share_tools_are_registered_in_hosted_mode(hosted):
    from agentcad.core import tools_share
    from agentcad.core.tools import ToolRegistry

    client, _ = hosted
    reg = ToolRegistry()
    tools_share.register(reg, client.agentcad_service)
    names = {t.name for t in reg.list()}
    assert {"share_create", "share_list", "share_revoke"} <= names


def test_the_share_tools_are_absent_in_local_mode():
    """The `whoami` precedent: no `SecurityConfig` means the tools do not
    register — an agent is never offered a tool that cannot run."""
    from agentcad.core import tools_share
    from agentcad.core.tools import ToolRegistry
    from agentcad.server import security

    saved = security.current_config()
    security.install(None)
    try:
        reg = ToolRegistry()
        tools_share.register(reg, service=object())
        assert reg.list() == []
    finally:
        security.install(saved)
