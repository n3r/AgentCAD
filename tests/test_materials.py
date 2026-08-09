import json

import pytest

from agentcad.core.materials import MATERIALS, MaterialLibrary, get_material
from agentcad.core.model import ValidationError
from agentcad.core.tools import build_registry

from .conftest import BOX_SCRIPT, make_test_service

V1_DENSITIES = {
    "al6061": 2.70, "steel_a36": 7.85, "stainless_316": 8.00, "ti6al4v": 4.43,
    "inconel718": 8.19, "abs": 1.04, "pla": 1.24, "nylon_pa12": 1.01,
    "concrete": 2.40, "douglas_fir": 0.53,
}


def test_v1_ids_and_densities_preserved():
    assert len(MATERIALS) >= 30
    for mid, density in V1_DENSITIES.items():
        assert get_material(mid).density_g_cm3 == pytest.approx(density)


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


def test_entry_validation_rejects_bad():
    lib = MaterialLibrary(global_path="/nonexistent")
    with pytest.raises(ValidationError):
        lib.resolve("x", {"x": {"E_gpa": 5}})  # missing required density
    with pytest.raises(ValidationError):
        lib.resolve("x", {"x": {"density_g_cm3": 2.0, "bogus": 1}})  # unknown field


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
