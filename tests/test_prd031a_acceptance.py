"""PRD-031a acceptance — the seeded marketplace catalog, AC1–AC9.

One test per criterion, each naming it, graded against the real anonymous
surface, the real (bundled) public catalog, the real PRD-007 containment and the
shipped frontend — never a stub built for the occasion.

Two ACs are partly a browser session, and a test cannot be one. **AC1 (the
browse → customize → download flow) and AC9 (the pages render, a slider drives a
rebuild) have contract halves driven here for real** — the anonymous
`TestClient` sweep, the mesh bytes flowing back, the STEP downloading — but their
**visual halves were never rendered by a browser** (`list_connected_browsers` →
`[]`, the PRD-005a/007 precedent). `test_ac9_browser_half_is_recorded_as_unverified`
asserts the record says so, which is the opposite of a pass.

Every build-path AC pins `AGENTCAD_KERNEL_POOL_SIZE >= 2` (the PRD-007 M-1
reservation): the customizer leaves one worker for members, so on a
single-worker pool `/variant` correctly 503s. CI pins the pool to 1 and the
session `kernel` fixture is one client, so a build-path test that does not pin
the pool passes locally on a multi-core host and FAILS in CI with a 503 — the
bug that cost PR #20 a CI round.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentcad.core import share_build
from agentcad.server import security

from .conftest import flatten_routes
# The AC5 "on a copy" rig + its package/part constants (PRD-011 verbatim).
from .test_market_install import (  # noqa: F401  (catalog_rig is a fixture)
    PACKAGE,
    PART,
    catalog_rig,
)
from .test_hosted_surface import BUILT_PUBLIC, _reachable

REPO = Path(__file__).resolve().parents[1]
CHANGELOG = REPO / "docs" / "changelog"
FRONTEND = REPO / "frontend"
PRD_NAME = "PRD-031a-marketplace-catalog.md"

NEMA = "/api/public/packages/nema17/versions/1.0.0/parts/motor"

#: The five new templates the design's Decision-4 table enumerates, PLUS the
#: slice-4 mesh route that closes the browser-viewport gap — the complete new
#: anonymous market surface. AC3 asserts they are all reachable and nothing else.
MARKET_TEMPLATES = {
    ("GET", "/api/public/packages/search"),
    ("GET", "/api/public/packages/{name}/versions/{version}/script/{part}"),
    ("GET", "/api/public/packages/{name}/versions/{version}/params/{part}"),
    ("GET", "/api/public/packages/{name}/versions/{version}/parts/{part}/variant"),
    ("GET",
     "/api/public/packages/{name}/versions/{version}/parts/{part}/download/{fmt}"),
    ("GET", "/api/public/packages/{name}/versions/{version}/parts/{part}/mesh/{key}"),
}


@pytest.fixture(autouse=True)
def _customizer_pool(monkeypatch):
    """The customizer reserves one worker for members, so it needs a DECLARED
    pool of >=2 to build; pin it high (the source `effective_max_inflight`
    reads). The one AC that probes the single-worker 503 overrides it back."""
    monkeypatch.setenv("AGENTCAD_KERNEL_POOL_SIZE", "4")


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


# =================================================================== AC1

def test_ac1_anonymous_browse_customize_download(hosted_with_catalog,
                                                 kernel_counter):
    """**AC1** — a logged-out visitor searches, opens the NEMA-17 listing, moves
    the `body_length` slider (the server rebuilds), fetches the rebuilt mesh
    bytes and downloads a STEP: no catalog code ran on their machine (it ran
    only in our server-side kernel). No auth cookie on any response."""
    client = hosted_with_catalog
    assert not client.cookies

    hits = client.get("/api/public/packages/search",
                      params={"q": "nema"}).json()["hits"]
    assert "nema17" in {h["name"] for h in hits}

    params = client.get(f"{NEMA.rsplit('/parts/', 1)[0]}/params/motor").json()
    assert any(p["name"] == "body_length" for p in params["params"])

    v = client.get(f"{NEMA}/variant", params={"body_length": 42})
    assert v.status_code == 200, v.text
    key = v.json()["mesh_key"]
    assert key and v.json()["metrics"] is not None

    # The browser viewport's mesh fetch (the slice-4 gap): the .acm flows back.
    mesh = client.get(f"{NEMA}/mesh/{key}")
    assert mesh.status_code == 200 and mesh.content

    step = client.get(f"{NEMA}/download/step", params={"body_length": 42})
    assert step.status_code == 200, step.text
    assert step.content[:4] in (b"ISO-", b"ISO ") or b"STEP" in step.content[:200]

    assert "set-cookie" not in {k.lower() for r in (v, mesh, step)
                                for k in r.headers}


# =================================================================== AC2

def test_ac2_browse_reaches_zero_kernel_with_a_positive_control(
        hosted_with_catalog, kernel_counter):
    """**AC2 (the invariant)** — browse, search, listing-detail, `/script`,
    `/params`, `/preview` and the mesh read (absent key) make ZERO kernel calls
    over a full sweep; a variant build is the positive control that DOES move the
    counter, so the zero is meaningful, not a dead meter."""
    client = hosted_with_catalog
    base = "/api/public/packages"
    ver = f"{base}/nema17/versions/1.0.0"
    before = kernel_counter.calls
    for path in (
        base,
        f"{base}/search?q=bearing",
        f"{base}/nema17",
        ver,
        f"{ver}/script/motor",
        f"{ver}/params/motor",
        f"{ver}/preview?path=previews/motor_iso.png",
        f"{ver}/parts/motor/mesh/{'a' * 40}",        # absent key: 404, no build
    ):
        client.get(path)
    assert kernel_counter.calls == before, kernel_counter.seen

    # Positive control: a variant DOES build.
    r = client.get(f"{NEMA}/variant", params={"body_length": 33})
    assert r.status_code == 200
    assert kernel_counter.calls > before


# =================================================================== AC3

def test_ac3_the_anonymous_surface_is_exactly_this_including_the_market_routes(
        hosted_app):
    """**AC3 (the surface)** — the reachable anonymous surface equals
    `EXPECTED_PUBLIC`, and the six new market templates are all in it (nothing
    market-shaped slipped public unreviewed, and none was forgotten)."""
    reachable = _reachable(hosted_app)
    assert reachable == BUILT_PUBLIC
    assert MARKET_TEMPLATES <= reachable


def test_ac3_a_private_listing_is_indistinguishable_from_nonexistent(
        hosted_with_private):
    """**AC3** — a private-scoped index yields a listing/variant/mesh miss
    byte-identical to a nonexistent package (no existence oracle)."""
    client, private = hosted_with_private
    for suffix in ("", "/versions/1.0.0/parts/ball_bearing/variant?designation=625",
                   f"/versions/1.0.0/parts/ball_bearing/mesh/{'a' * 40}"):
        mine = client.get(f"/api/public/packages/{private}{suffix}")
        gone = client.get(f"/api/public/packages/does-not-exist{suffix}")
        assert mine.status_code == gone.status_code == 404, suffix
        assert mine.json() == gone.json(), suffix


# =================================================================== AC4

def test_ac4_the_customizer_inherits_007s_shared_containment(
        hosted_with_catalog, kernel_counter):
    """**AC4 (the containment)** — a SECOND visitor at the same params is served
    from the variant cache (exactly one build for two requests), and the per-IP
    bucket is the SAME object `/s/` uses (`service.customizer_guard`), so a
    visitor cannot double their allowance across the two anonymous kernel paths."""
    client = hosted_with_catalog
    first = client.get(f"{NEMA}/variant", params={"body_length": 44})
    assert first.status_code == 200 and first.json()["cached"] is False
    mid = kernel_counter.calls
    second = client.get(f"{NEMA}/variant", params={"body_length": 44})
    assert second.json()["cached"] is True
    assert kernel_counter.calls == mid              # the repeat did not build
    assert second.json()["mesh_key"] == first.json()["mesh_key"]

    # The shared guard: one CustomizerGuard on the service, shared with /s/.
    service = client.agentcad_service
    share_build.ensure_share(service)
    assert isinstance(service.customizer_guard, share_build.CustomizerGuard)
    g = service.customizer_guard
    share_build.ensure_share(service)
    assert service.customizer_guard is g            # not replaced per surface


def test_ac4_a_single_worker_pool_503s_naming_the_knob(hosted_with_catalog,
                                                       kernel_counter,
                                                       monkeypatch):
    """**AC4** — on a 1-worker pool the customizer would starve members, so it
    503s naming `AGENTCAD_KERNEL_POOL_SIZE` and makes NO build."""
    monkeypatch.setenv("AGENTCAD_KERNEL_POOL_SIZE", "1")
    before = kernel_counter.calls
    r = hosted_with_catalog.get(f"{NEMA}/variant", params={"body_length": 25})
    assert r.status_code == 503
    assert "AGENTCAD_KERNEL_POOL_SIZE" in r.json()["error"]["message"]
    assert kernel_counter.calls == before


# =================================================================== AC5

def test_ac5_add_to_library_pins_the_version_via_the_lockfile(catalog_rig):
    """**AC5** — a signed-in add (on a COPY of the bundled catalog) records both
    manifest maps, the lockfile pins the exact version + `content_id`, and the
    materialised part rebuilds byte-identically (PRD-011 AC3 inherited)."""
    service, registry = catalog_rig
    result = registry.call("market_install", {
        "project": "proj", "package": PACKAGE, "part": PART, "part_id": "beam"})
    assert "error" not in result, result
    manifest = service.store.manifest("proj")
    assert PACKAGE in (manifest.get("packages") or {})
    lock = (manifest.get("packages_lock") or {})[PACKAGE]
    assert lock["version"] == "1.0.0" and lock["content_id"].startswith("sha256:")

    registry.call("market_install", {
        "project": "proj", "package": PACKAGE, "part": PART, "part_id": "beam2"})
    assert service.store.read_script("proj", "beam") == \
        service.store.read_script("proj", "beam2")


# =================================================================== AC6

def test_ac6_market_install_installs_only_from_the_seeded_catalog(catalog_rig):
    """**AC6** — `market_install` refuses a package resolvable only from a
    private index, before anything installs; it returns the lock entry on a
    seeded hit."""
    service, registry = catalog_rig
    ok = registry.call("market_install", {
        "project": "proj", "package": PACKAGE, "part": PART, "part_id": "beam"})
    assert ok["lock"]["content_id"].startswith("sha256:")

    refused = registry.call("market_install", {
        "project": "proj", "package": "acme-secret", "part": "ball_bearing",
        "part_id": "brg"})
    assert refused.get("error", {}).get("type") == "notfound_error"
    assert "brg" not in {p["id"] for p in service.store.manifest("proj")["parts"]}


# =================================================================== AC7

def test_ac7_a_listing_surfaces_provenance_read_only(hosted_with_catalog):
    """**AC7** — a listing surfaces `license`, `disclosure`, `standards`, the
    `gate: green` validated badge and the empty `signatures` slot read-only; the
    read-only script carries its own PRD-011 provenance header."""
    client = hosted_with_catalog
    doc = client.get("/api/public/packages/nema17/versions/1.0.0").json()
    assert doc.get("license")
    assert doc.get("gate", {}).get("status") == "green"
    assert doc.get("signatures", []) == []          # present and honest: unsigned
    assert doc.get("disclosure") in ("agent", "human")

    # The read-only script text travels verbatim from the version tree (its
    # own header/docstring rides with it); the customizer never sees a copy.
    script = client.get(
        "/api/public/packages/nema17/versions/1.0.0/script/motor").text
    assert "def build" in script and "NEMA" in script


def test_ac7_the_market_ui_shows_provenance_and_has_no_remix_affordance():
    """**AC7 (frontend, evidence)** — the shipped market page renders the
    provenance block and the correctness-not-security non-claim, and offers NO
    remix/economy affordance (there is none to build in 031a)."""
    src = (FRONTEND / "js" / "market.js").read_text(encoding="utf-8")
    for needle in ("provenanceBlock", "Signatures", "unsigned",
                   "not a security boundary", "License"):
        assert needle in src, needle
    for forbidden in ("remix", "checkout", "payout", "buy now", "price"):
        assert forbidden not in src.lower(), forbidden


# =================================================================== AC8

def test_ac8_the_market_data_modules_are_ocp_free():
    """**AC8 (OCP-free)** — the browse/search/listing/script/params surface
    imports no OCP/build123d, in a fresh interpreter with OCP blocked."""
    import subprocess
    import sys

    code = (
        "import importlib, sys\n"
        "class _B:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name.split('.')[0] in ('OCP', 'build123d'):\n"
        "            raise ImportError('blocked: ' + name)\n"
        "        return None\n"
        "sys.meta_path.insert(0, _B())\n"
        "importlib.import_module('agentcad.server.routes_public')\n"
        "importlib.import_module('agentcad.core.packages.search')\n"
        "assert 'OCP' not in sys.modules and 'build123d' not in sys.modules\n"
        "print('ok')\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().endswith("ok")


# =================================================================== AC9

def test_ac9_the_market_ui_is_wired_and_calls_the_market_routes():
    """**AC9 (contract half)** — the market view exists, is wired into the boot
    sequence, and the client it drives calls the routes it claims to. The visual
    half is graded as evidence (see the module docstring)."""
    main = (FRONTEND / "js" / "main.js").read_text(encoding="utf-8")
    assert 'import * as market from "./market.js";' in main
    assert "market.enter(" in main

    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    assert 'id="market-view"' in html
    assert 'id="market-btn"' in html

    api = (FRONTEND / "js" / "api.js").read_text(encoding="utf-8")
    for call in ("/api/public/packages", "marketSearch", "marketVariant",
                 "marketMesh", "marketDownloadUrl", "marketParams"):
        assert call in api, call

    market = (FRONTEND / "js" / "market.js").read_text(encoding="utf-8")
    # It reuses the PRD-007 customizer viewport, not a fresh WebGL surface.
    assert 'from "./share-viewport.js"' in market
    for used in ("api.marketVariant", "api.marketMesh", "vp.showPart", "vp.fit"):
        assert used in market, used


def test_ac9_browser_half_is_recorded_as_unverified():
    """The half a test cannot be — the pages were never rendered by a browser
    and no slider was dragged. This asserts the *record says so* (the opposite of
    a pass); delete it when someone with a browser closes AC9, updating the PRD
    in the same commit."""
    flat = " ".join(PRD.read_text(encoding="utf-8").replace("**", "").split())
    assert "graded as evidence" in flat.lower()
    assert "never rendered by a browser" in flat.lower()
    assert "list_connected_browsers" in flat.lower()

    latest = max(CHANGELOG.glob("022[0-9]-prd-031a-*.md"), default=None)
    assert latest is not None
    assert "evidence" in latest.read_text(encoding="utf-8").lower()


# =================================================== the browser path is private

def test_the_add_to_library_routes_are_never_anonymous():
    """Add-to-library reuses the existing package routes; a logged-out request
    to them is 401 — they are not on the anonymous market surface."""
    assert security.is_public("/api/projects/proj/packages") is False
    assert security.is_public("/api/projects/proj/packages/nema17/use") is False


# ==================================================== the PRD's own record

def test_the_prd_records_its_status_its_acs_and_its_residuals():
    text = PRD.read_text(encoding="utf-8")
    flat = " ".join(text.replace("**", "").split()).lower()
    assert any(f"status: {s}" in flat for s in ("implemented", "completed")), \
        "the PRD status is not a post-implementation one"
    for ac in [f"AC{n}" for n in range(1, 10)]:
        assert ac in text, ac
    for needle in (
            "peak memory",          # the defining residual, named not papered
            "prd-006",              # ...and whose it is
            "graded as evidence",   # the browser posture
            "never rendered by a browser",
            "list_connected_browsers",
            "mesh",                 # the slice-4 gap the mesh route closed
            "market_install",       # the agent add-to-library surface
            "customizer_guard",     # the shared containment identity
    ):
        assert needle in flat, f"the PRD does not record {needle!r}"


def test_the_newest_changelog_cites_a_make_test_count():
    """"The full suite is green" is a claim about a run, so the check is that a
    count is on the record in the newest PRD-031a changelog (the close-out)."""
    latest = max(CHANGELOG.glob("0[0-9][0-9][0-9]-*.md"))
    body = latest.read_text(encoding="utf-8")
    assert "make test" in body and "passed" in body, latest.name
    assert any(tok.isdigit() and len(tok) >= 4
               for tok in body.replace(",", " ").split()), \
        f"{latest.name} cites no suite count"
