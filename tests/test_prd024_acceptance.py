"""PRD-024 acceptance — AgentCAD-Bench.

Three of these are slow by construction: **every** shipped reference is built
and scored (AC1), **every** checked-in STEP datum is re-exported and compared
to its script (design D9), and the example external submission is scored
through the real `agentcad bench score` process boundary (AC6). They carry
explicit `@pytest.mark.timeout`s because the global one is 120 s.

The rest are cheap, structural, and PR-blocking: the bench adds **no**
model-facing tool (AC9), no bench module imports OCP/build123d or an `[fem]`
extra (AC9/FR3), the bench workflow lives in its own file and never lets a
`pull_request` reach the job that holds the API key (D20), and `ci.yml` — which
`tests/test_prd006_acceptance.py` asserts byte-wise — is untouched by all of
it.

Parallel safety: every test that takes the kernel uses the session-scoped
`kernel` fixture over its **own** projects root, and nothing here writes into
`benchmarks/`, which is a read-only input. The one test that re-exports a
reference does it from a copy staged under its own projects root, never in
place.
"""

from __future__ import annotations

import ast
import json
import re
import shutil
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from agentcad.bench import tasks as bench_tasks
from agentcad.bench.scoring import Scorer

from .conftest import make_test_service

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "bench.yml"
CI = REPO / ".github" / "workflows" / "ci.yml"
EXAMPLE = REPO / "benchmarks" / "examples" / "submission-mfd-001"
EXAMPLE_TASK = "model_from_drawing/mfd_001_spacer_plate"

#: What `benchmarks/examples/submission-mfd-001` measured when it was authored
#: (`docs/bench.md`, "A worked submission"): R4 corner rounds instead of R5 and
#: an ISO-273 medium Ø6.6 clearance hole instead of the drawing's Ø6.0, which
#: is a geometry subscore of 0.9919 and nothing else lost. The tolerance is
#: wide enough that a build123d pin move does not fail this test for a reason
#: that has nothing to do with the example — and narrow enough that the doc's
#: number stops being true before the test goes green on a copy of the
#: reference (which would score 1.0).
EXAMPLE_TOTAL = 0.995937
EXAMPLE_TOLERANCE = 0.01

#: The task ids, read at COLLECTION time. Parametrising over the roster rather
#: than over a hand-written list is the whole point of AC1: a 26th task is
#: covered by adding its directory.
TASK_IDS = [task.id for task in bench_tasks.load_tasks()]
DATUM_TASK_IDS = [task.id for task in bench_tasks.load_tasks()
                  if task.reference_steps]


@pytest.fixture
def service_with_kernel(kernel, tmp_path_factory):
    """A private projects root over the shared session kernel."""
    return make_test_service(tmp_path_factory.mktemp("prd024"), kernel)


# ------------------------------------------------------------------- AC1

@pytest.mark.slow
@pytest.mark.timeout(1800)
@pytest.mark.parametrize("task_id", TASK_IDS)
def test_ac1_every_shipped_reference_scores_one(service_with_kernel, task_id):
    """AC1: `bench score reference/project` is exactly 1.0 for every task.

    This is the task set's own self-test. It catches an over-tight metric
    window, a rubric row the reference itself fails, a datum exported from a
    different script, and a frame the reference does not sit on — each of
    which would make every candidate's score a statement about the task rather
    than about the agent.
    """
    task = bench_tasks.load_task(task_id)
    score = Scorer(service_with_kernel).score(task, task.reference_project)
    # Exactly 1.0, not "close to it": `score.json` is rounded to six
    # decimals before it is written, so a reference that measures right
    # returns the literal float, and a tolerance here would hide a
    # rubric that is a hair off from the thing it grades.
    assert score["total"] == 1.0, score["subscores"]
    # A total of 1.0 with everything excluded would be vacuous.
    assert score["weights_effective"], score
    for name, row in score["subscores"].items():
        assert row["status"] in ("ok", "not_applicable"), (name, row)


# ----------------------------------------------- the STEP-drift check (D9)

def _stage_reference(service, task) -> str:
    """Copy `reference/project` under the service's own projects root.

    Never opened in place: a build writes `.cache/` and an export writes
    `exports/` into the project directory, and `benchmarks/` is a read-only
    input (it is not even a writable root for the confined worker).
    """
    name = json.loads(
        (task.reference_project / "project.json").read_text(encoding="utf-8")
    )["name"]
    shutil.copytree(task.reference_project, Path(service.store.root) / name)
    return name


def _measure_step(service, proj: str, source: Path, mesh_path: Path) -> dict:
    """The kernel's own metrics for a STEP file — bbox and volume.

    `build_reference` is the import path the product uses for a reference
    part; it wants somewhere to put a mesh, which is why *mesh_path* is inside
    the project (a directory the worker may write to).
    """
    out = service.kernel.request(
        "build_reference",
        {"source_path": str(source), "mesh_path": str(mesh_path),
         "tolerance": 1.0},
        timeout_s=300.0)
    return out["metrics"]


@pytest.mark.slow
@pytest.mark.timeout(1800)
@pytest.mark.parametrize("task_id", DATUM_TASK_IDS)
def test_the_checked_in_step_datum_still_matches_its_script(
        service_with_kernel, task_id):
    """Design D9: the datum is the score, so drift from its script is a bug.

    The reference **script** is the published solution and the reference
    **STEP** is what every candidate is measured against. If a build123d pin
    move (or an edit to the script) changed the exported shape, every published
    number would silently become a function of the toolchain. So: re-export,
    and compare to the bytes on disk — volume within 1e-6 relative, every bbox
    bound within 1e-3 mm, and IoU >= 0.9999.

    **The IoU half runs only where the datum is actually scored** (a non-zero
    `geometry` weight). `fix_005_invalid_shell` is the exception the product
    already found: its swept pipe surface does not survive the STEP round trip
    as a **boolean operand** — two STEP imports of the identical shape (same
    volume to fifteen digits, as this test asserts) intersect at 0.0 mm³ — and
    that is exactly why the task weights `geometry` 0.00, argued in its own
    `prompt.md`. Asserting an IoU there would be asserting a product defect the
    bench deliberately fenced rather than the drift this test is for; the
    volume and bbox comparison still catches a datum that moved.

    Tasks whose category weights `geometry` at zero ship no datum at all
    (`reference.steps: {}`) and are not parametrised here — there is nothing to
    drift.
    """
    task = bench_tasks.load_task(task_id)
    service = service_with_kernel
    proj = _stage_reference(service, task)
    exports = Path(service.store.exports_dir(proj))
    scored = float(task.weights.get("geometry") or 0.0) > 0.0
    for part_id, datum in sorted(task.reference_steps.items()):
        assert datum.is_file(), (task_id, part_id, str(datum))
        fresh = Path(service.export_part(proj, part_id, "step")["path"])

        fresh_m = _measure_step(service, proj, fresh, exports / "_drift.acm")
        datum_m = _measure_step(service, proj, datum, exports / "_drift.acm")
        reference_volume = datum_m["volume_mm3"]
        assert reference_volume > 0.0, (task_id, part_id, datum_m)
        assert abs(fresh_m["volume_mm3"] - reference_volume) <= \
            1e-6 * reference_volume, (task_id, part_id,
                                      fresh_m["volume_mm3"], reference_volume)
        for bound in ("min", "max"):
            for axis, (a, b) in enumerate(zip(fresh_m["bbox"][bound],
                                              datum_m["bbox"][bound])):
                assert abs(a - b) <= 1e-3, (task_id, part_id, bound, axis,
                                            fresh_m["bbox"], datum_m["bbox"])
        if not scored:
            continue

        # Both sides are STEP, so this is the boolean the scorer takes minus
        # the candidate's script — the comparison the datum has to survive.
        out = service.kernel.request(
            "iou",
            {"candidate": {"source": str(fresh)},
             "reference": {"source": str(datum)},
             "align": "world", "rotations_deg": [[0.0, 0.0, 0.0]]},
            timeout_s=300.0)
        assert out["status"] == "ok", (task_id, part_id, out)
        assert out["iou"] >= 0.9999, (task_id, part_id, out)


# ------------------------------------------------------------------- AC6

@pytest.mark.slow
@pytest.mark.timeout(900)
def test_ac6_the_example_external_submission_is_accepted_and_scored(tmp_path):
    """AC6/FR10: the checked-in external submission scores through the CLI.

    Through `main()` rather than through `Scorer`, because the thing FR10
    promises an outside team is the **command**: point `agentcad bench score`
    at a project directory an agent produced and get a `score.json`. Exit 0,
    schema 1, and the total the walkthrough in `docs/bench.md` quotes.
    """
    from agentcad import cli as agentcad_cli
    from agentcad.core import locks

    out = tmp_path / "out"
    argv = ["agentcad", "bench", "score", str(EXAMPLE),
            "--task", EXAMPLE_TASK, "--out", str(out), "--quiet"]
    before = locks.current_client_id()
    try:
        with patch.object(sys, "argv", argv):
            with pytest.raises(SystemExit) as exc:
                agentcad_cli.main()
    finally:
        locks.set_client_id(before)
    assert exc.value.code == 0

    score = json.loads((out / "score.json").read_text(encoding="utf-8"))
    assert score["schema"] == 1
    assert score["task"] == EXAMPLE_TASK
    assert score["subscores"]["built"]["status"] == "ok"
    assert 0.0 <= score["total"] <= 1.0
    # Deliberately imperfect: a submission that scored 1.0 would be a copy of
    # the reference and would teach a reader nothing about the scorer.
    assert score["total"] < 1.0
    assert score["total"] == pytest.approx(EXAMPLE_TOTAL,
                                           abs=EXAMPLE_TOLERANCE), score


def test_the_example_submission_is_not_a_copy_of_the_reference():
    """It has to be someone else's answer, or AC6 proves nothing."""
    task = bench_tasks.load_task(EXAMPLE_TASK)
    mine = (EXAMPLE / "parts" / "spacer_plate.py").read_text(encoding="utf-8")
    theirs = (task.reference_project / "parts" / "spacer_plate.py").read_text(
        encoding="utf-8")
    assert mine != theirs
    assert (EXAMPLE / "project.json").is_file()
    assert (EXAMPLE / "README.md").is_file()
    # No rubric rides along: the scorer injects the task's own SPECS and
    # deletes any project-scope specs.py, and a submission that shipped one
    # would be measuring itself.
    assert not (EXAMPLE / "specs.py").exists()


# ------------------------------------------------------------------- AC9

def _imported_modules(path: Path) -> set[str]:
    """Every module name *path* imports, at any nesting depth.

    Parsed rather than grepped: `agentcad/bench/` is a package whose whole
    argument is written down in its own docstrings ("OCP-free by contract"),
    and a substring search flags the prose that states the rule as a violation
    of it. A lazy `import x` inside a function is still an import and is still
    caught, because the walk is over the whole tree.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module.split(".")[0])
    return names


def _bench_modules() -> list[Path]:
    return sorted((REPO / "agentcad" / "bench").rglob("*.py"))


def test_ac9_no_bench_module_imports_ocp_or_build123d():
    """Only `agentcad/kernel/` may import the geometry kernel."""
    offenders = []
    for path in _bench_modules():
        for name in sorted(_imported_modules(path) & {"build123d", "OCP"}):
            offenders.append(f"{path.relative_to(REPO)}: {name}")
    assert offenders == []


def test_ac9_no_bench_module_needs_the_fem_extra():
    """FR3: the suite is green without `[fem]`, so nothing here may import it."""
    offenders = []
    for path in _bench_modules():
        for name in sorted(_imported_modules(path)
                           & {"gmsh", "skfem", "meshio"}):
            offenders.append(f"{path.relative_to(REPO)}: {name}")
    assert offenders == []
    # And the loader refuses a task whose rubric reaches for it BY NAME, so a
    # bundle cannot smuggle the extra in through a spec row.
    assert bench_tasks.FORBIDDEN_SPEC_CALL == "check_fem_static"


def test_ac9_the_bench_adds_no_model_facing_tool(service_with_kernel):
    """The `iou` handler is kernel-internal: a bench-only tool would
    contaminate the very measurement it exists to take."""
    from agentcad.core.tools import build_registry

    registry = build_registry(service_with_kernel)
    names = {tool.name for tool in registry.list()}
    assert "iou" not in names
    assert not any(name.startswith("bench") for name in names), sorted(names)


def test_ac9_the_bench_adds_no_route_and_no_pack():
    """No `core/tools_bench.py`, no `server/routes_bench.py`, no manifest key."""
    assert not (REPO / "agentcad" / "core" / "tools_bench.py").exists()
    assert not (REPO / "agentcad" / "server" / "routes_bench.py").exists()


# --------------------------------------------------------------- the CI (D20)

def _workflow() -> dict:
    # PyYAML resolves the bare `on:` key to True (the Norway problem's cousin).
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_the_bench_ci_is_its_own_workflow_with_three_jobs():
    workflow = _workflow()
    triggers = workflow.get("on") or workflow.get(True)
    assert set(triggers) == {"push", "pull_request", "schedule",
                             "workflow_dispatch"}
    assert set(workflow["jobs"]) == {"selftest", "guard", "builtin"}
    assert workflow["permissions"] == {"contents": "read"}


def test_the_secret_job_never_runs_on_a_pull_request():
    """D20: a fork PR must not reach a job that holds a secret AND runs
    arbitrary agent-authored Python (`geometry-ci.yml:8-9`'s rule)."""
    text = _text()
    assert "ANTHROPIC_API_KEY" in text
    assert "pull_request_target" not in text

    builtin = _workflow()["jobs"]["builtin"]
    # The WHOLE condition, not three substring probes: `A || B` contains both
    # needles and would sail through a membership test while running the paid
    # job on every event that satisfies either half. The `roadmap` branch is in
    # `on.push` for `selftest`'s sake, so the ref test is what keeps the spend
    # on main (design §13).
    assert builtin["if"] == (
        "${{ needs.guard.outputs.has_key == 'true'"
        " && github.event_name != 'pull_request'"
        " && github.ref == 'refs/heads/main' }}")
    assert builtin["needs"] == "guard"
    assert builtin["env"]["ANTHROPIC_API_KEY"] == \
        "${{ secrets.ANTHROPIC_API_KEY }}"


def test_the_pr_job_touches_no_secret_and_runs_the_bench_suite():
    selftest = _workflow()["jobs"]["selftest"]
    body = yaml.safe_dump(selftest)
    assert "secrets." not in body, body
    runs = " ".join(step.get("run", "") for step in selftest["steps"])
    assert "tests/test_prd024_acceptance.py" in runs
    assert "tests/test_bench_" in runs
    # The slow half IS the PR-blocking half: AC1 scores all 25 references and
    # the STEP-drift check re-exports all 15 datums, and both carry `slow`.
    # A `-m "not slow"` here would leave the task set unproved on every PR
    # while the job still reported green.
    assert "not slow" not in runs
    # PRD-006 Decision 13: this job runs candidate-authored Python in a
    # confined worker, so a degraded sandbox is red, not skipped.
    suite_step = [step for step in selftest["steps"]
                  if "pytest" in step.get("run", "")]
    assert len(suite_step) == 1, suite_step
    assert suite_step[0]["env"]["AGENTCAD_EXPECT_SANDBOX"] == "active"


def test_the_guard_job_says_out_loud_when_the_key_is_absent():
    """Ruling 1: skipped **with a visible notice**, never silently — and the
    guard job exists at all because the `secrets` context is not available in
    a job-level `if:`."""
    guard = _workflow()["jobs"]["guard"]
    assert guard["outputs"]["has_key"] == "${{ steps.k.outputs.has_key }}"
    body = " ".join(step.get("run", "") for step in guard["steps"])
    assert "::notice" in body
    assert "has_key=true" in body and "has_key=false" in body
    # A guard that ran the agent would be the thing it guards against.
    assert "bench run" not in body


def test_the_builtin_job_gates_on_the_baseline_and_uploads_its_results():
    steps = _workflow()["jobs"]["builtin"]["steps"]
    runs = " ".join(step.get("run", "") for step in steps)
    assert "bench run --set fast --agent builtin" in runs
    assert "--baseline benchmarks/baseline.json" in runs
    upload = [s for s in steps if s.get("uses", "").startswith(
        "actions/upload-artifact")]
    assert upload and upload[0]["if"] == "always()", steps


def test_the_bench_workflow_installs_the_occt_libraries_ci_yml_does():
    """The same apt list and the same hardening: a bench job that cannot
    import OCCT is a red run whose cause is a missing `libgl1`."""
    def packages(text: str) -> set[str]:
        body = text.split("apt-get install -y --no-install-recommends", 1)[1]
        body = body.split("\n\n", 1)[0].replace("\\", " ")
        return {tok for tok in body.split() if tok.startswith("lib")}

    assert packages(_text()) == packages(CI.read_text(encoding="utf-8"))
    assert "Acquire::Retries=3" in _text()


def test_ci_yml_is_untouched_by_the_bench():
    """`tests/test_prd006_acceptance.py` asserts `ci.yml` byte-wise; the bench
    lives in its own file precisely so that assertion keeps holding."""
    ci = CI.read_text(encoding="utf-8")
    # (`test_sketch_bench.py` is a sketch-solver benchmark that predates this
    # feature and is named in ci.yml — hence the specific needles.)
    for needle in ("agentcad bench", "bench.yml", "test_prd024",
                   "test_bench_", "ANTHROPIC_API_KEY", "benchmarks/"):
        assert needle not in ci, needle
    assert ci.count("expect_sandbox: active") == 2


def test_the_task_tree_and_the_results_stay_out_of_the_image():
    """D22: `benchmarks/` is resource data, not runtime payload."""
    ignored = (REPO / ".dockerignore").read_text(encoding="utf-8").split()
    assert "benchmarks/" in ignored
    assert "out/" in ignored


# ------------------------------------------------------------- the evidence

def test_the_close_out_changelog_cites_a_full_suite_count():
    """"`make test` green" is a claim about a *run*, so it is an evidence check.

    The house precedent is `tests/test_prd006_acceptance.py`'s
    `test_ac8_the_full_suite_count_is_cited` (itself the PRD-004/008/011/012
    one): recomputing the number would mean running the full suite from inside
    the full suite, and `--collect-only` counts *cases*, which is not what
    `make test` reports.

    The digits are required **immediately before the word `passed`**, because
    every entry's own title is a four-digit number and "the file contains a
    long digit string" would be satisfied by an entry that cites nothing. The
    literal placeholder is **red on purpose**, so the close-out cannot forget
    to fill it in.

    The entry is found by its **slug**, never its number: this repo renumbers
    changelogs when two branches collide at merge, and a hardcoded `0267-`
    would be a test that fails for a rename it should not care about.
    """
    matches = sorted((REPO / "docs" / "changelog").glob(
        "*-prd-024-bench-acceptance-ci-docs.md"))
    assert len(matches) == 1, (
        f"expected exactly one PRD-024 close-out changelog entry, found "
        f"{[m.name for m in matches]}")
    text = matches[0].read_text(encoding="utf-8")
    assert "make test" in text
    assert re.search(r"\b\d{4,6}\s+passed\b", text.replace(",", "")), (
        f"{matches[0].name} does not cite a `make test` suite count — fill in "
        f"the placeholder before committing")


# ------------------------------------------------------------------ docs

@pytest.mark.parametrize("path,needle", [
    ("docs/agent-api.md", "](bench.md)"),
    ("docs/architecture.md", "agentcad/bench/"),
    ("docs/geometry-ci.md", "](bench.md)"),
    ("AGENTS.md", "docs/bench.md"),
    ("CLAUDE.md", "docs/bench.md"),
])
def test_the_bench_is_cross_referenced_from_the_surrounding_docs(path, needle):
    """FR10's home is `docs/bench.md`; a doc nobody links to is a doc nobody
    reads."""
    assert (REPO / "docs" / "bench.md").is_file()
    assert needle in (REPO / path).read_text(encoding="utf-8"), path
