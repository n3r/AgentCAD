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


@pytest.fixture(autouse=True)
def _restore_client_id():
    """`locks.set_client_id` writes a **ContextVar**, and these tests drive
    `main()` in-process: without this every test after the first `bench score`
    would run as `"bench"` and a neighbour asserting on lock ownership would
    fail for a reason nothing in its own body explains."""
    from agentcad.core import locks

    before = locks.current_client_id()
    try:
        yield
    finally:
        locks.set_client_id(before)


def _run(argv):
    """Drive main() the way a shell would; returns the SystemExit code."""
    with patch.object(sys, "argv", ["agentcad", *argv]):
        with pytest.raises(SystemExit) as exc:
            agentcad_cli.main()
    return exc.value.code


# ------------------------------------------------- edit 1: examples=False

@pytest.mark.timeout(300)
def test_build_service_examples_flag_defaults_to_registering_them(tmp_path):
    service = agentcad_cli._build_service(tmp_path / "projects")
    try:
        assert any(p["name"] == "prototyping" for p in service.list_projects())
    finally:
        service.kernel.stop()
        agentcad_cli._release_work_root(service)


@pytest.mark.timeout(300)
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


def _writable_spy(monkeypatch, bench_cli):
    """Capture `extra_writable` at the `bench_service` seam and stop there.

    The grant is decided before the kernel spawns, so the assertion costs no
    kernel: the spy raises, the handler's blanket arm reports exit 2, and what
    the test reads is the list the worker *would* have been confined with.
    """
    seen = {}

    def _fake(projects_dir, *, extra_writable=None):
        seen["extra"] = [str(root) for root in (extra_writable or [])]
        raise RuntimeError("stop here — the write grant is the assertion")

    monkeypatch.setattr(bench_cli, "bench_service", _fake)
    return seen


def test_bench_score_never_grants_the_worker_a_write_into_the_inputs(
        tmp_path, monkeypatch):
    """A candidate's part script runs *inside* the confined worker, so a write
    grant on the task bundle would let it overwrite the reference STEP it is
    about to be measured against — a geometry 1.0 it wrote itself — and a grant
    on the submission would break the one thing `bench score` promises. Neither
    grant buys a read: reads are unrestricted in the `local` posture and the
    `hosted` read roots are a separate list.
    """
    from agentcad.bench import cli as bench_cli
    from agentcad.bench import tasks as bench_tasks

    seen = _writable_spy(monkeypatch, bench_cli)
    task = bench_tasks.load_task(SEED)
    submission = tmp_path / "submission"
    submission.mkdir()
    work = tmp_path / "work"

    assert _run(["bench", "score", str(submission), "--task", SEED,
                 "--work-dir", str(work), "--quiet"]) == 2
    assert seen["extra"] == [str(work)]
    for granted in seen["extra"]:
        assert not str(task.root).startswith(granted)
        assert not str(bench_tasks.tasks_root()).startswith(granted)
        assert not str(submission).startswith(granted)


def test_bench_run_never_grants_the_worker_a_write_into_the_task_tree(
        tmp_path, monkeypatch):
    """`bench run`'s half of the same rule — and the sharper one: the tree it
    would have granted is the maintainer's checked-in `benchmarks/`."""
    from agentcad.bench import cli as bench_cli
    from agentcad.bench import tasks as bench_tasks

    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-used-the-spy-raises-first")
    seen = _writable_spy(monkeypatch, bench_cli)
    work = tmp_path / "work"

    assert _run(["bench", "run", "--report", str(tmp_path / "out"),
                 "--tasks", SEED, "--work-dir", str(work), "--quiet"]) == 2
    assert seen["extra"] == [str(work)]
    assert str(bench_tasks.tasks_root()) not in seen["extra"]


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


def test_bench_score_with_every_subscore_excluded_is_exit_two(tmp_path, capsys,
                                                              monkeypatch):
    """Design §4.8, the lane both other exit-2 tests short-circuit past.

    An empty `weights_effective` means there is no arithmetic left to report —
    "we could not produce a verdict", the harness lane. It is emphatically NOT
    a zero: a zero is a measurement, and answering 0.0 here would let a
    submission that made every subscore unmeasurable rank above one that was
    merely wrong.

    No kernel: the service is stubbed at `bench_service`, which is the seam
    `_cmd_score` builds through, and `Scorer` is stubbed at the name the
    handler binds.
    """
    from types import SimpleNamespace

    from agentcad.bench import cli as bench_cli

    stopped = []
    service = SimpleNamespace(
        kernel=SimpleNamespace(stop=lambda: stopped.append(True)),
        work_root=None)
    monkeypatch.setattr(bench_cli, "bench_service",
                        lambda *a, **k: service)
    monkeypatch.setattr("agentcad.core.tools.build_registry",
                        lambda *a, **k: None)

    class _Scorer:
        def __init__(self, service, registry=None):
            pass

        def score(self, task, submission, **kwargs):
            return {"schema": 1, "task": task.id, "category": task.category,
                    "task_set": task.task_set, "task_version": task.version,
                    "harness": 1, "agentcad": "0.1.0", "total": 0.0,
                    "weights_effective": {},
                    "subscores": {"built": {"value": 0.0, "weight": 0.0,
                                            "status": "error", "detail": {}}},
                    "notes": ["every subscore was excluded from the total, so "
                              "no verdict could be produced"]}

    monkeypatch.setattr("agentcad.bench.scoring.Scorer", _Scorer)

    out = tmp_path / "out"
    assert _run(["bench", "score", str(tmp_path), "--task", SEED,
                 "--out", str(out)]) == 2
    # The score is still written and still printed: evidence beats silence, and
    # the exit code is the only thing the exclusion moves.
    assert json.loads((out / "score.json").read_text())["weights_effective"] == {}
    assert "no verdict could be produced" in capsys.readouterr().err
    assert stopped == [True]          # the kernel is stopped on this path too


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


def test_bench_help_lists_every_subcommand(capsys):
    # `_run` already absorbs argparse's SystemExit; `--help` exits 0.
    assert _run(["bench", "--help"]) == 0
    text = capsys.readouterr().out
    for name in ("run", "score", "prompt", "report", "publish"):
        assert name in text


def test_bench_with_no_subcommand_is_exit_two(capsys):
    assert _run(["bench"]) == 2
    assert "pick a subcommand" in capsys.readouterr().err


# ----------------------------------------------------------- bench prompt

#: A task whose `prompt.md` carries the reviewer-only rationale block — the
#: whole reason this command exists. `cat prompt.md` hands a model the
#: reference parameters the thresholds were derived from.
COMMENTED = "optimize_under_constraints/opt_001_lightest_bracket"


def test_bench_prompt_strips_the_reviewer_comments_cat_would_leak(capsys):
    from agentcad.bench import tasks as bench_tasks

    raw = (bench_tasks.load_task(COMMENTED).prompt_path
           .read_text(encoding="utf-8"))
    assert "<!--" in raw          # the leak `cat prompt.md` would hand over

    assert _run(["bench", "prompt", COMMENTED]) == 0
    captured = capsys.readouterr()
    assert "<!--" not in captured.out and "-->" not in captured.out
    assert captured.err == ""
    assert captured.out == bench_tasks.prompt_text(
        bench_tasks.load_task(COMMENTED))


def test_bench_prompt_inlines_the_assets_as_the_runner_does(capsys):
    from agentcad.bench import tasks as bench_tasks

    task_id = "model_from_drawing/mfd_002_angle_bracket"
    task = bench_tasks.load_task(task_id)
    asset = task.asset_paths[0]

    assert _run(["bench", "prompt", task_id]) == 0
    out = capsys.readouterr().out
    assert f"--- attachment: {asset.relative_to(task.root).as_posix()} ---" in out
    assert asset.read_text(encoding="utf-8").strip() in out


def test_bench_prompt_json_names_the_task_the_prompt_and_the_assets(capsys):
    from agentcad.bench import tasks as bench_tasks

    task_id = "model_from_drawing/mfd_002_angle_bracket"
    assert _run(["bench", "prompt", task_id, "--json"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert payload["task"] == task_id
    assert payload["assets"] == ["assets/drawing.svg"]
    assert payload["prompt"] == bench_tasks.prompt_text(
        bench_tasks.load_task(task_id))


def test_bench_prompt_an_unknown_task_is_exit_two(capsys):
    assert _run(["bench", "prompt", "model_from_drawing/nope"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "agentcad bench prompt" in captured.err


def test_bench_prompt_needs_no_kernel(tmp_path, monkeypatch):
    """Pure over a bundle: an external evaluator runs it on a checkout, not on
    a machine that has ever built a solid."""
    def _explode(*args, **kwargs):
        raise AssertionError("bench prompt built a service")

    monkeypatch.setattr("agentcad.cli._build_service", _explode)
    assert _run(["bench", "prompt", COMMENTED]) == 0


def test_run_without_an_agent_it_can_drive_is_exit_two(tmp_path, capsys,
                                                       monkeypatch):
    """`run` refuses before it spawns anything, and names the fix.

    The rest of `bench run`'s contract — the results layout, the budgets, the
    over-budget flag — is pinned in `tests/test_bench_runner.py`, where a
    scripted client makes the whole path offline. What belongs *here* is the
    process boundary: a run that cannot reach an agent is a **harness** error
    (exit 2, never 1), and it costs no kernel to discover.
    """
    from agentcad.bench import runner as bench_runner

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(bench_runner, "CLIENT_FACTORY", None)
    assert _run(["bench", "run", "--report", str(tmp_path / "out")]) == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


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
    def _explode(*args, **kwargs):
        raise AssertionError("bench report built a service")

    # `agentcad.cli._build_service`, not `bench_cli.bench_service`: patching the
    # wrapper would still pass if `_cmd_report` reached past it.
    monkeypatch.setattr("agentcad.cli._build_service", _explode)
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


# ---------------------------------------------------------- bench publish

#: The roster the publish tests pin. `_cmd_publish` reads `load_tasks()`, and
#: `PUBLISH_ROSTER` is what the fixture below makes it answer: a CLI test must
#: measure the handler, not the state of `benchmarks/tasks/` on the day it runs
#: — the shipped roster grows to 25, and half of a task being on disk mid-commit
#: would fail these tests for a reason nothing in their bodies explains.
PUBLISH_ROSTER = ["model_from_drawing/a", "modify_to_spec/b"]


@pytest.fixture
def roster(monkeypatch):
    """Make `load_tasks()` answer `PUBLISH_ROSTER`, whatever is on disk."""
    from types import SimpleNamespace

    monkeypatch.setattr(
        "agentcad.bench.tasks.load_tasks",
        lambda *a, **k: [SimpleNamespace(id=task_id)
                         for task_id in PUBLISH_ROSTER])
    return PUBLISH_ROSTER


def _publish_report(total=0.6) -> dict:
    tasks = PUBLISH_ROSTER
    categories = sorted({task_id.split("/")[0] for task_id in tasks})
    return {"schema": 1, "task_set": "bench-v1", "harness": 1,
            "agentcad": "0.1.0", "agent": "builtin", "model": "m",
            "n": len(tasks), "total": total,
            "categories": {name: {"total": total, "n": 1, "missing": 0}
                           for name in categories},
            "tasks": {task_id: {"total": total, "over_budget": False,
                                "missing": False, "subscores": {}}
                      for task_id in tasks},
            "warnings": []}


def _publish_row(row_id, **over) -> dict:
    row = {"schema": 1, "id": row_id, "agent": "AgentCAD built-in chat agent",
           "harness_command": "agentcad bench run --set core",
           "model": "claude-sonnet-5", "agentcad": "0.1.0", "harness": 1,
           "task_set": "bench-v1", "date": "2026-08-19",
           "config": {"kernel_pool_size": 1},
           "submission": "https://example.invalid/s.tar.gz",
           "transcript": "https://example.invalid/t.tar.gz", "notes": ""}
    row.update(over)
    return row


def _board(root: Path, rows) -> Path:
    from agentcad.bench._json import write_json

    for row in rows:
        out = root / "rows" / row["id"]
        write_json(out / "row.json", row)
        write_json(out / "report.json", _publish_report())
    return root


def test_bench_publish_writes_the_page_and_exits_zero(tmp_path, capsys, roster):
    board = _board(tmp_path / "board", [_publish_row("builtin"),
                                        _publish_row("kcl")])
    page = tmp_path / "index.html"
    assert _run(["bench", "publish", str(board), "-o", str(page),
                 "--title", "AgentCAD-Bench"]) == 0
    html = page.read_text()
    assert "<script" not in html            # self-contained, no remote asset
    assert "builtin" in html and "kcl" in html
    assert capsys.readouterr().out.startswith("bench publish: 2 row(s)")


def test_bench_publish_incomplete_disclosure_is_exit_one_and_writes_nothing(
        tmp_path, capsys, roster):
    """The exit-1 lane: a rejected row refuses the WHOLE board.

    A board that published the disclosed rows and quietly dropped the rest
    would make the disclosure rule decorative.
    """
    board = _board(tmp_path / "board",
                   [_publish_row("builtin"),
                    _publish_row("mystery", submission="", config=None)])
    page = tmp_path / "index.html"
    assert _run(["bench", "publish", str(board), "-o", str(page)]) == 1
    captured = capsys.readouterr()
    assert "mystery" in captured.err and "submission" in captured.err
    assert "disclosure problem" in captured.err
    assert not page.exists()                # nothing partial ever lands


def test_bench_publish_a_partial_run_is_not_a_row(tmp_path, capsys, roster):
    """Rule 5: a report that does not cover the roster is rejected, so a row
    cannot buy a place by running the easy half."""
    from agentcad.bench._json import write_json

    board = tmp_path / "board"
    row = _publish_row("builtin")
    write_json(board / "rows" / "builtin" / "row.json", row)
    report = _publish_report()
    report["tasks"] = {}
    write_json(board / "rows" / "builtin" / "report.json", report)
    assert _run(["bench", "publish", str(board),
                 "-o", str(tmp_path / "index.html")]) == 1
    assert "does not cover" in capsys.readouterr().err


def test_bench_publish_an_absent_board_is_exit_two(tmp_path, capsys, roster):
    assert _run(["bench", "publish", str(tmp_path / "nope"),
                 "-o", str(tmp_path / "index.html")]) == 2
    assert "agentcad bench publish" in capsys.readouterr().err


def test_bench_publish_an_unwritable_output_is_exit_two(tmp_path, capsys, roster):
    board = _board(tmp_path / "board", [_publish_row("builtin")])
    # A directory where the page should go: the write fails, and a failure to
    # write is the harness lane, never a rejected row.
    blocked = tmp_path / "index.html"
    blocked.mkdir()
    assert _run(["bench", "publish", str(board), "-o", str(blocked)]) == 2
    assert "agentcad bench publish" in capsys.readouterr().err


def test_bench_publish_needs_no_kernel(tmp_path, monkeypatch, roster):
    def _explode(*args, **kwargs):
        raise AssertionError("bench publish built a service")

    monkeypatch.setattr("agentcad.cli._build_service", _explode)
    board = _board(tmp_path / "board", [_publish_row("builtin")])
    assert _run(["bench", "publish", str(board),
                 "-o", str(tmp_path / "index.html")]) == 0
