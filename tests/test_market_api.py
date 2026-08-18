"""PRD-031a slice 1: the public browse/search API, kernel-free.

Three new routes on the anonymous surface, all reading only the pre-generated
``index.json`` digest and shipped assets — no kernel, no network, public-scoped:

- ``GET /api/public/packages/search`` — ``search.search`` over
  :func:`_public_indexes`, ``refresh=False`` (PRD-005a's M2 no-network rule) with
  the PRD-031a ``license`` facet;
- ``GET .../versions/{version}/script/{part}`` — the read-only ``.py`` text;
- ``GET .../versions/{version}/params/{part}`` — the digest param list.

The whole point of the pack, exactly as PRD-005a's: a private index is never
scored, never listed, and its miss is byte-identical to a nonexistent package.
"""

from __future__ import annotations

import types

import pytest

from agentcad.core.packages import search as pkgsearch

from .conftest import configure_private_index, login

CATALOG = "agentcad-core"


# ----------------------------------------------------- search.py: refresh/license

def _fake_index(name, doc, *, scope="public"):
    """A minimal index whose ``refresh`` records that it was called."""
    calls = {"refresh": 0}

    def refresh():
        calls["refresh"] += 1

    idx = types.SimpleNamespace(
        name=name, kind="local", scope=scope, configured_scope=scope,
        stale=False, stale_reason=None,
        refresh=refresh, entries=lambda: doc, _calls=calls)
    return idx


_DOC = {"packages": {
    "apache_part": {"versions": {"1.0.0": {
        "license": "Apache-2.0", "summary": "an apache part",
        "keywords": ["cots"], "standards": ["ISO 4762"]}}},
    "mit_part": {"versions": {"1.0.0": {
        "license": "MIT", "summary": "an mit part", "keywords": ["cots"]}}},
}}


def test_refresh_false_makes_no_refresh_call():
    """The anonymous path passes ``refresh=False`` — PRD-005a forbids a network
    fetch with no credential in front of it (the M2 discipline)."""
    idx = _fake_index("core", _DOC)
    pkgsearch.search([idx], refresh=False)
    assert idx._calls["refresh"] == 0


def test_refresh_defaults_true_for_authenticated_callers():
    """The default is unchanged, so every existing caller keeps refreshing."""
    idx = _fake_index("core", _DOC)
    pkgsearch.search([idx])
    assert idx._calls["refresh"] == 1


def test_license_filter_is_an_and_filter():
    idx = _fake_index("core", _DOC)
    hits = pkgsearch.search([idx], license="Apache-2.0", refresh=False)["hits"]
    names = {h["name"] for h in hits}
    assert names == {"apache_part"}
    assert any(w.startswith("license:") for h in hits for w in h["why"])


def test_license_filter_is_case_insensitive():
    idx = _fake_index("core", _DOC)
    hits = pkgsearch.search([idx], license="apache-2.0", refresh=False)["hits"]
    assert {h["name"] for h in hits} == {"apache_part"}


def test_license_filter_excludes_the_rest():
    idx = _fake_index("core", _DOC)
    hits = pkgsearch.search([idx], license="MIT", refresh=False)["hits"]
    assert {h["name"] for h in hits} == {"mit_part"}


# ------------------------------------------------------ the search route

def _search(client, **params):
    return client.get("/api/public/packages/search", params=params)


def test_search_returns_public_hits_with_why(hosted_with_catalog):
    r = _search(hosted_with_catalog, q="bearing")
    assert r.status_code == 200
    hits = r.json()["hits"]
    names = {h["name"] for h in hits}
    assert "din625" in names
    assert all(h["why"] for h in hits)


def test_search_is_declared_before_name_so_it_is_not_a_package_lookup(
        hosted_with_catalog):
    """The route-order gotcha: ``/search`` must not bind ``{name} == 'search'``
    — a 200 search body, never a 404 for a package called ``search``."""
    r = _search(hosted_with_catalog)
    assert r.status_code == 200
    assert "hits" in r.json()


def test_search_by_standard_and_license(hosted_with_catalog):
    r = _search(hosted_with_catalog, license="Apache-2.0")
    assert r.status_code == 200
    hits = r.json()["hits"]
    assert hits
    assert all("apache" in (h["license"] or "").lower() for h in hits)


def test_search_by_param_range(hosted_with_catalog):
    """The digest param-range facet: NEMA-17 declares ``body_length`` 20..60."""
    r = _search(hosted_with_catalog, param="body_length",
                param_min=30, param_max=40)
    names = {h["name"] for h in r.json()["hits"]}
    assert "nema17" in names


def test_search_is_cacheable(hosted_with_catalog):
    r = _search(hosted_with_catalog, q="bearing")
    assert r.headers["cache-control"] == "public, max-age=300"


def test_search_never_scores_a_private_index(hosted_with_private):
    client, private_name = hosted_with_private
    hits = _search(client, q=private_name).json()["hits"]
    assert private_name not in {h["name"] for h in hits}
    # The negation: the public catalog is still searchable beside it.
    assert "din625" in {h["name"] for h in _search(client).json()["hits"]}


def test_the_authenticated_search_still_sees_private(hosted_with_private):
    """The private index is not disabled — a member's own search finds it."""
    client, private_name = hosted_with_private
    login(client)
    hits = client.get("/api/packages/search").json()["hits"]
    assert private_name in {h["name"] for h in hits}


# ------------------------------------------------------ the script route

def test_script_serves_the_readonly_part_text(hosted_with_catalog):
    r = hosted_with_catalog.get(
        "/api/public/packages/nema17/versions/1.0.0/script/motor")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert r.headers["cache-control"] == "public, max-age=300"
    assert "def build" in r.text


def test_script_of_an_undeclared_part_is_a_miss(hosted_with_catalog):
    a = hosted_with_catalog.get(
        "/api/public/packages/nema17/versions/1.0.0/script/nope")
    b = hosted_with_catalog.get(
        "/api/public/packages/nope/versions/1.0.0/script/motor")
    assert a.status_code == b.status_code == 404
    assert a.json() == b.json()


def test_script_of_a_private_part_is_indistinguishable(hosted_with_private):
    client, private_name = hosted_with_private
    mine = client.get(
        f"/api/public/packages/{private_name}/versions/1.0.0/script/ball_bearing")
    missing = client.get(
        "/api/public/packages/does-not-exist/versions/1.0.0/script/ball_bearing")
    assert mine.status_code == missing.status_code == 404
    assert mine.json() == missing.json()


# ------------------------------------------------------ the params route

def test_params_returns_the_digest_list(hosted_with_catalog):
    r = hosted_with_catalog.get(
        "/api/public/packages/nema17/versions/1.0.0/params/motor")
    assert r.status_code == 200
    body = r.json()
    assert body["part"] == "motor"
    names = {p["name"] for p in body["params"]}
    assert "body_length" in names
    spec = next(p for p in body["params"] if p["name"] == "body_length")
    assert spec["type"] == "number" and spec["min"] == 20.0 and spec["max"] == 60.0


def test_params_is_cacheable(hosted_with_catalog):
    r = hosted_with_catalog.get(
        "/api/public/packages/nema17/versions/1.0.0/params/motor")
    assert r.headers["cache-control"] == "public, max-age=300"


def test_params_of_an_undeclared_part_is_a_miss(hosted_with_catalog):
    r = hosted_with_catalog.get(
        "/api/public/packages/nema17/versions/1.0.0/params/nope")
    assert r.status_code == 404


# ------------------------------------------------------ kernel silence

def test_the_new_routes_make_no_kernel_calls(hosted_with_catalog, kernel_counter):
    """AC2 at this pack's door — a positive control lives in
    ``test_hosted_surface.test_the_kernel_counter_actually_counts``."""
    before = kernel_counter.calls
    for path in (
        "/api/public/packages/search?q=bearing",
        "/api/public/packages/search?license=Apache-2.0",
        "/api/public/packages/nema17/versions/1.0.0/script/motor",
        "/api/public/packages/nema17/versions/1.0.0/params/motor",
    ):
        assert hosted_with_catalog.get(path).status_code == 200, path
    assert kernel_counter.calls == before, kernel_counter.seen


# ------------------------------------------------------ OCP-free (AC8)

def test_the_market_data_modules_are_ocp_free():
    """The browse/search/script/params surface imports no OCP/build123d, proven
    in a fresh interpreter with OCP blocked at ``sys.meta_path`` (the
    ``test_packages_ocp_free`` pattern)."""
    import subprocess
    import sys

    code = (
        "import importlib, sys\n"
        "class _Blocked:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name.split('.')[0] in ('OCP', 'build123d'):\n"
        "            raise ImportError('blocked kernel import: ' + name)\n"
        "        return None\n"
        "sys.meta_path.insert(0, _Blocked())\n"
        "importlib.import_module('agentcad.server.routes_public')\n"
        "importlib.import_module('agentcad.core.packages.search')\n"
        "assert 'OCP' not in sys.modules and 'build123d' not in sys.modules\n"
        "print('ok')\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().endswith("ok")
