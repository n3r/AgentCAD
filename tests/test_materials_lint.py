"""`core/materials_lint.py` and `agentcad materials lint` (PRD-028 FR3/FR8).

The lint is pure: no service, no kernel, no I/O beyond reading the paths it is
handed. The CLI is asserted through a real subprocess, because an exit code is
the one thing an in-process call cannot prove.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agentcad.core import materials_lint as lint
from agentcad.core.materials import MATERIALS

DATA_DIR = Path(__file__).resolve().parents[1] / "agentcad" / "core" / "materials_data"


def card(**over) -> dict:
    base = {
        "label": "Test alloy", "category": "metal", "subcategory": "aluminum",
        "condition": "T6",
        "properties": {
            "density_g_cm3": {"value": 2.7, "unit": "g/cm3",
                              "basis": "typical", "source": "ASM Vol. 2"},
            "yield_mpa": {"value": 276, "unit": "MPa", "basis": "typical",
                          "source": "ASTM B209"},
        },
    }
    base.update(over)
    return base


def codes(findings, level=None):
    return sorted(f.code for f in findings
                  if level is None or f.level == level)


# ------------------------------------------------------------ the shipped library

def test_shipped_library_lints_clean_at_the_library_profile():
    findings = lint.lint_paths([DATA_DIR], "library")
    assert [f.to_dict() for f in findings if f.level == "error"] == []
    assert len(MATERIALS) >= 30


def test_lint_paths_skips_the_library_marker_file():
    files = {f.file for f in lint.lint_paths([DATA_DIR], "library")}
    assert not any(name and name.endswith("_library.json") for name in files)


# ------------------------------------------------------------------- rules

def test_missing_citation_names_the_property():
    entry = card()
    entry["properties"]["yield_mpa"].pop("source")
    findings = lint.lint_card("test_alloy", entry, "library")
    hit = [f for f in findings if f.code == "missing_citation"]
    assert len(hit) == 1
    assert hit[0].level == "error"
    assert hit[0].property == "yield_mpa"
    assert "yield_mpa" in hit[0].message and "test_alloy" in hit[0].message
    assert lint.has_errors(findings)
    assert hit[0].to_dict()["code"] == "missing_citation"


def test_missing_citation_is_only_a_warning_for_user_cards():
    entry = card()
    entry["properties"]["yield_mpa"].pop("source")
    findings = lint.lint_card("test_alloy", entry, "user")
    hit = [f for f in findings if f.code == "missing_citation"]
    assert [f.level for f in hit] == ["warning"]
    assert not lint.has_errors(findings)


def test_v1_flat_entry_is_uncited_under_the_user_profile():
    findings = lint.lint_card("custom_al", {"density_g_cm3": 2.7,
                                            "E_gpa": 70}, "user")
    assert codes(findings, "warning").count("missing_citation") == 2
    assert not lint.has_errors(findings)


def test_disallowed_aggregator_source():
    entry = card()
    entry["properties"]["yield_mpa"]["source"] = "MatWeb typical, 6061-T6"
    findings = lint.lint_card("test_alloy", entry, "library")
    hit = [f for f in findings if f.code == "disallowed_source"]
    assert hit and hit[0].level == "error" and hit[0].property == "yield_mpa"


def test_density_must_be_a_point_in_the_library():
    entry = card()
    entry["properties"]["density_g_cm3"] = {
        "range": [2.6, 2.8], "unit": "g/cm3", "source": "ASM Vol. 2"}
    assert "density_must_be_point" in codes(lint.lint_card("t", entry, "library"), "error")
    assert "density_must_be_point" in codes(lint.lint_card("t", entry, "user"), "warning")


def test_subcategory_and_process_source_are_library_only():
    entry = card(process={"machinability": "good"})
    entry.pop("subcategory")
    library = codes(lint.lint_card("t", entry, "library"), "error")
    assert "subcategory_required" in library
    assert "process_source_required" in library
    assert lint.lint_card("t", entry, "user") == []


def test_structural_rules_have_their_own_codes():
    cases = {
        "unit_mismatch": lambda c: c["properties"]["yield_mpa"].__setitem__(
            "unit", "psi"),
        "range_inverted": lambda c: c["properties"].__setitem__(
            "cost_usd_kg", {"range": [9, 3], "unit": "USD/kg", "source": "s"}),
        "table_not_monotonic": lambda c: c["properties"]["yield_mpa"].__setitem__(
            "table", [[100, 250], [20, 276]]),
        "point_outside_table": lambda c: c["properties"]["yield_mpa"].__setitem__(
            "table", [[20, 200], [100, 250]]),
        "cost_in_two_places": lambda c: (
            c["properties"].__setitem__("cost_usd_kg", {
                "value": 4, "unit": "USD/kg", "source": "s"}),
            c.__setitem__("cost_usd_kg", {"value": 4, "source": "s"})),
        "invalid_id": lambda c: None,
        "schema": lambda c: c.__setitem__("colour", "red"),
    }
    for code, mutate in cases.items():
        entry = card()
        mutate(entry)
        mid = "Bad Id" if code == "invalid_id" else "test_alloy"
        assert code in codes(lint.lint_card(mid, entry, "library"), "error"), code


def test_out_of_envelope_is_a_warning_not_an_error():
    entry = card()
    entry["properties"]["density_g_cm3"]["value"] = 24.5
    findings = lint.lint_card("test_alloy", entry, "library")
    hit = [f for f in findings if f.code == "out_of_envelope"]
    assert hit and hit[0].level == "warning" and hit[0].property == "density_g_cm3"
    assert not lint.has_errors(findings)


def test_findings_are_deterministically_ordered():
    entry = card()
    entry["properties"]["yield_mpa"].pop("source")
    entry["properties"]["density_g_cm3"].pop("source")
    entry.pop("subcategory")
    findings = lint.lint_card("test_alloy", entry, "library")
    keys = [(f.file or "", f.id, f.property or "", f.code) for f in findings]
    assert keys == sorted(keys)


# ------------------------------------------------------------------- files

def test_lint_file_reads_a_card_file_and_a_bare_mapping(tmp_path):
    good = tmp_path / "family.json"
    good.write_text(json.dumps({"schema_version": 2,
                                "materials": {"test_alloy": card()}}),
                    encoding="utf-8")
    assert lint.lint_file(good, "library") == []

    bare = tmp_path / "bare.json"
    entry = card()
    entry["properties"]["yield_mpa"].pop("source")
    bare.write_text(json.dumps({"test_alloy": entry}), encoding="utf-8")
    findings = lint.lint_file(bare, "library")
    assert codes(findings, "error") == ["missing_citation"]
    assert findings[0].file == str(bare)


def test_lint_file_lints_a_project_manifest_with_the_user_profile(tmp_path):
    project = tmp_path / "project.json"
    project.write_text(json.dumps({
        "schema_version": 2, "name": "demo", "units": "mm", "parts": [],
        "assembly": {"instances": []},
        "materials": {"custom_al": {"density_g_cm3": 2.7}},
    }), encoding="utf-8")
    # `library` is asked for, but a manifest is a user layer: warnings only.
    findings = lint.lint_file(project, "library")
    assert not lint.has_errors(findings)
    assert codes(findings, "warning") == ["missing_citation"]


def test_lint_file_reports_broken_json_as_a_schema_error(tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    findings = lint.lint_file(broken, "library")
    assert codes(findings, "error") == ["schema"]


def test_lint_paths_refuses_a_missing_path(tmp_path):
    with pytest.raises(OSError):
        lint.lint_paths([tmp_path / "nope.json"], "library")


# --------------------------------------------------------------------- CLI

def _argv() -> list[str]:
    script = Path(sys.executable).with_name("agentcad")
    if script.exists():
        return [str(script)]
    return [sys.executable, "-c", "from agentcad.cli import main; main()"]


def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(_argv() + ["materials", "lint", *args],
                          capture_output=True, text=True, timeout=300,
                          env=dict(os.environ))


@pytest.mark.integration
def test_cli_exit_codes(tmp_path):
    clean = tmp_path / "clean.json"
    clean.write_text(json.dumps({"schema_version": 2,
                                 "materials": {"test_alloy": card()}}),
                     encoding="utf-8")
    ok = _cli(str(clean))
    assert ok.returncode == 0, ok.stderr
    assert "0 errors, 0 warnings" in ok.stdout

    entry = card()
    entry["properties"]["yield_mpa"].pop("source")
    dirty = tmp_path / "dirty.json"
    dirty.write_text(json.dumps({"schema_version": 2,
                                 "materials": {"test_alloy": entry}}),
                     encoding="utf-8")
    bad = _cli(str(dirty))
    assert bad.returncode == 1
    assert "missing_citation" in bad.stdout
    assert "error " in bad.stdout and "yield_mpa" in bad.stdout

    # the same file under --profile user is a warning, so the run is clean
    lenient = _cli(str(dirty), "--profile", "user")
    assert lenient.returncode == 0
    assert "0 errors, 1 warnings" in lenient.stdout

    assert _cli().returncode == 2                      # no paths
    assert _cli(str(tmp_path / "ghost.json")).returncode == 2  # unreadable


@pytest.mark.integration
def test_cli_json_output_and_shipped_library(tmp_path):
    done = _cli(str(DATA_DIR), "--json")
    assert done.returncode == 0, done.stderr
    findings = json.loads(done.stdout)
    assert [f for f in findings if f["level"] == "error"] == []
