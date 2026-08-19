"""PRD-028 slice 2: `find_materials`/`get_material`/filtered `list_materials`
tools and the `routes_materials.py` HTTP surface.

Runs against the REAL shipped catalog (whatever size curation has grown it to
— never hard-coded) so AC2's exact query is proven against production data,
not a fixture.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from agentcad.core.tools import build_registry
from agentcad.server import security as security_module
from agentcad.server.app import create_app

from .conftest import make_test_service

# --------------------------------------------------------------------- tools


@pytest.fixture
def service(kernel, tmp_path):
    return make_test_service(tmp_path / "projects", kernel)


@pytest.fixture
def registry(service):
    return build_registry(service)


def _lower_bound(evidence: dict) -> float:
    return evidence["value"] if "value" in evidence else evidence["range"][0]


def _upper_bound(evidence: dict) -> float:
    return evidence["value"] if "value" in evidence else evidence["range"][1]


def test_find_materials_ac2_query_every_member_qualifies_and_is_cited(registry):
    result = registry.call("find_materials", {
        "require": {"yield_mpa_min": 240, "max_service_temp_c_min": 150}})
    assert "error" not in result
    assert result["count"] >= 1
    assert result["count"] == len(result["materials"])
    assert result["constraints"] == {"yield_mpa_min": 240.0,
                                     "max_service_temp_c_min": 150.0}
    for row in result["materials"]:
        yield_ev = row["constraining"]["yield_mpa"]
        temp_ev = row["constraining"]["max_service_temp_c"]
        assert _lower_bound(yield_ev) >= 240
        assert _lower_bound(temp_ev) >= 150
        assert yield_ev["source"]
        assert temp_ev["source"]
    assert "allowables" in result["caveat"]


def test_find_materials_prefers_ranking_direction(registry):
    result = registry.call("find_materials", {
        "require": {"yield_mpa_min": 100}, "prefer": {"cost_usd_kg": "min"}})
    assert "error" not in result
    scores = [row["score"] for row in result["materials"]]
    assert scores == sorted(scores)


def test_find_materials_zero_results_is_a_validation_error_with_relaxation(registry):
    result = registry.call("find_materials", {
        "require": {"yield_mpa_min": 100000, "max_service_temp_c_min": 100000}})
    assert result["error"]["type"] == "validation_error"
    assert "no material satisfies" in result["error"]["message"]
    details = result["error"]["details"]
    assert "tried" in details and details["tried"] == {
        "yield_mpa_min": 100000.0, "max_service_temp_c_min": 100000.0}
    # nearest_relaxation may be None only if dropping either constraint still
    # admits nothing at these absurd thresholds; assert the key exists either way.
    assert "nearest_relaxation" in details


def test_find_materials_limit_is_capped(registry):
    result = registry.call("find_materials", {
        "require": {"density_g_cm3_min": 0.01}, "limit": 3})
    assert "error" not in result
    assert len(result["materials"]) <= 3
    bad = registry.call("find_materials", {
        "require": {"density_g_cm3_min": 0.01}, "limit": 51})
    assert bad["error"]["type"] == "validation_error"


def test_find_materials_unknown_require_key_lists_grammar(registry):
    result = registry.call("find_materials", {"require": {"bogus_key": 1}})
    assert result["error"]["type"] == "validation_error"
    assert "category" in result["error"]["details"]["known"]


def test_get_material_al6061_full_payload_has_sources(registry):
    result = registry.call("get_material", {"id": "al6061"})
    assert "error" not in result
    assert result["id"] == "al6061"
    assert "properties" in result
    for key, prop in result["properties"].items():
        assert prop["source"], f"{key} is uncited"
        assert "unit" in prop and "basis" in prop
    assert "allowables" in result["caveat"]


def test_get_material_unknown_id_lists_known_ids(registry):
    result = registry.call("get_material", {"id": "unobtanium_9000"})
    assert result["error"]["type"] == "validation_error"
    assert "unknown material" in result["error"]["message"]
    assert "al6061" in result["error"]["details"]["known"]


def test_list_materials_category_filter_only_polymers(registry):
    result = registry.call("list_materials", {"category": "polymer"})
    assert "error" not in result
    assert result["count"] == len(result["materials"])
    assert result["materials"], "expected at least one polymer in the catalog"
    for row in result["materials"]:
        assert row["category"] == "polymer"


def test_list_materials_filter_unknown_key_is_a_validation_error(registry):
    result = registry.call("list_materials", {"filter": {"nope": 1}})
    assert result["error"]["type"] == "validation_error"
    assert "category" in result["error"]["details"]["known"]


def test_list_materials_ordering_is_category_subcategory_id(registry):
    result = registry.call("list_materials", {})
    keys = [(m["category"], m.get("subcategory") or "", m["id"])
            for m in result["materials"]]
    assert keys == sorted(keys)


# -------------------------------------------------------------------- routes


def _local_client(kernel, tmp_path) -> TestClient:
    security_module.install(None)
    service = make_test_service(tmp_path / "projects", kernel)
    app = create_app(service, build_registry(service),
                     extra_allowed_hosts={"testserver"})
    return TestClient(app, base_url="http://127.0.0.1")


def test_route_get_list_with_filter_json(kernel, tmp_path):
    client = _local_client(kernel, tmp_path)
    r = client.get("/api/materials", params={
        "filter": json.dumps({"category": "metal"})})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == len(body["materials"])
    assert all(m["category"] == "metal" for m in body["materials"])


def test_route_get_list_with_bad_filter_json_is_422(kernel, tmp_path):
    client = _local_client(kernel, tmp_path)
    assert client.get("/api/materials", params={"filter": "not json"}).status_code == 422
    assert client.get("/api/materials", params={"filter": "[1,2]"}).status_code == 422


def test_route_get_by_id_200_and_unknown(kernel, tmp_path):
    client = _local_client(kernel, tmp_path)
    ok = client.get("/api/materials/al6061")
    assert ok.status_code == 200
    assert ok.json()["id"] == "al6061"

    missing = client.get("/api/materials/nope_not_real")
    # `get_material`'s refusal is a `ValidationError` (it reuses
    # `MaterialLibrary.resolve`'s error, not a `NotFoundError`), and the house
    # convention (`routes_configs._result` / `app.py`'s `_ERROR_STATUS`) maps
    # that to 422 — so this is 422, not 404.
    assert missing.status_code == 422
    assert missing.json()["error"]["type"] == "ValidationError"


def test_route_post_find_200_and_zero_result_refusal(kernel, tmp_path):
    client = _local_client(kernel, tmp_path)
    ok = client.post("/api/materials/find", json={
        "require": {"yield_mpa_min": 100}})
    assert ok.status_code == 200
    assert ok.json()["count"] >= 1

    empty = client.post("/api/materials/find", json={
        "require": {"yield_mpa_min": 100000}})
    assert empty.status_code == 422
    assert empty.json()["error"]["type"] == "ValidationError"


def test_route_post_find_non_object_body_is_422(kernel, tmp_path):
    client = _local_client(kernel, tmp_path)
    assert client.post("/api/materials/find", json=[1, 2]).status_code == 422
    assert client.post("/api/materials/find", json="bad").status_code == 422


# ---------------------------------------------------------- hosted / gating


def test_new_routes_are_not_in_the_anonymous_surface():
    """None of the materials routes are public — `GET /api/materials` never
    was, and the two new routes must not widen the surface either."""
    for path in ("/api/materials", "/api/materials/al6061",
                 "/api/materials/find"):
        assert not security_module.is_public(path), path


def test_hosted_anonymous_requests_to_the_new_routes_are_401(hosted_client):
    r = hosted_client.get("/api/materials/al6061")
    assert r.status_code == 401
    assert r.json()["error"]["type"] == "AuthError"

    r = hosted_client.post("/api/materials/find", json={
        "require": {"yield_mpa_min": 100}})
    assert r.status_code == 401
    assert r.json()["error"]["type"] == "AuthError"

    r = hosted_client.get("/api/materials")
    assert r.status_code == 401
