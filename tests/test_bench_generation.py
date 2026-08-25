"""The `generate_from_prompt` bench category and AC8's loop-vs-one-shot delta.

PRD-018 §10 / AC8 — *"the multi-turn generation loop beats a single-turn
one-shot on the same prompt and rubric, and the delta is reported"*. Every test
here is **offline**: scripted fake clients are injected into both the PRD-018
loop and bench's single-turn runner, `ANTHROPIC_API_KEY` is never read, and a
run with neither a key nor a factory is refused. A stray network call is not
merely unlikely, it is unreachable.

Everything shares the session-scoped `kernel` (`conftest.py`) over its own
projects root, so the file is parallel-safe and nothing stops the kernel.
`benchmarks/` is a read-only input and is never written to.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agentcad.bench import generation as bench_gen
from agentcad.bench import report as bench_report
from agentcad.bench import tasks as bench_tasks
from agentcad.bench._json import canonical_json
from agentcad.bench.scoring import Scorer
from agentcad.core.tools import build_registry

from .conftest import make_test_service

TASK_ID = "generate_from_prompt/gfp_001_shim_bracket"
pytestmark = pytest.mark.timeout(600)

#: The reference part script — "what a perfect generation would have written".
REF_SCRIPT = (bench_tasks.load_task(TASK_ID).reference_project
              / "parts" / "shim.py").read_text(encoding="utf-8")

#: The loop's fake writes the reference geometry PLUS a passing SPECS of its
#: own, so the loop reaches `spec_green` in one iteration. The scorer discards
#: this SPECS (it re-binds the task rubric on top), so it inflates nothing.
GOOD_SCRIPT = REF_SCRIPT + (
    "\n\nfrom agentcad.toolkit.specs import check_valid as _cv\n"
    "SPECS = [_cv(name='valid', requirement='GFP-001')]\n")

#: The one-shot's fake writes a plausible-but-wrong part: an 8 mm plate where
#: 4 mm was asked. It still builds and is valid, but its bbox_z and volume
#: windows miss, its `envelope` bbox spec fails, and its IoU against the datum
#: drops — a genuine, measured, lower score.
WORSE_SCRIPT = REF_SCRIPT.replace('"thickness": {"default": 4.0',
                                  '"thickness": {"default": 8.0')


# ------------------------------------------------------- the fake clients

def _text(text):
    return SimpleNamespace(type="text", text=text)


def _tool_use(id, name, input):
    return SimpleNamespace(type="tool_use", id=id, name=name, input=input)


def _response(blocks, stop_reason="tool_use"):
    return SimpleNamespace(content=blocks, stop_reason=stop_reason)


class FakeMessages:
    """`test_bench_runner`'s scripted client: synchronous `create`, and running
    out of script returns a plain end-of-turn instead of raising."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            return _response([_text("done")], stop_reason="end_turn")
        return self._responses.pop(0)


class FakeAnthropic:
    def __init__(self, responses):
        self.messages = FakeMessages(responses)


def _loop_fake(script=GOOD_SCRIPT):
    """A loop client that writes *script* once. The loop force-scopes the
    project/part_id, so the tool input needs only the script."""
    return FakeAnthropic([
        _response([_text("I'll model the shim."),
                   _tool_use("g1", "create_part", {"script": script})])])


def _oneshot_fake(task, script=WORSE_SCRIPT):
    """A single-turn runner client: it must name the target project + part."""
    return FakeAnthropic([
        _response([_tool_use("o1", "create_part",
                             {"project": task.target_project,
                              "part_id": task.target_parts[0],
                              "script": script})]),
        _response([_text("Created the shim.")], stop_reason="end_turn")])


# --------------------------------------------------------------- fixtures

@pytest.fixture
def parent(tmp_path, kernel):
    """A bench-shaped parent service over the session kernel.

    `run_one_generation_task` derives its two per-run task services from this
    one, so they share the warm kernel and never spawn a second pool.
    """
    return make_test_service(tmp_path / "parent", kernel)


def _run_loop(task, cell, kernel):
    """Drive the PRD-018 loop offline and return `(LoopOutcome, service)`."""
    (cell / "projects").mkdir(parents=True, exist_ok=True)
    service = make_test_service(cell / "projects", kernel)
    outcome = bench_gen.run_loop_submission(
        task, service=service, registry=build_registry(service), cell=cell,
        model="fake-model", api_key="test-key",
        client_factory=lambda: _loop_fake())
    return outcome, service


# ------------------------------------------- the loop scores the six subscores

def test_generate_from_prompt_scores_the_six_subscores(tmp_path, kernel):
    """The loop produces a submission the scorer measures on all six subscores."""
    task = bench_tasks.load_task(TASK_ID)
    outcome, _ = _run_loop(task, tmp_path / "cell", kernel)
    # The loop reached a green verdict on its own SPECS and picked that candidate.
    assert outcome.stopped == "spec_green"
    assert outcome.over_budget is False
    assert outcome.best is not None and outcome.best["script"]

    submission = bench_gen.write_loop_submission(
        tmp_path / "sub", task, outcome.best)
    assert (submission / "parts" / "shim.py").is_file()

    score = Scorer(make_test_service(tmp_path / "proj", kernel)).score(
        task, submission)
    subs = score["subscores"]
    # The rubric (injected, re-binding SPECS) passes on the reference geometry.
    for name in ("built", "valid", "specs", "metrics"):
        assert subs[name]["status"] == "ok" and subs[name]["value"] == 1.0, name
    assert subs["geometry"]["status"] == "ok" and subs["geometry"]["value"] > 0.99
    # Single part: the task zeroed interference, so it is measured by nobody.
    assert subs["interference"]["status"] == "not_applicable"
    assert score["total"] > 0.99


def test_the_offline_fake_path_is_byte_stable(tmp_path, kernel):
    """AC3 for generation: the same fake script produces byte-identical bytes.

    The whole loop runs twice — a fixed fake in, a fixed `score.json` out — so
    this covers the loop, the accept and the scorer together.
    """
    task = bench_tasks.load_task(TASK_ID)

    def once(tag):
        outcome, _ = _run_loop(task, tmp_path / f"cell_{tag}", kernel)
        sub = bench_gen.write_loop_submission(tmp_path / f"sub_{tag}", task,
                                              outcome.best)
        return Scorer(make_test_service(tmp_path / f"proj_{tag}", kernel)).score(
            task, sub)

    assert canonical_json(once("a")) == canonical_json(once("b"))


# ------------------------------------- a candidate that deletes the part (D5)

def test_a_candidate_that_deletes_the_part_scores_zero_on_specs_not_error(
        tmp_path, kernel):
    """The rubric is the whole of what is scored; a candidate with no part to
    attach it to is a measured zero, `status: "ok"`, never an `error` (rule 2)."""
    task = bench_tasks.load_task(TASK_ID)
    # A loop that produced no script: the submission names the part with no
    # file behind it — the "deleted the part" shape.
    submission = bench_gen.write_loop_submission(tmp_path / "empty", task, None)
    assert not (submission / "parts" / "shim.py").exists()

    score = Scorer(make_test_service(tmp_path / "proj", kernel)).score(
        task, submission)
    specs = score["subscores"]["specs"]
    assert specs["status"] == "ok" and specs["value"] == 0.0
    assert specs["detail"]["reason"] == "no_rubric_attached"
    # And the whole run is a measurement, never a harness error.
    for name, row in score["subscores"].items():
        assert row["status"] in ("ok", "not_applicable"), (name, row)


# --------------------------------------------- the loop-vs-one-shot delta (AC8)

def test_loop_beats_one_shot_and_the_delta_is_reported(tmp_path, parent):
    """AC8 end to end: run both, score both, write the delta, report it."""
    task = bench_tasks.load_task(TASK_ID)
    report_dir = tmp_path / "results"
    scorer = Scorer(parent, build_registry(parent))
    failures: list = []

    row = bench_gen.run_one_generation_task(
        task, service=parent, scorer=scorer, report_dir=report_dir,
        work_dir=None, model="fake-model", api_key="test-key", agent="builtin",
        loop_client_factory=lambda: _loop_fake(),
        oneshot_client_factory=lambda: _oneshot_fake(task),
        failures=failures)
    assert failures == [], failures
    assert row["total"] > 0.99 and row["stopped"] == "spec_green"

    out = report_dir / "tasks" / "generate_from_prompt" / "gfp_001_shim_bracket"
    gen = json.loads((out / "generation.json").read_text())
    assert gen["task"] == TASK_ID
    assert gen["loop_total"] > gen["oneshot_total"]      # the loop wins
    assert gen["delta"] == pytest.approx(gen["loop_total"] - gen["oneshot_total"])
    # The one-shot's 8 mm plate loses metrics and part of specs; the loop's
    # matching geometry keeps them — a per-subscore delta a reader can see.
    assert gen["subscores"]["metrics"]["delta"] > 0.0
    assert gen["subscores"]["specs"]["delta"] > 0.0
    # Both submissions are on disk, scored the same way.
    assert (out / "score.json").is_file()
    assert (out / "oneshot_score.json").is_file()
    assert (out / "submission" / "parts" / "shim.py").is_file()
    assert (out / "oneshot_submission" / "parts" / "shim.py").is_file()

    # `bench report` surfaces the delta, and its markdown names it.
    report = bench_report.aggregate(report_dir)
    assert TASK_ID in report["generation"]
    assert report["generation"][TASK_ID]["delta"] > 0.0
    assert report["tasks"][TASK_ID]["generation"]["delta"] > 0.0
    md = bench_report.render_markdown(report)
    assert "Generation vs one-shot" in md and "`" + TASK_ID + "`" in md


# ------------------------------------------------------- the delta is honest

def test_generation_delta_refuses_to_compare_an_excluded_subscore():
    """A `not_applicable`/`error` side yields `delta: null`, never a number."""
    loop = {"total": 0.8, "subscores": {
        "built": {"value": 1.0, "status": "ok"},
        "interference": {"value": 0.0, "status": "not_applicable"},
        "geometry": {"value": 0.5, "status": "ok"}}}
    oneshot = {"total": 0.4, "subscores": {
        "built": {"value": 0.5, "status": "ok"},
        "interference": {"value": 0.0, "status": "not_applicable"},
        "geometry": {"value": 0.0, "status": "error"}}}
    delta = bench_gen.generation_delta(loop, oneshot)
    assert delta["delta"] == pytest.approx(0.4)
    assert delta["subscores"]["built"]["delta"] == pytest.approx(0.5)
    # interference is excluded on both sides; geometry errored on the one-shot.
    assert delta["subscores"]["interference"]["delta"] is None
    assert delta["subscores"]["geometry"]["delta"] is None


# ----------------------------------------------------- the live gate (AC8)

def test_bench_run_drives_the_generation_task_offline(tmp_path, monkeypatch):
    """`bench run --set generation` through argparse → `cmd_bench` → `_cmd_run`.

    The loop rides `generation.LOOP_CLIENT_FACTORY`; the one-shot rides
    `runner.CLIENT_FACTORY` (its baseline is the ordinary single-turn runner),
    so both halves run offline and the whole `main()` path is covered.
    """
    import sys
    from unittest.mock import patch

    from agentcad import cli as agentcad_cli
    from agentcad.bench import runner as bench_runner

    task = bench_tasks.load_task(TASK_ID)
    monkeypatch.setattr(bench_gen, "LOOP_CLIENT_FACTORY", lambda: _loop_fake())
    monkeypatch.setattr(bench_gen, "ONESHOT_CLIENT_FACTORY", None)
    monkeypatch.setattr(bench_runner, "CLIENT_FACTORY",
                        lambda: _oneshot_fake(task))
    report = tmp_path / "out"

    with patch.object(sys, "argv", ["agentcad", "bench", "run", "--set",
                                    "generation", "--model", "fake-model",
                                    "--report", str(report)]):
        with pytest.raises(SystemExit) as exc:
            agentcad_cli.main()
    assert exc.value.code == 0

    head = json.loads((report / "bench.json").read_text())
    assert list(head["tasks"]) == [TASK_ID]
    out = report / "tasks" / "generate_from_prompt" / "gfp_001_shim_bracket"
    gen = json.loads((out / "generation.json").read_text())
    assert gen["delta"] > 0.0
    assert bench_report.aggregate(report)["generation"][TASK_ID]["delta"] > 0.0


def test_a_generation_run_with_no_key_and_no_factory_is_refused(monkeypatch):
    """The live half is refused before the kernel spawns, with the fix named."""
    from agentcad.core.model import ValidationError

    monkeypatch.setattr(bench_gen, "LOOP_CLIENT_FACTORY", None)
    monkeypatch.setattr(bench_gen, "ONESHOT_CLIENT_FACTORY", None)
    monkeypatch.setattr("agentcad.bench.runner.CLIENT_FACTORY", None)
    with pytest.raises(ValidationError) as exc:
        bench_gen.require_generation_agents(None)
    assert "ANTHROPIC_API_KEY" in exc.value.message
    # A key alone satisfies both halves.
    bench_gen.require_generation_agents("a-key")
