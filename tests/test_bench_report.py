"""`bench report`: aggregation, the missing-task rule and the baseline gate.

Every fixture here is a hand-written `score.json` / `run.json` / `bench.json`
in `tmp_path` -- the shapes design §6 and §8.6 define -- so the whole module is
offline, kernel-free and parallel-safe. `benchmarks/` is read (never written)
in exactly one test, and only through `shutil.copytree` into `tmp_path`.
"""
import shutil
from pathlib import Path

import pytest

from agentcad.bench import report as bench_report
from agentcad.bench._json import canonical_json, read_json, write_json
from agentcad.core.model import ValidationError

REPO = Path(__file__).resolve().parents[1]


def _score(task_id, total, category):
    return {"schema": 1, "agentcad": "0.1.0", "harness": 1, "task": task_id,
            "task_set": "bench-v1", "task_version": 1, "category": category,
            "total": total, "weights_effective": {"built": 1.0},
            "subscores": {"built": {"value": total, "weight": 1.0,
                                    "status": "ok", "detail": {}}},
            "notes": []}


def _results(tmp_path, rows):
    for task_id, total, category in rows:
        out = tmp_path / "tasks" / task_id
        write_json(out / "score.json", _score(task_id, total, category))
        write_json(out / "run.json", {"schema": 1, "task": task_id,
                                      "over_budget": False, "agent": "builtin",
                                      "model": "m", "stopped": "model_ended_turn"})
    write_json(tmp_path / "bench.json", {"schema": 1, "agent": "builtin",
                                         "model": "m", "task_set": "bench-v1",
                                         "harness": 1, "agentcad": "0.1.0"})
    return tmp_path


# ------------------------------------------------------------- aggregation

def test_overall_total_is_the_mean_of_category_means(tmp_path):
    results = _results(tmp_path, [
        ("model_from_drawing/a", 1.0, "model_from_drawing"),
        ("model_from_drawing/b", 0.0, "model_from_drawing"),
        ("modify_to_spec/c", 1.0, "modify_to_spec"),
    ])
    report = bench_report.aggregate(results)
    assert report["categories"]["model_from_drawing"]["total"] == pytest.approx(0.5)
    assert report["total"] == pytest.approx(0.75)      # mean of {0.5, 1.0}
    assert report["n"] == 3


def test_a_missing_task_is_zero_and_flagged(tmp_path):
    results = _results(tmp_path, [("model_from_drawing/a", 1.0, "model_from_drawing")])
    report = bench_report.aggregate(results, tasks_root=None,
                                    expected=["model_from_drawing/a",
                                              "model_from_drawing/b"])
    assert report["tasks"]["model_from_drawing/b"]["missing"] is True
    assert report["tasks"]["model_from_drawing/b"]["total"] == 0.0
    assert report["categories"]["model_from_drawing"]["missing"] == 1


def test_expected_defaults_to_the_tasks_root_when_one_is_given(tmp_path):
    """A tasks root names the tasks the report *must* cover -- an unrun task
    cannot vanish from the denominator by never having been run."""
    root = tmp_path / "tasks_root"
    seed = "model_from_drawing/mfd_001_spacer_plate"
    shutil.copytree(REPO / "benchmarks" / "tasks" / seed, root / seed)
    results = _results(tmp_path / "out", [])
    report = bench_report.aggregate(results, tasks_root=root)
    assert report["tasks"]["model_from_drawing/mfd_001_spacer_plate"]["missing"]
    assert report["total"] == 0.0
    assert report["n"] == 1


def test_expected_comes_from_bench_jsons_per_task_index(tmp_path):
    """The runner's roster is the denominator: a task it selected and never
    scored is a `missing` row, not a hole in the average."""
    results = _results(tmp_path, [("model_from_drawing/a", 1.0, "model_from_drawing")])
    header = read_json(results / "bench.json")
    header["tasks"] = {"model_from_drawing/a": {"score": "tasks/model_from_drawing/a"},
                       "model_from_drawing/b": {"score": None}}
    write_json(results / "bench.json", header)
    report = bench_report.aggregate(results)
    assert report["tasks"]["model_from_drawing/b"]["missing"] is True
    assert report["n"] == 2 and report["total"] == pytest.approx(0.5)


def test_a_bench_json_index_may_be_a_plain_list_of_ids(tmp_path):
    results = _results(tmp_path, [("model_from_drawing/a", 1.0, "model_from_drawing")])
    header = read_json(results / "bench.json")
    header["tasks"] = ["model_from_drawing/a", "modify_to_spec/b"]
    write_json(results / "bench.json", header)
    report = bench_report.aggregate(results)
    assert report["categories"]["modify_to_spec"]["missing"] == 1
    assert report["total"] == pytest.approx(0.5)     # mean of {1.0, 0.0}


def test_an_explicit_expected_list_outranks_the_index(tmp_path):
    results = _results(tmp_path, [("model_from_drawing/a", 1.0, "model_from_drawing")])
    header = read_json(results / "bench.json")
    header["tasks"] = ["model_from_drawing/a", "modify_to_spec/b"]
    write_json(results / "bench.json", header)
    report = bench_report.aggregate(results, expected=["model_from_drawing/a"])
    assert report["n"] == 1


def test_a_non_finite_score_is_a_harness_error(tmp_path):
    """`json.loads` parses bare `NaN`; every `nan < -epsilon` is False, so an
    accepted NaN is a silently green gate."""
    results = _results(tmp_path, [("model_from_drawing/a", 1.0, "model_from_drawing")])
    path = results / "tasks" / "model_from_drawing" / "a" / "score.json"
    path.write_text(path.read_text().replace('"total": 1.0', '"total": NaN'))
    with pytest.raises(ValidationError):
        bench_report.aggregate(results)


def test_a_non_finite_subscore_is_a_harness_error(tmp_path):
    results = _results(tmp_path, [("model_from_drawing/a", 1.0, "model_from_drawing")])
    path = results / "tasks" / "model_from_drawing" / "a" / "score.json"
    path.write_text(path.read_text().replace('"value": 1.0', '"value": Infinity'))
    with pytest.raises(ValidationError):
        bench_report.aggregate(results)


def test_a_results_directory_with_nothing_in_it_is_a_harness_error(tmp_path):
    """`n: 0, total: 0.0` would answer "we measured nothing" with a number."""
    write_json(tmp_path / "bench.json", {"schema": 1, "task_set": "bench-v1",
                                         "harness": 1})
    with pytest.raises(ValidationError):
        bench_report.aggregate(tmp_path)


def test_a_score_from_another_harness_is_included_and_warned_about(tmp_path):
    results = _results(tmp_path, [("model_from_drawing/a", 1.0, "model_from_drawing")])
    rogue = results / "tasks" / "modify_to_spec" / "z"
    score = _score("modify_to_spec/z", 1.0, "modify_to_spec")
    score["harness"] = 99
    write_json(rogue / "score.json", score)
    report = bench_report.aggregate(results)
    assert report["n"] == 2                                # included
    assert any("modify_to_spec/z" in line and "harness" in line
               for line in report["warnings"])


def test_an_unreadable_score_is_a_harness_error(tmp_path):
    results = _results(tmp_path, [("model_from_drawing/a", 1.0, "model_from_drawing")])
    (results / "tasks" / "model_from_drawing" / "a" / "score.json").write_text("{")
    with pytest.raises(ValidationError):
        bench_report.aggregate(results)


def test_the_report_is_byte_identical_across_writes(tmp_path):
    results = _results(tmp_path, [("model_from_drawing/a", 1.0 / 3.0,
                                   "model_from_drawing")])
    first = bench_report.aggregate(results)
    second = bench_report.aggregate(results)
    assert canonical_json(first) == canonical_json(second)
    write_json(tmp_path / "report.json", first)
    assert read_json(tmp_path / "report.json") == first     # round-trips


# ------------------------------------------------------------- the gate

def test_a_regression_beyond_epsilon_is_exit_one(tmp_path):
    results = _results(tmp_path, [("model_from_drawing/a", 0.5, "model_from_drawing")])
    report = bench_report.aggregate(results)
    baseline = {"schema": 1, "task_set": "bench-v1", "harness": 1,
                "total": 0.9, "categories": {"model_from_drawing": 0.9},
                "tasks": {"model_from_drawing/a": 0.9}}
    report["baseline"] = bench_report.compare_baseline(report, baseline, 0.02)
    assert report["baseline"]["status"] == "regressed"
    scopes = {r["scope"] for r in report["baseline"]["regressions"]}
    assert "total" in scopes and "category:model_from_drawing" in scopes
    assert bench_report.report_exit_code(report) == 1


def test_a_drop_inside_epsilon_is_not_a_regression(tmp_path):
    results = _results(tmp_path, [("model_from_drawing/a", 0.89, "model_from_drawing")])
    report = bench_report.aggregate(results)
    report["baseline"] = bench_report.compare_baseline(
        report, {"schema": 1, "task_set": "bench-v1", "harness": 1, "total": 0.9,
                 "categories": {"model_from_drawing": 0.9}, "tasks": {}}, 0.02)
    assert report["baseline"]["status"] == "ok"
    assert bench_report.report_exit_code(report) == 0


def test_a_baseline_category_the_run_never_produced_is_a_regression(tmp_path):
    results = _results(tmp_path, [("model_from_drawing/a", 1.0, "model_from_drawing")])
    report = bench_report.aggregate(results)
    report["baseline"] = bench_report.compare_baseline(
        report, {"schema": 1, "task_set": "bench-v1", "harness": 1, "total": 0.9,
                 "categories": {"model_from_drawing": 1.0, "modify_to_spec": 0.7},
                 "tasks": {}}, 0.02)
    assert report["baseline"]["status"] == "regressed"
    scopes = {r["scope"] for r in report["baseline"]["regressions"]}
    assert scopes == {"category:modify_to_spec"}


def test_a_per_task_drop_alone_never_gates(tmp_path):
    results = _results(tmp_path, [("model_from_drawing/a", 1.0, "model_from_drawing"),
                                  ("model_from_drawing/b", 0.0, "model_from_drawing")])
    report = bench_report.aggregate(results)
    baseline = {"schema": 1, "task_set": "bench-v1", "harness": 1,
                "total": 0.5, "categories": {"model_from_drawing": 0.5},
                "tasks": {"model_from_drawing/a": 0.0,
                          "model_from_drawing/b": 1.0}}
    report["baseline"] = bench_report.compare_baseline(report, baseline, 0.02)
    assert report["baseline"]["status"] == "ok"
    assert report["baseline"]["task_deltas"]           # printed, not gated
    assert bench_report.report_exit_code(report) == 0


def test_a_non_finite_epsilon_is_refused(tmp_path):
    """A NaN tolerance deletes the gate rather than widening it."""
    results = _results(tmp_path, [("model_from_drawing/a", 0.0, "model_from_drawing")])
    report = bench_report.aggregate(results)
    baseline = {"schema": 1, "task_set": "bench-v1", "harness": 1, "total": 0.9,
                "categories": {}, "tasks": {}}
    with pytest.raises(ValidationError):
        bench_report.compare_baseline(report, baseline, float("nan"))


def test_a_baseline_task_the_run_never_scored_gates(tmp_path):
    """Half a suite must not pass by shrinking its own denominator."""
    results = _results(tmp_path, [("model_from_drawing/a", 0.9, "model_from_drawing")])
    report = bench_report.aggregate(results)
    assert report["n"] == 1                       # the roster is silent on disk
    baseline = {"schema": 1, "task_set": "bench-v1", "harness": 1, "total": 0.9,
                "categories": {"model_from_drawing": 0.9},
                "tasks": {"model_from_drawing/a": 0.9,
                          "model_from_drawing/b": 0.9}}
    report["baseline"] = bench_report.compare_baseline(report, baseline, 0.02)
    assert report["baseline"]["status"] == "regressed"
    coverage = [row for row in report["baseline"]["regressions"]
                if row["scope"] == "coverage"]
    assert coverage and coverage[0]["missing"] == ["model_from_drawing/b"]
    scopes = {row["scope"] for row in report["baseline"]["regressions"]}
    # the uncovered task is folded in at 0.0 *before* the means are gated
    assert {"total", "category:model_from_drawing"} <= scopes
    assert bench_report.report_exit_code(report) == 1
    assert "model_from_drawing/b" in bench_report.render_markdown(report)


def test_a_run_that_covers_the_baseline_exactly_is_exit_zero(tmp_path):
    results = _results(tmp_path, [("model_from_drawing/a", 0.9, "model_from_drawing"),
                                  ("model_from_drawing/b", 0.9, "model_from_drawing")])
    report = bench_report.aggregate(results)
    baseline = {"schema": 1, "task_set": "bench-v1", "harness": 1, "total": 0.9,
                "categories": {"model_from_drawing": 0.9},
                "tasks": {"model_from_drawing/a": 0.9,
                          "model_from_drawing/b": 0.9}}
    report["baseline"] = bench_report.compare_baseline(report, baseline, 0.02)
    assert report["baseline"]["status"] == "ok"
    assert report["baseline"]["regressions"] == []
    assert bench_report.report_exit_code(report) == 0


@pytest.mark.parametrize("measured, gated", [(0.88, False), (0.879999, True)])
def test_the_epsilon_boundary_is_the_reports_own_precision(tmp_path, measured,
                                                           gated):
    """A drop of exactly epsilon passes; epsilon + 1e-6 fails. Raw float
    arithmetic makes 0.9 - 0.88 come out at 0.020000000000000018, which would
    fail a gate whose own table prints -0.0200 against epsilon 0.0200."""
    results = _results(tmp_path, [("model_from_drawing/a", measured,
                                   "model_from_drawing")])
    report = bench_report.aggregate(results)
    report["baseline"] = bench_report.compare_baseline(
        report, {"schema": 1, "task_set": "bench-v1", "harness": 1, "total": 0.9,
                 "categories": {"model_from_drawing": 0.9}, "tasks": {}}, 0.02)
    assert (report["baseline"]["status"] == "regressed") is gated
    assert bench_report.report_exit_code(report) == (1 if gated else 0)


def test_a_category_the_baseline_does_not_record_is_warned_about(tmp_path):
    results = _results(tmp_path, [("model_from_drawing/a", 1.0, "model_from_drawing"),
                                  ("modify_to_spec/b", 1.0, "modify_to_spec")])
    report = bench_report.aggregate(results)
    report["baseline"] = bench_report.compare_baseline(
        report, {"schema": 1, "task_set": "bench-v1", "harness": 1, "total": 1.0,
                 "categories": {"model_from_drawing": 1.0}, "tasks": {}}, 0.02)
    assert report["baseline"]["status"] == "ok"
    assert any("modify_to_spec" in line
               for line in report["baseline"]["warnings"])


def test_a_harness_mismatch_is_exit_two(tmp_path):
    results = _results(tmp_path, [("model_from_drawing/a", 1.0, "model_from_drawing")])
    report = bench_report.aggregate(results)
    baseline = {"schema": 1, "task_set": "bench-v1", "harness": 99, "total": 1.0,
                "categories": {}, "tasks": {}}
    report["baseline"] = bench_report.compare_baseline(report, baseline, 0.02)
    assert report["baseline"]["status"] == "incomparable"
    assert bench_report.report_exit_code(report) == 2


def test_a_null_baseline_total_is_a_no_op(tmp_path):
    results = _results(tmp_path, [("model_from_drawing/a", 0.1, "model_from_drawing")])
    report = bench_report.aggregate(results)
    report["baseline"] = bench_report.compare_baseline(
        report, {"schema": 1, "task_set": "bench-v1", "harness": 1,
                 "total": None, "categories": {}, "tasks": {}}, 0.02)
    assert report["baseline"]["status"] == "unrecorded"
    assert bench_report.report_exit_code(report) == 0


def test_the_shipped_baseline_is_unrecorded_and_exits_zero(tmp_path):
    baseline = read_json(REPO / "benchmarks" / "baseline.json")
    assert baseline["schema"] == bench_report.BASELINE_SCHEMA
    assert baseline["total"] is None and baseline["harness"] == 1
    results = _results(tmp_path, [("model_from_drawing/a", 0.1, "model_from_drawing")])
    report = bench_report.aggregate(results)
    report["baseline"] = bench_report.compare_baseline(report, baseline, 0.02,
                                                       path="benchmarks/baseline.json")
    assert report["baseline"]["status"] == "unrecorded"
    assert report["baseline"]["path"] == "benchmarks/baseline.json"
    assert bench_report.report_exit_code(report) == 0


# ------------------------------------------------------------- rendering

def test_markdown_renders_the_category_table(tmp_path):
    report = bench_report.aggregate(_results(
        tmp_path, [("model_from_drawing/a", 1.0, "model_from_drawing")]))
    text = bench_report.render_markdown(report)
    assert "model_from_drawing" in text and "1.0" in text


def test_markdown_caps_the_task_rows_and_says_so(tmp_path):
    rows = [(f"model_from_drawing/t{index:03d}", index / 100.0,
             "model_from_drawing") for index in range(60)]
    report = bench_report.aggregate(_results(tmp_path, rows))
    text = bench_report.render_markdown(report)
    assert "more" in text
    assert text.count("| `model_from_drawing/t") == bench_report.MAX_RENDERED_TASKS


def test_markdown_names_every_regression(tmp_path):
    report = bench_report.aggregate(_results(
        tmp_path, [("model_from_drawing/a", 0.1, "model_from_drawing")]))
    report["baseline"] = bench_report.compare_baseline(
        report, {"schema": 1, "task_set": "bench-v1", "harness": 1, "total": 0.9,
                 "categories": {"model_from_drawing": 0.9},
                 "tasks": {"model_from_drawing/a": 0.9}}, 0.02)
    text = bench_report.render_markdown(report)
    assert "regressed" in text and "category:model_from_drawing" in text
    assert "None" not in text                  # an unnamed baseline path


def test_markdown_never_promises_an_unwritten_report_json(tmp_path):
    rows = [(f"model_from_drawing/t{index:03d}", index / 100.0,
             "model_from_drawing") for index in range(60)]
    text = bench_report.render_markdown(bench_report.aggregate(_results(tmp_path, rows)))
    assert "--json-out" in text and "see report.json" not in text
