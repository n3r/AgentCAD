"""The four-stage check pipeline over a live service (PRD-004, slice 2).

Slice 1 proved the report *shape*; this is the sequencer that fills it. The
rules being pinned here, all of them deliberate:

* **It composes, it never measures.** Every row comes from a surface that
  already exists — ``_ensure_built``, ``_resolved_instances``,
  ``check_interference``, ``SpecRunner.run``, the drawing tools — and a
  failure's ``error`` is that surface's payload **verbatim** (AC4).
* **All four stages always appear.** An unselected one is
  ``skip``/``not_selected``; a stage with nothing to measure names its reason.
* **A skip is data.** ``mesh_only`` stays a skip with its hint under
  ``--strict``; only the derived verdict moves.
* **The budget is a between-item deadline.** What it cut short is reported as
  ``skip``/``budget_exceeded``, ``complete`` goes false and the exit code is 2 —
  a partial report is evidence, a missing one is not.

Examples are always driven on a ``copytree`` copy: nothing here may touch a
byte of ``examples/``.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentcad.core import checks as checks_module
from agentcad.core import locks
from agentcad.core import specs as specs_module
from agentcad.core.branches import pinned_tree_var
from agentcad.core.checks import (
    STAGES,
    CheckRunner,
    finalize_report,
    render_markdown,
    validate_report,
)
from agentcad.core.model import InstanceSpec, NotFoundError, ValidationError
from agentcad.core.tools import build_registry

from .conftest import BOX_SCRIPT, make_test_service

pytestmark = [pytest.mark.integration, pytest.mark.slow,
              pytest.mark.timeout(900)]

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"

# A sheet-metal part: the only kind that declares `flat_pattern`, which is the
# whole point of the presence scan in the drawings stage.
BRACKET = '''\
from agentcad.toolkit.sheetmetal import SheetPart

PARAMS = {
    "width": {"default": 60.0, "min": 10.0, "max": 500.0, "unit": "mm",
              "description": "base plate width"},
    "thick": {"default": 2.0, "min": 0.5, "max": 6.0, "unit": "mm",
              "description": "sheet thickness"},
}

def _sheet(p):
    return SheetPart(p.thick).base(p.width, 40.0).flange("front", 90, 30.0)

def build(p):
    return _sheet(p).fold()

def flat_pattern(p):
    sp = _sheet(p)
    return sp.unfold(), sp.bend_lines()
'''

# One FEM declaration: measured with the [fem] extra, an honest
# `skip`/`fem_extra_missing` without it (AC8).
FEM_BOX = '''\
from build123d import *
from agentcad.toolkit.specs import check_fem_static

PARAMS = {"size": {"default": 20.0, "min": 10.0, "max": 40.0, "unit": "mm",
                   "description": "edge"}}

SPECS = [
    check_fem_static({"axis": "z", "side": "min"}, {"axis": "z", "side": "max"},
                     50.0, max_disp_mm=10.0, requirement="STR-001"),
]

def build(p):
    return Box(p.size, p.size, p.size)
'''


@pytest.fixture(autouse=True)
def _reset_context():
    """Identity and the branch pin are ContextVars: rebind them per test."""
    cid = locks.client_id_var.set("local")
    pin = pinned_tree_var.set(None)
    yield
    locks.client_id_var.reset(cid)
    pinned_tree_var.reset(pin)


@pytest.fixture
def stack(kernel, tmp_path):
    """A service, its full tool registry and a runner over the two."""
    service = make_test_service(tmp_path / "projects", kernel)
    registry = build_registry(service)
    return service, registry, CheckRunner(service, registry)


def _example(service, tmp_path, name: str) -> str:
    """Open a *copy* of a bundled example — never the example itself."""
    dest = tmp_path / "copies" / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(EXAMPLES / name, dest,
                    ignore=shutil.ignore_patterns(".cache", "exports"))
    return service.open_project(str(dest))["name"]


def _stage(report: dict, name: str) -> dict:
    return next(stage for stage in report["stages"] if stage["name"] == name)


def _item(report: dict, ident: str) -> dict:
    found = [item for stage in report["stages"] for item in stage["items"]]
    for item in found:
        if item["id"] == ident:
            return item
    raise AssertionError(f"no item {ident!r} in "
                         f"{[item['id'] for item in found]}")


# ------------------------------------------------- 1. the shape of a run


def test_a_run_always_reports_all_four_stages_and_validates(stack, tmp_path):
    service, _registry, runner = stack
    proj = _example(service, tmp_path, "prototyping")

    report = runner.run(proj)

    assert [stage["name"] for stage in report["stages"]] == list(STAGES)
    assert validate_report(report) == []
    assert report["project"] == "prototyping"
    assert report["source"] == {"kind": "worktree", "ref": None, "sha": None,
                                "label": None, "host_sha": None, "dirty": False}
    # Both version fields come from `agentcad.__version__`, so comparing them
    # to each other proves nothing: compare them to the INSTALLED package's
    # metadata instead, which is what a consumer reading the report resolves.
    from importlib.metadata import version

    assert report["agentcad"] == version("agentcad")
    assert report["host"]["agentcad"] == version("agentcad")
    assert report["host"]["kernel_pool"] in ("KernelClient", "KernelPool")
    assert report["complete"] is True
    assert report["duration_s"] >= 0
    assert all(stage["duration_s"] >= 0 for stage in report["stages"])


def test_the_host_provenance_flags_travel_into_the_source_block(stack, tmp_path):
    """`--sha`/`--ref-label` are the GitHub Action's provenance (Decision 9):
    they name the host commit, they never resolve anything."""
    service, _registry, runner = stack
    proj = _example(service, tmp_path, "prototyping")

    report = runner.run(proj, stages=("build",), sha="9f8e7d6c",
                        ref_label="refs/heads/main")

    assert report["source"]["host_sha"] == "9f8e7d6c"
    assert report["source"]["label"] == "refs/heads/main"
    assert report["source"]["kind"] == "worktree"
    assert "9f8e7d6" in render_markdown(report)


@pytest.mark.parametrize("name", ["construction", "prototyping", "rocketry"])
def test_a_clean_example_is_green_and_exits_zero(stack, tmp_path, name):
    """The bundled examples are the standing regression: every part builds, the
    assembly resolves interference-free, the declared specs hold and every
    drawing regenerates."""
    service, _registry, runner = stack
    proj = _example(service, tmp_path, name)

    report = runner.run(proj)

    assert report["status"] == "green", [
        (item["id"], item["status"], item["message"])
        for stage in report["stages"] for item in stage["items"]
        if item["status"] in ("fail", "error")]
    assert report["exit_code"] == 0
    assert report["errors"] == []
    assert validate_report(report) == []
    assert _stage(report, "build")["status"] == "green"


# ------------------------------------------------------ 2. the build stage


def test_ac4_a_broken_script_fails_the_build_stage_with_the_tool_payload(
        stack, tmp_path):
    """AC4: the row carries what `update_part_script` would have returned —
    the same `details.line` and the same Error-Doctor `details.hint`."""
    service, _registry, runner = stack
    proj = _example(service, tmp_path, "prototyping")
    script = service.store.read_script(proj, "enclosure_lid")
    # A primitive with a non-positive dimension: an OCCT failure the Error
    # Doctor has a catalogued fix for, so the row can prove BOTH halves of AC4.
    broken = script.replace("def build(p):", "def build(p):\n    Box(0, 0, 0)")
    service.store.write_script(proj, "enclosure_lid", broken)

    report = runner.run(proj, stages=("build",))

    stage = _stage(report, "build")
    assert stage["status"] == "red"
    item = _item(report, "build:enclosure_lid")
    assert item["kind"] == "part" and item["status"] == "fail"
    assert item["error"]["type"]
    assert item["error"]["details"]["line"] == \
        broken.splitlines().index("    Box(0, 0, 0)") + 1
    assert item["error"]["details"]["hint"], "the Error Doctor hint travels"
    assert report["status"] == "red" and report["exit_code"] == 1
    assert validate_report(report) == []

    text = render_markdown(report)
    assert "build:enclosure_lid" in text and "## Failures" in text


def test_a_built_part_reports_its_metrics_and_whether_it_was_cached(
        stack, tmp_path):
    service, _registry, runner = stack
    proj = _example(service, tmp_path, "prototyping")

    cold = runner.run(proj, stages=("build",))
    warm = runner.run(proj, stages=("build",))

    item = _item(cold, "build:enclosure_base")
    assert item["status"] == "pass"
    assert item["details"]["volume_mm3"] > 0
    assert item["details"]["mass_g"] > 0
    assert item["details"]["is_valid"] is True
    assert item["details"]["n_solids"] >= 1
    assert item["details"]["cache_key"]
    assert item["details"]["cached"] is False
    assert _item(warm, "build:enclosure_base")["details"]["cached"] is True


def test_a_reference_part_is_an_ordinary_build_row(stack, tmp_path):
    """Mesh-kind skipping belongs to the assembly stage, where the segfault
    risk actually lives; an imported part still builds.

    Its whole-shape `is_valid` is *reported*, never enforced: OCCT routinely
    calls a 180-solid imported STEP compound invalid, which is why
    `test_examples` exempts reference parts and `import_cad_file` only reports
    the flag. A red nobody can act on would be noise.
    """
    service, _registry, runner = stack
    proj = _example(service, tmp_path, "rocketry")

    report = runner.run(proj, stages=("build",))

    item = _item(report, "build:assembly_assembly")
    assert item["status"] == "pass" and item["kind"] == "part"
    assert item["details"]["is_valid"] is False
    assert any("assembly_assembly" in warning and "is_valid" in warning
               for warning in report["warnings"])
    assert "is_valid" in render_markdown(report)


# --------------------------------------------------- 3. the assembly stage


def test_ac2_an_overlapping_pair_reddens_the_assembly_stage(stack, tmp_path):
    """AC2: the offending pair is named in both renderings, with its volume."""
    service, _registry, runner = stack
    proj = _example(service, tmp_path, "construction")
    instances = [i.to_manifest() for i in service.store.instances(proj)]
    first, second = instances[0], instances[1]
    second["position"] = list(first["position"])   # park one inside the other
    service.set_assembly(proj, instances)

    report = runner.run(proj, stages=("build", "assembly"))

    stage = _stage(report, "assembly")
    assert stage["status"] == "red"
    pair = next(item for item in stage["items"] if item["kind"] == "pair")
    assert pair["status"] == "fail"
    assert first["id"] in pair["subject"] and second["id"] in pair["subject"]
    assert pair["details"]["volume_mm3"] > 0
    assert {pair["details"]["a"], pair["details"]["b"]} == {first["id"],
                                                           second["id"]}
    assert report["exit_code"] == 1
    text = render_markdown(report)
    assert first["id"] in text and second["id"] in text


def test_each_resolved_instance_gets_one_row(stack, tmp_path):
    service, _registry, runner = stack
    proj = _example(service, tmp_path, "construction")

    report = runner.run(proj, stages=("build", "assembly"))

    stage = _stage(report, "assembly")
    rows = [item for item in stage["items"] if item["kind"] == "instance"]
    assert [row["subject"] for row in rows] == \
        [inst.id for inst in service.store.instances(proj)]
    assert all(row["status"] == "pass" for row in rows)
    assert all("part" in row["details"] for row in rows)


def test_an_assembly_with_fewer_than_two_instances_skips_the_stage(
        stack, tmp_path):
    service, _registry, runner = stack
    service.create_project("solo")
    service.create_part("solo", "cube", script=BOX_SCRIPT)
    service.set_assembly("solo", [{"id": "cube_1", "part": "cube"}])

    report = runner.run("solo", stages=("assembly",))

    stage = _stage(report, "assembly")
    assert stage["status"] == "skip" and stage["reason"] == "no_instances"
    assert stage["items"] == []


def test_a_mate_that_will_not_resolve_is_one_fail_row_not_a_traceback(
        stack, tmp_path):
    service, _registry, runner = stack
    service.create_project("mated")
    service.create_part("mated", "cube", script=BOX_SCRIPT)
    # Through the store: a connector that does not exist on the target part
    # only fails when the mate is RESOLVED, and `set_assembly` resolves eagerly
    # (so the broken state could never be written through it).
    service.store.set_instances("mated", [
        InstanceSpec(id="cube_1", part="cube"),
        InstanceSpec(id="cube_2", part="cube",
                     mate={"connector": "no_such_connector",
                           "to_instance": "cube_1",
                           "to_connector": "no_such_connector"})])

    report = runner.run("mated", stages=("assembly",))

    stage = _stage(report, "assembly")
    assert stage["status"] == "red"
    item = next(item for item in stage["items"] if item["kind"] == "mate")
    assert item["status"] == "fail"
    assert item["error"]["type"] == "validation_error"
    assert "no_such_connector" in item["error"]["message"]


def test_an_imported_mesh_instance_is_a_skip_and_strict_flips_only_the_verdict(
        stack, tmp_path, kernel):
    """Decision 6 in one test: `pairs: []` beside a non-empty `skipped_mesh` is
    not proof of no interference, so the row is a named skip — and `--strict`
    turns it red **without rewriting it**."""
    service, _registry, runner = stack
    service.create_project("meshy")
    service.create_part("meshy", "cube", script=BOX_SCRIPT)
    stl = tmp_path / "ref.stl"
    kernel.request("export", {"script": BOX_SCRIPT, "params": {},
                              "format": "stl", "out_path": str(stl)})
    service.create_part("meshy", "imported", kind="reference", source="ref.stl")
    shutil.copy(stl, service.store.imports_dir("meshy") / "ref.stl")
    service.set_assembly("meshy", [
        {"id": "cube_1", "part": "cube"},
        {"id": "mesh_1", "part": "imported", "position": [200, 200, 200]}])

    honest = runner.run("meshy", stages=("build", "assembly"))
    item = _item(honest, "assembly:mesh_1")
    assert item["status"] == "skip" and item["reason"] == "mesh_only"
    assert "STL" in item["hint"] or "mesh" in item["hint"]
    assert honest["status"] == "green" and honest["exit_code"] == 0

    strict = runner.run("meshy", stages=("build", "assembly"), strict=True)
    row = _item(strict, "assembly:mesh_1")
    assert row["status"] == "skip" and row["reason"] == "mesh_only"
    assert row["hint"], "the skip still says what to do about it"
    assert strict["strict_failures"] == ["assembly:mesh_1"]
    assert strict["status"] == "red" and strict["exit_code"] == 1


# ------------------------------------------------------ 4. the specs stage


def test_ac3_a_failing_spec_reddens_the_specs_stage_with_measured_and_limit(
        stack, tmp_path):
    """AC3: the row names the check and carries measured vs limit."""
    service, _registry, runner = stack
    proj = _example(service, tmp_path, "rocketry")
    specs_file = Path(service.store.path_of(proj)) / "specs.py"
    specs_file.write_text(
        specs_file.read_text(encoding="utf-8").replace("min_mm=0.3",
                                                       "min_mm=30.0"),
        encoding="utf-8")

    report = runner.run(proj, stages=("build", "specs"))

    stage = _stage(report, "specs")
    assert stage["status"] == "red"
    item = _item(report, "specs:project:flange_bore_gap")
    assert item["kind"] == "check" and item["status"] == "fail"
    assert item["details"]["measured"] is not None
    assert item["details"]["limit"] == {"min_mm": 30.0}
    assert item["details"]["unit"] == "mm"
    assert item["requirement"] == "INT-003"
    assert report["requirements"]["INT-003"]["status"] == "fail"
    assert report["exit_code"] == 1


def test_the_specs_stage_embeds_the_spec_report_whole(stack, tmp_path):
    """Requirement traceability is passed through, never re-derived."""
    service, _registry, runner = stack
    proj = _example(service, tmp_path, "rocketry")

    report = runner.run(proj, stages=("build", "specs"))

    stage = _stage(report, "specs")
    embedded = stage["report"]
    assert embedded["project"] == proj and embedded["declared"] > 0
    assert len(stage["items"]) == len(embedded["checks"])
    # Same requirements, same verdicts — and the check report names the rows by
    # THIS report's item ids, so a reader can follow a requirement to a row.
    assert set(report["requirements"]) == set(embedded["requirements"])
    for key, block in report["requirements"].items():
        assert block["status"] == embedded["requirements"][key]["status"]
        assert block["checks"] == [f"specs:{ident}" for ident
                                   in embedded["requirements"][key]["checks"]]
    assert all(_item(report, ident)
               for block in report["requirements"].values()
               for ident in block["checks"])


def test_a_project_that_declares_nothing_skips_the_specs_stage(stack):
    service, _registry, runner = stack
    service.create_project("plain")
    service.create_part("plain", "cube", script=BOX_SCRIPT)

    report = runner.run("plain", stages=("specs",))

    stage = _stage(report, "specs")
    assert stage["status"] == "skip" and stage["reason"] == "not_declared"


def test_a_service_with_no_spec_runner_skips_the_stage(kernel, tmp_path):
    """`service.specs` is read inside the method — a runner constructed before
    `tools_specs` loaded must degrade, never `AttributeError`."""
    service = make_test_service(tmp_path / "projects", kernel)
    service.create_project("plain")
    runner = CheckRunner(service)           # no registry, no service.specs

    report = runner.run("plain", stages=("specs",))

    stage = _stage(report, "specs")
    assert stage["status"] == "skip" and stage["reason"] == "specs_unavailable"


def test_ac8_a_fem_check_skips_without_the_extra_and_strict_makes_it_red(
        stack, monkeypatch):
    """AC8: the suite is green **without** the `[fem]` extra. The skip keeps
    its reason and hint; `--strict` is the opt-in that makes it red."""
    service, _registry, runner = stack
    service.create_project("fem")
    service.create_part("fem", "fem_box", script=FEM_BOX)
    monkeypatch.setattr(specs_module, "_fem_available", lambda: False)

    report = runner.run("fem", stages=("build", "specs"))

    item = _item(report, "specs:fem_box:fem_static")
    assert item["status"] == "skip" and item["reason"] == "fem_extra_missing"
    assert "fem" in item["hint"]
    assert report["status"] == "green" and report["exit_code"] == 0
    assert report["host"]["fem"] is False

    strict = runner.run("fem", stages=("build", "specs"), strict=True)
    assert strict["exit_code"] == 1
    assert "specs:fem_box:fem_static" in strict["strict_failures"]
    assert _item(strict, "specs:fem_box:fem_static")["status"] == "skip"


# --------------------------------------------------- 5. the drawings stage


def test_every_script_part_gets_a_drawing_row(stack, tmp_path):
    service, _registry, runner = stack
    proj = _example(service, tmp_path, "prototyping")

    report = runner.run(proj, stages=("drawings",))

    stage = _stage(report, "drawings")
    assert stage["status"] == "green"
    assert [item["subject"] for item in stage["items"]] == \
        ["enclosure_base", "enclosure_lid"]
    item = _item(report, "drawings:enclosure_base")
    assert item["kind"] == "drawing" and item["status"] == "pass"
    assert item["details"]["format"] == "svg"
    assert Path(item["details"]["path"]).is_file()


def test_flat_pattern_rows_appear_only_where_the_script_declares_one(stack):
    """A part that does not define `flat_pattern` has NO row — absent, not
    green, not skipped."""
    service, _registry, runner = stack
    service.create_project("sheet")
    service.create_part("sheet", "bracket", script=BRACKET)
    service.create_part("sheet", "cube", script=BOX_SCRIPT)

    report = runner.run("sheet", stages=("drawings",))

    ids = [item["id"] for item in _stage(report, "drawings")["items"]]
    assert "drawings:bracket:flat_pattern" in ids
    assert "drawings:cube:flat_pattern" not in ids
    flat = _item(report, "drawings:bracket:flat_pattern")
    assert flat["kind"] == "flat_pattern" and flat["status"] == "pass"


def test_a_reference_part_skips_the_drawings_stage(stack, tmp_path):
    service, _registry, runner = stack
    proj = _example(service, tmp_path, "rocketry")

    report = runner.run(proj, stages=("drawings",))

    item = _item(report, "drawings:assembly_assembly")
    assert item["status"] == "skip" and item["reason"] == "not_script"
    assert item["hint"]


def test_a_drawing_that_will_not_generate_is_a_fail_row_with_its_payload(
        stack):
    service, _registry, runner = stack
    service.create_project("broken")
    service.create_part("broken", "cube", script=BOX_SCRIPT)
    service.store.write_script("broken", "cube",
                               BOX_SCRIPT.replace("Box(p.size, p.size, p.size)",
                                                  "no_such_name(p.size)"))

    report = runner.run("broken", stages=("drawings",))

    item = _item(report, "drawings:cube")
    assert item["status"] == "fail"
    assert item["error"]["type"] and item["error"]["message"]


def test_without_a_registry_the_drawings_stage_says_so(kernel, tmp_path):
    service = make_test_service(tmp_path / "projects", kernel)
    service.create_project("plain")
    service.create_part("plain", "cube", script=BOX_SCRIPT)

    report = CheckRunner(service).run("plain", stages=("drawings",))

    stage = _stage(report, "drawings")
    assert stage["status"] == "skip"
    assert stage["reason"] == "drawings_unavailable"


# ------------------------------------------- 6. selection, budget, seams


def test_an_unselected_stage_is_reported_as_not_selected(stack, tmp_path):
    service, _registry, runner = stack
    proj = _example(service, tmp_path, "prototyping")

    report = runner.run(proj, stages=("build",))

    assert _stage(report, "build")["status"] == "green"
    for name in ("assembly", "specs", "drawings"):
        stage = _stage(report, name)
        assert stage["status"] == "skip" and stage["reason"] == "not_selected"
        assert stage["items"] == []
    assert report["exit_code"] == 0


def test_an_unknown_stage_name_is_a_validation_error_naming_the_valid_ones(
        stack):
    _service, _registry, runner = stack
    with pytest.raises(ValidationError) as excinfo:
        runner.run("nothing", stages=("build", "fem-smoke"))
    assert "fem-smoke" in str(excinfo.value)
    assert excinfo.value.details["stages"] == list(STAGES)


def test_an_unknown_project_is_a_not_found_error(stack):
    _service, _registry, runner = stack
    with pytest.raises(NotFoundError):
        runner.run("no_such_project")


def test_a_blown_budget_reports_what_it_measured_and_exits_two(stack, tmp_path):
    """FR5: a partial report is evidence; a missing one is not."""
    service, _registry, runner = stack
    proj = _example(service, tmp_path, "construction")

    report = runner.run(proj, budget_s=0.0001)

    assert report["complete"] is False
    assert report["exit_code"] == 2
    assert validate_report(report) == []
    # Whatever the clock did in the microsecond the run had, every stage is
    # accounted for: skipped whole (a reason and no rows), or holding nothing
    # but budget skips — and never an empty stage with no reason at all, which
    # is what a bare `all()` over an empty item list would have waved through.
    for stage in report["stages"]:
        if stage["reason"] is None:
            assert stage["items"], f"{stage['name']} claims nothing and says why nothing"
            assert all(item["reason"] == "budget_exceeded"
                       for item in stage["items"])
        else:
            assert stage["reason"] == "budget_exceeded"
        assert stage["summary"]["passed"] == 0
    text = render_markdown(report)
    assert "budget" in text.lower() and "exit 2" in text


def test_a_budget_that_runs_out_mid_stage_degrades_the_rows_it_did_not_reach(
        stack, tmp_path):
    """The deadline is read **before each item**, not only between stages, so
    an exhausted budget names every part it never reached instead of dropping
    them. Driven at the stage helper because a real clock cannot be made to
    run out at a chosen row."""
    service, _registry, runner = stack
    proj = _example(service, tmp_path, "construction")
    runner._deadline = time.monotonic() - 1.0

    stage = runner._stage_build(proj, set(), [], [], time.monotonic())

    parts = service.store.manifest(proj)["parts"]
    assert [item["id"] for item in stage["items"]] == \
        [f"build:{entry['id']}" for entry in parts]
    assert all(item["status"] == "skip" for item in stage["items"])
    assert all(item["reason"] == "budget_exceeded" and item["hint"]
               for item in stage["items"])
    assert runner._truncated is True, "a truncated run is never `complete`"


def test_the_specs_stage_runs_under_the_pipelines_own_deadline(stack,
                                                               monkeypatch):
    """Review W2: the budget bounded three stages out of four. ``SpecRunner``
    has taken a *deadline* since PRD-003 (``_report`` threads it through every
    tier); ``run_specs`` passes ``None`` deliberately — an engineer asking for a
    full report has asked for the cost — but a check under ``--budget`` must
    hand the specs stage what is left of it, or the whole spec run is
    unpreemptable."""
    import inspect

    service, _registry, runner = stack
    service.create_project("slow")
    service.create_part("slow", "cube", script=BOX_SCRIPT)
    seen: dict = {}
    # The sanctioned entry point takes it: this is a passthrough, not a new
    # budget mechanism.
    assert "deadline" in inspect.signature(specs_module.SpecRunner.run).parameters

    def fake_run(self, proj, part_id=None, ref=None, deadline=None):
        seen["deadline"] = deadline
        # Unbounded: five seconds of tiers. Bounded: stop at the deadline, the
        # way every budgeted spec call does.
        time.sleep(5.0 if deadline is None
                   else max(0.0, deadline - time.monotonic()))
        return {"declared": 0, "checks": [], "warnings": []}

    monkeypatch.setattr(specs_module.SpecRunner, "run", fake_run)
    started = time.monotonic()

    report = runner.run("slow", stages=("specs",), budget_s=1.0)

    elapsed = time.monotonic() - started
    assert seen["deadline"] is not None, "the specs stage ran unbounded"
    assert elapsed < 3.0, f"the budget did not bound the specs stage ({elapsed:.1f}s)"
    assert report["complete"] is False, "a run cut short is never complete"
    assert report["exit_code"] == 2


class _SlowBuilds:
    """A stand-in for the two services ``_determinism_item`` drives: it records
    every build it is asked for and charges the clock for it."""

    def __init__(self, seconds: float, calls: list, cache: Path):
        self.seconds, self.calls = seconds, calls
        self.store = SimpleNamespace(cache_dir=lambda proj: cache)

    def _ensure_built(self, proj: str, part_id: str) -> dict:
        self.calls.append((proj, part_id))
        time.sleep(self.seconds)
        return {"ok": True, "cache_key": "k", "metrics": {}}


def test_determinism_reads_the_deadline_before_each_kernel_call(tmp_path):
    """Review W2: one budget check guarded **four** unpreemptable kernel calls
    (two 300 s builds and two 120 s drawings), so ``--budget`` could overshoot
    by four calls inside a single determinism row. The deadline is read before
    each of them."""
    calls: list = []
    first = _SlowBuilds(0.4, calls, tmp_path)
    second = _SlowBuilds(0.4, calls, tmp_path)

    runner = CheckRunner(first, None)
    runner._deadline = time.monotonic() + 0.2      # already below the floor
    row = runner._determinism_item(runner, second, None, "p", "p", "cube",
                                   {"kind": "script"}, set(), [], [])

    assert calls == [], "a call was issued the budget could not pay for"
    assert row["status"] == "skip" and row["reason"] == "budget_exceeded"
    assert row["hint"] and runner._truncated is True

    # And between the two builds: the first is affordable, the second is not.
    calls.clear()
    runner = CheckRunner(first, None)
    runner._deadline = time.monotonic() + 1.2
    row = runner._determinism_item(runner, second, None, "p", "p", "cube",
                                   {"kind": "script"}, set(), [], [])

    assert len(calls) == 1
    assert row["status"] == "skip" and row["reason"] == "budget_exceeded"
    assert runner._truncated is True


def test_no_stage_starts_a_kernel_call_the_budget_cannot_pay_for(stack,
                                                                 monkeypatch):
    """The floor is one rule, applied everywhere: a build (300 s) and a drawing
    (120 s) take no ``timeout_s``, so one started with a fraction of a second
    left cannot be preempted — it just overshoots the budget and then reports
    the overshoot as a failure. Below the floor the item is a
    ``budget_exceeded`` skip and the call is never made."""
    service, _registry, runner = stack
    service.create_project("floor")
    service.create_part("floor", "cube", script=BOX_SCRIPT)
    calls: list[str] = []
    monkeypatch.setattr(service, "_ensure_built",
                        lambda proj, part_id: calls.append(part_id))
    runner._deadline = time.monotonic() + 0.2      # positive, under the floor

    build = runner._stage("build", "floor", {"build"}, set(), [], [])
    drawings = runner._stage("drawings", "floor", {"drawings"}, set(), [], [])

    assert calls == [], "a kernel build was started the budget could not pay for"
    for stage in (build, drawings):
        assert [item["reason"] for item in stage["items"]] == \
            ["budget_exceeded"]
        assert stage["summary"]["errors"] == 0
    assert runner._truncated is True


def test_a_budget_that_expires_at_the_assembly_boundary_is_a_skip_not_a_red(
        stack):
    """Review W3: with a fraction of a second left, ``_resolved_instances`` and
    ``check_interference`` got that fraction as their ``timeout_s`` and the
    kernel timed out — an ``error`` row, ``complete: true`` and exit **1**,
    i.e. "the model is wrong" reported for a budget that ran out. A stage item
    that fails *because* the deadline expired is the truncation path."""
    service, _registry, runner = stack
    service.create_project("assy")
    service.create_part("assy", "cube", script=BOX_SCRIPT)
    service.set_assembly("assy", [
        {"id": "cube_1", "part": "cube"},
        {"id": "cube_2", "part": "cube", "position": [50.0, 0.0, 0.0]}])
    runner._deadline = time.monotonic() + 0.05     # positive, but unaffordable

    stage = runner._stage("assembly", "assy", {"assembly"}, set(), [], [])

    # Not red, and not measured: one `budget_exceeded` skip, which is the row
    # every other truncated item gets. (A stage holding only skips reads
    # `green` — skips never redden anything — and `complete: false` is what
    # turns the report into exit 2.)
    assert stage["status"] != "red"
    assert stage["summary"]["errors"] == 0 and stage["summary"]["failed"] == 0
    assert [(item["id"], item["status"], item["reason"])
            for item in stage["items"]] == [("assembly:mates", "skip",
                                             "budget_exceeded")]
    assert stage["items"][0]["hint"]
    assert runner._truncated is True
    report = finalize_report("assy", [stage], source={"kind": "worktree"},
                             host={"platform": "test"},
                             started="2026-01-01T00:00:00Z",
                             complete=not runner._truncated)
    assert report["exit_code"] == 2, "a blown budget is harness, never red"


@pytest.mark.parametrize("method,subject", [
    ("_resolved_instances", "assembly:mates"),
    ("check_interference", "assembly:interference"),
])
def test_a_kernel_timeout_the_deadline_caused_is_a_budget_skip(
        stack, monkeypatch, method, subject):
    """The same rule one layer down: a timeout on a call whose ``timeout_s``
    was *the remaining budget* (below the call's own ceiling) is the budget
    running out, not the geometry failing."""
    from agentcad.kernel.client import ERROR_TIMEOUT, KernelError

    service, _registry, runner = stack
    service.create_project("assy2")
    service.create_part("assy2", "cube", script=BOX_SCRIPT)
    service.set_assembly("assy2", [
        {"id": "cube_1", "part": "cube"},
        {"id": "cube_2", "part": "cube", "position": [50.0, 0.0, 0.0]}])

    def timeout(*args, **kwargs):
        raise KernelError(ERROR_TIMEOUT,
                          "kernel request 'x' exceeded 4s; worker restarted")

    monkeypatch.setattr(service, method, timeout)
    runner._deadline = time.monotonic() + 5.0      # above the floor, below 120 s

    stage = runner._stage("assembly", "assy2", {"assembly"}, set(), [], [])

    row = next(item for item in stage["items"] if item["id"] == subject)
    assert row["status"] == "skip" and row["reason"] == "budget_exceeded"
    assert runner._truncated is True
    assert stage["summary"]["errors"] == 0

    # Without a deadline the very same timeout is what it has always been: the
    # kernel broke, and we do not know.
    runner._deadline, runner._truncated = None, False
    stage = runner._stage("assembly", "assy2", {"assembly"}, set(), [], [])
    row = next(item for item in stage["items"] if item["id"] == subject)
    assert row["status"] == "error" and runner._truncated is False


def test_a_generous_budget_does_not_truncate_anything(stack, tmp_path):
    service, _registry, runner = stack
    proj = _example(service, tmp_path, "prototyping")

    report = runner.run(proj, stages=("build",), budget_s=600.0)

    assert report["complete"] is True
    assert all(item["status"] == "pass"
               for item in _stage(report, "build")["items"])


def test_ref_and_verify_determinism_are_live_not_silent_no_ops(stack):
    """Slice 3 landed both, and the seam stayed honest through the change: a
    `--ref` this runner cannot satisfy is refused by name, never quietly
    answered by measuring the working tree and calling it a ref.

    (`tests/test_checks_ref.py` owns the behaviour; this pins the seam the
    pipeline declared in slice 2 — it raised `NotImplementedError` here.)
    """
    service, _registry, runner = stack
    service.create_project("unversioned")
    with pytest.raises(NotFoundError):
        runner.run("anything", ref="feat/nozzle")
    with pytest.raises(ValidationError) as excinfo:
        runner.run("unversioned", ref="feat/nozzle")
    assert "git" in str(excinfo.value)
    assert runner.run("unversioned", stages=("build",),
                      verify_determinism=True)["stages"][-1]["name"] == \
        "determinism"


def test_the_runner_never_captures_service_specs_at_construction(stack):
    """The load-order trap (design "Load order"): the pack registers at `r`,
    before `tools_specs` at `s`, so the runner may only read `service.specs`
    inside a method."""
    service, _registry, _runner = stack
    del service.specs
    runner = CheckRunner(service)
    assert not any(isinstance(value, specs_module.SpecRunner)
                   for value in vars(runner).values())


def test_the_pipeline_module_is_still_free_of_the_geometry_kernel():
    """`core/checks.py` grew a sequencer, not a measurement.

    `tests/test_checks.py` runs the real probe (import the module in a fresh
    interpreter with OCP and build123d blocked at `sys.meta_path`); this pins
    the reason it still passes — the sequencer's only kernel import is
    `KernelError`, from the module that *spawns* workers rather than one that
    imports geometry.
    """
    from agentcad.kernel import client

    assert checks_module.KernelError is client.KernelError
    assert checks_module.CheckRunner.__module__ == "agentcad.core.checks"
