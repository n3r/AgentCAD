"""PRD-007 slice 2: the load-bearing containment proof.

Publishing pins by *copying* the part's bytes out of the owner project into the
state dir and builds against a muzzled service rooted there. So an authenticated
publish (and, in slice 4, a visitor flood) must leave the owner's project —
manifest, parts, ``.cache/`` and ``.history`` — **byte-unchanged**. Here the
publish alone is proved inert on the owner tree; AC5's flood is slice 4.
"""

from __future__ import annotations

import hashlib

from .conftest import BOX_SCRIPT, login


def _snapshot(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def _setup_owner(client):
    login(client)
    client.post("/api/projects", json={"name": "demo"})
    r = client.post("/api/projects/demo/parts",
                    json={"id": "box", "script": BOX_SCRIPT})
    assert r.status_code == 201, r.text
    tagged = client.post("/api/projects/demo/versions", json={"name": "v1"})
    assert tagged.status_code in (200, 201), tagged.text


def test_publishing_never_writes_the_owner_project(hosted, tmp_path):
    client, _ = hosted
    _setup_owner(client)
    owner_dir = client.agentcad_service.store.canonical_path_of("demo")

    before = _snapshot(owner_dir)
    r = client.post("/api/share", json={
        "project": "demo", "scope": "part", "part_id": "box",
        "ref": "v1", "customizer": True, "exports": ["step"]})
    assert r.status_code == 201, r.text

    # The pin read blobs and built in the state dir; the owner tree is untouched.
    assert _snapshot(owner_dir) == before


def test_the_variant_cache_lives_in_the_state_dir_not_the_project(hosted, tmp_path):
    """The default variant was warmed at publish — its ``.acm`` is under
    ``<state-dir>/publications/build/``, and there is no matching mesh newly
    written into the owner project's ``.cache/``."""
    client, _ = hosted
    _setup_owner(client)
    owner_dir = client.agentcad_service.store.canonical_path_of("demo")
    before_cache = _snapshot(owner_dir / ".cache") if (owner_dir / ".cache").is_dir() else {}

    r = client.post("/api/share", json={
        "project": "demo", "scope": "part", "part_id": "box",
        "ref": "v1", "customizer": True, "exports": ["step"]})
    assert r.status_code == 201, r.text

    after_cache = _snapshot(owner_dir / ".cache") if (owner_dir / ".cache").is_dir() else {}
    assert after_cache == before_cache

    # ...and the warmed default variant really exists in the state dir.
    builder = client.agentcad_service.share_builder
    pub_id = r.json()["pub_id"]
    rec = client.agentcad_service.publications.get(pub_id)
    mesh = builder.mesh_path(rec["script_sha"], rec["default_variant_key"])
    assert mesh is not None and mesh.is_file()
    assert client.agentcad_service.publications.build_root() in mesh.parents


def test_the_store_is_not_a_user_project(hosted):
    """The publication store lives under the state dir, never in a
    ``ProjectStore`` — so ``list_projects`` never shows it and a
    ``--projects-dir`` cannot reach it."""
    client, _ = hosted
    _setup_owner(client)
    client.post("/api/share", json={
        "project": "demo", "scope": "part", "part_id": "box", "ref": "v1"})
    names = [p["name"] for p in client.agentcad_service.list_projects()]
    assert names == ["demo"]                 # the muzzled build project is invisible
