import json
import re

import pytest

from agentcad.core.materials import (BASES, CATEGORIES, LIBRARY_VERSION,
                                     MATERIALS, PROPERTY_UNITS, SUBCATEGORIES,
                                     MaterialLibrary, Property, get_material,
                                     normalize_entry, validate_material_entry)
from agentcad.core.model import ValidationError
from agentcad.core.tools import build_registry

from .conftest import BOX_SCRIPT, make_test_service

# Every id shipped by the v1 Python table, with its density. These are pinned
# forever: `service._cache_key` hashes the density, so a changed number
# silently invalidates every existing mesh cache (PRD-028 AC1, and the
# editorial immutability rule — a re-based material gets a NEW id).
V1_DENSITIES = {
    "al6061": 2.70, "steel_a36": 7.85, "stainless_316": 8.00, "ti6al4v": 4.43,
    "inconel718": 8.19, "abs": 1.04, "pla": 1.24, "nylon_pa12": 1.01,
    "concrete": 2.40, "douglas_fir": 0.53,
}
LEGACY_DENSITIES = dict(V1_DENSITIES, **{
    "al7075": 2.81, "al2024": 2.78, "alsi10mg": 2.67, "inconel625": 8.44,
    "stainless_304": 8.00, "ss17_4ph": 7.75, "steel_4130": 7.85,
    "steel_4340": 7.85, "maraging300": 8.00, "steel_a992": 7.85,
    "copper_c110": 8.94, "brass_c260": 8.53, "bronze_c932": 8.93,
    "petg": 1.27, "pc": 1.20, "peek": 1.30, "ultem": 1.27,
    "cfrp_qi": 1.60, "gfrp_qi": 1.85, "glulam": 0.42,
})

# Full rows of the pre-migration table for a spread of families — the proof
# that the move from `_m(...)` to materials_data/*.json re-attributed the
# records without re-valuing them.
LEGACY_ROWS = {
    "al6061": dict(E_gpa=68.9, yield_mpa=276, ultimate_mpa=310,
                   elongation_pct=12, cte_um_m_k=23.6, k_w_m_k=167,
                   max_service_temp_c=150, cost_usd_kg=4.0),
    "steel_a36": dict(E_gpa=200, yield_mpa=250, ultimate_mpa=450,
                      elongation_pct=20, cte_um_m_k=11.7, k_w_m_k=50,
                      max_service_temp_c=400, cost_usd_kg=0.8),
    "ti6al4v": dict(E_gpa=114, yield_mpa=880, ultimate_mpa=950,
                    elongation_pct=14, cte_um_m_k=8.6, k_w_m_k=6.7,
                    max_service_temp_c=350, cost_usd_kg=35),
    "ultem": dict(E_gpa=3.2, yield_mpa=110, ultimate_mpa=105,
                  elongation_pct=60, cte_um_m_k=56, k_w_m_k=0.22,
                  max_service_temp_c=170, cost_usd_kg=40),
    "cfrp_qi": dict(E_gpa=50, yield_mpa=None, ultimate_mpa=600,
                    elongation_pct=1.5, cte_um_m_k=3.0, k_w_m_k=5.0,
                    max_service_temp_c=120, cost_usd_kg=40),
    "concrete": dict(E_gpa=33, yield_mpa=None, ultimate_mpa=2.9,
                     elongation_pct=None, cte_um_m_k=10, k_w_m_k=1.8,
                     max_service_temp_c=250, cost_usd_kg=0.10),
    "douglas_fir": dict(E_gpa=13.4, yield_mpa=None, ultimate_mpa=85,
                        elongation_pct=None, cte_um_m_k=4, k_w_m_k=0.12,
                        max_service_temp_c=60, cost_usd_kg=1.0),
}

AGGREGATORS = re.compile(r"matweb|makeitfrom|prospector|granta", re.I)


def card(**over) -> dict:
    """A minimal, fully cited v2 card."""
    base = {
        "label": "Test alloy", "category": "metal", "subcategory": "aluminum",
        "condition": "T6", "standards": ["ASTM B209"],
        "properties": {
            "density_g_cm3": {"value": 2.7, "unit": "g/cm3",
                              "basis": "typical", "source": "ASM Vol. 2"},
            "yield_mpa": {"value": 276, "unit": "MPa", "basis": "minimum",
                          "source": "ASTM B209"},
        },
    }
    base.update(over)
    return base


# ---------------------------------------------------------------- migration

def test_v1_ids_and_densities_preserved():
    assert len(MATERIALS) >= 30
    for mid, density in LEGACY_DENSITIES.items():
        assert get_material(mid).density_g_cm3 == pytest.approx(density)


def test_legacy_rows_migrated_without_revaluation():
    for mid, row in LEGACY_ROWS.items():
        material = get_material(mid)
        for key, expected in row.items():
            actual = getattr(material, key)
            if expected is None:
                assert actual is None, f"{mid}.{key}"
            else:
                assert actual == pytest.approx(expected), f"{mid}.{key}"


def test_builtins_are_versioned_and_cited():
    al = get_material("al6061")
    assert al.source == "builtin"
    assert al.library_version == LIBRARY_VERSION
    assert al.subcategory == "aluminum" and al.condition == "T6"
    assert al.prop("density_g_cm3").source
    assert al.prop("yield_mpa").basis == "typical"
    # A36's spec minimums are labelled as such (basis honesty, FR3).
    assert get_material("steel_a36").prop("yield_mpa").basis == "minimum"
    for material in MATERIALS.values():
        assert not material.to_payload()["uncited"], material.id


def test_no_builtin_source_names_an_aggregator():
    for material in MATERIALS.values():
        for prop in material.properties.values():
            assert not AGGREGATORS.search(prop.source or ""), material.id
        process = material.process or {}
        assert not AGGREGATORS.search(str(process.get("source") or ""))


def test_process_and_links_survived_the_migration():
    al = get_material("al6061")
    assert al.process["machinability"] == "excellent"
    assert al.process["sheet"]["k_factor_range"] == [0.33, 0.5]
    assert al.process["source"]
    assert al.links[0]["url"].startswith("https://")
    assert get_material("pla").process["printable"]["fdm"] == "excellent"


# ------------------------------------------------------------------ schema

def test_property_units_are_closed_and_canonical():
    assert len(PROPERTY_UNITS) == 15
    assert PROPERTY_UNITS["density_g_cm3"] == "g/cm3"
    assert PROPERTY_UNITS["cte_um_m_k"] == "um/(m*K)"
    assert PROPERTY_UNITS["poisson_ratio"] == "-"
    assert BASES == ("typical", "minimum", "characteristic")
    assert set(SUBCATEGORIES) == set(CATEGORIES)
    assert "ceramic" in CATEGORIES and "masonry" in CATEGORIES


def test_property_point_and_at_without_table():
    p = Property("E_gpa", 70.0, None, "GPa")
    assert p.point == pytest.approx(70.0)
    assert p.at(300) == (pytest.approx(70.0), False, False)
    r = Property("cost_usd_kg", None, (3.0, 5.0), "USD/kg")
    assert r.point == pytest.approx(4.0)


def test_property_at_interpolates_and_clamps():
    p = Property("k_w_m_k", 167.0, None, "W/(m*K)",
                 table=((25.0, 167.0), (100.0, 172.0), (200.0, 177.0)))
    # exactly on a row
    value, interpolated, clamped = p.at(100)
    assert (value, interpolated, clamped) == (pytest.approx(172.0), True, False)
    # between rows
    value, interpolated, clamped = p.at(62.5)
    assert (value, interpolated, clamped) == (pytest.approx(169.5), True, False)
    # below and above: clamped to the end rows, flagged
    assert p.at(-40) == (pytest.approx(167.0), True, True)
    assert p.at(500) == (pytest.approx(177.0), True, True)


def test_v1_flat_entry_normalizes_to_typical_and_uncited():
    material = validate_material_entry(
        "custom_al", {"density_g_cm3": 2.7, "E_gpa": 70, "category": "metal",
                      "notes": "hand entered"}, "project")
    assert material.density_g_cm3 == pytest.approx(2.7)
    assert material.source == "project"
    assert material.prop("E_gpa").basis == "typical"
    assert material.prop("E_gpa").unit == "GPa"
    assert material.prop("E_gpa").source is None
    payload = material.to_payload()
    assert payload["basis"]["E_gpa"] == "typical"
    assert sorted(payload["uncited"]) == ["E_gpa", "density_g_cm3"]
    assert payload["subcategory"] is None and payload["standards"] == []


def test_v2_card_validates_and_keeps_sources():
    material = normalize_entry("test_alloy", card(), "project")
    assert material.density_g_cm3 == pytest.approx(2.7)
    assert material.yield_mpa == pytest.approx(276)
    assert material.subcategory == "aluminum"
    assert material.standards == ("ASTM B209",)
    assert material.prop("yield_mpa").source == "ASTM B209"
    payload = material.to_payload()
    assert payload["basis"]["yield_mpa"] == "minimum"
    assert payload["uncited"] == []
    assert "properties" not in payload
    full = material.to_payload(full=True)
    assert full["properties"]["yield_mpa"] == {
        "value": 276, "unit": "MPa", "basis": "minimum", "source": "ASTM B209"}


def test_payload_keeps_the_v1_flat_shape():
    payload = get_material("al6061").to_payload()
    assert payload["id"] == "al6061" and payload["label"] == "Aluminum 6061-T6"
    assert payload["category"] == "metal" and payload["source"] == "builtin"
    assert payload["density_g_cm3"] == pytest.approx(2.70)
    assert payload["E_gpa"] == pytest.approx(68.9)
    assert payload["cost_usd_kg"] == pytest.approx(4.0)
    # v2 additions
    assert payload["subcategory"] == "aluminum"
    assert payload["condition"] == "T6"
    assert payload["standards"] and payload["links"]
    assert payload["process"]["machinability"] == "excellent"
    assert payload["warnings"] == []
    assert payload["library_version"] == LIBRARY_VERSION
    assert payload["basis"]["density_g_cm3"] == "typical"


def test_cost_may_be_written_beside_properties():
    material = normalize_entry("test_alloy", card(
        cost_usd_kg={"range": [3.0, 5.0], "as_of": "2025",
                     "source": "editorial estimate"}), "project")
    assert material.cost_usd_kg == pytest.approx(4.0)
    cost = material.prop("cost_usd_kg")
    assert cost.range == (3.0, 5.0) and cost.as_of == "2025"
    assert cost.unit == "USD/kg"


def test_density_range_resolves_to_midpoint_with_a_warning():
    entry = card()
    entry["properties"]["density_g_cm3"] = {
        "range": [2.6, 2.8], "unit": "g/cm3", "source": "handbook"}
    material = normalize_entry("test_alloy", entry, "project")
    assert material.density_g_cm3 == pytest.approx(2.7)
    assert material.warnings == ("density_range_midpoint",)
    assert material.to_payload()["warnings"] == ["density_range_midpoint"]


def test_property_table_is_kept_and_interpolated():
    entry = card()
    entry["properties"]["k_w_m_k"] = {
        "value": 167, "unit": "W/(m*K)", "source": "ASM",
        "table": [[25, 167], [200, 177]]}
    material = normalize_entry("test_alloy", entry, "project")
    assert material.k_w_m_k == pytest.approx(167)
    assert material.prop("k_w_m_k").at(112.5)[0] == pytest.approx(172.0)
    full = material.to_payload(full=True)
    assert full["properties"]["k_w_m_k"]["table"] == [[25, 167], [200, 177]]


@pytest.mark.parametrize("mutate", [
    # unknown property key
    lambda c: c["properties"].__setitem__("stiffness", {
        "value": 1, "unit": "GPa", "source": "s"}),
    # wrong unit
    lambda c: c["properties"]["yield_mpa"].__setitem__("unit", "psi"),
    # inverted range
    lambda c: c["properties"].__setitem__("cost_usd_kg", {
        "range": [9, 3], "unit": "USD/kg", "source": "s"}),
    # neither value nor range
    lambda c: c["properties"].__setitem__("E_gpa", {
        "unit": "GPa", "source": "s"}),
    # both value and range
    lambda c: c["properties"].__setitem__("E_gpa", {
        "value": 70, "range": [60, 80], "unit": "GPa", "source": "s"}),
    # unknown basis
    lambda c: c["properties"]["yield_mpa"].__setitem__("basis", "guess"),
    # mixing a flat numeric key with `properties`
    lambda c: c.__setitem__("E_gpa", 70),
    # unknown top-level key
    lambda c: c.__setitem__("colour", "red"),
    # non-monotonic table
    lambda c: c["properties"]["yield_mpa"].__setitem__(
        "table", [[100, 250], [20, 276]]),
    # a one-row table
    lambda c: c["properties"]["yield_mpa"].__setitem__("table", [[20, 276]]),
    # unknown subcategory for the category
    lambda c: c.__setitem__("subcategory", "softwood"),
    # unknown process rating
    lambda c: c.__setitem__("process", {"machinability": "superb",
                                        "source": "editorial"}),
    # unknown process key
    lambda c: c.__setitem__("process", {"grindability": "good",
                                        "source": "editorial"}),
    # unknown print process
    lambda c: c.__setitem__("process", {"printable": {"cnc": "good"},
                                        "source": "editorial"}),
    # k-factor out of (0, 1]
    lambda c: c.__setitem__("process", {
        "sheet": {"k_factor_range": [0.3, 1.4]}, "source": "editorial"}),
    # cost in two places
    lambda c: (c["properties"].__setitem__("cost_usd_kg", {
        "value": 4, "unit": "USD/kg", "source": "s"}),
        c.__setitem__("cost_usd_kg", {"value": 4, "source": "s"})),
    # links that are not {label, url}
    lambda c: c.__setitem__("links", ["https://example.com"]),
    # standards that are not strings
    lambda c: c.__setitem__("standards", [{"id": "ASTM B209"}]),
    # density outside the sanity band
    lambda c: c["properties"]["density_g_cm3"].__setitem__("value", 99.0),
    # missing density
    lambda c: c["properties"].pop("density_g_cm3"),
])
def test_card_refusals(mutate):
    entry = card()
    mutate(entry)
    with pytest.raises(ValidationError):
        normalize_entry("test_alloy", entry, "project")


def test_v1_rejections_are_preserved():
    for bad in ({"E_gpa": 5},                        # no density
                {"density_g_cm3": 2.0, "bogus": 1},  # unknown field
                {"density_g_cm3": 0},                # non-positive density
                {"density_g_cm3": 99},               # absurd density
                {"density_g_cm3": True},             # bool is not a number
                {"density_g_cm3": 2.0, "E_gpa": -1},
                {"density_g_cm3": 2.0, "category": "unobtanium"},
                {"density_g_cm3": 2.0, "label": 7}):
        with pytest.raises(ValidationError):
            validate_material_entry("x", bad, "project")
    with pytest.raises(ValidationError):
        validate_material_entry("Bad Id", {"density_g_cm3": 2.0}, "project")


# ------------------------------------------------------------------ layers

def test_unknown_material_raises():
    with pytest.raises(ValidationError):
        get_material("unobtanium")


def test_layered_precedence(tmp_path):
    global_file = tmp_path / "materials.json"
    global_file.write_text(json.dumps({
        "materials": {"al6061": {"density_g_cm3": 9.99}, "custom_g": {"density_g_cm3": 1.5}}
    }), encoding="utf-8")
    lib = MaterialLibrary(global_path=global_file)
    # global overrides builtin
    assert lib.resolve("al6061").density_g_cm3 == pytest.approx(9.99)
    assert lib.resolve("al6061").source == "global"
    # project overrides global
    proj = {"al6061": {"density_g_cm3": 3.33}}
    assert lib.resolve("al6061", proj).density_g_cm3 == pytest.approx(3.33)
    assert lib.resolve("al6061", proj).source == "project"
    # new custom id resolves
    assert lib.resolve("custom_g").density_g_cm3 == pytest.approx(1.5)


def test_layered_precedence_accepts_a_v2_card(tmp_path):
    lib = MaterialLibrary(global_path=tmp_path / "none.json")
    material = lib.resolve("test_alloy", {"test_alloy": card()})
    assert material.source == "project"
    assert material.prop("yield_mpa").source == "ASTM B209"


def test_entry_validation_rejects_bad():
    lib = MaterialLibrary(global_path="/nonexistent")
    with pytest.raises(ValidationError):
        lib.resolve("x", {"x": {"E_gpa": 5}})  # missing required density
    with pytest.raises(ValidationError):
        lib.resolve("x", {"x": {"density_g_cm3": 2.0, "bogus": 1}})  # unknown field


# -------------------------------------------------------------------- pack

@pytest.fixture
def service(kernel, tmp_path):
    return make_test_service(tmp_path / "projects", kernel)


def test_tool_pack_activates_resolver_and_custom_material(service):
    registry = build_registry(service)
    # registering the pack swapped in the project-aware resolver
    assert type(service.materials).__name__ == "ProjectMaterialResolver"

    service.create_project("demo")
    # define a custom dense alloy and use it
    result = registry.call("set_project_materials", {
        "project": "demo",
        "materials": {"heavy_al": {"density_g_cm3": 5.4, "label": "Heavy",
                                   "category": "metal"}},
    })
    assert any(m["id"] == "heavy_al" for m in result["materials"])

    service.create_part("demo", "box", script=BOX_SCRIPT, material="heavy_al")
    metrics = service.get_metrics("demo", "box")
    # 1000 mm^3 * 5.4 g/cm^3 / 1000 = 5.4 g
    assert metrics["mass_g"] == pytest.approx(5.4, rel=1e-6)


def test_list_materials_has_caveat(service):
    registry = build_registry(service)
    result = registry.call("list_materials", {})
    assert "allowables" in result["caveat"]
    assert len(result["materials"]) >= 30
    al = next(m for m in result["materials"] if m["id"] == "al6061")
    assert al["E_gpa"] == pytest.approx(68.9)
    assert al["source"] == "builtin"


def test_create_project_pins_the_library_version(service):
    service.create_project("demo")
    manifest = service.store.manifest("demo")
    assert manifest["materials_library"] == LIBRARY_VERSION


def test_list_materials_reports_the_library_version(service):
    registry = build_registry(service)
    service.create_project("demo")
    result = registry.call("list_materials", {"project": "demo"})
    assert result["library_version"] == LIBRARY_VERSION
    assert result["project_library_version"] == LIBRARY_VERSION
    assert result["count"] == len(result["materials"])
    assert result["warnings"] == []


def test_set_project_materials_refreshes_the_pin(service):
    registry = build_registry(service)
    service.create_project("demo")
    manifest = service.store.manifest("demo")
    manifest["materials_library"] = "1.0.0"
    service.store.save_manifest("demo", manifest)

    registry.call("set_project_materials", {
        "project": "demo",
        "materials": {"heavy_al": {"density_g_cm3": 5.4}}})
    assert service.store.manifest("demo")["materials_library"] == LIBRARY_VERSION


@pytest.mark.parametrize("pinned,warning", [
    ("99.0.0", "library_version_newer_than_shipped"),
    ("two point oh", "library_version_unreadable"),
])
def test_list_materials_warns_about_a_newer_pin(service, pinned, warning):
    registry = build_registry(service)
    service.create_project("demo")
    manifest = service.store.manifest("demo")
    manifest["materials_library"] = pinned
    service.store.save_manifest("demo", manifest)

    result = registry.call("list_materials", {"project": "demo"})
    assert result["project_library_version"] == pinned
    assert warning in result["warnings"]
