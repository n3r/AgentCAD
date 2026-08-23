"""PRD-027 slice 4 — the dashboard (FR6, design §6, ruling 8).

`navigation.dashboard` is the one listing call the entry screen makes, and its
whole contract is what it does **not** do: it never builds, never renders, and
never opens a part script. What it reads is the manifest on disk and the
service's in-memory `_status`; what it answers about geometry it can only
answer from what is already there — `mass_g` is `null` the moment one part is
unbuilt (an honest "unknown", never a partial sum), and `thumb` is a URL only
when a file exists to serve it from.

So the assertions come in two flavours: the payload, and the two spies — a
`CountingKernel` that must not move, and a `render_acm` that must not be
called. The AC (20 projects < 500 ms) is measured here and printed.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from agentcad.core import render
from agentcad.core.navigation import dashboard
from agentcad.core.tools import build_registry
from agentcad.server import security as security_module
from agentcad.server.app import create_app

from .conftest import BOX_SCRIPT, CountingKernel, login, make_test_service


@pytest.fixture
def counted(kernel):
    return CountingKernel(kernel)


@pytest.fixture
def demo(counted, tmp_path, monkeypatch):
    """Two projects: `alpha` (two parts, one instance) and `beta` (empty)."""
    monkeypatch.setattr(
        render, "render_acm",
        lambda *a, **k: pytest.fail("the dashboard rendered a thumbnail"))
    service = make_test_service(tmp_path / "projects", counted)
    service.create_project("alpha")
    for part_id in ("cube", "pin"):
        service.store.add_part("alpha", part_id, part_id, "al6061", BOX_SCRIPT)
    manifest = service.store.manifest("alpha")
    manifest["assembly"]["instances"] = [
        {"id": "i1", "part": "cube", "position": [0, 0, 0],
         "rotation_deg": [0, 0, 0]}]
    service.store.save_manifest("alpha", manifest)
    service.create_project("beta")
    return service


def row(payload, name):
    return next(r for r in payload["projects"] if r["name"] == name)


# ------------------------------------------------------------- the payload

def test_dashboard_answers_one_row_per_project(demo):
    payload = dashboard(demo)
    assert [r["name"] for r in payload["projects"]] == ["alpha", "beta"]
    assert set(payload) == {"projects"}


def test_a_row_carries_the_documented_keys(demo):
    alpha = row(dashboard(demo), "alpha")
    assert set(alpha) == {"name", "path", "n_parts", "n_instances", "mass_g",
                          "failing", "last_modified", "thumb"}
    assert alpha["n_parts"] == 2
    assert alpha["n_instances"] == 1
    assert alpha["path"] == str(demo.store.canonical_path_of("alpha"))


def test_an_empty_project_counts_zero(demo):
    beta = row(dashboard(demo), "beta")
    assert beta["n_parts"] == 0 and beta["n_instances"] == 0
    assert beta["failing"] == 0


# ------------------------------------------------------------------ mass_g

def test_mass_is_null_while_any_part_is_unbuilt(demo):
    assert row(dashboard(demo), "alpha")["mass_g"] is None


def test_mass_is_still_null_when_only_one_of_two_is_built(demo):
    demo.get_part("alpha", "cube")            # builds exactly one
    assert row(dashboard(demo), "alpha")["mass_g"] is None


@pytest.mark.integration
def test_mass_sums_when_every_part_is_built(demo):
    for part_id in ("cube", "pin"):
        demo.get_part("alpha", part_id)
    total = row(dashboard(demo), "alpha")["mass_g"]
    assert total is not None and total > 0
    expected = sum(
        demo._status[demo._status_key("alpha", p)]["metrics"]["mass_g"]
        for p in ("cube", "pin"))
    assert total == pytest.approx(expected)


def test_a_project_with_no_parts_has_a_zero_mass(demo):
    assert row(dashboard(demo), "beta")["mass_g"] == 0.0


# ----------------------------------------------------------------- failing

def test_failing_counts_the_error_states(demo):
    demo._status[demo._status_key("alpha", "cube")] = {
        "state": "error", "cache_key": None, "metrics": None,
        "warnings": [], "error": {"type": "script_error"}}
    alpha = row(dashboard(demo), "alpha")
    assert alpha["failing"] == 1
    assert alpha["mass_g"] is None                # a failing part is not a sum


def test_a_stale_status_for_a_deleted_part_is_not_counted(demo):
    """`_status` outlives a part; the manifest is what the count iterates."""
    demo._status[demo._status_key("alpha", "ghost")] = {
        "state": "error", "metrics": None}
    assert row(dashboard(demo), "alpha")["failing"] == 0


# ----------------------------------------------------------- last_modified

def test_last_modified_is_iso_8601_utc(demo):
    stamp = row(dashboard(demo), "alpha")["last_modified"]
    parsed = datetime.fromisoformat(stamp)
    assert parsed.tzinfo == timezone.utc
    path = demo.store.canonical_path_of("alpha") / "project.json"
    assert parsed == datetime.fromtimestamp(path.stat().st_mtime,
                                            tz=timezone.utc)


# ------------------------------------------------------------------- thumb

def test_thumb_is_null_without_any_cached_file(demo):
    assert row(dashboard(demo), "alpha")["thumb"] is None


def test_thumb_is_the_project_url_once_a_file_exists(demo):
    cache = demo.store.canonical_path_of("alpha") / ".cache"
    cache.mkdir(exist_ok=True)
    (cache / "deadbeef.acm").write_bytes(b"not really a mesh")
    assert row(dashboard(demo), "alpha")["thumb"] == \
        "/api/projects/alpha/thumb.png"


def test_the_thumb_gate_creates_no_cache_directory(demo):
    dashboard(demo)
    assert not (demo.store.canonical_path_of("beta") / ".cache").exists()


# ------------------------------------------------------------------- spies

def test_the_dashboard_makes_no_kernel_call(demo, counted):
    before = counted.calls
    dashboard(demo)
    assert counted.calls == before, counted.seen


def test_the_dashboard_reads_no_part_script(demo, monkeypatch):
    monkeypatch.setattr(
        type(demo.store), "read_script",
        lambda *a, **k: pytest.fail("the dashboard read a script"))
    dashboard(demo)


def test_a_corrupt_project_is_skipped_not_a_500(demo):
    (demo.store.canonical_path_of("beta") / "project.json").write_text(
        "{ not json", encoding="utf-8")
    assert [r["name"] for r in dashboard(demo)["projects"]] == ["alpha"]


# -------------------------------------------------------------------- route

@pytest.fixture
def http(demo):
    app = create_app(demo, build_registry(demo),
                     extra_allowed_hosts={"testserver"})
    return TestClient(app, base_url="http://127.0.0.1")


def test_the_route_answers_the_same_payload(http, demo):
    response = http.get("/api/dashboard")
    assert response.status_code == 200
    assert response.json() == dashboard(demo)


def test_the_route_is_registered_under_api(http):
    """I5: `[r.path for r in app.routes]` sees the 23 routes `app.py` declares
    and NONE of the ones a pack contributes — FastAPI leaves `include_router`
    opaque — so this used to be carried entirely by the `or` after it, which
    is a tautology (the route answering 200 is what "registered" means). The
    house walker flattens the packs."""
    from .conftest import flatten_routes

    assert ("GET", "/api/dashboard") in flatten_routes(http.app)


def test_the_dashboard_route_is_member_only():
    # Default-deny: nothing was added to PUBLIC_PATHS/PUBLIC_PREFIXES.
    assert not security_module.is_public("/api/dashboard")


@pytest.mark.integration
def test_the_route_is_401_anonymously_and_200_signed_in(hosted_client):
    assert hosted_client.get("/api/dashboard").status_code == 401
    login(hosted_client)
    response = hosted_client.get("/api/dashboard")
    assert response.status_code == 200
    assert "projects" in response.json()


# ---------------------------------------------------------- the 20-project AC

def _synthetic(service, name: str, parts: int) -> None:
    """A project written straight into the manifest — never built."""
    path = service.store.create(name)
    manifest = json.loads((path / "project.json").read_text(encoding="utf-8"))
    manifest["parts"] = [
        {"id": f"p{n}", "label": f"p{n}", "material": "al6061", "params": {}}
        for n in range(parts)]
    manifest["assembly"]["instances"] = [
        {"id": f"i{n}", "part": f"p{n}", "position": [0, 0, 0],
         "rotation_deg": [0, 0, 0]} for n in range(min(parts, 5))]
    (path / "project.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_twenty_projects_answer_in_under_half_a_second(counted, tmp_path,
                                                       capsys):
    service = make_test_service(tmp_path / "projects", counted)
    for n in range(20):
        _synthetic(service, f"proj{n:02d}", 25)
    dashboard(service)                              # warm the filesystem
    before = counted.calls
    start = time.perf_counter()
    payload = dashboard(service)
    elapsed = time.perf_counter() - start
    assert len(payload["projects"]) == 20
    assert counted.calls == before
    with capsys.disabled():
        print(f"\ndashboard: 20 projects x 25 parts in {elapsed * 1000:.1f} ms")
    assert elapsed < 0.5, f"{elapsed:.3f}s"


def test_the_naive_route_walk_would_not_have_seen_it(http):
    """The other half of I5, stated once: the walk the test used to do sees
    only what `app.py` declares itself."""
    naive = {r.path for r in http.app.routes if getattr(r, "path", None)}
    assert "/api/dashboard" not in naive


def test_the_thumb_url_is_encoded(demo, tmp_path):
    """M6: `list_projects` reports a DIRECTORY name and validates nothing, so
    a folder called "my proj" is listed verbatim — and interpolated raw into
    `/api/projects/{name}/thumb.png` it produced a URL with a space in it."""
    import shutil

    root = tmp_path / "projects"
    shutil.copytree(root / "alpha", root / "my proj")
    # something for `has_thumb` to find (an `.acm` is enough — it is a gate,
    # not a render)
    cache = root / "my proj" / ".cache"
    cache.mkdir(exist_ok=True)
    (cache / ("0" * 32 + ".acm")).write_bytes(b"not really a mesh")

    payload = dashboard(demo)
    card = row(payload, "my proj")
    assert card["thumb"] == "/api/projects/my%20proj/thumb.png"
