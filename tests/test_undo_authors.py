"""Snapshot authorship, author-aware undo and `revert` (PRD-008 slice 10).

Three things land here, in the order the design spec (Decisions 15 and 16)
argues for them:

* **Authorship** — ``snapshot()`` appends a ``Client:`` trailer to the commit
  *body*, so every existing subject-line assertion (``%s``) holds byte for
  byte, and ``log()`` parses it back into ``author`` (``None``, never
  ``"unknown"``, for a commit written before authorship existed).
* **``scope``** — ``undo``/``redo`` default to ``"any"``, which is the
  behavior the whole pre-existing undo suite pins. ``"mine"`` pops the
  caller's most recent entry, skipping everyone else's. The stacks are NOT
  re-keyed per client: a human pressing Cmd+Z to take back the agent's edit
  is the product's flagship loop.
* **``revert``** — when the caller's entry is no longer the branch head, the
  step is a real ``git revert`` (AC7's first half). An overlapping change is
  a structured refusal with the blocking commits named, never a partial
  apply (FR14).

``portability``: every test here shells git.
"""

from __future__ import annotations

import shutil

import pytest

from agentcad.core import locks
from agentcad.core.history import ProjectHistory
from agentcad.core.service import AgentCADService, EventBus
from agentcad.core.tools import build_registry

from .conftest import BOX_SCRIPT

pytestmark = [
    pytest.mark.integration,
    pytest.mark.portability,
    pytest.mark.skipif(shutil.which("git") is None, reason="git not found on PATH"),
]

BOX_V2 = BOX_SCRIPT.replace("p.size, p.size, p.size)", "p.size, p.size, p.size * 2)")
BOX_V3 = BOX_SCRIPT.replace("p.size, p.size, p.size)", "p.size, p.size, p.size * 3)")
assert BOX_V2 != BOX_SCRIPT and BOX_V3 not in (BOX_SCRIPT, BOX_V2)


@pytest.fixture
def demo(kernel, tmp_path):
    bus = EventBus()
    service = AgentCADService(tmp_path / "projects", kernel, bus)
    registry = build_registry(service)
    _as("browser:a")
    assert "error" not in registry.call("create_project", {"name": "demo"})
    for part in ("box", "pin"):
        created = registry.call(
            "create_part",
            {"project": "demo", "part_id": part, "script": BOX_SCRIPT},
        )
        assert "error" not in created, created
    return service, registry


def _as(client_id: str) -> None:
    locks.set_client_id(client_id)


def _script(registry, part: str, text: str) -> dict:
    result = registry.call(
        "update_part_script",
        {"project": "demo", "part_id": part, "script": text},
    )
    assert "error" not in result, result
    return result


def _text(service, part: str) -> str:
    return (service.store.path_of("demo") / "parts" / f"{part}.py").read_text()


def _log(service, limit: int = 20) -> list[dict]:
    return service.history.log(service.store.path_of("demo"), limit=limit)


# ------------------------------------------------------- authorship (FR13)


def test_the_client_trailer_round_trips_through_log(demo):
    service, registry = demo
    _as("chat:main")
    _script(registry, "box", BOX_V2)
    top = _log(service)[0]
    assert top["author"] == "chat:main"
    # The SUBJECT is untouched — every existing exact-message assertion in the
    # suite reads %s, which the trailer (a body line) cannot reach.
    assert top["message"] == "project_changed box"


def test_a_commit_without_the_trailer_reads_back_author_none(demo):
    service, _registry = demo
    path = service.store.path_of("demo")
    (path / "parts" / "box.py").write_text(BOX_V2, encoding="utf-8")
    service.history._run(path, "add", "-A")
    service.history._run(path, "commit", "-m", "hand-rolled, no trailer")
    top = _log(service)[0]
    assert top["message"] == "hand-rolled, no trailer"
    assert top["author"] is None


def test_project_history_rows_carry_the_author(demo):
    _service, registry = demo
    _as("browser:b")
    _script(registry, "box", BOX_V2)
    rows = registry.call("project_history", {"project": "demo"})["history"]
    assert rows[0]["author"] == "browser:b"


def test_a_trailer_is_never_appended_twice(demo):
    service, _registry = demo
    path = service.store.path_of("demo")
    (path / "parts" / "box.py").write_text(BOX_V2, encoding="utf-8")
    _as("browser:a")
    service.history.snapshot(path, "explicit\n\nClient: someone-else\n")
    body = service.history._run(path, "log", "-1", "--pretty=%B").stdout
    assert body.count("Client:") == 1
    assert _log(service)[0]["author"] == "someone-else"


# ------------------------------------------------------------ scope: "any"


def test_the_default_scope_is_byte_identical_to_today(demo):
    _service, registry = demo
    _as("browser:a")
    _script(registry, "box", BOX_V2)
    _as("browser:b")
    # No scope argument: browser:b undoes browser:a's edit, exactly as the
    # flagship Cmd+Z loop requires.
    undone = registry.call("undo", {"project": "demo"})
    assert "error" not in undone, undone
    assert undone["undone"] == "project_changed box"


def test_an_unknown_scope_is_a_validation_error(demo):
    _service, registry = demo
    bad = registry.call("undo", {"project": "demo", "scope": "everyone"})
    assert bad["error"]["type"] == "validation_error"


def test_status_reports_mine_counts(demo):
    service, registry = demo
    _as("browser:a")
    _script(registry, "box", BOX_V2)
    _as("browser:b")
    _script(registry, "pin", BOX_V2)
    status = registry.call("get_history", {"project": "demo"})
    assert len(status["undo"]) == 4  # 2 creates + 2 edits
    assert status["mine"]["undo"] == 1  # browser:b's pin edit only
    _as("browser:a")
    assert service.undo_cursor.status("demo")["mine"]["undo"] == 3


# ---------------------------------------------------------------- AC7


def test_ac7_mine_undo_reverts_only_my_edit(demo):
    """A edits part X, B edits part Y, A undoes — only X reverts."""
    service, registry = demo
    _as("browser:a")
    _script(registry, "box", BOX_V2)
    _as("browser:b")
    _script(registry, "pin", BOX_V3)

    _as("browser:a")
    undone = registry.call("undo", {"project": "demo", "scope": "mine"})
    assert "error" not in undone, undone
    assert _text(service, "box") == BOX_SCRIPT      # A's edit taken back
    assert _text(service, "pin") == BOX_V3          # B's edit stands

    top = _log(service)[0]
    assert top["message"].startswith("revert ")
    assert top["author"] == "browser:a"
    # A revert is not a restore: UndoCursor's post-restart fallback guard
    # keys on the "restore " prefix, and parent_of must still be the old head.
    assert not top["message"].startswith("restore ")
    assert service.history.parent_of(
        service.store.path_of("demo"), top["id"]
    ) == _log(service)[1]["id"]


def test_ac7_an_overlapping_change_is_a_structured_conflict(demo):
    """After B also edits X, A's undo of the X commit is refused with
    ``blocked_by`` naming B's commit — never a partial apply (FR14)."""
    service, registry = demo
    _as("browser:a")
    _script(registry, "box", BOX_V2)
    a_commit = _log(service)[0]["id"]
    _as("browser:b")
    _script(registry, "box", BOX_V3)
    b_commit = _log(service)[0]["id"]

    _as("browser:a")
    refused = registry.call("undo", {"project": "demo", "scope": "mine"})
    assert refused["error"]["type"] == "conflict_error", refused
    details = refused["error"]["details"]
    assert details["commit"] == a_commit
    assert details["reason"] == "overlapping_changes"
    assert "parts/box.py" in details["paths"]
    assert b_commit in details["blocked_by"]

    # Nothing landed, and the entry is still on A's stack to retry.
    assert _text(service, "box") == BOX_V3
    assert _log(service)[0]["id"] == b_commit
    assert service.undo_cursor.status("demo")["mine"]["undo"] >= 1
    assert service.history._run(
        service.store.path_of("demo"), "status", "--porcelain"
    ).stdout.strip() == ""


def test_mine_undo_with_nothing_of_mine_is_a_conflict(demo):
    _service, registry = demo
    _as("browser:a")
    _script(registry, "box", BOX_V2)
    _as("chat:main")
    refused = registry.call("undo", {"project": "demo", "scope": "mine"})
    assert refused["error"]["type"] == "conflict_error"


def test_redo_after_a_revert_reverts_the_revert(demo):
    service, registry = demo
    _as("browser:a")
    _script(registry, "box", BOX_V2)
    _as("browser:b")
    _script(registry, "pin", BOX_V3)

    _as("browser:a")
    assert "error" not in registry.call(
        "undo", {"project": "demo", "scope": "mine"})
    assert _text(service, "box") == BOX_SCRIPT

    redone = registry.call("redo", {"project": "demo", "scope": "mine"})
    assert "error" not in redone, redone
    assert _text(service, "box") == BOX_V2   # re-applied
    assert _text(service, "pin") == BOX_V3   # B's edit never moved
    assert _log(service)[0]["message"].startswith("revert ")


def test_the_post_restart_fallback_under_mine_checks_the_trailer(demo, kernel,
                                                                tmp_path):
    """A restart empties the in-memory undo stacks, and the fallback reads the
    latest snapshot out of the log instead. Under ``scope: "mine"`` that
    snapshot is only the caller's if its ``Client:`` trailer says so — this is
    the one place ``author`` comes from git rather than from the stack, and it
    had no test.
    """
    service, registry = demo
    _as("browser:a")
    _script(registry, "box", BOX_V2)

    # A fresh service over the same store: the cursor knows nothing.
    restarted = AgentCADService(tmp_path / "projects", kernel, EventBus())
    registry2 = build_registry(restarted)

    _as("browser:b")
    refused = registry2.call("undo", {"project": "demo", "scope": "mine"})
    assert refused["error"]["type"] == "conflict_error", refused
    assert _text(service, "box") == BOX_V2      # B took nothing back

    # …and "any" still steps back through it, because that scope never asks
    # whose it was.
    assert "error" not in registry2.call("undo", {"project": "demo"})
    assert _text(service, "box") == BOX_SCRIPT


def test_the_post_restart_fallback_under_mine_accepts_my_own_snapshot(
        demo, kernel, tmp_path):
    """The other half: the same fallback, taken by the identity whose trailer
    is on the commit."""
    service, registry = demo
    _as("browser:a")
    _script(registry, "box", BOX_V2)

    restarted = AgentCADService(tmp_path / "projects", kernel, EventBus())
    registry2 = build_registry(restarted)

    _as("browser:a")
    undone = registry2.call("undo", {"project": "demo", "scope": "mine"})
    assert "error" not in undone, undone
    assert _text(service, "box") == BOX_SCRIPT


def test_mine_undo_at_the_head_takes_the_restore_path(demo):
    """Decision 16 step 3: when the caller's entry IS the branch head there is
    nothing to revert around — the existing restore path runs unchanged."""
    service, registry = demo
    _as("browser:a")
    _script(registry, "box", BOX_V2)
    assert "error" not in registry.call(
        "undo", {"project": "demo", "scope": "mine"})
    assert _text(service, "box") == BOX_SCRIPT
    assert _log(service)[0]["message"].startswith("restore ")


# ------------------------------------------------- ProjectHistory.revert


def test_revert_of_a_merge_commit_reverts_against_the_first_parent(tmp_path):
    """Merges land on the undo stack (merge.py calls on_snapshot), so a
    ``mine`` undo can name a two-parent commit; git needs ``-m 1`` for those,
    and the first parent is the target branch — the side that keeps."""
    history = ProjectHistory()
    proj = tmp_path / "demo"
    (proj / "parts").mkdir(parents=True)
    (proj / "parts" / "a.py").write_text("A1\n", encoding="utf-8")
    base = history.snapshot(proj, "base")
    (proj / "parts" / "b.py").write_text("B1\n", encoding="utf-8")
    side = history.snapshot(proj, "side")
    tree = history._run(proj, "rev-parse", "HEAD^{tree}").stdout.strip()
    merge = history._run(
        proj, "commit-tree", tree, "-p", base, "-p", side, "-m", "merge feat",
    ).stdout.strip()
    history._run(proj, "reset", "--hard", merge)

    reverted = history.revert(proj, merge)
    assert reverted
    assert not (proj / "parts" / "b.py").exists()
    assert (proj / "parts" / "a.py").read_text() == "A1\n"


def test_revert_of_an_unknown_commit_raises(tmp_path):
    from agentcad.core.history import HistoryError

    history = ProjectHistory()
    proj = tmp_path / "demo"
    proj.mkdir()
    (proj / "a.txt").write_text("x\n", encoding="utf-8")
    history.snapshot(proj, "init")
    with pytest.raises(HistoryError):
        history.revert(proj, "deadbeef")
    with pytest.raises(HistoryError):
        history.revert(proj, "--help")


def test_revert_refuses_a_dirty_tree_and_keeps_the_uncommitted_work(tmp_path):
    """The one that ate somebody's editor buffer.

    ``git revert --no-commit`` REFUSES to start when the tree has local
    changes to a file it would touch — so the revert never applied anything,
    and the old cleanup path's unconditional ``reset --hard HEAD`` discarded
    the user's own uncommitted edit instead of the revert's. The user guide
    documents editing ``parts/<id>.py`` in an external editor, so a second
    client's ``undo {scope: "mine"}`` was enough to reach it. The refusal is
    now up front, it names the dirty paths, and the file is untouched.
    """
    from agentcad.core.model import ConflictError

    history = ProjectHistory()
    proj = tmp_path / "demo"
    (proj / "parts").mkdir(parents=True)
    (proj / "parts" / "a.py").write_text("line1\nline2\nline3\n", encoding="utf-8")
    history.snapshot(proj, "a v1")
    (proj / "parts" / "a.py").write_text("line1\nCHANGED\nline3\n", encoding="utf-8")
    target = history.snapshot(proj, "a v2")
    (proj / "parts" / "z.py").write_text("z\n", encoding="utf-8")
    history.snapshot(proj, "z")

    precious = "line1\nCHANGED\nline3\nPRECIOUS UNSAVED WORK\n"
    (proj / "parts" / "a.py").write_text(precious, encoding="utf-8")

    with pytest.raises(ConflictError) as excinfo:
        history.revert(proj, target)
    details = excinfo.value.details
    assert details["reason"] == "uncommitted_changes"
    assert details["commit"] == target
    assert "parts/a.py" in details["paths"]
    assert (proj / "parts" / "a.py").read_text(encoding="utf-8") == precious


def test_revert_refuses_a_dirty_tree_even_for_an_unrelated_file(tmp_path):
    """The guard is the tree, not the overlap. git would have *started* this
    revert (different file), and a mid-flight failure would then have reset
    the unrelated edit away. Refusing up front is the only state in which the
    cleanup below can be honest about what it is undoing."""
    from agentcad.core.model import ConflictError

    history = ProjectHistory()
    proj = tmp_path / "demo"
    (proj / "parts").mkdir(parents=True)
    (proj / "parts" / "a.py").write_text("A1\n", encoding="utf-8")
    (proj / "parts" / "b.py").write_text("B1\n", encoding="utf-8")
    history.snapshot(proj, "base")
    (proj / "parts" / "a.py").write_text("A2\n", encoding="utf-8")
    target = history.snapshot(proj, "edit a")
    (proj / "parts" / "z.py").write_text("z\n", encoding="utf-8")
    history.snapshot(proj, "z")

    (proj / "parts" / "b.py").write_text("B-UNSAVED\n", encoding="utf-8")
    with pytest.raises(ConflictError) as excinfo:
        history.revert(proj, target)
    assert excinfo.value.details["paths"] == ["parts/b.py"]
    assert (proj / "parts" / "b.py").read_text(encoding="utf-8") == "B-UNSAVED\n"
    assert (proj / "parts" / "a.py").read_text(encoding="utf-8") == "A2\n"


def test_revert_leaves_an_untracked_file_alone(tmp_path):
    """An untracked file is not "uncommitted work git is about to overwrite":
    the next snapshot will add it, ``reset --hard`` never deletes it, and
    blocking every undo on one would make the guard useless in a project a
    user drops a scratch file into."""
    history = ProjectHistory()
    proj = tmp_path / "demo"
    (proj / "parts").mkdir(parents=True)
    (proj / "parts" / "a.py").write_text("A1\n", encoding="utf-8")
    history.snapshot(proj, "base")
    (proj / "parts" / "a.py").write_text("A2\n", encoding="utf-8")
    target = history.snapshot(proj, "edit a")
    (proj / "parts" / "z.py").write_text("z\n", encoding="utf-8")
    history.snapshot(proj, "z")

    scratch = proj / "parts" / "scratch.txt"
    scratch.write_text("notes\n", encoding="utf-8")
    assert history.revert(proj, target)
    assert (proj / "parts" / "a.py").read_text(encoding="utf-8") == "A1\n"
    assert scratch.read_text(encoding="utf-8") == "notes\n"


def test_reverting_an_already_reverted_commit_is_refused(tmp_path):
    from agentcad.core.model import ConflictError

    history = ProjectHistory()
    proj = tmp_path / "demo"
    proj.mkdir()
    (proj / "a.txt").write_text("1\n", encoding="utf-8")
    history.snapshot(proj, "init")
    (proj / "a.txt").write_text("2\n", encoding="utf-8")
    target = history.snapshot(proj, "edit")
    (proj / "b.txt").write_text("other\n", encoding="utf-8")
    history.snapshot(proj, "unrelated")
    history.revert(proj, target)
    with pytest.raises(ConflictError) as excinfo:
        history.revert(proj, target)
    assert excinfo.value.details["reason"] == "already_reverted"
    assert history._run(proj, "status", "--porcelain").stdout.strip() == ""


def test_a_failure_after_the_patch_applied_leaves_nothing_behind(tmp_path):
    """K2: "never a partial apply" has to hold on the way OUT too.

    ``git revert --no-commit`` succeeds, and then the commit behind it fails —
    a repository hook that rejects it is the review's scenario. The inverse
    patch is applied and staged at that moment, so an error that simply
    propagated left the project mutated while ``UndoCursor`` put the entry
    back and told the caller nothing had happened.
    """
    from agentcad.core.history import HistoryError

    history = ProjectHistory()
    proj = tmp_path / "demo"
    (proj / "parts").mkdir(parents=True)
    (proj / "parts" / "a.py").write_text("A1\n", encoding="utf-8")
    history.snapshot(proj, "base")
    (proj / "parts" / "a.py").write_text("A2\n", encoding="utf-8")
    target = history.snapshot(proj, "edit a")

    before_head = history.head(proj)
    before_tree = history._run(proj, "rev-parse", "HEAD^{tree}").stdout.strip()

    hook = proj / ".history" / "hooks" / "pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)

    with pytest.raises(HistoryError):
        history.revert(proj, target)

    assert history.head(proj) == before_head
    assert history._run(
        proj, "rev-parse", "HEAD^{tree}").stdout.strip() == before_tree
    assert history._run(proj, "status", "--porcelain").stdout.strip() == ""
    assert (proj / "parts" / "a.py").read_text(encoding="utf-8") == "A2\n"


# --------------------------------------- merges: authorship and ``undo_to``


@pytest.fixture
def branched(demo):
    """'demo' plus a branch 'feat' forked from master."""
    service, registry = demo
    service.branches.create("demo", "feat")
    return service, registry


def _on(service, client: str, branch: str) -> None:
    """Put a client on a branch (identity + checkout), like test_merge.py."""
    _as(client)
    if service.branches.current("demo") != branch:
        service.branches.switch("demo", branch)


def _restarted_cursor(service):
    """A cursor with empty stacks over the same durable history — exactly what
    a server restart leaves behind, without rebuilding the whole service."""
    from agentcad.core.history import UndoCursor

    return UndoCursor(service.history, service.store, EventBus())


def test_a_merge_commit_carries_its_author_into_history(branched):
    """K7: a non-fast-forward merge writes ``Merged-by:``, not ``Client:``, so
    ``author_of`` read it as "no author at all" — ``project_history`` showed
    ``null`` for the one commit that always has a person behind it, and a
    post-restart ``scope: "mine"`` undo could never select it."""
    service, registry = branched
    _on(service, "browser:a", "feat")
    _script(registry, "box", BOX_V2)
    _on(service, "browser:b", "master")
    _script(registry, "pin", BOX_V3)          # divergence: a real two-parent

    merged = registry.call("merge_branch", {"project": "demo", "source": "feat"})
    assert "error" not in merged, merged
    assert merged["fast_forward"] is False

    top = _log(service)[0]
    assert top["message"].startswith("merge feat into master")
    assert top["author"] == "browser:b"

    # …and the restart path can therefore select it.
    cursor = _restarted_cursor(service)
    _as("browser:b")
    undone = cursor.undo("demo", scope="mine")
    assert undone["label"].startswith("merge feat into master")


def test_a_scoped_undo_of_a_fast_forward_merge_honours_undo_to(branched):
    """K5: a fast-forward moves the branch onto a commit whose first parent
    belongs to the SOURCE, which is why ``on_snapshot`` records ``undo_to``.
    The scoped path ignored it and reverted only the source tip, leaving every
    earlier merged commit standing — a half-undone merge."""
    service, registry = branched
    _on(service, "browser:a", "feat")
    _script(registry, "box", BOX_V2)          # S1
    _script(registry, "box", BOX_V3)          # S2
    _on(service, "browser:a", "master")

    merged = registry.call("merge_branch", {"project": "demo", "source": "feat"})
    assert "error" not in merged, merged
    assert merged["fast_forward"] is True

    _on(service, "browser:b", "master")
    _script(registry, "pin", BOX_V2)          # unrelated later work

    _on(service, "browser:a", "master")
    undone = registry.call("undo", {"project": "demo", "scope": "mine"})
    assert "error" not in undone, undone
    # The whole fast-forward is undone — S1 as well as S2 …
    assert _text(service, "box") == BOX_SCRIPT
    # … and B's later, unrelated edit is untouched.
    assert _text(service, "pin") == BOX_V2


def test_the_post_restart_fallback_under_mine_looks_past_the_head(demo):
    """K6: the fallback read ``log(limit=1)``, so "is the newest commit mine?"
    stood in for "do I have anything to undo?". A commits, B commits, the
    process restarts — A's commit is still reachable and still A's."""
    service, registry = demo
    _as("browser:a")
    _script(registry, "box", BOX_V2)
    a_commit = _log(service)[0]["id"]
    _as("browser:b")
    _script(registry, "pin", BOX_V3)

    cursor = _restarted_cursor(service)
    _as("browser:a")
    undone = cursor.undo("demo", scope="mine")
    assert undone["label"] == "project_changed box"
    assert _text(service, "box") == BOX_SCRIPT   # A's edit taken back …
    assert _text(service, "pin") == BOX_V3       # … B's left standing
    assert _log(service)[0]["message"].startswith(f"revert {a_commit[:8]}")
