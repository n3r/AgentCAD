"""PRD-008 slice 1: the comment store and the thread lifecycle.

The storage and lifecycle semantics only — anchor *resolution* is slice 2,
the tool/route surface is slice 3, and mentions are slice 5. Everything here
is domain logic over files, so the module carries no marker; only section 8
(``project_restore`` and git visibility) needs git and is marked there.

Sections: 1. store and layout · 2. ids and the index · 3. the audit log ·
4. anchors · 5. attachments (AC9) · 6. the lifecycle · 7. listing ·
8. durability (AC8).
"""

from __future__ import annotations

import json
import shutil

import pytest

from agentcad.core import locks
from agentcad.core.comments import (
    ANCHOR_KINDS,
    MAX_ATTACHMENTS,
    MAX_BODY_BYTES,
    STATES,
    CommentManager,
    CommentStore,
)
from agentcad.core.model import NotFoundError, ValidationError
from agentcad.core.service import AgentCADService, EventBus
from agentcad.core.tools import build_registry

from .conftest import BOX_SCRIPT

_GIT = [
    pytest.mark.integration,
    pytest.mark.portability,
    pytest.mark.skipif(shutil.which("git") is None, reason="git not found on PATH"),
]


def _needs_git(fn):
    """Section 8's two cases: git-driven, and OS-sensitive with it."""
    for mark in _GIT:
        fn = mark(fn)
    return fn


@pytest.fixture(autouse=True)
def _reset_identity():
    """Identity is a ContextVar: rebind it per test so one test's client can
    never leak into the next."""
    token = locks.client_id_var.set("browser")
    yield
    locks.client_id_var.reset(token)


@pytest.fixture
def demo(kernel, tmp_path):
    """A real service (NOT make_test_service — AC8 needs the snapshot hook)
    with one buildable part and one assembly instance."""
    service = AgentCADService(tmp_path / "projects", kernel, EventBus())
    registry = build_registry(service)
    assert "error" not in registry.call("create_project", {"name": "demo"})
    created = registry.call(
        "create_part", {"project": "demo", "part_id": "box", "script": BOX_SCRIPT}
    )
    assert "error" not in created, created
    assert "error" not in registry.call(
        "set_assembly",
        {"project": "demo", "instances": [{"id": "box_1", "part": "box"}]},
    )
    return service, registry, CommentManager(service)


def _open(manager, **anchor) -> dict:
    payload = {"kind": "part", "part": "box"}
    payload.update(anchor)
    return manager.create("demo", payload, "this boss needs a fillet")


# ----------------------------------------------- 1. store and layout


def test_the_store_lives_in_the_prd002_sidecar(demo):
    service, _registry, manager = demo
    canonical = service.store.canonical_path_of("demo")

    assert manager.store.dir_of("demo") == (
        canonical / ".history" / "agentcad" / "comments"
    )
    thread = _open(manager)
    assert thread["id"] == "1"
    assert (manager.store.dir_of("demo") / "1" / "thread.json").is_file()
    assert (manager.store.dir_of("demo") / "1" / "audit.jsonl").is_file()


def test_a_thread_round_trips_through_the_store(demo):
    _service, _registry, manager = demo
    thread = _open(manager)

    stored = manager.store.load("demo", "1")
    assert stored["state"] == "open"
    assert stored["project"] == "demo"
    assert stored["author"] == "browser"
    assert stored["author_kind"] == "human"
    assert stored["resolved"] is None
    assert stored["anchor"]["kind"] == "part"
    assert [c["body"] for c in stored["comments"]] == ["this boss needs a fillet"]
    assert manager.get("demo", "1")["id"] == thread["id"]
    assert manager.store.load("demo", "1") == stored  # a read mutates nothing


def test_an_unknown_thread_id_is_a_notfound(demo):
    _service, _registry, manager = demo
    _open(manager)
    for bad in ("2", "0", "007", "../../etc", "1/../1", "abc", 1):
        with pytest.raises(NotFoundError):
            manager.get("demo", bad)


# ------------------------------------------------ 2. ids and the index


def test_ids_are_decimal_strings_from_one_and_are_never_reused(demo):
    _service, _registry, manager = demo
    ids = [_open(manager)["id"] for _ in range(3)]
    assert ids == ["1", "2", "3"]

    # A directory removed by hand must not hand its id to the next thread.
    shutil.rmtree(manager.store.dir_of("demo") / "3")
    assert _open(manager)["id"] == "4"


def test_an_id_is_never_reused_even_when_the_index_is_lost(demo):
    """The persisted high-water mark is the only thing that remembers the id
    of a thread that was deleted by hand (PRD-002's C8 fix)."""
    _service, _registry, manager = demo
    for _ in range(3):
        _open(manager)
    base = manager.store.dir_of("demo")
    shutil.rmtree(base / "3")
    (base / "index.json").unlink()

    assert _open(manager)["id"] == "4"


def test_a_missing_index_is_rebuilt_from_the_directories(demo):
    _service, _registry, manager = demo
    for _ in range(2):
        _open(manager)
    index = manager.store.dir_of("demo") / "index.json"
    index.unlink()

    assert [t["id"] for t in manager.store.list("demo")] == ["1", "2"]
    rebuilt = json.loads(index.read_text(encoding="utf-8"))
    assert [row["id"] for row in rebuilt["threads"]] == ["1", "2"]
    assert rebuilt["next_id"] == 3


def test_a_corrupt_index_is_rebuilt_never_raised(demo):
    _service, _registry, manager = demo
    _open(manager)
    index = manager.store.dir_of("demo") / "index.json"
    index.write_text("{not json", encoding="utf-8")

    assert [t["id"] for t in manager.store.list("demo")] == ["1"]
    assert json.loads(index.read_text(encoding="utf-8"))["next_id"] == 2


# ---------------------------------------------------- 3. the audit log


def test_the_audit_log_is_append_only(demo):
    """Three mutations, three byte-prefix-stable snapshots: an entry is
    appended and nothing already written is ever rewritten."""
    _service, _registry, manager = demo
    _open(manager)
    path = manager.store.dir_of("demo") / "1" / "audit.jsonl"

    snapshots = [path.read_bytes()]
    manager.reply("demo", "1", "on it")
    snapshots.append(path.read_bytes())
    manager.resolve("demo", "1")
    snapshots.append(path.read_bytes())
    manager.reopen("demo", "1")
    snapshots.append(path.read_bytes())

    for earlier, later in zip(snapshots, snapshots[1:]):
        assert later.startswith(earlier)
        assert len(later) > len(earlier)
    assert [e["action"] for e in manager.store.audit("demo", "1")] == [
        "created", "replied", "resolved", "reopened",
    ]
    assert [e["seq"] for e in manager.store.audit("demo", "1")] == [1, 2, 3, 4]


def test_every_action_is_attributed_with_actor_and_actor_kind(demo):
    _service, _registry, manager = demo
    locks.set_client_id("browser:7f3a")
    _open(manager)
    locks.set_client_id("chat:main")
    thread = manager.reply("demo", "1", "fixed it")

    assert [(c["author"], c["author_kind"]) for c in thread["comments"]] == [
        ("browser:7f3a", "human"), ("chat:main", "agent"),
    ]
    audit = manager.store.audit("demo", "1")
    assert [(e["actor"], e["actor_kind"]) for e in audit] == [
        ("browser:7f3a", "human"), ("chat:main", "agent"),
    ]


# --------------------------------------------------------- 4. anchors


def test_part_param_and_instance_anchors_validate_against_the_manifest(demo):
    _service, _registry, manager = demo
    kinds = [
        {"kind": "part", "part": "box"},
        {"kind": "param", "part": "box", "param": "size"},
        {"kind": "instance", "instance": "box_1"},
    ]
    for anchor in kinds:
        thread = manager.create("demo", anchor, "look here")
        for key, value in anchor.items():
            assert thread["anchor"][key] == value


def test_an_unknown_target_is_a_validation_error_carrying_the_known_set(demo):
    _service, _registry, manager = demo
    cases = [
        ({"kind": "part", "part": "nope"}, "parts"),
        ({"kind": "param", "part": "box", "param": "nope"}, "params"),
        ({"kind": "instance", "instance": "nope"}, "instances"),
    ]
    for anchor, known in cases:
        with pytest.raises(ValidationError) as exc:
            manager.create("demo", anchor, "look here")
        assert exc.value.details.get(known), exc.value.details
    with pytest.raises(ValidationError):
        manager.create("demo", {"kind": "param", "part": "nope",
                                "param": "size"}, "x")


def test_an_unknown_or_unsupported_kind_is_a_validation_error(demo):
    """Every kind in ``ANCHOR_KINDS`` now has a validator (``face`` and
    ``script_range`` with slice 2's ``core/anchors.py``, ``proposal_hunk`` with
    slice 4's; ``tests/test_anchors.py`` and
    ``tests/test_comments_proposals.py`` own their rules), so what is left
    here is the vocabulary itself: an anchor that is not one of the six, or is
    not an object at all."""
    _service, _registry, manager = demo
    for anchor in (
        {"kind": "nonsense", "part": "box"},
        {"part": "box"},
        "part",
    ):
        with pytest.raises(ValidationError) as exc:
            manager.create("demo", anchor, "look here")
        assert "kind" in exc.value.message or "kind" in (exc.value.details or {})
    assert set(ANCHOR_KINDS) == {
        "part", "face", "param", "script_range", "instance", "proposal_hunk",
    }


def test_an_anchor_records_its_branch_and_head_and_rejects_stray_keys(demo):
    service, _registry, manager = demo
    thread = _open(manager)

    anchor = thread["anchor"]
    assert set(anchor) == {"kind", "part", "branch", "head"}
    if getattr(service, "branches", None) is not None:
        assert anchor["branch"] == service.branches.current("demo")
    with pytest.raises(ValidationError):
        manager.create("demo", {"kind": "part", "part": "box", "face_index": 2},
                       "look here")


def test_a_body_must_be_a_non_empty_string_within_the_cap(demo):
    _service, _registry, manager = demo
    for body in ("", "   ", None, 3):
        with pytest.raises(ValidationError):
            manager.create("demo", {"kind": "part", "part": "box"}, body)
    with pytest.raises(ValidationError):
        manager.create("demo", {"kind": "part", "part": "box"},
                       "x" * (MAX_BODY_BYTES + 1))
    thread = manager.create("demo", {"kind": "part", "part": "box"},
                            "x" * MAX_BODY_BYTES)
    assert len(thread["comments"][0]["body"]) == MAX_BODY_BYTES


# ------------------------------------------------- 5. attachments (AC9)


def _render(service, name: str = "box_iso.png"):
    path = service.store.exports_dir("demo") / "renders" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG")
    return path


def test_an_attachment_inside_exports_is_accepted_by_either_spelling(demo):
    service, _registry, manager = demo
    absolute = _render(service)
    for value in (str(absolute), "exports/renders/box_iso.png"):
        thread = manager.create("demo", {"kind": "part", "part": "box"},
                                "see the render", attachments=[value])
        assert thread["comments"][0]["attachments"] == [
            {"path": "exports/renders/box_iso.png", "available": True}
        ]
        assert manager.store.load("demo", thread["id"])["comments"][0][
            "attachments"
        ] == ["exports/renders/box_iso.png"]


def test_attachments_outside_exports_are_refused(demo):
    """AC9: no path disclosure through comments."""
    service, _registry, manager = demo
    outside = service.store.canonical_path_of("demo") / "project.json"
    link = service.store.exports_dir("demo") / "escape.png"
    link.symlink_to(outside)

    for value in (
        "../../etc/passwd",
        "exports/../project.json",
        str(outside),
        "/etc/passwd",
        "exports/escape.png",       # a symlink pointing out of the tree
        "parts/box.py",
        "exports/missing.png",      # must exist at creation
        42,
    ):
        with pytest.raises(ValidationError):
            manager.create("demo", {"kind": "part", "part": "box"},
                           "see this", attachments=[value])


def test_at_most_max_attachments_per_comment(demo):
    service, _registry, manager = demo
    paths = [f"exports/renders/r{i}.png" for i in range(MAX_ATTACHMENTS + 1)]
    for name in paths:
        _render(service, name.rsplit("/", 1)[1])

    assert manager.create("demo", {"kind": "part", "part": "box"}, "ok",
                          attachments=paths[:MAX_ATTACHMENTS])
    with pytest.raises(ValidationError):
        manager.create("demo", {"kind": "part", "part": "box"}, "too many",
                       attachments=paths)


def test_an_attachment_that_disappears_reads_back_as_unavailable(demo):
    service, _registry, manager = demo
    path = _render(service)
    manager.create("demo", {"kind": "part", "part": "box"}, "see the render",
                   attachments=[str(path)])
    path.unlink()

    attachments = manager.get("demo", "1")["comments"][0]["attachments"]
    assert attachments == [
        {"path": "exports/renders/box_iso.png", "available": False}
    ]


# --------------------------------------------------- 6. the lifecycle


def test_a_reply_appends_a_sequential_comment_and_bumps_updated(demo):
    _service, _registry, manager = demo
    _open(manager)
    thread = manager.reply("demo", "1", "on it")
    thread = manager.reply("demo", "1", "done")

    assert [c["id"] for c in thread["comments"]] == ["1", "2", "3"]
    assert thread["updated"] >= thread["created"]
    assert thread["state"] == "open"
    assert [e["action"] for e in manager.store.audit("demo", "1")][-2:] == [
        "replied", "replied",
    ]


def test_resolve_and_reopen_record_the_actor_and_are_idempotent(demo):
    _service, _registry, manager = demo
    _open(manager)
    locks.set_client_id("chat:main")

    resolved = manager.resolve("demo", "1")
    assert resolved["state"] == "resolved"
    assert resolved["resolved"]["actor"] == "chat:main"
    assert resolved["resolved"]["actor_kind"] == "agent"
    assert resolved["resolved"]["ts"]
    again = manager.resolve("demo", "1")
    assert again == resolved  # idempotent: nothing recorded, nothing changed

    reopened = manager.reopen("demo", "1")
    assert reopened["state"] == "open"
    assert reopened["resolved"] is None
    assert manager.reopen("demo", "1") == reopened
    assert [e["action"] for e in manager.store.audit("demo", "1")] == [
        "created", "resolved", "reopened",
    ]
    assert set(STATES) == {"open", "resolved"}


def test_a_resolved_thread_still_takes_replies(demo):
    _service, _registry, manager = demo
    _open(manager)
    manager.resolve("demo", "1")
    thread = manager.reply("demo", "1", "one more thing")

    assert thread["state"] == "resolved"
    assert len(thread["comments"]) == 2


def test_the_root_comment_cannot_be_deleted(demo):
    _service, _registry, manager = demo
    _open(manager)
    with pytest.raises(ValidationError):
        manager.delete_comment("demo", "1", "1")
    assert manager.get("demo", "1")["comments"][0]["deleted"] is False


def test_only_the_author_may_edit_or_delete_a_comment(demo):
    _service, _registry, manager = demo
    _open(manager)
    locks.set_client_id("chat:main")
    manager.reply("demo", "1", "on it")

    locks.set_client_id("browser")
    for call in (
        lambda: manager.edit_comment("demo", "1", "2", "not my words"),
        lambda: manager.delete_comment("demo", "1", "2"),
    ):
        with pytest.raises(ValidationError) as exc:
            call()
        assert exc.value.details.get("author") == "chat:main"


def test_a_delete_leaves_a_tombstone_and_an_audit_line(demo):
    _service, _registry, manager = demo
    _open(manager)
    manager.reply("demo", "1", "never mind")
    thread = manager.delete_comment("demo", "1", "2")

    tombstone = thread["comments"][1]
    assert tombstone["deleted"] is True
    assert tombstone["body"] is None
    assert tombstone["author"] == "browser"  # who is preserved, what is not
    entry = manager.store.audit("demo", "1")[-1]
    assert entry["action"] == "comment_deleted"
    assert entry["details"]["comment"] == "2"
    with pytest.raises(ValidationError):
        manager.edit_comment("demo", "1", "2", "back again")


def test_an_edit_records_the_previous_sha256_not_the_previous_text(demo):
    _service, _registry, manager = demo
    _open(manager)
    manager.reply("demo", "1", "wrong wall")
    thread = manager.edit_comment("demo", "1", "2", "wrong fillet")

    assert thread["comments"][1]["body"] == "wrong fillet"
    assert thread["comments"][1]["edited"]
    entry = manager.store.audit("demo", "1")[-1]
    assert entry["action"] == "comment_edited"
    assert len(entry["details"]["previous_sha256"]) == 64
    assert "wrong wall" not in json.dumps(entry)
    with pytest.raises(NotFoundError):
        manager.edit_comment("demo", "1", "9", "nobody")


# --------------------------------------------------------- 7. listing


def test_list_filters_and_counts(demo):
    _service, _registry, manager = demo
    manager.create("demo", {"kind": "part", "part": "box"}, "a")
    manager.create("demo", {"kind": "param", "part": "box", "param": "size"}, "b")
    manager.create("demo", {"kind": "instance", "instance": "box_1"}, "c")
    manager.resolve("demo", "3")

    everything = manager.list("demo")
    assert [t["id"] for t in everything["threads"]] == ["1", "2", "3"]
    # ``orphaned`` joins the counts once slice 2's resolution runs; it counts
    # the whole project, like the other two.
    assert everything["counts"] == {"open": 2, "resolved": 1, "orphaned": 0}
    assert [t["id"] for t in manager.list("demo", state="open")["threads"]] == [
        "1", "2",
    ]
    assert [t["id"] for t in manager.list("demo", kind="param")["threads"]] == ["2"]
    assert [t["id"] for t in manager.list("demo", part_id="box")["threads"]] == [
        "1", "2",
    ]
    assert manager.list("demo", part_id="nope")["threads"] == []
    # Counts describe the whole project, not the filtered page.
    assert manager.list("demo", state="open")["counts"] == everything["counts"]
    with pytest.raises(ValidationError):
        manager.list("demo", state="closed")
    with pytest.raises(ValidationError):
        manager.list("demo", kind="nonsense")


def test_listing_an_empty_project_is_empty_not_an_error(demo):
    _service, _registry, manager = demo
    assert manager.list("demo") == {
        "threads": [], "counts": {"open": 0, "resolved": 0, "orphaned": 0}}


# --------------------------------------------- 8. durability (AC8)


@_needs_git
def test_threads_survive_project_restore(demo):
    """AC8, true by construction: the store is inside GIT_DIR, and restore is
    ``git checkout <commit> -- .`` in a working tree."""
    service, registry, manager = demo
    history = registry.call("project_history", {"project": "demo"})
    assert history["available"], history
    earliest = history["history"][-1]["id"]

    thread = _open(manager)
    manager.reply("demo", "1", "on it")
    before = manager.store.load("demo", "1")

    assert "error" not in registry.call(
        "update_part_script",
        {"project": "demo", "part_id": "box",
         "script": BOX_SCRIPT.replace("p.size, p.size, p.size",
                                      "p.size, p.size, p.size * 2")},
    )
    restored = registry.call("project_restore",
                             {"project": "demo", "commit": earliest})
    assert "error" not in restored, restored

    assert [t["id"] for t in manager.list("demo")["threads"]] == [thread["id"]]
    assert manager.store.load("demo", "1") == before
    assert len(manager.store.audit("demo", "1")) == 2
    assert service.store.read_script("demo", "box") == BOX_SCRIPT


@_needs_git
def test_a_thread_is_invisible_to_git(demo):
    """Workflow metadata is not model state: nothing a thread writes can ever
    be staged by ``git add -A``."""
    service, _registry, manager = demo
    _open(manager)
    manager.reply("demo", "1", "on it")

    path = service.store.canonical_path_of("demo")
    status = service.history._run(path, "status", "--porcelain", check=False)
    assert status.returncode == 0, status.stderr
    assert status.stdout.strip() == ""


def test_the_store_needs_no_service_and_no_git(kernel, tmp_path):
    """A CommentStore is files and nothing else — it takes a ProjectStore."""
    from .conftest import make_test_service

    service = make_test_service(tmp_path / "projects", kernel)
    service.store.create("plain")
    store = CommentStore(service.store)
    store.save("plain", {"id": "1", "project": "plain", "state": "open",
                         "comments": []})

    assert store.load("plain", "1")["state"] == "open"
    assert [t["id"] for t in store.list("plain")] == ["1"]
    assert store.allocate_id("plain") == "2"
