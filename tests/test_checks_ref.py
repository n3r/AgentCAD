"""``--ref`` and ``--verify-determinism`` (PRD-004, slice 3).

This is the riskiest code in geometry CI: it touches the user's git
repository. So the assertions here are about *containment* first and
measurement second.

* **AC7 — a ref check leaves the project byte-identical.** Every file under
  the project directory is hashed before and after; the maps must be equal,
  ``.cache/`` included. The ref is materialized into a throwaway detached
  worktree and measured by a second, ephemeral service rooted there.
* **The ephemeral service must never commit into the user's repository.** It
  is one forgotten line (``bus.on_publish = None``) away from doing exactly
  that through the linked worktree, so head, commit count, ``git status`` and
  the worktree registry are all pinned — including after an exception injected
  mid-run.
* **Refs resolve branch → tag → commit, never through ``rev-parse``.** git
  searches tags *before* branches, so an ambiguous name resolves as the branch
  and says so (PRD-001 X1).
* **A ref check measures the commit.** A branch with uncommitted edits is
  reported ``source.dirty`` and measured as of its last snapshot — a check may
  not snapshot on the user's behalf.
* **AC6 — determinism.** Every part built a second time on a cold cache, with
  ``.acm``/``.faces.u32``/metrics/SVG compared byte for byte. DXF is excluded
  by name, because ezdxf stamps a timestamp and fresh GUIDs into every
  document it creates.

The git admin directory (``.history/``) is excluded from the byte-identity map
and asserted separately (head, commit count, ``status``, the worktree list):
git's own bookkeeping — a worktree registration, an index stat refresh — is
git's, and the design says plainly that a stale registration is expected and
self-healing. What must not move is a single byte the *user* owns.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest

from agentcad.core.checks import (
    CheckRunner,
    _byte_diff,
    _compare_builds,
    validate_report,
)
from agentcad.core.model import NotFoundError, ValidationError
from agentcad.core.service import AgentCADService, EventBus
from agentcad.core.tools import build_registry
from agentcad.core import locks
from agentcad.core.branches import pinned_tree_var

from .conftest import BOX_SCRIPT

pytestmark = [
    pytest.mark.integration,
    pytest.mark.portability,
    pytest.mark.slow,
    pytest.mark.timeout(900),
    pytest.mark.skipif(shutil.which("git") is None,
                       reason="git is required to materialize a ref"),
]

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


@pytest.fixture(autouse=True)
def _reset_context():
    """Identity and the branch pin are ContextVars: rebind them per test."""
    cid = locks.client_id_var.set("local")
    pin = pinned_tree_var.set(None)
    yield
    locks.client_id_var.reset(cid)
    pinned_tree_var.reset(pin)


@pytest.fixture
def stack(kernel, tmp_path):
    """The **real** service — history matters here, so the snapshot hook that
    `make_test_service` disables is exactly what we need live."""
    service = AgentCADService(tmp_path / "projects", kernel, EventBus())
    registry = build_registry(service)
    return service, registry, CheckRunner(service, registry)


def _tiny(service, name: str = "tiny") -> str:
    """A one-part project with real git history (every create publishes, and
    the service's bus hook snapshots every publish)."""
    service.create_project(name)
    service.create_part(name, "cube", script=BOX_SCRIPT)
    return name


def _example(service, tmp_path, name: str) -> str:
    dest = tmp_path / "copies" / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(EXAMPLES / name, dest,
                    ignore=shutil.ignore_patterns(".cache", "exports"))
    proj = service.open_project(str(dest))["name"]
    service.history.snapshot(dest, "import the example")
    return proj


def _fingerprint(root: Path) -> dict[str, str]:
    """sha256 of every file the user owns under *root* (``.history/`` — git's
    own admin state — excluded; it is asserted separately)."""
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if rel.parts and rel.parts[0] == ".history":
            continue
        if path.is_file():
            out[str(rel)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def _git(service, path: Path, *args: str) -> str:
    return (service.history._run(path, *args, check=False).stdout or "").strip()


def _worktrees(service, path: Path) -> list[str]:
    listing = _git(service, path, "worktree", "list", "--porcelain")
    return [line for line in listing.splitlines()
            if line.startswith("worktree ")]


def _repo_state(service, path: Path) -> dict:
    return {"head": service.history.head(path),
            "commits": _git(service, path, "rev-list", "--count", "HEAD"),
            "status": _git(service, path, "status", "--porcelain"),
            "worktrees": len(_worktrees(service, path))}


def _stage(report: dict, name: str) -> dict:
    return next(stage for stage in report["stages"] if stage["name"] == name)


def _item(report: dict, ident: str) -> dict:
    for stage in report["stages"]:
        for item in stage["items"]:
            if item["id"] == ident:
                return item
    raise AssertionError(f"no item {ident!r} in the report")


# ------------------------------------------------------- 1. containment


def test_ac7_a_ref_check_leaves_the_users_project_byte_identical(
        stack, tmp_path):
    """AC7, the whole point of the ephemeral service: not one byte the user
    owns moves — the working tree *and* `.cache/`.

    The cache is warmed first, deliberately: an untouched `.cache/` is only
    evidence if there was something in it to disturb. And the ref check's own
    rows prove the stated price — it ran cold.
    """
    service, _registry, runner = stack
    proj = _example(service, tmp_path, "prototyping")
    path = service.store.canonical_path_of(proj)
    branch = service.branches.default_branch(proj)   # writes config.json first
    runner.run(proj, stages=("build",))              # warm the real cache

    before = _fingerprint(path)
    state = _repo_state(service, path)
    assert any(name.startswith(".cache") for name in before), \
        "the cache must hold something for its stillness to mean anything"

    report = runner.run(proj, ref=branch, stages=("build", "assembly"))

    assert _fingerprint(path) == before
    assert _repo_state(service, path) == state
    assert report["source"] == {"kind": "branch", "ref": branch,
                                "sha": state["head"], "label": None,
                                "host_sha": None, "dirty": False}
    assert report["status"] == "green" and report["exit_code"] == 0
    assert validate_report(report) == []
    # The stated price (design Decision 5): the work dir holds no `.cache/`,
    # so every part is a real kernel build.
    assert all(item["details"]["cached"] is False
               for item in _stage(report, "build")["items"])


def test_the_ephemeral_service_never_commits_into_the_users_repository(
        stack, tmp_path):
    """The design's risk #5, as a test: the materialized tree is a LINKED
    worktree of the user's `.history` repo, so a single `project_changed`
    publish reaching the snapshot hook would commit into the user's real
    repository from a command whose contract is "never mutates".

    A build publishes `rebuild_started`/`part_built` on every part, so this
    run does exercise the bus it must not use.
    """
    service, _registry, runner = stack
    proj = _tiny(service)
    path = service.store.canonical_path_of(proj)
    branch = service.branches.default_branch(proj)
    before = _repo_state(service, path)
    work = tmp_path / "work"

    report = runner.run(proj, ref=branch, work_dir=str(work))

    after = _repo_state(service, path)
    assert after["head"] == before["head"], "a check committed to the repo"
    assert after["commits"] == before["commits"]
    assert after["status"] == "", "a check left the working tree dirty"
    assert after["worktrees"] == before["worktrees"]
    assert not (work / path.name).exists(), "the worktree was not removed"
    assert report["warnings"] == [] or all(
        "worktree" not in warning for warning in report["warnings"])


def test_the_worktree_is_released_even_when_the_run_raises(
        stack, tmp_path, monkeypatch):
    """Cleanup is a `finally`, not a happy path. An exception injected between
    the `worktree add` and the teardown must still leave `git worktree list`
    and the work dir as it found them."""
    service, _registry, runner = stack
    proj = _tiny(service)
    path = service.store.canonical_path_of(proj)
    branch = service.branches.default_branch(proj)
    before = _repo_state(service, path)
    work = tmp_path / "work"

    def explode(*args, **kwargs):
        raise RuntimeError("injected mid-run")

    monkeypatch.setattr(CheckRunner, "_measure", explode)
    with pytest.raises(RuntimeError, match="injected mid-run"):
        runner.run(proj, ref=branch, work_dir=str(work))

    assert _repo_state(service, path) == before
    assert not (work / path.name).exists()


def test_the_default_work_dir_is_a_temp_dir_and_is_deleted(stack, monkeypatch):
    """No `--work-dir` means a `mkdtemp` we own — and therefore clean up."""
    service, _registry, runner = stack
    proj = _tiny(service)
    branch = service.branches.default_branch(proj)
    seen: list[Path] = []
    original = CheckRunner._materialized

    def spy(self, canonical, sha, work_dir, warnings):
        seen.append(Path(work_dir))
        return original(self, canonical, sha, work_dir, warnings)

    monkeypatch.setattr(CheckRunner, "_materialized", spy)
    runner.run(proj, ref=branch, stages=("build",))

    assert seen and seen[0].name.startswith("agentcad-check-")
    assert not seen[0].exists()


def test_a_relative_work_dir_never_lands_inside_the_project(
        stack, tmp_path, monkeypatch):
    """`history._run` runs git with `cwd` set to the project, so a relative
    work dir handed straight to `worktree add` would materialize the throwaway
    tree *inside the user's project* — the one directory it may not touch."""
    service, _registry, runner = stack
    proj = _tiny(service)
    path = service.store.canonical_path_of(proj)
    branch = service.branches.default_branch(proj)
    before = _repo_state(service, path)
    monkeypatch.chdir(tmp_path)

    runner.run(proj, ref=branch, stages=("build",), work_dir="work")

    assert (tmp_path / "work").is_dir()
    assert not (path / "work").exists()
    assert _repo_state(service, path) == before


# --------------------------------------------------- 2. ref resolution


def test_a_branch_and_a_tag_with_the_same_name_resolve_to_the_branch(
        stack, tmp_path):
    """git's `rev-parse` searches refs/tags BEFORE refs/heads, so a tag named
    like a branch would silently answer for it (PRD-001 X1). The branch wins,
    and the ambiguity is named in `warnings`."""
    service, _registry, runner = stack
    proj = _tiny(service)
    path = service.store.canonical_path_of(proj)
    old = service.history.head(path)
    service.set_params(proj, "cube", {"size": 20.0})
    new = service.history.head(path)
    assert new != old
    service.history._run(path, "tag", "shared", old)
    service.history._run(path, "branch", "shared", new)

    report = runner.run(proj, ref="shared", stages=("build",))

    assert report["source"]["kind"] == "branch"
    assert report["source"]["sha"] == new
    assert any("branch and a tag" in warning
               for warning in report["warnings"]), report["warnings"]
    # And the tag is still reachable, explicitly.
    tagged = runner.run(proj, ref="refs/tags/shared", stages=("build",))
    assert tagged["source"] == {"kind": "tag", "ref": "refs/tags/shared",
                                "sha": old, "label": None, "host_sha": None,
                                "dirty": False}


def test_a_tag_and_a_bare_commit_id_both_resolve(stack):
    service, _registry, runner = stack
    proj = _tiny(service)
    path = service.store.canonical_path_of(proj)
    head = service.history.head(path)
    service.branches.tag(proj, "v1", "first release")

    tagged = runner.run(proj, ref="v1", stages=("build",))
    assert tagged["source"]["kind"] == "tag"
    assert tagged["source"]["sha"] == head

    # A short id is spelled out in full, so `source.sha` is one shape.
    by_commit = runner.run(proj, ref=head[:8], stages=("build",))
    assert by_commit["source"]["kind"] == "commit"
    assert by_commit["source"]["ref"] == head[:8]
    assert by_commit["source"]["sha"] == head
    assert by_commit["source"]["dirty"] is False


def test_a_ref_without_git_is_a_validation_error_naming_git(stack,
                                                            monkeypatch):
    """Two ways to have no git, both exit 2 with a message that says so."""
    service, _registry, runner = stack
    proj = _tiny(service)

    shutil.rmtree(service.store.canonical_path_of(proj) / ".history")
    with pytest.raises(ValidationError) as no_repo:
        runner.run(proj, ref="main")
    assert "git" in str(no_repo.value)

    monkeypatch.setattr(service.history, "available", lambda: False)
    with pytest.raises(ValidationError) as no_git:
        runner.run(proj, ref="main")
    assert "git" in str(no_git.value)


def test_an_unknown_ref_says_what_was_searched(stack):
    service, _registry, runner = stack
    proj = _tiny(service)

    with pytest.raises(NotFoundError) as excinfo:
        runner.run(proj, ref="no-such-ref")

    assert "no-such-ref" in str(excinfo.value)
    assert "refs/heads" in str(excinfo.value)
    assert excinfo.value.details["searched"] == ["refs/heads", "refs/tags",
                                                 "commit"]


def test_an_unknown_project_is_still_a_not_found_error(stack):
    _service, _registry, runner = stack
    with pytest.raises(NotFoundError):
        runner.run("no_such_project", ref="main")


# ------------------------------------------- 3. committed state vs disk


def test_a_dirty_branch_is_reported_and_the_commit_is_what_gets_measured(
        stack):
    """A ref check measures the **commit**. The runner says so — in
    `source.dirty` and in a warning — and it deliberately does not snapshot
    first: producing the evidence is the packet's job, not a check's.
    """
    service, _registry, runner = stack
    proj = _tiny(service)
    path = service.store.canonical_path_of(proj)
    branch = service.branches.default_branch(proj)
    # Through the STORE, so nothing publishes and nothing is snapshotted:
    # the commit still says 10 mm while the disk says 30 mm.
    manifest = service.store.manifest(proj)
    manifest["parts"][0]["params"] = {"size": 30.0}
    service.store.save_manifest(proj, manifest)
    assert _git(service, path, "status", "--porcelain")

    report = runner.run(proj, ref=branch, stages=("build",))

    assert report["source"]["dirty"] is True
    assert any("uncommitted" in warning and branch in warning
               for warning in report["warnings"]), report["warnings"]
    assert _item(report, "build:cube")["details"]["volume_mm3"] == \
        pytest.approx(10.0 ** 3)
    # The same runner over the working tree measures what is on disk.
    live = runner.run(proj, stages=("build",))
    assert live["source"]["dirty"] is True
    assert _item(live, "build:cube")["details"]["volume_mm3"] == \
        pytest.approx(30.0 ** 3)


# --------------------------------------------------- 4. determinism (AC6)


def test_byte_diff_finds_the_first_differing_offset(tmp_path):
    left, right = tmp_path / "a.bin", tmp_path / "b.bin"
    left.write_bytes(b"ACM1" + b"\x00" * 32)
    right.write_bytes(b"ACM1" + b"\x00" * 32)
    assert _byte_diff(left, right) is None

    right.write_bytes(b"ACM1" + b"\x00" * 8 + b"\x01" + b"\x00" * 23)
    assert _byte_diff(left, right) == 12
    # A file that is a prefix of the other differs at its own length.
    right.write_bytes(b"ACM1")
    assert _byte_diff(left, right) == 4


def test_compare_builds_names_which_artefact_diverged_and_where(tmp_path):
    """"Not deterministic" is not a useful sentence; this is what the row
    says instead."""
    cache_a, cache_b = tmp_path / "a", tmp_path / "b"
    cache_a.mkdir()
    cache_b.mkdir()
    metrics = {"volume_mm3": 1000.0, "mass_g": 2.7, "area_mm2": 600.0}
    (cache_a / "k.acm").write_bytes(b"ACM1" + b"\x02" * 16)
    (cache_b / "k.acm").write_bytes(b"ACM1" + b"\x02" * 16)
    problems, compared = _compare_builds("k", "k", cache_a, cache_b, metrics,
                                         metrics)
    assert problems == []
    # An artefact neither build wrote is NOT counted as agreement.
    assert compared == ["cache_key", ".acm", "volume_mm3", "mass_g",
                        "area_mm2"]

    (cache_b / "k.acm").write_bytes(b"ACM1" + b"\x02" * 8 + b"\x03" * 8)
    (cache_a / "k.faces.u32").write_bytes(b"\x00\x01")
    problems, compared = _compare_builds("k", "k", cache_a, cache_b, metrics,
                                         {**metrics, "mass_g": 2.8})
    assert ".faces.u32" in compared
    assert any(".acm differs at byte 12" in problem for problem in problems)
    assert any(".faces.u32 was written by one build" in problem
               for problem in problems)
    assert any("mass_g differs" in problem for problem in problems)
    # A key mismatch short-circuits: nothing else is comparable.
    assert _compare_builds("k", "other", cache_a, cache_b, metrics,
                           metrics) == (
        ["the cache key differs (k vs other) — the same script and parameters "
         "hashed to two different content ids"], ["cache_key"])


def test_the_determinism_stage_compares_the_stable_artefacts_and_skips_dxf(
        stack):
    """FR6 on a one-part project: the row names what it compared, and DXF is
    excluded **by name** with the reason, never silently."""
    service, _registry, runner = stack
    proj = _tiny(service)

    report = runner.run(proj, stages=("build",), verify_determinism=True)

    stage = _stage(report, "determinism")
    assert stage["status"] == "green"
    row = _item(report, "determinism:cube")
    assert row["kind"] == "part" and row["status"] == "pass"
    assert row["details"]["compared"] == [
        "cache_key", ".acm", ".faces.u32", "volume_mm3", "mass_g", "area_mm2",
        "drawing.svg"]
    assert row["details"]["diverged"] == []
    dxf = _item(report, "determinism:dxf")
    assert dxf["status"] == "skip" and dxf["reason"] == "not_byte_stable"
    assert "ezdxf" in dxf["hint"] and "$TDCREATE" in dxf["hint"]
    assert report["exit_code"] == 0
    assert validate_report(report) == []


def test_determinism_composes_with_a_ref_check(stack):
    """The determinism stage runs against whatever service the pipeline was
    given — the ephemeral one included, where BOTH builds are cold."""
    service, _registry, runner = stack
    proj = _tiny(service)
    path = service.store.canonical_path_of(proj)
    before = _repo_state(service, path)
    branch = service.branches.default_branch(proj)

    report = runner.run(proj, ref=branch, stages=("build",),
                        verify_determinism=True)

    assert _stage(report, "determinism")["status"] == "green"
    assert _item(report, "determinism:cube")["status"] == "pass"
    assert report["exit_code"] == 0
    assert _repo_state(service, path) == before


@pytest.mark.parametrize("name", ["construction", "prototyping"])
def test_ac6_verify_determinism_is_green_on_a_clean_example(stack, tmp_path,
                                                            name):
    """AC6: every part of a bundled example builds byte-identically twice."""
    service, _registry, runner = stack
    proj = _example(service, tmp_path, name)

    report = runner.run(proj, stages=("build",), verify_determinism=True)

    stage = _stage(report, "determinism")
    assert stage["status"] == "green", [
        (item["id"], item["details"].get("diverged"))
        for item in stage["items"] if item["status"] != "pass"]
    parts = [entry["id"] for entry in service.store.manifest(proj)["parts"]]
    assert [item["subject"] for item in stage["items"]] == [*parts, "dxf"]
    assert report["exit_code"] == 0
    assert validate_report(report) == []


def test_a_part_that_will_not_build_makes_determinism_an_error_not_a_pass(
        stack):
    """"We do not know" is `error`, never a green — the build stage rules on
    the failure itself."""
    service, _registry, runner = stack
    proj = _tiny(service)
    service.store.write_script(proj, "cube",
                               BOX_SCRIPT.replace("Box(p.size, p.size, p.size)",
                                                  "no_such_name(p.size)"))

    report = runner.run(proj, stages=(), verify_determinism=True)

    row = _item(report, "determinism:cube")
    assert row["status"] == "error" and row["error"]["type"]
    assert _stage(report, "determinism")["status"] == "red"
    assert report["exit_code"] == 1
