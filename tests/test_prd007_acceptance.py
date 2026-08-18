"""PRD-007 acceptance — share links & the customizer, AC1–AC9.

One test per criterion, each naming it. The criteria are graded against the real
guard, the real publication store, the muzzled build service and the shipped
frontend — never a stub built for the occasion. Two of them are browser ACs:

* **AC1 (the logged-out page) and AC7 (the embed) are partly a browser session,
  and a test cannot be one.** Their *contract* halves — the shell serves with no
  cookie, `/model` renders metrics + attribution with zero kernel calls, the
  embed carries `frame-ancestors *` while the app carries `'none'` — are driven
  here for real. Their *visual* halves were **never rendered by a browser**
  (`list_connected_browsers` → `[]`, the PRD-005a AC3 precedent).
  `test_ac1_and_ac7_browser_halves_are_recorded_as_unverified` asserts the
  record says so, which is the opposite of a pass.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import pytest

from agentcad.core import share_build
from agentcad.server import security

from .conftest import BOX_SCRIPT, TYPED_SCRIPT, flatten_routes, login

REPO = Path(__file__).resolve().parents[1]
CHANGELOG = REPO / "docs" / "changelog"
FRONTEND = REPO / "frontend"
PRD_NAME = "PRD-007-share-links-customizer.md"


def _find_prd() -> Path:
    """Locate the PRD wherever it currently lives — a PRD moves stage at
    *merge*, not when the build finishes, so a hard-coded directory is red for
    the whole review window (the PRD-010 close-out trap, changelog 0164)."""
    prd_root = REPO / "docs" / "prd"
    for stage in ("in-progress", "completed", "pending"):
        candidate = prd_root / stage / PRD_NAME
        if candidate.is_file():
            return candidate
    found = sorted(prd_root.rglob(PRD_NAME))
    assert found, f"{PRD_NAME} is not anywhere under {prd_root}"
    return found[0]


PRD = _find_prd()


def _publish(client, *, script=TYPED_SCRIPT, part="widget", customizer=True,
             exports=("step",), **settings):
    login(client)
    client.post("/api/projects", json={"name": "demo"})
    assert client.post("/api/projects/demo/parts",
                       json={"id": part, "script": script}).status_code == 201
    assert client.post("/api/projects/demo/versions",
                       json={"name": "v1"}).status_code in (200, 201)
    body = dict(project="demo", part_id=part, ref="v1", customizer=customizer,
                exports=list(exports))
    body.update(settings)
    r = client.post("/api/share", json=body)
    assert r.status_code == 201, r.text
    token = r.json()["url"].rsplit("/s/", 1)[1]
    pub_id = r.json()["pub_id"]
    client.cookies.clear()
    return token, pub_id


def _owner_snapshot(client, project="demo"):
    root = client.agentcad_service.store.canonical_path_of(project)
    return {str(p.relative_to(root)): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


# =================================================================== AC1


def test_ac1_the_logged_out_page_renders_with_no_auth_cookie(hosted,
                                                             kernel_counter):
    """**AC1** — a share link opens for a logged-out visitor: viewer, metrics
    and attribution render with **no auth cookie on any response**. (The visual
    half is graded as evidence — see the module docstring.)"""
    client, _ = hosted
    token, _ = _publish(client, script=BOX_SCRIPT, part="box")

    before = kernel_counter.calls
    page = client.get(f"/s/{token}")
    assert page.status_code == 200
    assert "set-cookie" not in {k.lower() for k in page.headers}
    assert 'id="share-viewport"' in page.text

    model = client.get(f"/s/{token}/model")
    assert model.status_code == 200
    body = model.json()
    assert body["attribution"]["part_id"] == "box"
    assert body["metrics"]["mass_g"] > 0
    assert kernel_counter.calls == before, kernel_counter.seen  # zero kernel


# =================================================================== AC2


def test_ac2_a_repeat_variant_is_one_build_for_two_requests(hosted,
                                                            kernel_counter):
    """**AC2** — the slider rebuilds live and metrics update; a second visitor
    requesting the **same** variant is served from the cache — exactly one
    kernel build for the two requests, with a positive control that a DIFFERENT
    param set does build (so the zero is a cache hit, not a stuck counter)."""
    client, _ = hosted
    token, _ = _publish(client)

    before = kernel_counter.calls
    a = client.get(f"/s/{token}/variant", params={"size": 25})
    assert a.status_code == 200 and a.json()["metrics"]["mass_g"] > 0
    one = kernel_counter.calls
    assert one > before

    b = client.get(f"/s/{token}/variant", params={"size": 25})
    assert b.json()["mesh_key"] == a.json()["mesh_key"]
    assert kernel_counter.calls == one              # the repeat did not build

    c = client.get(f"/s/{token}/variant", params={"size": 30})   # positive control
    assert c.json()["mesh_key"] != a.json()["mesh_key"]
    assert kernel_counter.calls > one


# =================================================================== AC3


def test_ac3_step_downloads_and_a_disabled_stl_404s_before_the_builder(
        hosted, kernel_counter):
    """**AC3** — a STEP of the visitor's variant downloads when the mask allows;
    a disabled STL is 404 **at the route, before any build**."""
    client, _ = hosted
    token, _ = _publish(client, exports=("step",))

    ok = client.get(f"/s/{token}/download/step", params={"size": 24})
    assert ok.status_code == 200
    assert "widget_" in ok.headers.get("content-disposition", "")

    before = kernel_counter.calls
    disabled = client.get(f"/s/{token}/download/stl", params={"size": 24})
    assert disabled.status_code == 404
    assert kernel_counter.calls == before, kernel_counter.seen


# =================================================================== AC4


def test_ac4_param_validation_parity_with_the_authoring_path(hosted):
    """**AC4** — parity with `set_params`: an out-of-range numeric **clamps**
    with a warning, a non-member enum choice is **rejected**, an unknown name is
    **rejected** — against the same `normalize_params`/`_resolve_params` the
    editor uses (not a fork)."""
    client, _ = hosted
    token, _ = _publish(client)                     # TYPED_SCRIPT: size 10..40, enum grade

    clamped = client.get(f"/s/{token}/variant", params={"size": 100000})
    assert clamped.status_code == 200
    assert any("clamp" in w.lower() for w in clamped.json()["warnings"])

    assert client.get(f"/s/{token}/variant",
                      params={"grade": "not-a-member"}).status_code == 422
    assert client.get(f"/s/{token}/variant",
                      params={"nope": 1}).status_code == 422


# =================================================================== AC5


def test_ac5_over_the_limit_is_quota_exceeded_and_the_owner_tree_is_untouched(
        hosted):
    """**AC5** — hammering past the per-link limit returns `quota_exceeded` with
    `retry_after_s` and the page degrades to view-only; throughout, the owner's
    manifest, params, history and `.cache/` are **byte-unchanged**."""
    client, _ = hosted
    token, _ = _publish(client)
    before = _owner_snapshot(client)

    codes = [client.get(f"/s/{token}/variant",
                        params={"size": 20}).status_code for _ in range(40)]
    assert 429 in codes
    # The bucket is empty now, so a further request 429s and carries retry_after.
    retried = None
    for _ in range(5):
        r = client.get(f"/s/{token}/variant", params={"size": 20})
        if r.status_code == 429:
            retried = r
            break
    assert retried is not None
    assert retried.json()["error"]["details"]["retry_after_s"] > 0
    assert _owner_snapshot(client) == before


def test_ac5_the_inflight_semaphore_is_consulted_with_a_positive_control(
        hosted, monkeypatch):
    """**AC5's** concurrency half — a global in-flight flood never exceeds
    `SHARE_MAX_INFLIGHT` concurrent builds. With the cap at 1, a request that
    finds the slot held is 429; releasing it lets the same request build (the
    positive control that the 429 was the cap, not a broken route)."""
    client, _ = hosted
    token, _ = _publish(client)
    monkeypatch.setenv("AGENTCAD_SHARE_MAX_INFLIGHT", "1")

    sem = share_build.inflight_semaphore()
    assert sem.acquire(blocking=False)
    try:
        bounded = client.get(f"/s/{token}/variant", params={"size": 21})
        assert bounded.status_code == 429
        assert bounded.json()["error"]["details"]["retry_after_s"] > 0
    finally:
        sem.release()
    assert client.get(f"/s/{token}/variant",
                      params={"size": 21}).status_code == 200


# =================================================================== AC6


def test_ac6_revoked_expired_and_unknown_are_indistinguishable(hosted):
    """**AC6** — revoked, expired and unknown links all 404 with identical
    bodies; and a `customizer:false` link 404s `/variant` **before** the builder
    (the escalation boundary — the bit is owner-written, not in the request)."""
    client, _ = hosted
    token, pub_id = _publish(client)
    login(client)
    assert client.request("DELETE", f"/api/share/{pub_id}").status_code == 200
    client.cookies.clear()

    rec = client.agentcad_service.publications.get(pub_id)
    _, expired = client.agentcad_service.publications.create(
        share_scope="part", project="demo", part_id="widget", ref=rec["ref"],
        script_sha=rec["script_sha"],
        settings={"customizer": True, "exports": [], "show_script": False,
                  "expires": int(time.time()) - 1, "config": None},
        created_by="nikita", default_variant_key=rec["default_variant_key"])
    unknown = "shr_deadbeef_" + "x" * 43

    bodies = {}
    for label, tok in (("revoked", token), ("expired", expired),
                       ("unknown", unknown)):
        r = client.get(f"/s/{tok}/variant", params={"size": 20})
        assert r.status_code == 404, label
        bodies[label] = r.json()
    assert bodies["revoked"] == bodies["expired"] == bodies["unknown"]


def test_ac6_a_viewer_only_link_cannot_rebuild(hosted, kernel_counter):
    """AC6's escalation boundary, in the negative: `customizer:false` 404s
    `/variant` before the builder — no request shape turns the bit on."""
    client, _ = hosted
    token, _ = _publish(client, customizer=False, exports=())
    before = kernel_counter.calls
    assert client.get(f"/s/{token}/variant",
                      params={"size": 20}).status_code == 404
    assert kernel_counter.calls == before, kernel_counter.seen


# =================================================================== AC7


def test_ac7_the_embed_frames_and_the_app_does_not(hosted):
    """**AC7** — the embed iframe is servable cross-origin (`frame-ancestors *`)
    while the authenticated app refuses to frame (`frame-ancestors 'none'`).
    (The visual "renders and orbits" half is graded as evidence.)"""
    client, _ = hosted
    token, _ = _publish(client)
    embed = client.get(f"/embed/{token}")
    assert embed.status_code == 200
    assert embed.headers["content-security-policy"] == "frame-ancestors *"
    assert client.get("/api/health").headers.get(
        "content-security-policy") == "frame-ancestors 'none'"


# =================================================================== AC8


def test_ac8_a_tag_pinned_link_does_not_drift_when_the_owner_edits(hosted):
    """**AC8** — a tag-pinned link keeps serving the tagged geometry after the
    source branch moves on and the owner edits the working part: the pin is a
    COPY, so the link's default variant is byte-stable across an edit."""
    client, _ = hosted
    token, _ = _publish(client, script=BOX_SCRIPT, part="box")
    before = client.get(f"/s/{token}/model").json()

    # The owner edits the WORKING part to a different geometry, and moves on.
    login(client)
    bigger = BOX_SCRIPT.replace('"default": 10.0', '"default": 40.0')
    assert client.put("/api/projects/demo/parts/box",
                      json={"script": bigger}).status_code == 200
    client.cookies.clear()

    after = client.get(f"/s/{token}/model").json()
    assert after["default_variant_key"] == before["default_variant_key"]
    assert after["metrics"] == before["metrics"]    # the link did not drift


# =================================================================== AC9


def test_ac9_the_share_routes_are_the_only_new_guard_exempt_surface(hosted_app):
    """**AC9** — the share routes are provably the only new guard-exempt
    surface: the set-equality enumeration, grown to the eight `/s/`+`/embed/`
    templates (the two customizer routes included), fails when a ninth goes
    public by accident. `NOT_YET_BUILT` is empty — nothing is staged."""
    from .test_hosted_surface import EXPECTED_PUBLIC, NOT_YET_BUILT

    assert NOT_YET_BUILT == set()
    reachable = {(m, p) for m, p in flatten_routes(hosted_app)
                 if m not in {"HEAD", "OPTIONS", "WS"} and security.is_public(p)}
    assert reachable == EXPECTED_PUBLIC
    for template in ("/s/{token}/variant", "/s/{token}/download/{fmt}",
                     "/s/{token}", "/embed/{token}"):
        assert template in {p for _, p in EXPECTED_PUBLIC}
    # The trailing-slash gotcha: the bare stems must not be public.
    for stem in ("/s", "/status", "/embed", "/embedding", "/svg"):
        assert security.is_public(stem) is False


def test_ac9_extra_the_viewer_routes_reach_zero_kernel(hosted, kernel_counter):
    """The invariant AC7-style half: the **viewer** routes make zero kernel
    calls over a full sweep, with a positive control that a real build moves the
    counter — so the zero means something."""
    client, _ = hosted
    token, _ = _publish(client, script=BOX_SCRIPT, part="box")
    key = client.get(f"/s/{token}/model").json()["default_variant_key"]

    before = kernel_counter.calls
    for path in (f"/s/{token}", f"/embed/{token}", f"/s/{token}/model",
                 f"/s/{token}/params", f"/s/{token}/script",
                 f"/s/{token}/mesh/{key}"):
        client.get(path)
    assert kernel_counter.calls == before, kernel_counter.seen

    login(client)
    novel = BOX_SCRIPT.replace("10.0", "13.0")
    assert client.post("/api/projects/demo/parts",
                       json={"id": "box2", "script": novel}).status_code == 201
    assert kernel_counter.calls > before            # positive control


# ==================================================== the PRD's own record


def test_the_prd_records_its_status_its_acs_and_its_residuals():
    text = PRD.read_text(encoding="utf-8")
    flat = " ".join(text.replace("**", "").split())
    assert any(f"Status: {s}" in flat for s in ("implemented", "completed")), \
        "the PRD status is not a post-implementation one"
    for ac in [f"AC{n}" for n in range(1, 10)]:
        assert ac in text, ac
    for needle in (
            "peak memory",          # the defining residual, named not papered
            "prd-006",              # ...and whose it is
            "graded as evidence",   # the browser posture
            "never rendered by a browser",
            "list_connected_browsers",
            "get",                  # the GET-not-POST divergence, folded in
            "acm",                  # ACM-not-glTF
            "publications",         # the state-dir storage
    ):
        assert needle in flat.lower(), f"the PRD does not record {needle!r}"


def test_ac1_and_ac7_browser_halves_are_recorded_as_unverified():
    """The half a test cannot be — the viewer page, a slider drag, a download
    and the embedded iframe were never rendered by a browser. This asserts the
    *record says so* (the opposite of a pass); delete it when someone with a
    browser closes AC1/AC7, and update the PRD in the same commit."""
    flat = " ".join(PRD.read_text(encoding="utf-8").replace("**", "").split())
    assert "graded as evidence" in flat
    assert "never rendered by a browser" in flat
    latest = max(CHANGELOG.glob("021[0-9]-prd-007-*.md"), default=None)
    assert latest is not None
    frontend = (CHANGELOG / "0217-prd-007-slice-5-frontend-embed.md").read_text(
        encoding="utf-8")
    assert "browser" in frontend.lower() and "evidence" in frontend.lower()

    # What a test CAN grade: the surface those sessions would have driven exists
    # and calls the routes it claims to.
    share_js = (FRONTEND / "js" / "share.js").read_text(encoding="utf-8")
    for call in ("/model", "/params", "/variant", "/download/"):
        assert call in share_js, call
    assert 'id="share-viewport"' in (FRONTEND / "share.html").read_text(
        encoding="utf-8")


def test_the_newest_changelog_cites_a_make_test_count():
    """"The full suite is green" is a claim about a run, so the check is that a
    count is on the record in the newest PRD-007 changelog (the close-out)."""
    latest = max(CHANGELOG.glob("0[0-9][0-9][0-9]-*.md"))
    text = latest.read_text(encoding="utf-8")
    assert "make test" in text and "passed" in text, latest.name
    assert any(tok.isdigit() and len(tok) >= 4
               for tok in text.replace(",", " ").split()), \
        f"{latest.name} cites no suite count"


def test_the_roadmap_link_resolves_to_the_prd_where_it_lives():
    roadmap = (REPO / "docs" / "roadmap.md").read_text(encoding="utf-8")
    row = next(line for line in roadmap.splitlines()
               if line.startswith("| [007]"))
    match = re.search(r"\((prd/[^)]+\.md)\)", row)
    assert match, f"the roadmap row for PRD-007 carries no link: {row}"
    assert (REPO / "docs" / match.group(1)).is_file(), \
        f"the roadmap link {match.group(1)} does not resolve"


def test_the_agents_guide_carries_the_share_gotchas():
    guide = (REPO / "AGENTS.md").read_text(encoding="utf-8")
    assert "## Share/customizer gotchas (PRD-007" in guide
    for needle in (
            "`GET`, not a `POST`",   # the pure-read rebuild
            "COPY, not a reference", # the pin
            "muzzled",               # the build service
            "trailing slash",        # the PUBLIC_PREFIXES rule
            "request.client.host",   # the M3 per-IP discipline
            "core/ratelimit.py",     # the promoted TokenBucket
            "NOT_YET_BUILT",         # the surface-growth discipline
    ):
        assert needle in guide, needle
