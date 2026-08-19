"""PRD-015 Slice 1 — the BOM builder (FR1-3).

The load-bearing invariants proven here:

* **Zero kernel calls.** `build_bom` walks the manifest structurally and peeks
  cached metrics — it never resolves transforms and never builds. A project
  with a polar pattern AND a sub-assembly makes zero `kernel.request` calls.
* **Count-only roll-ups.** A pattern of N counts N; a sub-assembly instanced
  twice, each carrying an 8-member bolt pattern, rolls up to one screw line of
  qty 16 (AC2). Flat and indented agree on totals.
* **Cached-metrics peek, never a build.** An unbuilt part reports
  `mass_source: unbuilt` with a warning and no `_status` slot is written.
* **Cost honesty.** Manual cost wins; else a material estimate; else none —
  each labelled by `cost_source`.
"""

import pytest

from agentcad.core import bom as bommod
from agentcad.core import manifest_merge
from agentcad.core.materials import get_material
from agentcad.core.model import InstanceSpec, ValidationError
from agentcad.core.tools import build_registry

from .conftest import (BOX_SCRIPT, FLANGE_SCRIPT, PLATE_SCRIPT,
                       THREE_SIZE_CONFIGS, make_test_service)


def _service(tmp_path, kernel):
    service = make_test_service(tmp_path / "projects", kernel)
    registry = build_registry(service)  # installs structure + materials packs
    return service, registry


# ----------------------------------------------------------- flat roll-ups


def test_flat_pattern_rollup(tmp_path, kernel):
    """A linear pattern of count 5 → one line, qty 5 (not five lines)."""
    service, _ = _service(tmp_path, kernel)
    service.create_project("p")
    service.create_part("p", "bolt", script=BOX_SCRIPT)
    service.set_assembly("p", [{"id": "b", "part": "bolt",
        "pattern": {"kind": "linear", "count": 5, "step_mm": 10}}])

    result = bommod.build_bom(service, "p")
    lines = result["lines"]
    assert len(lines) == 1
    assert lines[0]["part_id"] == "bolt"
    assert lines[0]["qty"] == 5
    assert lines[0]["item"] == 1


def test_flat_subassembly_rollup(tmp_path, kernel):
    """Sub-assembly instanced twice, each with an 8-member bolt pattern →
    one screw line, qty 16, keyed to the SOURCE project (AC2)."""
    service, _ = _service(tmp_path, kernel)
    service.create_project("widget")
    service.create_part("widget", "bolt", script=BOX_SCRIPT)
    service.set_assembly("widget", [{"id": "bolts", "part": "bolt",
        "pattern": {"kind": "linear", "count": 8, "step_mm": 5}}])

    service.create_project("top")
    service.set_assembly("top", [
        {"id": "w1", "assembly": {"project": "widget"}, "position": [0, 0, 0]},
        {"id": "w2", "assembly": {"project": "widget"}, "position": [50, 0, 0]},
    ])

    result = bommod.build_bom(service, "top")
    lines = result["lines"]
    assert len(lines) == 1
    assert lines[0]["part_id"] == "bolt"
    assert lines[0]["origin_project"] == "widget"
    assert lines[0]["qty"] == 16


def test_polar_pattern_counts(tmp_path, kernel):
    """A polar pattern counts by `count` with no kernel transform pass."""
    service, _ = _service(tmp_path, kernel)
    service.create_project("p")
    service.create_part("p", "bolt", script=BOX_SCRIPT)
    service.set_assembly("p", [{"id": "b", "part": "bolt",
        "pattern": {"kind": "polar", "count": 6, "angle_step_deg": 60}}])

    result = bommod.build_bom(service, "p")
    assert result["lines"][0]["qty"] == 6


# ------------------------------------------------------ flat == indented


def test_flat_and_indented_agree_on_totals(tmp_path, kernel):
    """Both structures roll to the same mass and cost total (AC2)."""
    service, _ = _service(tmp_path, kernel)
    service.create_project("widget")
    service.create_part("widget", "bolt", script=BOX_SCRIPT)
    service.create_part("widget", "plate", script=PLATE_SCRIPT)
    service.set_assembly("widget", [
        {"id": "bolts", "part": "bolt",
         "pattern": {"kind": "linear", "count": 4, "step_mm": 5}},
        {"id": "plate", "part": "plate"},
    ])
    service.create_project("top")
    service.set_assembly("top", [
        {"id": "w1", "assembly": {"project": "widget"}},
        {"id": "w2", "assembly": {"project": "widget"}, "position": [40, 0, 0]},
    ])
    # Warm every part so the totals are non-trivially numeric.
    service.get_metrics("widget", "bolt")
    service.get_metrics("widget", "plate")

    flat = bommod.build_bom(service, "top", structure="flat")
    indented = bommod.build_bom(service, "top", structure="indented")

    assert flat["totals"] == indented["totals"]
    # Flat collapses the two sub-assemblies; indented keeps each occurrence.
    assert len(flat["lines"]) == 2          # bolt, plate
    assert len(indented["lines"]) == 4      # 2 subassemblies * (bolt, plate)
    assert all("level" in ln for ln in indented["lines"])
    # bolt total: 4 per widget * 2 widgets = 8
    bolt_flat = next(ln for ln in flat["lines"] if ln["part_id"] == "bolt")
    assert bolt_flat["qty"] == 8


# ---------------------------------------------------------- per-config (AC7)


def test_per_config_lines(tmp_path, kernel):
    """Three instances bound to three configurations → three lines with
    distinct configs and distinct per-config mass (AC7)."""
    service, registry = _service(tmp_path, kernel)
    service.create_project("p")
    service.create_part("p", "flange", script=FLANGE_SCRIPT)
    registry.call("set_part_configs",
                  {"project": "p", "part_id": "flange",
                   "configs": THREE_SIZE_CONFIGS})
    service.set_assembly("p", [
        {"id": "fs", "part": "flange", "config": "s"},
        {"id": "fm", "part": "flange", "config": "m"},
        {"id": "fl", "part": "flange", "config": "l"},
    ])
    # Warm each configuration's build so the peek finds per-config mass.
    for cfg in ("s", "m", "l"):
        service._ensure_config_built("p", "flange", cfg)

    result = bommod.build_bom(service, "p")
    lines = result["lines"]
    assert len(lines) == 3
    configs = {ln["config"] for ln in lines}
    assert configs == {"s", "m", "l"}
    masses = {ln["config"]: ln["unit_mass_g"] for ln in lines}
    assert all(m is not None for m in masses.values())
    # A bigger flange masses more: s < m < l.
    assert masses["s"] < masses["m"] < masses["l"]
    assert all(ln["mass_source"] == "built" for ln in lines)


# --------------------------------------------------------------- cost (FR3)


def test_cost_manual_wins(tmp_path, kernel):
    service, registry = _service(tmp_path, kernel)
    service.create_project("p")
    service.create_part("p", "bolt", script=BOX_SCRIPT)
    service.set_assembly("p", [{"id": "b", "part": "bolt"}])
    registry.call("set_bom_fields",
                  {"project": "p", "part_id": "bolt", "unit_cost_usd": 1.25})

    line = bommod.build_bom(service, "p")["lines"][0]
    assert line["cost_source"] == "manual"
    assert line["unit_cost_usd"] == 1.25
    assert line["ext_cost_usd"] == 1.25  # qty 1


def test_cost_material_estimate(tmp_path, kernel):
    service, _ = _service(tmp_path, kernel)
    service.create_project("p")
    service.create_part("p", "bolt", script=BOX_SCRIPT, material="al6061")
    service.set_assembly("p", [{"id": "b", "part": "bolt"}])
    mass_g = service.get_metrics("p", "bolt")["mass_g"]

    line = bommod.build_bom(service, "p")["lines"][0]
    assert line["cost_source"] == "material_estimate"
    expected = mass_g * get_material("al6061").cost_usd_kg / 1000.0
    assert line["unit_cost_usd"] == pytest.approx(expected)


def test_cost_none_when_material_has_no_cost(tmp_path, kernel):
    """A material without cost_usd_kg and no manual override → cost_source
    none, cost None."""
    service, registry = _service(tmp_path, kernel)
    service.create_project("p")
    # A project material with no cost_usd_kg.
    registry.call("set_project_materials",
                  {"project": "p",
                   "materials": {"mystery": {"density_g_cm3": 2.0}}})
    service.create_part("p", "bolt", script=BOX_SCRIPT, material="mystery")
    service.set_assembly("p", [{"id": "b", "part": "bolt"}])
    service.get_metrics("p", "bolt")

    line = bommod.build_bom(service, "p")["lines"][0]
    assert line["cost_source"] == "none"
    assert line["unit_cost_usd"] is None
    assert line["ext_cost_usd"] is None


# ------------------------------------------------ unbuilt: warn, never build


def test_unbuilt_part_warns_and_does_not_build(tmp_path, kernel):
    service, _ = _service(tmp_path, kernel)
    service.create_project("p")
    service.create_part("p", "bolt", script=BOX_SCRIPT)
    service.store.set_instances("p", [InstanceSpec(id="b", part="bolt")])
    # Simulate a fresh process (the documented post-restart case): the metrics
    # memo is empty, so the part reads unbuilt.
    service._status.clear()
    service._config_status.clear()

    result = bommod.build_bom(service, "p")
    line = result["lines"][0]
    assert line["mass_source"] == "unbuilt"
    assert line["unit_mass_g"] is None
    assert any(w["kind"] == "mass_unbuilt" and w["part"] == "bolt"
               for w in result["warnings"])
    # The peek must NOT have built anything.
    assert service._status == {}
    assert service._config_status == {}


def test_stale_when_script_changes(tmp_path, kernel):
    """A built part whose script then changes reads mass_source stale."""
    service, _ = _service(tmp_path, kernel)
    service.create_project("p")
    service.create_part("p", "bolt", script=BOX_SCRIPT)
    service.set_assembly("p", [{"id": "b", "part": "bolt"}])
    service.get_metrics("p", "bolt")
    # Mutate the script so the cache key no longer matches the memo.
    service.store.write_script("p", "bolt",
                               BOX_SCRIPT.replace("100.0", "120.0"))

    result = bommod.build_bom(service, "p")
    assert result["lines"][0]["mass_source"] == "stale"
    assert any(w["kind"] == "mass_stale" for w in result["warnings"])


# --------------------------------------------------------- set_bom_fields


def test_set_bom_fields_round_trip(tmp_path, kernel):
    service, registry = _service(tmp_path, kernel)
    service.create_project("p")
    service.create_part("p", "bolt", script=BOX_SCRIPT)
    service.set_assembly("p", [{"id": "b", "part": "bolt"}])

    out = registry.call("set_bom_fields",
                        {"project": "p", "part_id": "bolt",
                         "part_number": "PN-42", "supplier": "Acme",
                         "url": "https://acme.example/pn42"})
    assert "error" not in out
    line = bommod.build_bom(service, "p")["lines"][0]
    assert line["part_number"] == "PN-42"
    assert line["source"] == "https://acme.example/pn42"


def test_set_bom_fields_rejects_unknown_key(tmp_path, kernel):
    service, registry = _service(tmp_path, kernel)
    service.create_project("p")
    service.create_part("p", "bolt", script=BOX_SCRIPT)
    out = registry.call("set_bom_fields",
                        {"project": "p", "part_id": "bolt", "bogus": "x"})
    assert out["error"]["type"] == "invalid_arguments"


def test_set_bom_fields_rejects_negative_cost(tmp_path, kernel):
    service, registry = _service(tmp_path, kernel)
    service.create_project("p")
    service.create_part("p", "bolt", script=BOX_SCRIPT)
    out = registry.call("set_bom_fields",
                        {"project": "p", "part_id": "bolt",
                         "unit_cost_usd": -1.0})
    assert out["error"]["type"] == "validation_error"


def test_set_bom_fields_rejects_control_char(tmp_path, kernel):
    service, registry = _service(tmp_path, kernel)
    service.create_project("p")
    service.create_part("p", "bolt", script=BOX_SCRIPT)
    out = registry.call("set_bom_fields",
                        {"project": "p", "part_id": "bolt",
                         "part_number": "bad\x00pn"})
    assert out["error"]["type"] == "validation_error"


def test_set_bom_fields_unknown_part(tmp_path, kernel):
    service, registry = _service(tmp_path, kernel)
    service.create_project("p")
    out = registry.call("set_bom_fields",
                        {"project": "p", "part_id": "nope",
                         "part_number": "x"})
    assert out["error"]["type"] == "notfound_error"


# --------------------------------------------------------- merge (per-field)


def test_bom_field_merges_per_field():
    base = {"name": "p", "parts": [{"id": "a", "bom": {}}]}
    ours = {"name": "p", "parts": [{"id": "a", "bom": {"part_number": "PN"}}]}
    theirs = {"name": "p",
              "parts": [{"id": "a", "bom": {"unit_cost_usd": 2.5}}]}

    merged, conflicts = manifest_merge.merge_manifests(base, ours, theirs)
    assert conflicts == []
    assert merged["parts"][0]["bom"] == {"part_number": "PN",
                                         "unit_cost_usd": 2.5}


# -------------------------------------------------------- zero kernel calls


def test_build_bom_makes_zero_kernel_calls(tmp_path, kernel):
    """Even with a polar pattern AND a sub-assembly, build_bom calls the
    kernel zero times (the whole point of the count-only walk)."""
    service, _ = _service(tmp_path, kernel)
    service.create_project("widget")
    service.create_part("widget", "bolt", script=BOX_SCRIPT)
    service.set_assembly("widget", [{"id": "bolts", "part": "bolt",
        "pattern": {"kind": "polar", "count": 6, "angle_step_deg": 60}}])
    service.create_project("top")
    service.set_assembly("top", [
        {"id": "w1", "assembly": {"project": "widget"}}])

    calls = {"n": 0}
    real = service.kernel.request

    def spy(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    service.kernel.request = spy
    try:
        result = bommod.build_bom(service, "top")
    finally:
        service.kernel.request = real
    assert calls["n"] == 0
    assert result["lines"][0]["qty"] == 6


def test_count_leaves_detects_cycle(tmp_path, kernel):
    service, _ = _service(tmp_path, kernel)
    service.create_project("a")
    service.create_project("b")
    # Author the cyclic references directly (set_assembly would resolve and
    # reject the cycle at write time — count_leaves is the read-side guard).
    service.store.set_instances("a", [InstanceSpec(id="b",
                                      assembly={"project": "b"})])
    service.store.set_instances("b", [InstanceSpec(id="a",
                                      assembly={"project": "a"})])

    with pytest.raises(ValidationError) as exc:
        bommod.build_bom(service, "a")
    assert "cycle" in exc.value.details
