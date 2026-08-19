"""PRD-014 Slice 5 (FR10) — configuration tabulation with letter variables.

`generate_drawing {tabulate: true}` renders, for a part with PRD-012
configurations, a **config table** whose columns are letter variables (A, B,
C, … assigned at render time) and a per-config mass, one row per member. The
letters label the overall dimension lines on the drawn views; the drawn views
use the **active** configuration. A part with no configurations degrades to a
`warnings` note and a byte-identical plain sheet — never an error.

The family is drawn for real through the tool and the kernel, because the whole
claim of the table is that every number in it was measured from a built shape
(the same reason `test_configs_drawing.py` refuses a mocked kernel).
"""

from __future__ import annotations

import hashlib
import re

import pytest

from agentcad.core.materials import DEFAULT_MATERIAL
from agentcad.core.tools import build_registry

from .conftest import (
    FLANGE_SCRIPT,
    THREE_SIZE_CONFIGS,
    clone_test_service,
    make_test_service,
)


def _svg(service, name: str) -> str:
    return (service.store.exports_dir("demo") / name).read_text(encoding="utf-8")


@pytest.mark.timeout(900)
class TestTabulate:
    """`generate_drawing {tabulate, config?}` against one real flange family."""

    @pytest.fixture(scope="class")
    @classmethod
    def tabulate_projects(cls, kernel, tmp_path_factory):
        projects = tmp_path_factory.mktemp("tabulate_projects")
        svc = make_test_service(projects, kernel)
        svc.create_project("demo")
        svc.store.add_part("demo", "flange", "Flange", DEFAULT_MATERIAL,
                           FLANGE_SCRIPT)
        svc.store.update_part_entry("demo", "flange",
                                    configs=THREE_SIZE_CONFIGS)
        # The same script with NO family: the "unchanged without configurations"
        # control (a free build — it shares flange's base cache key).
        svc.store.add_part("demo", "plate", "Plate", DEFAULT_MATERIAL,
                           FLANGE_SCRIPT)
        return projects

    @pytest.fixture
    def stack(self, kernel, tmp_path, tabulate_projects):
        service = clone_test_service(tabulate_projects, tmp_path / "projects",
                                     kernel)
        return service, build_registry(service)

    # ---------------------------------------------------- letters + the table

    def test_tabulate_renders_letter_variables_and_a_config_table(self, stack):
        """A, B, C are assigned to the overall X/Y/Z extents; the config table
        has one row per member with each config's measured values + mass."""
        service, registry = stack
        result = registry.call("generate_drawing", {
            "project": "demo", "part_id": "flange", "tabulate": True})

        assert "error" not in result, result
        table = result["config_table"]
        # The guaranteed core: three overall-extent variables A/B/C.
        assert [v["letter"] for v in table["variables"]] == ["A", "B", "C"]
        assert [v["source"] for v in table["variables"]] == [
            "overall X (mm)", "overall Y (mm)", "overall Z (mm)"]
        # One row per member, in family order, each buildable.
        assert [r["config"] for r in table["rows"]] == ["s", "m", "l"]
        assert [r["label"] for r in table["rows"]] == ["Small", "Medium",
                                                       "Large"]
        assert all(r["ok"] for r in table["rows"])
        vals = {r["config"]: r["values"] for r in table["rows"]}
        # X/Y = OD (the flange is a disc), Z = thickness — measured, not echoed.
        assert [vals[n]["A"] for n in ("s", "m", "l")] == [100.0, 140.0, 200.0]
        assert [vals[n]["B"] for n in ("s", "m", "l")] == [100.0, 140.0, 200.0]
        assert {vals[n]["C"] for n in ("s", "m", "l")} == {14.0}
        # Per-config mass, larger member weighs more.
        masses = {r["config"]: r["mass"] for r in table["rows"]}
        assert all(masses[n] for n in ("s", "m", "l"))
        assert masses["s"] != masses["l"]

        svg = _svg(service, "flange_drawing.svg")
        # Header cells name the letters and the mass column.
        for cell in (">config<", ">A<", ">B<", ">C<", ">mass<"):
            assert svg.count(cell) >= 1, cell
        # The letter labels the overall dimension line on the drawn view.
        assert re.search(r">A \d", svg), "the overall X dim carries its letter"

    def test_config_table_maps_every_config_with_values_and_mass(self, stack):
        """FR13: an agent can verify every config appears with its A/B/C values
        and a mass, straight off the result (no SVG parsing)."""
        service, registry = stack
        result = registry.call("generate_drawing", {
            "project": "demo", "part_id": "flange", "tabulate": True})
        assert "error" not in result, result

        rows = result["config_table"]["rows"]
        letters = [v["letter"] for v in result["config_table"]["variables"]]
        assert {r["config"] for r in rows} == {"s", "m", "l"}
        for row in rows:
            assert row["ok"]
            assert set(row["values"]) == set(letters)
            assert all(row["values"][L] is not None for L in letters)
            assert row["mass"]

    # -------------------------------------------------- the active config wins

    def test_the_active_config_drives_the_drawn_views(self, stack):
        """FR10: with no explicit `config`, the sheet draws the ACTIVE
        configuration's geometry (pure resolution through effective_params)."""
        service, registry = stack
        service.store.update_part_entry("demo", "flange", active_config="l")
        result = registry.call("generate_drawing", {
            "project": "demo", "part_id": "flange", "tabulate": True})

        assert "error" not in result, result
        # L's OD is 200 and its bore 120 — the drawn views are L's geometry.
        diameters = result["detected"]["diameters_mm"]
        assert any(abs(d - 200.0) < 0.05 for d in diameters), diameters
        assert any(abs(d - 120.0) < 0.05 for d in diameters), diameters
        assert result["config_table"]["active_config"] == "l"
        # The overall X dim on the sheet is 200 (L), lettered A.
        svg = _svg(service, "flange_drawing.svg")
        assert re.search(r">A 200", svg), svg[:0]

    # --------------------------------------------------------- degrade cleanly

    def test_a_part_without_configurations_is_a_warning_not_an_error(self, stack):
        """G5: `tabulate: true` on an unconfigured part is a question — a
        `warnings` note, no table, and bytes identical to a plain call."""
        service, registry = stack
        plain = registry.call("generate_drawing", {
            "project": "demo", "part_id": "plate"})
        assert "error" not in plain, plain
        before = (service.store.exports_dir("demo") /
                  "plate_drawing.svg").read_bytes()

        asked = registry.call("generate_drawing", {
            "project": "demo", "part_id": "plate", "tabulate": True})

        assert "error" not in asked, asked
        assert asked["config_table"]["rows"] == []
        assert asked["config_table"]["variables"] == []
        assert any("no configuration" in w.lower()
                   for w in asked["config_table"]["warnings"])
        after = (service.store.exports_dir("demo") /
                 "plate_drawing.svg").read_bytes()
        assert after == before, "no table drawn ⇒ byte-identical sheet"

    # ------------------------------------------------------ tabulate vs table

    def test_tabulate_wins_over_dim_table_when_both_requested(self, stack):
        """A deterministic interaction: they share the sheet's table column, so
        the letter-variable table wins and dim_table is dropped with a note."""
        service, registry = stack
        result = registry.call("generate_drawing", {
            "project": "demo", "part_id": "flange",
            "tabulate": True, "dim_table": True})

        assert "error" not in result, result
        assert "config_table" in result
        assert "dim_table" not in result
        assert any("dim_table" in w for w in result["config_table"]["warnings"])

    # --------------------------------------------------------- PMI dim letters

    def test_a_pmi_diameter_dim_becomes_a_lettered_variable(self, stack):
        """FR10: a PMI diameter dim extends the letters past A/B/C (declaration
        order). Its per-config value is the detected diameter within tolerance,
        an em-dash (None) where the member has no such feature — honest."""
        service, registry = stack
        registry.call("set_part_pmi", {
            "project": "demo", "part_id": "flange",
            "pmi": {"dims": [{"id": "bore", "kind": "diameter",
                              "target": 120.0, "plus": 0.05, "minus": 0.05}]}})
        result = registry.call("generate_drawing", {
            "project": "demo", "part_id": "flange", "config": "l",
            "tabulate": True})

        assert "error" not in result, result
        variables = result["config_table"]["variables"]
        assert [v["letter"] for v in variables] == ["A", "B", "C", "D"]
        assert "120" in variables[3]["source"]
        vals = {r["config"]: r["values"] for r in result["config_table"]["rows"]}
        # Only L's bore is ⌀120; s (⌀50) and m (⌀80) have no match → em-dash.
        assert vals["l"]["D"] == 120.0
        assert vals["s"]["D"] is None and vals["m"]["D"] is None

    # ------------------------------------------------------------ determinism

    def test_a_tabulated_sheet_is_deterministic_svg_and_pdf(self, stack):
        """FR12: two renders at a pinned version produce identical SVG and PDF
        bytes (the geometry-CI determinism stage depends on it)."""
        service, registry = stack
        pinned = {"ref": "-", "date": "-"}

        def _render(fmt: str) -> str:
            out = registry.call("generate_drawing", {
                "project": "demo", "part_id": "flange", "format": fmt,
                "tabulate": True, "version": pinned})
            assert "error" not in out, out
            data = (service.store.exports_dir("demo") /
                    out["path"].split("/")[-1]).read_bytes()
            return hashlib.sha256(data).hexdigest()

        assert _render("svg") == _render("svg")
        assert _render("pdf") == _render("pdf")
