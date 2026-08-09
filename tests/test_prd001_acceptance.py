"""PRD-001 acceptance criteria — one named test per AC (slice 5).

The feature's mechanics are covered in depth by ``tests/test_manifest_merge.py``
(the pure driver), ``tests/test_branches.py`` (store seam, worktrees, refs,
tags) and ``tests/test_merge.py`` / ``tests/test_versioning_api.py`` (merge,
validation pass, tools, routes, events). This file is the *contract* layer: it
walks each acceptance criterion of
``docs/prd/in-progress/PRD-001-branching-version-control.md`` end to end through
the real stack, so a reviewer can map AC → test without reading the unit
suites.

| AC | Test |
|----|------|
| AC1 | ``test_ac1_disjoint_parts_merge_clean`` (the rocketry example, on a copy) |
| AC2 | ``test_ac2_script_conflict_resolved_by_tools_only`` |
| AC3 | ``test_ac3_param_vs_script_merges_clean`` |
| AC4 | ``test_ac4_interference_blocks_then_lands_with_allow_invalid`` |
| AC5 | ``test_ac5_tag_round_trip_and_survives_branch_delete`` |
| AC6 | ``test_ac6_browser_session_evidence_is_recorded`` — the browser session
        itself was run in slice 4 (screenshots, clean console); this check
        asserts the evidence is on the record, it does not re-drive a browser |
| AC7 | ``test_ac7_history_and_undo_are_unchanged_on_the_default_branch`` plus
        the full-suite run and ``git diff main -- tests/`` cited in
        ``docs/changelog/0071-branching-docs-and-acceptance.md`` |

Everything here touches git and the real kernel, so the tests carry
``integration`` + ``portability`` and skip without git; the example-driven AC1
is additionally ``slow``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from agentcad.core import locks
from agentcad.core.branches import pinned_tree_var
from agentcad.core.service import AgentCADService, EventBus
from agentcad.core.tools import build_registry

from .conftest import BOX_SCRIPT

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLES_DIR = REPO_ROOT / "examples"
ROCKETRY = EXAMPLES_DIR / "rocketry"

_GIT = [
    pytest.mark.integration,
    pytest.mark.portability,
    pytest.mark.skipif(shutil.which("git") is None, reason="git not found on PATH"),
]
pytestmark = _GIT

BOX_V2_SCRIPT = BOX_SCRIPT.replace(
    "Box(p.size, p.size, p.size)", "Box(p.size, p.size, p.size * 2)"
)
BOX_V3_SCRIPT = BOX_SCRIPT.replace(
    "Box(p.size, p.size, p.size)", "Box(p.size, p.size, p.size * 3)"
)
assert BOX_V2_SCRIPT != BOX_SCRIPT != BOX_V3_SCRIPT


@pytest.fixture(autouse=True)
def _reset_context():
    """Client identity and the merge pin are ContextVars: rebind them per test
    so one test's switch can never leak into the next."""
    cid = locks.client_id_var.set("local")
    pin = pinned_tree_var.set(None)
    yield
    locks.client_id_var.reset(cid)
    pinned_tree_var.reset(pin)


@pytest.fixture
def stack(kernel, tmp_path):
    """The real service + registry: the versioning pack installs the branch
    resolver, ``service.branches`` and ``service.merges``."""
    service = AgentCADService(tmp_path / "projects", kernel, EventBus())
    registry = build_registry(service)
    assert getattr(service, "branches", None) is not None
    assert getattr(service, "merges", None) is not None
    return service, registry


def _git(git_dir: Path, work_tree: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_CONFIG_NOSYSTEM": "1",
        "HOME": str(git_dir),
        "XDG_CONFIG_HOME": str(git_dir / "xdg"),
    }
    result = subprocess.run(
        ["git", "--git-dir", str(git_dir), "--work-tree", str(work_tree), *args],
        cwd=str(work_tree), env=env, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def _parents(canonical: Path, ref: str) -> list[str]:
    return _git(canonical / ".history", canonical,
                "rev-list", "--parents", "-n", "1", ref).split()[1:]


def _commit_message(canonical: Path, ref: str) -> str:
    return _git(canonical / ".history", canonical, "log", "-1", "--pretty=%B", ref)


def _on(service, proj: str, client: str, branch: str) -> None:
    """Put a client identity on a branch (creating no branches of its own)."""
    locks.set_client_id(client)
    if service.branches.current(proj) != branch:
        service.branches.switch(proj, branch)


def _demo(stack) -> str:
    """A hand-built project ('box' + 'pin', 10 mm cubes) with a 'feat' branch
    forked from the default — the fast fixture for AC2–AC5."""
    service, registry = stack
    assert "error" not in registry.call("create_project", {"name": "demo"})
    for part in ("box", "pin"):
        created = registry.call(
            "create_part",
            {"project": "demo", "part_id": part, "script": BOX_SCRIPT},
        )
        assert "error" not in created, created
    service.branches.create("demo", "feat")
    return "demo"


# ------------------------------------------------------------------- AC1


@pytest.mark.slow
@pytest.mark.skipif(not (ROCKETRY / "project.json").is_file(),
                    reason="rocketry example not present")
@pytest.mark.timeout(600)
def test_ac1_disjoint_parts_merge_clean(stack, tmp_path):
    """AC1 — two branches editing *different* parts of the rocketry example
    merge with zero conflicts, the merged project builds green, and the merge
    commit has two parents.

    The example is copied first: examples/ is never mutated in place.
    """
    service, registry = stack
    dest = tmp_path / "ex" / "rocketry"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROCKETRY, dest,
                    ignore=shutil.ignore_patterns(".cache", "exports"))
    opened = registry.call("open_project", {"path": str(dest)})
    assert "error" not in opened, opened
    proj = opened["name"]
    canonical = service.store.canonical_path_of(proj)
    assert canonical == dest

    service.branches.create(proj, "flange-weld")
    base_flange = (dest / "parts" / "flange.py").read_text(encoding="utf-8")
    base_nozzle = (dest / "parts" / "nozzle.py").read_text(encoding="utf-8")

    # Branch A rewrites the flange script; the default branch rewrites the
    # nozzle script. Different files, different manifest entries.
    _on(service, proj, "agent_a", "flange-weld")
    flange_v2 = base_flange + "\n# design study: welded flange variant\n"
    assert "error" not in registry.call(
        "update_part_script",
        {"project": proj, "part_id": "flange", "script": flange_v2},
    )
    source_head = service.history.resolve_ref(canonical, "flange-weld")

    _on(service, proj, "agent_b", service.branches.default_branch(proj))
    nozzle_v2 = base_nozzle + "\n# design study: longer nozzle extension\n"
    assert "error" not in registry.call(
        "update_part_script",
        {"project": proj, "part_id": "nozzle", "script": nozzle_v2},
    )
    target = service.branches.default_branch(proj)
    target_head = service.history.resolve_ref(canonical, target)

    result = registry.call(
        "merge_branch", {"project": proj, "source": "flange-weld"}
    )
    assert "error" not in result, result
    assert result["merged"] is True and result["fast_forward"] is False
    assert result["conflicts_resolved"] == 0
    validation = result["validation"]
    assert validation["ok"] is True, validation
    assert validation["failures"] == [] and validation["integrity"] == []
    # The validation pass rebuilds what the merge changes *relative to the
    # target*: the nozzle edit is already the target's, so only the flange
    # (the source's edit) is rebuilt.
    assert [b["part"] for b in validation["built"]] == ["flange"]

    # A real git merge commit with both parents (ours = target first).
    assert result["parents"] == [target_head, source_head]
    assert _parents(canonical, target) == [target_head, source_head]

    # Both edits landed in the working tree...
    assert (dest / "parts" / "flange.py").read_text(encoding="utf-8") == flange_v2
    assert (dest / "parts" / "nozzle.py").read_text(encoding="utf-8") == nozzle_v2

    # ...and the merged project builds green, every part.
    project = registry.call("get_project", {"project": proj})
    assert project["parts"]
    for entry in project["parts"]:
        detail = registry.call(
            "get_part", {"project": proj, "part_id": entry["id"]}
        )
        assert detail["status"]["state"] == "ok", detail["status"]
        assert detail["metrics"]["volume_mm3"] > 0


# ------------------------------------------------------------------- AC2


def test_ac2_script_conflict_resolved_by_tools_only(stack):
    """AC2 — two branches editing the *same lines* of one script produce a
    ``merge_conflict`` naming that part with ours/theirs/base; an agent
    resolves it through tools only, and the merge completes with a validation
    report."""
    service, registry = stack
    proj = _demo(stack)
    canonical = service.store.canonical_path_of(proj)

    # Both sides rewrite the same Box(...) line of parts/box.py.
    _on(service, proj, "agent_a", "feat")
    assert "error" not in registry.call(
        "update_part_script",
        {"project": proj, "part_id": "box", "script": BOX_V2_SCRIPT},
    )
    _on(service, proj, "agent_b", "master")
    assert "error" not in registry.call(
        "update_part_script",
        {"project": proj, "part_id": "box", "script": BOX_V3_SCRIPT},
    )

    conflicted = registry.call("merge_branch", {"project": proj, "source": "feat"})
    error = conflicted["error"]
    assert error["type"] == "merge_conflict"
    details = error["details"]
    assert details["source"] == "feat" and details["target"] == "master"
    assert details["outstanding"] == 1
    conflict = details["conflicts"][0]
    assert conflict["kind"] == "script"
    assert conflict["part"] == "box" and conflict["path"] == "parts/box.py"
    assert conflict["ours"] == BOX_V3_SCRIPT      # ours = target
    assert conflict["theirs"] == BOX_V2_SCRIPT    # theirs = source
    assert conflict["base"] == BOX_SCRIPT
    for marker in ("<<<<<<<", "|||||||", "=======", ">>>>>>>"):
        assert marker in conflict["merged"]

    # The merge is staged, not applied: the target's script is untouched.
    assert service.store.read_script(proj, "box") == BOX_V3_SCRIPT
    assert registry.call("merge_status", {"project": proj})["merge"]["outstanding"] == 1

    # Resolution through the tool surface only — no filesystem writes by the
    # test, which is what an agent (or the conflict UI) does.
    hand_merged = BOX_SCRIPT.replace(
        "Box(p.size, p.size, p.size)", "Box(p.size, p.size, p.size * 6)"
    )
    done = registry.call("resolve_merge", {
        "project": proj,
        "choices": {"parts/box.py": {"content": hand_merged}},
    })
    assert "error" not in done, done
    assert done["merged"] is True and done["conflicts_resolved"] == 1
    validation = done["validation"]
    assert validation["ok"] is True, validation
    assert [b["part"] for b in validation["built"]] == ["box"]
    assert validation["failures"] == [] and validation["integrity"] == []
    assert validation["interference"]["new_pairs"] == []

    assert service.store.read_script(proj, "box") == hand_merged
    assert (canonical / "parts" / "box.py").read_text(encoding="utf-8") == hand_merged
    assert len(_parents(canonical, "master")) == 2
    assert registry.call("merge_status", {"project": proj})["merge"] is None


# ------------------------------------------------------------------- AC3


def test_ac3_param_vs_script_merges_clean(stack):
    """AC3 (FR8, second case) — one branch edits a part's *script* while the
    other edits that same part's *params*: different keys, so both land with
    zero conflicts."""
    service, registry = stack
    proj = _demo(stack)
    canonical = service.store.canonical_path_of(proj)

    _on(service, proj, "agent_a", "feat")
    assert "error" not in registry.call(
        "update_part_script",
        {"project": proj, "part_id": "box", "script": BOX_V2_SCRIPT},
    )
    _on(service, proj, "agent_b", "master")
    assert "error" not in registry.call(
        "set_params",
        {"project": proj, "part_id": "box", "values": {"size": 12.0}},
    )

    result = registry.call("merge_branch", {"project": proj, "source": "feat"})
    assert "error" not in result, result
    assert result["conflicts_resolved"] == 0
    assert result["validation"]["ok"] is True

    assert service.store.read_script(proj, "box") == BOX_V2_SCRIPT
    manifest = json.loads((canonical / "project.json").read_text(encoding="utf-8"))
    box = [p for p in manifest["parts"] if p["id"] == "box"][0]
    assert box["params"]["size"] == 12.0
    # The merged geometry reflects BOTH edits: 12 x 12 x 24.
    metrics = registry.call("get_metrics", {"project": proj, "part_id": "box"})
    assert metrics["volume_mm3"] == pytest.approx(12.0 * 12.0 * 24.0, rel=1e-6)


# ------------------------------------------------------------------- AC4


def test_ac4_interference_blocks_then_lands_with_allow_invalid(stack):
    """AC4 — a merge that would introduce assembly interference is blocked by
    default and lands with ``allow_invalid: true``, with the interfering pair
    named in both cases (and in the merge commit message)."""
    service, registry = stack
    proj = _demo(stack)
    canonical = service.store.canonical_path_of(proj)

    # Baseline on the default branch: two 10 mm cubes, far apart. Re-fork
    # 'feat' from it so both branches share this assembly as their base.
    _on(service, proj, "agent_b", "master")
    assert "error" not in registry.call("set_assembly", {
        "project": proj,
        "instances": [
            {"id": "box_1", "part": "box", "position": [0, 0, 0]},
            {"id": "pin_1", "part": "pin", "position": [100, 0, 0]},
        ],
    })
    service.branches.delete(proj, "feat")
    service.branches.create(proj, "feat")

    # Neither move overlaps anything on its own branch...
    _on(service, proj, "agent_a", "feat")
    assert "error" not in registry.call("set_assembly", {
        "project": proj,
        "instances": [
            {"id": "box_1", "part": "box", "position": [20, 0, 0]},
            {"id": "pin_1", "part": "pin", "position": [100, 0, 0]},
        ],
    })
    _on(service, proj, "agent_b", "master")
    assert "error" not in registry.call("set_assembly", {
        "project": proj,
        "instances": [
            {"id": "box_1", "part": "box", "position": [0, 0, 0]},
            {"id": "pin_1", "part": "pin", "position": [25, 0, 0]},
        ],
    })
    assert registry.call("check_interference", {"project": proj})["pairs"] == []
    head_before = service.history.resolve_ref(canonical, "master")

    # ...but the key-wise merge takes box_1 from theirs and pin_1 from ours,
    # and the two cubes then overlap.
    blocked = registry.call("merge_branch", {"project": proj, "source": "feat"})
    assert blocked["error"]["type"] == "validation_error"
    report = blocked["error"]["details"]["validation"]
    assert report["ok"] is False and report["blocked"] is True
    pairs = {frozenset((p["a"], p["b"])) for p in report["interference"]["new_pairs"]}
    assert pairs == {frozenset(("box_1", "pin_1"))}
    assert all(p["volume_mm3"] > 0 for p in report["interference"]["new_pairs"])
    # Blocked means blocked: nothing moved, and the merge stays staged.
    assert service.history.resolve_ref(canonical, "master") == head_before
    assert registry.call("merge_status", {"project": proj})["merge"]

    landed = registry.call("merge_branch", {
        "project": proj, "source": "feat", "allow_invalid": True})
    assert "error" not in landed, landed
    assert landed["validation"]["ok"] is False
    assert landed["validation"]["blocked"] is False
    landed_pairs = {
        frozenset((p["a"], p["b"]))
        for p in landed["validation"]["interference"]["new_pairs"]
    }
    assert landed_pairs == pairs
    message = _commit_message(canonical, "master")
    assert "Validation: FAILED" in message and "allow_invalid" in message
    assert "box_1" in message and "pin_1" in message
    assert len(_parents(canonical, "master")) == 2


# ------------------------------------------------------------------- AC5


def test_ac5_tag_round_trip_and_survives_branch_delete(stack):
    """AC5 — tag → mutate → restore round-trips byte-identically (manifest and
    every script), and the tag outlives the branch it was made on."""
    service, registry = stack
    proj = _demo(stack)
    canonical = service.store.canonical_path_of(proj)

    _on(service, proj, "agent_b", "master")
    tagged = registry.call("version_tag", {
        "project": proj, "name": "shop-rev-a", "message": "sent to the shop"})
    assert "error" not in tagged, tagged
    assert tagged["tag"] == "shop-rev-a"
    before = {
        path.relative_to(canonical).as_posix(): path.read_bytes()
        for path in [canonical / "project.json",
                     *sorted((canonical / "parts").glob("*.py"))]
    }
    assert set(before) == {"project.json", "parts/box.py", "parts/pin.py"}

    versions = registry.call("list_versions", {"project": proj})["versions"]
    assert [v["name"] for v in versions] == ["shop-rev-a"]
    assert versions[0]["message"] == "sent to the shop"
    assert versions[0]["author"] and versions[0]["ts"]

    # Mutate both a script and the manifest, on two branches.
    assert "error" not in registry.call(
        "update_part_script",
        {"project": proj, "part_id": "box", "script": BOX_V2_SCRIPT})
    assert "error" not in registry.call(
        "set_params",
        {"project": proj, "part_id": "pin", "values": {"size": 30.0}})
    assert (canonical / "parts" / "box.py").read_bytes() != before["parts/box.py"]
    assert (canonical / "project.json").read_bytes() != before["project.json"]

    _on(service, proj, "agent_a", "feat")
    assert "error" not in registry.call(
        "update_part_script",
        {"project": proj, "part_id": "pin", "script": BOX_V3_SCRIPT})

    # Restore the version by NAME (project_restore accepts refs).
    _on(service, proj, "agent_a", "master")   # free 'feat' for deletion below
    _on(service, proj, "agent_b", "master")
    restored = registry.call("project_restore",
                             {"project": proj, "commit": "shop-rev-a"})
    assert "error" not in restored, restored
    assert restored["restored"] == "shop-rev-a"
    after = {
        path.relative_to(canonical).as_posix(): path.read_bytes()
        for path in [canonical / "project.json",
                     *sorted((canonical / "parts").glob("*.py"))]
    }
    assert after == before

    # The tag survives deleting the branch that existed alongside it, and it
    # still restores afterwards.
    service.branches.delete(proj, "feat")
    assert "feat" not in {b["name"]
                          for b in registry.call(
                              "branch_list", {"project": proj})["branches"]}
    still_there = registry.call("list_versions", {"project": proj})["versions"]
    assert [v["name"] for v in still_there] == ["shop-rev-a"]
    assert still_there[0]["commit"] == versions[0]["commit"]

    assert "error" not in registry.call(
        "update_part_script",
        {"project": proj, "part_id": "box", "script": BOX_V3_SCRIPT})
    again = registry.call("project_restore",
                          {"project": proj, "commit": "shop-rev-a"})
    assert "error" not in again, again
    assert (canonical / "parts" / "box.py").read_bytes() == before["parts/box.py"]


# ------------------------------------------------------------------- AC6



def test_ac6_browser_session_evidence_is_recorded():
    """AC6 — the browser session (create branch, edit, switch back and forth,
    merge clean, resolve a conflict, zero console errors) was driven for real
    in slice 4. This is the evidence check: it asserts the session and its
    screenshots are recorded in the changelog, so AC6 has a named, failing-if-
    removed check without re-driving a browser from the test suite.
    """
    entry = REPO_ROOT / "docs" / "changelog" / "0070-branching-ui.md"
    assert entry.is_file(), "slice 4 changelog entry is missing"
    text = entry.read_text(encoding="utf-8")
    assert "AC6" in text
    for phrase in ("branch", "merge", "conflict", "Console"):
        assert phrase in text, f"browser evidence does not mention {phrase!r}"


# ------------------------------------------------------------------- AC7


def test_ac7_history_and_undo_are_unchanged_on_the_default_branch(stack):
    """AC7 (behavioral half) — with the versioning pack installed but no
    branch switched to, ``project_history``/``project_restore``/``undo``/
    ``redo`` behave exactly as they did before branching: linear history on the
    default branch, the project name as the lock/undo key, one snapshot per
    mutation.

    (The other half — the full suite green and no pre-existing test file
    edited — is a command, cited in the slice-5 changelog:
    ``make test`` and ``git diff --name-status main -- tests/``.)
    """
    service, registry = stack
    assert "error" not in registry.call("create_project", {"name": "demo"})
    assert "error" not in registry.call(
        "create_part", {"project": "demo", "part_id": "box", "script": BOX_SCRIPT})

    # The store key is still the bare project name while nobody has branched.
    assert service.store.lock_key("demo") == "demo"
    assert service.store.path_of("demo") == service.store.canonical_path_of("demo")

    history = registry.call("project_history", {"project": "demo"})
    assert history["available"] is True
    depth = len(history["history"])

    assert "error" not in registry.call(
        "update_part_script",
        {"project": "demo", "part_id": "box", "script": BOX_V2_SCRIPT})
    after = registry.call("project_history", {"project": "demo"})["history"]
    assert len(after) == depth + 1          # exactly one snapshot per mutation

    undone = registry.call("undo", {"project": "demo"})
    assert "error" not in undone, undone
    assert service.store.read_script("demo", "box") == BOX_SCRIPT
    redone = registry.call("redo", {"project": "demo"})
    assert "error" not in redone, redone
    assert service.store.read_script("demo", "box") == BOX_V2_SCRIPT

    # ...and restore by commit id still works verbatim.
    entries = registry.call("project_history", {"project": "demo"})["history"]
    restored = registry.call(
        "project_restore", {"project": "demo", "commit": entries[-1]["id"]})
    assert "error" not in restored, restored
