"""PRD-004 acceptance criteria — one named test per AC (slice 8).

The feature's mechanics are covered in depth by ``tests/test_checks.py`` (the
pure report layer), ``tests/test_checks_pipeline.py`` (the four stages over a
live service), ``tests/test_checks_ref.py`` (``--ref`` containment and the
determinism guard), ``tests/test_checks_cli.py`` (the command and its exit
codes), ``tests/test_checks_api.py`` (the tool, the routes and
``check_finished``), ``tests/test_checks_gate.py`` (posting and the proposal
gate) and ``tests/test_geometry_ci_action.py`` (the Action and the dogfood
workflow). This file is the *contract* layer: it walks each acceptance
criterion of ``docs/prd/in-progress/PRD-004-geometry-ci.md`` end to end through
the surfaces a user and an agent actually touch — the ``run_checks`` tool, the
HTTP passthrough MCP proxies, the real console script, git and the bundled
examples on a copy — so a reviewer can map AC → test without reading the unit
suites.

| AC | Test |
|----|------|
| AC1 | ``test_ac1_the_dogfood_workflow_certifies_the_bundled_examples`` — the
        workflow/action *shape* plus the changelog's live-run record; the green
        run itself is cited in the PR and in
        ``docs/changelog/0105-prd-004-docs-and-acceptance.md`` (the PRD-001 AC6
        / PRD-003 AC8 evidence-check precedent — a live CI run cannot be
        re-driven from the suite) |
| AC2 | ``test_ac2_interference_in_construction_is_red_in_both_renderings`` |
| AC3 | ``test_ac3_a_broken_spec_names_the_check_with_measured_and_limit`` |
| AC4 | ``test_ac4_a_script_error_carries_the_update_part_script_payload`` |
| AC5 | ``test_ac5_the_three_exit_codes_and_a_report_that_validates`` |
| AC6 | ``test_ac6_verify_determinism_passes_on_a_bundled_example`` |
| AC7 | ``test_ac7_checking_a_tag_leaves_the_project_byte_identical`` |
| AC8 | ``test_ac8_without_the_fem_extra_a_check_skips_and_strict_flips_it`` |
| AC9 | ``test_ac9_the_mcp_passthrough_and_the_cli_report_the_same_thing`` |
| AC10 | ``test_ac10_the_full_suite_count_is_cited`` — the evidence check over
         the slice-8 changelog; the run itself is `make test` |

Two recorded deviations are asserted **as designed**, not worked around:

* **The report is honest; ``--strict`` is the opt-in.** A ``skip`` row keeps
  its status, its reason and its hint under ``--strict`` — only the derived
  verdict moves (AC8). PRD-003's ``specs`` gate is the fail-closed reading of
  the same measurements and stays that way.
* **A reference part's ``is_valid`` is reported, never enforced** (changelog
  0099): OCCT calls the shipped rocketry STEP import invalid, exactly as
  ``tests/test_examples.py`` records, so the row passes and the fact travels in
  ``details.is_valid`` plus a warning.

Everything here drives the **real** service (whose publish hook snapshots into
git), so the module carries ``integration`` + ``portability`` and skips without
git; every case that builds geometry is additionally ``slow``. Examples are
always used on a ``copytree`` copy that is **renamed** first, so a copy can
never collide by name with the original a CLI registers at startup.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from agentcad.core import locks
from agentcad.core import specs as specs_module
from agentcad.core.branches import pinned_tree_var
from agentcad.core.checks import STAGES, render_markdown, validate_report
from agentcad.core.service import AgentCADService, EventBus
from agentcad.core.tools import build_registry
from agentcad.server.app import create_app

from .conftest import BOX_SCRIPT

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = REPO_ROOT / "examples"
CHANGELOG = REPO_ROOT / "docs" / "changelog"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "geometry-ci.yml"
ACTION = REPO_ROOT / ".github" / "actions" / "agentcad-check" / "action.yml"
SLICE8 = CHANGELOG / "0105-prd-004-docs-and-acceptance.md"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.portability,
    pytest.mark.timeout(900),
    pytest.mark.skipif(shutil.which("git") is None,
                       reason="git not found on PATH"),
]


# A hollow box with a wall budget: the PRD-003 fixture shape AC3 asks for.
HOLLOW_BOX = '''\
from build123d import *
from agentcad.toolkit.specs import check_wall

PARAMS = {"size": {"default": 20.0, "min": 10.0, "max": 60.0, "unit": "mm",
                   "description": "outer edge"},
          "wall": {"default": 2.5, "min": 0.5, "max": 5.0, "unit": "mm",
                   "description": "wall thickness"}}

SPECS = [check_wall(min_mm=2.0, grid=4, requirement="ENG-014")]

def build(p):
    inner = p.size - 2 * p.wall
    return Box(p.size, p.size, p.size) - Box(inner, inner, inner)
'''

# One FEM declaration: the [fem] extra is what decides whether it can be
# measured, and the suite must be green either way (AC8).
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

# A primitive with a non-positive dimension: an OCCT failure the Error Doctor
# has a catalogued fix for, so AC4 can prove BOTH halves (line and hint).
BROKEN_BUILD = '''\
from build123d import *

PARAMS = {"size": {"default": 10.0, "min": 1.0, "max": 100.0, "unit": "mm",
                   "description": "edge"}}

def build(p):
    Box(0, 0, 0)
    return Box(p.size, p.size, p.size)
'''


@pytest.fixture(autouse=True)
def _reset_context():
    """Identity and the branch pin are ContextVars: rebind them per test so one
    test's identity or pin can never leak into the next."""
    cid = locks.client_id_var.set("local")
    pin = pinned_tree_var.set(None)
    yield
    locks.client_id_var.reset(cid)
    pinned_tree_var.reset(pin)


@pytest.fixture
def stack(kernel, tmp_path):
    """The real service + its full registry (NOT ``make_test_service``, which
    disables the snapshot hook): ``tools_run_checks`` installs
    ``service.checks`` and the ``checks`` gate at ``build_registry`` time."""
    service = AgentCADService(tmp_path / "projects", kernel, EventBus())
    registry = build_registry(service)
    assert getattr(service, "checks", None) is not None
    return service, registry


def _copy_example(dest: Path, example: str, name: str) -> Path:
    """A copy of a bundled example, **renamed** so it can never collide with
    the original by name. ``.cache`` and ``exports`` are dropped, the way
    ``tests/test_examples.py`` does it; ``examples/`` is never mutated."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(EXAMPLES / example, dest,
                    ignore=shutil.ignore_patterns(".cache", "exports"))
    manifest = json.loads((dest / "project.json").read_text(encoding="utf-8"))
    manifest["name"] = name
    (dest / "project.json").write_text(json.dumps(manifest, indent=2) + "\n",
                                       encoding="utf-8")
    return dest


def _open_example(service, tmp_path, example: str, name: str) -> str:
    dest = _copy_example(tmp_path / "copies" / name, example, name)
    opened = service.open_project(str(dest))
    assert opened["name"] == name, opened
    return name


def _stage(report: dict, name: str) -> dict:
    return next(stage for stage in report["stages"] if stage["name"] == name)


def _item(report: dict, ident: str) -> dict:
    rows = [item for stage in report["stages"] for item in stage["items"]]
    for item in rows:
        if item["id"] == ident:
            return item
    raise AssertionError(f"no item {ident!r} in {[row['id'] for row in rows]}")


def _argv() -> list[str]:
    """The real entry point: the console script when the venv has one,
    otherwise ``main()`` through this interpreter (``agentcad.cli`` has no
    ``__main__`` guard)."""
    script = Path(sys.executable).with_name("agentcad")
    if script.exists():
        return [str(script)]
    return [sys.executable, "-c", "from agentcad.cli import main; main()"]


def _cli(*args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["AGENTCAD_KERNEL_POOL_SIZE"] = "1"   # one worker: CI memory, not cores
    return subprocess.run(_argv() + list(args), capture_output=True, text=True,
                          timeout=600, env=env)


def _fingerprint(root: Path) -> dict[str, str]:
    """sha256 of every file the *user* owns under *root*. ``.history/`` — git's
    own admin state, where a worktree registration is expected and
    self-healing — is excluded and asserted separately."""
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if rel.parts and rel.parts[0] == ".history":
            continue
        if path.is_file():
            out[str(rel)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


# ------------------------------------------------------------------- AC1


def test_ac1_the_dogfood_workflow_certifies_the_bundled_examples():
    """AC1 — "geometry CI runs green on the bundled examples in this
    repository's own GitHub Actions".

    A live workflow run cannot be produced from the test suite, so this is the
    two-part evidence check the repo already uses for run-once criteria
    (PRD-001 AC6, PRD-003 AC8): the **shape** — the workflow exists, matrixes
    the bundled examples the PRD names, and drives them through the *same*
    composite action a user's CAD repository consumes — and the **record**: the
    slice-8 changelog carries the live run's citation, and this test fails if
    that record is removed. The URL and conclusion are cited there and in the
    pull request.
    """
    assert WORKFLOW.is_file(), "the dogfood workflow is missing"
    assert ACTION.is_file(), "the composite action is missing"
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))

    # `on:` is parsed by PyYAML as the boolean True — YAML 1.1, and the reason
    # every workflow test in this repo reads it that way.
    triggers = workflow.get(True) or workflow.get("on")
    assert "push" in triggers and "pull_request" in triggers
    assert "pull_request_target" not in triggers, \
        "a fork's part scripts are arbitrary Python; never pull_request_target"

    matrix = workflow["jobs"]["examples"]["strategy"]["matrix"]["example"]
    # The three the PRD names, plus the fasteners example slice 7 added.
    for example in ("construction", "prototyping", "rocketry"):
        assert example in matrix, matrix
        assert (EXAMPLES / example / "project.json").is_file(), example

    steps = workflow["jobs"]["examples"]["steps"]
    check = next(s for s in steps if str(s.get("uses", "")).endswith(
        "actions/agentcad-check"))
    assert check["uses"] == "./.github/actions/agentcad-check"
    assert check["with"]["agentcad"] == ".", \
        "the dogfood run must install the checked-out source"
    assert check["with"]["project"] == "examples/${{ matrix.example }}"

    action = yaml.safe_load(ACTION.read_text(encoding="utf-8"))
    assert action["runs"]["using"] == "composite"
    bodies = "\n".join(step.get("run", "") for step in action["runs"]["steps"])
    assert "agentcad check" in bodies, "the action must run the real command"

    # The record the live run is cited in.
    assert SLICE8.is_file(), "the slice-8 changelog entry is missing"
    text = SLICE8.read_text(encoding="utf-8").lower()
    assert "ac1" in text
    assert "live run" in text
    assert "geometry-ci.yml" in text


# ------------------------------------------------------------------- AC2


@pytest.mark.slow
def test_ac2_interference_in_construction_is_red_in_both_renderings(
        stack, tmp_path):
    """AC2 — an interference introduced into the construction example turns the
    assembly stage red with the offending pair named in **both** renderings and
    the exit code 1. The pair is one row of kind ``pair`` carrying the overlap
    volume, so a consumer never has to parse prose to learn which instances
    collided.
    """
    service, registry = stack
    proj = _open_example(service, tmp_path, "construction", "ac2_construction")
    instances = [inst.to_manifest() for inst in service.store.instances(proj)]
    first, second = instances[0], instances[1]
    second["position"] = list(first["position"])      # park one inside the other
    service.set_assembly(proj, instances)

    report = registry.call(
        "run_checks", {"project": proj, "stages": ["build", "assembly"]})
    assert "error" not in report, report

    stage = _stage(report, "assembly")
    assert stage["status"] == "red"
    pair = next(item for item in stage["items"] if item["kind"] == "pair")
    assert pair["status"] == "fail"
    assert {pair["details"]["a"], pair["details"]["b"]} == \
        {first["id"], second["id"]}
    assert pair["details"]["volume_mm3"] > 0
    assert report["status"] == "red" and report["exit_code"] == 1
    assert validate_report(report) == []

    # report.md — the same fact, for a human and for $GITHUB_STEP_SUMMARY.
    text = render_markdown(report)
    assert first["id"] in text and second["id"] in text
    assert "## Failures" in text and "| assembly | red |" in text


# ------------------------------------------------------------------- AC3


@pytest.mark.slow
def test_ac3_a_broken_spec_names_the_check_with_measured_and_limit(stack):
    """AC3 — breaking a design spec turns the specs stage red naming the check
    with measured vs limit. The row is PRD-003's verdict passed through, not
    re-derived: the same status, requirement, ``measured``, ``limit`` and unit
    ``run_specs`` reports, plus the requirement traceability at the top of the
    check report.
    """
    service, registry = stack
    assert "error" not in registry.call("create_project", {"name": "spec"})
    assert "error" not in registry.call(
        "create_part", {"project": "spec", "part_id": "box",
                        "script": HOLLOW_BOX})

    green = registry.call("run_checks",
                          {"project": "spec", "stages": ["build", "specs"]})
    assert green["status"] == "green", green["summary"]
    assert _item(green, "specs:box:wall_min")["status"] == "pass"

    # Thin the wall past its declared budget.
    assert "error" not in registry.call(
        "set_params", {"project": "spec", "part_id": "box",
                       "values": {"wall": 0.8}})

    report = registry.call("run_checks",
                           {"project": "spec", "stages": ["build", "specs"]})
    stage = _stage(report, "specs")
    assert stage["status"] == "red"

    row = _item(report, "specs:box:wall_min")
    assert row["kind"] == "check" and row["status"] == "fail"
    assert row["requirement"] == "ENG-014"
    assert row["details"]["measured"] < row["details"]["limit"]["min_mm"]
    assert row["details"]["unit"] == "mm"
    assert report["requirements"]["ENG-014"]["status"] == "fail"
    assert report["exit_code"] == 1
    assert validate_report(report) == []
    assert "wall_min" in render_markdown(report)
    # The geometry still landed: a failing spec is signal, never a failed build.
    assert _item(report, "build:box")["status"] == "pass"


# ------------------------------------------------------------------- AC4


@pytest.mark.slow
def test_ac4_a_script_error_carries_the_update_part_script_payload(stack):
    """AC4 — a script error in one part fails the build stage carrying
    ``details.line`` and the Error Doctor hint: **the same payload**
    ``update_part_script`` returns, asserted by comparing the two side by side
    rather than by asserting each field is merely present.
    """
    service, registry = stack
    assert "error" not in registry.call("create_project", {"name": "broken"})
    assert "error" not in registry.call(
        "create_part", {"project": "broken", "part_id": "cube",
                        "script": BOX_SCRIPT})

    # The tool an agent would have used, and the payload it hands back. The
    # write is unconditional, so the broken script is what a check now measures.
    edited = registry.call(
        "update_part_script", {"project": "broken", "part_id": "cube",
                               "script": BROKEN_BUILD})
    assert edited["ok"] is False, edited
    payload = edited["error"]
    assert payload["details"]["line"], payload
    assert payload["details"]["hint"], "the Error Doctor hint"

    report = registry.call("run_checks",
                           {"project": "broken", "stages": ["build"]})
    assert "error" not in report, report      # a red check is data, not a raise

    stage = _stage(report, "build")
    assert stage["status"] == "red"
    row = _item(report, "build:cube")
    assert row["kind"] == "part" and row["status"] == "fail"
    assert row["error"]["type"] == payload["type"]
    assert row["error"]["details"]["line"] == payload["details"]["line"]
    assert row["error"]["details"]["hint"] == payload["details"]["hint"]
    assert report["exit_code"] == 1

    # Both halves reach the markdown, which is what a reviewer reads first.
    text = render_markdown(report)
    assert f"at line {payload['details']['line']}" in text
    assert payload["details"]["hint"][:40] in text


# ------------------------------------------------------------------- AC5


@pytest.mark.slow
def test_ac5_the_three_exit_codes_and_a_report_that_validates(tmp_path):
    """AC5 — ``report.json`` validates against the published schema and the
    three exit codes are each covered, all through the **real console script**:
    an exit code is the one thing a unit test cannot honestly stand in for.

    ``0`` green · ``1`` red, the model is wrong · ``2`` harness, no verdict.
    """
    projects = tmp_path / "projects"
    report = tmp_path / "report.json"
    markdown = tmp_path / "report.md"

    # --- 0: a clean copy of a bundled example
    green = _copy_example(tmp_path / "green", "prototyping", "ac5_green")
    res = _cli("check", "--project", str(green),
               "--projects-dir", str(projects),
               "--report", str(report), "--md", str(markdown))
    assert res.returncode == 0, res.stderr
    document = json.loads(report.read_text(encoding="utf-8"))
    assert validate_report(document) == [], validate_report(document)
    assert document["schema"] == 1
    assert document["status"] == "green" and document["exit_code"] == 0
    assert [stage["name"] for stage in document["stages"]] == list(STAGES)
    assert markdown.read_text(encoding="utf-8").startswith("# Geometry CI")

    # --- 1: the same example with one part script broken
    broken = _copy_example(tmp_path / "broken", "prototyping", "ac5_broken")
    script = broken / "parts" / "enclosure_lid.py"
    script.write_text("this is not python\n" + script.read_text(
        encoding="utf-8"), encoding="utf-8")
    red_report = tmp_path / "red.json"
    red_md = tmp_path / "red.md"
    res = _cli("check", "--project", str(broken),
               "--projects-dir", str(projects),
               "--report", str(red_report), "--md", str(red_md))
    assert res.returncode == 1, res.stderr
    document = json.loads(red_report.read_text(encoding="utf-8"))
    assert validate_report(document) == []
    assert document["status"] == "red" and document["exit_code"] == 1
    assert "enclosure_lid" in red_md.read_text(encoding="utf-8")

    # --- 2: no verdict at all
    res = _cli("check", "--project", "no_such_project",
               "--projects-dir", str(projects))
    assert res.returncode == 2, res.stdout + res.stderr
    assert "no_such_project" in res.stderr


# ------------------------------------------------------------------- AC6


@pytest.mark.slow
def test_ac6_verify_determinism_passes_on_a_bundled_example(stack, tmp_path):
    """AC6 — ``--verify-determinism`` passes on a bundled example: every part is
    built a second time on a **cold** cache and the stable artefacts agree byte
    for byte — the cache key, the ``.acm`` mesh (and its ``.faces.u32``
    sidecar), the SVG drawing and the metrics.

    DXF is excluded **by name**, as one ``skip`` row: ``ezdxf`` stamps
    ``$TDCREATE`` and fresh GUIDs into every document, so it is not byte-stable
    and never will be without adopting its fixed-date path. That is why the
    verdict here is exit 0 and not "green under ``--strict``" — a skip is not
    measured, and ``--strict`` says so.
    """
    service, _registry = stack
    proj = _open_example(service, tmp_path, "prototyping", "ac6_determinism")

    report = service.checks.run(proj, verify_determinism=True)

    stage = _stage(report, "determinism")
    assert stage["status"] == "green", [
        (item["id"], item["status"], item["message"])
        for item in stage["items"] if item["status"] != "pass"]
    passed = [item for item in stage["items"] if item["status"] == "pass"]
    assert passed, stage["items"]
    for item in passed:
        # A green row must name what it actually looked at.
        assert item["details"]["compared"], item
    assert report["status"] == "green" and report["exit_code"] == 0
    assert validate_report(report) == []

    dxf = next(item for item in stage["items"] if item["id"].endswith(":dxf"))
    assert dxf["status"] == "skip" and dxf["reason"] == "not_byte_stable"
    assert "ezdxf" in dxf["hint"]


# ------------------------------------------------------------------- AC7


@pytest.mark.slow
def test_ac7_checking_a_tag_leaves_the_project_byte_identical(stack, tmp_path):
    """AC7 — ``agentcad check --ref <tag>`` leaves the working tree and
    ``.cache/`` byte-identical. The ref is materialized into a throwaway
    detached ``git worktree`` and measured through a second, ephemeral service
    whose event bus and branch resolver are muzzled, so nothing the check does
    can reach back into the user's repository.

    The cache is warmed **first**: an untouched empty cache would prove
    nothing. ``.history/`` is excluded from the fingerprint and asserted
    separately — a worktree registration is git's own bookkeeping, expected and
    self-healing.
    """
    service, registry = stack
    assert "error" not in registry.call("create_project", {"name": "tagged"})
    assert "error" not in registry.call(
        "create_part", {"project": "tagged", "part_id": "cube",
                        "script": BOX_SCRIPT})
    root = Path(service.store.path_of("tagged"))

    # Warm the working tree's cache, then freeze the state under a tag.
    warm = registry.call("run_checks", {"project": "tagged"})
    assert warm["status"] == "green", warm["summary"]
    assert list(root.glob(".cache/*")), "the cache must be warm to matter"
    assert "error" not in registry.call(
        "version_tag", {"project": "tagged", "name": "v1",
                        "message": "as shipped"})

    before = _fingerprint(root)
    head_before = service.history.head(root)

    report = service.checks.run("tagged", ref="v1")

    assert report["source"]["kind"] == "tag"
    assert report["source"]["ref"] == "v1"
    assert report["source"]["sha"], "a ref check names the commit it measured"
    assert report["status"] == "green" and report["exit_code"] == 0
    # The stated price of containment: a ref check runs on a cold cache.
    assert _item(report, "build:cube")["details"]["cached"] is False

    assert _fingerprint(root) == before, "a check may not mutate the project"
    assert service.history.head(root) == head_before
    assert (service.history._run(root, "status", "--porcelain",
                                 check=False).stdout or "").strip() == ""


# ------------------------------------------------------------------- AC8


@pytest.mark.slow
def test_ac8_without_the_fem_extra_a_check_skips_and_strict_flips_it(
        stack, monkeypatch):
    """AC8 — without the ``[fem]`` extra a fem-linked check reports ``skip``
    and the exit code stays ``0``; ``--strict`` flips it to ``1``. The suite is
    green **without** the extra: the half that must hold on every machine is
    forced rather than skipped.

    The row itself never moves. ``--strict`` records the ids it counted in
    ``strict_failures`` and lets only the derived ``status``/``exit_code``
    change, so a reader can always tell what was *measured* from what was
    *demanded*. That is the deliberate difference between this report and
    PRD-003's unconditionally fail-closed ``specs`` gate.
    """
    service, registry = stack
    assert "error" not in registry.call("create_project", {"name": "fem"})
    assert "error" not in registry.call(
        "create_part", {"project": "fem", "part_id": "fem_box",
                        "script": FEM_BOX})
    monkeypatch.setattr(specs_module, "_fem_available", lambda: False)

    report = registry.call("run_checks",
                           {"project": "fem", "stages": ["build", "specs"]})
    row = _item(report, "specs:fem_box:fem_static")
    assert row["status"] == "skip"
    assert row["reason"] == "fem_extra_missing"
    assert row["hint"] and "fem" in row["hint"]
    assert report["status"] == "green" and report["exit_code"] == 0
    assert report["summary"]["skipped"] == 1
    assert report["summary"]["failed"] == 0
    assert report["host"]["fem"] is False
    assert validate_report(report) == []

    strict = registry.call("run_checks", {"project": "fem",
                                          "stages": ["build", "specs"],
                                          "strict": True})
    assert strict["exit_code"] == 1
    assert strict["status"] == "red"
    assert strict["strict_failures"] == ["specs:fem_box:fem_static"]
    survivor = _item(strict, "specs:fem_box:fem_static")
    assert survivor["status"] == "skip", "--strict never rewrites a row"
    assert survivor["reason"] == "fem_extra_missing"
    assert validate_report(strict) == []


# ------------------------------------------------------------------- AC9


def _normalize(report: dict) -> dict:
    """Everything that cannot be identical between two runs of one project: the
    clock, the host block and every duration."""
    stages = [{**stage, "duration_s": 0.0} for stage in report["stages"]]
    return {**report, "started": "", "finished": "", "duration_s": 0.0,
            "host": {}, "stages": stages}


@pytest.mark.slow
def test_ac9_the_mcp_passthrough_and_the_cli_report_the_same_thing(
        stack, tmp_path):
    """AC9 — ``run_checks`` over MCP returns a report identical to the CLI's.

    MCP is a stdio proxy in front of ``POST /api/tools/{name}``, so the honest
    test of "over MCP" is that passthrough, driven through a ``TestClient``,
    against the real console script over the same project. The only differences
    a consumer may see are the clock, the host block and the durations.
    """
    service, registry = stack
    assert "error" not in registry.call("create_project", {"name": "same"})
    assert "error" not in registry.call(
        "create_part", {"project": "same", "part_id": "cube",
                        "script": BOX_SCRIPT})
    projects = str(service.store.root)

    # Warm the cache first, so `details.cached` records the project's state
    # rather than which of the two runs happened to be first.
    assert "error" not in registry.call("run_checks", {"project": "same"})

    out = tmp_path / "cli.json"
    result = _cli("check", "--project", "same", "--projects-dir", projects,
                  "--report", str(out), "--quiet")
    assert result.returncode == 0, result.stderr
    from_cli = json.loads(out.read_text(encoding="utf-8"))

    app = create_app(service, registry, extra_allowed_hosts={"testserver"})
    client = TestClient(app, base_url="http://127.0.0.1")
    response = client.post("/api/tools/run_checks", json={"project": "same"})
    assert response.status_code == 200, response.text
    from_mcp = response.json()

    assert "error" not in from_mcp, from_mcp
    assert _normalize(from_cli) == _normalize(from_mcp)
    assert validate_report(from_mcp) == []


# ------------------------------------------------------------------ AC10


def test_ac10_the_full_suite_count_is_cited():
    """AC10 — "full suite green, count cited". The run itself is ``make test``;
    this is the evidence check that its count is on the record in the slice-8
    changelog, so the criterion has a named check that fails if the record is
    removed.
    """
    assert SLICE8.is_file(), "the slice-8 changelog entry is missing"
    text = SLICE8.read_text(encoding="utf-8").lower()
    assert "make test" in text
    assert "passed" in text and "skipped" in text
    assert "ac10" in text
