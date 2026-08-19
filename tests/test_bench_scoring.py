"""The bench scorer: six subscores, rubric injection, a byte-stable score.json.

PRD-024 AC2 (a flawed solution loses exactly the right subscores) and AC3 (two
runs over the same submission produce byte-identical bytes). Every test builds
its own projects root and shares the session-scoped kernel; nothing here writes
into `benchmarks/`, which is a read-only input.
"""
import dataclasses
import json
import shutil
import time
from types import SimpleNamespace

import pytest

from agentcad.bench import tasks as bench_tasks
from agentcad.bench._json import canonical_json
from agentcad.bench.scoring import (SUBSCORE_STATUSES, Scorer,
                                    interference_fraction, metric_of,
                                    window_satisfied)
from agentcad.core.model import NotFoundError
from agentcad.kernel.client import KernelError

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


def _reference_copy(task, tmp_path, name="flawed"):
    dst = tmp_path / name
    shutil.copytree(task.reference_project, dst)
    return dst


def _assert_statuses(score):
    """Every emitted status is one of the four the contract names."""
    for name, row in score["subscores"].items():
        assert row["status"] in SUBSCORE_STATUSES, (name, row["status"])
        assert 0.0 <= row["value"] <= 1.0, name


def _weighted(task, **weights):
    """The task with a hand-picked weight vector — for the kernel-free unit
    tests, where the point is which subscore is switched on."""
    base = {name: 0.0 for name in bench_tasks.SUBSCORES}
    base.update(weights)
    return dataclasses.replace(task, weights=base)


class _Explodes:
    """Any attribute access is a test failure: proves a zero-weight subscore
    short-circuits before it touches the service at all."""

    def __getattr__(self, name):
        raise AssertionError(f"a zero-weight subscore touched {name!r}")


def test_the_reference_solution_scores_one(scorer):
    task = bench_tasks.load_task(SEED)
    score = scorer.score(task, task.reference_project)
    _assert_statuses(score)
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
    detail = geometry["detail"]["parts"]["spacer_plate"]
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
    _assert_statuses(score)
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
    _assert_statuses(score)
    geometry = score["subscores"]["geometry"]
    # One target part, and it is mesh-only, so EVERY part is: the subscore's
    # own status says so, and the part is named.
    assert geometry["status"] == "skipped_mesh"
    assert geometry["value"] == 0.0
    assert geometry["detail"]["skipped_mesh"] == ["spacer_plate"]
    assert geometry["detail"]["parts"]["spacer_plate"]["reason"] == \
        "candidate_is_a_mesh"
    # Included, at its declared weight: the renormalised weights still name it.
    assert geometry["weight"] == 0.5
    assert "geometry" in score["weights_effective"]
    # Critical 3: a mesh-only candidate has no `parts/<id>.py`, so the rubric
    # attaches nowhere. That is the candidate's doing — a zero, NOT an
    # exclusion that renormalises the other subscores upwards.
    specs = score["subscores"]["specs"]
    assert specs["status"] == "ok"
    assert specs["value"] == 0.0
    assert specs["detail"]["reason"] == "no_rubric_attached"
    assert set(score["weights_effective"]) == {"built", "valid", "specs",
                                               "geometry", "metrics"}


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


# ------------------------------------ rule 2: the candidate is never excluded
# `error` is the HARNESS failing to measure. Every one of these is a candidate
# failing, and every one of them must be a measured zero at full weight — an
# exclusion renormalises the remaining weights and PAYS the candidate for
# destroying the evidence.

_SECOND_PART = '''from build123d import *

PARAMS = {"size": {"default": 10.0, "min": 1.0, "max": 100.0}}


def build(p):
    return Box(p.size, p.size, p.size)
'''

_FILLER_PART = '''from build123d import *
from agentcad.toolkit.specs import check_valid

PARAMS = {"size": {"default": 5.0, "min": 1.0, "max": 50.0}}


def build(p):
    return Box(p.size, p.size, p.size)


SPECS = [check_valid(name="filler")]
'''


def _bundle(tmp_path, **edits):
    """The seed bundle copied into tmp with `task.json` keys replaced.

    `benchmarks/` is a read-only input, so a test that needs a *different*
    task shape copies it rather than editing it.
    """
    seed = bench_tasks.load_task(SEED)
    root = tmp_path / "tasks"
    dst = root / "model_from_drawing" / "mfd_001_spacer_plate"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(seed.root, dst)
    doc = json.loads((dst / "task.json").read_text())
    doc.update(edits)
    (dst / "task.json").write_text(json.dumps(doc, indent=2))
    return root, dst


def _add_part(project, part_id, script, **entry):
    (project / "parts" / f"{part_id}.py").write_text(script)
    manifest = project / "project.json"
    doc = json.loads(manifest.read_text())
    doc["parts"].append({"id": part_id, "label": part_id,
                         "material": "al6061",
                         "params": {"size": 10.0}, **entry})
    manifest.write_text(json.dumps(doc, indent=2))


def _two_part_task(tmp_path):
    """A two-part task with geometry switched off (no second STEP to author).

    The weights still sum to 1.0, so `weights_effective` must come back equal
    to them: any renormalisation at all is the bug.
    """
    root, bundle = _bundle(
        tmp_path,
        target={"project": "bench_mfd_001_spacer_plate",
                "parts": ["spacer_plate", "second_plate"]},
        reference={"project": "reference/project", "steps": {},
                   "metrics": "reference/metrics.json"},
        weights={"built": 0.3, "valid": 0.2, "specs": 0.2, "geometry": 0.0,
                 "interference": 0.0, "metrics": 0.3})
    _add_part(bundle / "reference" / "project", "second_plate", _SECOND_PART)
    return bench_tasks.load_task(SEED, root=root)


def test_a_deleted_part_script_is_a_zero_and_never_renormalises(scorer,
                                                                tmp_path):
    """Critical 2: `_ensure_built` raises `NotFoundError` out of
    `store.read_script` for a part whose script the candidate deleted. As a
    bare `except Exception` that marked `built`/`valid`/`specs`/`metrics`
    `error` and handed the candidate a `weights_effective` of one subscore.
    """
    task = _two_part_task(tmp_path)
    submission = tmp_path / "submission"
    shutil.copytree(task.reference_project, submission)
    (submission / "parts" / "second_plate.py").unlink()

    score = scorer.score(task, submission)
    _assert_statuses(score)
    assert score["subscores"]["built"]["status"] == "ok"
    assert score["subscores"]["built"]["value"] == pytest.approx(0.5)
    assert score["subscores"]["built"]["detail"]["failed"] == ["second_plate"]
    assert score["subscores"]["valid"]["value"] == pytest.approx(0.5)
    # `SpecRunner.run` refuses over the WHOLE project when one part's script
    # file is gone, so the rubric never gets measured — a zero the candidate
    # earned, at full weight, and not an exclusion.
    specs = score["subscores"]["specs"]
    assert specs["status"] == "ok"
    assert specs["value"] == 0.0
    assert specs["detail"]["reason"] == "spec_run_refused"
    assert score["subscores"]["metrics"]["value"] == pytest.approx(1.0)
    # NOT renormalised: the four scored subscores keep their declared weights.
    assert score["weights_effective"] == pytest.approx(
        {"built": 0.3, "valid": 0.2, "specs": 0.2, "metrics": 0.3})
    assert score["total"] == pytest.approx(0.3 * 0.5 + 0.2 * 0.5 + 0.3)


def test_filler_parts_cannot_dilute_the_specs_denominator(scorer, tmp_path):
    """Critical 4: the denominator is the injected rubric's rows, nothing else.

    Measured on the seed before the fix: nine filler parts each declaring one
    trivially-true check moved a real 2/3 to 11/12.
    """
    task = bench_tasks.load_task(SEED)
    flawed = _reference_copy(task, tmp_path)
    manifest = flawed / "project.json"
    manifest.write_text(manifest.read_text().replace('"thickness": 6.0',
                                                     '"thickness": 12.0'))
    for index in range(2):
        _add_part(flawed, f"filler_{index}", _FILLER_PART)

    score = scorer.score(task, flawed)
    specs = score["subscores"]["specs"]
    assert specs["detail"]["total"] == 3          # the rubric's three, only
    assert specs["value"] == pytest.approx(2 / 3)
    assert specs["detail"]["failed"] == ["spacer_plate:envelope"]
    assert not any(row.startswith("filler_")
                   for row in specs["detail"]["failed"]
                   + specs["detail"]["skipped"] + specs["detail"]["errors"])


def test_a_candidate_authored_specs_py_never_scores(scorer, tmp_path):
    """Critical 4, second half: the seed ships no `specs/project.py`, so the
    copy's `specs.py` is DELETED — a candidate cannot bring its own project
    rubric to a task that declared none."""
    task = bench_tasks.load_task(SEED)
    flawed = _reference_copy(task, tmp_path)
    (flawed / "specs.py").write_text(
        "from agentcad.toolkit.specs import check_valid\n"
        "SPECS = [check_valid(name='self_awarded')]\n")

    score = scorer.score(task, flawed)
    specs = score["subscores"]["specs"]
    assert specs["detail"]["total"] == 3
    assert specs["value"] == pytest.approx(1.0)
    rows = (specs["detail"]["failed"] + specs["detail"]["skipped"]
            + specs["detail"]["errors"])
    assert not any(row.startswith("project:") for row in rows)


def test_a_non_utf8_part_script_is_a_zeroed_score_not_a_traceback(scorer,
                                                                  tmp_path):
    """Important 5: `inject_rubric` reads the candidate's script as UTF-8, and
    `UnicodeDecodeError` is a `ValueError` — not an `OSError`. Outside the
    guard it came straight out of `score()`."""
    task = bench_tasks.load_task(SEED)
    flawed = _reference_copy(task, tmp_path)
    (flawed / "parts" / "spacer_plate.py").write_bytes(b"\xff\xfe not utf-8")

    score = scorer.score(task, flawed)
    _assert_statuses(score)
    assert score["total"] == 0.0
    for name in ("built", "valid", "specs", "geometry", "metrics"):
        assert score["subscores"][name]["status"] == "ok", name
        assert score["subscores"][name]["value"] == 0.0, name
    assert any("could not be opened" in note for note in score["notes"])


def test_a_submission_path_never_reaches_the_document(scorer, tmp_path):
    """Important 6: an `OSError` names the path it failed on, and the
    submission root is a path `score.json` may not carry."""
    task = bench_tasks.load_task(SEED)
    not_a_directory = tmp_path / "submission.json"
    not_a_directory.write_text("{}")

    score = scorer.score(task, not_a_directory)
    body = canonical_json(score)
    assert b"<submission>" in body
    assert str(tmp_path).encode() not in body
    assert str(task.root).encode() not in body
    assert score["total"] == 0.0


# ------------------------------------------------- kernel-free unit coverage
# The arithmetic below has no reference task yet (no shipped task weights
# `interference`, and the seed's windows are all satisfied), so it is tested
# directly over dicts rather than waiting for Tasks 8-10 to author one.


@pytest.fixture
def bare_scorer():
    """A scorer with no service at all: every method here takes the service it
    measures through as an argument, so none of this needs a kernel."""
    return Scorer(SimpleNamespace())


def _interference_task(task, **kwargs):
    return _weighted(task, interference=1.0)


def test_a_broken_candidate_assembly_is_a_zero_not_an_exclusion(bare_scorer):
    """Critical 1: `check_interference` resolves the CANDIDATE's assembly, so
    a part that raises or an instance naming a part that is gone comes back as
    an exception the candidate caused. As a blanket `error` that excluded the
    subscore and renormalised the rest upwards — measured on the seed, one
    broken extra part took a 0.8 to a 1.0."""
    task = _interference_task(bench_tasks.load_task(SEED))

    def refusing(exc):
        def raise_it(*args, **kwargs):
            raise exc
        return SimpleNamespace(check_interference=raise_it)

    for exc in (KernelError("script_error", "name 'Bx' is not defined"),
                KernelError("kernel_error", "OCCT could not resolve"),
                NotFoundError("part 'gone' not found in project 'p'")):
        row = bare_scorer._interference(refusing(exc), task, "p", None, [])
        assert row["status"] == "ok", exc
        assert row["value"] == 0.0
        assert row["detail"]["reason"] == "assembly_unresolved"

    # Ours, and only ours: a timeout and a dead worker are the harness.
    for exc in (KernelError("timeout", "the worker did not answer"),
                KernelError("kernel_crash", "worker unreachable")):
        row = bare_scorer._interference(refusing(exc), task, "p", None, [])
        assert row["status"] == "error", exc


def test_interference_fraction_counts_an_unmeasurable_pair_as_unclean():
    # 4 instances = 6 pairs, one overlapping pair reported: 5 clean.
    assert interference_fraction(4, 1, 0) == pytest.approx(5 / 6)
    # One instance skipped as a mesh: the 3 pairs touching it are not clean
    # either, so only C(3,2) = 3 pairs are measurable and 2 of them are clean.
    assert interference_fraction(4, 1, 1) == pytest.approx(2 / 6)
    # A clean four-part assembly.
    assert interference_fraction(4, 0, 0) == 1.0
    # Fewer than two instances cannot be a clean assembly: the task asked for
    # one and got none.
    assert interference_fraction(1, 0, 0) == 0.0
    assert interference_fraction(0, 0, 0) == 0.0
    # Never below zero, whatever the kernel reports.
    assert interference_fraction(2, 5, 0) == 0.0


def test_the_interference_subscore_sorts_and_names_what_it_measured(
        bare_scorer):
    task = _interference_task(bench_tasks.load_task(SEED))
    service = SimpleNamespace(check_interference=lambda *a, **k: {
        "checked": 3,
        "pairs": [{"a": "z", "b": "y", "volume_mm3": 2.0},
                  {"a": "a", "b": "b", "volume_mm3": 1.0}],
        "skipped_mesh": ["mesh_one"]})
    row = bare_scorer._interference(service, task, "p", None, [])
    assert row["status"] == "ok"
    # 3 instances = 3 pairs; one is a mesh, so only C(2,2)=1 pair is
    # measurable and both reported overlaps are against it: 0 clean.
    assert row["value"] == 0.0
    assert [pair["a"] for pair in row["detail"]["pairs"]] == ["a", "z"]
    assert row["detail"]["skipped_mesh"] == ["mesh_one"]


def test_fewer_than_two_instances_is_a_zero_with_a_reason(bare_scorer):
    task = _interference_task(bench_tasks.load_task(SEED))
    service = SimpleNamespace(
        check_interference=lambda *a, **k: {"pairs": [], "checked": 1})
    row = bare_scorer._interference(service, task, "p", None, [])
    assert (row["status"], row["value"]) == ("ok", 0.0)
    assert row["detail"]["reason"] == "no_pairs"


def test_metric_of_derives_the_bbox_and_com_axes():
    metrics = {"volume_mm3": 12.5, "bbox": {"min": [0.0, -1.0, 2.0],
                                            "max": [3.0, 1.0, 8.0]},
               "center_of_mass": [1.0, 2.0, 3.0]}
    assert metric_of(metrics, "volume_mm3") == 12.5
    assert metric_of(metrics, "bbox_x_mm") == 3.0
    assert metric_of(metrics, "bbox_y_mm") == 2.0
    assert metric_of(metrics, "bbox_z_mm") == 6.0
    assert metric_of(metrics, "com_z_mm") == 3.0
    # A metric this build does not carry is not a number and not an error.
    assert metric_of(metrics, "n_faces") is None
    assert metric_of({}, "bbox_z_mm") is None
    assert metric_of({}, "com_x_mm") is None


def test_window_bounds_are_inclusive_within_slack():
    window = bench_tasks.MetricWindow("w", "p", "volume_mm3", 1.0, 100.0)
    assert window_satisfied(window, 100.0)          # inclusive at the ceiling
    assert window_satisfied(window, 1.0)            # and at the floor
    assert window_satisfied(window, 100.0 + 1e-10)  # `_slack`, not the ulp
    assert not window_satisfied(window, 100.5)
    assert not window_satisfied(window, 0.5)
    assert not window_satisfied(window, None)       # no number is not a pass
    assert not window_satisfied(window, float("nan"))
    one_sided = bench_tasks.MetricWindow("w", "p", "volume_mm3", None, 10.0)
    assert window_satisfied(one_sided, -1e9)        # no floor means no floor
    floor_only = bench_tasks.MetricWindow("w", "p", "volume_mm3", 10.0, None)
    assert window_satisfied(floor_only, 1e9)


def test_the_metrics_subscore_over_synthetic_builds(bare_scorer, tmp_path):
    doc = tmp_path / "windows.json"
    doc.write_text(json.dumps({"schema": 1, "windows": [
        {"name": "a_exact_max", "part": "p1", "metric": "volume_mm3",
         "max": 100.0},
        {"name": "b_min_only", "part": "p1", "metric": "mass_g", "min": 1.0},
        {"name": "c_derived", "part": "p1", "metric": "bbox_z_mm",
         "min": 3.0, "max": 3.0},
        {"name": "d_missing_metric", "part": "p1", "metric": "n_faces",
         "min": 1.0},
        {"name": "e_unbuilt_part", "part": "p2", "metric": "volume_mm3",
         "min": 1.0},
    ]}))
    task = dataclasses.replace(_weighted(bench_tasks.load_task(SEED),
                                         metrics=1.0), metrics_path=doc)
    builds = {
        "p1": {"state": "ok", "reason": None, "result": {"metrics": {
            "volume_mm3": 100.0, "mass_g": 2.0,
            "bbox": {"min": [0.0, 0.0, 0.0], "max": [1.0, 2.0, 3.0]}}}},
        "p2": {"state": "failed", "reason": "build_failed", "result": None},
    }
    row = bare_scorer._metrics(task, builds)
    assert row["status"] == "ok"
    assert row["value"] == pytest.approx(3 / 5)
    assert [item["name"] for item in row["detail"]["failed"]] == [
        "d_missing_metric", "e_unbuilt_part"]
    # A window with no measurement carries `null`, never a fabricated number.
    assert row["detail"]["failed"][0]["measured"] is None


def test_a_harness_build_error_is_the_only_metrics_error(bare_scorer,
                                                         tmp_path):
    doc = tmp_path / "windows.json"
    doc.write_text(json.dumps({"schema": 1, "windows": [
        {"name": "w", "part": "p1", "metric": "volume_mm3", "min": 1.0}]}))
    task = dataclasses.replace(_weighted(bench_tasks.load_task(SEED),
                                         metrics=1.0), metrics_path=doc)
    failed = {"p1": {"state": "failed", "reason": "build_failed",
                     "result": None}}
    assert bare_scorer._metrics(task, failed)["status"] == "ok"
    errored = {"p1": {"state": "error", "result": None,
                      "error": {"type": "timeout", "message": "gone"}}}
    assert bare_scorer._metrics(task, errored)["status"] == "error"


def test_a_budget_shortens_the_kernel_timeout_and_names_the_truncation(
        bare_scorer):
    ceiling = 300.0
    assert bare_scorer._timeout(None, ceiling) == (ceiling, None)
    timeout_s, remaining = bare_scorer._timeout(time.monotonic() + 10.0,
                                                ceiling)
    assert timeout_s == pytest.approx(remaining, abs=0.5)
    assert timeout_s < ceiling
    # Already over: the call is floored, never zero or negative.
    timeout_s, remaining = bare_scorer._timeout(time.monotonic() - 5.0,
                                                ceiling)
    assert timeout_s == 1.0 and remaining < 0.0

    timed_out = KernelError("timeout", "no answer")
    # Handed less than its own ceiling, a timeout is the BUDGET, not the shape.
    assert bare_scorer._budget_broke(timed_out, 10.0, ceiling)
    # Handed the full ceiling, it is a fact about the geometry.
    assert not bare_scorer._budget_broke(timed_out, None, ceiling)
    assert not bare_scorer._budget_broke(
        KernelError("kernel_error", "boom"), 10.0, ceiling)


def test_a_zero_weight_subscore_never_touches_the_service(bare_scorer,
                                                          tmp_path):
    """§4.7 is literal: the task decides applicability, and a zeroed subscore
    costs nothing — no kernel call, no spec run, no build."""
    task = _weighted(bench_tasks.load_task(SEED), built=1.0)
    boom = _Explodes()
    rows = {
        "valid": bare_scorer._valid(task, {}),
        "specs": bare_scorer._specs(boom, task, "p", [], None, []),
        "geometry": bare_scorer._geometry(boom, task, "p", {}, None, []),
        "interference": bare_scorer._interference(boom, task, "p", None, []),
        "metrics": bare_scorer._metrics(task, {}),
    }
    for name, row in rows.items():
        assert row["status"] == "not_applicable", name
        assert row["weight"] == 0.0 and row["value"] == 0.0, name
        assert row["detail"] == {"reason": "weight_zero"}, name


def test_no_part_is_built_when_no_subscore_reads_a_build(bare_scorer):
    """The build itself is a kernel call, so it is under §4.7 too."""
    def never(proj):
        raise AssertionError("a part was built for a subscore nobody scores")

    task = _weighted(bench_tasks.load_task(SEED), interference=1.0)
    service = SimpleNamespace(
        store=SimpleNamespace(part_ids=never), specs=None,
        check_interference=lambda *a, **k: {"pairs": [], "checked": 0})
    subscores = bare_scorer._measure(service, task, "p", [], None, [])
    assert subscores["interference"]["status"] == "ok"
    for name in ("built", "valid", "specs", "geometry", "metrics"):
        assert subscores[name]["status"] == "not_applicable", name


def test_scrub_labels_the_longest_matching_root_first():
    """Important 6: a submission nested inside the projects root must come
    back as `<submission>`, not as `<projects>` plus a leftover tail."""
    from agentcad.bench.scoring import _scrub

    payload = {"notes": ["/roots/projects/sub/project.json is not a project"],
               "detail": {"error": {"message": "cannot read /task/w.json"}}}
    scrubbed = _scrub(payload, (("/roots/projects", "<projects>"),
                                ("/roots/projects/sub", "<submission>"),
                                ("/task", "<task>")))
    assert scrubbed["notes"] == ["<submission>/project.json is not a project"]
    assert scrubbed["detail"]["error"]["message"] == \
        "cannot read <task>/w.json"


def test_an_unreadable_window_document_names_the_task_root_as_a_token(
        bare_scorer, tmp_path):
    """The other half of Important 6: `load_windows` raises with the TASK
    tree's path in the message, and the task tree is a path `score.json` may
    not carry."""
    from agentcad.bench.scoring import _scrub

    task = bench_tasks.load_task(SEED)
    scored = dataclasses.replace(_weighted(task, metrics=1.0),
                                 metrics_path=task.root / "nope.json")
    row = bare_scorer._metrics(scored, {})
    assert row["status"] == "error"
    assert str(task.root) in row["detail"]["error"]["message"]
    cleaned = _scrub(row, ((task.root, "<task>"),))
    assert str(task.root) not in cleaned["detail"]["error"]["message"]
    assert "<task>" in cleaned["detail"]["error"]["message"]
