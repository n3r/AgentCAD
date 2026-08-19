"""PRD-028 Materials database — acceptance suite (AC1-AC7).

One place that grades the PRD's acceptance criteria against the shipped
surface, reusing the slice suites' own fixtures and pinned tables rather than
re-deriving them: `tests/test_materials.py`'s 30-id/density table (AC1),
`tests/test_fem_material_resolution.py`'s fake-kernel harness pattern (AC3,
AC6), `tests/test_materials_lint.py`'s subprocess CLI pattern (AC5).

| AC | Test |
|---|---|
| AC1 | `test_ac1_thirty_legacy_ids_and_densities_are_pinned` + `test_ac1_payload_keeps_the_flat_ui_keys_for_every_legacy_material` + `test_ac1_cache_key_invariance_across_materials_states` |
| AC2 | `test_ac2_find_materials_prd_query_every_row_qualifies_and_cites` + `test_ac2_prefer_cost_min_orders_the_qualifying_set_non_decreasing` + `test_ac2_find_materials_tool_is_wired` |
| AC3 | `test_ac3_fem_thermal_interpolates_inside_the_table` + `test_ac3_fem_thermal_clamps_outside_and_warns` + `test_ac3_real_solver_thermal_reports_the_interpolated_k` |
| AC4 | `test_ac4_library_size_and_every_property_cited` + `test_ac4_taxonomy_leaves_populated` + `test_ac4_provenance_file_exists_and_attests` |
| AC5 | `test_ac5_lint_card_rejects_missing_citation` + `test_ac5_cli_rejects_missing_citation_with_exit_1` |
| AC6 | `TestAC6ConstructionMaterials::test_ac6_mass_tracks_volume_times_density` + `TestAC6ConstructionMaterials::test_ac6_fem_static_uses_the_materials_modulus` + `test_ac6_real_solver_static_on_c24_base_plate` |
| AC7 | `test_ac7_no_aggregator_named_in_source_notes_or_label_and_links_are_https` |

A few of these are worth reading before you believe them:

* **AC1's cache-key invariance is not "two equal strings".** `_cache_key`
  hashes `(content, params, density, densities)` — no `Material`, no `basis`,
  no `source` — so the claim is that the materials-v2 *resolver's identity*
  cannot leak into a mesh cache key. Proved two ways: a service that never
  registers the materials pack (`_DefaultMaterialResolver`) and one that does
  (`ProjectMaterialResolver`) mint the *same* key for the same script, and the
  key an independent `service._cache_key(content, params, density)` call
  computes from the built part's own inputs matches the key the real build
  minted.
* **AC2's `prefer` claim is about the qualifying set's cost order, not the
  full catalog's.** The check filters `find_materials`' result rows down to
  the ones that carry a cost at all (many qualifying alloys are uncosted) and
  asserts that filtered sequence is non-decreasing — exactly what "ranked by
  cost, ascending" promises for materials `prefer` can compare.
* **AC6 reassigns `base_plate`'s material only, never its geometry.** The
  construction example ships it in `steel_a36`; EN 338 timber and EN 1992-1-1
  concrete are not remotely how a real base plate would be built, but the
  criterion is that the *density and modulus feed through correctly* — mass
  tracks `volume x density` within 5%, and the fake-kernel path proves
  `fem_static` sent the material's own `E_gpa * 1000`, not a steel default.

The construction example is opened from a **copy** (`shutil.copytree`,
`.cache`/`exports` excluded), the house rule for example-driven tests
(`tests/test_examples.py`).
"""

from __future__ import annotations

import inspect
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agentcad.core import materials_query
from agentcad.core.materials import LIBRARY_VERSION, MATERIALS, PROPERTY_UNITS, SUBCATEGORIES
from agentcad.core.materials_lint import lint_card
from agentcad.core.service import AgentCADService
from agentcad.core.tools import build_registry

from .conftest import BOX_SCRIPT, make_test_service
from .test_fem_material_resolution import FACES, FakeKernel, SYNTH_K, THERMAL_FACES
from .test_materials import AGGREGATORS, LEGACY_DENSITIES, card

REPO = Path(__file__).resolve().parents[1]
DATA_DIR = REPO / "agentcad" / "core" / "materials_data"
EXAMPLES = REPO / "examples"

#: `SUBCATEGORIES`' leaves that the PRD floors at >= 5 records because an
#: example or an integration test actually touches them (spec Decision 4).
TOUCHED_LEAVES = {
    "aluminum", "steel", "stainless", "titanium", "nickel", "copper",
    "commodity", "engineering", "high_performance", "laminate", "softwood",
    "engineered", "concrete",
}


def _lower_bound(evidence: dict) -> float:
    """A constraint-grammar evidence dict qualifies `_min` by its value or the
    lower end of its range (`materials_query.qualifies`) — read it back the
    same way to check the claim."""
    return evidence["value"] if "value" in evidence else evidence["range"][0]


def _copy_construction(tmp_path: Path) -> Path:
    dest = tmp_path / "projects" / "construction"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(EXAMPLES / "construction", dest,
                    ignore=shutil.ignore_patterns(".cache", "exports"))
    return dest


# ==================================================================== AC1

def test_ac1_thirty_legacy_ids_and_densities_are_pinned():
    """**AC1** — all 30 pre-migration materials still resolve with their
    exact density (the table is `test_materials.py`'s pinned
    `LEGACY_DENSITIES`, copied by import rather than re-derived here)."""
    assert len(LEGACY_DENSITIES) == 30
    for material_id, density in LEGACY_DENSITIES.items():
        assert material_id in MATERIALS, material_id
        assert MATERIALS[material_id].density_g_cm3 == pytest.approx(density)


def test_ac1_payload_keeps_the_flat_ui_keys_for_every_legacy_material():
    """`to_payload()` still carries the flat keys the UI/tests read: the
    v1 shape (`id, label, category, density_g_cm3, source`) plus every
    numeric property the material actually carries."""
    for material_id in LEGACY_DENSITIES:
        material = MATERIALS[material_id]
        payload = material.to_payload()
        for key in ("id", "label", "category", "density_g_cm3", "source"):
            assert key in payload, f"{material_id} payload missing {key!r}"
        for key in PROPERTY_UNITS:
            if getattr(material, key) is not None:
                assert key in payload, \
                    f"{material_id} payload missing numeric key {key!r}"


def test_ac1_cache_key_invariance_across_materials_states(kernel, tmp_path):
    """`service._cache_key` hashes `(content, params, density, densities)`
    only (`grep -n "_cache_key" agentcad/core/service.py`) — no Material
    object, no basis, no source. Two services in different materials-resolver
    states (the Wave-0 `_DefaultMaterialResolver` vs the v2
    `ProjectMaterialResolver` the materials pack installs) mint the identical
    cache key for the same script/params/material id."""
    svc_a = make_test_service(tmp_path / "a", kernel)          # never registers the pack
    svc_b = make_test_service(tmp_path / "b", kernel)
    build_registry(svc_b)                                       # swaps in the v2 resolver
    assert type(svc_a.materials).__name__ == "_DefaultMaterialResolver"
    assert type(svc_b.materials).__name__ == "ProjectMaterialResolver"

    for svc in (svc_a, svc_b):
        svc.create_project("demo")
        svc.create_part("demo", "box", script=BOX_SCRIPT, material="al6061")

    key_a = svc_a.mesh_info("demo", "box")["key"]
    key_b = svc_b.mesh_info("demo", "box")["key"]
    assert key_a == key_b, "materials-resolver identity leaked into the cache key"

    # The signature itself carries no materials-shaped parameter...
    sig = inspect.signature(AgentCADService._cache_key)
    assert list(sig.parameters) == ["self", "content", "params", "density", "densities"]

    # ...and a key recomputed from the same primitives independently
    # reproduces the one the real build minted.
    density = svc_a.material_density("demo", "al6061")
    assert density == pytest.approx(2.70)
    record = svc_a.store.get_part("demo", "box")
    content = svc_a._content_signature("demo", record)
    assert svc_a._cache_key(content, record.effective_params, density) == key_a


# ==================================================================== AC2

def test_ac2_find_materials_prd_query_every_row_qualifies_and_cites():
    """**AC2** — the PRD's exact query returns >= 1 row, and every row's
    constraining evidence clears the bar and names its source."""
    rows = materials_query.find(
        MATERIALS,
        require={"yield_mpa_min": 240, "max_service_temp_c_min": 150},
        limit=50,
    )
    assert len(rows) >= 1
    for row in rows:
        yield_evidence = row["constraining"]["yield_mpa"]
        temp_evidence = row["constraining"]["max_service_temp_c"]
        assert _lower_bound(yield_evidence) >= 240, row["id"]
        assert yield_evidence["source"], row["id"]
        assert _lower_bound(temp_evidence) >= 150, row["id"]
        assert temp_evidence["source"], row["id"]


def test_ac2_prefer_cost_min_orders_the_qualifying_set_non_decreasing():
    """`prefer: {cost_usd_kg: "min"}` ranks the qualifying set so that,
    among the rows that carry a cost at all, cost is non-decreasing."""
    rows = materials_query.find(
        MATERIALS,
        require={"yield_mpa_min": 240, "max_service_temp_c_min": 150},
        prefer={"cost_usd_kg": "min"}, limit=50,
    )
    costs = [MATERIALS[row["id"]].cost_usd_kg for row in rows
             if MATERIALS[row["id"]].cost_usd_kg is not None]
    assert len(costs) >= 2, "not enough costed rows to prove an order"
    assert costs == sorted(costs)


def test_ac2_find_materials_tool_is_wired(kernel, tmp_path):
    """The pure query engine is reachable through the registered tool, not
    just as a library function."""
    service = make_test_service(tmp_path / "projects", kernel)
    registry = build_registry(service)
    result = registry.call("find_materials", {
        "require": {"yield_mpa_min": 240, "max_service_temp_c_min": 150}})
    assert "error" not in result, result
    assert result["count"] >= 1
    assert len(result["materials"]) == result["count"]


# ==================================================================== AC3

@pytest.fixture
def fem_synth(kernel, tmp_path, monkeypatch):
    """A service whose FEM tools are registered without the `[fem]` extra
    (`fem_available` patched true before `build_registry`, the
    `test_fem_material_resolution.py` pattern) with one part on a project
    material carrying a synthetic `k_w_m_k` table."""
    from agentcad.kernel.handlers import fem as fem_module

    monkeypatch.setattr(fem_module, "fem_available", lambda: True)
    service = make_test_service(tmp_path / "projects", kernel)
    registry = build_registry(service)
    service.create_project("demo")
    registry.call("set_project_materials",
                  {"project": "demo", "materials": {"synth": SYNTH_K}})
    service.create_part("demo", "bar", script=BOX_SCRIPT, material="synth")
    fake = FakeKernel()
    service.kernel = fake
    return service, registry, fake


def test_ac3_fem_thermal_interpolates_inside_the_table(fem_synth):
    """**AC3** — a thermal FEM run on a material with a `k(T)` table
    interpolates at the case temperature (T_eval = mean of the two fixed
    faces: (100 + 20) / 2 = 60 C -> 47.5 W/(m*K) on `SYNTH_K`'s table)."""
    service, registry, fake = fem_synth
    result = registry.call("fem_thermal", {
        "project": "demo", "part_id": "bar",
        "t_hot_c": 100.0, "t_cold_c": 20.0, **THERMAL_FACES})
    assert "error" not in result, result
    assert fake.calls[-1][1]["k_w_m_k"] == pytest.approx(47.5)
    assert result["material_basis"]["k_w_m_k"]["interpolated"] is True
    assert result["material_basis"]["k_w_m_k"]["clamped"] is False
    assert result["warnings"] == []


def test_ac3_fem_thermal_clamps_outside_and_warns(fem_synth):
    """...and warns, `temperature_out_of_table_range:`-prefixed, when the
    evaluation temperature falls outside the table's span."""
    service, registry, fake = fem_synth
    result = registry.call("fem_thermal", {
        "project": "demo", "part_id": "bar",
        "t_hot_c": 500.0, "t_cold_c": 300.0, **THERMAL_FACES})
    assert "error" not in result, result
    assert fake.calls[-1][1]["k_w_m_k"] == pytest.approx(35.0)   # clamped to the table's end row
    assert result["material_basis"]["k_w_m_k"]["clamped"] is True
    assert any(w.startswith("temperature_out_of_table_range:")
              for w in result["warnings"]), result["warnings"]


def test_ac3_real_solver_thermal_reports_the_interpolated_k(kernel, tmp_path):
    """Real-solver variant: the same synthetic table, run through the actual
    `skfem` solver when the `[fem]` extra is present."""
    pytest.importorskip("skfem")
    pytest.importorskip("gmsh")
    pytest.importorskip("meshio")
    service = make_test_service(tmp_path / "projects", kernel)
    registry = build_registry(service)
    service.create_project("demo")
    registry.call("set_project_materials",
                  {"project": "demo", "materials": {"synth": SYNTH_K}})
    service.create_part("demo", "bar", script=BOX_SCRIPT, material="synth")
    result = registry.call("fem_thermal", {
        "project": "demo", "part_id": "bar",
        "t_hot_c": 100.0, "t_cold_c": 20.0, **THERMAL_FACES})
    assert "error" not in result, result
    assert result["material_basis"]["k_w_m_k"]["value"] == pytest.approx(47.5)
    assert result["flux_w"] > 0


# ==================================================================== AC4

def test_ac4_library_size_and_every_property_cited():
    """**AC4** — the browser's catalog is >= 300 records, and every builtin
    property carries a non-empty citation (AC4's "traces every value to its
    named source", machine half)."""
    assert len(MATERIALS) >= 300
    for material in MATERIALS.values():
        for key, prop in material.properties.items():
            assert prop.source, f"{material.id}.{key} has no source"


def test_ac4_taxonomy_leaves_populated():
    """Every one of the 30 taxonomy leaves has >= 1 record; the
    example-touched leaves have >= 5 (spec Decision 4's launch floor)."""
    all_leaves = {leaf for leaves in SUBCATEGORIES.values() for leaf in leaves}
    assert len(all_leaves) == 30
    counts: dict[str, int] = {}
    for material in MATERIALS.values():
        if material.subcategory:
            counts[material.subcategory] = counts.get(material.subcategory, 0) + 1
    for leaf in all_leaves:
        assert counts.get(leaf, 0) >= 1, f"taxonomy leaf {leaf!r} has zero records"
    for leaf in TOUCHED_LEAVES:
        assert leaf in all_leaves, f"{leaf!r} is not a real taxonomy leaf"
        assert counts.get(leaf, 0) >= 5, \
            f"example-touched leaf {leaf!r} has fewer than 5 records ({counts.get(leaf, 0)})"


def test_ac4_provenance_file_exists_and_attests():
    """The editorial QA record exists and carries the AC7 attestation
    (loose on purpose: a QA agent is actively rewriting this file's prose in
    a concurrent slice — see changelog 0291)."""
    path = DATA_DIR / "PROVENANCE.md"
    assert path.is_file(), f"{path} is missing"
    text = path.read_text(encoding="utf-8")
    assert "Prospector" in text


# ==================================================================== AC5

def _uncited_yield_card() -> dict:
    entry = card()
    del entry["properties"]["yield_mpa"]["source"]
    return entry


def test_ac5_lint_card_rejects_missing_citation():
    """**AC5** — `lint_card` rejects a card whose `yield_mpa` has no `source`
    with a `missing_citation` finding naming the property."""
    findings = lint_card("test_alloy", _uncited_yield_card(), "library")
    errors = [f for f in findings if f.level == "error"]
    assert any(f.code == "missing_citation" and f.property == "yield_mpa"
              for f in errors), errors


def test_ac5_cli_rejects_missing_citation_with_exit_1(tmp_path):
    """The same refusal through the real CLI subprocess (an exit code is the
    one thing an in-process call cannot prove — `test_materials_lint.py`'s
    own rationale)."""
    path = tmp_path / "dirty.json"
    path.write_text(json.dumps({
        "schema_version": 2,
        "materials": {"test_alloy": _uncited_yield_card()},
    }), encoding="utf-8")
    argv = [sys.executable, "-c", "from agentcad.cli import main; main()"]
    result = subprocess.run(argv + ["materials", "lint", str(path)],
                            capture_output=True, text=True, timeout=120,
                            env=dict(os.environ))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "missing_citation" in result.stdout
    assert "yield_mpa" in result.stdout


# ==================================================================== AC6

@pytest.mark.slow
class TestAC6ConstructionMaterials:
    """**AC6** — a wood (`c24`, EN 338) and a concrete (`concrete_c30_37`,
    EN 1992-1-1) record drive a plausible mass and static FEM on the
    construction example's `base_plate` (steel by default; only its
    *material* is reassigned, never its geometry)."""

    @pytest.fixture
    def rig(self, kernel, tmp_path, monkeypatch):
        from agentcad.kernel.handlers import fem as fem_module

        monkeypatch.setattr(fem_module, "fem_available", lambda: True)
        dest = _copy_construction(tmp_path)
        service = make_test_service(tmp_path / "projects", kernel)
        registry = build_registry(service)
        name = service.open_project(str(dest))["name"]
        return service, registry, name

    @pytest.mark.parametrize("material_id", ["c24", "concrete_c30_37"])
    def test_ac6_mass_tracks_volume_times_density(self, rig, material_id):
        service, registry, name = rig
        service.update_part(name, "base_plate", material=material_id)
        metrics = service.get_metrics(name, "base_plate")
        density = MATERIALS[material_id].density_g_cm3
        expected_mass_g = metrics["volume_mm3"] * density / 1000.0
        assert metrics["mass_g"] == pytest.approx(expected_mass_g, rel=0.05)

    @pytest.mark.parametrize("material_id,e_mpa", [
        ("c24", 11.0 * 1000.0),               # EN 338 C24 E_0,mean = 11 GPa
        ("concrete_c30_37", 33.0 * 1000.0),   # EN 1992-1-1 Table 3.1 E_cm = 33 GPa
    ])
    def test_ac6_fem_static_uses_the_materials_modulus(self, rig, material_id, e_mpa):
        service, registry, name = rig
        service.update_part(name, "base_plate", material=material_id)
        service.get_metrics(name, "base_plate")   # a real build exists before we fake the kernel
        fake = FakeKernel()
        service.kernel = fake
        result = registry.call("fem_static", {
            "project": name, "part_id": "base_plate", **FACES})
        assert "error" not in result, result
        assert fake.calls[-1][1]["E_mpa"] == pytest.approx(e_mpa)
        assert result["material_basis"]["E_mpa"]["basis"]


def test_ac6_real_solver_static_on_c24_base_plate(kernel, tmp_path):
    """Real-solver variant: the actual `skfem` static solve on the c24
    base plate, when the `[fem]` extra is present."""
    pytest.importorskip("skfem")
    pytest.importorskip("gmsh")
    pytest.importorskip("meshio")
    dest = _copy_construction(tmp_path)
    service = make_test_service(tmp_path / "projects", kernel)
    registry = build_registry(service)
    name = service.open_project(str(dest))["name"]
    service.update_part(name, "base_plate", material="c24")
    result = registry.call("fem_static", {
        "project": name, "part_id": "base_plate", **FACES})
    assert "error" not in result, result
    assert result["material_basis"]["E_mpa"]["value"] == pytest.approx(11000.0)
    assert result["max_disp_mm"] > 0


# ==================================================================== AC7

def test_ac7_no_aggregator_named_in_source_notes_or_label_and_links_are_https():
    """**AC7** — no builtin `source`, `notes` or `label` names a licensed
    aggregator; `links` is the one place an aggregator/allowables provider
    may legitimately appear (as an outbound label, e.g. "Prospector" or
    "MMPDS"), so only its `url` is checked, and it must be `https`."""
    for material in MATERIALS.values():
        assert not AGGREGATORS.search(material.label or ""), material.id
        assert not AGGREGATORS.search(material.notes or ""), material.id
        for key, prop in material.properties.items():
            assert not AGGREGATORS.search(prop.source or ""), f"{material.id}.{key}"
        process = material.process or {}
        assert not AGGREGATORS.search(str(process.get("source") or "")), material.id
        for link in material.links:
            assert link["url"].startswith("https://"), f"{material.id} link {link}"


# ==================================================================== docs

def test_docs_materials_md_exists_and_covers_the_essentials():
    """`docs/materials.md` names the lint command, the immutability rule, and
    the three deferrals (community repo, package distribution, FreeCAD
    import) — the house docs-test pattern (`test_prd012_acceptance.py`'s
    `test_the_documentation_describes_the_shipped_configuration_surface`)."""
    text = (REPO / "docs" / "materials.md").read_text(encoding="utf-8")
    assert "agentcad materials lint" in text
    assert "never changed in place" in text or "immutab" in text.lower()
    for needle in ("agentcad-materials", "PRD-011", "FreeCAD", "PRD-031"):
        assert needle in text, f"docs/materials.md does not cover {needle!r}"
