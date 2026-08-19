"""Pure tests for `core/materials_query.py` (PRD-028 slice 2, FR6/G5).

No I/O, no kernel, no service — every material here is hand-built with
`mat()` so the bound rule, the process mapping and the ranking arithmetic are
exercised in isolation from the shipped library (whose card count is still
growing under a concurrent curation pass).
"""

from __future__ import annotations

import pytest

from agentcad.core.materials import PROPERTY_UNITS, Material, Property
from agentcad.core.materials_query import (
    CONSTRAINT_PROCESSES,
    find,
    nearest_relaxation,
    normalize_constraints,
    parse_constraints,
    qualifies,
    rank,
    row,
)
from agentcad.core.model import ValidationError


def mat(id, *, category="metal", subcategory="aluminum", condition=None,
        process=None, **props) -> Material:
    """A minimal Material for pure query tests: every keyword is a property
    (a number -> a `value` point, a tuple -> a `range`, a `Property` -> kept
    verbatim so a test can set `basis`/`source` explicitly)."""
    properties: dict[str, Property] = {}
    flat: dict[str, float | None] = {}
    for key, val in props.items():
        if isinstance(val, Property):
            p = val
        elif isinstance(val, tuple):
            p = Property(key=key, value=None, range=val, unit=PROPERTY_UNITS[key],
                        basis="typical", source="test")
        else:
            p = Property(key=key, value=val, range=None, unit=PROPERTY_UNITS[key],
                        basis="typical", source="test")
        properties[key] = p
        flat[key] = p.point
    density = flat.pop("density_g_cm3", 2.0)
    return Material(id=id, label=id, density_g_cm3=density, category=category,
                    subcategory=subcategory, condition=condition, process=process,
                    properties=properties, **flat)


# ------------------------------------------------------------------ grammar

def test_constraint_processes_matches_the_spec_grammar():
    assert CONSTRAINT_PROCESSES == {
        "cnc": ("machinability",), "weld": ("weldability",),
        "fdm": ("printable", "fdm"), "sla": ("printable", "sla"),
        "sls": ("printable", "sls"), "mjf": ("printable", "mjf"),
        "dmls": ("printable", "dmls"), "im": ("im",), "sheet": ("sheet",),
        "casting": ("casting",),
    }


def test_unknown_constraint_key_lists_the_grammar():
    with pytest.raises(ValidationError) as exc:
        parse_constraints({"bogus_key": 1})
    assert "unknown constraint" in str(exc.value)
    known = exc.value.details["known"]
    assert "category" in known and "process" in known and "basis" in known
    assert "yield_mpa_min" in known and "yield_mpa_max" in known


def test_unknown_category_subcategory_process_basis_refuse():
    for bad in ({"category": "unobtanium"}, {"subcategory": "nope"},
                {"process": "laser"}, {"basis": "guess"}):
        with pytest.raises(ValidationError):
            parse_constraints(bad)


def test_bool_is_rejected_as_a_numeric_constraint():
    with pytest.raises(ValidationError):
        parse_constraints({"yield_mpa_min": True})


def test_min_greater_than_max_refuses():
    with pytest.raises(ValidationError):
        parse_constraints({"yield_mpa_min": 500, "yield_mpa_max": 100})


def test_normalize_constraints_merges_category_and_subcategory():
    c = normalize_constraints({"yield_mpa_min": 1}, category="metal",
                              subcategory="steel")
    assert c.category == "metal" and c.subcategory == "steel"
    with pytest.raises(ValidationError):
        normalize_constraints({}, category="nope")


# ---------------------------------------------------------------- qualifies

def test_range_satisfies_min_by_lower_bound_and_max_by_upper_bound():
    m = mat("x", cost_usd_kg=(3.0, 5.0))
    assert qualifies(m, parse_constraints({"cost_usd_kg_min": 3.0})) is not None
    assert qualifies(m, parse_constraints({"cost_usd_kg_min": 3.5})) is None
    assert qualifies(m, parse_constraints({"cost_usd_kg_max": 5.0})) is not None
    assert qualifies(m, parse_constraints({"cost_usd_kg_max": 4.5})) is None


def test_missing_property_does_not_qualify():
    m = mat("x")  # no yield_mpa at all
    assert qualifies(m, parse_constraints({"yield_mpa_min": 100})) is None


def test_qualifies_with_no_constraints_is_the_empty_evidence_dict():
    m = mat("x")
    assert qualifies(m, parse_constraints(None)) == {}
    assert qualifies(m, parse_constraints({})) == {}


def test_category_and_subcategory_constraints():
    m = mat("x", category="polymer", subcategory="commodity")
    assert qualifies(m, parse_constraints({"category": "polymer"})) is not None
    assert qualifies(m, parse_constraints({"category": "metal"})) is None
    assert qualifies(m, parse_constraints({"subcategory": "commodity"})) is not None
    assert qualifies(m, parse_constraints({"subcategory": "aluminum"})) is None


def test_constraining_evidence_carries_unit_basis_source():
    p = Property("yield_mpa", 300, None, "MPa", basis="minimum", source="ASM")
    m = mat("x", yield_mpa=p)
    evidence = qualifies(m, parse_constraints({"yield_mpa_min": 200}))
    assert evidence == {"yield_mpa": {"value": 300, "unit": "MPa",
                                       "basis": "minimum", "source": "ASM"}}


def test_process_cnc_maps_to_machinability_rating():
    good = mat("good", process={"machinability": "good", "source": "s"})
    poor = mat("poor", process={"machinability": "poor", "source": "s"})
    absent = mat("absent")
    c = parse_constraints({"process": "cnc"})
    assert qualifies(good, c) == {}
    assert qualifies(poor, c) is None
    assert qualifies(absent, c) is None


def test_process_sheet_qualifies_on_block_presence():
    m = mat("sheet", process={"sheet": {"k_factor_range": [0.3, 0.5]},
                              "source": "s"})
    assert qualifies(m, parse_constraints({"process": "sheet"})) == {}
    assert qualifies(mat("none"), parse_constraints({"process": "sheet"})) is None


def test_process_fdm_reads_the_nested_printable_block():
    m = mat("fdm_ok", process={"printable": {"fdm": "excellent"}, "source": "s"})
    n = mat("fdm_poor", process={"printable": {"fdm": "poor"}, "source": "s"})
    c = parse_constraints({"process": "fdm"})
    assert qualifies(m, c) == {}
    assert qualifies(n, c) is None


def test_basis_restricts_to_matching_constraining_properties():
    m = mat("x", yield_mpa=Property("yield_mpa", 250, None, "MPa",
                                    basis="minimum", source="s"))
    assert qualifies(m, parse_constraints(
        {"yield_mpa_min": 200, "basis": "minimum"})) is not None
    assert qualifies(m, parse_constraints(
        {"yield_mpa_min": 200, "basis": "typical"})) is None


# --------------------------------------------------------------------- rank

def test_prefer_ranks_min_direction_and_missing_property_ranks_last():
    cheap = mat("cheap", cost_usd_kg=1.0)
    mid = mat("mid", cost_usd_kg=5.0)
    pricey = mat("pricey", cost_usd_kg=10.0)
    none = mat("zzz_none")  # no cost_usd_kg; ties pricey at rank 1.0, sorts after by id
    candidates = [{"material": m, "constraining": {}}
                  for m in (pricey, none, mid, cheap)]
    ranked = rank(candidates, {"cost_usd_kg": "min"})
    assert [c["material"].id for c in ranked] == ["cheap", "mid", "pricey", "zzz_none"]
    assert ranked[0]["score"] == 0.0
    assert ranked[-1]["score"] == 1.0


def test_prefer_ranks_max_direction():
    weak = mat("weak", yield_mpa=100.0)
    strong = mat("strong", yield_mpa=900.0)
    candidates = [{"material": m, "constraining": {}} for m in (weak, strong)]
    ranked = rank(candidates, {"yield_mpa": "max"})
    assert [c["material"].id for c in ranked] == ["strong", "weak"]
    assert ranked[0]["score"] == 0.0


def test_prefer_unknown_property_or_direction_refuses():
    candidates = [{"material": mat("x"), "constraining": {}}]
    with pytest.raises(ValidationError):
        rank(candidates, {"bogus": "min"})
    with pytest.raises(ValidationError):
        rank(candidates, {"yield_mpa": "sideways"})


def test_no_prefer_orders_by_category_subcategory_id():
    a = mat("bbb", category="metal", subcategory="steel")
    b = mat("aaa", category="metal", subcategory="aluminum")
    c = mat("ccc", category="polymer", subcategory="commodity")
    candidates = [{"material": m, "constraining": {}} for m in (a, c, b)]
    ranked = rank(candidates, None)
    assert [x["material"].id for x in ranked] == ["aaa", "bbb", "ccc"]
    assert "score" not in ranked[0]


def test_tied_score_falls_back_to_the_stable_tie_break():
    # Equal cost -> equal score; the winner is decided by (category, subcategory, id).
    a = mat("bbb", category="metal", subcategory="steel", cost_usd_kg=5.0)
    b = mat("aaa", category="metal", subcategory="steel", cost_usd_kg=5.0)
    candidates = [{"material": m, "constraining": {}} for m in (a, b)]
    ranked = rank(candidates, {"cost_usd_kg": "min"})
    assert [c["material"].id for c in ranked] == ["aaa", "bbb"]
    assert ranked[0]["score"] == ranked[1]["score"] == 0.0


# ------------------------------------------------------------ relaxation

def test_nearest_relaxation_names_the_more_helpful_drop():
    catalog = {
        "a": mat("a", yield_mpa=300.0, max_service_temp_c=100.0),
        "b": mat("b", yield_mpa=100.0, max_service_temp_c=200.0),
        "d": mat("d", yield_mpa=100.0, max_service_temp_c=250.0),
    }
    c = parse_constraints({"yield_mpa_min": 250, "max_service_temp_c_min": 150})
    assert all(qualifies(m, c) is None for m in catalog.values())
    result = nearest_relaxation(catalog, c)
    assert result == {"drop": "yield_mpa_min", "count": 2}


def test_nearest_relaxation_ties_pick_the_lexicographically_first_key():
    catalog = {
        "a": mat("a", yield_mpa=300.0, max_service_temp_c=100.0),
        "b": mat("b", yield_mpa=100.0, max_service_temp_c=200.0),
        "c": mat("c", yield_mpa=100.0, max_service_temp_c=100.0),
    }
    c = parse_constraints({"yield_mpa_min": 250, "max_service_temp_c_min": 150})
    result = nearest_relaxation(catalog, c)
    # dropping either admits exactly one record; "max_service_temp_c_min" sorts
    # before "yield_mpa_min".
    assert result == {"drop": "max_service_temp_c_min", "count": 1}


def test_nearest_relaxation_names_the_only_constraint_and_is_none_with_zero():
    """One constraint is the likeliest agent case: the relaxation names it
    (dropping it admits the whole catalog); zero constraints → nothing to drop."""
    catalog = {"a": mat("a", yield_mpa=100.0)}
    assert nearest_relaxation(catalog, parse_constraints({"yield_mpa_min": 500})) == {
        "drop": "yield_mpa_min", "count": 1}
    assert nearest_relaxation(catalog, parse_constraints({})) is None


def test_standalone_basis_means_carries_a_value_on_that_basis():
    """``{"basis": "minimum"}`` alone used to match everything (the basis was
    only tested inside the property loop). Now it means "at least one property
    on that basis", and the evidence is those properties."""
    spec_min = mat("spec_min", yield_mpa=Property("yield_mpa", 100.0, None, "MPa",
                                                  basis="minimum", source="s"))
    typical = mat("typical", yield_mpa=100.0)
    catalog = {"spec_min": spec_min, "typical": typical}
    rows = find(catalog, require={"basis": "minimum"})
    assert [r["id"] for r in rows] == ["spec_min"]
    assert set(rows[0]["constraining"]) == {"yield_mpa"}
    with pytest.raises(ValidationError):
        find(catalog, require={"basis": "characteristic"})


def test_nearest_relaxation_none_when_nothing_helps():
    catalog = {"a": mat("a", yield_mpa=10.0, max_service_temp_c=10.0)}
    c = parse_constraints({"yield_mpa_min": 500, "max_service_temp_c_min": 500})
    assert nearest_relaxation(catalog, c) is None


# ------------------------------------------------------------------- row

def test_row_shape_and_optional_score():
    p = Property("yield_mpa", 300, None, "MPa", basis="typical", source="ASM")
    m = mat("x", condition="T6", yield_mpa=p)
    constraining = qualifies(m, parse_constraints({"yield_mpa_min": 100}))
    r = row(m, constraining, score=0.25)
    assert r == {"id": "x", "label": "x", "category": "metal",
                 "subcategory": "aluminum", "condition": "T6",
                 "constraining": {"yield_mpa": {"value": 300, "unit": "MPa",
                                                 "basis": "typical", "source": "ASM"}},
                 "score": 0.25}
    assert "score" not in row(m, constraining)


# ------------------------------------------------------------------- find

def test_find_composes_qualify_and_rank(catalog=None):
    catalog = {
        "cheap": mat("cheap", yield_mpa=300.0, cost_usd_kg=1.0),
        "mid": mat("mid", yield_mpa=300.0, cost_usd_kg=5.0),
        "toolow": mat("toolow", yield_mpa=50.0, cost_usd_kg=1.0),
    }
    rows = find(catalog, require={"yield_mpa_min": 100}, prefer={"cost_usd_kg": "min"})
    assert [r["id"] for r in rows] == ["cheap", "mid"]
    assert rows[0]["constraining"]["yield_mpa"]["value"] == 300


def test_find_limit_default_cap_and_validation():
    catalog = {f"m{i}": mat(f"m{i}", yield_mpa=300.0) for i in range(5)}
    rows = find(catalog, require={"yield_mpa_min": 100}, limit=2)
    assert len(rows) == 2
    with pytest.raises(ValidationError):
        find(catalog, require={"yield_mpa_min": 100}, limit=51)
    with pytest.raises(ValidationError):
        find(catalog, require={"yield_mpa_min": 100}, limit=0)
    with pytest.raises(ValidationError):
        find(catalog, require={"yield_mpa_min": 100}, limit=True)


def test_find_zero_results_raises_with_nearest_relaxation_and_tried():
    catalog = {"a": mat("a", yield_mpa=100.0, max_service_temp_c=300.0),
               "b": mat("b", yield_mpa=300.0, max_service_temp_c=50.0)}
    with pytest.raises(ValidationError) as exc:
        find(catalog, require={"yield_mpa_min": 250, "max_service_temp_c_min": 200})
    assert "no material satisfies the constraints" in str(exc.value)
    details = exc.value.details
    assert details["nearest_relaxation"] is not None
    assert details["tried"] == {"yield_mpa_min": 250.0, "max_service_temp_c_min": 200.0}


def test_find_category_argument_merges_into_constraints():
    catalog = {"a": mat("a", category="metal", yield_mpa=300.0),
               "b": mat("b", category="polymer", yield_mpa=300.0)}
    rows = find(catalog, require={"yield_mpa_min": 100}, category="polymer")
    assert [r["id"] for r in rows] == ["b"]


def test_process_filter_works_on_the_real_catalog_not_only_on_dict_doubles():
    """Regression: ``Material.process`` is a read-only ``MappingProxyType``; the
    first cut tested ``isinstance(node, dict)`` and every process filter except
    ``sheet`` matched nothing on the shipped library while the hand-built
    doubles above (plain dicts) passed. The browser's process chips found it."""
    from agentcad.core.materials import MATERIALS

    cnc = {r["id"] for r in find(MATERIALS, require={"process": "cnc"}, limit=50)}
    assert "al6061" in cnc                      # machinability: excellent
    sls = {r["id"] for r in find(MATERIALS,
                                 require={"process": "sls"}, limit=50)}
    assert "nylon_pa12" in sls                  # printable.sls: excellent
    for proc in ("weld", "fdm", "dmls", "im", "casting"):
        assert find(MATERIALS, require={"process": proc}, limit=5), proc
