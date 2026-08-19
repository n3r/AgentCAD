"""PRD-028 slice 5 — the materials browser's pure model, in node (same
harness shape as `test_frontend_tree.py`), plus one live-contract check that
the HTTP surface the view depends on (`GET /api/materials`, `GET
/api/materials/{id}`) actually answers what `materials.js` assumes.

`frontend/js/materials_model.js` is pure (no DOM, no imports) so its query
building, tree counts, compare rendering, basis badges and sort all run in
node exactly as they run in the browser.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentcad.core.tools import build_registry
from agentcad.server import security as security_module
from agentcad.server.app import create_app

from .conftest import make_test_service

FRONTEND = Path(__file__).resolve().parents[1] / "frontend" / "js"
MODEL = FRONTEND / "materials_model.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not installed")

HARNESS = """
import {{ __materialsModel__ }} from {module};
const call = process.env.AGENTCAD_CALL;
const args = JSON.parse(process.env.AGENTCAD_ARGS);
const fn = __materialsModel__[call];
const result = fn(...args);
process.stdout.write(JSON.stringify(result));
"""


def run(call, *args):
    script = HARNESS.format(module=json.dumps(MODEL.as_uri()))
    env = {**os.environ, "AGENTCAD_CALL": call, "AGENTCAD_ARGS": json.dumps(args)}
    out = subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        capture_output=True, text=True, timeout=60, env=env)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


# ---------------------------------------------------------------- filterToQuery


def test_filter_to_query_builds_min_max_and_process_keys():
    out = run("filterToQuery", {
        "density_g_cm3_min": "1.5",
        "density_g_cm3_max": "3",
        "E_gpa_min": "60",
        "yield_mpa_min": "",
        "max_service_temp_c_min": None,
        "cost_usd_kg_max": "20",
        "process": "cnc",
        "basis": "typical",
        "category": "metal",
        "subcategory": "aluminum",
    })
    assert out == {
        "category": "metal",
        "subcategory": "aluminum",
        "filter": {
            "density_g_cm3_min": 1.5,
            "density_g_cm3_max": 3,
            "E_gpa_min": 60,
            "cost_usd_kg_max": 20,
            "process": "cnc",
            "basis": "typical",
        },
    }


def test_filter_to_query_omits_blank_and_non_finite_fields():
    out = run("filterToQuery", {"density_g_cm3_min": "", "E_gpa_min": "not a number"})
    assert out == {"filter": {}}


def test_filter_to_query_with_no_filters_is_an_empty_object():
    assert run("filterToQuery", {}) == {"filter": {}}
    assert run("filterToQuery", None) == {"filter": {}}


# ------------------------------------------------------------------ treeCounts


def test_tree_counts_groups_by_category_and_subcategory():
    rows = [
        {"category": "metal", "subcategory": "aluminum"},
        {"category": "metal", "subcategory": "aluminum"},
        {"category": "metal", "subcategory": "steel"},
        {"category": "polymer", "subcategory": "commodity"},
        {"category": "polymer", "subcategory": None},
    ]
    out = run("treeCounts", rows)
    assert out["metal"]["count"] == 3
    assert out["metal"]["subcategories"] == {"aluminum": 2, "steel": 1}
    assert out["polymer"]["count"] == 2
    assert out["polymer"]["subcategories"] == {"commodity": 1}


def test_tree_counts_of_empty_rows_is_empty():
    assert run("treeCounts", []) == {}


# ------------------------------------------------------------------ compareRows


def test_compare_rows_renders_ranges_missing_and_values():
    records = [
        {"id": "a", "properties": {
            "density_g_cm3": {"value": 2.7, "unit": "g/cm3", "basis": "typical"},
            "yield_mpa": {"range": [240, 280], "unit": "MPa", "basis": "minimum"},
        }},
        {"id": "b", "properties": {
            "density_g_cm3": {"value": 7.85, "unit": "g/cm3", "basis": "typical"},
            # no yield_mpa on b at all
        }},
    ]
    out = run("compareRows", records, ["density_g_cm3", "yield_mpa"])
    by_key = {row["key"]: row for row in out}
    assert by_key["density_g_cm3"]["values"] == ["2.7 g/cm3", "7.85 g/cm3"]
    assert by_key["density_g_cm3"]["label"] == "Density"
    assert by_key["yield_mpa"]["values"] == ["240–280 MPa", "—"]


def test_compare_rows_of_zero_records_is_still_one_row_per_key_all_missing():
    out = run("compareRows", [], ["density_g_cm3"])
    assert out == [{"key": "density_g_cm3", "label": "Density", "unit": "g/cm3",
                    "values": []}]


# ------------------------------------------------------------------- basisBadge


@pytest.mark.parametrize("basis,text,cls", [
    ("typical", "typical", "mat-badge-typical"),
    ("minimum", "minimum", "mat-badge-minimum"),
    ("characteristic", "characteristic", "mat-badge-characteristic"),
])
def test_basis_badge_known_bases(basis, text, cls):
    assert run("basisBadge", basis) == {"text": text, "cls": cls}


@pytest.mark.parametrize("basis", [None, "uncited", "made_up"])
def test_basis_badge_unknown_or_null_is_uncited(basis):
    assert run("basisBadge", basis) == {"text": "uncited", "cls": "mat-badge-uncited"}


# --------------------------------------------------------------------- sortRows


def test_sort_rows_numeric_ascending_and_descending():
    rows = [{"id": "a", "density_g_cm3": 7.85}, {"id": "b", "density_g_cm3": 1.2},
            {"id": "c", "density_g_cm3": 2.7}]
    asc = run("sortRows", rows, "density_g_cm3", "asc")
    assert [r["id"] for r in asc] == ["b", "c", "a"]
    desc = run("sortRows", rows, "density_g_cm3", "desc")
    assert [r["id"] for r in desc] == ["a", "c", "b"]


def test_sort_rows_missing_values_sort_last_both_directions():
    rows = [{"id": "a", "yield_mpa": 240}, {"id": "b"}, {"id": "c", "yield_mpa": 100}]
    asc = run("sortRows", rows, "yield_mpa", "asc")
    assert [r["id"] for r in asc] == ["c", "a", "b"]
    desc = run("sortRows", rows, "yield_mpa", "desc")
    assert [r["id"] for r in desc] == ["a", "c", "b"]


def test_sort_rows_is_stable_for_ties():
    rows = [{"id": "a", "category": "metal"}, {"id": "b", "category": "metal"},
            {"id": "c", "category": "polymer"}]
    out = run("sortRows", rows, "category", "asc")
    assert [r["id"] for r in out] == ["a", "b", "c"]


# =================================================================== HTTP live


def _local_client(kernel, tmp_path) -> TestClient:
    security_module.install(None)
    service = make_test_service(tmp_path / "projects", kernel)
    app = create_app(service, build_registry(service),
                     extra_allowed_hosts={"testserver"})
    return TestClient(app, base_url="http://127.0.0.1")


def test_list_route_filter_and_category_matches_what_the_view_relies_on(kernel, tmp_path):
    """The view sends `GET /api/materials?category=&filter=<json>`
    (`materials_model.filterToQuery` builds exactly this shape). Prove the
    live route answers only qualifying rows for a real query the filter bar
    can produce."""
    client = _local_client(kernel, tmp_path)
    r = client.get("/api/materials", params={
        "category": "metal",
        "filter": json.dumps({"yield_mpa_min": 200}),
    })
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == len(body["materials"])
    assert body["count"] >= 1
    for row in body["materials"]:
        assert row["category"] == "metal"
        assert row.get("yield_mpa") is not None and row["yield_mpa"] >= 200


def test_get_route_by_id_carries_properties_for_the_detail_pane(kernel, tmp_path):
    """The detail pane reads `properties` off `GET /api/materials/{id}` —
    the summary row (`GET /api/materials`) never carries it."""
    client = _local_client(kernel, tmp_path)
    r = client.get("/api/materials/al6061")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "al6061"
    assert "properties" in body and body["properties"]
    for key, prop in body["properties"].items():
        assert "unit" in prop and "basis" in prop
