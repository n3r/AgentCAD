"""PRD-008 slice 4: ``proposal_hunk`` anchors — the open question, answered.

A thread anchored to a diff hunk re-maps by **byte-identical hunk header
within the new generation** and orphans otherwise (design Decision 8). The
whole surface reads the *persisted* ``packet.json`` and nothing else: never
``service.packets.packet(...)``, which rebuilds geometry and can move a
proposal's state, and never the kernel.

Sections: 1. validation against the persisted packet · 2. the resolution table
· 3. the honesty guarantees (no kernel, no packet rebuild, nothing written) ·
4. the ``proposal`` list filter · 5. a real packet, built and regenerated
(``slow``).

Sections 1-4 hand-write ``packet.json`` on purpose: the contract under test is
"whatever is on disk is what we read", and a hand-written packet is the only
way to pin a *frozen* or a *rewritten* one without a merge and two rebuilds.
Section 5 proves the hand-written shape is the shape ``PacketBuilder`` writes.
"""

from __future__ import annotations

import json
import shutil

import pytest

from agentcad.core import anchors, locks
from agentcad.core.comments import CommentManager
from agentcad.core.model import ValidationError
from agentcad.core.project import ProjectStore
from agentcad.core.service import AgentCADService, EventBus
from agentcad.core.tools import build_registry

from .conftest import BOX_SCRIPT

pytestmark = [
    pytest.mark.integration,
    pytest.mark.portability,
    pytest.mark.skipif(shutil.which("git") is None, reason="git not found on PATH"),
]

HEADER_A = "@@ -1,6 +1,8 @@ def build(p):"
HEADER_B = "@@ -40,7 +42,9 @@ def build(p):"
HEADER_C = "@@ -80,3 +84,4 @@ def build(p):"


class _NoKernel:
    """A kernel client that fails the test if anything asks it to build.

    Nothing in a hunk anchor's life — validating it, listing it, resolving it —
    may reach the pool: the packet was measured once, and reading a thread is
    not a reason to measure it again.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    def request(self, op, params, timeout_s=None):
        self.calls.append(op)
        raise AssertionError(f"a proposal_hunk anchor called the kernel: {op}")


@pytest.fixture(autouse=True)
def _reset_identity():
    token = locks.client_id_var.set("browser")
    yield
    locks.client_id_var.reset(token)


@pytest.fixture
def demo(tmp_path):
    """A project, a branch and a proposal — and no geometry anywhere.

    A hunk anchor never touches a part, so this fixture builds none; the
    kernel is the one that raises if anything tries.
    """
    service = AgentCADService(tmp_path / "projects", _NoKernel(), EventBus())
    registry = build_registry(service)
    assert getattr(service, "proposals", None) is not None
    assert "error" not in registry.call("create_project", {"name": "demo"})
    service.branches.create("demo", "feat")
    created = registry.call("proposal_create",
                            {"project": "demo", "source": "feat",
                             "title": "Thinner wall"})
    assert "error" not in created, created
    return service, registry, created["proposal"]["id"]


def _packet(service, pid: str, *, generation: str = "aaaa1111",
            frozen: bool = False, files: dict | None = None,
            truncated: bool = False) -> dict:
    """Persist a ``packet.json`` shaped exactly like ``PacketBuilder`` writes
    one (section 5 pins the shape against the real builder)."""
    files = {"parts/box.py": [HEADER_A, HEADER_B]} if files is None else files
    parts = []
    for path, headers in files.items():
        parts.append({
            "part": path[len("parts/"):-len(".py")],
            "change": "modified",
            "changed_by": ["script"],
            "script_diff": {
                "path": path,
                "unified": None if truncated else "".join(
                    f"{header}\n" for header in headers),
                "added_lines": len(headers),
                "removed_lines": 0,
                "truncated": truncated,
                "hunks": [{"index": index, "header": header,
                           "old_start": 1 + index, "new_start": 1 + index}
                          for index, header in enumerate(headers)],
            },
        })
    packet = {
        "proposal": pid, "generation": generation, "ok": True, "stale": False,
        "frozen": frozen, "generated": "2026-08-11T09:00:00Z",
        "source": "feat", "target": "master", "parts": parts,
    }
    ProjectStore._atomic_write(
        service.proposals.store.packet_path("demo", pid),
        json.dumps(packet, indent=2).encode())
    return packet


def _anchor(pid: str, **fields) -> dict:
    anchor = {"kind": "proposal_hunk", "proposal": pid,
              "file": "parts/box.py", "hunk": 1}
    anchor.update(fields)
    return anchor


def _open(service, pid: str, **fields) -> dict:
    return CommentManager(service).create(
        "demo", _anchor(pid, **fields), "this hunk changes the wall twice")


# --------------------------------- 1. validation against the persisted packet


def test_a_hunk_anchor_stores_the_header_and_the_generation(demo):
    """The evidence Decision 8 re-maps by, captured at creation and stamped by
    the server — a header a client asserts is not evidence of anything."""
    service, _registry, pid = demo
    _packet(service, pid)

    thread = _open(service, pid)

    anchor = thread["anchor"]
    assert set(anchor) == {"kind", "proposal", "file", "hunk", "hunk_header",
                           "generation", "branch", "head"}
    assert anchor["proposal"] == pid
    assert anchor["hunk"] == 1
    assert anchor["hunk_header"] == HEADER_B
    assert anchor["generation"] == "aaaa1111"
    assert thread["resolution"]["status"] == "ok"
    assert service.kernel.calls == []


def test_a_caller_supplied_header_or_generation_is_never_taken(demo):
    service, _registry, pid = demo
    _packet(service, pid)

    thread = _open(service, pid, hunk_header="@@ made up @@",
                   generation="not-a-generation")

    assert thread["anchor"]["hunk_header"] == HEADER_B
    assert thread["anchor"]["generation"] == "aaaa1111"


def test_an_unknown_proposal_is_a_validation_error(demo):
    service, _registry, _pid = demo

    with pytest.raises(ValidationError) as exc:
        _open(service, "9")
    assert "9" in exc.value.message
    assert exc.value.details.get("proposal") == "9"


def test_a_missing_packet_says_to_open_the_packet_first(demo):
    """Never a rebuild: the anchor names a measurement, so the measurement has
    to exist before a thread can point at it."""
    service, _registry, pid = demo

    with pytest.raises(ValidationError) as exc:
        _open(service, pid)
    assert "packet" in exc.value.message
    assert "proposal_packet" in (exc.value.details.get("hint") or "")
    assert service.kernel.calls == []


def test_an_unknown_file_lists_the_files_the_packet_diffs(demo):
    service, _registry, pid = demo
    _packet(service, pid)

    with pytest.raises(ValidationError) as exc:
        _open(service, pid, file="parts/nope.py")
    assert exc.value.details.get("files") == ["parts/box.py"]


def test_a_hunk_index_out_of_range_names_the_count(demo):
    service, _registry, pid = demo
    _packet(service, pid)

    with pytest.raises(ValidationError) as exc:
        _open(service, pid, hunk=2)
    assert exc.value.details.get("hunks") == 2
    assert exc.value.details.get("hunk") == 2

    for bad in (-1, "1", True, None):
        with pytest.raises(ValidationError) as exc:
            _open(service, pid, hunk=bad)
        assert "hunk" in exc.value.message


def test_a_truncated_diff_is_refused_by_name(demo):
    """A diff too large to keep has no text to review, so a line-level thread
    on it would point at something nobody can read."""
    service, _registry, pid = demo
    _packet(service, pid, truncated=True)

    with pytest.raises(ValidationError) as exc:
        _open(service, pid)
    assert "truncat" in exc.value.message
    assert exc.value.details.get("truncated") is True


# ------------------------------------------------------ 2. the resolution table


def _resolve(service, anchor: dict) -> dict:
    return anchors.resolve(service, "demo", anchor)


def test_the_same_generation_is_ok(demo):
    service, _registry, pid = demo
    _packet(service, pid)
    anchor = _open(service, pid)["anchor"]

    result = _resolve(service, anchor)
    assert result["status"] == "ok"
    assert result["hunk"] == 1
    assert result["generation"] == "aaaa1111"


def test_a_regenerated_packet_re_maps_by_a_byte_identical_header(demo):
    """Decision 8: the header is the identity, and the index is not. The new
    packet grew a hunk above this one, so the index moved and the thread
    followed it."""
    service, _registry, pid = demo
    _packet(service, pid)
    anchor = _open(service, pid)["anchor"]

    _packet(service, pid, generation="bbbb2222",
            files={"parts/box.py": [HEADER_C, HEADER_A, HEADER_B]})

    result = _resolve(service, anchor)
    assert result["status"] == "moved"
    assert result["reason"] == "hunk_remapped_by_header"
    assert result["hunk"] == 2
    assert result["generation"] == "bbbb2222"


def test_a_regenerated_packet_is_moved_even_at_the_same_index(demo):
    """A new generation is a new measurement of a different diff; calling that
    'ok' would claim the reviewed text is unchanged when nobody looked."""
    service, _registry, pid = demo
    _packet(service, pid)
    anchor = _open(service, pid)["anchor"]

    _packet(service, pid, generation="bbbb2222")

    result = _resolve(service, anchor)
    assert result["status"] == "moved"
    assert result["hunk"] == 1


def test_a_rewritten_hunk_is_orphaned_never_re_pointed(demo):
    service, _registry, pid = demo
    _packet(service, pid)
    anchor = _open(service, pid)["anchor"]

    _packet(service, pid, generation="bbbb2222",
            files={"parts/box.py":
                   [HEADER_A, "@@ -40,9 +42,13 @@ def build(p):"]})

    result = _resolve(service, anchor)
    assert result["status"] == "orphaned"
    assert result["reason"] == "hunk_regenerated"
    assert result["hint"]
    assert "hunk" not in result  # no guess, not even a plausible one


def test_a_header_that_now_occurs_twice_is_orphaned_not_guessed(demo):
    """Two candidates are indistinguishable, and pointing at the wrong one is
    worse than pointing at nothing."""
    service, _registry, pid = demo
    _packet(service, pid)
    anchor = _open(service, pid)["anchor"]

    _packet(service, pid, generation="bbbb2222",
            files={"parts/box.py": [HEADER_B, HEADER_A, HEADER_B]})

    result = _resolve(service, anchor)
    assert (result["status"], result["reason"]) == ("orphaned",
                                                    "hunk_regenerated")
    assert "twice" in result["hint"] or "2" in result["hint"]


def test_a_frozen_packet_is_unverified_even_at_the_same_generation(demo):
    """The diff a frozen packet describes is history now, and the thread is a
    record of a review of exactly that. 'ok' would invite a UI to open a live
    diff that no longer exists."""
    service, _registry, pid = demo
    _packet(service, pid)
    anchor = _open(service, pid)["anchor"]

    _packet(service, pid, frozen=True)

    result = _resolve(service, anchor)
    assert result["status"] == "unverified"
    assert result["reason"] == "packet_frozen"
    assert result["hint"]


def test_a_terminal_proposal_freezes_its_packet_too(demo):
    """``PacketBuilder`` refuses to re-measure a merged or closed proposal, so
    the packet on disk is pinned whether or not the flag was written."""
    service, _registry, pid = demo
    _packet(service, pid)
    anchor = _open(service, pid)["anchor"]

    proposal = service.proposals.store.load("demo", pid)
    proposal["state"] = "merged"
    service.proposals.store.save("demo", proposal)

    result = _resolve(service, anchor)
    assert (result["status"], result["reason"]) == ("unverified",
                                                    "packet_frozen")
    assert "merged" in result["hint"]


def test_a_packet_that_is_gone_is_orphaned(demo):
    service, _registry, pid = demo
    _packet(service, pid)
    anchor = _open(service, pid)["anchor"]

    service.proposals.store.packet_path("demo", pid).unlink()

    result = _resolve(service, anchor)
    assert (result["status"], result["reason"]) == ("orphaned",
                                                    "packet_missing")


def test_a_file_that_left_the_diff_is_orphaned(demo):
    service, _registry, pid = demo
    _packet(service, pid)
    anchor = _open(service, pid)["anchor"]

    _packet(service, pid, generation="bbbb2222",
            files={"parts/other.py": [HEADER_A]})

    result = _resolve(service, anchor)
    assert (result["status"], result["reason"]) == ("orphaned",
                                                    "file_not_in_diff")


def test_a_proposal_that_is_gone_is_orphaned(demo):
    service, _registry, pid = demo
    _packet(service, pid)
    anchor = _open(service, pid)["anchor"]

    shutil.rmtree(service.proposals.store.dir_of("demo") / pid)

    result = _resolve(service, anchor)
    assert (result["status"], result["reason"]) == ("orphaned",
                                                    "proposal_removed")


def test_without_the_proposals_pack_a_hunk_anchor_is_unverified(demo):
    """No git, no proposals — and "we did not look" is not "the hunk is
    gone"."""
    service, _registry, pid = demo
    _packet(service, pid)
    anchor = _open(service, pid)["anchor"]
    del service.proposals

    result = _resolve(service, anchor)
    assert (result["status"], result["reason"]) == ("unverified",
                                                    "proposals_unavailable")
    assert result["hint"]


def test_an_anchor_without_a_stored_header_is_unverified(demo):
    service, _registry, pid = demo
    _packet(service, pid)
    anchor = _open(service, pid)["anchor"]
    anchor.pop("hunk_header")
    _packet(service, pid, generation="bbbb2222")

    result = _resolve(service, anchor)
    assert (result["status"], result["reason"]) == ("unverified", "no_header")


# ------------------------------------------------- 3. the honesty guarantees


def test_resolution_never_rebuilds_the_packet(demo, monkeypatch):
    """``service.packets.packet(...)`` builds geometry and can move a
    proposal's state — reading a comment must never do either."""
    service, registry, pid = demo
    _packet(service, pid)

    def _explode(*args, **kwargs):
        raise AssertionError("resolution rebuilt the review packet")

    monkeypatch.setattr(service.packets, "packet", _explode)
    thread = _open(service, pid)

    listed = CommentManager(service).list("demo")
    assert listed["threads"][0]["resolution"]["status"] == "ok"
    assert thread["resolution"]["status"] == "ok"
    assert service.kernel.calls == []


def test_reading_a_hunk_thread_writes_nothing_to_the_proposal(demo):
    """The packet's ``generated`` stamp, its bytes and the proposal document
    are all evidence: a read that moves them is not a read."""
    service, _registry, pid = demo
    _packet(service, pid)
    _open(service, pid)
    packet_path = service.proposals.store.packet_path("demo", pid)
    before = (packet_path.read_bytes(),
              json.dumps(service.proposals.store.load("demo", pid),
                         sort_keys=True))

    for _ in range(3):
        CommentManager(service).list("demo")

    after = (packet_path.read_bytes(),
             json.dumps(service.proposals.store.load("demo", pid),
                        sort_keys=True))
    assert after == before
    assert json.loads(packet_path.read_text())["generated"] == \
        "2026-08-11T09:00:00Z"


def test_a_hunk_thread_survives_its_own_orphaning(demo):
    """FR3: the thread stays readable, listable and resolvable, and keeps its
    last-known anchor."""
    service, _registry, pid = demo
    _packet(service, pid)
    manager = CommentManager(service)
    manager.create("demo", _anchor(pid), "look at this hunk")
    _packet(service, pid, generation="bbbb2222",
            files={"parts/box.py": [HEADER_A]})

    listed = manager.list("demo")
    assert listed["counts"]["orphaned"] == 1
    assert listed["threads"][0]["anchor"]["hunk_header"] == HEADER_B
    assert manager.resolve("demo", "1")["state"] == "resolved"


# ---------------------------------------------------- 4. the proposal filter


def test_list_comments_filters_to_one_proposals_threads(demo):
    """So the proposals UI fetches exactly its own threads in one call."""
    service, registry, pid = demo
    _packet(service, pid)
    manager = CommentManager(service)
    manager.create("demo", _anchor(pid), "hunk one")
    manager.create("demo", _anchor(pid, hunk=0), "hunk zero")
    # A part written straight into the manifest: this module never builds.
    service.store.add_part("demo", "box", "Box", "al6061", BOX_SCRIPT)
    manager.create("demo", {"kind": "part", "part": "box"}, "the whole part")

    listed = registry.call("list_comments",
                           {"project": "demo", "kind": "proposal_hunk",
                            "proposal": pid})
    assert [t["id"] for t in listed["threads"]] == ["1", "2"]
    # ``counts`` still describes the WHOLE project, filter or no filter.
    assert listed["counts"]["open"] == 3

    assert registry.call("list_comments",
                         {"project": "demo", "proposal": "9"})["threads"] == []


def test_the_proposal_filter_reaches_the_route(demo, tmp_path):
    from fastapi.testclient import TestClient

    from agentcad.server.app import create_app

    service, registry, pid = demo
    _packet(service, pid)
    CommentManager(service).create("demo", _anchor(pid), "hunk one")
    http = TestClient(create_app(service, registry,
                                 extra_allowed_hosts={"testserver"}),
                      base_url="http://127.0.0.1")

    listed = http.get(f"/api/projects/demo/comments?proposal={pid}")
    assert listed.status_code == 200, listed.text
    assert [t["id"] for t in listed.json()["threads"]] == ["1"]
    assert http.get(
        "/api/projects/demo/comments?proposal=9").json()["threads"] == []


# -------------------------------------------- 5. a real packet, built for real


@pytest.mark.slow
def test_a_real_packet_carries_the_shape_this_module_hand_writes(kernel,
                                                                 tmp_path):
    """The one case that runs ``PacketBuilder``: it pins that a real
    ``packet.json`` names its hunks the way sections 1-4 assume, and that a
    regeneration after an unrelated change re-maps the thread by header."""
    service = AgentCADService(tmp_path / "projects", kernel, EventBus())
    registry = build_registry(service)
    assert "error" not in registry.call("create_project", {"name": "demo"})
    assert "error" not in registry.call(
        "create_part",
        {"project": "demo", "part_id": "box", "script": BOX_SCRIPT})
    service.branches.create("demo", "feat")
    service.branches.switch("demo", "feat")
    wider = BOX_SCRIPT.replace("Box(p.size, p.size, p.size)",
                               "Box(p.size, p.size, p.size * 2)")
    assert "error" not in registry.call(
        "update_part_script",
        {"project": "demo", "part_id": "box", "script": wider})
    created = registry.call("proposal_create",
                            {"project": "demo", "source": "feat",
                             "title": "Twice as tall"})
    assert "error" not in created, created
    pid = created["proposal"]["id"]

    packet = service.packets.packet("demo", pid)
    diff = packet["parts"][0]["script_diff"]
    assert diff["path"] == "parts/box.py"
    assert diff["hunks"] and diff["hunks"][0]["header"].startswith("@@")
    header = diff["hunks"][0]["header"]

    manager = CommentManager(service)
    thread = manager.create("demo", _anchor(pid, hunk=0), "why twice as tall?")
    assert thread["anchor"]["hunk_header"] == header
    assert thread["anchor"]["generation"] == packet["generation"]
    assert thread["resolution"]["status"] == "ok"

    # An unrelated change on the source branch: a new part, so the hunk this
    # thread points at is measured again, byte for byte, in a new generation.
    assert "error" not in registry.call(
        "create_part",
        {"project": "demo", "part_id": "pin", "script": BOX_SCRIPT})
    regenerated = service.packets.packet("demo", pid, regenerate=True)
    assert regenerated["generation"] != packet["generation"]

    result = manager.get("demo", thread["id"])["resolution"]
    assert result["status"] == "moved"
    assert result["reason"] == "hunk_remapped_by_header"
    assert result["hunk"] == 0
