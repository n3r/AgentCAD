"""PRD-007 slice 4: the customizer rebuild and its caps.

``GET /s/<token>/variant`` is the first anonymous request that legitimately
reaches ``exec()`` in the kernel. Every containment property the design names
gets a test here, and — the maximal bar for an anonymous-facing kernel path —
each gets a **negation** that proves the wall is really load-bearing:

- the variant cache: a repeat is served from disk with **zero** kernel calls
  (AC2/AC7), and a positive control proves the counter is not simply stuck;
- param-validation parity: an out-of-range numeric **clamps** with a warning,
  a non-member enum and an unknown name are **refused before any build** (AC4),
  the negation being that the kernel counter never moves on the refusal;
- the global in-flight semaphore is actually consulted (a held slot forces a
  429; releasing it lets the very same request through — the positive control);
- the export mask: an allowed format streams, a disabled one **404s before the
  builder** (AC3), the negation being kernel silence on the 404;
- the escalation boundary: a ``customizer:false`` link 404s ``/variant``
  before the builder (AC6);
- the per-IP limit is keyed on the **resolved** address, so a forged
  ``X-Forwarded-For`` cannot mint a fresh bucket per request (the 005a M3
  lesson, negated);
- the owner's tree is byte-unchanged after a rebuild flood (AC5);
- the login-gate knob is off by default and bites when set.
"""

from __future__ import annotations

import os

import pytest

from agentcad.core import share_build
from agentcad.server import security

from .conftest import BOX_SCRIPT, TYPED_SCRIPT, login

# An enum whose choices are strings that look numeric — the m-3 regression: the
# query "1" must select the string choice "1", not coerce to int 1 and miss.
NUMSTR_ENUM_SCRIPT = '''\
PARAMS = {
    "size": {"default": 20.0, "min": 10.0, "max": 40.0},
    "mode": {"default": "1", "type": "enum", "choices": ["1", "2"]},
}
def build(p):
    from build123d import Box
    return Box(p.size, p.size, p.size)
'''


@pytest.fixture(autouse=True)
def _customizer_pool(monkeypatch):
    """PRD-007 finding M-1: the customizer reserves one worker for members, so
    it needs a kernel pool of >=2. The session ``kernel`` fixture is a single
    client, so pin the DECLARED pool size (the source ``effective_max_inflight``
    reads) high enough that these tests exercise the customizer deterministically
    regardless of the host core count. Tests that probe the single-worker
    refusal override this back to 1."""
    monkeypatch.setenv("AGENTCAD_KERNEL_POOL_SIZE", "4")


# --------------------------------------------------------------- publishing

def _publish(client, *, script=TYPED_SCRIPT, part="widget", customizer=True,
             exports=("step",)):
    """Publish *script* at a tag and return ``(token, pub_id)`` for an
    anonymous visitor (the cookie is cleared)."""
    login(client)
    client.post("/api/projects", json={"name": "demo"})
    r = client.post("/api/projects/demo/parts",
                    json={"id": part, "script": script})
    assert r.status_code == 201, r.text
    assert client.post("/api/projects/demo/versions",
                       json={"name": "v1"}).status_code in (200, 201)
    r = client.post("/api/share", json={
        "project": "demo", "part_id": part, "ref": "v1",
        "customizer": customizer, "exports": list(exports)})
    assert r.status_code == 201, r.text
    token = r.json()["url"].rsplit("/s/", 1)[1]
    pub_id = r.json()["pub_id"]
    client.cookies.clear()
    return token, pub_id


@pytest.fixture
def share_link(hosted):
    client, _ = hosted
    token, pub_id = _publish(client)
    return client, token, pub_id


@pytest.fixture
def viewer_link(hosted):
    client, _ = hosted
    token, pub_id = _publish(client, customizer=False, exports=())
    return client, token, pub_id


def _owner_snapshot(client):
    """Bytes of the owner project's manifest + parts + cache + history — the
    tree a visitor build must never touch (AC5/AC8, a property of
    construction)."""
    root = client.agentcad_service.store.canonical_path_of("demo")
    snap = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            snap[str(path.relative_to(root))] = path.read_bytes()
    return snap


# --------------------------------------------------- variant cache (AC2/AC7)

def test_a_fresh_variant_builds_once_and_a_repeat_is_cached(share_link,
                                                            kernel_counter):
    """AC2/AC7: exactly one kernel build for two identical requests, and a
    positive control proves the counter moves for a genuinely new param set."""
    client, token, _ = share_link
    before = kernel_counter.calls

    a = client.get(f"/s/{token}/variant", params={"size": 25})
    assert a.status_code == 200, a.text
    assert a.json()["metrics"]["mass_g"] > 0
    after_one = kernel_counter.calls
    assert after_one > before                       # it really built

    b = client.get(f"/s/{token}/variant", params={"size": 25})
    assert b.status_code == 200
    assert b.json()["mesh_key"] == a.json()["mesh_key"]
    assert kernel_counter.calls == after_one        # the repeat did NOT build

    # Positive control: a different param set is a fresh build, so the zero
    # above is a cache hit and not a broken counter.
    c = client.get(f"/s/{token}/variant", params={"size": 30})
    assert c.status_code == 200
    assert c.json()["mesh_key"] != a.json()["mesh_key"]
    assert kernel_counter.calls > after_one


# ------------------------------------------------------ param parity (AC4)

def test_out_of_range_numeric_clamps_with_a_warning(share_link):
    client, token, _ = share_link
    r = client.get(f"/s/{token}/variant", params={"size": 100000})  # max is 40
    assert r.status_code == 200, r.text
    assert any("clamp" in w.lower() for w in r.json()["warnings"]), r.json()


def test_a_non_member_enum_is_refused(share_link):
    client, token, _ = share_link
    r = client.get(f"/s/{token}/variant", params={"grade": "not-a-member"})
    assert r.status_code == 422
    assert r.json()["error"]["type"] == "ValidationError"


def test_an_unknown_param_never_reaches_the_builder(share_link, kernel_counter):
    """The negation of param parity: a name outside the published spec is a
    422 and the kernel counter does not move — it is refused before build."""
    client, token, _ = share_link
    before = kernel_counter.calls
    r = client.get(f"/s/{token}/variant", params={"nope": 1})
    assert r.status_code == 422
    assert kernel_counter.calls == before, kernel_counter.seen


def test_a_non_numeric_value_for_a_number_param_is_refused(share_link):
    client, token, _ = share_link
    r = client.get(f"/s/{token}/variant", params={"size": "huge"})
    assert r.status_code == 422


# --------------------------------------------- the in-flight semaphore cap

def test_the_inflight_semaphore_is_consulted_with_a_positive_control(
        share_link, monkeypatch):
    """With the global cap at 1, a request that finds the slot already held is
    bounded (429); releasing it lets the *same* request through. The positive
    control is that after release the build succeeds, proving the 429 was the
    cap and not a broken route."""
    client, token, _ = share_link
    monkeypatch.setenv("AGENTCAD_SHARE_MAX_INFLIGHT", "1")

    sem = share_build.inflight_semaphore()
    assert sem.acquire(blocking=False)              # take the only slot
    try:
        bounded = client.get(f"/s/{token}/variant", params={"size": 21})
        assert bounded.status_code == 429, bounded.text
        assert bounded.json()["error"]["details"]["retry_after_s"] > 0
    finally:
        sem.release()

    # Positive control: the slot is free now, so the identical request builds.
    freed = client.get(f"/s/{token}/variant", params={"size": 21})
    assert freed.status_code == 200, freed.text


def test_the_cap_is_actually_the_env_knob(monkeypatch):
    """A negation for the cap itself: the semaphore's size follows the knob, so
    a test that holds 'the only slot' is really holding the only slot."""
    monkeypatch.setenv("AGENTCAD_SHARE_MAX_INFLIGHT", "1")
    one = share_build.inflight_semaphore()
    assert one.acquire(blocking=False)
    assert not one.acquire(blocking=False)          # cap 1: no second slot
    one.release()
    monkeypatch.setenv("AGENTCAD_SHARE_MAX_INFLIGHT", "3")
    three = share_build.inflight_semaphore()
    assert three is not one                          # rebuilt for the new size
    got = [three.acquire(blocking=False) for _ in range(3)]
    assert got == [True, True, True]
    assert not three.acquire(blocking=False)         # cap 3
    for _ in range(3):
        three.release()


# --------------------------------------------- per-link rate limit (AC5)

def test_over_the_per_link_limit_is_quota_exceeded_with_retry_after(share_link):
    """Hammering a single link past its bucket returns a 429 carrying
    ``retry_after_s`` so the page can degrade to view-only. Identical params
    keep it fast: one build, then cache hits, until the bucket empties."""
    codes = []
    client, token, _ = share_link
    for _ in range(40):
        r = client.get(f"/s/{token}/variant", params={"size": 22})
        codes.append(r.status_code)
        if r.status_code == 429:
            assert r.json()["error"]["details"]["retry_after_s"] > 0
    assert 429 in codes
    assert 200 in codes                              # the first ones went through


# -------------------------------------------------- per-IP honesty (M3)

def test_the_per_ip_limit_ignores_a_forged_x_forwarded_for(share_link):
    """The 005a M3 lesson, negated: the per-IP bucket keys on the address the
    proxy layer resolved (``request.client.host``), NOT a header the visitor
    controls. A rotating ``X-Forwarded-For`` must not mint a fresh bucket per
    request, or the per-IP cap would be a fiction."""
    client, token, _ = share_link
    codes = []
    for i in range(40):
        r = client.get(f"/s/{token}/variant", params={"size": 23},
                       headers={"X-Forwarded-For": f"10.0.0.{i}"})
        codes.append(r.status_code)
    # If the handler trusted the forged header, every request would be a new
    # bucket and none would be limited. It is limited, so it keys on the
    # resolved address.
    assert 429 in codes


# ------------------------------------------------------ export mask (AC3)

def test_the_export_mask_allows_step_and_404s_stl(share_link):
    client, token, _ = share_link
    ok = client.get(f"/s/{token}/download/step", params={"size": 24})
    assert ok.status_code == 200, ok.text
    assert ok.headers["content-type"] not in ("application/json",)
    assert "widget_" in ok.headers.get("content-disposition", "")

    disabled = client.get(f"/s/{token}/download/stl", params={"size": 24})
    assert disabled.status_code == 404


def test_a_disabled_export_404s_before_the_builder(share_link, kernel_counter):
    """The negation of the export mask: a format outside the mask never reaches
    the kernel — the 404 is structural, before any build."""
    client, token, _ = share_link
    before = kernel_counter.calls
    disabled = client.get(f"/s/{token}/download/stl", params={"size": 99})
    assert disabled.status_code == 404
    assert kernel_counter.calls == before, kernel_counter.seen


def test_a_repeat_download_is_a_content_addressed_cache_read(share_link,
                                                             kernel_counter):
    """A download is content-addressed, so the second identical download is a
    file read — no second export build."""
    client, token, _ = share_link
    first = client.get(f"/s/{token}/download/step", params={"size": 26})
    assert first.status_code == 200
    mid = kernel_counter.calls
    second = client.get(f"/s/{token}/download/step", params={"size": 26})
    assert second.status_code == 200
    assert second.content == first.content
    assert kernel_counter.calls == mid              # cache read, no kernel


# ------------------------------------------ the escalation boundary (AC6)

def test_a_viewer_only_link_cannot_rebuild(viewer_link, kernel_counter):
    """``customizer:false`` 404s ``/variant`` before the builder — the bit
    lives in the owner-written record, not the request, so there is no request
    shape that turns it on."""
    client, token, _ = viewer_link
    before = kernel_counter.calls
    r = client.get(f"/s/{token}/variant", params={"size": 20})
    assert r.status_code == 404
    assert kernel_counter.calls == before, kernel_counter.seen


def test_a_viewer_only_link_cannot_download(viewer_link, kernel_counter):
    client, token, _ = viewer_link
    before = kernel_counter.calls
    r = client.get(f"/s/{token}/download/step", params={"size": 20})
    assert r.status_code == 404
    assert kernel_counter.calls == before, kernel_counter.seen


# ---------------------------------------------------- owner isolation (AC5)

def test_the_owner_tree_is_byte_unchanged_after_a_flood(share_link):
    client, token, _ = share_link
    before = _owner_snapshot(client)
    for i in range(20):
        client.get(f"/s/{token}/variant", params={"size": 10 + i})
    assert _owner_snapshot(client) == before


# ------------------------------------------------ unknown-token safety

def test_variant_and_download_404_a_bad_token_without_building(hosted,
                                                              kernel_counter):
    client, _ = hosted
    _publish(client)                                # ensure the builder exists
    before = kernel_counter.calls
    bogus = "shr_deadbeef_" + "x" * 43
    assert client.get(f"/s/{bogus}/variant",
                      params={"size": 20}).status_code == 404
    assert client.get(f"/s/{bogus}/download/step",
                      params={"size": 20}).status_code == 404
    assert kernel_counter.calls == before, kernel_counter.seen


# ------------------------------------------------ the login-gate knob

def test_the_login_gate_is_off_by_default(share_link):
    """Off by default: many anonymous rebuilds are allowed (bounded only by the
    buckets), never a 401."""
    client, token, _ = share_link
    assert "AGENTCAD_SHARE_REQUIRE_LOGIN_ABOVE" not in os.environ
    codes = {client.get(f"/s/{token}/variant", params={"size": 20}).status_code
             for _ in range(3)}
    assert 401 not in codes


def test_the_login_gate_bites_an_anonymous_flood_when_set(share_link,
                                                          monkeypatch):
    """Set to N, the N+1-th anonymous rebuild from one IP is a 401 — a login
    wall on a link under attack, without taking the viewer offline."""
    client, token, _ = share_link
    monkeypatch.setenv("AGENTCAD_SHARE_REQUIRE_LOGIN_ABOVE", "2")
    codes = [client.get(f"/s/{token}/variant",
                        params={"size": 20}).status_code for _ in range(4)]
    assert 401 in codes
    # A signed-in member is never gated.
    login(client)
    assert client.get(f"/s/{token}/variant",
                      params={"size": 20}).status_code in (200, 429)


# ---------------------------- the member-worker reservation (finding M-1)

def test_the_inflight_cap_reserves_a_member_worker(monkeypatch):
    """The containment wall for M-1: the anonymous in-flight cap is clamped to
    ``pool_size - 1``, so it can NEVER occupy every worker — a member always has
    one free. On a single-worker pool it clamps to 0 (the customizer refuses)."""
    cases = [("1", None, 0), ("2", None, 1), ("2", "5", 1),
             ("3", "2", 2), ("4", "10", 3), ("8", None, 2)]
    for pool, ceiling, want in cases:
        monkeypatch.setenv("AGENTCAD_KERNEL_POOL_SIZE", pool)
        if ceiling is None:
            monkeypatch.delenv("AGENTCAD_SHARE_MAX_INFLIGHT", raising=False)
        else:
            monkeypatch.setenv("AGENTCAD_SHARE_MAX_INFLIGHT", ceiling)
        eff = share_build.effective_max_inflight()
        assert eff == want, (pool, ceiling, eff)
        assert eff <= max(0, int(pool) - 1)         # a worker is always spared


def test_a_single_worker_pool_refuses_the_customizer_cleanly(share_link,
                                                             kernel_counter,
                                                             monkeypatch):
    """On a 1-worker pool the customizer would starve members, so ``/variant``
    and ``/download`` return a structured 503 naming the pool-size knob and make
    NO build — while the kernel-free viewer surface still works (M-1)."""
    monkeypatch.setenv("AGENTCAD_KERNEL_POOL_SIZE", "1")
    client, token, _ = share_link
    before = kernel_counter.calls

    v = client.get(f"/s/{token}/variant", params={"size": 25})
    assert v.status_code == 503, v.text
    assert v.json()["error"]["type"] == "ServiceUnavailableError"
    assert "AGENTCAD_KERNEL_POOL_SIZE" in v.json()["error"]["message"]
    assert v.json()["error"]["details"]["kernel_pool_size"] == 1

    d = client.get(f"/s/{token}/download/step", params={"size": 25})
    assert d.status_code == 503
    assert kernel_counter.calls == before           # neither built

    # Viewer links are kernel-free and unaffected on a 1-worker pool.
    assert client.get(f"/s/{token}/model").status_code == 200
    assert client.get(f"/s/{token}/params").status_code == 200


# ------------------------- clamp-equal cache coalescing (finding M-2)

def test_clamp_equal_out_of_range_values_coalesce_to_one_build(share_link,
                                                              kernel_counter):
    """The M-2 probe: five out-of-range values that all clamp to max=40 make ONE
    build and one cache key — the variant cache keys on the CLAMPED params, so a
    clamp flood is a cache hit, not a fresh build each time. Each still warns."""
    client, token, _ = share_link
    before = kernel_counter.calls
    keys = []
    for size in (100000, 100001, 100002, 100003, 100004):
        r = client.get(f"/s/{token}/variant", params={"size": size})
        assert r.status_code == 200, r.text
        assert any("clamp" in w.lower() for w in r.json()["warnings"]), r.json()
        keys.append(r.json()["mesh_key"])
    assert len(set(keys)) == 1                       # coalesced
    assert kernel_counter.calls == before + 1        # exactly one build


def test_a_genuinely_distinct_in_range_set_still_builds(share_link,
                                                       kernel_counter):
    """The positive control for M-2: distinct IN-RANGE values are distinct
    geometry and still build — the coalescing above is clamp-equality, not a
    broken cache that fuses everything."""
    client, token, _ = share_link
    before = kernel_counter.calls
    keys = {client.get(f"/s/{token}/variant",
                       params={"size": s}).json()["mesh_key"]
            for s in (11, 21, 31)}
    assert len(keys) == 3
    assert kernel_counter.calls == before + 3


# ------------------------- NaN guard (finding m-2)

def test_a_nan_numeric_is_refused_before_the_kernel(share_link, kernel_counter):
    """``size=nan`` slips past ``value<mn`` and ``value>mx`` (both False for
    NaN), so without a guard it reaches ``build(p)`` and returns a degenerate
    200. It is now a 422 before any kernel call (m-2)."""
    client, token, _ = share_link
    before = kernel_counter.calls
    r = client.get(f"/s/{token}/variant", params={"size": "nan"})
    assert r.status_code == 422, r.text
    assert r.json()["error"]["type"] == "ValidationError"
    assert kernel_counter.calls == before            # never reached the kernel


def test_inf_still_clamps_and_is_not_rejected(share_link):
    """The negation of the NaN guard: ``inf`` is finite-enough to clamp to max
    (``inf > mx`` is True), so it stays a 200-with-warning, not a 422."""
    client, token, _ = share_link
    r = client.get(f"/s/{token}/variant", params={"size": "inf"})
    assert r.status_code == 200, r.text
    assert any("clamp" in w.lower() for w in r.json()["warnings"])


# ------------------------- numeric-string enum choices (finding m-3)

def test_a_numeric_string_enum_choice_is_selectable(hosted):
    """m-3: an enum whose choices are strings that look numeric (``"1"``,
    ``"2"``) must be selectable — the query ``"1"`` selects the string choice
    rather than coercing to int 1 and being refused as a non-member."""
    client, _ = hosted
    token, _ = _publish(client, script=NUMSTR_ENUM_SCRIPT, part="widget",
                        exports=())
    r = client.get(f"/s/{token}/variant", params={"mode": "2", "size": 20})
    assert r.status_code == 200, r.text


# ------------------------------------------------ the anonymous surface

def test_the_customizer_routes_are_the_public_surface():
    assert security.is_public("/s/{token}/variant")
    assert security.is_public("/s/{token}/download/{fmt}")
