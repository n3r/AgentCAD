"""PRD-028 slice 3 — FEM temperature resolution + `material_basis` (FR4/AC3).

The `agentcad[fem]` extra is not installed here (or in CI), so the FEM tools
would normally not register at all: `tools_analysis.register` reads
`kernel.handlers.fem.fem_available()` at registration time. Every service-side
assertion below therefore patches that gate **before** `build_registry` and
replaces `service.kernel` with a fake that records `(op, args)` — which is
exactly the seam under test (the kernel is unchanged; the service resolves the
scalar the solver already takes and says how it got it). Two `importorskip`
tests run the real solver when the extra is present.
"""

from __future__ import annotations

import copy

import pytest

from agentcad.core.specs import SpecRunner
from agentcad.core.tools import build_registry
from agentcad.core.tools_analysis import resolve_property

from .conftest import BOX_SCRIPT, make_test_service

# A synthetic k(T) table: 50 at 20 C, 45 at 100 C, 35 at 300 C. 60 C (the mean
# of a 100/20 thermal run) is exactly half way down the first segment → 47.5.
SYNTH_K = {
    "label": "Synthetic", "category": "metal",
    "properties": {
        "density_g_cm3": {"value": 2.7, "unit": "g/cm3", "basis": "typical",
                          "source": "synthetic test card"},
        "k_w_m_k": {"value": 50, "unit": "W/(m*K)", "basis": "typical",
                    "source": "synthetic test table",
                    "table": [[20, 50], [100, 45], [300, 35]]},
    },
}

# E(T): 70 GPa at 20 C, 60 GPa at 200 C → 65 GPa (65000 MPa) at 110 C.
SYNTH_E = {
    "label": "Synthetic E(T)", "category": "metal",
    "properties": {
        "density_g_cm3": {"value": 2.7, "unit": "g/cm3", "basis": "typical",
                          "source": "synthetic test card"},
        "E_gpa": {"value": 70, "unit": "GPa", "basis": "typical",
                  "source": "synthetic test table",
                  "table": [[20, 70], [200, 60]]},
    },
}

# Flat v1 entries: no E at all, and one carrying a Poisson ratio.
NO_E = {"density_g_cm3": 2.7, "label": "No modulus", "category": "metal"}
WITH_NU = {"density_g_cm3": 2.7, "E_gpa": 70.0, "poisson_ratio": 0.34,
           "label": "With nu", "category": "metal"}

FACES = {"fixed_face": {"axis": "z", "side": "min"},
         "load_face": {"axis": "z", "side": "max"}}
THERMAL_FACES = {"hot_face": {"axis": "x", "side": "min"},
                 "cold_face": {"axis": "x", "side": "max"}}


class FakeKernel:
    """Records every `(op, args)` and answers a minimal plausible FEM result."""

    def __init__(self, result: dict | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.result = {"ok": True, "warnings": []} if result is None else result

    def request(self, op: str, args: dict, **kwargs) -> dict:
        self.calls.append((op, copy.deepcopy(args)))
        return copy.deepcopy(self.result)


class Harness:
    """A service whose FEM tools are registered without the extra.

    Parts are created against the REAL kernel (a part record only exists once
    it has built); every call after that goes to the fake.
    """

    def __init__(self, service, registry, real_kernel):
        self.service = service
        self.registry = registry
        self.real = real_kernel
        self.fake = FakeKernel()

    def part(self, part_id: str, material: str = "al6061",
             materials: dict | None = None) -> str:
        self.service.kernel = self.real
        if materials is not None:
            self.registry.call("set_project_materials",
                               {"project": "demo", "materials": materials})
        self.service.create_part("demo", part_id, script=BOX_SCRIPT,
                                 material=material)
        self.service.kernel = self.fake
        return part_id

    def call(self, tool: str, **args) -> dict:
        return self.registry.call(tool, {"project": "demo", **args})

    @property
    def sent(self) -> dict:
        """The args of the last kernel request."""
        return self.fake.calls[-1][1]


@pytest.fixture
def fem(kernel, tmp_path, monkeypatch):
    """`Harness` with `fem_available` patched true BEFORE the registry builds."""
    from agentcad.kernel.handlers import fem as fem_module

    monkeypatch.setattr(fem_module, "fem_available", lambda: True)
    service = make_test_service(tmp_path / "projects", kernel)
    registry = build_registry(service)
    service.create_project("demo")
    return Harness(service, registry, kernel)


# ------------------------------------------------------------------ fixture

def test_the_fem_tools_register_behind_a_patched_gate(fem):
    names = {t.name for t in fem.registry.list()}
    assert {"fem_static", "fem_modal", "fem_thermal"} <= names


# ------------------------------------------------------------ resolve_property

def test_resolve_property_carries_the_evidence(fem):
    fem.part("bar", material="synth", materials={"synth": SYNTH_K})
    entry = resolve_property(fem.service, "demo", "synth", "k_w_m_k", 60.0)
    assert entry == {"value": pytest.approx(47.5), "basis": "typical",
                     "source": "synthetic test table", "T_c": 60.0,
                     "interpolated": True, "clamped": False,
                     "table_range": [20.0, 300.0], "unit": "W/(m*K)"}


def test_resolve_property_clamps_and_says_so(fem):
    fem.part("bar", material="synth", materials={"synth": SYNTH_K})
    entry = resolve_property(fem.service, "demo", "synth", "k_w_m_k", 400.0)
    assert entry["value"] == pytest.approx(35.0)
    assert entry["clamped"] is True and entry["interpolated"] is True


def test_resolve_property_is_none_without_the_property(fem):
    fem.part("bar", material="plain", materials={"plain": NO_E})
    assert resolve_property(fem.service, "demo", "plain", "E_gpa", 20.0) is None


def test_resolve_property_ignores_temperature_without_a_table(fem):
    fem.part("bar")
    entry = resolve_property(fem.service, "demo", "al6061", "E_gpa", 400.0)
    assert entry["value"] == pytest.approx(68.9)
    assert entry["interpolated"] is False and entry["clamped"] is False
    assert entry["table_range"] is None and entry["unit"] == "GPa"


# ------------------------------------------------------------------- thermal

def test_thermal_k_is_interpolated_at_the_mean_temperature(fem):
    fem.part("bar", material="synth", materials={"synth": SYNTH_K})
    result = fem.call("fem_thermal", part_id="bar", t_hot_c=100.0,
                      t_cold_c=20.0, **THERMAL_FACES)
    assert fem.sent["k_w_m_k"] == pytest.approx(47.5)   # T_eval = 60 C
    basis = result["material_basis"]["k_w_m_k"]
    assert basis["value"] == pytest.approx(47.5)
    assert basis["interpolated"] is True and basis["clamped"] is False
    assert basis["basis"] == "typical" and basis["T_c"] == pytest.approx(60.0)
    assert result["warnings"] == []


def test_thermal_k_clamps_outside_the_table_and_warns(fem):
    fem.part("bar", material="synth", materials={"synth": SYNTH_K})
    fem.fake.result = {"ok": True, "warnings": ["mesh_is_coarse"]}
    result = fem.call("fem_thermal", part_id="bar", t_hot_c=500.0,
                      t_cold_c=300.0, **THERMAL_FACES)
    assert fem.sent["k_w_m_k"] == pytest.approx(35.0)   # T_eval = 400 C
    assert result["material_basis"]["k_w_m_k"]["clamped"] is True
    # The kernel's own warnings survive; ours is appended, verbatim.
    assert result["warnings"] == [
        "mesh_is_coarse",
        "temperature_out_of_table_range: k_w_m_k evaluated at 400.0 C, "
        "table covers 20.0..300.0 C; end value used",
    ]


def test_thermal_warning_list_is_created_when_the_kernel_has_none(fem):
    fem.part("bar", material="synth", materials={"synth": SYNTH_K})
    fem.fake.result = {"ok": True, "t_max_c": 500.0}
    result = fem.call("fem_thermal", part_id="bar", t_hot_c=500.0,
                      t_cold_c=300.0, **THERMAL_FACES)
    assert len(result["warnings"]) == 1
    assert result["warnings"][0].startswith("temperature_out_of_table_range:")


def test_explicit_k_bypasses_the_material(fem):
    fem.part("bar", material="synth", materials={"synth": SYNTH_K})
    result = fem.call("fem_thermal", part_id="bar", t_hot_c=500.0,
                      t_cold_c=300.0, k_w_m_k=10.0, **THERMAL_FACES)
    assert fem.sent["k_w_m_k"] == pytest.approx(10.0)
    assert result["material_basis"] == {"k_w_m_k": {"value": 10.0,
                                                    "basis": "explicit"}}
    assert result["warnings"] == []


def test_thermal_without_conductivity_still_refuses(fem):
    fem.part("bar", material="plain", materials={"plain": NO_E})
    result = fem.call("fem_thermal", part_id="bar", t_hot_c=100.0,
                      t_cold_c=20.0, **THERMAL_FACES)
    assert "no thermal" in result["error"]["message"]


# -------------------------------------------------------------------- static

def test_static_defaults_e_and_nu_from_the_part_material(fem):
    fem.part("bar")                                    # al6061
    result = fem.call("fem_static", part_id="bar", **FACES)
    assert fem.sent["E_mpa"] == pytest.approx(68900.0)   # 68.9 GPa
    assert result["material_basis"]["E_mpa"]["basis"] == "typical"
    assert result["material_basis"]["E_mpa"]["unit"] == "MPa"

    # nu follows the landed card: al6061 has no `poisson_ratio` today, so the
    # historical 0.3 is used and recorded as such. Asserted against the
    # resolved material rather than a literal, because the shipped card may
    # gain one (curation) without this rule changing.
    material = fem.service.materials.resolve("demo", "al6061")
    nu_basis = result["material_basis"]["nu"]
    if material.poisson_ratio is None:
        assert fem.sent["nu"] == pytest.approx(0.3)
        assert nu_basis == {"value": 0.3, "basis": "fallback_default"}
    else:
        assert fem.sent["nu"] == pytest.approx(material.poisson_ratio)
        assert nu_basis["basis"] != "fallback_default"


def test_static_falls_back_to_steel_without_a_modulus(fem):
    fem.part("bar", material="plain", materials={"plain": NO_E})
    result = fem.call("fem_static", part_id="bar", **FACES)
    assert fem.sent["E_mpa"] == pytest.approx(210000.0)
    assert fem.sent["nu"] == pytest.approx(0.3)
    assert result["material_basis"] == {
        "E_mpa": {"value": 210000.0, "basis": "fallback_default"},
        "nu": {"value": 0.3, "basis": "fallback_default"},
    }


def test_static_uses_the_material_poisson_ratio(fem):
    fem.part("bar", material="nu34", materials={"nu34": WITH_NU})
    result = fem.call("fem_static", part_id="bar", **FACES)
    assert fem.sent["nu"] == pytest.approx(0.34)
    assert fem.sent["E_mpa"] == pytest.approx(70000.0)
    assert result["material_basis"]["nu"]["basis"] == "typical"
    assert result["material_basis"]["nu"]["unit"] == "-"


def test_static_interpolates_e_at_temperature_c(fem):
    fem.part("bar", material="synth_e", materials={"synth_e": SYNTH_E})
    result = fem.call("fem_static", part_id="bar", temperature_c=110.0, **FACES)
    assert fem.sent["E_mpa"] == pytest.approx(65000.0)   # 65 GPa at 110 C
    basis = result["material_basis"]["E_mpa"]
    assert basis["interpolated"] is True and basis["clamped"] is False
    assert basis["T_c"] == pytest.approx(110.0)
    assert result["warnings"] == []


def test_static_clamps_e_above_the_table_and_warns(fem):
    fem.part("bar", material="synth_e", materials={"synth_e": SYNTH_E})
    result = fem.call("fem_static", part_id="bar", temperature_c=400.0, **FACES)
    assert fem.sent["E_mpa"] == pytest.approx(60000.0)
    assert result["warnings"] == [
        "temperature_out_of_table_range: E_mpa evaluated at 400.0 C, "
        "table covers 20.0..200.0 C; end value used"]


def test_static_refuses_a_zero_stiffness_table_end(fem):
    """EN 1993-1-2's E(T) curve ends at 0 at 1200 C (``steel_a36`` ships it):
    resolving there must refuse, not hand the solver a singular stiffness."""
    fem.part("bar", material="steel_a36")
    result = fem.call("fem_static", part_id="bar", temperature_c=1200.0, **FACES)
    assert "no stiffness at 1200.0 C" in result["error"]["message"]
    assert fem.fake.calls == []          # nothing reached the kernel
    ok = fem.call("fem_static", part_id="bar", temperature_c=600.0, **FACES)
    assert fem.sent["E_mpa"] == pytest.approx(62000.0)   # 0.31 x 200 GPa
    assert ok["material_basis"]["E_mpa"]["interpolated"] is True


def test_static_records_explicit_values(fem):
    fem.part("bar")
    result = fem.call("fem_static", part_id="bar", E_mpa=1000.0, nu=0.1,
                      **FACES)
    assert fem.sent["E_mpa"] == pytest.approx(1000.0)
    assert fem.sent["nu"] == pytest.approx(0.1)
    assert result["material_basis"] == {
        "E_mpa": {"value": 1000.0, "basis": "explicit"},
        "nu": {"value": 0.1, "basis": "explicit"},
    }


def test_static_request_keeps_its_shape(fem):
    fem.part("bar")
    fem.call("fem_static", part_id="bar", **FACES)
    op, args = fem.fake.calls[-1]
    assert op == "fem_static"
    assert set(args) == {"script", "params", "fixed_face", "load_face",
                         "load_N", "load_dir", "E_mpa", "nu", "mesh_size_mm"}


# --------------------------------------------------------------------- modal

def test_modal_without_a_modulus_still_refuses(fem):
    fem.part("bar", material="plain", materials={"plain": NO_E})
    result = fem.call("fem_modal", part_id="bar")
    assert "has no Young's modulus; pass E_mpa" in result["error"]["message"]


def test_modal_interpolates_e_at_temperature_c(fem):
    fem.part("bar", material="synth_e", materials={"synth_e": SYNTH_E})
    result = fem.call("fem_modal", part_id="bar", temperature_c=110.0)
    assert fem.sent["E_mpa"] == pytest.approx(65000.0)
    assert fem.sent["nu"] == pytest.approx(0.3)
    assert result["material_basis"]["E_mpa"]["interpolated"] is True
    assert result["material_basis"]["nu"] == {"value": 0.3,
                                              "basis": "fallback_default"}


def test_modal_request_keeps_its_shape(fem):
    fem.part("bar")
    fem.call("fem_modal", part_id="bar", n_modes=4)
    op, args = fem.fake.calls[-1]
    assert op == "fem_modal"
    assert set(args) == {"script", "params", "n_modes", "E_mpa", "nu",
                         "density_g_cm3"}


# --------------------------------------------------------------------- specs

def test_specs_reads_e_through_the_resolver_unchanged(fem):
    """`_fem_material_key` must not move for a point-only material: the memo it
    guards is keyed on the number the solver consumed."""
    fem.part("bar")
    runner = SpecRunner(fem.service)
    assert runner._youngs_mpa("demo", "al6061") == pytest.approx(68900.0)
    # Byte-for-byte what the pre-slice-3 code computed: E_gpa * 1000, "%.10g".
    material = fem.service.materials.resolve("demo", "al6061")
    assert runner._fem_material_key("demo", "bar") == \
        f"E{material.E_gpa * 1000.0:.10g}" == "E68900"


def test_specs_key_is_default_without_a_modulus(fem):
    fem.part("bar", material="plain", materials={"plain": NO_E})
    runner = SpecRunner(fem.service)
    assert runner._youngs_mpa("demo", "plain") is None
    assert runner._fem_material_key("demo", "bar") == "default"


def test_specs_youngs_survives_an_unknown_material(fem):
    runner = SpecRunner(fem.service)
    assert runner._youngs_mpa("demo", "no_such_material") is None


# --------------------------------------------------- real solver (skipped here)

def test_real_solver_thermal_reports_the_material_basis(kernel, tmp_path):
    pytest.importorskip("skfem")
    pytest.importorskip("gmsh")
    pytest.importorskip("meshio")
    service = make_test_service(tmp_path / "projects", kernel)
    registry = build_registry(service)
    service.create_project("demo")
    service.create_part("demo", "bar", script=BOX_SCRIPT)   # al6061, k = 167
    result = registry.call("fem_thermal", {
        "project": "demo", "part_id": "bar", "t_hot_c": 100.0, "t_cold_c": 0.0,
        **THERMAL_FACES})
    assert result["material_basis"]["k_w_m_k"]["value"] == pytest.approx(167.0)
    assert result["flux_w"] > 0


def test_real_solver_static_reports_the_material_basis(kernel, tmp_path):
    pytest.importorskip("skfem")
    pytest.importorskip("gmsh")
    pytest.importorskip("meshio")
    service = make_test_service(tmp_path / "projects", kernel)
    registry = build_registry(service)
    service.create_project("demo")
    service.create_part("demo", "bar", script=BOX_SCRIPT)   # al6061
    result = registry.call("fem_static", {
        "project": "demo", "part_id": "bar", "load_N": 100.0, **FACES})
    assert result["material_basis"]["E_mpa"]["value"] == pytest.approx(68900.0)
    assert result["max_disp_mm"] > 0


def test_non_finite_temperatures_are_refused_at_the_door(fem):
    """A NaN temperature used to read the table's last row with no clamp flag
    (``Property.at`` fell through) and then 500 the HTTP response on echo;
    ``_quietly`` must not turn that refusal into a silent fallback either."""
    fem.part("bar", material="synth_e", materials={"synth_e": SYNTH_E})
    for bad in (float("nan"), float("inf")):
        result = fem.call("fem_static", part_id="bar", temperature_c=bad, **FACES)
        assert "temperature_c must be finite" in result["error"]["message"]
        result = fem.call("fem_thermal", part_id="bar", t_hot_c=bad, t_cold_c=20.0,
                          **THERMAL_FACES)
        assert "t_hot_c must be finite" in result["error"]["message"]
    assert fem.fake.calls == []
    with pytest.raises(ValueError):
        fem.service.materials.resolve("demo", "synth_e").prop("E_gpa").at(float("nan"))
