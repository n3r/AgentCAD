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
