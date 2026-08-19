"""The bench scorer: six subscores, rubric injection, a byte-stable score.json.

PRD-024 AC2 (a flawed solution loses exactly the right subscores) and AC3 (two
runs over the same submission produce byte-identical bytes). Every test builds
its own projects root and shares the session-scoped kernel; nothing here writes
into `benchmarks/`, which is a read-only input.
"""
import shutil

import pytest

from agentcad.bench import tasks as bench_tasks
from agentcad.bench._json import canonical_json
from agentcad.bench.scoring import Scorer

from .conftest import make_test_service

SEED = "model_from_drawing/mfd_001_spacer_plate"
pytestmark = pytest.mark.timeout(600)


@pytest.fixture
def service_with_kernel(kernel, tmp_path_factory):
    """A private projects root over the shared session kernel.

    The scorer never scores *through* this service's store — it opens a second,
    muzzled one over its own work cell — but it needs a real `projects_root`
    for the overlap refusal and a warm kernel to measure with.
    """
    return make_test_service(tmp_path_factory.mktemp("bench_projects"), kernel)


@pytest.fixture
def scorer(service_with_kernel):
    return Scorer(service_with_kernel)


def _reference_copy(task, tmp_path):
    dst = tmp_path / "flawed"
    shutil.copytree(task.reference_project, dst)
    return dst


def test_the_reference_solution_scores_one(scorer):
    task = bench_tasks.load_task(SEED)
    score = scorer.score(task, task.reference_project)
    assert score["schema"] == 1
    assert score["task"] == SEED
    assert score["total"] == pytest.approx(1.0, abs=1e-9)
    for name in ("built", "valid", "specs", "geometry", "metrics"):
        assert score["subscores"][name]["status"] == "ok", name
        assert score["subscores"][name]["value"] == pytest.approx(1.0,
                                                                  abs=1e-6)
    assert score["subscores"]["interference"]["status"] == "not_applicable"


def test_scoring_twice_is_byte_identical(scorer):
    task = bench_tasks.load_task(SEED)
    first = canonical_json(scorer.score(task, task.reference_project))
    second = canonical_json(scorer.score(task, task.reference_project))
    assert first == second
    assert b"generated" not in first and b"started" not in first
    assert b"/private" not in first and b"/tmp" not in first


# --------------------------------------------------------------------- AC2
# One flaw at a time, each losing exactly the subscores it should.

#: The reference drills a 2x2 grid of holes in the plate's top face. Dropping
#: the two `y < 0` positions removes exactly two holes and ADDS none — which is
#: the whole point: `GridLocations(dx, dy, 2, 1)` would *also* move the two
#: survivors onto y = 0, so the candidate would be missing four reference holes
#: and carrying two the reference does not have, and the IoU loss would be
#: 6 hole volumes rather than the 2 the test is about.
_GRID = "with GridLocations(p.hole_dx, p.hole_dy, 2, 2):"
_TWO_HOLES = ("with Locations((-p.hole_dx / 2, -p.hole_dy / 2),\n"
              "                       (p.hole_dx / 2, -p.hole_dy / 2)):")


def test_one_missing_hole_costs_geometry_and_metrics_but_not_specs(scorer,
                                                                   tmp_path):
    task = bench_tasks.load_task(SEED)
    flawed = _reference_copy(task, tmp_path)
    script = flawed / "parts" / "spacer_plate.py"
    text = script.read_text()
    assert _GRID in text
    script.write_text(text.replace(_GRID, _TWO_HOLES))

    score = scorer.score(task, flawed)
    geometry = score["subscores"]["geometry"]
    assert geometry["status"] == "ok"
    # Two of four holes are gone; the candidate strictly contains the
    # reference, so the drop is exactly 2 * hole_volume / union.
    hole = 3.1415926535 * 3.0 ** 2 * 6.0
    detail = geometry["detail"]["spacer_plate"]
    assert 1.0 - geometry["value"] == pytest.approx(
        2 * hole / detail["union_mm3"], rel=0.05)
    # The rubric measures wall thickness, validity and envelope — none of which
    # a missing hole breaks.
    assert score["subscores"]["specs"]["value"] == pytest.approx(1.0)
    assert score["total"] < 1.0


def test_a_violated_spec_names_the_failing_check(scorer, tmp_path):
    task = bench_tasks.load_task(SEED)
    flawed = _reference_copy(task, tmp_path)
    manifest = flawed / "project.json"
    manifest.write_text(manifest.read_text().replace('"thickness": 6.0',
                                                     '"thickness": 12.0'))
    score = scorer.score(task, flawed)
    specs = score["subscores"]["specs"]
    assert specs["status"] == "ok"
    assert specs["value"] < 1.0
    assert "spacer_plate:envelope" in specs["detail"]["failed"]


def test_a_candidates_own_SPECS_are_discarded(scorer, tmp_path):
    task = bench_tasks.load_task(SEED)
    flawed = _reference_copy(task, tmp_path)
    script = flawed / "parts" / "spacer_plate.py"
    script.write_text(script.read_text() + (
        "\nfrom agentcad.toolkit.specs import check_valid\n"
        "SPECS = [check_valid(name='free_point')]\n"))
    score = scorer.score(task, flawed)
    specs = score["subscores"]["specs"]
    assert specs["detail"]["total"] == 3
    assert "spacer_plate:free_point" not in specs["detail"]["failed"]
    assert "spacer_plate:free_point" not in specs["detail"]["skipped"]


def test_a_missing_target_part_is_zero_everywhere_and_never_not_applicable(
        scorer, tmp_path):
    task = bench_tasks.load_task(SEED)
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "project.json").write_text(
        '{"schema_version": 1, "name": "empty", "units": "mm", "parts": []}')
    score = scorer.score(task, empty)
    for name in ("built", "valid", "geometry", "metrics"):
        assert score["subscores"][name]["value"] == 0.0, name
        assert score["subscores"][name]["status"] == "ok", name
    assert score["total"] == 0.0


def test_the_submission_directory_is_never_mutated(scorer, tmp_path):
    task = bench_tasks.load_task(SEED)
    copy = _reference_copy(task, tmp_path)
    before = {p.relative_to(copy): p.read_bytes()
              for p in copy.rglob("*") if p.is_file()}
    scorer.score(task, copy)
    after = {p.relative_to(copy): p.read_bytes()
             for p in copy.rglob("*") if p.is_file()}
    assert before == after


def test_an_errored_geometry_subscore_is_excluded_and_weights_renormalise(
        scorer, tmp_path, monkeypatch):
    task = bench_tasks.load_task(SEED)
    from agentcad.kernel.client import KernelError
    real = scorer.service.kernel.request

    def boom(method, params, *args, **kwargs):
        if method == "iou":
            raise KernelError("kernel_error", "iou unavailable: boom",
                              {"stage": "intersect"})
        return real(method, params, *args, **kwargs)

    monkeypatch.setattr(scorer.service.kernel, "request", boom)
    score = scorer.score(task, task.reference_project)
    geometry = score["subscores"]["geometry"]
    assert geometry["status"] == "error"
    assert geometry["detail"]["error"]["stage"] == "intersect"
    assert "geometry" not in score["weights_effective"]
    assert score["weights_effective"]["built"] == pytest.approx(0.15 / 0.5,
                                                                rel=1e-9)
    # The four remaining subscores are perfect, so renormalising them is 1.0.
    assert score["total"] == pytest.approx(1.0, abs=1e-9)


# ------------------------------------------------------ the surrounding rules

#: A four-facet ASCII tetrahedron. The point is only that `import_stl` accepts
#: it: a mesh side must be refused BEFORE any boolean, because an OCCT boolean
#: on a welded STL face segfaults the worker.
_TETRAHEDRON = """solid t
facet normal 0 0 -1
  outer loop
    vertex 0 0 0
    vertex 0 10 0
    vertex 10 0 0
  endloop
endfacet
facet normal 0 -1 0
  outer loop
    vertex 0 0 0
    vertex 10 0 0
    vertex 0 0 10
  endloop
endfacet
facet normal -1 0 0
  outer loop
    vertex 0 0 0
    vertex 0 0 10
    vertex 0 10 0
  endloop
endfacet
facet normal 0.577 0.577 0.577
  outer loop
    vertex 10 0 0
    vertex 0 10 0
    vertex 0 0 10
  endloop
endfacet
endsolid t
"""


def test_a_mesh_only_candidate_is_a_zero_and_never_an_exclusion(scorer,
                                                                tmp_path):
    """FR5/AC4 at the scorer's level: `skipped_mesh` is **included** at zero.

    Being handed a mesh when a model was asked for is a fact about the
    candidate, so it may never be excluded from the total — excluding it would
    let a candidate raise its score by exporting an STL.
    """
    task = bench_tasks.load_task(SEED)
    flawed = _reference_copy(task, tmp_path)
    (flawed / "parts" / "spacer_plate.py").unlink()
    (flawed / "imports").mkdir()
    (flawed / "imports" / "spacer_plate.stl").write_text(_TETRAHEDRON)
    manifest = flawed / "project.json"
    manifest.write_text(manifest.read_text().replace(
        '"id": "spacer_plate",',
        '"id": "spacer_plate", "kind": "reference",'
        ' "source": "spacer_plate.stl",'))

    score = scorer.score(task, flawed)
    geometry = score["subscores"]["geometry"]
    assert geometry["status"] == "skipped_mesh"
    assert geometry["value"] == 0.0
    # Included, at its declared weight: the renormalised weights still name it.
    assert geometry["weight"] == 0.5
    assert "geometry" in score["weights_effective"]


def test_a_work_dir_inside_the_task_bundle_is_refused(tmp_path):
    from agentcad.bench.scoring import refuse_scoring_overlap
    from agentcad.core.model import ValidationError

    task = bench_tasks.load_task(SEED)
    inside = task.root / "reference"
    with pytest.raises(ValidationError) as excinfo:
        refuse_scoring_overlap(inside, tmp_path / "submission", task.root,
                               tmp_path / "projects")
    assert "the task directory" in excinfo.value.message
    # A work dir elsewhere is accepted, and None means "no --work-dir at all".
    refuse_scoring_overlap(tmp_path / "work", tmp_path / "submission",
                           task.root, tmp_path / "projects")
    refuse_scoring_overlap(None, tmp_path / "submission", task.root,
                           tmp_path / "projects")


def test_total_of_renormalises_and_answers_zero_when_nothing_is_included():
    from agentcad.bench.scoring import total_of

    def row(value, weight, status="ok"):
        return {"value": value, "weight": weight, "status": status,
                "detail": {}}

    subscores = {
        "built": row(1.0, 0.15),
        "geometry": row(0.0, 0.5, "error"),
        "interference": row(0.0, 0.0, "not_applicable"),
        "metrics": row(0.5, 0.35),
    }
    total, effective = total_of(subscores)
    assert set(effective) == {"built", "metrics"}
    assert effective["built"] == pytest.approx(0.3)
    assert total == pytest.approx(0.3 * 1.0 + 0.7 * 0.5)

    total, effective = total_of({"built": row(1.0, 0.4, "error")})
    assert (total, effective) == (0.0, {})


def test_a_submission_that_is_not_a_project_scores_zero_and_leaks_no_path(
        scorer, tmp_path):
    """A candidate that produced nothing is measured, and it measures zero.

    It also proves the scrub: `ProjectStore._read_manifest`'s refusal is
    literally `f"{path} is not a project"`, and *path* is this run's randomly
    named work cell — embedded raw, one run's `score.json` would differ from
    the next's.
    """
    task = bench_tasks.load_task(SEED)
    nothing = tmp_path / "nothing"
    nothing.mkdir()
    first = canonical_json(scorer.score(task, nothing))
    second = canonical_json(scorer.score(task, nothing))
    assert first == second
    assert b"agentcad-bench-" not in first
    assert b"/private" not in first and b"/tmp" not in first
    assert b"<cell>" in first          # the refusal is still reported

    score = scorer.score(task, nothing)
    assert score["total"] == 0.0
    for name in ("built", "valid", "specs", "geometry", "metrics"):
        assert score["subscores"][name]["status"] == "ok", name
        assert score["subscores"][name]["value"] == 0.0, name
    assert score["subscores"]["interference"]["status"] == "not_applicable"
