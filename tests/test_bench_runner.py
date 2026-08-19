"""The bench runner: a budgeted `ChatEngine`, a transcript, `run.json`.

PRD-024 AC8 — *"a task that exhausts its budget is stopped, flagged
`over_budget`, and still scored on whatever it produced"* — is the point of
this file, and every test here is **offline**: a scripted fake client is
injected everywhere, `ANTHROPIC_API_KEY` is never read, and `run_task` refuses
to construct a real client when neither a key nor a factory is given. A stray
network call is not merely unlikely here, it is unreachable.

Everything takes the session-scoped `kernel` (`conftest.py`) and its own
projects root, so the file is parallel-safe and **nothing stops the kernel** —
it belongs to the session, and stopping it would break every neighbour.
`benchmarks/` is a read-only input and is never written to.
"""
from __future__ import annotations

import dataclasses
import json
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agentcad import cli as agentcad_cli
from agentcad.bench import cli as bench_cli
from agentcad.bench import runner as bench_runner
from agentcad.bench import tasks as bench_tasks
from agentcad.core.tools import build_registry

from .conftest import make_test_service

SEED = "model_from_drawing/mfd_001_spacer_plate"

#: The reference part script, used as "what a perfect agent would have written".
#: Read once: `benchmarks/` is a read-only input and this only ever reads it.
REF_SCRIPT = (bench_tasks.load_task(SEED).reference_project
              / "parts" / "spacer_plate.py").read_text(encoding="utf-8")


# ------------------------------------------------------- the fake client

def _text(text):
    return SimpleNamespace(type="text", text=text)


def _tool_use(id, name, input):
    return SimpleNamespace(type="tool_use", id=id, name=name, input=input)


def _response(blocks, stop_reason="end_turn"):
    return SimpleNamespace(content=blocks, stop_reason=stop_reason)


class FakeMessages:
    """`tests/test_chat.py`'s scripted client, with two deliberate changes.

    ``create`` is **synchronous**: `BudgetedClient` awaits the inner result
    only when it is awaitable, so the wrapper works with the real
    `AsyncAnthropic` *and* with a plain callable — and a test that had to
    write `async def` to script three responses is a test that leaks the
    engine's internals into every fixture.

    Running out of script returns a plain end-of-turn instead of raising, so a
    budget test can supply "a lot" without counting exactly.
    """

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            return _response([_text("done")])
        return self._responses.pop(0)


class FakeAnthropic:
    def __init__(self, responses):
        self.messages = FakeMessages(responses)


def _create_part_call(task, id="t1"):
    return _tool_use(id, "create_part",
                     {"project": task.target_project,
                      "part_id": "spacer_plate", "script": REF_SCRIPT})


# ------------------------------------------------------------- fixtures

@pytest.fixture
def bench_cell(tmp_path):
    cell = tmp_path / "cell"
    (cell / "projects").mkdir(parents=True)
    return cell


@pytest.fixture
def stack(bench_cell, kernel):
    """`(service, registry)` over the cell, on the shared session kernel.

    `make_test_service` nulls the bus's `on_publish`, so a run in a test does
    not git-snapshot every write into the throwaway cell. The real
    `bench run` keeps the hook — a bench measures the product surface — and
    that path is covered by the CLI test at the bottom of this file.
    """
    service = make_test_service(bench_cell / "projects", kernel)
    return service, build_registry(service)


# ------------------------------------------------- a scripted agent runs

@pytest.mark.timeout(600)
def test_a_scripted_agent_produces_a_scoreable_project(bench_cell, stack):
    from agentcad.bench.scoring import Scorer

    service, registry = stack
    task = bench_tasks.load_task(SEED)
    fake = FakeAnthropic([
        _response([_text("I'll model the plate."), _create_part_call(task)],
                  stop_reason="tool_use"),
        _response([_text("Created the spacer plate.")]),
    ])
    outcome = bench_runner.run_task(
        task, service=service, registry=registry, cell=bench_cell,
        model="fake-model", api_key="test-key", client_factory=lambda: fake)

    assert outcome.stopped == "model_ended_turn"
    assert outcome.over_budget is False
    assert outcome.usage["tool_calls"] == 1
    assert outcome.usage["api_turns"] == 2
    assert outcome.transcript and outcome.transcript[0]["role"] == "user"

    score = Scorer(service, registry).score(
        task, bench_cell / "projects" / task.target_project)
    assert score["subscores"]["built"]["value"] == 1.0
    assert score["total"] > 0.9


# --------------------------------------------------- AC8: over budget

@pytest.mark.timeout(600)
def test_a_runaway_agent_is_stopped_and_still_scored(bench_cell, stack):
    """AC8: the budget stops the turn, the flag is raised, the work is scored."""
    from agentcad.bench.scoring import Scorer

    service, registry = stack
    task = bench_tasks.load_task(SEED)
    spin = _response([_tool_use("tN", "get_project",
                                {"project": task.target_project})],
                     stop_reason="tool_use")
    fake = FakeAnthropic([_response([_create_part_call(task)],
                                    stop_reason="tool_use")] + [spin] * 200)
    outcome = bench_runner.run_task(
        task, service=service, registry=registry, cell=bench_cell,
        model="fake-model", api_key="test-key", client_factory=lambda: fake)

    assert outcome.over_budget is True
    assert outcome.stopped in ("tool_calls", "api_turns")
    assert outcome.stopped in bench_runner.STOPPED
    assert outcome.usage["tool_calls"] >= task.budgets.turns
    # The engine's own 30-call ceiling never fired: the task's budget is lower
    # and the client refuses first, which is the "one engine turn" rule.
    assert outcome.usage["tool_calls"] == task.budgets.turns
    # The scripted client never ran out, so the stop came from the budget.
    assert fake.messages._responses

    score = Scorer(service, registry).score(
        task, bench_cell / "projects" / task.target_project)
    assert score["subscores"]["built"]["value"] == 1.0


@pytest.mark.timeout(300)
def test_a_zero_wall_budget_stops_before_the_first_call(bench_cell, stack):
    service, registry = stack
    task = bench_tasks.load_task(SEED)
    task = dataclasses.replace(task, budgets=bench_tasks.Budgets(
        wall_s=0.0, turns=task.budgets.turns,
        api_turns=task.budgets.api_turns))
    fake = FakeAnthropic([])
    outcome = bench_runner.run_task(
        task, service=service, registry=registry, cell=bench_cell,
        model="fake-model", api_key="test-key", client_factory=lambda: fake)

    assert outcome.stopped == "wall_clock"
    assert outcome.over_budget is True
    assert outcome.usage["tool_calls"] == 0
    assert fake.messages.calls == []      # the inner client was never reached


def test_the_budget_deadline_is_monotonic_not_wall_clock():
    """An NTP step must not move a budget (`checks.py:1208-1210`)."""
    import inspect

    source = inspect.getsource(bench_runner)
    assert "time.monotonic" in source
    assert "time.time()" not in source


def test_a_run_with_no_key_and_no_client_is_refused_not_attempted():
    """No key means no client is ever constructed — a stray call is impossible."""
    from agentcad.core.model import ValidationError

    with pytest.raises(ValidationError) as exc:
        bench_runner.require_agent(None, None)
    assert "ANTHROPIC_API_KEY" in exc.value.message
    bench_runner.require_agent(None, lambda: FakeAnthropic([]))   # injected: ok
    bench_runner.require_agent("k", None)                          # key: ok


# ----------------------------------------------------- the two documents

def test_transcript_is_redacted_and_image_free(tmp_path):
    payload = bench_runner.transcript_payload(
        bench_tasks.load_task(SEED),
        [{"role": "user",
          "content": [{"type": "text",
                       "text": f"wrote {tmp_path}/projects/x.py"}]},
         {"role": "user",
          "content": [{"type": "tool_result", "tool_use_id": "t1",
                       "content": [{"type": "text",
                                    "text": '{"png_base64": "AAAA"}'}]}]},
         {"role": "user",
          "content": [{"type": "tool_result", "tool_use_id": "t2",
                       "content": [{"type": "image",
                                    "source": {"type": "base64",
                                               "media_type": "image/png",
                                               "data": "BBBB"}}]}]}],
        cell=tmp_path, projects_root=tmp_path / "projects")
    blob = json.dumps(payload)

    assert str(tmp_path) not in blob
    assert "<cell>" in blob
    assert "AAAA" not in blob and "BBBB" not in blob
    assert blob.count("<image omitted>") == 2
    assert payload["schema"] == 1
    assert payload["task"] == SEED


def test_run_json_carries_the_timestamps_score_json_does_not():
    task = bench_tasks.load_task(SEED)
    outcome = bench_runner.RunOutcome(
        over_budget=True, stopped="tool_calls",
        usage={"wall_s": 12.5, "tool_calls": 24, "api_turns": 13},
        transcript=[])
    doc = bench_runner.run_json(task, outcome, agent="builtin", model="m",
                                started="2026-08-19T10:00:00Z",
                                finished="2026-08-19T10:00:12Z")

    assert doc["schema"] == bench_runner.RUN_SCHEMA
    assert doc["over_budget"] is True and doc["stopped"] == "tool_calls"
    assert doc["budgets"]["turns"] == task.budgets.turns
    assert doc["budgets"]["api_turns"] == task.budgets.turns + 4
    assert doc["usage"]["tool_calls"] == 24
    assert doc["duration_s"] == 12.5
    assert doc["transcript"] == "transcript.json"
    assert doc["started"].endswith("Z") and doc["finished"].endswith("Z")
    assert doc["host"]["python"].count(".") == 2


# ------------------------------------------------ the shipped surface

@pytest.mark.timeout(600)
def test_examples_are_off_so_the_scratch_project_is_alone(bench_cell):
    """§8.2: a task derived from a bundled example is not solvable by opening it.

    This is the one test here that builds a service the way `bench run` does —
    through `bench_service` — because "the examples are not registered" is a
    property of that construction and of nothing the runner does.
    """
    task = bench_tasks.load_task(SEED)
    service = bench_cli.bench_service(bench_cell / "projects")
    try:
        outcome = bench_runner.run_task(
            task, service=service, registry=build_registry(service),
            cell=bench_cell, model="fake-model", api_key="test-key",
            client_factory=lambda: FakeAnthropic(
                [_response([_text("nothing to do")])]))
        assert outcome.stopped == "model_ended_turn"
        assert [p["name"] for p in service.list_projects()] == [
            task.target_project]
    finally:
        service.kernel.stop()
        agentcad_cli._release_work_root(service)


# ------------------------------------------------------------ the CLI

def _run(argv):
    """Drive main() the way a shell would; returns the SystemExit code."""
    with patch.object(sys, "argv", ["agentcad", *argv]):
        with pytest.raises(SystemExit) as exc:
            agentcad_cli.main()
    return exc.value.code


@pytest.mark.timeout(900)
def test_bench_run_writes_the_whole_results_layout(tmp_path, monkeypatch):
    """`bench run` end to end, offline, through argparse and `cmd_bench`.

    The client arrives through `runner.CLIENT_FACTORY`, the module-level test
    seam: an env var would be process-global and would clobber a neighbouring
    pytest worker, and a `cmd_bench(..., client_factory=…)` keyword would be
    unreachable from `main()` — which is the path this test exists to cover.
    """
    task = bench_tasks.load_task(SEED)
    monkeypatch.setattr(bench_runner, "CLIENT_FACTORY", lambda: FakeAnthropic([
        _response([_create_part_call(task)], stop_reason="tool_use"),
        _response([_text("Created the spacer plate.")]),
    ]))
    report = tmp_path / "out"

    code = _run(["bench", "run", "--tasks", "model_from_drawing/mfd_001*",
                 "--agent", "builtin", "--model", "fake-model",
                 "--report", str(report)])
    assert code == 0

    head = json.loads((report / "bench.json").read_text())
    assert head["schema"] == bench_runner.BENCH_SCHEMA
    assert head["agent"] == "builtin" and head["model"] == "fake-model"
    assert head["task_set"] == task.task_set and head["n"] == 1
    # `report.aggregate` reads its roster from here: a selected task that never
    # scored must not leave the denominator.
    assert list(head["tasks"]) == [SEED]
    assert head["tasks"][SEED]["over_budget"] is False

    out = report / "tasks" / "model_from_drawing" / "mfd_001_spacer_plate"
    score = json.loads((out / "score.json").read_text())
    assert score["task"] == SEED and score["total"] > 0.9
    run = json.loads((out / "run.json").read_text())
    assert run["stopped"] == "model_ended_turn" and run["agent"] == "builtin"
    transcript = json.loads((out / "transcript.json").read_text())
    assert transcript["project"] == task.target_project
    assert (out / "submission" / "project.json").is_file()
    assert (out / "submission" / "parts" / "spacer_plate.py").is_file()
    # The submission is a project, not a work cell: derived and versioned trees
    # stay behind (`scoring.COPY_IGNORE`).
    assert not (out / "submission" / ".cache").exists()
    assert not (out / "submission" / ".history").exists()

    # `bench report` reads what `bench run` wrote, with an honest denominator.
    from agentcad.bench.report import aggregate

    assert aggregate(report)["n"] == 1


def test_bench_run_requires_an_api_key_and_names_the_fix(tmp_path, capsys,
                                                         monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(bench_runner, "CLIENT_FACTORY", None)
    code = _run(["bench", "run", "--tasks", SEED,
                 "--report", str(tmp_path / "out")])
    assert code == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err
    # Refused before anything was built: nothing was written.
    assert not (tmp_path / "out").exists()


def test_bench_run_refuses_a_selection_that_matches_nothing(tmp_path, capsys,
                                                            monkeypatch):
    monkeypatch.setattr(bench_runner, "CLIENT_FACTORY",
                        lambda: FakeAnthropic([]))
    code = _run(["bench", "run", "--tasks", "model_from_drawing/nope_*",
                 "--report", str(tmp_path / "out")])
    assert code == 2
    assert "no task matched" in capsys.readouterr().err


@pytest.mark.timeout(600)
def test_budget_flag_overrides_the_declared_wall_clock(tmp_path, monkeypatch,
                                                       capsys):
    """`--budget 0` stops every task, and `run.json` reports what was enforced."""
    task = bench_tasks.load_task(SEED)
    monkeypatch.setattr(bench_runner, "CLIENT_FACTORY", lambda: FakeAnthropic([
        _response([_create_part_call(task)], stop_reason="tool_use")]))
    report = tmp_path / "out"

    code = _run(["bench", "run", "--tasks", SEED, "--budget", "0", "--json",
                 "--model", "fake-model", "--report", str(report)])
    # Never 1: an over-budget task is a measurement, and it was still scored.
    assert code == 0

    out = report / "tasks" / "model_from_drawing" / "mfd_001_spacer_plate"
    run = json.loads((out / "run.json").read_text())
    assert run["stopped"] == "wall_clock" and run["over_budget"] is True
    assert run["budgets"]["wall_s"] == 0.0
    assert run["usage"]["tool_calls"] == 0
    # Nothing was modelled, so the submission measures zero — a measurement,
    # not a harness failure.
    assert json.loads((out / "score.json").read_text())["total"] == 0.0
    assert json.loads((report / "bench.json").read_text())[
        "tasks"][SEED]["over_budget"] is True
    # `--json` puts the header alone on stdout, byte-for-byte what was written.
    assert capsys.readouterr().out == (report / "bench.json").read_text()


def test_a_run_refuses_a_service_rooted_outside_its_cell(tmp_path, kernel):
    """§8.1 as a check: an agent's writes never reach a tree the run does not own."""
    from agentcad.core.model import ValidationError

    service = make_test_service(tmp_path / "elsewhere", kernel)
    with pytest.raises(ValidationError) as exc:
        bench_runner.run_task(
            bench_tasks.load_task(SEED), service=service,
            registry=build_registry(service), cell=tmp_path / "cell",
            model="fake-model", api_key="test-key",
            client_factory=lambda: FakeAnthropic([]))
    assert "throwaway cell" in exc.value.message
    # The store's own root is all that exists; the refusal created no project.
    assert list((tmp_path / "elsewhere").iterdir()) == []
