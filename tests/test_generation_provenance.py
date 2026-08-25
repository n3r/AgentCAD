"""PRD-018 slice 4 — generation provenance survives restore, and a generated
part is an ordinary script part (AC5).

Git-backed (the `generated` loose key is written into project.json and rides
git history for free), so the module skips without git.
"""

from __future__ import annotations

import shutil

import pytest

from agentcad.core import tools_generate
from agentcad.core.service import AgentCADService, EventBus
from agentcad.core.tools import build_registry

from .test_tools_generate import GREEN_SCRIPT, _green_factory, _use_fake

pytestmark = [
    pytest.mark.integration,
    pytest.mark.portability,
    pytest.mark.skipif(shutil.which("git") is None,
                       reason="git not found on PATH"),
]

PROJECT = "provproj"

BOX_V2 = GREEN_SCRIPT.replace("Box(p.w, p.w, p.w)", "Box(p.w, p.w, p.w * 2)")
assert BOX_V2 != GREEN_SCRIPT


@pytest.fixture()
def accepted(tmp_path, kernel, monkeypatch):
    """A project with one accepted, provenance-stamped generated part."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    service = AgentCADService(tmp_path / "projects", kernel, EventBus())
    registry = build_registry(service)
    assert "error" not in registry.call("create_project", {"name": PROJECT})
    _use_fake(monkeypatch, _green_factory())
    result = registry.call("generate_part",
                           {"project": PROJECT, "prompt": "a small bracket"})
    out = registry.call("accept_candidate",
                        {"project": PROJECT,
                         "generation_id": result["generation_id"],
                         "candidate": 0, "part_id": "bracket"})
    assert "error" not in out, out
    return service, registry


# ---------------------------------------------- provenance survives restore

def test_generated_key_survives_project_restore(accepted):
    service, registry = accepted

    part = service.get_part(PROJECT, "bracket")
    assert part["generated"]["prompt_sha256"]
    prompt_sha = part["generated"]["prompt_sha256"]

    # The commit that holds the accepted part + its provenance.
    history = registry.call("project_history", {"project": PROJECT})["history"]
    commit = history[0]["id"]

    # Edit the part (a new commit), then restore to the accepted state.
    assert registry.call("update_part_script",
                         {"project": PROJECT, "part_id": "bracket",
                          "script": BOX_V2})["ok"] is True
    restored = registry.call("project_restore",
                             {"project": PROJECT, "commit": commit})
    assert "error" not in restored, restored

    # The provenance loose key came back, still surfaced by the wrapper.
    part = service.get_part(PROJECT, "bracket")
    assert part["script"] == GREEN_SCRIPT
    assert part["generated"]["prompt_sha256"] == prompt_sha


# ---------------------------------------------- ordinary-part behaviour (AC5)

def test_generated_part_is_an_ordinary_script_part(accepted):
    service, registry = accepted

    # It builds and measures like any script part.
    metrics = service.get_metrics(PROJECT, "bracket")
    assert metrics["is_valid"] is True

    # It edits (update_part_script) and the provenance loose key survives the
    # edit — an edit rewrites the script, not the manifest entry's `generated`.
    edited = registry.call("update_part_script",
                           {"project": PROJECT, "part_id": "bracket",
                            "script": BOX_V2})
    assert edited["ok"] is True
    part = service.get_part(PROJECT, "bracket")
    assert part["script"] == BOX_V2
    assert part["generated"]["prompt_sha256"]

    # And it undoes like any part (durable git undo).
    undone = registry.call("undo", {"project": PROJECT})
    assert "error" not in undone, undone
    assert service.store.read_script(PROJECT, "bracket") == GREEN_SCRIPT
