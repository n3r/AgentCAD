"""The chat seam for skills (PRD-029 FR4/FR5): system context + LRU budget.

Scripted `FakeAnthropic` from `tests/test_chat.py` — no network. The engine
runs the REAL `load_skill` tool through the registry, so what these tests
assert is the shipped path: the tool publishes `skill_loaded`, the engine does
the bookkeeping, and an eviction rewrites the transcript it is reclaiming.
"""

from __future__ import annotations

import asyncio

import pytest

from agentcad.agent import chat as chat_module
from agentcad.agent.chat import SKILLS_RULE, SYSTEM_PROMPT, ChatEngine
from agentcad.core.service import EventBus
from agentcad.core.skills import SkillBudget, SkillLibrary
from agentcad.core.tools import build_registry

from .conftest import make_test_service
from .test_chat import FakeAnthropic, _drain, _response, _text, _tool_use
from .test_chat import _UnusedKernel
from .test_skills_library import write_skill

PROJECT = "skillchat"


@pytest.fixture
def stack(tmp_path):
    bus = EventBus()
    service = make_test_service(tmp_path / "projects", _UnusedKernel(), bus)
    service.create_project(PROJECT)
    registry = build_registry(service)
    return service, registry, bus


def fixture_library(service, tmp_path, names, body="Body.\n"):
    """A core layer of small skills, installed as the service's library."""
    core = tmp_path / "core-skills"
    for name in names:
        write_skill(core, name, body=body)
    library = SkillLibrary(service.store, core_dir=core)
    service.skills = library
    return library


def run_turn(engine, message="go", project=PROJECT, session="main"):
    async def main():
        info = await engine.start_turn(project, message, session)
        task = engine._tasks[info["turn_id"]]
        await asyncio.wait_for(task, timeout=10)

    asyncio.run(main())


def load_round(tool_use_id, name, **args):
    return _response([_tool_use(tool_use_id, "load_skill",
                                {"name": name, **args})],
                     stop_reason="tool_use")


DONE = _response([_text("Done.")], stop_reason="end_turn")


def engine_for(registry, bus, responses, **kwargs):
    fake = FakeAnthropic(responses)
    engine = ChatEngine(registry, bus, api_key="test-key",
                        client_factory=lambda: fake, **kwargs)
    return engine, fake


# ------------------------------------------------------------ system context

def test_the_first_request_carries_the_rule_and_the_compact_index(stack):
    service, registry, bus = stack
    engine, fake = engine_for(registry, bus, [DONE], skills=service.skills)

    run_turn(engine)

    system = fake.messages.calls[0]["system"]
    assert system.startswith(SYSTEM_PROMPT)
    assert SKILLS_RULE in system
    assert "- snap-fits —" in system
    assert "Loaded this session:" not in system


def test_without_a_library_the_prompt_is_byte_identical(stack):
    _, registry, bus = stack
    engine, fake = engine_for(registry, bus, [DONE])

    run_turn(engine)

    assert fake.messages.calls[0]["system"] == SYSTEM_PROMPT
    assert engine.loaded_skills(PROJECT) == []


def test_an_empty_library_is_byte_identical_too(stack, tmp_path):
    service, registry, bus = stack
    empty = tmp_path / "no-skills"
    empty.mkdir()
    engine, fake = engine_for(registry, bus, [DONE],
                              skills=SkillLibrary(service.store,
                                                  core_dir=empty))

    run_turn(engine)

    assert fake.messages.calls[0]["system"] == SYSTEM_PROMPT


def test_a_vanished_project_falls_back_to_the_core_layer(stack):
    service, registry, bus = stack
    engine, _ = engine_for(registry, bus, [DONE], skills=service.skills)

    # `compact_index` raises NotFoundError for a project that is not there;
    # the system prompt must still be built, from the core layer alone.
    prompt = engine._system_prompt("gone-project", "main")
    assert SKILLS_RULE in prompt
    assert "- snap-fits —" in prompt


def test_a_loaded_skill_is_named_in_the_next_request(stack):
    service, registry, bus = stack
    engine, fake = engine_for(registry, bus,
                              [load_round("tu1", "snap-fits"), DONE],
                              skills=service.skills)

    run_turn(engine)

    assert "Loaded this session:" not in fake.messages.calls[0]["system"]
    assert "Loaded this session: snap-fits" in fake.messages.calls[1]["system"]
    assert [s["name"] for s in engine.loaded_skills(PROJECT)] == ["snap-fits"]
    assert engine.loaded_skills(PROJECT)[0]["layer"] == "core"
    assert engine.loaded_skills(PROJECT)[0]["chars"] > 0


# -------------------------------------------------------------- the budget

def test_five_loads_under_max_loaded_four_evict_the_oldest(stack, tmp_path):
    service, registry, bus = stack
    names = ["skill-a", "skill-b", "skill-c", "skill-d", "skill-e"]
    library = fixture_library(service, tmp_path, names)
    responses = [load_round(f"tu{i}", name)
                 for i, name in enumerate(names)] + [DONE]
    engine, fake = engine_for(registry, bus, responses, skills=library,
                              budget=SkillBudget(max_loaded=4))
    queue = bus.subscribe()

    run_turn(engine)

    assert [s["name"] for s in engine.loaded_skills(PROJECT)] == names[1:]
    assert "Loaded this session: skill-b, skill-c, skill-d, skill-e" in \
        fake.messages.calls[-1]["system"]

    events = _drain(queue)
    assert len([e for e in events if e["type"] == "skill_loaded"]) == 5
    unloaded = [e for e in events if e["type"] == "skill_unloaded"]
    assert unloaded == [{"type": "skill_unloaded", "project": PROJECT,
                         "session": "main", "name": "skill-a",
                         "reason": "budget"}]

    # The reclaimed context is really gone from the transcript.
    stub = ("[skill skill-a unloaded to free context budget — call "
            "load_skill again if you need it]")
    results = [block for message in engine.history(PROJECT)
               if isinstance(message.get("content"), list)
               for block in message["content"]
               if isinstance(block, dict)
               and block.get("type") == "tool_result"]
    evicted = next(b for b in results if b["tool_use_id"] == "tu0")
    assert evicted["content"] == stub
    kept = next(b for b in results if b["tool_use_id"] == "tu1")
    assert "Body." in kept["content"]


def test_the_char_budget_evicts_even_under_the_count(stack, tmp_path):
    service, registry, bus = stack
    library = fixture_library(service, tmp_path, ["skill-a", "skill-b"])
    engine, _ = engine_for(
        registry, bus,
        [load_round("tu0", "skill-a"), load_round("tu1", "skill-b"), DONE],
        skills=library,
        # Ten characters against a six-character body: one skill fits, two
        # never do — and the count budget (10) is nowhere near binding.
        budget=SkillBudget(max_loaded=10, max_loaded_chars=10),
    )
    queue = bus.subscribe()

    run_turn(engine)

    assert [s["name"] for s in engine.loaded_skills(PROJECT)] == ["skill-b"]
    unloaded = [e for e in _drain(queue) if e["type"] == "skill_unloaded"]
    assert [e["name"] for e in unloaded] == ["skill-a"]


def test_reloading_the_same_skill_evicts_nothing(stack, tmp_path):
    service, registry, bus = stack
    library = fixture_library(service, tmp_path, ["skill-a", "skill-b"])
    engine, fake = engine_for(
        registry, bus,
        [load_round("tu0", "skill-a"), load_round("tu1", "skill-a"), DONE],
        skills=library, budget=SkillBudget(max_loaded=1))
    queue = bus.subscribe()

    run_turn(engine)

    assert [s["name"] for s in engine.loaded_skills(PROJECT)] == ["skill-a"]
    assert [e for e in _drain(queue) if e["type"] == "skill_unloaded"] == []
    assert "Loaded this session: skill-a" in fake.messages.calls[-1]["system"]


def test_a_failed_load_records_nothing(stack):
    service, registry, bus = stack
    engine, fake = engine_for(registry, bus,
                              [load_round("tu0", "no-such-skill"), DONE],
                              skills=service.skills)

    run_turn(engine)

    assert engine.loaded_skills(PROJECT) == []
    assert "Loaded this session:" not in fake.messages.calls[-1]["system"]


def test_an_asset_read_is_not_a_skill_load(stack, tmp_path):
    service, registry, bus = stack
    library = fixture_library(service, tmp_path, ["skill-a"])
    snippet = tmp_path / "core-skills" / "skill-a" / "snippets" / "x.py"
    snippet.parent.mkdir(parents=True)
    snippet.write_text("PARAMS = {}\n", encoding="utf-8")
    engine, _ = engine_for(
        registry, bus,
        [load_round("tu0", "skill-a", asset="snippets/x.py"), DONE],
        skills=library)

    run_turn(engine)

    assert engine.loaded_skills(PROJECT) == []


def test_clear_history_drops_the_loaded_set(stack):
    service, registry, bus = stack
    engine, _ = engine_for(registry, bus,
                           [load_round("tu0", "snap-fits"), DONE],
                           skills=service.skills)

    run_turn(engine)
    assert engine.loaded_skills(PROJECT)

    engine.clear_history(PROJECT)
    assert engine.loaded_skills(PROJECT) == []
    assert engine.history(PROJECT) == []


def test_sessions_keep_separate_loaded_sets(stack):
    service, registry, bus = stack
    engine, _ = engine_for(registry, bus,
                           [load_round("tu0", "snap-fits"), DONE],
                           skills=service.skills)

    run_turn(engine, session="lane")

    assert [s["name"] for s in engine.loaded_skills(PROJECT, "lane")] == \
        ["snap-fits"]
    assert engine.loaded_skills(PROJECT) == []


def test_the_rule_names_the_tool_and_fences_the_content(stack):
    assert "load_skill" in SKILLS_RULE
    assert "data" in SKILLS_RULE
    assert "load_skill" in chat_module.SYSTEM_PROMPT
    assert "part_template" in chat_module.SYSTEM_PROMPT
