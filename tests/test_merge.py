"""Branch merge orchestration, staging and the kernel validation pass.

PRD-001 slice 3 (FR6–FR10): ``git merge-tree`` for scripts, the structure-aware
driver for ``project.json``, a staged merge that is never partially applied, a
validation pass that rebuilds the merged state with the real kernel before the
two-parent merge commit lands, and compare-and-swap ref updates.

Git-touching tests carry ``integration`` + ``portability`` and skip without
git, mirroring ``tests/test_history.py``.
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

_GIT = [
    pytest.mark.integration,
    pytest.mark.portability,
    pytest.mark.skipif(shutil.which("git") is None, reason="git not found on PATH"),
]

BOX_V2_SCRIPT = BOX_SCRIPT.replace(
    "Box(p.size, p.size, p.size)", "Box(p.size, p.size, p.size * 2)"
)
BOX_V3_SCRIPT = BOX_SCRIPT.replace(
    "Box(p.size, p.size, p.size)", "Box(p.size, p.size, p.size * 3)"
)
BROKEN_SCRIPT = BOX_SCRIPT.replace("return part.part", "return no_such_name")
assert BOX_V2_SCRIPT != BOX_SCRIPT != BOX_V3_SCRIPT

# A plate/pin pair with connectors, so the validation pass can re-resolve a
# real mate: no bundled example declares one.
PLATE_SCRIPT = '''\
from build123d import *

PARAMS = {"t": {"default": 10.0, "min": 1.0, "max": 50.0}}

def build(p):
    with BuildPart() as part:
        Box(40, 40, p.t)
    return part.part

def connectors(p, part):
    return {"top": {"type": "rigid", "location": ((0, 0, p.t / 2), (0, 0, 0))}}
'''

PIN_SCRIPT = '''\
from build123d import *

PARAMS = {"h": {"default": 15.0, "min": 1.0, "max": 50.0}}

def build(p):
    with BuildPart() as part:
        Cylinder(radius=3, height=p.h, align=(Align.CENTER, Align.CENTER, Align.MIN))
    return part.part

def connectors(p, part):
    return {"base": {"type": "rigid", "location": ((0, 0, 0), (0, 0, 0))}}
'''


@pytest.fixture(autouse=True)
def _reset_context():
    cid = locks.client_id_var.set("local")
    pin = pinned_tree_var.set(None)
    yield
    locks.client_id_var.reset(cid)
    pinned_tree_var.reset(pin)


def _git(git_dir: Path, work_tree: Path, *args: str, stdin: str | None = None) -> str:
    env = {
        **os.environ,
        "GIT_CONFIG_NOSYSTEM": "1",
        "HOME": str(git_dir),
        "XDG_CONFIG_HOME": str(git_dir / "xdg"),
    }
    result = subprocess.run(
        ["git", "--git-dir", str(git_dir), "--work-tree", str(work_tree), *args],
        cwd=str(work_tree), env=env, capture_output=True, text=True, check=True,
        input=stdin,
    )
    return result.stdout.strip()


@pytest.fixture
def stack(kernel, tmp_path):
    """Real service + registry: the versioning pack installs branches/merges."""
    bus = EventBus()
    service = AgentCADService(tmp_path / "projects", kernel, bus)
    registry = build_registry(service)
    assert getattr(service, "branches", None) is not None
    assert getattr(service, "merges", None) is not None
    return service, registry


@pytest.fixture
def demo(stack):
    """Project 'demo' with two parts and a branch 'feat' forked from master."""
    service, registry = stack
    assert "error" not in registry.call("create_project", {"name": "demo"})
    for part in ("box", "pin"):
        created = registry.call(
            "create_part",
            {"project": "demo", "part_id": part, "script": BOX_SCRIPT},
        )
        assert "error" not in created, created
    service.branches.create("demo", "feat")
    return service, registry


def _on(service, client: str, branch: str) -> None:
    """Put a client on a branch (identity + checkout)."""
    locks.set_client_id(client)
    if service.branches.current("demo") != branch:
        service.branches.switch("demo", branch)


def _script(registry, part: str, text: str, *, builds: bool = True) -> dict:
    result = registry.call(
        "update_part_script",
        {"project": "demo", "part_id": part, "script": text},
    )
    # A script that does not build is still written and snapshotted; only the
    # rebuild result reports the failure.
    assert ("error" not in result) is builds, result
    return result


def _parents(canonical: Path, ref: str) -> list[str]:
    return _git(canonical / ".history", canonical,
                "rev-list", "--parents", "-n", "1", ref).split()[1:]


# ------------------------------------------------------------- 1. plumbing


class TestPlumbing:
    pytestmark = _GIT

    def test_merge_tree_reports_the_tree_and_per_path_stages(self, demo):
        service, registry = demo
        canonical = service.store.canonical_path_of("demo")

        _on(service, "agent_a", "feat")
        _script(registry, "box", BOX_V2_SCRIPT)
        _on(service, "agent_b", "master")
        _script(registry, "box", BOX_V3_SCRIPT)

        target = service.history.resolve_ref(canonical, "master")
        source = service.history.resolve_ref(canonical, "feat")
        tree, stages = service.merges._merge_tree(canonical, target, source)

        assert len(tree) == 40
        assert "parts/box.py" in stages
        entry = stages["parts/box.py"]
        assert set(entry) == {1, 2, 3}
        assert service.merges._blob(canonical, entry[2]) == BOX_V3_SCRIPT
        assert service.merges._blob(canonical, entry[3]) == BOX_V2_SCRIPT
        assert service.merges._blob(canonical, entry[1]) == BOX_SCRIPT
        assert service.merges._blob(canonical, None) is None

    def test_clean_merge_tree_reports_no_stages(self, demo):
        service, registry = demo
        canonical = service.store.canonical_path_of("demo")
        _on(service, "agent_a", "feat")
        _script(registry, "box", BOX_V2_SCRIPT)
        _on(service, "agent_b", "master")
        _script(registry, "pin", BOX_V3_SCRIPT)

        tree, stages = service.merges._merge_tree(
            canonical,
            service.history.resolve_ref(canonical, "master"),
            service.history.resolve_ref(canonical, "feat"),
        )
        assert stages == {}
        assert len(tree) == 40

    def test_old_git_is_refused_by_name(self, demo, monkeypatch):
        service, registry = demo
        monkeypatch.setattr(service.merges, "_git_version", lambda path: (2, 30))
        _on(service, "agent_a", "feat")
        _script(registry, "box", BOX_V2_SCRIPT)
        _on(service, "agent_b", "master")
        result = registry.call("merge_branch", {"project": "demo", "source": "feat"})
        assert result["error"]["type"] == "validation_error"
        assert "2.38" in result["error"]["message"]


# ------------------------------------------------- 2. merge, stage, conflict


class TestMerge:
    pytestmark = _GIT

    def test_disjoint_parts_merge_clean_with_two_parents(self, demo):
        service, registry = demo
        canonical = service.store.canonical_path_of("demo")

        _on(service, "agent_a", "feat")
        _script(registry, "box", BOX_V2_SCRIPT)
        source_head = service.history.resolve_ref(canonical, "feat")
        _on(service, "agent_b", "master")
        _script(registry, "pin", BOX_V3_SCRIPT)
        target_head = service.history.resolve_ref(canonical, "master")

        result = registry.call("merge_branch", {"project": "demo", "source": "feat"})
        assert "error" not in result, result
        assert result["merged"] is True
        assert result["validation"]["ok"] is True
        assert result["conflicts_resolved"] == 0
        parents = _parents(canonical, "master")
        assert parents == [target_head, source_head]
        assert (canonical / "parts" / "box.py").read_text() == BOX_V2_SCRIPT
        assert (canonical / "parts" / "pin.py").read_text() == BOX_V3_SCRIPT
        assert service.store.read_script("demo", "box") == BOX_V2_SCRIPT
        # no staged state survives a completed merge
        assert not (canonical / ".history" / "agentcad" / "merge.json").exists()
        assert registry.call("merge_status", {"project": "demo"})["merge"] is None

    def test_fast_forward_moves_the_ref_and_the_tree(self, demo):
        service, registry = demo
        canonical = service.store.canonical_path_of("demo")
        _on(service, "agent_a", "feat")
        _script(registry, "box", BOX_V2_SCRIPT)
        source_head = service.history.resolve_ref(canonical, "feat")

        _on(service, "agent_b", "master")
        result = registry.call("merge_branch", {"project": "demo", "source": "feat"})
        assert "error" not in result, result
        assert result["fast_forward"] is True
        # FR9 has no fast-forward exemption (see TestFastForwardValidation).
        assert result["validation"]["ok"] is True
        assert service.history.resolve_ref(canonical, "master") == source_head
        assert len(_parents(canonical, "master")) == 1
        assert (canonical / "parts" / "box.py").read_text() == BOX_V2_SCRIPT

    def test_already_up_to_date_is_a_noop(self, demo):
        service, registry = demo
        canonical = service.store.canonical_path_of("demo")
        _on(service, "agent_b", "master")
        _script(registry, "box", BOX_V2_SCRIPT)
        head = service.history.resolve_ref(canonical, "master")

        result = registry.call("merge_branch", {"project": "demo", "source": "feat"})
        assert "error" not in result, result
        assert result["already_up_to_date"] is True
        assert service.history.resolve_ref(canonical, "master") == head

    def test_script_conflict_stages_and_changes_nothing(self, demo):
        service, registry = demo
        canonical = service.store.canonical_path_of("demo")

        _on(service, "agent_a", "feat")
        _script(registry, "box", BOX_V2_SCRIPT)
        _on(service, "agent_b", "master")
        _script(registry, "box", BOX_V3_SCRIPT)
        head_before = service.history.resolve_ref(canonical, "master")
        script_before = (canonical / "parts" / "box.py").read_bytes()
        manifest_before = (canonical / "project.json").read_bytes()

        result = registry.call("merge_branch", {"project": "demo", "source": "feat"})
        error = result["error"]
        assert error["type"] == "merge_conflict"
        details = error["details"]
        assert details["source"] == "feat" and details["target"] == "master"
        assert details["outstanding"] == 1
        conflict = details["conflicts"][0]
        assert conflict["kind"] == "script"
        assert conflict["path"] == "parts/box.py"
        assert conflict["part"] == "box"
        assert conflict["ours"] == BOX_V3_SCRIPT      # ours = target
        assert conflict["theirs"] == BOX_V2_SCRIPT    # theirs = source
        assert conflict["base"] == BOX_SCRIPT
        for marker in ("<<<<<<<", "|||||||", "=======", ">>>>>>>"):
            assert marker in conflict["merged"]
        assert "master" in conflict["merged"] and "feat" in conflict["merged"]
        assert conflict["truncated"] is False
        assert "resolve_merge" in details["hint"]

        # Nothing outside .history/agentcad/ moved, and no ref moved.
        assert service.history.resolve_ref(canonical, "master") == head_before
        assert (canonical / "parts" / "box.py").read_bytes() == script_before
        assert (canonical / "project.json").read_bytes() == manifest_before
        staged = canonical / ".history" / "agentcad"
        assert (staged / "merge.json").is_file()
        assert list(staged.glob("merge-*"))

        status = registry.call("merge_status", {"project": "demo"})
        assert status["merge"]["source"] == "feat"
        assert status["merge"]["target"] == "master"
        assert status["merge"]["outstanding"] == 1

    def test_manifest_conflict_names_the_key(self, demo):
        service, registry = demo
        _on(service, "agent_a", "feat")
        assert "error" not in registry.call(
            "set_params", {"project": "demo", "part_id": "box",
                           "values": {"size": 12.0}})
        _on(service, "agent_b", "master")
        assert "error" not in registry.call(
            "set_params", {"project": "demo", "part_id": "box",
                           "values": {"size": 15.0}})

        result = registry.call("merge_branch", {"project": "demo", "source": "feat"})
        conflicts = result["error"]["details"]["conflicts"]
        assert [c["kind"] for c in conflicts] == ["manifest"]
        assert conflicts[0]["key"] == "parts.box.params.size"
        assert conflicts[0]["ours"] == 15.0 and conflicts[0]["theirs"] == 12.0

    def test_param_vs_script_edits_merge_clean(self, demo):
        """FR8 / AC3: different keys of one part, both land."""
        service, registry = demo
        _on(service, "agent_a", "feat")
        _script(registry, "box", BOX_V2_SCRIPT)
        _on(service, "agent_b", "master")
        assert "error" not in registry.call(
            "set_params", {"project": "demo", "part_id": "box",
                           "values": {"size": 12.0}})

        result = registry.call("merge_branch", {"project": "demo", "source": "feat"})
        assert "error" not in result, result
        assert result["validation"]["ok"] is True
        canonical = service.store.canonical_path_of("demo")
        assert (canonical / "parts" / "box.py").read_text() == BOX_V2_SCRIPT
        manifest = json.loads((canonical / "project.json").read_text())
        box = [p for p in manifest["parts"] if p["id"] == "box"][0]
        assert box["params"]["size"] == 12.0

    def test_resolve_merge_completes_the_merge(self, demo):
        service, registry = demo
        canonical = service.store.canonical_path_of("demo")
        _on(service, "agent_a", "feat")
        _script(registry, "box", BOX_V2_SCRIPT)
        assert "error" not in registry.call(
            "set_params", {"project": "demo", "part_id": "pin",
                           "values": {"size": 12.0}})
        _on(service, "agent_b", "master")
        _script(registry, "box", BOX_V3_SCRIPT)
        assert "error" not in registry.call(
            "set_params", {"project": "demo", "part_id": "pin",
                           "values": {"size": 15.0}})

        first = registry.call("merge_branch", {"project": "demo", "source": "feat"})
        assert first["error"]["type"] == "merge_conflict"
        assert first["error"]["details"]["outstanding"] == 2

        partial = registry.call("resolve_merge", {
            "project": "demo",
            "choices": {"parts/box.py": {"take": "theirs"}},
        })
        assert partial["error"]["type"] == "merge_conflict"
        remaining = partial["error"]["details"]["conflicts"]
        assert [c["key"] for c in remaining] == ["parts.pin.params.size"]

        done = registry.call("resolve_merge", {
            "project": "demo",
            "choices": {"parts.pin.params.size": {"value": 20.0}},
        })
        assert "error" not in done, done
        assert done["conflicts_resolved"] == 2
        assert (canonical / "parts" / "box.py").read_text() == BOX_V2_SCRIPT
        manifest = json.loads((canonical / "project.json").read_text())
        pin = [p for p in manifest["parts"] if p["id"] == "pin"][0]
        assert pin["params"]["size"] == 20.0
        assert len(_parents(canonical, "master")) == 2
        assert not (canonical / ".history" / "agentcad" / "merge.json").exists()

    def test_resolve_merge_accepts_hand_written_content(self, demo):
        service, registry = demo
        canonical = service.store.canonical_path_of("demo")
        _on(service, "agent_a", "feat")
        _script(registry, "box", BOX_V2_SCRIPT)
        _on(service, "agent_b", "master")
        _script(registry, "box", BOX_V3_SCRIPT)
        assert registry.call(
            "merge_branch", {"project": "demo", "source": "feat"}
        )["error"]["type"] == "merge_conflict"

        merged_by_hand = BOX_SCRIPT.replace(
            "Box(p.size, p.size, p.size)", "Box(p.size, p.size, p.size * 4)"
        )
        done = registry.call("resolve_merge", {
            "project": "demo",
            "choices": {"parts/box.py": {"content": merged_by_hand}},
        })
        assert "error" not in done, done
        assert (canonical / "parts" / "box.py").read_text() == merged_by_hand

    def test_resolve_merge_rejects_unknown_keys(self, demo):
        service, registry = demo
        _on(service, "agent_a", "feat")
        _script(registry, "box", BOX_V2_SCRIPT)
        _on(service, "agent_b", "master")
        _script(registry, "box", BOX_V3_SCRIPT)
        registry.call("merge_branch", {"project": "demo", "source": "feat"})

        bad = registry.call("resolve_merge", {
            "project": "demo", "choices": {"parts/ghost.py": {"take": "ours"}}})
        assert bad["error"]["type"] == "validation_error"
        # ...and the staged merge survives an invalid resolution attempt.
        assert registry.call("merge_status", {"project": "demo"})["merge"]

    def test_resolve_without_a_staged_merge_is_a_conflict(self, demo):
        service, registry = demo
        _on(service, "agent_b", "master")
        result = registry.call("resolve_merge", {"project": "demo", "choices": {}})
        assert result["error"]["type"] == "conflict_error"

    def test_merge_abort_removes_the_staged_worktree(self, demo):
        service, registry = demo
        canonical = service.store.canonical_path_of("demo")
        _on(service, "agent_a", "feat")
        _script(registry, "box", BOX_V2_SCRIPT)
        _on(service, "agent_b", "master")
        _script(registry, "box", BOX_V3_SCRIPT)
        registry.call("merge_branch", {"project": "demo", "source": "feat"})
        assert list((canonical / ".history" / "agentcad").glob("merge-*"))

        aborted = registry.call("merge_abort", {"project": "demo"})
        assert aborted["aborted"] is True
        assert aborted["source"] == "feat" and aborted["target"] == "master"
        assert not (canonical / ".history" / "agentcad" / "merge.json").exists()
        assert not list((canonical / ".history" / "agentcad").glob("merge-*"))
        listing = _git(canonical / ".history", canonical, "worktree", "list")
        assert "merge-" not in listing

        again = registry.call("merge_abort", {"project": "demo"})
        assert again["aborted"] is False

    def test_a_staged_merge_of_another_pair_is_refused(self, demo):
        service, registry = demo
        service.branches.create("demo", "other")
        _on(service, "agent_a", "feat")
        _script(registry, "box", BOX_V2_SCRIPT)
        _on(service, "agent_c", "other")
        _script(registry, "box", BOX_V2_SCRIPT.replace("* 2", "* 5"))
        _on(service, "agent_b", "master")
        _script(registry, "box", BOX_V3_SCRIPT)
        assert registry.call(
            "merge_branch", {"project": "demo", "source": "feat"}
        )["error"]["type"] == "merge_conflict"

        blocked = registry.call("merge_branch", {"project": "demo", "source": "other"})
        assert blocked["error"]["type"] == "conflict_error"
        assert registry.call("merge_status", {"project": "demo"})["merge"]["source"] \
            == "feat"

    def test_concurrent_target_move_fails_the_compare_and_swap(self, demo):
        service, registry = demo
        canonical = service.store.canonical_path_of("demo")
        _on(service, "agent_a", "feat")
        _script(registry, "box", BOX_V2_SCRIPT)
        _on(service, "agent_b", "master")
        _script(registry, "box", BOX_V3_SCRIPT)
        assert registry.call(
            "merge_branch", {"project": "demo", "source": "feat"}
        )["error"]["type"] == "merge_conflict"

        # Someone commits on the target behind the staged merge's back.
        (canonical / "parts" / "pin.py").write_text(BOX_V2_SCRIPT, encoding="utf-8")
        moved = service.history.snapshot(canonical, "behind our back")
        assert moved

        result = registry.call("resolve_merge", {
            "project": "demo", "choices": {"parts/box.py": {"take": "ours"}}})
        assert result["error"]["type"] == "conflict_error"
        assert service.history.resolve_ref(canonical, "master") == moved
        assert registry.call("merge_status", {"project": "demo"})["merge"]

    def test_a_turn_held_on_the_target_blocks_the_merge(self, demo):
        service, registry = demo
        _on(service, "agent_a", "feat")
        _script(registry, "box", BOX_V2_SCRIPT)
        _on(service, "agent_b", "master")
        _script(registry, "pin", BOX_V3_SCRIPT)
        assert "error" not in registry.call("acquire_turn", {"project": "demo"})

        locks.set_client_id("agent_a")   # still on 'feat', merging into master
        blocked = registry.call("merge_branch", {
            "project": "demo", "source": "feat", "target": "master"})
        assert blocked["error"]["type"] == "conflict_error"
        assert "agent_b" in blocked["error"]["message"]

    def test_an_oversized_conflict_body_is_elided_from_the_payload(self, demo,
                                                                   monkeypatch):
        """truncated:true must cover the diff3 body too — it is roughly the
        sum of the sides it already elides. The staged file keeps it all."""
        from agentcad.core import merge as merge_mod

        service, registry = demo
        canonical = service.store.canonical_path_of("demo")
        monkeypatch.setattr(merge_mod, "_MAX_BODY_BYTES", 256)
        _on(service, "agent_a", "feat")
        _script(registry, "box", BOX_V2_SCRIPT + "# feat\n" * 60)
        _on(service, "agent_b", "master")
        _script(registry, "box", BOX_V3_SCRIPT + "# master\n" * 60)

        result = registry.call("merge_branch", {"project": "demo",
                                                "source": "feat"})
        conflict = result["error"]["details"]["conflicts"][0]
        assert conflict["truncated"] is True
        assert conflict["merged"] is None
        assert "ours" not in conflict and "theirs" not in conflict

        staged = next(
            (canonical / ".history" / "agentcad").glob("merge-*")
        ) / "parts" / "box.py"
        body = staged.read_text()
        assert "<<<<<<<" in body and len(body.encode()) > 256

    def test_a_moved_target_invalidates_the_staged_merge_loudly(self, demo):
        """Re-merging a staged merge whose branches moved must NOT silently
        drop the resolutions recorded against the old heads."""
        service, registry = demo
        canonical = service.store.canonical_path_of("demo")
        _on(service, "agent_a", "feat")
        _script(registry, "box", BOX_V2_SCRIPT)
        assert "error" not in registry.call(
            "set_params", {"project": "demo", "part_id": "pin",
                           "values": {"size": 12.0}})
        _on(service, "agent_b", "master")
        _script(registry, "box", BOX_V3_SCRIPT)
        assert "error" not in registry.call(
            "set_params", {"project": "demo", "part_id": "pin",
                           "values": {"size": 15.0}})
        assert registry.call(
            "merge_branch", {"project": "demo", "source": "feat"}
        )["error"]["type"] == "merge_conflict"
        partial = registry.call("resolve_merge", {
            "project": "demo", "choices": {"parts/box.py": {"take": "theirs"}}})
        assert partial["error"]["type"] == "merge_conflict"

        # The target moves behind the staged merge's back.
        (canonical / "parts" / "pin.py").write_text(BOX_V2_SCRIPT,
                                                    encoding="utf-8")
        assert service.history.snapshot(canonical, "behind our back")

        blocked = registry.call("merge_branch", {"project": "demo",
                                                 "source": "feat"})
        assert blocked["error"]["type"] == "conflict_error"
        assert blocked["error"]["details"]["resolved"] == 1
        assert "merge_abort" in blocked["error"]["message"]
        # ...and the recorded resolution is still there to be discarded.
        staged = registry.call("merge_status", {"project": "demo"})["merge"]
        assert staged["resolved"] == ["parts/box.py"]
        assert registry.call("merge_abort", {"project": "demo"})["aborted"]

    def test_unknown_branches_are_not_found(self, demo):
        service, registry = demo
        _on(service, "agent_b", "master")
        missing = registry.call("merge_branch", {"project": "demo", "source": "ghost"})
        assert missing["error"]["type"] == "notfound_error"
        same = registry.call("merge_branch", {"project": "demo", "source": "master"})
        assert same["error"]["type"] == "validation_error"


# --------------------------------------------- 2b. binary files, absent sides


# Non-UTF-8 payloads with NUL bytes: what every tracked file under imports/
# (STL/STEP) really is.
OURS_STL = bytes(range(256)) * 4
THEIRS_STL = bytes(range(255, -1, -1)) * 4
BASE_STL = bytes([7, 0, 255, 128]) * 256
assert len({OURS_STL, THEIRS_STL, BASE_STL}) == 3
assert all(len(b) == 1024 for b in (OURS_STL, THEIRS_STL, BASE_STL))


def _write_binary(service, branch: str, name: str, data: bytes) -> Path:
    """Put raw bytes under imports/ on ``branch`` and snapshot them."""
    tree = service.branches.tree_of("demo", branch)
    path = tree / "imports" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    assert service.history.snapshot(tree, f"import {name} on {branch}")
    return path


class TestBinaryFiles:
    """Binary content must never round-trip through str: the git plumbing
    decodes text with errors='replace', which silently rewrites every
    non-UTF-8 byte into U+FFFD and commits the result as a clean merge."""

    pytestmark = _GIT

    def _stage_conflict(self, service, registry) -> dict:
        _on(service, "agent_a", "feat")
        _write_binary(service, "feat", "model.stl", THEIRS_STL)
        _on(service, "agent_b", "master")
        _write_binary(service, "master", "model.stl", OURS_STL)
        result = registry.call("merge_branch", {"project": "demo",
                                                "source": "feat"})
        assert result["error"]["type"] == "merge_conflict", result
        conflicts = result["error"]["details"]["conflicts"]
        assert [c["path"] for c in conflicts] == ["imports/model.stl"]
        return conflicts[0]

    def test_binary_conflict_carries_no_text_and_takes_a_side(self, demo):
        service, registry = demo
        canonical = service.store.canonical_path_of("demo")
        conflict = self._stage_conflict(service, registry)

        assert conflict["kind"] == "binary"
        for key in ("merged", "ours", "theirs", "base"):
            assert key not in conflict, key
        assert conflict["sides"]["ours"]["bytes"] == len(OURS_STL)
        assert conflict["sides"]["theirs"]["bytes"] == len(THEIRS_STL)
        assert conflict["sides"]["base"] is None  # both branches added it
        assert conflict["sides"]["ours"]["sha256"] != \
            conflict["sides"]["theirs"]["sha256"]

        # Hand-written content cannot describe bytes.
        rejected = registry.call("resolve_merge", {
            "project": "demo",
            "choices": {"imports/model.stl": {"content": "solid\n"}}})
        assert rejected["error"]["type"] == "validation_error"
        assert "binary" in rejected["error"]["message"]

        done = registry.call("resolve_merge", {
            "project": "demo",
            "choices": {"imports/model.stl": {"take": "theirs"}}})
        assert "error" not in done, done
        assert (canonical / "imports" / "model.stl").read_bytes() == THEIRS_STL

    def test_taking_ours_keeps_the_target_bytes_exactly(self, demo):
        service, registry = demo
        canonical = service.store.canonical_path_of("demo")
        self._stage_conflict(service, registry)

        done = registry.call("resolve_merge", {
            "project": "demo",
            "choices": {"imports/model.stl": {"take": "ours"}}})
        assert "error" not in done, done
        assert (canonical / "imports" / "model.stl").read_bytes() == OURS_STL

    def test_a_one_sided_binary_change_merges_byte_exact(self, demo):
        service, registry = demo
        canonical = service.store.canonical_path_of("demo")
        _on(service, "agent_b", "master")
        _write_binary(service, "master", "model.stl", BASE_STL)
        service.branches.delete("demo", "feat")
        service.branches.create("demo", "feat")  # forked with the STL in place

        _on(service, "agent_a", "feat")
        _write_binary(service, "feat", "model.stl", THEIRS_STL)
        _on(service, "agent_b", "master")
        _script(registry, "box", BOX_V3_SCRIPT)  # keep it a real merge

        result = registry.call("merge_branch", {"project": "demo",
                                                "source": "feat"})
        assert "error" not in result, result
        assert result["fast_forward"] is False
        assert (canonical / "imports" / "model.stl").read_bytes() == THEIRS_STL


class TestAbsentSides:
    """Taking a side that does not exist: the file was deleted there (so the
    merge must delete it), or there is no base at all (so 'base' is refused
    instead of leaving conflict markers in the staged file)."""

    pytestmark = _GIT

    def test_taking_the_side_that_deleted_the_file_deletes_it(self, demo):
        service, registry = demo
        canonical = service.store.canonical_path_of("demo")
        _on(service, "agent_a", "feat")
        assert "error" not in registry.call(
            "delete_part", {"project": "demo", "part_id": "box"})
        _on(service, "agent_b", "master")
        _script(registry, "box", BOX_V3_SCRIPT)

        result = registry.call("merge_branch", {"project": "demo",
                                                "source": "feat"})
        assert result["error"]["type"] == "merge_conflict", result
        conflict = result["error"]["details"]["conflicts"][0]
        assert conflict["path"] == "parts/box.py"
        assert conflict["theirs"] is None       # the source deleted it

        done = registry.call("resolve_merge", {
            "project": "demo", "choices": {"parts/box.py": {"take": "theirs"}}})
        assert "error" not in done, done
        assert not (canonical / "parts" / "box.py").exists()
        manifest = json.loads((canonical / "project.json").read_text())
        assert [p["id"] for p in manifest["parts"]] == ["pin"]

    def test_taking_base_when_both_branches_added_the_file_is_refused(self, demo):
        service, registry = demo
        canonical = service.store.canonical_path_of("demo")
        _on(service, "agent_a", "feat")
        assert "error" not in registry.call(
            "create_part", {"project": "demo", "part_id": "gizmo",
                            "script": BOX_V2_SCRIPT})
        _on(service, "agent_b", "master")
        assert "error" not in registry.call(
            "create_part", {"project": "demo", "part_id": "gizmo",
                            "script": BOX_V3_SCRIPT})

        result = registry.call("merge_branch", {"project": "demo",
                                                "source": "feat"})
        assert result["error"]["type"] == "merge_conflict", result
        conflict = [c for c in result["error"]["details"]["conflicts"]
                    if c.get("path") == "parts/gizmo.py"][0]
        assert conflict["base"] is None

        refused = registry.call("resolve_merge", {
            "project": "demo", "choices": {"parts/gizmo.py": {"take": "base"}}})
        assert refused["error"]["type"] == "validation_error"
        assert "no base" in refused["error"]["message"]
        assert refused["error"]["details"]["valid"] == ["ours", "theirs"]
        assert registry.call("merge_status", {"project": "demo"})["merge"]

        done = registry.call("resolve_merge", {
            "project": "demo", "choices": {"parts/gizmo.py": {"take": "ours"}}})
        assert "error" not in done, done
        landed = (canonical / "parts" / "gizmo.py").read_text()
        assert landed == BOX_V3_SCRIPT
        assert "<<<<<<<" not in landed


# -------------------------------------------------------- 3. validation pass


class TestValidationPass:
    pytestmark = _GIT

    def test_a_broken_script_blocks_then_lands_with_allow_invalid(self, demo):
        service, registry = demo
        canonical = service.store.canonical_path_of("demo")
        _on(service, "agent_a", "feat")
        _script(registry, "box", BROKEN_SCRIPT, builds=False)
        _on(service, "agent_b", "master")
        _script(registry, "pin", BOX_V2_SCRIPT)
        head_before = service.history.resolve_ref(canonical, "master")

        blocked = registry.call("merge_branch", {"project": "demo", "source": "feat"})
        assert blocked["error"]["type"] == "validation_error"
        validation = blocked["error"]["details"]["validation"]
        assert validation["ok"] is False and validation["blocked"] is True
        assert [f["part"] for f in validation["failures"]] == ["box"]
        assert validation["failures"][0]["error"]["type"]
        assert service.history.resolve_ref(canonical, "master") == head_before
        assert registry.call("merge_status", {"project": "demo"})["merge"]

        landed = registry.call("merge_branch", {
            "project": "demo", "source": "feat", "allow_invalid": True})
        assert "error" not in landed, landed
        assert landed["validation"]["ok"] is False
        assert landed["validation"]["blocked"] is False
        message = _git(canonical / ".history", canonical,
                       "log", "-1", "--pretty=%B", "master")
        assert "Validation: FAILED" in message
        assert "allow_invalid" in message
        assert (canonical / "parts" / "box.py").read_text() == BROKEN_SCRIPT

    def test_dangling_instance_blocks_the_merge(self, demo):
        """The Slice-1 'clean but broken' case: ours deletes a part while
        theirs adds an instance of it."""
        service, registry = demo
        _on(service, "agent_a", "feat")
        assert "error" not in registry.call("set_assembly", {
            "project": "demo",
            "instances": [{"id": "pin_1", "part": "pin", "position": [0, 0, 0]}],
        })
        _on(service, "agent_b", "master")
        assert "error" not in registry.call(
            "delete_part", {"project": "demo", "part_id": "pin"})

        blocked = registry.call("merge_branch", {"project": "demo", "source": "feat"})
        assert blocked["error"]["type"] == "validation_error"
        integrity = blocked["error"]["details"]["validation"]["integrity"]
        assert integrity == [
            {"kind": "dangling_instance", "instance": "pin_1", "part": "pin"}
        ]

    def test_interference_blocks_only_new_pairs(self, demo):
        """AC4: a merge that introduces an overlap is blocked with the pair
        named, and lands with allow_invalid."""
        service, registry = demo
        canonical = service.store.canonical_path_of("demo")
        _on(service, "agent_b", "master")
        assert "error" not in registry.call("set_assembly", {
            "project": "demo",
            "instances": [
                {"id": "box_1", "part": "box", "position": [0, 0, 0]},
                {"id": "pin_1", "part": "pin", "position": [100, 0, 0]},
            ],
        })
        service.branches.delete("demo", "feat")
        service.branches.create("demo", "feat")

        _on(service, "agent_a", "feat")           # move box_1 next to nothing
        assert "error" not in registry.call("set_assembly", {
            "project": "demo",
            "instances": [
                {"id": "box_1", "part": "box", "position": [20, 0, 0]},
                {"id": "pin_1", "part": "pin", "position": [100, 0, 0]},
            ],
        })
        _on(service, "agent_b", "master")         # move pin_1 next to nothing
        assert "error" not in registry.call("set_assembly", {
            "project": "demo",
            "instances": [
                {"id": "box_1", "part": "box", "position": [0, 0, 0]},
                {"id": "pin_1", "part": "pin", "position": [25, 0, 0]},
            ],
        })

        blocked = registry.call("merge_branch", {"project": "demo", "source": "feat"})
        assert blocked["error"]["type"] == "validation_error"
        report = blocked["error"]["details"]["validation"]["interference"]
        assert report["skipped"] is None
        pairs = {frozenset((p["a"], p["b"])) for p in report["new_pairs"]}
        assert pairs == {frozenset(("box_1", "pin_1"))}

        landed = registry.call("merge_branch", {
            "project": "demo", "source": "feat", "allow_invalid": True})
        assert "error" not in landed, landed
        landed_pairs = landed["validation"]["interference"]["new_pairs"]
        assert {frozenset((p["a"], p["b"])) for p in landed_pairs} == pairs
        message = _git(canonical / ".history", canonical,
                       "log", "-1", "--pretty=%B", "master")
        assert "box_1" in message and "pin_1" in message

    def test_a_pre_existing_overlap_does_not_block(self, demo):
        service, registry = demo
        _on(service, "agent_b", "master")
        assert "error" not in registry.call("set_assembly", {
            "project": "demo",
            "instances": [
                {"id": "box_1", "part": "box", "position": [0, 0, 0]},
                {"id": "pin_1", "part": "pin", "position": [2, 0, 0]},
            ],
        })
        service.branches.delete("demo", "feat")
        service.branches.create("demo", "feat")

        _on(service, "agent_a", "feat")
        _script(registry, "pin", BOX_V2_SCRIPT.replace("* 2", "* 1.0"))
        _on(service, "agent_b", "master")
        _script(registry, "box", BOX_V3_SCRIPT.replace("* 3", "* 1.0"))
        result = registry.call("merge_branch", {"project": "demo", "source": "feat"})
        assert "error" not in result, result
        report = result["validation"]["interference"]
        assert report["new_pairs"] == []
        assert result["validation"]["ok"] is True

    def test_instance_cap_skips_the_interference_check(self, demo, monkeypatch):
        from agentcad.core import merge as merge_mod

        service, registry = demo
        monkeypatch.setattr(merge_mod, "MERGE_INTERFERENCE_MAX_INSTANCES", 2)
        _on(service, "agent_b", "master")
        assert "error" not in registry.call("set_assembly", {
            "project": "demo",
            "instances": [
                {"id": f"box_{i}", "part": "box", "position": [i * 20, 0, 0]}
                for i in range(3)
            ],
        })
        service.branches.delete("demo", "feat")
        service.branches.create("demo", "feat")

        _on(service, "agent_a", "feat")
        _script(registry, "pin", BOX_V2_SCRIPT)
        _on(service, "agent_b", "master")
        _script(registry, "box", BOX_V3_SCRIPT)
        result = registry.call("merge_branch", {"project": "demo", "source": "feat"})
        assert "error" not in result, result
        assert result["validation"]["interference"]["skipped"] == "instances"
        assert result["validation"]["interference"]["new_pairs"] == []

    def test_validation_reuses_the_shared_mesh_cache(self, demo, monkeypatch):
        service, registry = demo
        _on(service, "agent_a", "feat")
        _script(registry, "box", BOX_V2_SCRIPT)
        assert service.get_metrics("demo", "box")["volume_mm3"] > 0
        _on(service, "agent_b", "master")
        _script(registry, "pin", BOX_V3_SCRIPT)
        assert service.get_metrics("demo", "pin")["volume_mm3"] > 0

        calls = {"build": 0}
        original = service.kernel.request

        def counting(method, params, timeout_s=None, affinity=None):
            if method == "build":
                calls["build"] += 1
            return original(method, params, timeout_s=timeout_s, affinity=affinity)

        monkeypatch.setattr(service.kernel, "request", counting)
        service._status.clear()

        result = registry.call("merge_branch", {"project": "demo", "source": "feat"})
        assert "error" not in result, result
        assert calls["build"] == 0
        built = result["validation"]["built"]
        assert [b["part"] for b in built] == ["box"]   # pin already on the target
        assert all(b["cached"] for b in built)

    def test_mates_are_re_resolved_and_dangling_mates_block(self, stack):
        service, registry = stack
        assert "error" not in registry.call("create_project", {"name": "demo"})
        for part, script in (("plate", PLATE_SCRIPT), ("pin", PIN_SCRIPT)):
            assert "error" not in registry.call(
                "create_part",
                {"project": "demo", "part_id": part, "script": script})
        assert "error" not in registry.call("set_assembly", {
            "project": "demo",
            "instances": [
                {"id": "plate_1", "part": "plate", "position": [0, 0, 0]},
                {"id": "pin_1", "part": "pin", "position": [0, 0, 0]},
            ],
        })
        assert "error" not in registry.call("set_mate", {
            "project": "demo", "instance": "pin_1", "connector": "base",
            "to_instance": "plate_1", "to_connector": "top",
        })
        service.branches.create("demo", "feat")

        # A clean merge whose validation pass must re-resolve the mate.
        _on(service, "agent_a", "feat")
        assert "error" not in registry.call(
            "set_params", {"project": "demo", "part_id": "plate",
                           "values": {"t": 20.0}})
        _on(service, "agent_b", "master")
        assert "error" not in registry.call(
            "set_params", {"project": "demo", "part_id": "pin",
                           "values": {"h": 25.0}})
        result = registry.call("merge_branch", {"project": "demo", "source": "feat"})
        assert "error" not in result, result
        assert result["validation"]["ok"] is True
        assert result["validation"]["integrity"] == []

        # ...and a merge that strands the mate's target is blocked.
        mate = {"connector": "base", "to_instance": "plate_1", "to_connector": "top"}
        service.branches.create("demo", "feat2")
        _on(service, "agent_a", "feat2")
        assert "error" not in registry.call("set_assembly", {
            "project": "demo",
            "instances": [
                {"id": "plate_1", "part": "plate", "position": [0, 0, 0]},
                {"id": "pin_1", "part": "pin", "position": [0, 0, 0], "mate": mate},
                {"id": "pin_2", "part": "pin", "position": [0, 0, 0], "mate": mate},
            ],
        })
        _on(service, "agent_b", "master")
        assert "error" not in registry.call("set_assembly", {
            "project": "demo",
            "instances": [{"id": "pin_1", "part": "pin", "position": [0, 0, 0]}],
        })
        blocked = registry.call("merge_branch", {"project": "demo", "source": "feat2"})
        assert blocked["error"]["type"] == "validation_error"
        kinds = [e["kind"]
                 for e in blocked["error"]["details"]["validation"]["integrity"]]
        assert "dangling_mate" in kinds


# ------------------------- 4. second-review regressions (Codex, X1–X13)


class TestUnambiguousRefs:
    """X1 — ``git rev-parse <name>`` searches refs/tags BEFORE refs/heads, so a
    tag can shadow a branch of the same name. Every operation that MEANS a
    branch must say ``refs/heads/<name>``."""

    pytestmark = _GIT

    def test_x1_a_tag_shadowing_a_branch_does_not_hijack_the_merge(self, demo):
        service, registry = demo
        canonical = service.store.canonical_path_of("demo")
        _on(service, "agent_a", "feat")
        _script(registry, "box", BOX_V2_SCRIPT)
        _on(service, "agent_b", "master")
        _script(registry, "pin", BOX_V3_SCRIPT)
        target_head = service.history.resolve_ref(canonical, "master")
        source_head = service.history.resolve_ref(canonical, "feat")

        # A tag called exactly like the source branch, at the TARGET's head:
        # resolving it would make the merge a no-op ("already up to date").
        _git(canonical / ".history", canonical, "tag", "feat", target_head)
        assert service.history.resolve_ref(canonical, "feat") == target_head
        assert service.history.resolve_branch(canonical, "feat") == source_head

        result = registry.call("merge_branch", {"project": "demo", "source": "feat"})

        assert "error" not in result, result
        assert result.get("already_up_to_date") is not True
        assert _parents(canonical, "master") == [target_head, source_head]
        assert (canonical / "parts" / "box.py").read_text() == BOX_V2_SCRIPT


class TestFastForwardValidation:
    """X2 — FR9 has no fast-forward exemption. A branch can carry a script that
    does not build (the edit persists; only the rebuild reports the failure),
    and a fast-forward used to land it with ``validation: null``."""

    pytestmark = _GIT

    def test_x2_a_broken_script_blocks_the_fast_forward(self, demo):
        service, registry = demo
        canonical = service.store.canonical_path_of("demo")
        _on(service, "agent_a", "feat")
        _script(registry, "box", BROKEN_SCRIPT, builds=False)
        _on(service, "agent_b", "master")
        head_before = service.history.resolve_branch(canonical, "master")

        blocked = registry.call("merge_branch", {"project": "demo", "source": "feat"})

        assert blocked["error"]["type"] == "validation_error"
        validation = blocked["error"]["details"]["validation"]
        assert validation["ok"] is False and validation["blocked"] is True
        assert [f["part"] for f in validation["failures"]] == ["box"]
        assert service.history.resolve_branch(canonical, "master") == head_before
        assert (canonical / "parts" / "box.py").read_text() == BOX_SCRIPT

    def test_x2_allow_invalid_lands_the_fast_forward_with_the_report(self, demo):
        service, registry = demo
        canonical = service.store.canonical_path_of("demo")
        _on(service, "agent_a", "feat")
        _script(registry, "box", BROKEN_SCRIPT, builds=False)
        source_head = service.history.resolve_branch(canonical, "feat")
        _on(service, "agent_b", "master")

        landed = registry.call("merge_branch", {
            "project": "demo", "source": "feat", "allow_invalid": True})

        assert "error" not in landed, landed
        assert landed["fast_forward"] is True
        assert landed["validation"] is not None      # no longer null on FF
        assert landed["validation"]["ok"] is False
        assert landed["validation"]["blocked"] is False
        assert service.history.resolve_branch(canonical, "master") == source_head
        assert (canonical / "parts" / "box.py").read_text() == BROKEN_SCRIPT

    def test_x2_a_clean_fast_forward_reports_what_it_revalidated(self, demo):
        service, registry = demo
        _on(service, "agent_a", "feat")
        _script(registry, "box", BOX_V2_SCRIPT)
        _on(service, "agent_b", "master")

        result = registry.call("merge_branch", {"project": "demo", "source": "feat"})

        assert "error" not in result, result
        assert result["fast_forward"] is True
        assert result["validation"]["ok"] is True
        assert [b["part"] for b in result["validation"]["built"]] == ["box"]


class TestValidationWarnings:
    """X3 — the interference cap stays (accepted design), but a skipped check
    must be loud: it rides the report as a warning, into merge_completed and
    the UI."""

    pytestmark = _GIT

    def test_x3_a_skipped_interference_check_is_a_warning(self, demo, monkeypatch):
        from agentcad.core import merge as merge_mod

        service, registry = demo
        monkeypatch.setattr(merge_mod, "MERGE_INTERFERENCE_MAX_INSTANCES", 2)
        _on(service, "agent_b", "master")
        assert "error" not in registry.call("set_assembly", {
            "project": "demo",
            "instances": [
                {"id": f"box_{i}", "part": "box", "position": [i * 20, 0, 0]}
                for i in range(3)
            ],
        })
        service.branches.delete("demo", "feat")
        service.branches.create("demo", "feat")
        _on(service, "agent_a", "feat")
        _script(registry, "pin", BOX_V2_SCRIPT)
        _on(service, "agent_b", "master")
        _script(registry, "box", BOX_V3_SCRIPT)

        queue = service.bus.subscribe()
        try:
            result = registry.call("merge_branch", {"project": "demo",
                                                    "source": "feat"})
            events = []
            while not queue.empty():
                events.append(queue.get_nowait())
        finally:
            service.bus.unsubscribe(queue)

        assert "error" not in result, result
        report = result["validation"]
        assert report["interference"]["skipped"] == "instances"
        assert report["ok"] is True
        assert any("interference" in w and "3" in w for w in report["warnings"])
        completed = [e for e in events if e["type"] == "merge_completed"]
        assert completed[-1]["validation"]["warnings"] == report["warnings"]

    def test_x3_a_normal_report_carries_an_empty_warnings_list(self, demo):
        service, registry = demo
        _on(service, "agent_a", "feat")
        _script(registry, "box", BOX_V2_SCRIPT)
        _on(service, "agent_b", "master")
        _script(registry, "pin", BOX_V3_SCRIPT)

        result = registry.call("merge_branch", {"project": "demo", "source": "feat"})

        assert "error" not in result, result
        assert result["validation"]["warnings"] == []


class TestFinalizeAtomicity:
    """X6 + X9 — the turn/cleanliness checks at the top of a merge are worth
    nothing unless they are HELD through finalization, and the ref must never
    move without its tree."""

    pytestmark = _GIT

    def test_x6_a_competing_write_during_finalize_is_locked_out(self, demo,
                                                                monkeypatch):
        service, registry = demo
        canonical = service.store.canonical_path_of("demo")
        _on(service, "agent_a", "feat")
        _script(registry, "box", BOX_V2_SCRIPT)
        _on(service, "agent_b", "master")
        _script(registry, "pin", BOX_V3_SCRIPT)

        seen = {}
        original = service.merges._validate

        def racing(*args, **kwargs):
            report = original(*args, **kwargs)
            previous = locks.current_client_id()
            locks.set_client_id("agent_z")   # another client, same branch
            try:
                seen["write"] = registry.call(
                    "set_params",
                    {"project": "demo", "part_id": "pin", "values": {"size": 31.0}})
            finally:
                locks.set_client_id(previous)
            return report

        monkeypatch.setattr(service.merges, "_validate", racing)
        result = registry.call("merge_branch", {"project": "demo", "source": "feat"})

        assert seen["write"]["error"]["type"] == "conflict_error"
        assert "error" not in result, result
        manifest = json.loads((canonical / "project.json").read_text())
        pin = [p for p in manifest["parts"] if p["id"] == "pin"][0]
        assert pin.get("params", {}).get("size") != 31.0

    def test_x6_a_target_dirtied_during_validation_fails_instead_of_clobbering(
            self, demo, monkeypatch):
        """A write that dodges the turn lock (raw file, other process) must
        stop the merge, not be destroyed by ``reset --hard``."""
        service, registry = demo
        canonical = service.store.canonical_path_of("demo")
        _on(service, "agent_a", "feat")
        _script(registry, "box", BOX_V2_SCRIPT)
        _on(service, "agent_b", "master")
        _script(registry, "pin", BOX_V3_SCRIPT)
        head_before = service.history.resolve_branch(canonical, "master")
        original = service.merges._validate

        def dirtying(*args, **kwargs):
            report = original(*args, **kwargs)
            (canonical / "parts" / "pin.py").write_text(
                BOX_V2_SCRIPT + "# raw\n", encoding="utf-8")
            return report

        monkeypatch.setattr(service.merges, "_validate", dirtying)
        result = registry.call("merge_branch", {"project": "demo", "source": "feat"})

        assert result["error"]["type"] == "conflict_error"
        assert service.history.resolve_branch(canonical, "master") == head_before
        assert (canonical / "parts" / "pin.py").read_text().endswith("# raw\n")

    def test_x9_a_failed_tree_sync_rolls_the_ref_back(self, demo, monkeypatch):
        service, registry = demo
        canonical = service.store.canonical_path_of("demo")
        _on(service, "agent_a", "feat")
        _script(registry, "box", BOX_V2_SCRIPT)
        _on(service, "agent_b", "master")
        _script(registry, "box", BOX_V3_SCRIPT)
        assert registry.call(
            "merge_branch", {"project": "demo", "source": "feat"}
        )["error"]["type"] == "merge_conflict"
        head_before = service.history.resolve_branch(canonical, "master")

        from agentcad.core.model import ValidationError

        def boom(tree, commit):
            raise ValidationError("index.lock exists")

        monkeypatch.setattr(service.merges, "_sync_tree", boom)
        failed = registry.call("resolve_merge", {
            "project": "demo", "choices": {"parts/box.py": {"take": "theirs"}}})

        assert failed["error"]["type"] == "validation_error"
        # the ref is back where it was, and the tree still matches it
        assert service.history.resolve_branch(canonical, "master") == head_before
        assert (canonical / "parts" / "box.py").read_text() == BOX_V3_SCRIPT
        # ...and the staged merge survives, resolutions included
        staged = registry.call("merge_status", {"project": "demo"})["merge"]
        assert staged and staged["resolved"] == ["parts/box.py"]


class TestMalformedManifests:
    """X11 — a project.json that exists but does not parse used to read as
    ``{}``: 'this side deleted everything'. That merges clean, validates, and
    explodes on the next get_project."""

    pytestmark = _GIT

    def _corrupt_feat(self, service, text: str) -> Path:
        tree = service.branches.tree_of("demo", "feat")
        (tree / "project.json").write_text(text, encoding="utf-8")
        assert service.history.snapshot(tree, "hand-edited manifest")
        return tree

    def test_x11_a_corrupt_manifest_on_a_side_refuses_the_merge(self, demo):
        service, registry = demo
        canonical = service.store.canonical_path_of("demo")
        _on(service, "agent_a", "feat")
        _script(registry, "box", BOX_V2_SCRIPT)
        self._corrupt_feat(service, "{not json")
        _on(service, "agent_b", "master")
        _script(registry, "pin", BOX_V3_SCRIPT)
        head_before = service.history.resolve_branch(canonical, "master")
        manifest_before = (canonical / "project.json").read_bytes()

        result = registry.call("merge_branch", {"project": "demo", "source": "feat"})

        assert result["error"]["type"] == "validation_error"
        assert "project.json" in result["error"]["message"]
        assert "feat" in result["error"]["message"]
        assert result["error"]["details"]["file"] == "project.json"
        assert service.history.resolve_branch(canonical, "master") == head_before
        assert (canonical / "project.json").read_bytes() == manifest_before
        assert registry.call("merge_status", {"project": "demo"})["merge"] is None

    def test_x11_a_merged_manifest_missing_required_fields_is_blocked(self, demo):
        service, registry = demo
        _on(service, "agent_a", "feat")
        tree = service.branches.tree_of("demo", "feat")
        doc = json.loads((tree / "project.json").read_text())
        doc.pop("name")
        self._corrupt_feat(service, json.dumps(doc, indent=2))
        _on(service, "agent_b", "master")
        _script(registry, "pin", BOX_V3_SCRIPT)

        blocked = registry.call("merge_branch", {"project": "demo", "source": "feat"})

        assert blocked["error"]["type"] == "validation_error"
        integrity = blocked["error"]["details"]["validation"]["integrity"]
        assert [e["kind"] for e in integrity] == ["manifest_invalid"]

    def test_x11_a_commit_without_a_manifest_still_reads_as_empty(self, demo):
        """The legitimate orphan case: no project.json at all is {} as before,
        which is what makes an unrelated/empty base work."""
        service, _registry = demo
        canonical = service.store.canonical_path_of("demo")
        empty_tree = _git(canonical / ".history", canonical, "mktree", stdin="")
        commit = _git(canonical / ".history", canonical, "commit-tree",
                      empty_tree, "-m", "orphan")

        assert service.merges._manifest_at(canonical, commit) == {}
