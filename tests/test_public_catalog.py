"""PRD-005a slice 7: the anonymous catalog read.

Four routes, no credential, no kernel, and — the point of the whole pack — no
private index. `routes_packages.py`'s `search` and `preview` walk **every**
configured index, `scope: "private"` ones included, which is why this is a
separate pack that filters rather than a flag on that one (design Decision 8,
"the single most important detail").

The leak tests are written against a private index that is configured *first*,
so it wins precedence: an implementation that walked `service.packages.indexes`
in order without filtering would serve it and these tests would be red.
"""

from __future__ import annotations

import pytest

from .conftest import configure_private_index, login

CATALOG = "agentcad-core"


def _packages(client):
    body = client.get("/api/public/packages").json()
    return {p["name"]: p for p in body["packages"]}


# ------------------------------------------------------------ the happy path

def test_the_bundled_catalog_lists_anonymously(hosted_with_catalog):
    client = hosted_with_catalog
    names = {p["name"] for p in client.get("/api/public/packages").json()["packages"]}
    assert "iso4762" in names


def test_the_listing_carries_the_latest_version_of_each_package(hosted_with_catalog):
    """A catalog page needs enough to render a card without a second request:
    the resolved version, the summary, the licence and the gate verdict."""
    entry = _packages(hosted_with_catalog)["din625"]
    assert entry["version"] == "1.0.0"
    assert entry["index"] == CATALOG
    assert entry["license"] == "Apache-2.0"
    assert entry["gate"]["status"] == "green"
    assert "ball bearing" in entry["summary"].lower()


def test_a_package_document_lists_its_versions(hosted_with_catalog):
    body = hosted_with_catalog.get("/api/public/packages/din625").json()
    assert body["name"] == "din625"
    assert body["index"] == CATALOG
    assert body["latest"] == "1.0.0"
    assert body["versions"] == ["1.0.0"]


def test_a_version_carries_the_pregenerated_metadata(hosted_with_catalog):
    body = hosted_with_catalog.get(
        "/api/public/packages/din625/versions/1.0.0").json()
    assert body["gate"]["status"] == "green"
    assert body["parts"]["ball_bearing"]["connectors"] == {
        "bore": "cylindrical", "face": "rigid"}
    assert body["previews"] == ["previews/ball_bearing_iso.png"]


def test_a_preview_png_is_served_anonymously(hosted_with_catalog):
    r = hosted_with_catalog.get(
        "/api/public/packages/din625/versions/1.0.0/preview"
        "?path=previews/ball_bearing_iso.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.headers["cache-control"] == "public, max-age=300"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.parametrize("path", [
    "/api/public/packages",
    "/api/public/packages/din625",
    "/api/public/packages/din625/versions/1.0.0",
])
def test_every_json_route_is_cacheable(hosted_with_catalog, path):
    """Design Decision 9's residual: anonymous read is unmetered in 005-lite,
    and the answer to a flood is that a CDN or reverse proxy can absorb it.
    That is only true if the responses actually say they are cacheable."""
    assert hosted_with_catalog.get(path).headers["cache-control"] == \
        "public, max-age=300"


@pytest.mark.parametrize("path", [
    "/api/public/packages/no-such-package",
    "/api/public/packages/no-such-package/versions/1.0.0",
    "/api/public/packages/din625/versions/9.9.9",
    "/api/public/packages/din625/versions/1.0.0/preview?path=previews/gone.png",
])
def test_the_misses_are_cacheable_too(hosted_with_catalog, path):
    """Review finding m1. A flood asks for names that do not exist, so the
    404s are the majority of it — and they went out with no `Cache-Control`
    because raising discards the handler's `Response`. `AppError.headers` is
    what carries it now."""
    r = hosted_with_catalog.get(path)
    assert r.status_code == 404, path
    assert r.headers["cache-control"] == "public, max-age=300", path


def test_the_whole_surface_answers_with_no_credential(hosted_with_catalog):
    """These are the four routes `security.PUBLIC_PREFIXES` opens; assert they
    answer 200 with an empty cookie jar rather than trusting the enumeration."""
    assert not hosted_with_catalog.cookies
    for path in ("/api/public/packages",
                 "/api/public/packages/din625",
                 "/api/public/packages/din625/versions/1.0.0",
                 "/api/public/packages/din625/versions/1.0.0/preview"
                 "?path=previews/ball_bearing_iso.png"):
        assert hosted_with_catalog.get(path).status_code == 200, path


# ------------------------------------------------------------- the scope wall

def test_a_private_index_is_invisible_and_indistinguishable(hosted_with_private):
    client, private_name = hosted_with_private
    listed = {p["name"] for p in client.get("/api/public/packages").json()["packages"]}
    assert private_name not in listed
    a = client.get(f"/api/public/packages/{private_name}")
    b = client.get("/api/public/packages/does-not-exist-at-all")
    assert a.status_code == b.status_code == 404
    assert a.json() == b.json()


def test_the_public_catalog_is_still_there_beside_the_private_one(
        hosted_with_private):
    """The negation of the test above: filtering that dropped *everything*
    would satisfy "the private one is invisible" perfectly."""
    client, _ = hosted_with_private
    assert "din625" in _packages(client)


def test_a_private_version_and_preview_are_equally_invisible(hosted_with_private):
    client, private_name = hosted_with_private
    version = client.get(
        f"/api/public/packages/{private_name}/versions/1.0.0")
    preview = client.get(
        f"/api/public/packages/{private_name}/versions/1.0.0/preview"
        "?path=previews/ball_bearing_iso.png")
    missing = client.get("/api/public/packages/nope/versions/1.0.0")
    assert version.status_code == preview.status_code == 404
    assert version.json() == missing.json()


def test_a_private_index_cannot_shadow_a_public_package(hosted_with_catalog,
                                                        tmp_path):
    """The private index is first in precedence and carries a package of the
    *same name* as a public one. A loop that took the first index carrying the
    name would serve private content under a public name — the leak that
    filtering-before-lookup exists to prevent."""
    configure_private_index(hosted_with_catalog, tmp_path / "shadow",
                            package_name="din625")
    body = hosted_with_catalog.get(
        "/api/public/packages/din625/versions/1.0.0").json()
    assert body["index"] == CATALOG
    assert "internal only" not in body["summary"]


def test_the_operators_private_scope_beats_the_documents_public_one(
        hosted_with_catalog, tmp_path):
    """Review finding M2, end to end.

    The operator wrote `scope: "private"` in `~/.agentcad/config.json`; the
    index document — which for a git index is written by whoever owns the
    repository, not by the operator — declares `"scope": "public"`. The
    operator's word is the one that decides internet exposure, so this index
    is not consulted at all and its package answers the same name-free 404 as
    a package that does not exist.
    """
    private_name = configure_private_index(
        hosted_with_catalog, tmp_path / "doc-says-public",
        document_scope="public")
    listed = {p["name"] for p in
              hosted_with_catalog.get("/api/public/packages").json()["packages"]}
    assert private_name not in listed
    mine = hosted_with_catalog.get(f"/api/public/packages/{private_name}")
    missing = hosted_with_catalog.get("/api/public/packages/does-not-exist-at-all")
    assert mine.status_code == missing.status_code == 404
    assert mine.json() == missing.json()
    # And the version and preview routes, which reach `_find` by a second path.
    assert hosted_with_catalog.get(
        f"/api/public/packages/{private_name}/versions/1.0.0"
    ).status_code == 404
    # Not disabled, just not anonymous: a signed-in member still finds it.
    login(hosted_with_catalog)
    hits = hosted_with_catalog.get("/api/packages/search").json()["hits"]
    assert private_name in {h["name"] for h in hits}


def test_the_authenticated_search_route_still_sees_private_indexes(
        hosted_with_private):
    """The private index is not *disabled*; it is not anonymously readable.
    A member's own Library dialog must still find it."""
    client, private_name = hosted_with_private
    login(client)
    # `search.search` returns {"hits": [...], "indexes": [...], ...} —
    # `hits`, never `results` (agentcad/core/packages/search.py:103).
    hits = client.get("/api/packages/search").json()["hits"]
    assert private_name in {h["name"] for h in hits}


def test_the_filter_is_on_scope_not_on_the_index_name():
    """`_public_indexes` is the whole access rule, so test it directly: an
    index is admitted for its scope and never for what it is called or for
    where it sits in precedence order."""
    import types

    from agentcad.server.routes_public import _public_indexes

    def stub(name, scope, configured=None):
        return types.SimpleNamespace(
            name=name, scope=scope,
            configured_scope=scope if configured is None else configured)

    service = types.SimpleNamespace(packages=types.SimpleNamespace(indexes=[
        stub("public-mirror", "private"),   # named public, is not
        stub("agentcad-core", "public"),
        stub("secret", "private"),
        stub("unscoped", None),             # a kind that has no scope at all
    ]))
    assert [ix.name for ix in _public_indexes(service)] == ["agentcad-core"]


def test_both_scopes_must_agree_before_an_index_is_anonymously_readable():
    """Review finding M2, at the filter itself.

    `index.scope` lets the index *document* win, which is right for publish
    policy and wrong for access control — for a git index the third party who
    writes `index.json` would otherwise decide whether the operator's instance
    serves it to the internet. `configured_scope` is the operator's own word,
    and the filter needs both.
    """
    import types

    from agentcad.server.routes_public import _public_indexes

    def stub(name, configured, declared):
        return types.SimpleNamespace(name=name, configured_scope=configured,
                                     scope=declared)

    service = types.SimpleNamespace(packages=types.SimpleNamespace(indexes=[
        stub("doc-says-public", "private", "public"),   # the finding
        stub("doc-says-private", "public", "private"),  # the old refusal
        stub("agreed", "public", "public"),
        stub("no-configured-scope-attribute",
             None, "public"),                            # a future index kind
    ]))
    assert [ix.name for ix in _public_indexes(service)] == ["agreed"]


# ------------------------------------------------------------- the containment

def test_a_preview_path_may_not_escape_the_version_directory(hosted_with_catalog):
    r = hosted_with_catalog.get(
        "/api/public/packages/din625/versions/1.0.0/preview"
        "?path=../../../../etc/passwd")
    assert r.status_code in (404, 422)


@pytest.mark.parametrize("path", [
    "../../../../etc/passwd",
    "../../iso4762/1.0.0/previews/socket_head_cap_screw_iso.png",
    "/etc/passwd",
    "previews/../../../index.json",
    "",
])
def test_no_spelling_of_escape_is_served(hosted_with_catalog, path):
    """`content.resolve_within` is the containment, and the sibling-package
    spelling is the one a `.png` suffix check alone would let through."""
    r = hosted_with_catalog.get(
        "/api/public/packages/din625/versions/1.0.0/preview", params={"path": path})
    assert r.status_code in (404, 422), (path, r.status_code)


def test_a_non_png_preview_is_refused(hosted_with_catalog):
    r = hosted_with_catalog.get(
        "/api/public/packages/din625/versions/1.0.0/preview"
        "?path=parts/ball_bearing.py")
    assert r.status_code == 422


def test_a_preview_with_no_path_is_the_house_envelope(hosted_with_catalog):
    """422, and in `{"error": {...}}` — not FastAPI's native `{"detail": ...}`.

    Review finding m5: this was the one route on the anonymous surface that
    answered in a second error dialect, which a client written against this
    API has no reason to parse. `path` is therefore optional in the signature
    and checked in the handler.
    """
    r = hosted_with_catalog.get(
        "/api/public/packages/din625/versions/1.0.0/preview")
    assert r.status_code == 422
    body = r.json()
    assert "detail" not in body, body
    assert body["error"]["type"] == "ValidationError"
    assert body["error"]["details"] == {"parameter": "path"}


def test_a_missing_preview_file_is_a_404(hosted_with_catalog):
    assert hosted_with_catalog.get(
        "/api/public/packages/din625/versions/1.0.0/preview"
        "?path=previews/not_here.png").status_code == 404


# --------------------------------------------------------------- kernel-free

def test_the_catalog_read_makes_no_kernel_calls(hosted_with_catalog,
                                                kernel_counter):
    """AC7, at this pack's own door. `test_hosted_surface.py` asserts it for
    the whole nine-entry surface; this one fails on the pack that broke it."""
    before = kernel_counter.calls
    for path in ("/api/public/packages",
                 "/api/public/packages/din625",
                 "/api/public/packages/din625/versions/1.0.0",
                 "/api/public/packages/din625/versions/1.0.0/preview"
                 "?path=previews/ball_bearing_iso.png"):
        assert hosted_with_catalog.get(path).status_code == 200, path
    assert kernel_counter.calls == before, kernel_counter.seen


def test_the_pack_does_not_reach_the_registry_or_the_store(hosted_with_catalog):
    """A **lint, not a proof**: this pack must not name `service.kernel`,
    `service.store` or `registry.call`.

    Stated plainly because the review asked for it (finding m8). A source scan
    for three attribute spellings is defeated by `getattr(service, "kernel")`,
    by an alias, or by a helper in another module — it catches the change
    somebody writes by hand, not the one somebody hides. The *proof* that no
    anonymous request reaches execution is the runtime one above
    (`test_the_catalog_read_makes_no_kernel_calls`), which counts at the
    kernel's own door with a positive control. This test earns its place by
    failing at review time instead of at runtime, and by documenting the
    intent; it is not evidence on its own.

    `routes_packages.py` is a registry passthrough; this pack deliberately is
    not. A tool call would be a second code path to the kernel and a place for
    a future tool to acquire a side effect.
    """
    import ast
    import inspect

    from agentcad.server import routes_public

    tree = ast.parse(inspect.getsource(routes_public))
    reached = {
        f"{node.value.id}.{node.attr}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
    }
    # The AST, not the text: the module docstring *names* these on purpose.
    assert reached & {"service.kernel", "service.store", "registry.call"} == set(), \
        reached


# ----------------------------------------------------------------- robustness

def test_a_broken_index_does_not_take_the_anonymous_route_down(
        hosted_with_catalog, tmp_path):
    """`load_indexes` already refuses to let one broken index hide the others;
    the same must hold on read, on a route with no credential in front of it."""
    from agentcad import config as user_config
    from agentcad.core.packages import indexes as index_module

    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "index.json").write_text("{ not json", encoding="utf-8")
    user_config.save_config({"indexes": [
        {"name": "broken", "kind": "local", "path": str(broken),
         "scope": "public"}]})
    service = hosted_with_catalog.agentcad_service
    service.packages.reload_indexes()
    assert isinstance(service.packages.indexes[0], index_module.LocalIndex)

    response = hosted_with_catalog.get("/api/public/packages")
    assert response.status_code == 200
    assert "din625" in {p["name"] for p in response.json()["packages"]}


def test_with_no_index_configured_the_listing_is_empty_not_an_error(
        hosted_client):
    body = hosted_client.get("/api/public/packages")
    assert body.status_code == 200
    assert body.json() == {"packages": []}


# ---------------------------------------------------------------- local mode

def test_the_pack_is_mounted_in_local_mode_too_and_still_filters(
        kernel, tmp_path, monkeypatch):
    """Unlike `routes_auth`, this pack is not inert in local mode: it reads
    nothing but public catalog files and it is strictly narrower than
    `/api/packages/search`, which loopback already serves to anybody. The
    scope filter is the same code either way, and that is what is pinned —
    a filter that only ran when a `SecurityConfig` was installed would be a
    second code path nobody exercises.
    """
    from fastapi.testclient import TestClient

    from agentcad.cli import bundled_index_entries
    from agentcad.core.tools import build_registry
    from agentcad.server.app import create_app

    from .conftest import make_test_service

    monkeypatch.setenv("AGENTCAD_CONFIG", str(tmp_path / "cfg" / "config.json"))
    service = make_test_service(tmp_path / "projects", kernel)
    service.bundled_indexes = bundled_index_entries()
    app = create_app(service, build_registry(service),
                     extra_allowed_hosts={"testserver"})
    client = TestClient(app, base_url="http://testserver")
    client.agentcad_service = service

    assert "din625" in _packages(client)
    private_name = configure_private_index(client, tmp_path / "priv")
    assert private_name not in _packages(client)
