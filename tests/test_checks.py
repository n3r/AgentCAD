"""The pure layer of geometry CI: rows, stages, the report and its renderings.

No kernel, no service, no git — every test here is a dict in and a dict (or a
string, or an int) out. That is the point of the layer: the contract slices 2
onwards are written against is provable without building a single solid.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from agentcad.core import checks as checks_module
from agentcad.core import specs as specs_module
from agentcad.core.checks import (
    ALL_STAGES,
    MAX_RENDERED_FAILURES,
    REPORT_SCHEMA,
    STAGES,
    declares_flat_pattern,
    exit_code,
    finalize_report,
    make_item,
    make_stage,
    render_markdown,
    validate_report,
)

SOURCE = {"kind": "worktree", "ref": None, "sha": None, "label": None,
          "host_sha": None, "dirty": False}
HOST = {"platform": "darwin", "python": "3.12.4", "agentcad": "0.1.0",
        "fem": False, "sandbox": True, "pool_size": 1,
        "kernel_pool": "KernelPool"}


def _report(stages: list[dict], **kwargs) -> dict:
    return finalize_report("demo", stages, source=dict(SOURCE),
                           host=dict(HOST), started="2026-08-11T09:14:02Z",
                           finished="2026-08-11T09:14:44Z", duration_s=42.3,
                           **kwargs)


def _all_stages(*present: dict) -> list[dict]:
    """The four declared stages, with *present* filling in by name and the
    rest reported as ``not_selected`` — the shape a real run always has."""
    by_name = {stage["name"]: stage for stage in present}
    return [by_name.get(name)
            or make_stage(name, reason="not_selected") for name in STAGES]


# --------------------------------------------------------- 1. the vocabulary


def test_the_status_vocabulary_is_prd_003s_verbatim():
    """One product, one vocabulary: these are imports, not re-implementations.
    A second `summarize` would be a second definition of `green`."""
    assert checks_module.summarize is specs_module.summarize
    assert checks_module.report_status is specs_module.report_status
    assert checks_module.group_requirements is specs_module.group_requirements
    assert checks_module.assign_ids is specs_module.assign_ids


def test_stage_names_are_declared_in_order():
    assert STAGES == ("build", "assembly", "specs", "drawings")
    assert ALL_STAGES == STAGES + ("determinism",)


# ------------------------------------------------------------- 2. make_item


def test_make_item_ids_are_stage_colon_subject():
    item = make_item("build", "part", "nozzle", "pass", "built in 3.1 s",
                     details={"volume_mm3": 41240.0})
    assert item["id"] == "build:nozzle"
    assert item["kind"] == "part" and item["subject"] == "nozzle"
    assert item["status"] == "pass"
    assert item["reason"] is None and item["hint"] is None
    assert item["error"] is None
    assert item["details"] == {"volume_mm3": 41240.0}


def test_make_item_dedupes_ids_and_warns():
    """A duplicate subject must never merge two rows into one — `assign_ids`'
    rule, reused rather than restated."""
    seen: set[str] = set()
    warnings: list[str] = []
    first = make_item("build", "part", "nozzle", "pass", "ok", seen=seen,
                      warnings=warnings)
    second = make_item("build", "part", "nozzle", "fail", "no", seen=seen,
                       warnings=warnings)
    assert first["id"] == "build:nozzle"
    assert second["id"] == "build:nozzle#2"
    assert len(warnings) == 1 and "nozzle" in warnings[0]


def test_a_skip_item_must_carry_a_reason_and_a_hint():
    """PRD-003's rule, enforced at construction: a skip with no reason is a
    programming error, not a silent row."""
    with pytest.raises(ValueError):
        make_item("assembly", "instance", "ref_1", "skip", "skipped")
    with pytest.raises(ValueError):
        make_item("assembly", "instance", "ref_1", "skip", "skipped",
                  reason="mesh_only")
    with pytest.raises(ValueError):
        make_item("assembly", "instance", "ref_1", "skip", "skipped",
                  hint="install the extra")
    item = make_item("assembly", "instance", "ref_1", "skip", "skipped",
                     reason="mesh_only", hint="booleans on an STL segfault")
    assert item["reason"] == "mesh_only" and item["hint"]


def test_make_item_rejects_an_unknown_status_or_kind():
    with pytest.raises(ValueError):
        make_item("build", "part", "nozzle", "green", "…")
    with pytest.raises(ValueError):
        make_item("build", "widget", "nozzle", "pass", "…")


def test_make_item_carries_the_tool_payload_verbatim():
    """FR7: `error` is what the tool returned — not a re-wording of it."""
    payload = {"type": "script_error", "message": "name 'x' is not defined",
               "details": {"traceback": "…", "line": 37, "hint": "define x"}}
    item = make_item("build", "part", "nozzle", "fail", "build failed",
                     error=payload)
    assert item["error"] is payload
    assert item["error"]["details"]["line"] == 37


# ------------------------------------------------------------ 3. make_stage


def test_make_stage_summarizes_and_statuses_itself():
    items = [make_item("build", "part", "a", "pass", "ok"),
             make_item("build", "part", "b", "fail", "boom")]
    stage = make_stage("build", items, duration_s=8.1)
    assert stage["name"] == "build" and stage["status"] == "red"
    assert stage["reason"] is None and stage["duration_s"] == 8.1
    assert stage["summary"] == {"passed": 1, "failed": 1, "skipped": 0,
                                "errors": 0, "total": 2}
    assert stage["items"] == items


def test_a_stage_with_only_passes_is_green_and_an_empty_one_skips():
    green = make_stage("build", [make_item("build", "part", "a", "pass", "ok")])
    assert green["status"] == "green"
    assert make_stage("build", [])["status"] == "skip"


def test_an_explicitly_skipped_stage_keeps_its_reason():
    stage = make_stage("specs", reason="not_declared")
    assert stage["status"] == "skip" and stage["reason"] == "not_declared"
    assert stage["items"] == []


def test_a_skip_row_never_makes_a_stage_red():
    stage = make_stage("assembly", [
        make_item("assembly", "instance", "ref_1", "skip", "excluded",
                  reason="mesh_only", hint="STL booleans segfault OCCT")])
    assert stage["status"] == "green"


def test_the_specs_stage_can_embed_its_own_report():
    """Requirement traceability is passed through, never re-derived."""
    spec_report = {"project": "demo", "checks": [], "declared": 0}
    stage = make_stage("specs", [], report=spec_report)
    assert stage["report"] is spec_report
    assert "report" not in make_stage("build", [])


# ------------------------------------------------------- 4. finalize_report


def test_finalize_report_has_the_documented_shape():
    build = make_stage("build", [make_item("build", "part", "a", "pass", "ok")])
    report = _report(_all_stages(build))
    assert report["schema"] == REPORT_SCHEMA == 1
    assert report["project"] == "demo"
    assert report["agentcad"] == HOST["agentcad"]
    assert report["source"]["kind"] == "worktree"
    assert report["started"] == "2026-08-11T09:14:02Z"
    assert report["finished"] == "2026-08-11T09:14:44Z"
    assert report["duration_s"] == 42.3
    assert report["status"] == "green"
    assert report["complete"] is True and report["strict"] is False
    assert report["strict_failures"] == []
    assert report["exit_code"] == 0
    assert report["summary"] == {"passed": 1, "failed": 0, "skipped": 0,
                                 "errors": 0, "total": 1}
    assert [stage["name"] for stage in report["stages"]] == list(STAGES)
    assert report["requirements"] == {}
    assert report["warnings"] == [] and report["errors"] == []
    assert report["host"] == HOST
    assert validate_report(report) == []


def test_the_summary_is_every_stages_items_flattened():
    build = make_stage("build", [make_item("build", "part", "a", "pass", "ok"),
                                 make_item("build", "part", "b", "pass", "ok")])
    assembly = make_stage("assembly", [
        make_item("assembly", "pair", "a ↔ b", "fail", "overlap by 812.4 mm³",
                  details={"a": "a", "b": "b", "volume_mm3": 812.4})])
    report = _report(_all_stages(build, assembly))
    assert report["summary"]["total"] == 3
    assert report["summary"]["failed"] == 1
    assert report["status"] == "red" and report["exit_code"] == 1


def test_a_pair_the_kernel_could_not_boolean_reads_as_indeterminate():
    """A degenerate pair (kernel/handlers/_bop.py) is in `pairs` because the
    worker fails closed, and its `volume_mm3` is 0.0 and means nothing. Rendered
    with the ordinary sentence it would say "overlap by 0.0 mm³", which reads as
    a rounding artefact — the opposite of "the measurement did not happen".
    `_pair_item` touches no `self`, so it is callable unbound: still a pure
    test, no kernel."""
    from agentcad.core.checks import CheckRunner

    row = CheckRunner._pair_item(None, {"a": "elbow_a", "b": "elbow_b",
                                        "volume_mm3": 0.0,
                                        "degenerate": True}, set(), [])

    assert row["status"] == "fail"
    assert row["message"] == ("elbow_a and elbow_b: indeterminate (degenerate "
                              "boolean) — counted as interfering")
    assert row["details"]["degenerate"] is True


def test_an_ordinary_overlapping_pair_keeps_the_sentence_it_always_had():
    from agentcad.core.checks import CheckRunner

    row = CheckRunner._pair_item(None, {"a": "a", "b": "b",
                                        "volume_mm3": 812.4}, set(), [])

    assert row["message"] == "a and b overlap by 812.4 mm³"
    assert "degenerate" not in row["details"]


def test_requirements_come_from_the_specs_stages_rows():
    specs_stage = make_stage("specs", [
        make_item("specs", "check", "nozzle:throat_wall", "pass", "3.0 mm",
                  details={}, requirement="REQ-12"),
        make_item("specs", "check", "nozzle:mass", "fail", "too heavy",
                  requirement="REQ-12"),
        make_item("specs", "check", "case:wall", "pass", "ok",
                  requirement="REQ-13")])
    report = _report(_all_stages(specs_stage))
    assert report["requirements"] == {
        "REQ-12": {"status": "fail",
                   "checks": ["specs:nozzle:throat_wall", "specs:nozzle:mass"]},
        "REQ-13": {"status": "pass", "checks": ["specs:case:wall"]}}


def test_an_incomplete_report_keeps_what_it_measured():
    """FR5: a partial report is evidence; a missing one is not."""
    build = make_stage("build", [make_item("build", "part", "a", "pass", "ok")])
    truncated = make_stage("assembly", reason="budget_exceeded")
    report = _report(_all_stages(build, truncated), complete=False)
    assert report["complete"] is False
    assert report["status"] == "green"          # nothing measured said no
    assert report["exit_code"] == 2             # but we could not finish
    assert validate_report(report) == []


def test_strict_flips_the_verdict_without_rewriting_a_single_row():
    """Decision 6: a reader of report.json can always tell what was measured
    from what was demanded."""
    stage = make_stage("assembly", [
        make_item("assembly", "instance", "ref_1", "skip", "excluded",
                  reason="mesh_only", hint="STL booleans segfault OCCT")])
    honest = _report(_all_stages(stage))
    assert honest["status"] == "green" and honest["exit_code"] == 0
    assert honest["strict_failures"] == []

    strict = _report(_all_stages(make_stage("assembly", [
        make_item("assembly", "instance", "ref_1", "skip", "excluded",
                  reason="mesh_only", hint="STL booleans segfault OCCT")])),
        strict=True)
    assert strict["strict"] is True
    assert strict["strict_failures"] == ["assembly:ref_1"]
    assert strict["status"] == "red" and strict["exit_code"] == 1
    row = strict["stages"][1]["items"][0]
    assert row["status"] == "skip" and row["reason"] == "mesh_only"
    assert row["hint"]


def test_strict_never_counts_a_row_that_is_exempt_by_construction():
    """Review W8, at the pure level: `--strict` asks *"is anything unmeasured
    that could have been measured"*. A skip that no project can ever make pass
    (the DXF determinism row) is not a candidate — counting it would make the
    flag permanently red and say nothing. The row is untouched: same status,
    same reason, same hint, same place in the counts."""
    stage = make_stage("determinism", [
        make_item("determinism", "part", "cube", "pass", "identical"),
        make_item("determinism", "drawing", "dxf", "skip", "not compared",
                  reason="not_byte_stable", hint="ezdxf stamps $TDCREATE",
                  strict_exempt=True)])
    report = _report([stage], strict=True)

    assert report["strict_failures"] == []
    assert report["status"] == "green" and report["exit_code"] == 0
    assert report["summary"]["skipped"] == 1
    row = stage["items"][1]
    assert row["status"] == "skip" and row["reason"] == "not_byte_stable"
    assert row["strict_exempt"] is True
    assert stage["items"][0]["strict_exempt"] is False
    assert validate_report(report) == []

    # It is meaningless on anything but a skip, both at construction …
    with pytest.raises(ValueError, match="strict-exempt"):
        make_item("build", "part", "a", "pass", "ok", strict_exempt=True)
    # … and to a consumer validating a document somebody else wrote.
    forged = _report([make_stage("build", [
        make_item("build", "part", "a", "pass", "ok")])])
    forged["stages"][0]["items"][0]["strict_exempt"] = True
    assert any("strict-exempt" in problem for problem in validate_report(forged))
    forged["stages"][0]["items"][0]["strict_exempt"] = "yes"
    assert any("strict_exempt" in problem for problem in validate_report(forged))


def test_finalize_report_defaults_are_a_finished_now_and_no_stages():
    report = finalize_report("demo", [], source=dict(SOURCE), host=dict(HOST),
                             started="2026-08-11T09:14:02Z")
    assert report["finished"].endswith("Z")
    assert report["status"] == "skip" and report["exit_code"] == 0
    assert validate_report(report) == []


# --------------------------------------------------------- 5. the exit code


def _coded(*, status="green", complete=True, strict=False,
           strict_failures=(), summary=None) -> dict:
    return {"status": status, "complete": complete, "strict": strict,
            "strict_failures": list(strict_failures),
            "summary": summary or {"passed": 1, "failed": 0, "skipped": 0,
                                   "errors": 0, "total": 1}}


def test_exit_code_zero_is_green_and_complete():
    assert exit_code(_coded()) == 0


def test_exit_code_one_is_the_model_being_wrong():
    assert exit_code(_coded(status="red", summary={
        "passed": 0, "failed": 1, "skipped": 0, "errors": 0, "total": 1})) == 1
    # a spec `error` is "we do not know" about the MODEL — 1, never 2
    assert exit_code(_coded(status="red", summary={
        "passed": 0, "failed": 0, "skipped": 0, "errors": 1, "total": 1})) == 1


def test_exit_code_one_is_strict_meeting_a_skip():
    assert exit_code(_coded(summary={"passed": 0, "failed": 0, "skipped": 1,
                                     "errors": 0, "total": 1})) == 0
    assert exit_code(_coded(strict=True, strict_failures=["assembly:ref_1"],
                            summary={"passed": 0, "failed": 0, "skipped": 1,
                                     "errors": 0, "total": 1})) == 1
    # strict with nothing skipped is still green
    assert exit_code(_coded(strict=True)) == 0


def test_exit_code_two_outranks_everything_else():
    """`complete: false` means we could not produce a verdict, so the verdict
    we did produce is not the answer."""
    assert exit_code(_coded(complete=False)) == 2
    assert exit_code(_coded(complete=False, status="red", summary={
        "passed": 0, "failed": 3, "skipped": 0, "errors": 0, "total": 3})) == 2
    assert exit_code(_coded(complete=False, strict=True,
                            strict_failures=["a:b"])) == 2


def test_exit_code_tolerates_a_report_missing_its_optional_keys():
    assert exit_code({"summary": {"failed": 0, "errors": 0}}) == 0
    assert exit_code({"summary": {"failed": 1, "errors": 0}}) == 1
    assert exit_code({}) == 0


# ----------------------------------------------------------- 6. the validator


def _valid() -> dict:
    build = make_stage("build", [
        make_item("build", "part", "nozzle", "fail", "build failed",
                  error={"type": "script_error", "message": "boom",
                         "details": {"line": 37, "hint": "define x"}})])
    assembly = make_stage("assembly", [
        make_item("assembly", "instance", "ref_1", "skip", "excluded",
                  reason="mesh_only", hint="STL booleans segfault OCCT")])
    return _report(_all_stages(build, assembly))


def test_a_well_formed_report_validates():
    assert validate_report(_valid()) == []


def test_the_validator_rejects_a_non_dict_and_a_wrong_schema():
    assert validate_report([]) == ["report is not an object"]
    bad = _valid()
    bad["schema"] = 2
    assert any("schema" in problem for problem in validate_report(bad))


def test_the_validator_names_a_missing_key():
    bad = _valid()
    del bad["summary"]
    problems = validate_report(bad)
    assert any("summary" in problem for problem in problems)


def test_the_validator_checks_every_enum():
    bad = _valid()
    bad["status"] = "amber"
    assert any("status" in problem and "amber" in problem
               for problem in validate_report(bad))

    bad = _valid()
    bad["stages"][0]["items"][0]["status"] = "failed"
    assert any("failed" in problem for problem in validate_report(bad))

    bad = _valid()
    bad["source"]["kind"] = "svn"
    assert any("svn" in problem for problem in validate_report(bad))

    bad = _valid()
    bad["stages"][0]["status"] = "pass"      # a stage is green/red/skip
    assert any("pass" in problem for problem in validate_report(bad))


def test_the_validator_rejects_an_unknown_stage_name():
    bad = _valid()
    bad["stages"][0]["name"] = "fem-smoke"
    assert any("fem-smoke" in problem for problem in validate_report(bad))


def test_the_validator_rejects_duplicate_item_ids():
    bad = _valid()
    bad["stages"][0]["items"].append(dict(bad["stages"][0]["items"][0]))
    assert any("duplicate" in problem for problem in validate_report(bad))


def test_the_validator_rejects_a_skip_with_no_reason_or_hint():
    bad = _valid()
    bad["stages"][1]["items"][0]["hint"] = None
    problems = validate_report(bad)
    assert any("hint" in problem for problem in problems)


def test_the_validator_rejects_a_strict_failure_naming_no_row():
    bad = _valid()
    bad["strict_failures"] = ["assembly:ghost"]
    assert any("assembly:ghost" in problem for problem in validate_report(bad))


def test_the_validator_checks_the_summary_shape_and_the_exit_code():
    bad = _valid()
    bad["summary"] = {"passed": 1}
    assert any("summary" in problem for problem in validate_report(bad))

    bad = _valid()
    bad["exit_code"] = 3
    assert any("exit_code" in problem for problem in validate_report(bad))


def test_the_validator_checks_a_gate_state_where_one_appears():
    """`state` is the gate's vocabulary; `status` is the row's. They are not
    interchangeable and the validator is where that is enforced."""
    bad = _valid()
    bad["state"] = "green"
    assert any("state" in problem for problem in validate_report(bad))
    ok = _valid()
    ok["state"] = "pending"
    assert validate_report(ok) == []


def test_the_validator_accepts_the_determinism_pseudo_stage():
    report = _valid()
    report["stages"].append(make_stage("determinism", [
        make_item("determinism", "part", "nozzle", "pass", "identical")]))
    assert validate_report(report) == []


# ------------------------------------------------------------ 7. the markdown


def test_the_markdown_header_names_the_run():
    report = _valid()
    text = render_markdown(report)
    header = "\n".join(text.splitlines()[:4])
    assert header.startswith("#")
    assert "demo" in header and "red" in header
    assert "0.1.0" in header and "darwin" in header
    assert "fem: no" in header and "exit 1" in header


def test_the_markdown_has_a_row_for_every_stage():
    text = render_markdown(_valid())
    assert "| Stage | Status |" in text
    for name in STAGES:
        assert f"| {name} |" in text


def test_the_markdown_names_each_failure_with_its_line_and_hint():
    text = render_markdown(_valid())
    assert "## Failures" in text
    assert "build:nozzle" in text and "build failed" in text
    assert "line 37" in text
    assert "define x" in text


def test_the_markdown_groups_skips_by_reason():
    text = render_markdown(_valid())
    assert "## Skipped" in text
    assert "mesh_only" in text and "assembly:ref_1" in text


def test_a_green_report_has_no_failures_section():
    report = _report(_all_stages(make_stage(
        "build", [make_item("build", "part", "a", "pass", "ok")])))
    text = render_markdown(report)
    assert "## Failures" not in text
    assert "## Skipped" not in text
    assert "green" in text


def test_the_markdown_caps_rendered_failures():
    """$GITHUB_STEP_SUMMARY is capped at 1 MiB; 300 broken parts must not
    write 300 blocks."""
    over = MAX_RENDERED_FAILURES + 7
    items = [make_item("build", "part", f"p{i}", "fail", f"boom {i}")
             for i in range(over)]
    text = render_markdown(_report(_all_stages(make_stage("build", items))))
    assert text.count("### ") == MAX_RENDERED_FAILURES
    assert "_+7 more — see report.json_" in text
    assert len(text) < 1024 * 1024


def test_the_markdown_says_when_a_run_was_cut_short():
    report = _report(_all_stages(make_stage("assembly",
                                            reason="budget_exceeded")),
                     complete=False)
    text = render_markdown(report)
    assert "budget" in text.lower()
    assert "exit 2" in text


def test_the_markdown_reports_strict_mode():
    report = _report(_all_stages(make_stage("assembly", [
        make_item("assembly", "instance", "ref_1", "skip", "excluded",
                  reason="mesh_only", hint="STL booleans segfault OCCT")])),
        strict=True)
    text = render_markdown(report)
    assert "strict" in text
    assert "assembly:ref_1" in text


def test_the_markdown_names_the_ref_and_the_host_provenance():
    report = _valid()
    report["source"] = {"kind": "branch", "ref": "feat/nozzle",
                        "sha": "a1b2c3d4e5f6", "label": "refs/heads/feat/nozzle",
                        "host_sha": "9f8e7d6c", "dirty": True}
    text = render_markdown(report)
    assert "feat/nozzle" in text and "a1b2c3d" in text
    assert "9f8e7d6" in text
    assert "dirty" in text


# ---------------------------------------------- 8. the flat_pattern presence scan


def test_declares_flat_pattern_finds_a_module_level_def():
    assert declares_flat_pattern("def flat_pattern(p, part):\n    return None\n")
    assert declares_flat_pattern(
        "import x\n\n\nasync def flat_pattern(p):\n    return None\n")


def test_a_part_that_does_not_define_it_declares_nothing():
    assert declares_flat_pattern("def build(p):\n    return None\n") is False
    assert declares_flat_pattern("") is False
    assert declares_flat_pattern(None) is False


def test_a_nested_definition_is_not_a_declaration():
    """`flat_pattern` is called from the module namespace; a local one is
    invisible there."""
    nested = ("class Sheet:\n"
              "    def flat_pattern(self):\n"
              "        return None\n")
    assert declares_flat_pattern(nested) is False
    inner = ("def build(p):\n"
             "    def flat_pattern(q):\n"
             "        return None\n"
             "    return None\n")
    assert declares_flat_pattern(inner) is False


def test_a_script_that_will_not_parse_falls_back_to_text_and_fails_closed():
    """PRD-003's precedent: a false positive costs one row on a script that
    already failed its build; a false negative loses a declared surface."""
    broken = "def build(p:\n    pass\n\ndef flat_pattern(p, part):\n    pass\n"
    assert declares_flat_pattern(broken) is True
    assert declares_flat_pattern("def build(p:\n    pass\n") is False
    assert declares_flat_pattern("# def flat_pattern(p)\ndef build(p:\n") is False


def test_the_presence_scan_is_memoized_and_bounded():
    checks_module._FLAT_MEMO.clear()
    script = "def flat_pattern(p, part):\n    return None\n"
    assert declares_flat_pattern(script) is True
    assert len(checks_module._FLAT_MEMO) == 1
    assert declares_flat_pattern(script) is True
    assert len(checks_module._FLAT_MEMO) == 1
    for index in range(checks_module._MEMO_LIMIT + 2):
        declares_flat_pattern(f"def build(p):\n    return {index}\n")
    assert len(checks_module._FLAT_MEMO) <= checks_module._MEMO_LIMIT + 1


def test_the_presence_scan_never_executes_the_script():
    script = ("raise SystemExit('executed')\n"
              "def flat_pattern(p, part):\n    return None\n")
    assert declares_flat_pattern(script) is True


# ------------------------------------------------------- 9. no geometry kernel


_NO_KERNEL_PROBE = """
import importlib
import sys


class _Blocked:
    \"\"\"Refuse OCP/build123d so an accidental kernel import is a hard error.\"\"\"

    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in ("OCP", "build123d"):
            raise ImportError("blocked kernel import: " + name)
        return None


sys.meta_path.insert(0, _Blocked())
mod = importlib.import_module("agentcad.core.checks")
assert mod.REPORT_SCHEMA == 1
assert mod.declares_flat_pattern("def flat_pattern(p):\\n    pass\\n") is True
assert "OCP" not in sys.modules and "build123d" not in sys.modules
print("ok")
"""


@pytest.mark.integration
@pytest.mark.portability
def test_the_report_layer_imports_with_no_geometry_kernel_available():
    """`core/checks.py` is server-process code: it sequences kernel calls
    through the service and must import where OCP cannot."""
    repo = Path(__file__).resolve().parents[1]
    proc = subprocess.run([sys.executable, "-c", _NO_KERNEL_PROBE],
                          cwd=repo, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
