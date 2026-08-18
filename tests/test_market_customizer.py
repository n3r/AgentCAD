"""PRD-031a slice 2: the listing customizer — the ONE anonymous kernel path.

The whole risk of the slice, and its negations. The market variant/download
reach ``exec()`` through PRD-007's containment *reused verbatim*
(``build_catalog_variant`` → the shared ``_variant`` tail), not a second set of
limits:

- the variant cache: a second visitor at the same params is served from disk
  with **zero** extra kernel calls (AC4) — exactly one build for two requests;
- clamp-equal coalescing: two out-of-range values that clamp to the same
  geometry are one build (finding M-2, inherited);
- the pool reservation: a single-worker pool 503s naming
  ``AGENTCAD_KERNEL_POOL_SIZE`` and makes NO build (finding M-1);
- the shared per-IP bucket: the SAME object as ``/s/``, so a drained address
  refuses on both surfaces (AC4 — no double allowance);
- the export mask: a format outside ``{step, stl, 3mf}`` 404s **before** the
  builder (kernel silence on the 404);
- ``scope: public`` only: a private listing's variant is one indistinguishable
  ``_miss``.
"""

from __future__ import annotations

import pytest

from agentcad.core import share_build

from .conftest import configure_private_index

NEMA_VARIANT = ("/api/public/packages/nema17/versions/1.0.0/parts/motor/variant")
NEMA_DOWNLOAD = ("/api/public/packages/nema17/versions/1.0.0/parts/motor/"
                 "download")


@pytest.fixture(autouse=True)
def _market_pool(monkeypatch):
    """The customizer reserves one worker for members, so it needs a declared
    kernel pool of >=2. The session ``kernel`` fixture is a single client, so
    pin the DECLARED pool size high (the source ``effective_max_inflight``
    reads); tests that probe the single-worker refusal override it back to 1."""
    monkeypatch.setenv("AGENTCAD_KERNEL_POOL_SIZE", "4")


# ------------------------------------------------------------ AC1 end-to-end

def test_ac1_anonymous_search_customize_download(hosted_with_catalog):
    """A logged-out visitor searches, reads the params, rebuilds a variant and
    downloads a STEP — no catalog code ran on their machine (it ran only in our
    server-side kernel)."""
    client = hosted_with_catalog
    assert not client.cookies

    hits = client.get("/api/public/packages/search",
                      params={"q": "nema"}).json()["hits"]
    assert "nema17" in {h["name"] for h in hits}

    params = client.get(
        "/api/public/packages/nema17/versions/1.0.0/params/motor").json()
    assert any(p["name"] == "body_length" for p in params["params"])

    v = client.get(NEMA_VARIANT, params={"body_length": 40})
    assert v.status_code == 200, v.text
    body = v.json()
    assert body["mesh_key"]
    assert body["metrics"] is not None

    d = client.get(f"{NEMA_DOWNLOAD}/step", params={"body_length": 40})
    assert d.status_code == 200, d.text
    assert d.content[:4] in (b"ISO-", b"ISO ") or b"STEP" in d.content[:200]


def test_variant_is_not_cacheable_but_the_bytes_flow(hosted_with_catalog):
    v = hosted_with_catalog.get(NEMA_VARIANT, params={"body_length": 30})
    assert v.status_code == 200
    assert v.headers.get("cache-control", "no-store") == "no-store" or \
        "cache-control" not in v.headers


# ------------------------------------------------------ AC4 cache coalescing

def test_ac4_a_repeat_variant_is_one_build_for_two_requests(
        hosted_with_catalog, kernel_counter):
    """The content-addressed variant cache: a second visitor at the same params
    is a disk read — zero extra kernel calls."""
    client = hosted_with_catalog
    first = client.get(NEMA_VARIANT, params={"body_length": 44})
    assert first.status_code == 200, first.text
    assert first.json()["cached"] is False
    mid = kernel_counter.calls
    second = client.get(NEMA_VARIANT, params={"body_length": 44})
    assert second.status_code == 200
    assert second.json()["cached"] is True
    assert kernel_counter.calls == mid, kernel_counter.seen
    assert second.json()["mesh_key"] == first.json()["mesh_key"]


def test_ac4_out_of_range_values_coalesce_at_the_clamp(
        hosted_with_catalog, kernel_counter):
    """Two different out-of-range body lengths both clamp to max (60), so they
    are one geometry, one cache key, one build (finding M-2 inherited)."""
    client = hosted_with_catalog
    a = client.get(NEMA_VARIANT, params={"body_length": 100000})
    assert a.status_code == 200, a.text
    mid = kernel_counter.calls
    b = client.get(NEMA_VARIANT, params={"body_length": 99999})
    assert b.status_code == 200
    assert kernel_counter.calls == mid, kernel_counter.seen
    assert a.json()["mesh_key"] == b.json()["mesh_key"]
    assert any("clamp" in w for w in a.json()["warnings"])


# ------------------------------------------------ AC4 the pool reservation

def test_ac4_single_worker_pool_refuses_cleanly(hosted_with_catalog,
                                                kernel_counter, monkeypatch):
    """On a 1-worker pool the customizer would starve members, so both routes
    return a structured 503 naming the pool knob and make NO build."""
    monkeypatch.setenv("AGENTCAD_KERNEL_POOL_SIZE", "1")
    client = hosted_with_catalog
    before = kernel_counter.calls

    v = client.get(NEMA_VARIANT, params={"body_length": 25})
    assert v.status_code == 503, v.text
    assert v.json()["error"]["type"] == "ServiceUnavailableError"
    assert "AGENTCAD_KERNEL_POOL_SIZE" in v.json()["error"]["message"]

    d = client.get(f"{NEMA_DOWNLOAD}/step", params={"body_length": 25})
    assert d.status_code == 503
    assert kernel_counter.calls == before          # neither built


# ------------------------------------------ AC4 the shared per-IP bucket

def test_ac4_the_per_ip_bucket_is_the_same_object_as_slash_s(
        hosted_with_catalog):
    """The defining property: the market and ``/s/`` share ONE per-IP bucket via
    ``service.customizer_guard``, so a visitor cannot double their allowance. We
    prove it by draining the shared bucket for this client's address and showing
    BOTH surfaces refuse from it."""
    from .test_share_customizer import _publish

    client = hosted_with_catalog
    token, _pub = _publish(client)                 # a live share link; cookies cleared
    service = client.agentcad_service
    share_build.ensure_share(service)
    guard = service.customizer_guard
    assert isinstance(guard, share_build.CustomizerGuard)

    addr = "testclient"                            # TestClient's request.client.host
    for _ in range(int(share_build.SHARE_BURST) + 3):
        guard.addr_rate.take(f"addr:{addr}")       # drain the SHARED bucket

    market = client.get(NEMA_VARIANT, params={"body_length": 40})
    share = client.get(f"/s/{token}/variant", params={"size": 20})
    assert market.status_code == 429, market.text
    assert share.status_code == 429, share.text


def test_the_guard_is_one_object_across_the_surfaces(hosted_with_catalog):
    """The identity assertion, directly: one guard on the service, and it is a
    ``CustomizerGuard`` — not one bucket per pack."""
    service = hosted_with_catalog.agentcad_service
    share_build.ensure_share(service)
    g1 = service.customizer_guard
    # A market request and a repeated ensure_share must not replace it.
    hosted_with_catalog.get(NEMA_VARIANT, params={"body_length": 33})
    share_build.ensure_share(service)
    assert service.customizer_guard is g1


# ------------------------------------------------ the export mask (AC/FR6)

def test_a_format_outside_the_fixed_set_404s_before_the_builder(
        hosted_with_catalog, kernel_counter):
    """``{step, stl, 3mf}`` is the whole set; a format outside it is a ``_miss``
    before any build (kernel silence on the 404)."""
    client = hosted_with_catalog
    before = kernel_counter.calls
    for fmt in ("obj", "iges", "gltf", "exe"):
        r = client.get(f"{NEMA_DOWNLOAD}/{fmt}", params={"body_length": 40})
        assert r.status_code == 404, fmt
    assert kernel_counter.calls == before, kernel_counter.seen


def test_every_allowed_format_downloads(hosted_with_catalog):
    client = hosted_with_catalog
    for fmt in ("step", "stl", "3mf"):
        r = client.get(f"{NEMA_DOWNLOAD}/{fmt}", params={"body_length": 40})
        assert r.status_code == 200, (fmt, r.text[:200])


# ---------------------------------------------------- param parity (no build)

def test_an_out_of_spec_param_never_reaches_build(hosted_with_catalog,
                                                  kernel_counter):
    """An unknown parameter name is refused by ``normalize_params`` before any
    build — the kernel counter never moves."""
    client = hosted_with_catalog
    before = kernel_counter.calls
    r = client.get(NEMA_VARIANT, params={"no_such_param": 5})
    assert r.status_code == 422, r.text
    assert r.json()["error"]["type"] == "ValidationError"
    assert kernel_counter.calls == before, kernel_counter.seen


# ------------------------------------------------------ scope: public only

def test_a_private_listings_variant_is_indistinguishable(hosted_with_private):
    """A private index never surfaces a variant; its miss is byte-identical to a
    nonexistent package (no oracle)."""
    client, private_name = hosted_with_private
    mine = client.get(
        f"/api/public/packages/{private_name}/versions/1.0.0/parts/"
        "ball_bearing/variant", params={"designation": "625"})
    missing = client.get(
        "/api/public/packages/does-not-exist/versions/1.0.0/parts/"
        "ball_bearing/variant", params={"designation": "625"})
    assert mine.status_code == missing.status_code == 404
    assert mine.json() == missing.json()


def test_an_undeclared_part_variant_is_a_miss(hosted_with_catalog):
    r = hosted_with_catalog.get(
        "/api/public/packages/nema17/versions/1.0.0/parts/nope/variant",
        params={"body_length": 40})
    assert r.status_code == 404
