"""``agentcad bench`` — the CLI surface (PRD-024, Task 4).

What is pinned here is the *contract at the process boundary*, the way
``tests/test_checks_cli.py`` pins ``agentcad check``'s:

* **The exit code is the API** (design §9.3). ``bench score``: ``0`` a score
  was produced · ``2`` harness — an unknown task, an unscoreable submission,
  every subscore excluded. There is deliberately no ``1``: a low score is a
  *measurement*, and only ``bench report --baseline`` gates.
* **``_build_service`` grew exactly one keyword-only parameter.** The default
  keeps every existing caller byte-identical; ``examples=False`` is the bench's,
  so a task derived from a bundled example is not solvable by opening it.
* **A refused ``--work-dir`` leaves nothing behind**, and the kernel is stopped
  and the work root released on every path.
* **stdout is a contract, stderr is for humans**: ``--json`` puts the score
  alone on stdout, ``--quiet`` puts nothing anywhere, and neither moves the
  exit code.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from agentcad import cli as agentcad_cli

SEED = "model_from_drawing/mfd_001_spacer_plate"


def _run(argv):
    """Drive main() the way a shell would; returns the SystemExit code."""
    with patch.object(sys, "argv", ["agentcad", *argv]):
        with pytest.raises(SystemExit) as exc:
            agentcad_cli.main()
    return exc.value.code


# ------------------------------------------------- edit 1: examples=False

def test_build_service_examples_flag_defaults_to_registering_them(tmp_path):
    service = agentcad_cli._build_service(tmp_path / "projects")
    try:
        assert any(p["name"] == "prototyping" for p in service.list_projects())
    finally:
        service.kernel.stop()
        agentcad_cli._release_work_root(service)


def test_build_service_examples_false_registers_none(tmp_path):
    service = agentcad_cli._build_service(tmp_path / "projects", examples=False)
    try:
        assert service.list_projects() == []
    finally:
        service.kernel.stop()
        agentcad_cli._release_work_root(service)


# ------------------------------------------------------------ bench score

@pytest.mark.timeout(900)
def test_bench_score_writes_a_score_and_exits_zero(tmp_path, monkeypatch):
    """AC1 through the process boundary: the reference project scores 1.0.

    The same run pins the cleanup contract, because it is the only test with a
    real service to look at: the kernel is stopped, the work root is gone and
    the throwaway projects root is gone — on the success path too, not only
    when something raised.
    """
    from agentcad.bench import cli as bench_cli
    from agentcad.bench import tasks as bench_tasks

    built = []
    real = bench_cli.bench_service

    def _spy(projects_dir, **kwargs):
        service = real(projects_dir, **kwargs)
        built.append(service)
        return service

    monkeypatch.setattr(bench_cli, "bench_service", _spy)

    task = bench_tasks.load_task(SEED)
    out = tmp_path / "out"
    code = _run(["bench", "score", str(task.reference_project), "--task", SEED,
                 "--out", str(out), "--quiet"])
    assert code == 0
    score = json.loads((out / "score.json").read_text())
    assert score["task"] == SEED
    assert score["total"] == pytest.approx(1.0, abs=1e-9)

    service = built[0]
    assert not service.kernel.alive
    assert not Path(service.work_root).exists()
    assert not Path(service.store.root).exists()


def test_bench_score_registers_no_examples(tmp_path, monkeypatch):
    """A task derived from `examples/prototyping` must not be solvable by
    opening `examples/prototyping` (design §8.2). The catalog stays."""
    from agentcad.bench import cli as bench_cli

    seen = {}

    def _fake_build(projects_dir, extra_writable=None, *, posture=None,
                    examples=True):
        seen["examples"] = examples
        raise RuntimeError("stop here — the flag is the whole assertion")

    monkeypatch.setattr("agentcad.cli._build_service", _fake_build)
    with pytest.raises(RuntimeError):
        bench_cli.bench_service(tmp_path)
    assert seen["examples"] is False


def test_bench_score_json_and_quiet_are_the_check_conventions(capsys):
    """stdout is a contract, stderr is for humans — without a kernel."""
    from types import SimpleNamespace

    from agentcad.bench import cli as bench_cli
    from agentcad.bench._json import canonical_json

    score = {"schema": 1, "task": "model_from_drawing/a", "category":
             "model_from_drawing", "task_set": "bench-v1", "task_version": 1,
             "harness": 1, "agentcad": "0.1.0", "total": 0.5,
             "weights_effective": {"built": 1.0},
             "subscores": {"built": {"value": 0.5, "weight": 0.3,
                                     "status": "ok", "detail": {}}},
             "notes": []}

    bench_cli._print_score(SimpleNamespace(quiet=True, json=False), score, [])
    assert capsys.readouterr() == ("", "")

    bench_cli._print_score(SimpleNamespace(quiet=False, json=True), score, [])
    captured = capsys.readouterr()
    assert captured.out == canonical_json(score).decode()
    assert captured.err == ""

    bench_cli._print_score(SimpleNamespace(quiet=False, json=False), score,
                           ["/tmp/score.json"])
    captured = capsys.readouterr()
    assert "built" in captured.err and "wrote /tmp/score.json" in captured.err
    assert captured.out.startswith("bench score: model_from_drawing/a — 0.5000")


def test_bench_score_refuses_a_non_finite_budget(capsys):
    assert _run(["bench", "score", ".", "--task", SEED, "--budget", "nan"]) == 2
    assert "--budget" in capsys.readouterr().err


def test_bench_score_unknown_task_is_exit_two(tmp_path, capsys):
    code = _run(["bench", "score", str(tmp_path), "--task", "nope/nope",
                 "--quiet"])
    assert code == 2
    assert "nope/nope" in capsys.readouterr().err


def test_bench_score_refuses_a_work_dir_inside_the_submission(tmp_path, capsys):
    from agentcad.bench import tasks as bench_tasks

    task = bench_tasks.load_task(SEED)
    inside = tmp_path / "sub"
    shutil.copytree(task.reference_project, inside)
    code = _run(["bench", "score", str(inside), "--task", SEED,
                 "--work-dir", str(inside / "cell"), "--quiet"])
    assert code == 2
    assert "overlaps" in capsys.readouterr().err
    assert not (inside / "cell").exists()   # a refused path leaves nothing behind


def test_bench_help_lists_the_four_subcommands(capsys):
    # `_run` already absorbs argparse's SystemExit; `--help` exits 0.
    assert _run(["bench", "--help"]) == 0
    text = capsys.readouterr().out
    for name in ("run", "score", "report", "publish"):
        assert name in text


def test_bench_with_no_subcommand_is_exit_two(capsys):
    assert _run(["bench"]) == 2
    assert "pick a subcommand" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["run", "publish"])
def test_run_and_publish_refuse_honestly_for_now(tmp_path, capsys, command):
    """A stub that exited 0 would read to CI as 'the suite ran'."""
    argv = {"run": ["bench", "run", "--report", str(tmp_path / "out")],
            "publish": ["bench", "publish", str(tmp_path / "l.json")]}[command]
    assert _run(argv) == 2
    assert "not implemented in this slice" in capsys.readouterr().err


# ----------------------------------------------------------- bench report

def _results(root: Path, rows) -> Path:
    """A results directory of the shape `bench run` writes (design §8.7)."""
    from agentcad.bench._json import write_json

    for task_id, total in rows:
        out = root / "tasks" / task_id
        write_json(out / "score.json", {
            "schema": 1, "agentcad": "0.1.0", "harness": 1, "task": task_id,
            "task_set": "bench-v1", "task_version": 1,
            "category": task_id.split("/")[0], "total": total,
            "weights_effective": {"built": 1.0},
            "subscores": {"built": {"value": total, "weight": 1.0,
                                    "status": "ok", "detail": {}}},
            "notes": []})
        write_json(out / "run.json", {"schema": 1, "task": task_id,
                                      "over_budget": False, "agent": "builtin",
                                      "model": "m",
                                      "stopped": "model_ended_turn"})
    write_json(root / "bench.json", {"schema": 1, "agent": "builtin",
                                     "model": "m", "task_set": "bench-v1",
                                     "harness": 1, "agentcad": "0.1.0"})
    return root


def _baseline(path: Path, payload) -> Path:
    from agentcad.bench._json import write_json

    write_json(path, payload)
    return path


def test_bench_report_writes_both_outputs_and_exits_zero(tmp_path, capsys):
    results = _results(tmp_path / "results", [("model_from_drawing/a", 1.0)])
    code = _run(["bench", "report", str(results),
                 "--json-out", str(tmp_path / "report.json"),
                 "--md", str(tmp_path / "report.md")])
    assert code == 0
    document = json.loads((tmp_path / "report.json").read_text())
    assert document["total"] == pytest.approx(1.0)
    assert "model_from_drawing" in (tmp_path / "report.md").read_text()
    captured = capsys.readouterr()
    assert "model_from_drawing" in captured.err
    assert captured.out.startswith("bench report: 1.0000")


def test_bench_report_needs_no_kernel(tmp_path, monkeypatch):
    """`bench report` is pure: a CI job runs it on a machine that has never
    built a solid, so the command may not construct a service."""
    from agentcad.bench import cli as bench_cli

    monkeypatch.setattr(bench_cli, "bench_service", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("bench report built a service")))
    results = _results(tmp_path / "results", [("model_from_drawing/a", 1.0)])
    assert _run(["bench", "report", str(results), "--quiet"]) == 0


def test_bench_report_regression_beyond_epsilon_is_exit_one(tmp_path, capsys):
    results = _results(tmp_path / "results", [("model_from_drawing/a", 0.5)])
    baseline = _baseline(tmp_path / "baseline.json",
                         {"schema": 1, "task_set": "bench-v1", "harness": 1,
                          "total": 0.9,
                          "categories": {"model_from_drawing": 0.9},
                          "tasks": {"model_from_drawing/a": 0.9}})
    code = _run(["bench", "report", str(results), "--baseline", str(baseline),
                 "--epsilon", "0.02"])
    assert code == 1
    captured = capsys.readouterr()
    assert "regressions" in captured.err
    assert "baseline regressed" in captured.out


def test_bench_report_incomparable_baseline_is_exit_two(tmp_path):
    results = _results(tmp_path / "results", [("model_from_drawing/a", 1.0)])
    baseline = _baseline(tmp_path / "baseline.json",
                         {"schema": 1, "task_set": "bench-v1", "harness": 99,
                          "total": 1.0, "categories": {}, "tasks": {}})
    assert _run(["bench", "report", str(results), "--baseline", str(baseline),
                 "--quiet"]) == 2


def test_bench_report_ships_baseline_is_unrecorded_and_green(tmp_path, capsys):
    """The shipped `benchmarks/baseline.json` must not turn CI red before a
    single number exists."""
    results = _results(tmp_path / "results", [("model_from_drawing/a", 0.1)])
    shipped = Path(__file__).resolve().parents[1] / "benchmarks" / "baseline.json"
    code = _run(["bench", "report", str(results), "--baseline", str(shipped)])
    assert code == 0
    assert "baseline unrecorded" in capsys.readouterr().out


def test_bench_report_unreadable_results_is_exit_two(tmp_path, capsys):
    assert _run(["bench", "report", str(tmp_path / "nope"), "--quiet"]) == 2
    assert "agentcad bench report" in capsys.readouterr().err


def test_bench_report_refuses_a_non_finite_epsilon(tmp_path, capsys):
    assert _run(["bench", "report", str(tmp_path), "--epsilon", "inf"]) == 2
    assert "--epsilon" in capsys.readouterr().err


def test_bench_report_json_is_the_document_on_stdout(tmp_path, capsys):
    from agentcad.bench._json import canonical_json

    results = _results(tmp_path / "results", [("model_from_drawing/a", 1.0)])
    assert _run(["bench", "report", str(results), "--json"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == canonical_json(json.loads(captured.out)).decode()
