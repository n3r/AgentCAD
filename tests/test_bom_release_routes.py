"""HTTP surface for PRD-015 Slice 6: routes_bom.py / routes_releases.py.

Registry passthroughs only — the tool behavior itself (BOM enumeration/
pricing, the release state machine) is covered by ``tests/test_bom.py`` /
``tests/test_release_*.py``. This is the route pack's own contract: strict
``_json`` body parsing, key whitelisting, the refusal/post-state split, and the
CSV/JSON export streams.
"""

from __future__ import annotations

import shutil

import pytest
from fastapi.testclient import TestClient

from agentcad.core.service import AgentCADService, EventBus
from agentcad.core.tools import build_registry
from agentcad.server.app import create_app

from .conftest import BOX_SCRIPT

_GIT = [
    pytest.mark.integration,
    pytest.mark.portability,
    pytest.mark.skipif(shutil.which("git") is None, reason="git not found on PATH"),
]


@pytest.fixture
def stack(kernel, tmp_path):
    service = AgentCADService(tmp_path / "projects", kernel, EventBus())
    registry = build_registry(service)
    assert "error" not in registry.call("create_project", {"name": "demo"})
    assert "error" not in registry.call(
        "create_part", {"project": "demo", "part_id": "box", "script": BOX_SCRIPT})
    # get_bom walks assembly.instances (core/bom.py:count_leaves) — a part
    # with no instance contributes no line.
    service.set_assembly("demo", [{"id": "b", "part": "box"}])
    return service, registry


@pytest.fixture
def client(stack):
    service, registry = stack
    app = create_app(service, registry, extra_allowed_hosts={"testserver"})
    return service, registry, TestClient(app, base_url="http://127.0.0.1")


# ------------------------------------------------------------------- BOM


def test_get_bom_returns_lines(client):
    _service, _registry, http = client
    response = http.get("/api/projects/demo/bom")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["structure"] == "flat"
    assert len(body["lines"]) == 1
    assert body["lines"][0]["part_id"] == "box"
    assert body["lines"][0]["qty"] == 1


def test_get_bom_unknown_project_is_404(client):
    _service, _registry, http = client
    response = http.get("/api/projects/nope/bom")
    assert response.status_code == 404, response.text
    assert response.json()["error"]["type"] == "NotFoundError"


def test_get_bom_bad_structure_is_422(client):
    _service, _registry, http = client
    response = http.get("/api/projects/demo/bom?structure=nonsense")
    assert response.status_code == 422, response.text
    assert response.json()["error"]["type"] == "ValidationError"


def test_patch_bom_sets_fields_reflected_in_a_re_get(client):
    _service, _registry, http = client
    response = http.patch(
        "/api/projects/demo/parts/box/bom",
        json={"part_number": "PN-001", "unit_cost_usd": 4.5,
              "supplier": "Acme"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["bom"]["part_number"] == "PN-001"

    again = http.get("/api/projects/demo/bom")
    line = again.json()["lines"][0]
    assert line["part_number"] == "PN-001"
    assert line["unit_cost_usd"] == 4.5
    assert line["cost_source"] == "manual"


def test_patch_bom_whitelists_keys_and_drops_unknown_ones(client):
    """`_body_keys` forwards ONLY the whitelisted fields — an unknown
    top-level key (`junk`) is silently dropped rather than reaching the tool
    (never `**body`, the `routes_configs` convention every route pack
    shares), so it never appears in the stored bom."""
    _service, _registry, http = client
    response = http.patch(
        "/api/projects/demo/parts/box/bom",
        json={"part_number": "PN-9", "junk": 1})
    assert response.status_code == 200, response.text
    assert response.json()["bom"] == {"part_number": "PN-9"}


def test_patch_bom_unknown_part_is_404(client):
    _service, _registry, http = client
    response = http.patch(
        "/api/projects/demo/parts/ghost/bom", json={"part_number": "X"})
    assert response.status_code == 404, response.text


def test_get_bom_csv_streams_text_csv(client):
    _service, _registry, http = client
    response = http.get("/api/projects/demo/bom.csv")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["cache-control"] == "no-store"
    assert response.text.startswith("item,qty,part_number")
    assert "TOTAL" in response.text


def test_get_bom_json_streams_application_json(client):
    _service, _registry, http = client
    response = http.get("/api/projects/demo/bom.json")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["lines"][0]["part_id"] == "box"


# --------------------------------------------------------------- releases


class TestReleases:
    pytestmark = _GIT

    @pytest.fixture
    def rel_client(self, stack):
        service, registry = stack
        # A release must be cut from a branch other than the project default
        # (core/releases.py:release_start) — create one and switch it under
        # the SAME client identity the HTTP calls below carry (X-Agent-Id:
        # local), since `branches.current` is keyed per client and a
        # header-less request would otherwise arrive as "browser" (unswitched,
        # still on the default branch).
        service.branches.create("demo", "feat")
        service.branches.switch("demo", "feat")
        app = create_app(service, registry, extra_allowed_hosts={"testserver"})
        return service, registry, TestClient(
            app, base_url="http://127.0.0.1", headers={"X-Agent-Id": "local"})

    def test_post_releases_opens_a_draft_or_in_review_with_a_gate_report(
            self, rel_client):
        _service, _registry, http = rel_client
        response = http.post(
            "/api/projects/demo/releases", json={"notes": "first cut"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["rev"] == "A"
        assert body["status"] in ("draft", "in_review")
        assert body["gate"]["status"] in ("green", "red")
        assert isinstance(body["gate"]["checks"], list)

    def test_get_releases_lists_the_cut_release(self, rel_client):
        _service, _registry, http = rel_client
        http.post("/api/projects/demo/releases", json={"notes": "x"})
        response = http.get("/api/projects/demo/releases")
        assert response.status_code == 200, response.text
        revs = [r["rev"] for r in response.json()["releases"]]
        assert revs == ["A"]

    def test_get_release_by_rev(self, rel_client):
        _service, _registry, http = rel_client
        http.post("/api/projects/demo/releases", json={"notes": "x"})
        response = http.get("/api/projects/demo/releases/A")
        assert response.status_code == 200, response.text
        assert response.json()["release"]["rev"] == "A"

    def test_get_release_unknown_rev_is_404(self, rel_client):
        _service, _registry, http = rel_client
        response = http.get("/api/projects/demo/releases/Z")
        assert response.status_code == 404, response.text

    def test_finalize_before_approval_is_a_conflict(self, rel_client):
        _service, _registry, http = rel_client
        created = http.post(
            "/api/projects/demo/releases", json={"notes": "x"}).json()
        if created["status"] != "in_review":
            pytest.skip("gate did not go green in this fixture; finalize "
                        "path covered by the release-record unit tests")
        response = http.post("/api/projects/demo/releases/A/finalize")
        assert response.status_code == 409, response.text
        assert response.json()["error"]["type"] == "ConflictError"

    def test_releases_router_is_empty_without_git_tools(self, stack):
        """When the tool pack registered nothing (no git), the route pack
        mounts an empty router rather than 500ing on a missing tool."""
        service, registry = stack
        if registry.get("release_start") is None:
            app = create_app(service, registry,
                             extra_allowed_hosts={"testserver"})
            http = TestClient(app, base_url="http://127.0.0.1")
            response = http.get("/api/projects/demo/releases")
            assert response.status_code == 404
        else:
            pytest.skip("git available in this environment; release routes "
                        "are mounted (covered by the other release tests)")
