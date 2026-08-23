"""PRD-029 AC6 — skill files round-trip through branch/merge like any project
file.

This is the criterion with the least code behind it, and that is the point.
A project skill is a plain file under `<project>/skills/` in the project's
**working tree** (`store.path_of`, which is branch-aware), so PRD-001 already
owns its versioning: `history.snapshot` runs `git add -A`, `branches.switch`
checkpoints the tree it is leaving before it re-points the client, and
`merges.merge` carries the new path over with everything else. `core/skills.py`
contributes nothing to any of that — it rescans the layer on every call, so it
reads whatever the caller's branch currently has on disk.

Which means this file is a test of an *absence*: no skill-specific branch
code, no cache to invalidate, no watcher to get stale. It grades the whole
round trip through the shipped surfaces (`branch_create`/`branch_switch`/
`merge_branch` tools + `SkillLibrary.index`), not through git plumbing.

Git-touching, so it carries `integration` + `portability` and skips without
git — `tests/test_branches.py` / `tests/test_merge.py`'s rule.
"""

from __future__ import annotations

import shutil

import pytest

from agentcad.core import locks
from agentcad.core.branches import pinned_tree_var
from agentcad.core.service import AgentCADService, EventBus
from agentcad.core.tools import build_registry

from .conftest import BOX_SCRIPT
from .test_skills_library import write_skill

PROJECT = "skillbranch"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.portability,
    pytest.mark.skipif(shutil.which("git") is None,
                       reason="git not found on PATH"),
]

BOX_V2 = BOX_SCRIPT.replace("Box(p.size, p.size, p.size)",
                            "Box(p.size, p.size, p.size * 2)")
BOX_V3 = BOX_SCRIPT.replace("Box(p.size, p.size, p.size)",
                            "Box(p.size, p.size, p.size * 3)")
assert BOX_V2 != BOX_SCRIPT != BOX_V3


@pytest.fixture(autouse=True)
def _reset_context():
    """Identity and pin are ContextVars: pin them per test so this file's
    `set_client_id` can never leak into the next module on this worker."""
    cid = locks.client_id_var.set("local")
    pin = pinned_tree_var.set(None)
    yield
    locks.client_id_var.reset(cid)
    pinned_tree_var.reset(pin)


@pytest.fixture
def stack(kernel, tmp_path):
    """A REAL service: `bus.on_publish` is left as the constructor sets it, so
    every `project_changed` writes a git snapshot — which is the mechanism
    under test. `make_test_service` (which nulls the hook) would silently make
    this criterion unprovable.
    """
    bus = EventBus()
    service = AgentCADService(tmp_path / "projects", kernel, bus)
    registry = build_registry(service)
    assert getattr(service, "branches", None) is not None, \
        "the versioning pack did not install: no git?"
    assert "error" not in registry.call("create_project", {"name": PROJECT})
    created = registry.call("create_part", {"project": PROJECT,
                                            "part_id": "box",
                                            "script": BOX_SCRIPT})
    assert "error" not in created, created
    return service, registry


def _write(service, name: str, body: str) -> None:
    """One flat `skills/<name>.md` in the CALLER'S branch working tree."""
    write_skill(service.store.path_of(PROJECT) / "skills", name,
                description=f"{name} house rules.", body=body, flat=True)


def _commit(registry, script: str) -> None:
    """Land a real mutation so its `project_changed` snapshots the tree.

    `history.snapshot` is `git add -A`: the skill file written just before
    this rides along in the same commit, exactly as it would in real use.
    """
    result = registry.call("update_part_script",
                           {"project": PROJECT, "part_id": "box",
                            "script": script})
    assert "error" not in result, result


def _skills(service) -> set[str]:
    """What the shipped index reports for this project's layer, right now."""
    return {entry["name"] for entry in service.skills.index(PROJECT)
            if entry["layer"] == "project"}


def test_ac6_a_project_skill_branches_switches_and_merges(stack):
    """**AC6** — a skill written on a branch is absent after switching back,
    present again on return, and carried over by a merge.
    """
    service, registry = stack
    main = service.branches.default_branch(PROJECT)

    # 1. A skill on the default branch, committed by an ordinary mutation.
    _write(service, "ours", "# Ours\n\nHow we do it here.\n")
    _commit(registry, BOX_V2)
    assert _skills(service) == {"ours"}

    # 2. A branch inherits it (it is in the commit the branch forks from).
    assert "error" not in registry.call("branch_create",
                                        {"project": PROJECT, "name": "feature"})
    assert "error" not in registry.call("branch_switch",
                                        {"project": PROJECT, "name": "feature"})
    assert _skills(service) == {"ours"}

    # 3. A second skill, authored only on the branch.
    _write(service, "feature-only", "# Feature only\n\nBranch rules.\n")
    _commit(registry, BOX_V3)
    assert _skills(service) == {"ours", "feature-only"}

    # 4. Back on the default branch it is gone — the file, and the index.
    assert "error" not in registry.call("branch_switch",
                                        {"project": PROJECT, "name": main})
    assert _skills(service) == {"ours"}
    assert not (service.store.path_of(PROJECT) / "skills"
                / "feature-only.md").exists()

    # 5. Returning to the branch brings it back: no cache, no watcher, no
    #    stale index — `index()` rescans the layer on every call.
    assert "error" not in registry.call("branch_switch",
                                        {"project": PROJECT, "name": "feature"})
    assert _skills(service) == {"ours", "feature-only"}
    loaded = service.store.path_of(PROJECT) / "skills" / "feature-only.md"
    assert "Branch rules." in loaded.read_text(encoding="utf-8")

    # 6. A merge carries it onto the default branch, with no skill-specific
    #    merge code anywhere: it is an added path like any other.
    assert "error" not in registry.call("branch_switch",
                                        {"project": PROJECT, "name": main})
    merged = registry.call("merge_branch",
                           {"project": PROJECT, "source": "feature"})
    assert "error" not in merged, merged
    assert merged["merged"] is True, merged
    assert _skills(service) == {"ours", "feature-only"}
    assert (service.store.path_of(PROJECT) / "skills"
            / "feature-only.md").is_file()


def test_ac6_a_skills_trust_grant_does_not_travel_with_the_branch(stack):
    """The other half of "like any project file": trust is **not** one.

    `.history/agentcad/skills/trust.json` lives inside GIT_DIR (the PRD-008
    comments precedent), so it is branch-free, never versioned and never
    cloned — a `git pull` that rewrites a trusted skill cannot bring its
    approval with it. Asserting it here, beside the round trip, is what keeps
    "skills version like any file" from being read as "so does the approval".
    """
    service, registry = stack
    main = service.branches.default_branch(PROJECT)
    _write(service, "ours", "# Ours\n\nHow we do it here.\n")
    _commit(registry, BOX_V2)
    service.skills.trust(PROJECT, "ours")

    assert "error" not in registry.call("branch_create",
                                        {"project": PROJECT, "name": "feature"})
    assert "error" not in registry.call("branch_switch",
                                        {"project": PROJECT, "name": "feature"})

    entry = next(e for e in service.skills.index(PROJECT)
                 if e["name"] == "ours")
    assert entry["trusted"] is True, "trust is per-project, not per-branch"

    # One document, resolved to the SAME canonical path from every branch,
    # and living inside GIT_DIR — which `history._EXCLUDE_LINES` excludes, so
    # `git add -A` never stages it and a clone never carries it.
    canonical = service.skills.trust_path(PROJECT)
    for branch in ("feature", main):
        registry.call("branch_switch", {"project": PROJECT, "name": branch})
        assert service.skills.trust_path(PROJECT) == canonical
    assert canonical.is_file()
    assert canonical.parent.parent.parent == (
        service.store.canonical_path_of(PROJECT) / ".history")
