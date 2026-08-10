"""Branch-aware store, worktree-aware history, refs and tags (PRD-001, slice 2).

Covers the substrate under branching: the ProjectStore resolver seam, a
ProjectHistory that works inside a linked git worktree, per-client branch
checkouts with per-branch turn locks and undo stacks, tags, and the
byte-determinism guarantee that a mesh built on one branch is reused on
another (the cache stays canonical while authored state follows the branch).

Merge orchestration, the tool/route packs and the UI are NOT in this slice.

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
from agentcad.core.branches import BranchManager, _inside, pinned_tree_var
from agentcad.core.history import ProjectHistory
from agentcad.core.model import ConflictError, PartRecord
from agentcad.core.project import ProjectStore
from agentcad.core.service import AgentCADService, EventBus
from agentcad.core.tools import build_registry
from agentcad.core.tools_versioning import install_write_guard

from .conftest import BOX_SCRIPT

_GIT = [
    pytest.mark.integration,
    pytest.mark.portability,
    pytest.mark.skipif(shutil.which("git") is None, reason="git not found on PATH"),
]

BOX_V2_SCRIPT = BOX_SCRIPT.replace(
    "Box(p.size, p.size, p.size)", "Box(p.size, p.size, p.size * 2)"
)
assert BOX_V2_SCRIPT != BOX_SCRIPT


@pytest.fixture(autouse=True)
def _reset_context():
    """Identity and pin are ContextVars: pin them per test so one test's
    set_client_id / pinned() can never leak into the next."""
    cid = locks.client_id_var.set("local")
    pin = pinned_tree_var.set(None)
    yield
    locks.client_id_var.reset(cid)
    pinned_tree_var.reset(pin)


@pytest.fixture
def registry_error():
    """Call a manager method and return the tool-layer error type its raised
    AppError maps to (same derivation as ToolRegistry.call)."""
    from agentcad.core.model import AppError

    def call(fn, *args, **kwargs) -> str:
        try:
            fn(*args, **kwargs)
        except AppError as exc:
            return type(exc).__name__.replace("Error", "").lower() + "_error"
        raise AssertionError(f"{getattr(fn, '__name__', fn)} did not raise")

    return call


def _manifest(name: str, parts: list[dict]) -> dict:
    return {
        "schema_version": 2,
        "name": name,
        "units": "mm",
        "parts": parts,
        "assembly": {"instances": []},
    }


def _state(registry, part_id: str) -> str:
    """A part's badge as get_project reports it to the caller's branch."""
    project = registry.call("get_project", {"project": "demo"})
    return [p for p in project["parts"] if p["id"] == part_id][0]["state"]


def _write_project(path: Path, name: str, parts: list[dict], script: str) -> Path:
    (path / "parts").mkdir(parents=True, exist_ok=True)
    (path / "project.json").write_text(
        json.dumps(_manifest(name, parts), indent=2), encoding="utf-8"
    )
    for entry in parts:
        (path / "parts" / f"{entry['id']}.py").write_text(script, encoding="utf-8")
    return path


# ------------------------------------------- 1. ProjectStore resolver seam


class TestStoreResolverSeam:
    """Kernel-free, git-free: path resolution only."""

    def test_without_a_resolver_the_store_behaves_exactly_as_before(self, tmp_path):
        root = tmp_path / "projects"
        _write_project(root / "demo", "demo", [{"id": "box", "label": "Box",
                                                "material": "steel",
                                                "params": {}}], "canonical\n")
        store = ProjectStore(root)

        assert store.branch_resolver is None
        assert store.path_of("demo") == root / "demo"
        assert store.canonical_path_of("demo") == root / "demo"
        assert store.lock_key("demo") == "demo"
        assert store.cache_dir("demo") == root / "demo" / ".cache"

    def test_resolver_moves_authored_state_but_not_the_cache(self, tmp_path):
        root = tmp_path / "projects"
        canonical = _write_project(
            root / "demo", "demo",
            [{"id": "box", "label": "Box", "material": "steel", "params": {}}],
            "canonical\n",
        )
        tree = _write_project(
            tmp_path / "tree", "demo",
            [{"id": "box", "label": "Box", "material": "steel", "params": {}},
             {"id": "pin", "label": "Pin", "material": "steel", "params": {}}],
            "branch\n",
        )
        store = ProjectStore(root)
        store.branch_resolver = lambda proj, path: tree

        assert store.path_of("demo") == tree
        assert store.canonical_path_of("demo") == canonical
        assert store.lock_key("demo") == str(tree)
        # Authored state follows the resolver...
        assert store.read_script("demo", "box") == "branch\n"
        assert store.script_path("demo", "box") == tree / "parts" / "box.py"
        assert store.part_ids("demo") == ["box", "pin"]
        assert store.exports_dir("demo") == tree / "exports"
        assert store.imports_dir("demo") == tree / "imports"
        # ...the mesh cache does not (byte-determinism across branches, FR13).
        assert store.cache_dir("demo") == canonical / ".cache"

        store.write_script("demo", "box", "edited\n")
        assert (tree / "parts" / "box.py").read_text() == "edited\n"
        assert (canonical / "parts" / "box.py").read_text() == "canonical\n"

        store.save_manifest("demo", _manifest("demo", []))
        assert json.loads((tree / "project.json").read_text())["parts"] == []
        assert json.loads((canonical / "project.json").read_text())["parts"]

    def test_list_projects_reports_the_resolved_tree(self, tmp_path):
        root = tmp_path / "projects"
        _write_project(root / "demo", "demo",
                       [{"id": "box", "label": "Box", "material": "steel",
                         "params": {}}], "canonical\n")
        tree = _write_project(
            tmp_path / "tree", "demo",
            [{"id": "box", "label": "Box", "material": "steel", "params": {}},
             {"id": "pin", "label": "Pin", "material": "steel", "params": {}}],
            "branch\n",
        )
        store = ProjectStore(root)
        assert store.list_projects() == [
            {"name": "demo", "path": str(root / "demo"), "n_parts": 1}
        ]
        store.branch_resolver = lambda proj, path: tree
        assert store.list_projects() == [
            {"name": "demo", "path": str(tree), "n_parts": 2}
        ]


# --------------------------------------- 2. history inside a linked worktree


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


class TestWorktreeAwareHistory:
    pytestmark = _GIT

    @pytest.fixture
    def repo(self, tmp_path):
        """A project with history plus a linked worktree for branch 'feat'."""
        project = _write_project(
            tmp_path / "demo", "demo",
            [{"id": "box", "label": "Box", "material": "steel", "params": {}}],
            "v1\n",
        )
        history = ProjectHistory()
        first = history.snapshot(project, "init")
        assert first
        git_dir = project / ".history"
        _git(git_dir, project, "branch", "feat")
        _git(git_dir, project, "worktree", "add",
             str(git_dir / "trees" / "feat"), "feat")
        return history, project, git_dir / "trees" / "feat", first

    def test_snapshot_in_a_linked_tree_commits_on_that_branch(self, repo):
        history, project, tree, first = repo

        assert history._has_repo(tree) is True
        (tree / "parts" / "box.py").write_text("v2-on-feat\n", encoding="utf-8")
        commit = history.snapshot(tree, "edit on feat")
        assert commit and commit != first

        assert history.head(tree) == commit
        assert history.head(project) == first          # main tree untouched
        assert history.log(tree)[0]["message"] == "edit on feat"
        assert history.log(project)[0]["message"] == "init"
        assert (project / "parts" / "box.py").read_text() == "v1\n"
        assert _git(project / ".history", project,
                    "rev-parse", "refs/heads/feat") == commit

    def test_restore_operates_on_the_linked_tree(self, repo):
        history, project, tree, first = repo
        (tree / "parts" / "box.py").write_text("v2-on-feat\n", encoding="utf-8")
        history.snapshot(tree, "edit on feat")

        history.restore(tree, first)
        assert (tree / "parts" / "box.py").read_text() == "v1\n"
        assert history.log(tree)[0]["message"] == f"restore {first[:8]}"

    def test_ref_primitives(self, repo):
        history, project, tree, first = repo
        (tree / "parts" / "box.py").write_text("v2-on-feat\n", encoding="utf-8")
        feat_head = history.snapshot(tree, "edit on feat")
        _git(project / ".history", project, "tag", "-a", "v1", "-m", "shipped")

        assert history.resolve_ref(project, "feat") == feat_head
        assert history.resolve_ref(project, "v1") == first
        assert history.resolve_ref(project, first) == first
        assert history.resolve_ref(project, "master") == first
        assert history.resolve_ref(project, "nope") is None
        for bad in ("--help", "..", "a..b", "a/", "x.lock", "HEAD@{1}", "", "-x"):
            assert history.resolve_ref(project, bad) is None, bad

        names = {b["name"] for b in history.branches(project)}
        assert names == {"master", "feat"}
        feat = [b for b in history.branches(project) if b["name"] == "feat"][0]
        assert feat["head"] == feat_head and feat["ts"] and feat["message"]

        tags = history.tags(project)
        assert [t["name"] for t in tags] == ["v1"]
        assert tags[0]["commit"] == first
        assert tags[0]["message"] == "shipped"
        assert tags[0]["author"]

        # log(ref=...) reads another branch without touching the work tree.
        assert history.log(project, ref="feat")[0]["message"] == "edit on feat"
        assert history.log(project, ref="nope") == []

    def test_excludes_are_refreshed_on_an_existing_repo(self, repo):
        history, project, _tree, _first = repo
        exclude = project / ".history" / "info" / "exclude"
        exclude.write_text("stale\n", encoding="utf-8")
        history.snapshot(project, "noop")
        assert ".history/" in exclude.read_text(encoding="utf-8")


# --------------------------------------------------- 3. the BranchManager


@pytest.fixture
def stack(kernel, tmp_path):
    """Real service (real snapshot hook) + registry + BranchManager, wired the
    way the slice-3 tool pack will wire them."""
    bus = EventBus()
    service = AgentCADService(tmp_path / "projects", kernel, bus)
    registry = build_registry(service)
    service.branches = BranchManager(service)
    install_write_guard(service)  # exactly what the slice-3 tool pack installs
    return service, registry, service.branches


@pytest.fixture
def demo(stack):
    service, registry, branches = stack
    assert "error" not in registry.call("create_project", {"name": "demo"})
    created = registry.call(
        "create_part", {"project": "demo", "part_id": "box", "script": BOX_SCRIPT}
    )
    assert "error" not in created, created
    return service, registry, branches


class TestBranchLifecycle:
    pytestmark = _GIT

    def test_create_makes_a_ref_and_a_worktree(self, demo):
        service, _registry, branches = demo
        payload = branches.create("demo", "feat")

        assert payload["created"] == "feat"
        assert {b["name"] for b in payload["branches"]} == {"master", "feat"}
        canonical = service.store.canonical_path_of("demo")
        tree = canonical / ".history" / "trees" / "feat"
        assert (tree / "project.json").is_file()
        assert (tree / "parts" / "box.py").is_file()
        assert (tree / ".git").is_file()          # linked worktree marker
        # git prints forward-slash paths on every OS; compare in posix form.
        listing = _git(canonical / ".history", canonical, "worktree", "list")
        assert canonical.as_posix() in listing and tree.as_posix() in listing
        # create does not switch the caller
        assert branches.current("demo") == "master"

    @pytest.mark.parametrize(
        "name",
        ["Feat", "-x", "a..b", "a/", "x.lock", "", "feat x", "a" * 65,
         ".hidden", "HEAD@{1}"],
    )
    def test_invalid_branch_names_are_rejected(self, demo, registry_error, name):
        _service, _registry, branches = demo
        assert registry_error(branches.create, "demo", name) == "validation_error"

    def test_valid_nested_branch_name(self, demo):
        _service, _registry, branches = demo
        payload = branches.create("demo", "feat/x-1")
        assert "feat/x-1" in {b["name"] for b in payload["branches"]}

    def test_duplicate_branch_is_a_conflict(self, demo, registry_error):
        _service, _registry, branches = demo
        branches.create("demo", "feat")
        assert registry_error(branches.create, "demo", "feat") == "conflict_error"

    def test_switch_is_per_client(self, demo):
        service, registry, branches = demo
        branches.create("demo", "feat")

        locks.set_client_id("agent_a")
        assert branches.switch("demo", "feat") == "feat"
        assert "error" not in registry.call(
            "update_part_script",
            {"project": "demo", "part_id": "box", "script": BOX_V2_SCRIPT},
        )
        assert service.store.read_script("demo", "box") == BOX_V2_SCRIPT

        locks.set_client_id("agent_b")
        assert branches.current("demo") == "master"
        assert service.store.read_script("demo", "box") == BOX_SCRIPT
        canonical = service.store.canonical_path_of("demo")
        assert (canonical / "parts" / "box.py").read_text() == BOX_SCRIPT

        listing = branches.list("demo")
        assert listing["you"] == "agent_b"
        assert listing["current"] == "master"
        assert listing["default"] == "master"
        feat = [b for b in listing["branches"] if b["name"] == "feat"][0]
        assert feat["checked_out_by"] == ["agent_a"]
        assert feat["is_default"] is False and feat["is_current"] is False

    def test_snapshot_hook_commits_to_the_mutating_clients_branch(self, demo):
        _service, registry, branches = demo
        branches.create("demo", "feat")

        locks.set_client_id("agent_a")
        branches.switch("demo", "feat")
        registry.call(
            "update_part_script",
            {"project": "demo", "part_id": "box", "script": BOX_V2_SCRIPT},
        )
        on_feat = registry.call("project_history", {"project": "demo"})["history"]

        locks.set_client_id("agent_b")
        on_master = registry.call("project_history", {"project": "demo"})["history"]

        assert len(on_feat) == len(on_master) + 1
        assert on_feat[1]["id"] == on_master[0]["id"]

    def test_history_ref_reads_another_branch_without_switching(self, demo):
        _service, registry, branches = demo
        branches.create("demo", "feat")
        locks.set_client_id("agent_a")
        branches.switch("demo", "feat")
        registry.call(
            "update_part_script",
            {"project": "demo", "part_id": "box", "script": BOX_V2_SCRIPT},
        )

        locks.set_client_id("agent_b")
        mine = registry.call("project_history", {"project": "demo"})
        other = registry.call("project_history", {"project": "demo", "ref": "feat"})
        assert "error" not in other
        assert len(other["history"]) == len(mine["history"]) + 1
        assert branches.current("demo") == "master"
        unknown = registry.call("project_history", {"project": "demo", "ref": "nope"})
        assert unknown["error"]["type"] == "notfound_error"
        bad = registry.call("project_history", {"project": "demo", "ref": "--help"})
        assert bad["error"]["type"] == "validation_error"

    def test_delete_rejects_default_and_checked_out_branches(self, demo,
                                                             registry_error):
        service, _registry, branches = demo
        branches.create("demo", "feat")
        assert registry_error(branches.delete, "demo", "master") == "validation_error"

        locks.set_client_id("agent_a")
        branches.switch("demo", "feat")
        assert registry_error(branches.delete, "demo", "feat") == "validation_error"

        locks.set_client_id("agent_b")
        # still checked out by agent_a
        assert registry_error(branches.delete, "demo", "feat") == "validation_error"

        locks.set_client_id("agent_a")
        branches.switch("demo", "master")
        payload = branches.delete("demo", "feat")
        assert payload["deleted"] == "feat"
        assert {b["name"] for b in payload["branches"]} == {"master"}
        canonical = service.store.canonical_path_of("demo")
        assert not (canonical / ".history" / "trees" / "feat").exists()
        assert "feat" not in _git(canonical / ".history", canonical,
                                  "branch", "--list")

    def test_tree_of_and_pinned_override_resolution(self, demo):
        """The seam slice 3's merge validation pass runs inside."""
        service, _registry, branches = demo
        canonical = service.store.canonical_path_of("demo")
        branches.create("demo", "feat")
        tree = branches.tree_of("demo", "feat")
        assert tree == canonical / ".history" / "trees" / "feat"
        assert branches.tree_of("demo", "master") == canonical

        with branches.pinned("demo", tree):
            assert service.store.path_of("demo") == tree
            assert service.store.lock_key("demo") == str(tree)
            assert service.store.read_script("demo", "box") == BOX_SCRIPT
            assert service.store.cache_dir("demo") == canonical / ".cache"
        assert service.store.path_of("demo") == canonical

    def test_default_branch_is_discovered_and_persisted(self, demo):
        service, _registry, branches = demo
        assert branches.default_branch("demo") == "master"
        config = (service.store.canonical_path_of("demo") / ".history"
                  / "agentcad" / "config.json")
        assert json.loads(config.read_text())["default_branch"] == "master"

    def test_checkouts_round_trip_a_restart(self, stack, demo):
        service, _registry, branches = demo
        branches.create("demo", "feat")
        branches.create("demo", "spare")
        locks.set_client_id("agent_a")
        branches.switch("demo", "feat")
        locks.set_client_id("agent_c")
        branches.switch("demo", "spare")

        # A fresh manager over the same project = a server restart.
        fresh = BranchManager(service)
        locks.set_client_id("agent_a")
        assert fresh.current("demo") == "feat"
        assert fresh.resolve_path(
            "demo", service.store.canonical_path_of("demo")
        ).name == "feat"

        # An entry pointing at a branch that no longer exists is dropped.
        locks.set_client_id("agent_c")
        fresh.switch("demo", "master")
        fresh.delete("demo", "spare")
        checkouts = json.loads(
            (service.store.canonical_path_of("demo") / ".history" / "agentcad"
             / "checkouts.json").read_text()
        )
        checkouts["clients"]["ghost"] = "spare"
        (service.store.canonical_path_of("demo") / ".history" / "agentcad"
         / "checkouts.json").write_text(json.dumps(checkouts), encoding="utf-8")
        reloaded = BranchManager(service)
        locks.set_client_id("ghost")
        assert reloaded.current("demo") == "master"

    def test_existing_linear_history_becomes_the_default_branch(self, kernel,
                                                                tmp_path):
        """No-op migration: a project built before branching keeps its files,
        its history and its HEAD; nothing new appears on disk until a branch
        is created."""
        bus = EventBus()
        service = AgentCADService(tmp_path / "projects", kernel, bus)
        registry = build_registry(service)
        registry.call("create_project", {"name": "demo"})
        registry.call("create_part", {"project": "demo", "part_id": "box",
                                      "script": BOX_SCRIPT})
        registry.call("update_part_script", {"project": "demo", "part_id": "box",
                                             "script": BOX_V2_SCRIPT})
        canonical = service.store.path_of("demo")
        before = registry.call("project_history", {"project": "demo"})["history"]
        head_before = service.history.head(canonical)

        branches = BranchManager(service)  # first branching call for the project
        assert branches.default_branch("demo") == "master"
        listing = branches.list("demo")
        assert [b["name"] for b in listing["branches"]] == ["master"]
        assert listing["branches"][0]["head"] == head_before
        assert listing["current"] == "master" and listing["default"] == "master"
        assert service.store.path_of("demo") == canonical
        assert registry.call("project_history",
                             {"project": "demo"})["history"] == before
        assert not (canonical / ".history" / "trees").exists()

    def test_a_deleted_worktree_is_recreated_on_switch(self, demo):
        """git leaves a deleted linked tree registered and 'prunable'; the
        manager must prune before re-adding (verified empirically)."""
        service, _registry, branches = demo
        branches.create("demo", "feat")
        canonical = service.store.canonical_path_of("demo")
        tree = canonical / ".history" / "trees" / "feat"
        shutil.rmtree(tree)
        assert "prunable" in _git(canonical / ".history", canonical,
                                  "worktree", "list")

        locks.set_client_id("agent_a")
        branches.switch("demo", "feat")
        assert (tree / "project.json").is_file()
        assert service.store.read_script("demo", "box") == BOX_SCRIPT
        assert "prunable" not in _git(canonical / ".history", canonical,
                                      "worktree", "list")

    def test_a_tree_that_lost_its_git_link_is_rematerialized(self, demo):
        """A directory that merely contains project.json is not a checkout:
        adopting one without its .git link makes the next snapshot 'git init'
        an invisible throwaway repo inside it."""
        service, registry, branches = demo
        branches.create("demo", "feat")
        canonical = service.store.canonical_path_of("demo")
        tree = canonical / ".history" / "trees" / "feat"
        (tree / ".git").unlink()

        locks.set_client_id("agent_a")
        branches.switch("demo", "feat")

        assert (tree / ".git").is_file()
        assert "gitdir:" in (tree / ".git").read_text(encoding="utf-8")
        assert "worktrees" in (tree / ".git").read_text(encoding="utf-8")
        assert "error" not in registry.call(
            "update_part_script",
            {"project": "demo", "part_id": "box", "script": BOX_V2_SCRIPT},
        )
        assert not (tree / ".history").exists()  # no throwaway repo
        head = service.history.resolve_ref(canonical, "feat")
        assert head == service.history.head(tree)
        entry = [b for b in branches.list("demo")["branches"]
                 if b["name"] == "feat"][0]
        assert entry["head"] == head  # the commit is visible to branch_list

    def test_a_copied_project_never_writes_into_the_original(self, demo, kernel,
                                                             tmp_path):
        """A copied project brings the original's branch trees along, whose
        .git files still point at the ORIGINAL repo — following one commits
        the copy's edits into the project it was copied from."""
        service, registry, branches = demo
        branches.create("demo", "feat")
        locks.set_client_id("agent_a")
        branches.switch("demo", "feat")
        assert "error" not in registry.call(
            "update_part_script",
            {"project": "demo", "part_id": "box", "script": BOX_V2_SCRIPT},
        )
        original = service.store.canonical_path_of("demo")
        original_head = service.history.resolve_ref(original, "feat")
        original_script = (original / ".history" / "trees" / "feat" / "parts"
                           / "box.py").read_bytes()

        copy_root = tmp_path / "copied"
        shutil.copytree(tmp_path / "projects", copy_root, symlinks=True)
        copied = AgentCADService(copy_root, kernel, EventBus())
        copy_registry = build_registry(copied)
        copy_canonical = copied.store.canonical_path_of("demo")

        locks.set_client_id("agent_a")
        copied.branches.switch("demo", "feat")
        edited = BOX_V2_SCRIPT.replace("* 2", "* 7")
        assert "error" not in copy_registry.call(
            "update_part_script",
            {"project": "demo", "part_id": "box", "script": edited},
        )

        # The copy's commit landed in the COPY's repo...
        copy_head = copied.history.resolve_ref(copy_canonical, "feat")
        assert copy_head != original_head
        assert copied.store.read_script("demo", "box") == edited
        assert _inside(Path(copied.store.path_of("demo")), copy_canonical)
        # ...and the original is untouched, ref and working tree alike.
        assert service.history.resolve_ref(original, "feat") == original_head
        assert (original / ".history" / "trees" / "feat" / "parts"
                / "box.py").read_bytes() == original_script

    def test_branch_worktrees_are_never_tracked(self, demo):
        """info/exclude's '.history/' keeps `git add -A` in the main tree from
        seeing .history/trees/** (verified empirically)."""
        service, registry, branches = demo
        branches.create("demo", "feat")
        assert "error" not in registry.call(
            "update_part_script",
            {"project": "demo", "part_id": "box", "script": BOX_V2_SCRIPT},
        )
        canonical = service.store.canonical_path_of("demo")
        tracked = _git(canonical / ".history", canonical, "ls-files").splitlines()
        assert "project.json" in tracked
        assert not [f for f in tracked if f.startswith(".history")]
        assert _git(canonical / ".history", canonical,
                    "status", "--porcelain") == ""


class TestPerBranchLocksAndUndo:
    pytestmark = _GIT

    def test_turn_locks_are_per_branch(self, demo):
        _service, registry, branches = demo
        branches.create("demo", "feat")
        locks.set_client_id("agent_a")
        branches.switch("demo", "feat")
        assert "error" not in registry.call("acquire_turn", {"project": "demo"})

        # B is on master: A's turn on 'feat' does not block it (FR2).
        locks.set_client_id("agent_b")
        assert "error" not in registry.call(
            "update_part_script",
            {"project": "demo", "part_id": "box", "script": BOX_V2_SCRIPT},
        )
        assert registry.call("get_turn", {"project": "demo"})["lock"] is None

        # ...but a turn held on master blocks another client on master.
        assert "error" not in registry.call("acquire_turn", {"project": "demo"})
        locks.set_client_id("agent_c")
        blocked = registry.call(
            "update_part_script",
            {"project": "demo", "part_id": "box", "script": BOX_SCRIPT},
        )
        assert blocked["error"]["type"] == "conflict_error"
        assert "agent_b" in blocked["error"]["message"]

        # A, on its own branch, still writes.
        locks.set_client_id("agent_a")
        assert "error" not in registry.call(
            "update_part_script",
            {"project": "demo", "part_id": "box", "script": BOX_V2_SCRIPT},
        )

    def test_build_state_badges_are_per_branch(self, demo):
        """get_project reports each part's ok/error badge from the in-memory
        status map; keyed by project alone it would show one branch's badges
        on another branch's parts."""
        service, registry, branches = demo
        branches.create("demo", "feat")

        locks.set_client_id("agent_b")            # master: build it green
        assert registry.call("get_part", {"project": "demo", "part_id": "box"}
                             )["status"]["state"] == "ok"
        assert _state(registry, "box") == "ok"

        locks.set_client_id("agent_a")
        branches.switch("demo", "feat")
        assert _state(registry, "box") == "unbuilt"   # master's badge is not ours
        broken = BOX_SCRIPT.replace("return part.part", "return no_such_name")
        assert registry.call(
            "update_part_script",
            {"project": "demo", "part_id": "box", "script": broken},
        )["error"]
        assert _state(registry, "box") == "error"

        locks.set_client_id("agent_b")            # ...and master stayed green
        assert _state(registry, "box") == "ok"

    def test_undo_stacks_are_per_branch(self, demo):
        service, registry, branches = demo
        branches.create("demo", "feat")
        canonical = service.store.canonical_path_of("demo")

        locks.set_client_id("agent_b")            # stays on the default branch
        master_undo = service.undo_cursor.status("demo")["undo"]
        master_head = service.history.head(canonical)

        locks.set_client_id("agent_a")
        branches.switch("demo", "feat")
        assert service.undo_cursor.status("demo")["undo"] == []  # fresh stack
        registry.call("update_part_script", {"project": "demo", "part_id": "box",
                                             "script": BOX_V2_SCRIPT})
        assert service.undo_cursor.status("demo")["undo"] == ["project_changed box"]

        # A's edit landed on neither B's stack nor B's tree.
        locks.set_client_id("agent_b")
        assert service.undo_cursor.status("demo")["undo"] == master_undo
        assert service.history.head(canonical) == master_head
        assert (canonical / "parts" / "box.py").read_text() == BOX_SCRIPT

        locks.set_client_id("agent_a")
        assert service.store.read_script("demo", "box") == BOX_V2_SCRIPT
        service.undo_cursor.undo("demo")
        assert service.store.read_script("demo", "box") == BOX_SCRIPT
        # ...and undoing on 'feat' moved only 'feat'.
        assert service.history.head(service.store.path_of("demo")) != master_head
        locks.set_client_id("agent_b")
        assert service.history.head(canonical) == master_head


class TestTags:
    pytestmark = _GIT

    def test_tag_round_trip_is_byte_identical_and_survives_branch_delete(
        self, demo, registry_error
    ):
        service, registry, branches = demo
        canonical = service.store.canonical_path_of("demo")
        tagged = branches.tag("demo", "v1", "shipped to the shop")
        assert tagged["tag"] == "v1"
        assert tagged["commit"] == service.history.head(canonical)
        manifest_bytes = (canonical / "project.json").read_bytes()
        script_bytes = (canonical / "parts" / "box.py").read_bytes()

        versions = branches.versions("demo")
        assert [v["name"] for v in versions] == ["v1"]
        assert versions[0]["message"] == "shipped to the shop"
        assert versions[0]["author"] and versions[0]["ts"]
        assert versions[0]["referrers"] == []

        # Re-tagging the same name is refused (tags are immutable, FR5).
        assert registry_error(branches.tag, "demo", "v1") == "conflict_error"

        registry.call("update_part_script", {"project": "demo", "part_id": "box",
                                             "script": BOX_V2_SCRIPT})
        assert (canonical / "parts" / "box.py").read_bytes() != script_bytes

        restored = registry.call("project_restore",
                                 {"project": "demo", "commit": "v1"})
        assert "error" not in restored, restored
        assert restored["restored"] == "v1"
        assert (canonical / "project.json").read_bytes() == manifest_bytes
        assert (canonical / "parts" / "box.py").read_bytes() == script_bytes

        # AC5: the tag survives deleting the branch it was made on.
        branches.create("demo", "feat")
        locks.set_client_id("agent_a")
        branches.switch("demo", "feat")
        branches.tag("demo", "v2", "on feat")
        branches.switch("demo", "master")
        branches.delete("demo", "feat")
        assert [v["name"] for v in branches.versions("demo")] == ["v2", "v1"]
        again = registry.call("project_restore", {"project": "demo", "commit": "v1"})
        assert "error" not in again, again

    def test_invalid_and_unknown_refs(self, demo, registry_error):
        _service, registry, branches = demo
        assert registry_error(branches.tag, "demo", "V1") == "validation_error"
        missing = registry.call("project_restore",
                                {"project": "demo", "commit": "no-such-tag"})
        assert missing["error"]["type"] == "notfound_error"
        # A malformed ref stays a validation_error, as it was for commit ids.
        bad = registry.call("project_restore", {"project": "demo", "commit": "a..b"})
        assert bad["error"]["type"] == "validation_error"


class TestCacheAcrossBranches:
    pytestmark = _GIT

    def test_mesh_cache_is_reused_across_branches(self, demo, monkeypatch):
        """FR3/FR13: identical content on another branch is a cache hit with
        zero kernel builds, because .cache/ stays canonical."""
        service, _registry, branches = demo
        assert service.get_metrics("demo", "box")["volume_mm3"] == pytest.approx(
            1000.0, rel=1e-6
        )
        canonical = service.store.canonical_path_of("demo")
        cached = list((canonical / ".cache").glob("*.acm"))

        branches.create("demo", "feat")
        locks.set_client_id("agent_a")
        branches.switch("demo", "feat")

        calls = {"build": 0}
        original = service.kernel.request

        def counting(method, params, timeout_s=None, affinity=None):
            if method == "build":
                calls["build"] += 1
            return original(method, params, timeout_s=timeout_s, affinity=affinity)

        monkeypatch.setattr(service.kernel, "request", counting)
        service._status.clear()  # force the full cache-key path, not memoization

        assert service.get_metrics("demo", "box")["volume_mm3"] == pytest.approx(
            1000.0, rel=1e-6
        )
        assert calls["build"] == 0
        # The branch reuses the canonical cache dir; nothing is duplicated.
        assert service.store.cache_dir("demo") == canonical / ".cache"
        assert not (canonical / ".history" / "trees" / "feat" / ".cache").exists()
        assert list((canonical / ".cache").glob("*.acm")) == cached


class TestReferenceCacheSignature:
    """FR13 for reference parts: a worktree checkout stamps a fresh mtime, so
    the signature must be content-addressed (the one service.py change)."""

    def test_signature_is_content_addressed(self, kernel, tmp_path):
        from .conftest import make_test_service

        service = make_test_service(tmp_path / "projects", kernel)
        service.store.create("demo")
        record = PartRecord(id="ref", label="Ref", material="steel",
                            kind="reference", source="imports/widget.step")
        imported = service.store.imports_dir("demo") / "widget.step"
        imported.write_bytes(b"ISO-10303-21;\n")

        first = service._content_signature("demo", record)
        assert "sha256" in first
        os.utime(imported, (1_000_000, 1_000_000))
        assert service._content_signature("demo", record) == first

        # The same bytes at a different path (another worktree) key the same.
        service.store.create("other")
        other = service.store.imports_dir("other") / "widget.step"
        other.write_bytes(b"ISO-10303-21;\n")
        assert service._content_signature("other", record) == first

        imported.write_bytes(b"ISO-10303-21;\n(changed)\n")
        assert service._content_signature("demo", record) != first




# ------------------- second-review regressions (Codex, X1 / X4 / X5)


class TestUnambiguousBranchRefs:
    """X1 — ``git rev-parse <name>`` searches refs/tags BEFORE refs/heads, so a
    tag can shadow a branch. Branch operations must name refs/heads/<name>."""

    pytestmark = _GIT

    def test_x1_a_tag_shadowing_a_branch_never_steers_branch_ops(self, demo):
        service, registry, branches = demo
        canonical = service.store.canonical_path_of("demo")
        branches.create("demo", "feat")
        branch_head = service.history.resolve_ref(canonical, "feat")

        # master moves on, and a TAG called 'feat' is pinned to its head.
        assert "error" not in registry.call(
            "update_part_script",
            {"project": "demo", "part_id": "box", "script": BOX_V2_SCRIPT})
        master_head = service.history.resolve_ref(canonical, "master")
        assert master_head != branch_head
        _git(canonical / ".history", canonical, "tag", "feat", master_head)

        assert service.history.resolve_ref(canonical, "feat") == master_head
        assert service.history.resolve_branch(canonical, "feat") == branch_head
        assert service.history.resolve_tag(canonical, "feat") == master_head

        # forking from the caller's current branch forks the BRANCH's head
        locks.set_client_id("agent_a")
        branches.switch("demo", "feat")
        branches.create("demo", "feat-fork")
        assert service.history.resolve_branch(canonical, "feat-fork") \
            == branch_head
        assert service.store.read_script("demo", "box") == BOX_SCRIPT

        # ...and a worktree materialized for the branch carries the branch
        shutil.rmtree(canonical / ".history" / "trees" / "feat")
        tree = branches.tree_of("demo", "feat")
        assert (tree / "parts" / "box.py").read_text() == BOX_SCRIPT


class TestBrokenWorktreeFailsClosed:
    """X4 — ``resolve_path`` is total by contract (a read of a project whose
    tree vanished degrades to the canonical directory). A WRITE must not
    degrade that way: it would land one branch's edits on the default one."""

    pytestmark = _GIT

    def test_x4_a_write_never_silently_lands_on_the_default_branch(self, demo):
        service, registry, branches = demo
        canonical = service.store.canonical_path_of("demo")
        branches.create("demo", "feat")
        locks.set_client_id("agent_a")
        branches.switch("demo", "feat")
        tree = canonical / ".history" / "trees" / "feat"
        default_before = (canonical / "parts" / "box.py").read_bytes()
        shutil.rmtree(tree)

        result = registry.call(
            "update_part_script",
            {"project": "demo", "part_id": "box", "script": BOX_V2_SCRIPT})

        if "error" in result:
            assert result["error"]["type"] == "conflict_error", result
        else:
            assert (tree / "parts" / "box.py").read_text() == BOX_V2_SCRIPT
        assert (canonical / "parts" / "box.py").read_bytes() == default_before
        assert branches.current("demo") == "feat"

    def test_x4_a_tree_that_cannot_be_restored_refuses_the_write(
            self, demo, monkeypatch, registry_error):
        from agentcad.core.model import ValidationError

        service, _registry, branches = demo
        canonical = service.store.canonical_path_of("demo")
        branches.create("demo", "feat")
        locks.set_client_id("agent_a")
        branches.switch("demo", "feat")
        shutil.rmtree(canonical / ".history" / "trees" / "feat")
        default_before = (canonical / "parts" / "box.py").read_bytes()

        def refuse(*_args, **_kwargs):
            raise ValidationError("no working tree for you")

        monkeypatch.setattr(branches, "_materialize", refuse)

        assert registry_error(
            service.store.write_script, "demo", "box", BOX_V2_SCRIPT
        ) == "conflict_error"
        assert (canonical / "parts" / "box.py").read_bytes() == default_before


class TestSnapshotFailuresAreLoud:
    """X5 — ``snapshot`` returns None both for 'nothing to commit' (fine) and
    for a git failure (not fine). Switching, tagging and deleting used to
    ignore the difference and lose the tree's uncommitted state."""

    pytestmark = _GIT

    @staticmethod
    def _break_snapshot(monkeypatch, service):
        monkeypatch.setattr(service.history, "snapshot", lambda *a, **k: None)

    def test_x5_switch_refuses_when_a_dirty_tree_cannot_be_snapshotted(
            self, demo, monkeypatch, registry_error):
        service, _registry, branches = demo
        canonical = service.store.canonical_path_of("demo")
        branches.create("demo", "feat")
        (canonical / "parts" / "box.py").write_text(BOX_V2_SCRIPT,
                                                    encoding="utf-8")
        self._break_snapshot(monkeypatch, service)

        assert registry_error(branches.switch, "demo", "feat") == "conflict_error"
        assert branches.current("demo") == "master"
        assert (canonical / "parts" / "box.py").read_text() == BOX_V2_SCRIPT

    def test_x5_switch_of_a_clean_tree_is_unaffected(self, demo, monkeypatch):
        service, _registry, branches = demo
        branches.create("demo", "feat")
        self._break_snapshot(monkeypatch, service)  # clean tree: nothing to do

        locks.set_client_id("agent_a")
        assert branches.switch("demo", "feat") == "feat"

    def test_x5_tag_refuses_when_a_dirty_tree_cannot_be_snapshotted(
            self, demo, monkeypatch, registry_error):
        service, _registry, branches = demo
        canonical = service.store.canonical_path_of("demo")
        (canonical / "parts" / "box.py").write_text(BOX_V2_SCRIPT,
                                                    encoding="utf-8")
        self._break_snapshot(monkeypatch, service)

        assert registry_error(branches.tag, "demo", "v1") == "conflict_error"
        assert branches.versions("demo") == []

    def test_x5_delete_refuses_a_dirty_tree_it_cannot_snapshot(
            self, demo, monkeypatch, registry_error):
        service, _registry, branches = demo
        canonical = service.store.canonical_path_of("demo")
        branches.create("demo", "feat")
        tree = canonical / ".history" / "trees" / "feat"
        (tree / "parts" / "box.py").write_text(BOX_V2_SCRIPT, encoding="utf-8")
        self._break_snapshot(monkeypatch, service)

        assert registry_error(
            branches.delete, "demo", "feat") == "validation_error"
        assert "feat" in {b["name"] for b in branches.list("demo")["branches"]}
        assert (tree / "parts" / "box.py").read_text() == BOX_V2_SCRIPT

    def test_x5_delete_snapshots_a_dirty_tree_before_removing_it(self, demo):
        service, _registry, branches = demo
        canonical = service.store.canonical_path_of("demo")
        branches.create("demo", "feat")
        tree = canonical / ".history" / "trees" / "feat"
        (tree / "parts" / "box.py").write_text(BOX_V2_SCRIPT, encoding="utf-8")

        payload = branches.delete("demo", "feat")

        assert payload["deleted"] == "feat"
        assert not tree.exists()
        assert "feat" not in {b["name"] for b in payload["branches"]}


# --------------------------- verifier regressions (D2 / G1, PRD-001)


class TestDetachedWorktreeIsRepaired:
    """D2 — a branch tree that lost its ``.git`` file still *looks* like a
    checkout (project.json is right there), so the write path's fast path
    accepted it: the edit landed in a directory git no longer knew about, the
    next snapshot ``git init``-ed a throwaway repo inside it, and the following
    switch discarded the lot. Re-attach the tree; never discard its content."""

    pytestmark = _GIT

    @staticmethod
    def _on_broken_feat(service, branches):
        canonical = service.store.canonical_path_of("demo")
        branches.create("demo", "feat")
        locks.set_client_id("agent_a")
        branches.switch("demo", "feat")
        tree = canonical / ".history" / "trees" / "feat"
        (tree / ".git").unlink()
        return canonical, tree

    def test_d2_a_write_re_attaches_a_tree_that_lost_its_git_link(self, demo):
        service, registry, branches = demo
        canonical, tree = self._on_broken_feat(service, branches)

        result = registry.call(
            "update_part_script",
            {"project": "demo", "part_id": "box", "script": BOX_V2_SCRIPT})

        if "error" in result:  # a git too old to repair: refuse, don't misfile
            assert result["error"]["type"] == "conflict_error", result
            assert (tree / "parts" / "box.py").read_text() == BOX_SCRIPT
            return
        assert (tree / ".git").is_file()
        assert not (tree / ".history").exists()  # no throwaway repo
        assert (tree / "parts" / "box.py").read_text() == BOX_V2_SCRIPT
        # the edit is on the real branch — visible to git, and to branch_list
        assert service.history._run(
            canonical, "cat-file", "blob", "refs/heads/feat:parts/box.py"
        ).stdout == BOX_V2_SCRIPT
        assert (canonical / "parts" / "box.py").read_text() == BOX_SCRIPT
        # ...and survives a round trip through the default branch
        branches.switch("demo", "master")
        branches.switch("demo", "feat")
        assert (tree / "parts" / "box.py").read_text() == BOX_V2_SCRIPT

    def test_d2_an_unrepairable_tree_is_refused_not_discarded(
            self, demo, registry_error):
        service, _registry, branches = demo
        canonical, tree = self._on_broken_feat(service, branches)
        (tree / "parts" / "box.py").write_text("uncommitted\n", encoding="utf-8")
        # git repairs a tree from its admin directory; without one it cannot.
        shutil.rmtree(canonical / ".history" / "worktrees" / "feat")

        assert registry_error(
            service.store.write_script, "demo", "box", BOX_V2_SCRIPT
        ) == "conflict_error"
        assert (tree / "parts" / "box.py").read_text() == "uncommitted\n"
        assert (canonical / "parts" / "box.py").read_text() == BOX_SCRIPT

        # and materializing it again says so instead of deleting the content
        branches.switch("demo", "master")
        assert registry_error(branches.switch, "demo", "feat") == "conflict_error"
        assert (tree / "parts" / "box.py").read_text() == "uncommitted\n"


class TestImportsFollowTheBranch:
    """G1 — an ingested STL/STEP payload is authored state (it is committed,
    and a reference part points at it), so ingest must go through the write
    guard like a script write: same branch tree, same ensure_checkout."""

    pytestmark = _GIT

    def test_g1_ingest_lands_in_the_callers_branch_tree(self, demo, tmp_path):
        from agentcad.core.imports import ingest_file

        service, _registry, branches = demo
        canonical = service.store.canonical_path_of("demo")
        branches.create("demo", "feat")
        locks.set_client_id("agent_a")
        branches.switch("demo", "feat")
        src = tmp_path / "widget.step"
        src.write_bytes(b"ISO-10303-21;\n")

        name = ingest_file(service.store, "demo", "widget.step", str(src))

        tree = canonical / ".history" / "trees" / "feat"
        assert (tree / "imports" / name).read_bytes() == b"ISO-10303-21;\n"
        assert not (canonical / "imports" / name).exists()

    def test_g1_ingest_onto_a_detached_tree_repairs_or_refuses(
            self, demo, tmp_path, registry_error):
        from agentcad.core.imports import ingest_file

        service, _registry, branches = demo
        canonical = service.store.canonical_path_of("demo")
        branches.create("demo", "feat")
        locks.set_client_id("agent_a")
        branches.switch("demo", "feat")
        tree = canonical / ".history" / "trees" / "feat"
        (tree / ".git").unlink()
        src = tmp_path / "widget.step"
        src.write_bytes(b"ISO-10303-21;\n")

        try:
            name = ingest_file(service.store, "demo", "widget.step", str(src))
        except ConflictError:
            assert not (canonical / "imports" / "widget.step").exists()
            return
        assert (tree / ".git").is_file()
        assert (tree / "imports" / name).read_bytes() == b"ISO-10303-21;\n"
        assert not (canonical / "imports" / name).exists()
