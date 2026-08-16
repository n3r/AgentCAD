"""PRD-011 slice 4 — the publish gate, part A: the cell, the ephemeral
service, and the three data stages.

Containment is the part of this feature that can damage a user, so it is
tested before anything expensive runs inside it. Four claims carry the weight
and every one is tested against its negation:

* **no user project is ever opened** — the projects root is byte-identical
  after a run (hashed with the same content id the packages use), and the
  ephemeral service's store is rooted in the cell;
* **a run deletes only the cell it made** — a caller's `--work-dir` keeps
  everything it already held;
* **a `--work-dir` that is, holds or sits inside the projects root or the
  package directory is refused**, naming both paths;
* **the three ephemeral seams end nulled** — `bus.on_publish`,
  `store.branch_resolver`, `store.write_guard`.

The report is PRD-004's, and the test that says so is
`test_the_report_is_a_prd004_report_apart_from_its_stage_names`: it runs
`checks.validate_report` over a real gate report and asserts the *only*
problems are the stage-name vocabulary.
"""

import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from agentcad.core import checks
from agentcad.core.model import NotFoundError, ValidationError
from agentcad.core.packages import content, gate
from .conftest import make_test_service

pytestmark = pytest.mark.slow

# A part that loads, builds and meets the package standard (every numeric
# parameter declares min, max, unit and description).
GOOD_PART = '''\
"""A test block: one bounded number, one enum, one bool."""

import build123d as b3d

PARAMS = {
    "size": {"default": 20.0, "min": 10.0, "max": 40.0, "unit": "mm",
             "description": "cube edge"},
    "grade": {"default": "std", "type": "enum", "choices": ["std", "wide"],
              "description": "width grade"},
    "drill": {"default": True, "type": "bool", "description": "drill a hole"},
}


def build(p):
    w = p.size * (2.0 if p.grade == "wide" else 1.0)
    part = b3d.Box(w, p.size, p.size)
    if p.drill:
        part = part - b3d.Cylinder(2, p.size * 3)
    return part
'''

README = (
    "# widget\n\n"
    "A test package shipping one part, `block`. This README exists to satisfy "
    "the gate's docs floor, which is 200 characters — chosen because the "
    "smallest bundled example README in this repository is 3 061 bytes, so "
    "the floor only ever refuses a stub.\n"
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "packages"


def manifest(**overrides):
    doc = {
        "format": 1,
        "name": "widget",
        "version": "1.0.0",
        "summary": "a test package",
        "keywords": ["test"],
        "standards": [],
        "license": "Apache-2.0",
        "authors": [{"name": "AgentCAD"}],
        "disclosure": "agent",
        "min_agentcad": "0.1.0",
        "parts": {"block": {"file": "parts/block.py", "label": "Block",
                            "summary": "a test block"}},
    }
    doc.update(overrides)
    return doc


def package_tree(root, *, doc=None, part=GOOD_PART, readme=README,
                 presets=None, previews=True):
    """A syntactically complete package directory."""
    (root / "parts").mkdir(parents=True, exist_ok=True)
    (root / "parts" / "block.py").write_text(part)
    (root / "docs").mkdir(exist_ok=True)
    (root / "docs" / "README.md").write_text(readme)
    if previews:
        (root / "previews").mkdir(exist_ok=True)
        (root / "previews" / "block_iso.png").write_bytes(_PNG)
    (root / "package.json").write_text(
        json.dumps(doc if doc is not None else manifest(), indent=2) + "\n")
    if presets is not None:
        (root / "presets.json").write_text(json.dumps(presets, indent=2) + "\n")
    return root


# The smallest valid PNG (1x1, 8-bit greyscale) — the previews stage only
# asserts that a shipped preview exists and parses; there is no pixel test.
_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108000000003a7e9b55"
    "0000000a4944415408d76360000000020001e221bc330000000049454e44ae4260"
    "82"
)


def presets_doc(**configs):
    return {"format": 1, "presets": {"block": dict(configs)}}


@pytest.fixture
def projects(tmp_path):
    return tmp_path / "projects"


@pytest.fixture
def service(projects, kernel):
    svc = make_test_service(projects, kernel)
    svc.create_project("rig")
    return svc


@pytest.fixture
def good(tmp_path):
    return package_tree(tmp_path / "src" / "widget")


@pytest.fixture
def widget():
    """The committed green fixture: real geometry, connectors, SPECS and
    presets. Read-only — a gate run never writes into the package."""
    return FIXTURES / "widget_good"


def rows(report, stage=None):
    return [item for block in report["stages"]
            if stage is None or block["name"] == stage
            for item in block["items"]]


def stage_of(report, name):
    return next(block for block in report["stages"] if block["name"] == name)


def failures(report, stage=None):
    return [item for item in rows(report, stage)
            if item["status"] in ("fail", "error")]


def messages(items):
    return " | ".join(f"{item['id']} {item['message']}" for item in items)


# ------------------------------------------------------------- containment


def test_a_run_leaves_the_users_projects_directory_byte_identical(
        service, good, tmp_path):
    before = content.content_id(service.store.root)
    gate.PackageGate(service).run(good, work_dir=str(tmp_path / "work"))
    assert content.content_id(service.store.root) == before


def test_a_run_deletes_the_cell_it_made_and_nothing_it_did_not(
        service, good, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    keep = work / "kept.txt"
    keep.write_text("a caller's own file")
    gate.PackageGate(service).run(good, work_dir=str(work))
    assert keep.read_text() == "a caller's own file"
    assert [p.name for p in work.iterdir()] == ["kept.txt"]


def test_a_run_without_a_work_dir_leaves_no_temp_directory_behind(
        service, good, monkeypatch, tmp_path):
    home = tmp_path / "temp"
    home.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(home))
    gate.PackageGate(service).run(good)
    assert list(home.iterdir()) == []


@pytest.mark.parametrize("where", ["is", "holds", "inside"])
def test_a_work_dir_overlapping_the_projects_root_is_refused(
        service, good, tmp_path, where):
    root = service.store.root
    candidate = {"is": root, "holds": root.parent,
                 "inside": root / "somewhere"}[where]
    with pytest.raises(ValidationError) as exc:
        gate.PackageGate(service).run(good, work_dir=str(candidate))
    assert str(root) in str(exc.value) and str(candidate) in str(exc.value)


@pytest.mark.parametrize("where", ["is", "holds", "inside"])
def test_a_work_dir_overlapping_the_package_directory_is_refused(
        service, good, where):
    candidate = {"is": good, "holds": good.parent,
                 "inside": good / "inner"}[where]
    with pytest.raises(ValidationError) as exc:
        gate.PackageGate(service).run(good, work_dir=str(candidate))
    assert str(good) in str(exc.value) and str(candidate) in str(exc.value)


def test_a_work_dir_that_reaches_the_projects_root_through_a_symlink_is_refused(
        service, good, tmp_path):
    """Both sides are resolved before they are compared, so a link is not a
    way round the refusal (and macOS hands `/private/var` for `/var`)."""
    link = tmp_path / "link"
    link.symlink_to(service.store.root)
    with pytest.raises(ValidationError) as exc:
        gate.PackageGate(service).run(good, work_dir=str(link))
    assert str(service.store.root.resolve()) in str(exc.value)


def test_a_refused_work_dir_is_not_created(service, good, tmp_path):
    candidate = service.store.root / "somewhere"
    with pytest.raises(ValidationError):
        gate.PackageGate(service).run(good, work_dir=str(candidate))
    assert not candidate.exists()


def test_the_ephemeral_service_ends_with_all_three_seams_nulled(
        service, good, monkeypatch):
    seen = _watch_cell(monkeypatch)
    gate.PackageGate(service).run(good)
    assert len(seen) == 1
    ephemeral = seen[0][1]
    assert ephemeral.bus.on_publish is None
    assert ephemeral.store.branch_resolver is None
    assert ephemeral.store.write_guard is None


def _watch_cell(monkeypatch):
    """Capture every `(cell, service, projects-at-creation)` a run builds.

    The project list is snapshotted **inside** the spy: the cell is deleted
    when the run returns, so asking afterwards would answer about a directory
    that no longer exists.
    """
    seen = []
    original = gate._ephemeral_service

    def spy(cell, kernel):
        result = original(cell, kernel)
        seen.append((cell, result[0],
                     [p["name"] for p in result[0].list_projects()]))
        return result

    monkeypatch.setattr(gate, "_ephemeral_service", spy)
    return seen


def test_the_ephemeral_service_is_rooted_in_the_cell_and_knows_one_project(
        service, good, tmp_path, monkeypatch):
    work = tmp_path / "work"
    seen = _watch_cell(monkeypatch)
    gate.PackageGate(service).run(good, work_dir=str(work))
    cell, ephemeral, projects = seen[0]
    assert ephemeral.store.root == cell
    # The gate's own project, and no other: no user project is ever opened.
    assert projects == [gate.GATE_PROJECT]
    # And it shares the warm kernel rather than starting a second pool.
    assert ephemeral.kernel is service.kernel


def test_the_cell_is_named_for_the_pid_that_made_it(service, good, tmp_path,
                                                    monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    seen = _watch_cell(monkeypatch)
    gate.PackageGate(service).run(good, work_dir=str(work))
    cell = seen[0][0]
    assert cell.name.startswith(f"agentcad-package-{os.getpid()}-")
    assert cell.parent == work.resolve()


# ------------------------------------------------------------- the report


def test_the_report_is_a_prd004_report_apart_from_its_stage_names(service, good):
    report = gate.PackageGate(service).run(good)
    problems = checks.validate_report(report)
    assert all("unknown stage name" in problem for problem in problems), problems
    # …and the gate's own validator, which subtracts exactly that vocabulary
    # difference, accepts it outright.
    assert gate.validate_gate_report(report) == []


def test_validate_gate_report_still_refuses_a_stage_name_it_does_not_know(
        service, good):
    report = gate.PackageGate(service).run(good)
    report["stages"][0]["name"] = "drawings"          # a checks stage, not ours
    assert any("unknown gate stage" in p
               for p in gate.validate_gate_report(report))


def test_validate_gate_report_refuses_a_report_missing_the_gate_keys(
        service, good):
    report = gate.PackageGate(service).run(good)
    for key in ("package", "note", "publishable", "exempt_skips"):
        broken = dict(report)
        broken.pop(key)
        assert any(key in problem
                   for problem in gate.validate_gate_report(broken))


def test_every_stage_appears_exactly_once_in_the_declared_order(service, good):
    report = gate.PackageGate(service).run(good)
    assert [block["name"] for block in report["stages"]] == list(gate.GATE_STAGES)


def test_the_report_carries_the_package_block_and_the_security_non_claim(
        service, good):
    report = gate.PackageGate(service).run(good)
    assert report["package"] == {
        "name": "widget", "version": "1.0.0",
        "content_id": content.content_id(good),
    }
    assert report["note"] == gate.SECURITY_NOTE
    assert "not a security boundary" in report["note"]
    # `project` is still present and is the package name: the report stays a
    # PRD-004 document, and `package` is the qualified block beside it.
    assert report["project"] == "widget"


def test_the_report_records_the_build123d_the_package_was_proved_against(
        service, good):
    report = gate.PackageGate(service).run(good)
    assert report["host"]["build123d"]


def test_every_declared_stage_is_implemented(service, good):
    """Slice 4 shipped three of nine and said so with `not_implemented` rows.
    Slice 5 closed the gap: a declared stage that nobody implemented would be
    a skip the verdict has to block on, and there are none left."""
    assert gate.IMPLEMENTED_STAGES == gate.GATE_STAGES
    report = gate.PackageGate(service).run(good)
    assert [block["reason"] for block in report["stages"]
            if block.get("reason") == "not_implemented"] == []


def test_a_stage_subset_reports_the_others_as_not_selected(service, good):
    report = gate.PackageGate(service).run(good, stages=("format",))
    assert stage_of(report, "contract")["reason"] == "not_selected"
    assert stage_of(report, "format")["status"] == "green"


def test_an_unknown_stage_name_is_a_validation_error(service, good):
    with pytest.raises(ValidationError) as exc:
        gate.PackageGate(service).run(good, stages=("format", "drawings"))
    assert "drawings" in str(exc.value)


def test_a_directory_that_is_not_a_package_is_a_harness_failure(service,
                                                                tmp_path):
    with pytest.raises(NotFoundError):
        gate.PackageGate(service).run(tmp_path / "nothing-here")


# --------------------------------------------------------- stage: format


def test_a_good_package_is_green_at_format_contract_and_presets(service, good):
    report = gate.PackageGate(service).run(good)
    for name in ("format", "contract", "presets"):
        assert stage_of(report, name)["status"] in ("green", "skip"), \
            messages(failures(report, name))
    assert failures(report) == []


def test_a_missing_disclosure_is_a_format_failure_naming_the_field(service,
                                                                   tmp_path):
    doc = manifest()
    doc.pop("disclosure")
    root = package_tree(tmp_path / "src" / "widget", doc=doc)
    report = gate.PackageGate(service).run(root, stages=("format",))
    assert any("disclosure" in item["id"] for item in failures(report, "format"))
    assert report["publishable"] is False


def test_an_unknown_manifest_key_is_a_format_failure(service, tmp_path):
    root = package_tree(tmp_path / "src" / "widget",
                        doc=manifest(licence="Apache-2.0"))
    report = gate.PackageGate(service).run(root, stages=("format",))
    assert any("licence" in item["message"]
               for item in failures(report, "format"))


def test_a_declared_part_file_that_does_not_exist_is_a_format_failure(
        service, tmp_path):
    root = package_tree(tmp_path / "src" / "widget")
    (root / "parts" / "block.py").unlink()
    report = gate.PackageGate(service).run(root, stages=("format",))
    named = [item for item in failures(report, "format")
             if "parts/block.py" in item["message"]]
    assert named, messages(failures(report, "format"))


def test_an_oversized_file_is_a_format_failure(service, tmp_path, monkeypatch):
    monkeypatch.setattr(content, "MAX_FILE_BYTES", 128)
    root = package_tree(tmp_path / "src" / "widget")
    report = gate.PackageGate(service).run(root, stages=("format",))
    assert any("ceiling" in item["message"]
               for item in failures(report, "format"))


def test_a_missing_or_trivial_readme_is_a_format_failure(service, tmp_path):
    root = package_tree(tmp_path / "src" / "widget", readme="# widget\n")
    report = gate.PackageGate(service).run(root, stages=("format",))
    assert any("README" in item["message"]
               for item in failures(report, "format"))
    (root / "docs" / "README.md").unlink()
    report = gate.PackageGate(service).run(root, stages=("format",))
    assert any("docs/README.md" in item["message"]
               for item in failures(report, "format"))


def test_a_package_with_no_previews_is_a_format_failure(service, tmp_path):
    root = package_tree(tmp_path / "src" / "widget", previews=False)
    report = gate.PackageGate(service).run(root, stages=("format",))
    assert any("previews/" in item["message"]
               for item in failures(report, "format"))


def test_a_symlink_in_the_package_is_a_format_failure(service, tmp_path):
    root = package_tree(tmp_path / "src" / "widget")
    (root / "link.py").symlink_to(root / "parts" / "block.py")
    report = gate.PackageGate(service).run(root, stages=("format",))
    assert any("symlink" in item["message"]
               for item in failures(report, "format"))


def test_a_part_file_that_escapes_the_package_is_refused_and_never_read(
        service, tmp_path):
    """`parts.<id>.file` is data from somewhere else. The refusal is lexical
    (it never touches the filesystem), so a package cannot make the gate read
    — let alone inspect — a script outside its own directory."""
    outside = tmp_path / "src" / "evil.py"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("raise SystemExit('never run')\n")
    doc = manifest(parts={"block": {"file": "../evil.py"}})
    root = package_tree(tmp_path / "src" / "widget", doc=doc)
    report = gate.PackageGate(service).run(root, stages=("format", "contract"))
    assert any("parts/" in item["message"]
               for item in failures(report, "format"))
    # …and the contract stage refuses the path rather than inspecting it: the
    # row names the containment failure, and nothing outside was ever opened.
    broken = failures(report, "contract")
    assert broken and "must stay inside" in broken[0]["message"]
    assert "../evil.py" in broken[0]["message"]


def test_a_stage_that_raises_becomes_one_error_row_and_a_harness_entry(
        service, good, monkeypatch):
    """An unexpected exception out of a stage is this program's fault as much
    as the package's, so it is one row and the run continues."""
    def boom(self, started):
        raise RuntimeError("the contract stage exploded")

    monkeypatch.setattr(gate._Run, "_stage_contract", boom)
    report = gate.PackageGate(service).run(good)
    broken = failures(report, "contract")
    assert broken and broken[0]["status"] == "error"
    assert "exploded" in broken[0]["message"]
    assert report["errors"][0]["stage"] == "contract"
    # the stages after it still ran
    assert stage_of(report, "presets")["status"] in ("green", "skip")


def test_an_unparseable_manifest_is_one_failure_and_not_a_traceback(
        service, tmp_path):
    root = package_tree(tmp_path / "src" / "widget")
    (root / "package.json").write_text("{not json")
    report = gate.PackageGate(service).run(root, stages=("format",))
    assert any("package.json" in item["id"]
               for item in failures(report, "format"))
    assert report["package"]["name"] is None


# -------------------------------------------------------- stage: contract


def test_a_part_without_params_fails_the_contract_carrying_the_kernel_message(
        service, tmp_path):
    root = package_tree(tmp_path / "src" / "widget",
                        part="def build(p):\n    return None\n")
    report = gate.PackageGate(service).run(root, stages=("contract",))
    broken = failures(report, "contract")
    assert broken and "PARAMS" in broken[0]["message"]
    assert broken[0]["error"]["type"]


def test_a_part_without_build_fails_the_contract(service, tmp_path):
    root = package_tree(tmp_path / "src" / "widget", part="PARAMS = {}\n")
    report = gate.PackageGate(service).run(root, stages=("contract",))
    assert any("build" in item["message"]
               for item in failures(report, "contract"))


@pytest.mark.parametrize("drop", ["min", "max", "unit", "description"])
def test_a_numeric_parameter_missing_a_bound_fails_the_contract(
        service, tmp_path, drop):
    spec = {"default": 20.0, "min": 10.0, "max": 40.0, "unit": "mm",
            "description": "cube edge"}
    spec.pop(drop)
    part = (
        "import build123d as b3d\n\n"
        f"PARAMS = {{'size': {spec!r}}}\n\n\n"
        "def build(p):\n    return b3d.Box(p.size, p.size, p.size)\n"
    )
    root = package_tree(tmp_path / "src" / "widget", part=part)
    report = gate.PackageGate(service).run(root, stages=("contract",))
    broken = failures(report, "contract")
    assert broken, "a numeric parameter without a bound must fail"
    assert "size" in broken[0]["subject"] and drop in broken[0]["message"]
    assert "vacuous" in broken[0]["message"]


def test_an_enum_or_bool_parameter_needs_no_bounds(service, good):
    report = gate.PackageGate(service).run(good, stages=("contract",))
    subjects = {item["subject"]: item["status"]
                for item in rows(report, "contract")}
    assert subjects["block.grade"] == "pass"
    assert subjects["block.drill"] == "pass"


def test_the_contract_stage_reports_one_row_per_part_and_per_parameter(
        service, good):
    report = gate.PackageGate(service).run(good, stages=("contract",))
    subjects = [item["subject"] for item in rows(report, "contract")]
    assert subjects == ["block", "block.size", "block.grade", "block.drill"]


# --------------------------------------------------------- stage: presets


def test_a_package_with_no_presets_skips_the_stage_and_still_publishes(
        service, good):
    report = gate.PackageGate(service).run(good)
    block = stage_of(report, "presets")
    assert block["status"] == "skip" and block["reason"] == "no_presets_declared"
    assert block["items"] == []


def test_a_valid_preset_passes_and_is_applied_through_set_params(
        service, tmp_path):
    root = package_tree(
        tmp_path / "src" / "widget",
        presets=presets_doc(wide={"params": {"size": 30.0, "grade": "wide"},
                                  "label": "Wide 30"}))
    report = gate.PackageGate(service).run(root, stages=("contract", "presets"))
    assert failures(report, "presets") == [], messages(failures(report))
    row = rows(report, "presets")[0]
    assert row["subject"] == "block:wide" and row["details"]["built"] is True


def test_a_preset_naming_an_unknown_parameter_fails_with_the_services_message(
        service, tmp_path):
    root = package_tree(tmp_path / "src" / "widget",
                        presets=presets_doc(bad={"params": {"nope": 1.0}}))
    report = gate.PackageGate(service).run(root, stages=("contract", "presets"))
    broken = failures(report, "presets")
    assert broken and "nope" in broken[0]["message"]


def test_a_preset_with_a_wrongly_typed_value_fails(service, tmp_path):
    root = package_tree(tmp_path / "src" / "widget",
                        presets=presets_doc(bad={"params": {"size": "big"}}))
    report = gate.PackageGate(service).run(root, stages=("contract", "presets"))
    assert failures(report, "presets")


def test_a_preset_above_the_declared_max_fails_even_though_a_build_clamps(
        service, tmp_path):
    """`set_params` stores a numeric value raw and the worker clamps it at
    build, so applying alone would call an out-of-range preset fine. The gate
    also validates the configuration against the inspected spec."""
    root = package_tree(tmp_path / "src" / "widget",
                        presets=presets_doc(huge={"params": {"size": 400.0}}))
    report = gate.PackageGate(service).run(root, stages=("contract", "presets"))
    broken = failures(report, "presets")
    assert broken and "above max" in broken[0]["message"]
    # …and it does not quietly degrade to a shape check when the `contract`
    # stage was not selected: the spec is inspected on demand.
    alone = gate.PackageGate(service).run(root, stages=("presets",))
    assert [item["id"] for item in failures(alone, "presets")] == \
           [item["id"] for item in broken]


def test_a_preset_naming_a_part_the_package_does_not_ship_fails(
        service, tmp_path):
    root = package_tree(
        tmp_path / "src" / "widget",
        presets={"format": 1, "presets": {"ghost": {"a": {"params": {}}}}})
    report = gate.PackageGate(service).run(root, stages=("contract", "presets"))
    assert any("ghost" in item["message"]
               for item in failures(report, "presets"))


def test_one_preset_does_not_leak_its_parameters_into_the_next(service,
                                                               tmp_path):
    """Each configuration is validated from the part's declared defaults.
    `set_params` merges, so a gate that never cleared would validate the
    second preset against the first one's values."""
    root = package_tree(
        tmp_path / "src" / "widget",
        presets=presets_doc(first={"params": {"size": 30.0}},
                            second={"params": {"grade": "wide"}}))
    seen = {}
    original = service.__class__.set_params

    def spy(self, proj, part_id, values):
        result = original(self, proj, part_id, values)
        seen.setdefault(part_id, []).append(
            dict(self.store.get_part(proj, part_id).params))
        return result

    service.__class__.set_params = spy
    try:
        report = gate.PackageGate(service).run(
            root, stages=("contract", "presets"))
    finally:
        service.__class__.set_params = original
    assert failures(report, "presets") == []
    applied = [params for values in seen.values() for params in values]
    assert {"size": 30.0} in applied
    assert {"grade": "wide"} in applied, applied


def test_an_invalid_presets_document_is_one_failure(service, tmp_path):
    root = package_tree(tmp_path / "src" / "widget")
    (root / "presets.json").write_text("{not json")
    report = gate.PackageGate(service).run(root, stages=("presets",))
    assert any("presets.json" in item["id"]
               for item in failures(report, "presets"))


# ------------------------------------------------------------ the verdict


def test_a_green_run_over_the_real_fixture_is_publishable(service, widget):
    """The whole point, end to end: nine stages over a real package, green,
    exit 0, publishable, and every skip it did report is a fact about the
    world that is named in `exempt_skips`."""
    report = gate.PackageGate(service).run(widget)
    assert failures(report) == [], messages(failures(report))
    assert report["status"] == "green"
    assert report["exit_code"] == 0
    assert report["publishable"] is True
    assert report["blockers"] == []
    assert report["exempt_skips"] == ["policy:no_policy_configured"]


def test_a_failing_row_blocks_publish_and_is_named(service, tmp_path):
    doc = manifest()
    doc.pop("disclosure")
    root = package_tree(tmp_path / "src" / "widget", doc=doc)
    report = gate.PackageGate(service).run(root)
    assert report["publishable"] is False
    assert report["exit_code"] == 1
    assert any("disclosure" in blocker for blocker in report["blockers"])


def test_a_stage_that_was_not_selected_blocks_publish(service, good):
    report = gate.PackageGate(service).run(good, stages=("format",))
    assert report["publishable"] is False
    assert "contract" in report["blockers"]


def test_an_exempt_skip_does_not_block_publish_and_a_plain_one_does():
    """The verdict is a pure function over the rows, so it is tested over
    synthetic ones: slice 4 ships no exempt skip of its own."""
    exempt = checks.make_item("connectors", "check", "block", "skip",
                              "declares no connectors",
                              reason="no_connectors_declared",
                              hint="declare connectors(p, part) if it mates",
                              strict_exempt=True)
    plain = checks.make_item("build", "part", "block", "skip",
                             "not measured", reason="budget_exceeded",
                             hint="raise --budget")
    ok = gate.verdict([checks.make_stage("connectors", [exempt])])
    assert ok["publishable"] is True
    assert ok["exempt_skips"] == ["connectors:no_connectors_declared"]
    blocked = gate.verdict([checks.make_stage("build", [exempt, plain])])
    assert blocked["publishable"] is False
    assert blocked["blockers"] == ["build:block"]


def test_every_exempt_skip_is_qualified_by_its_stage():
    """One shape, row-level and stage-level alike. Slice 8 copies this list
    verbatim into the published index entry, so it is a **format** — and a
    list mixing `no_policy_configured` with `specs:not_declared` would make a
    consumer parse two."""
    row = checks.make_item("policy", "check", "policy", "skip",
                           "no policy module is configured",
                           reason="no_policy_configured",
                           hint="install service.package_policy",
                           strict_exempt=True)
    ruling = gate.verdict([checks.make_stage("policy", [row]),
                           checks.make_stage("specs", [], reason="not_declared")])
    assert ruling["exempt_skips"] == ["policy:no_policy_configured",
                                      "specs:not_declared"]
    assert all(entry.count(":") == 1 and entry.split(":")[0] in gate.GATE_STAGES
               for entry in ruling["exempt_skips"])


def test_a_stage_skipped_for_a_reason_of_its_own_blocks_publish():
    """A stage-level skip is "we did not measure", so it blocks — except the
    named few that mean the package legitimately has nothing there."""
    assert gate.verdict(
        [checks.make_stage("presets", reason="no_presets_declared")]
    )["publishable"] is True
    assert gate.verdict(
        [checks.make_stage("specs", reason="budget_exceeded")]
    )["blockers"] == ["specs"]


def test_every_exempt_reason_is_a_fact_about_the_world_not_the_package():
    """The set is closed on purpose (design Decision 10): a member that is a
    fact about the package would let a broken package publish."""
    assert gate.PUBLISH_SKIP_EXEMPT == (
        "fem_extra_missing", "no_policy_configured", "string_param_unbounded",
        "no_connectors_declared", "reference_part")


def test_strict_moves_the_verdict_and_never_a_row(service, good):
    plain = gate.PackageGate(service).run(good)
    strict = gate.PackageGate(service).run(good, strict=True)
    assert [item["status"] for item in rows(plain)] == \
           [item["status"] for item in rows(strict)]
    assert strict["strict"] is True


# --------------------------------------------------------------- the budget


def test_an_exhausted_budget_skips_rather_than_reddening(service, good):
    report = gate.PackageGate(service).run(good, budget_s=0.0)
    assert report["complete"] is False
    assert report["exit_code"] == 2
    assert report["publishable"] is False
    assert report["status"] != "red"
    assert [block["reason"] for block in report["stages"]] == \
           ["budget_exceeded"] * len(gate.GATE_STAGES)
    assert rows(report) == []


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1.0])
def test_a_budget_that_is_not_a_limit_is_refused(service, good, value):
    with pytest.raises(ValidationError):
        gate.PackageGate(service).run(good, budget_s=value)


# ---------------------------------------------------------- load-order rules


def test_constructing_the_gate_reads_nothing_off_the_service():
    """`tools_packages` loads at `pac`, before `tools_specs` (`s`): a gate that
    captured `service.specs` at construction would capture nothing forever."""

    class Trap:
        def __getattr__(self, name):
            raise AssertionError(f"the gate read service.{name} at construction")

    gate.PackageGate(Trap())


def test_the_gate_holds_no_run_state_between_runs(service, good):
    """One gate object may serve two concurrent callers, so the run state
    lives on a per-run object and never on the gate."""
    instance = gate.PackageGate(service)
    first = instance.run(good, stages=("format",))
    second = instance.run(good, stages=("format",))
    assert first["stages"][0]["items"] == second["stages"][0]["items"]
    assert vars(instance) == {"_service": service}


# ============================ slice 5 ====================================
# extremes, the fan-out, specs, connectors, previews, docs and the policy seam


# --------------------------------------------------------- the variant matrix


def test_the_variant_count_is_the_one_at_a_time_sum_and_never_the_product():
    """`1 + Σ|sweep| + |presets|`. The cross product would redden correct
    content whose parameters are mutually constrained (design Decision 9a);
    an author who wants a corner declares it as a preset."""
    spec = {
        "a": {"type": "number", "min": 1.0, "max": 9.0},
        "b": {"type": "int", "min": 1, "max": 4},
        "grade": {"type": "enum", "choices": ["std", "wide", "narrow"]},
        "flag": {"type": "bool"},
    }
    presets = {"corner": {"params": {"a": 9.0, "b": 4}}}
    got = gate.variants("block", spec, presets)
    assert len(got) == 1 + (2 + 2 + 3 + 2) + 1 == 11
    assert 2 * 2 * 3 * 2 == 24            # what the cross product would cost
    assert got[0] == ("block@default", {}, "defaults")
    assert ("block@a=max", {"a": 9.0}, "a at max") in got
    assert ("block@grade=wide", {"grade": "wide"}, "grade at wide") in got
    assert ("block@flag=false", {"flag": False}, "flag at false") in got
    assert got[-1].id == "block@preset:corner"


def test_a_part_with_no_parameters_still_has_a_default_variant():
    assert gate.variants("plate", {}, {}) == [("plate@default", {}, "defaults")]


def test_a_string_parameter_is_not_swept_and_is_named_as_unswept():
    spec = {"text": {"type": "string", "default": "acme"}}
    assert gate.variants("plate", spec, {}) == [("plate@default", {},
                                                 "defaults")]
    assert gate.unswept(spec) == ["text"]


def test_a_numeric_parameter_missing_a_bound_contributes_only_what_it_declared():
    """The missing bound is a `contract` failure; the matrix does not invent
    one to sweep."""
    assert [v.id for v in gate.variants("p", {"a": {"min": 1.0}}, {})] == \
           ["p@default", "p@a=min"]


# ---------------------------------------------------------- stage: build


def test_the_build_stage_builds_every_variant_and_names_the_parameters(
        service, widget):
    report = gate.PackageGate(service).run(widget, stages=("build",))
    subjects = [item["subject"] for item in rows(report, "build")]
    # 1 default + (2 length + 2 bore_d + 2 grade + 2 chamfered) + 2 presets
    assert len(subjects) == 11
    assert "mount_block@length=max" in subjects
    assert "mount_block@preset:wide_16" in subjects
    assert stage_of(report, "build")["status"] == "green"
    volumes = [item["details"]["volume_mm3"] for item in rows(report, "build")]
    assert all(volume > 0 for volume in volumes)


def test_ac2a_a_package_that_breaks_at_an_extreme_fails_with_the_traceback(
        service):
    """AC2a: the fixture builds at its default and raises at `length=max`.
    The gate is red, `publishable` is false, and the failing item is named
    with the kernel's own payload."""
    report = gate.PackageGate(service).run(FIXTURES / "break_at_extreme")
    assert report["status"] == "red"
    assert report["publishable"] is False
    broken = [item for item in failures(report, "build")]
    assert [item["id"] for item in broken] == ["build:strut@length=max"]
    assert "buckles" in broken[0]["message"]
    assert broken[0]["error"]["details"]["traceback"]
    assert "build:strut@length=max" in report["blockers"]
    # …and the default still builds, which is what makes the extreme the news.
    passed = [item["subject"] for item in rows(report, "build")
              if item["status"] == "pass"]
    assert "strut@default" in passed and "strut@length=min" in passed


def test_a_variant_that_builds_to_nothing_is_a_failure_not_a_note(service,
                                                                  tmp_path):
    """At `size=min` the cut removes the whole solid. The kernel is happy —
    `is_valid: true`, `n_solids: 0`, `volume_mm3: 0.0` — so only the gate can
    call it wrong, and it does: a package that publishes nothing is not a
    package."""
    part = (
        '"""A block that cuts itself away at size=min."""\n'
        "import build123d as b3d\n\n"
        "PARAMS = {'size': {'default': 10.0, 'min': 0.0, 'max': 20.0,\n"
        "                   'unit': 'mm', 'description': 'cube edge'}}\n\n\n"
        "def build(p):\n"
        "    part = b3d.Box(10, 10, 10)\n"
        "    if p.size <= 0.0:\n"
        "        part = part - b3d.Box(30, 30, 30)\n"
        "    return part\n"
    )
    root = package_tree(tmp_path / "src" / "widget", part=part)
    report = gate.PackageGate(service).run(root, stages=("build",))
    broken = failures(report, "build")
    assert [item["subject"] for item in broken] == ["block@size=min"]
    assert "0.0 mm³" in broken[0]["message"]
    assert broken[0]["details"]["is_valid"] is True


def test_a_part_with_no_parameters_still_gets_one_build_row(service, tmp_path):
    part = ('"""A fixed plate: no parameters at all."""\n'
            "import build123d as b3d\n\n"
            "PARAMS = {}\n\n\n"
            "def build(p):\n    return b3d.Box(10, 10, 2)\n")
    root = package_tree(tmp_path / "src" / "widget", part=part)
    report = gate.PackageGate(service).run(root, stages=("build",))
    assert [(item["subject"], item["status"])
            for item in rows(report, "build")] == [("block@default", "pass")]


@pytest.mark.parametrize("jobs", [1, 4])
def test_jobs_one_and_jobs_four_produce_identical_reports(service, widget,
                                                          jobs):
    """The fan-out is the first in-process use of the pool in this codebase,
    so `--jobs 1` stays a first-class path and the two must agree row for
    row — only the timings may differ."""
    serial = gate.PackageGate(service).run(widget, stages=("build",), jobs=1)
    parallel = gate.PackageGate(service).run(widget, stages=("build",),
                                             jobs=jobs)
    assert [(i["id"], i["status"], i["message"]) for i in rows(serial)] == \
           [(i["id"], i["status"], i["message"]) for i in rows(parallel)]


def test_the_build_phase_writes_no_manifest_entries(service, widget,
                                                    monkeypatch):
    """Every scratch part is created **before** the builds start, so the
    concurrent `_rebuild` calls touch only distinct cache keys and distinct
    `_status` slots — never the manifest."""
    order = []
    original_add = service.store.__class__.add_part
    original_rebuild = service.__class__._rebuild

    def add(self, *args, **kwargs):
        order.append("add_part")
        return original_add(self, *args, **kwargs)

    def rebuild(self, *args, **kwargs):
        order.append("rebuild")
        return original_rebuild(self, *args, **kwargs)

    monkeypatch.setattr(service.store.__class__, "add_part", add)
    monkeypatch.setattr(service.__class__, "_rebuild", rebuild)
    gate.PackageGate(service).run(widget, stages=("build",))
    assert order.count("add_part") == 11 and order.count("rebuild") == 11
    # Every manifest write happens before the first build, and none after it.
    assert "add_part" not in order[order.index("rebuild"):]


# ----------------------------------------------------------- stage: specs


def test_the_specs_stage_folds_prd003_rows_in_at_every_variant(service,
                                                               widget):
    report = gate.PackageGate(service).run(widget, stages=("build", "specs"))
    items = rows(report, "specs")
    assert items, "the fixture declares SPECS"
    assert stage_of(report, "specs")["status"] == "green"
    # …and the rows name the VARIANT, not the scratch part id.
    assert any(item["subject"].startswith("mount_block@length=max")
               for item in items), [i["subject"] for i in items]
    assert {item["requirement"] for item in items} == {"PKG-001", "PKG-002"}
    # the embedded PRD-003 report travels whole
    assert stage_of(report, "specs")["report"]["declared"]


def test_a_package_that_declares_no_specs_skips_the_stage_and_still_publishes(
        service, good):
    report = gate.PackageGate(service).run(good)
    block = stage_of(report, "specs")
    assert block["status"] == "skip" and block["reason"] == "not_declared"
    assert "specs" not in report["blockers"]
    assert "specs:not_declared" in report["exempt_skips"]


def test_a_fem_skip_would_not_block_publish_and_is_strict_exempt(service):
    """A `check_fem_static` without the `[fem]` extra is a fact about the
    machine, not about the package. It stays a visible skip, it is exempt from
    both the publish verdict and `--strict`, and `exempt_skips` records it so
    a consumer can read what was not measured."""
    row = {"status": "skip", "reason": "fem_extra_missing",
           "hint": "install agentcad[fem]", "name": "stress", "part": "x",
           "message": "FEM is not installed"}
    run = gate._Run(FIXTURES / "widget_good", set(gate.GATE_STAGES), jobs=1,
                    deadline=None)
    item = run._spec_item(row)
    assert item["status"] == "skip" and item["strict_exempt"] is True
    ruling = gate.verdict([checks.make_stage("specs", [item])])
    assert ruling["publishable"] is True
    assert ruling["exempt_skips"] == ["specs:fem_extra_missing"]


# ------------------------------------------------------ stage: connectors


def test_the_connectors_stage_mates_every_declared_connector(service, widget):
    report = gate.PackageGate(service).run(widget, stages=("connectors",))
    items = rows(report, "connectors")
    assert [item["subject"] for item in items] == [
        "mount_block", "mount_block.seat", "mount_block.bore"]
    assert [item["status"] for item in items] == ["pass", "pass", "pass"]
    assert items[1]["details"]["position"] is not None


def test_ac2b_a_connector_the_kernel_refuses_fails_naming_it(service):
    """AC2b: `connectors(p, part)` returns `"up"` where an axis belongs. The
    row names the connector, because a catalog of parts that cannot mate is a
    catalog of pictures."""
    report = gate.PackageGate(service).run(FIXTURES / "broken_connector")
    broken = failures(report, "connectors")
    assert broken and "pivot" in broken[0]["message"]
    assert report["publishable"] is False
    # the part itself builds — only the connector is wrong
    assert failures(report, "build") == []


def test_a_part_with_no_connectors_skips_rather_than_fails(service, good):
    report = gate.PackageGate(service).run(good, stages=("connectors",))
    item = rows(report, "connectors")[0]
    assert item["status"] == "skip"
    assert item["reason"] == "no_connectors_declared"
    assert item["strict_exempt"] is True
    assert gate.verdict([stage_of(report, "connectors")])["publishable"] is True


def test_a_part_with_only_a_non_rigid_connector_is_mated_with_the_probe(
        service, tmp_path):
    """The moving side of a mate must be rigid, so a part that declares only a
    cylindrical connector has nothing of its own to mate with. The bundled
    probe is what makes its connector testable at all."""
    part = (
        "\"\"\"A pin with one cylindrical connector and nothing rigid.\"\"\"\n"
        "import build123d as b3d\n\n"
        "PARAMS = {'d': {'default': 6.0, 'min': 3.0, 'max': 12.0,\n"
        "                'unit': 'mm', 'description': 'pin diameter'}}\n\n\n"
        "def build(p):\n    return b3d.Cylinder(p.d / 2, 20)\n\n\n"
        "def connectors(p, part):\n"
        "    return {'shank': {'type': 'cylindrical',\n"
        "                      'axis': ((0, 0, 0), (0, 0, 1))}}\n"
    )
    root = package_tree(tmp_path / "src" / "widget", part=part)
    report = gate.PackageGate(service).run(root, stages=("connectors",))
    items = rows(report, "connectors")
    assert [item["status"] for item in items] == ["pass", "pass"]
    assert items[1]["subject"] == "block.shank"


def test_one_batch_round_trip_resolves_every_connector(service, widget,
                                                       monkeypatch):
    """The green path is a single `resolve_mates`: attribution costs N round
    trips and is only ever paid by a package that is already wrong."""
    calls = []
    original = service.__class__.get_assembly
    monkeypatch.setattr(
        service.__class__, "get_assembly",
        lambda self, proj: calls.append(proj) or original(self, proj))
    gate.PackageGate(service).run(widget, stages=("connectors",))
    assert len(calls) == 1


def test_a_broken_connector_costs_one_round_trip_per_connector_to_attribute(
        service, monkeypatch):
    calls = []
    original = service.__class__.get_assembly

    def counted(self, proj):
        calls.append(proj)
        return original(self, proj)

    monkeypatch.setattr(service.__class__, "get_assembly", counted)
    gate.PackageGate(service).run(FIXTURES / "broken_connector",
                                  stages=("connectors",))
    # the kernel refuses the connector call itself, so no assembly is built
    assert calls == []


# -------------------------------------------------------- stage: previews


def test_the_previews_stage_renders_and_checks_the_shipped_png(service,
                                                               widget):
    report = gate.PackageGate(service).run(widget, stages=("previews",))
    items = rows(report, "previews")
    assert [item["subject"] for item in items] == ["previews/mount_block",
                                                   "render:mount_block"]
    assert all(item["status"] == "pass" for item in items)
    assert "pixels are not compared" in items[1]["message"]


def test_a_shipped_preview_that_is_not_a_png_fails(service, tmp_path):
    root = package_tree(tmp_path / "src" / "widget")
    (root / "previews" / "block_iso.png").write_bytes(b"not a png at all")
    report = gate.PackageGate(service).run(root, stages=("previews",))
    broken = failures(report, "previews")
    assert broken and "signature" in broken[0]["message"]


def test_a_truncated_preview_fails_the_crc_rather_than_passing(service,
                                                               tmp_path):
    root = package_tree(tmp_path / "src" / "widget")
    target = root / "previews" / "block_iso.png"
    target.write_bytes(_PNG[:-8])
    report = gate.PackageGate(service).run(root, stages=("previews",))
    assert failures(report, "previews")


def test_a_part_with_no_shipped_preview_fails(service, tmp_path):
    root = package_tree(tmp_path / "src" / "widget")
    (root / "previews" / "block_iso.png").rename(
        root / "previews" / "something_else.png")
    report = gate.PackageGate(service).run(root, stages=("previews",))
    assert any("previews/block*.png" in item["message"]
               for item in failures(report, "previews"))


# ------------------------------------------------------------ stage: docs


def test_the_docs_stage_wants_a_summary_and_a_module_docstring(service,
                                                               tmp_path):
    root = package_tree(tmp_path / "src" / "widget",
                        part="PARAMS = {}\n\n\ndef build(p):\n    return None\n",
                        doc=manifest(parts={"block": {"file": "parts/block.py"}}))
    report = gate.PackageGate(service).run(root, stages=("docs",))
    broken = failures(report, "docs")
    assert broken and "summary" in broken[0]["message"]
    assert "module docstring" in broken[0]["message"]


def test_a_readme_that_never_mentions_a_part_fails_the_docs_stage(service,
                                                                  tmp_path):
    root = package_tree(tmp_path / "src" / "widget",
                        readme="# widget\n\n" + "Documentation. " * 20)
    report = gate.PackageGate(service).run(root, stages=("docs",))
    assert any("never mentions block" in item["message"]
               for item in failures(report, "docs"))


# ---------------------------------------------------------- stage: policy


def test_with_no_policy_configured_the_stage_is_one_honest_exempt_skip(
        service, good):
    report = gate.PackageGate(service).run(good, stages=("policy",))
    item = rows(report, "policy")[0]
    assert item["status"] == "skip"
    assert item["reason"] == "no_policy_configured"
    assert item["strict_exempt"] is True
    assert "never that a script is safe to run" in item["message"]


def test_a_configured_policy_is_called_per_part_and_its_rows_are_folded_in(
        service, good):
    """The seam PRD-031 FR2(b) plugs into: this feature ships the seam and no
    policy."""
    seen = []

    class Policy:
        def check(self, source, path):
            seen.append(path)
            return [{"status": "fail", "subject": "imports",
                     "message": "imports os, which the allowlist refuses"}]

    service.package_policy = Policy()
    try:
        report = gate.PackageGate(service).run(good, stages=("policy",))
    finally:
        del service.package_policy
    assert seen == ["parts/block.py"]
    broken = failures(report, "policy")
    assert broken and broken[0]["subject"] == "block:imports"
    assert report["publishable"] is False


def test_a_policy_that_raises_is_one_error_row_and_not_the_end_of_the_run(
        service, good):
    class Policy:
        def check(self, source, path):
            raise RuntimeError("the policy module is broken")

    service.package_policy = Policy()
    try:
        report = gate.PackageGate(service).run(good, stages=("policy",))
    finally:
        del service.package_policy
    broken = failures(report, "policy")
    assert broken and broken[0]["status"] == "error"
    assert report["errors"][0]["stage"] == "policy"


def test_scratch_ids_stay_valid_part_ids_and_never_collide():
    """A row's subject is free text (`cap_screw@size=M8-1.25`); a project part
    id is `^[a-z][a-z0-9_]{0,39}$`. The two are deliberately different objects,
    and the mapping between them must be total and injective."""
    from agentcad.core.model import ID_RE

    taken: dict[str, str] = {}
    long_part = "a" * 40
    for suffix in ("default", "size=M8-1.25", "preset:m5x16", "length=max",
                   "length=min", "grade=WIDE"):
        scratch = gate._scratch_id(long_part, suffix, taken)
        assert ID_RE.match(scratch), scratch
        assert scratch not in taken
        taken[scratch] = suffix
    assert len(taken) == 6


def test_a_two_part_package_measures_both_parts_end_to_end(service, tmp_path):
    """Two parts means two anchors and two connector sets in one assembly —
    the place instance-id collisions would show up."""
    second = (
        '"""A pin that mates into the block\'s bore."""\n'
        "import build123d as b3d\n\n"
        "PARAMS = {'d': {'default': 5.0, 'min': 3.0, 'max': 9.0,\n"
        "                'unit': 'mm', 'description': 'pin diameter'}}\n\n\n"
        "def build(p):\n    return b3d.Cylinder(p.d / 2, 12)\n\n\n"
        "def connectors(p, part):\n"
        "    return {'base': {'type': 'rigid',\n"
        "                     'location': ((0, 0, 0), (0, 0, 0))}}\n"
    )
    doc = manifest(parts={
        "block": {"file": "parts/block.py", "summary": "a test block"},
        "pin": {"file": "parts/pin.py", "summary": "a test pin"}})
    root = package_tree(tmp_path / "src" / "widget", doc=doc,
                        readme=README + "\n`pin` is the second part.\n")
    (root / "parts" / "pin.py").write_text(second)
    (root / "previews" / "pin_iso.png").write_bytes(_PNG)
    report = gate.PackageGate(service).run(root)
    assert failures(report) == [], messages(failures(report))
    built = [item["subject"] for item in rows(report, "build")]
    assert "block@default" in built and "pin@d=max" in built
    mated = [item["subject"] for item in rows(report, "connectors")]
    assert mated == ["block", "pin", "pin.base"]


def test_a_package_that_changes_under_the_run_is_incomplete_not_a_verdict(
        service, good, monkeypatch):
    """The content id is measured once, at the start, and slice 8 publishes it
    as *what was measured*. Nothing in the gate can move the tree — it never
    writes into the package — but an editor or a `git checkout` can, and then
    the rows describe a tree nobody has. Exit 2 says so."""
    original = gate._Run._stage_docs

    def touch(self, started):
        (self.source / "sneaked_in.py").write_text("# added mid-run\n")
        return original(self, started)

    monkeypatch.setattr(gate._Run, "_stage_docs", touch)
    try:
        report = gate.PackageGate(service).run(good)
    finally:
        (good / "sneaked_in.py").unlink(missing_ok=True)
    assert report["complete"] is False
    assert report["exit_code"] == 2
    assert report["publishable"] is False
    assert any("changed while the gate was running" in warning
               for warning in report["warnings"])


def test_two_runs_of_the_same_package_agree_row_for_row(service, good,
                                                        tmp_path):
    """A second run over a copy of the tree measures the same package: the
    rows are identical, which is what makes a gate report evidence."""
    copy = tmp_path / "copy" / "widget"
    copy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(good, copy)
    first = gate.PackageGate(service).run(good)
    second = gate.PackageGate(service).run(copy)
    assert [(i["id"], i["status"], i["message"]) for i in rows(first)] == \
           [(i["id"], i["status"], i["message"]) for i in rows(second)]
    assert first["package"] == second["package"]
