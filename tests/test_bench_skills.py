"""PRD-029 Slice 5 — `agentcad bench run --skills` (FR8, AC5).

The claim this file pins is **the with/without comparison**: the same task,
the same scripted agent, run twice — once with the skill library switched off
and once with exactly one skill selectable — produces two results directories
whose `score.json` bytes are **identical** and whose `run.json` provenance is
not. That is what makes "did this skill help?" a measurement rather than an
impression: the selection is provenance, the score is the measurement, and
`bench report --baseline` subtracts one from the other.

Three details are easy to get wrong and are asserted here rather than
described:

* **`none` has to reach the tool, not only the engine.** `skills=None` makes
  `ChatEngine`'s system prompt byte-identical to `SYSTEM_PROMPT`, but
  `load_skill` is on the registry either way — an agent that calls it anyway
  must be refused, so the task service's own library is restricted too.
* **A restricted selection refuses with the library's own vocabulary.**
  `SkillLibrary(only=…)` answers an out-of-selection name with
  `NotFoundError` / `skill_not_found` and a hint naming `bench --skills`
  (`core/skills.py`); the bench adds no second spelling of that refusal.
* **`--skills nope` is a usage error**, refused by argparse before a kernel
  spawns and naming what *is* selectable.

Offline throughout: the agent arrives through `runner.CLIENT_FACTORY`, the
module-level test seam, so `ANTHROPIC_API_KEY` is never read and a stray
network call is unreachable. `benchmarks/` is a read-only input.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from agentcad import cli as agentcad_cli
from agentcad.bench import cli as bench_cli
from agentcad.bench import runner as bench_runner
from agentcad.bench import tasks as bench_tasks

# The scripted-client fixtures the bench tests already use — one definition of
# "a fake Anthropic client" for the whole bench suite.
from .test_bench_runner import (FakeAnthropic, _create_part_call, _response,
                                _text, _tool_use)

SEED = "model_from_drawing/mfd_001_spacer_plate"
#: Where `_run_one_task` files the seed task inside a results directory.
OUT = Path("tasks") / "model_from_drawing" / "mfd_001_spacer_plate"
#: The `tool_use` id the scripted client loads a skill under.
LOAD_ID = "skill-1"


@pytest.fixture(autouse=True)
def _restore_client_id():
    """`bench run` sets the client id ContextVar; these tests drive `main()`
    in-process, so it is put back (`tests/test_bench_cli.py`'s fixture)."""
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


def _tool_result(transcript: dict, tool_use_id: str) -> dict:
    """The parsed payload of one `tool_result` in a written transcript."""
    for message in transcript["messages"]:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if (isinstance(block, dict)
                    and block.get("type") == "tool_result"
                    and block.get("tool_use_id") == tool_use_id):
                return json.loads(block["content"])
    raise AssertionError(f"no tool_result for {tool_use_id!r}")


def _baseline_from(report: dict) -> dict:
    """A baseline document from a report — `docs/bench.md`'s recipe in Python.

    A report's `categories`/`tasks` carry *rows*; a baseline carries *numbers*
    (`report.compare_baseline`), which is why the doc hands over a `jq` filter
    rather than "pass report.json".
    """
    return {
        "schema": report["schema"],
        "task_set": report["task_set"],
        "harness": report["harness"],
        "agent": report["agent"],
        "model": report["model"],
        "agentcad": report["agentcad"],
        "total": report["total"],
        "categories": {name: row["total"]
                       for name, row in report["categories"].items()},
        "tasks": {task: row.get("total")
                  for task, row in report["tasks"].items()},
    }


# ------------------------------------------------------- the flag itself

def test_the_selection_parses_all_none_and_a_sorted_list():
    parse = bench_cli._skills_arg

    assert parse("all") == {"mode": "all", "names": []}
    assert parse("none") == {"mode": "none", "names": []}
    assert parse(" snap-fits , enclosures ") == {
        "mode": "only", "names": ["enclosures", "snap-fits"]}
    # A repeated name is one name: the selection is a set, sorted for the doc.
    assert parse("snap-fits,snap-fits")["names"] == ["snap-fits"]
    # An empty selection is not "all": it is a typo, and refusing it beats
    # running the whole library under a flag that asked for nothing.
    with pytest.raises(argparse.ArgumentTypeError):
        parse(" , ")


def test_an_unknown_skill_name_is_a_usage_error_naming_the_valid_ones(tmp_path,
                                                                      capsys):
    code = _run(["bench", "run", "--tasks", SEED, "--skills", "nope",
                 "--report", str(tmp_path / "out")])
    assert code == 2
    err = capsys.readouterr().err
    assert "nope" in err
    assert "snap-fits" in err            # what IS selectable is named
    # Refused during parsing: no kernel, no results directory, nothing written.
    assert not (tmp_path / "out").exists()


def test_a_capability_gated_skill_says_so_instead_of_unknown():
    """`fem-workflow` is real but not loadable without the `[fem]` extra, and
    "unknown skill" would send its author looking for a typo."""
    from agentcad.core.skills import SkillLibrary

    library = SkillLibrary(capabilities=lambda: frozenset({"specs"}))
    with pytest.raises(argparse.ArgumentTypeError) as exc:
        bench_cli._skills_arg("fem-workflow", library=library)
    assert "fem-workflow" in str(exc.value)
    assert "not loadable here" in str(exc.value)


def test_run_json_defaults_to_the_whole_library():
    task = bench_tasks.load_task(SEED)
    outcome = bench_runner.RunOutcome(
        over_budget=False, stopped="model_ended_turn",
        usage={"wall_s": 1.0, "tool_calls": 1, "api_turns": 2}, transcript=[])
    doc = bench_runner.run_json(task, outcome, agent="builtin", model="m",
                                started="2026-08-23T10:00:00Z",
                                finished="2026-08-23T10:00:01Z")
    assert doc["skills"] == {"mode": "all", "names": []}


# --------------------------------------------------- AC5: the comparison

def _scripted(made, task):
    """A client factory that loads one skill, writes the reference part, ends.

    The *same* script in both modes: the geometry it produces cannot depend on
    whether the skill loaded, which is what makes the `score.json` byte
    comparison below meaningful.
    """
    def factory():
        fake = FakeAnthropic([
            _response([_tool_use(LOAD_ID, "load_skill", {"name": "snap-fits"})],
                      stop_reason="tool_use"),
            _response([_create_part_call(task, id="part-1")],
                      stop_reason="tool_use"),
            _response([_text("Created the spacer plate.")]),
        ])
        made.append(fake)
        return fake

    return factory


@pytest.mark.timeout(1800)
def test_ac5_the_same_task_measured_with_and_without_one_skill(tmp_path,
                                                               monkeypatch,
                                                               capsys):
    from agentcad.agent.chat import SKILLS_RULE, SYSTEM_PROMPT

    task = bench_tasks.load_task(SEED)
    without: list = []
    with_skill: list = []
    a, b = tmp_path / "A", tmp_path / "B"

    monkeypatch.setattr(bench_runner, "CLIENT_FACTORY", _scripted(without, task))
    assert _run(["bench", "run", "--tasks", SEED, "--skills", "none",
                 "--model", "fake-model", "--report", str(a), "--quiet"]) == 0
    monkeypatch.setattr(bench_runner, "CLIENT_FACTORY",
                        _scripted(with_skill, task))
    assert _run(["bench", "run", "--tasks", SEED, "--skills", "snap-fits",
                 "--model", "fake-model", "--report", str(b), "--quiet"]) == 0

    # --- the selection is provenance, and it is recorded on both documents
    run_a = json.loads((a / OUT / "run.json").read_text())
    run_b = json.loads((b / OUT / "run.json").read_text())
    assert run_a["skills"] == {"mode": "none", "names": []}
    assert run_b["skills"] == {"mode": "only", "names": ["snap-fits"]}
    assert json.loads((a / "bench.json").read_text())["skills"] == run_a["skills"]
    assert json.loads((b / "bench.json").read_text())["skills"] == run_b["skills"]

    # --- the system prompt: byte-identical without, indexed with
    assert without[0].messages.calls[0]["system"] == SYSTEM_PROMPT
    system_b = with_skill[0].messages.calls[0]["system"]
    assert SKILLS_RULE in system_b
    assert "- snap-fits —" in system_b
    # `only` restricts the whole surface, not just the load: nothing else is
    # advertised, so the run measures one skill and not the library.
    assert "- enclosures —" not in system_b

    # --- the tool: content in B, a refusal that names the flag in A
    loaded = _tool_result(json.loads((b / OUT / "transcript.json").read_text()),
                          LOAD_ID)
    assert loaded["name"] == "snap-fits" and loaded["layer"] == "core"
    assert loaded["chars"] == len(loaded["content"]) > 0
    refused = _tool_result(json.loads((a / OUT / "transcript.json").read_text()),
                           LOAD_ID)
    assert refused["error"]["type"] == "notfound_error"
    assert refused["error"]["details"]["reason"] == "skill_not_found"
    assert "--skills" in refused["error"]["details"]["hint"]

    # --- the measurement itself is untouched by the selection
    assert (a / OUT / "score.json").read_bytes() == (
        b / OUT / "score.json").read_bytes()

    # --- and `bench report --baseline` subtracts one run from the other
    assert _run(["bench", "report", str(a), "--quiet",
                 "--json-out", str(a / "report.json")]) == 0
    baseline = _baseline_from(json.loads((a / "report.json").read_text()))
    (a / "baseline.json").write_text(json.dumps(baseline), encoding="utf-8")

    capsys.readouterr()
    code = _run(["bench", "report", str(b), "--baseline", str(a / "baseline.json"),
                 "--json-out", str(b / "report.json")])
    captured = capsys.readouterr()
    assert code == 0
    assert "baseline ok" in captured.out
    doc = json.loads((b / "report.json").read_text())
    delta = doc["baseline"]["task_deltas"][0]
    assert delta["task"] == SEED
    assert delta["baseline"] == delta["measured"] == doc["tasks"][SEED]["total"]
    assert delta["delta"] == 0.0

    # The gate lane, on the same pair: a baseline half a point higher prints
    # both scores and the delta, and exits 1.
    strict = dict(baseline, total=baseline["total"] + 0.5,
                  categories={name: value + 0.5
                              for name, value in baseline["categories"].items()})
    (a / "strict.json").write_text(json.dumps(strict), encoding="utf-8")
    capsys.readouterr()
    code = _run(["bench", "report", str(b), "--baseline", str(a / "strict.json")])
    err = capsys.readouterr().err
    assert code == 1
    assert "total:" in err and "(-0.5000)" in err
