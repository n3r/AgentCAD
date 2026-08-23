"""`core/tools_skills.py` — the two agent-facing skill tools (PRD-029 FR3).

No kernel: both tools are filesystem reads over `service.skills`. The pack is
exercised through `build_registry`, so these tests also prove it loads at `sk`
without reading a later pack's seam at registration time.
"""

from __future__ import annotations

import pytest

from agentcad.core import skills as sk
from agentcad.core.service import EventBus
from agentcad.core.skills import SkillLibrary
from agentcad.core.tools import build_registry

from .conftest import make_test_service
from .test_skills_library import write_skill

PROJECT = "skillproj"


class _UnusedKernel:
    """These tools never build geometry; any kernel use is a bug."""

    alive = True

    def request(self, *args, **kwargs):  # pragma: no cover — guard
        raise AssertionError("kernel must not be used by this test")


@pytest.fixture
def stack(tmp_path):
    bus = EventBus()
    service = make_test_service(tmp_path / "projects", _UnusedKernel(), bus)
    service.create_project(PROJECT)
    registry = build_registry(service)
    return service, registry, bus


def project_skills(service, proj: str = PROJECT):
    d = service.store.path_of(proj) / "skills"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _drain(q):
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    return events


# ----------------------------------------------------------------- registry

def test_both_tools_are_registered(stack):
    _, registry, _ = stack
    names = [tool.name for tool in registry.list()]
    assert "list_skills" in names
    assert "load_skill" in names
    load = registry.get("load_skill")
    assert load.input_schema["required"] == ["name"]
    assert set(load.input_schema["properties"]) == {"project", "name", "asset"}
    listing = registry.get("list_skills")
    assert listing.input_schema["required"] == []
    assert set(listing.input_schema["properties"]) == {"project", "query"}


# --------------------------------------------------------------- list_skills

def test_list_skills_without_a_project_is_the_core_layer_only(stack):
    service, registry, _ = stack
    write_skill(project_skills(service), "house-rules")

    out = registry.call("list_skills", {})
    names = [entry["name"] for entry in out["skills"]]
    assert "snap-fits" in names
    assert "house-rules" not in names
    assert {entry["layer"] for entry in out["skills"]} == {"core"}
    assert out["matched"] is False
    assert isinstance(out["hidden"], list)


def test_list_skills_with_a_project_shows_the_project_layer(stack):
    service, registry, _ = stack
    write_skill(project_skills(service), "house-rules")

    out = registry.call("list_skills", {"project": PROJECT})
    entry = next(e for e in out["skills"] if e["name"] == "house-rules")
    assert entry["layer"] == "project"
    assert entry["trusted"] is False
    assert entry["overrides"] is None


def test_list_skills_query_ranks_the_matching_skill_first(stack):
    _, registry, _ = stack
    out = registry.call("list_skills", {"query": "snap-fits"})
    assert out["matched"] is True
    assert out["skills"][0]["name"] == "snap-fits"


def test_hidden_names_a_capability_gated_skill(stack, monkeypatch):
    _, registry, _ = stack
    monkeypatch.setitem(sk.CAPABILITIES, "fem", lambda: False)

    out = registry.call("list_skills", {})
    assert "fem-workflow" not in [e["name"] for e in out["skills"]]
    hidden = next(e for e in out["hidden"] if e["name"] == "fem-workflow")
    assert hidden["reason"] == "capability"
    assert hidden["requires"] == ["fem"]

    refusal = registry.call("load_skill", {"name": "fem-workflow"})
    assert refusal["error"]["type"] == "validation_error"
    assert refusal["error"]["details"]["reason"] == "skill_unavailable"


def test_an_unknown_project_is_a_notfound_error_on_both_tools(stack):
    _, registry, _ = stack
    for name, args in (("list_skills", {"project": "nope"}),
                       ("load_skill", {"project": "nope",
                                       "name": "snap-fits"})):
        out = registry.call(name, args)
        assert out["error"]["type"] == "notfound_error", name


# ---------------------------------------------------------------- load_skill

def test_load_skill_returns_content_and_publishes_skill_loaded(stack):
    service, registry, bus = stack
    queue = bus.subscribe()

    out = registry.call("load_skill", {"name": "snap-fits"})
    assert out["name"] == "snap-fits"
    assert out["layer"] == "core"
    assert out["chars"] == len(out["content"]) > 0
    assert out["provenance"]["layer"] == "core"

    event = next(e for e in _drain(queue) if e["type"] == "skill_loaded")
    assert event == {"type": "skill_loaded", "project": None,
                     "name": "snap-fits", "layer": "core",
                     "chars": out["chars"], "client": "local"}


def test_an_untrusted_project_skill_is_refused_until_a_human_trusts_it(stack):
    service, registry, bus = stack
    write_skill(project_skills(service), "house-rules", body="Ours.\n")
    queue = bus.subscribe()

    refusal = registry.call("load_skill",
                            {"project": PROJECT, "name": "house-rules"})
    assert refusal["error"]["type"] == "validation_error"
    assert refusal["error"]["details"]["reason"] == "skill_untrusted"
    # A refused load publishes nothing.
    assert [e for e in _drain(queue) if e["type"] == "skill_loaded"] == []

    service.skills.trust(PROJECT, "house-rules")
    out = registry.call("load_skill",
                        {"project": PROJECT, "name": "house-rules"})
    assert out["layer"] == "project"
    assert "Ours." in out["content"]
    event = next(e for e in _drain(queue) if e["type"] == "skill_loaded")
    assert event["project"] == PROJECT and event["layer"] == "project"


def test_an_unknown_skill_is_a_notfound_error(stack):
    _, registry, _ = stack
    out = registry.call("load_skill", {"name": "no-such-skill"})
    assert out["error"]["type"] == "notfound_error"
    assert out["error"]["details"]["reason"] == "skill_not_found"


def test_an_asset_is_readable_and_traversal_is_refused(stack, tmp_path):
    service, registry, _ = stack
    core = tmp_path / "core-skills"
    write_skill(core, "widgets")
    snippet = core / "widgets" / "snippets" / "x.py"
    snippet.parent.mkdir(parents=True)
    snippet.write_text("PARAMS = {}\n", encoding="utf-8")
    # Swapped AFTER build_registry: the pack must read `service.skills` inside
    # its handlers, never at register() time.
    service.skills = SkillLibrary(service.store, core_dir=core)

    out = registry.call("load_skill",
                        {"name": "widgets", "asset": "snippets/x.py"})
    assert out["content"] == "PARAMS = {}\n"
    assert {a["path"] for a in out["assets"]} == {"snippets/x.py"}

    for bad in ("../../../etc/passwd", "/etc/passwd", "snippets/../../x"):
        refusal = registry.call("load_skill",
                                {"name": "widgets", "asset": bad})
        assert "error" in refusal, bad
        assert refusal["error"]["details"]["reason"] in {"skill_not_found",
                                                         "skill_invalid"}
