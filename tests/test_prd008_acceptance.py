"""PRD-008 acceptance criteria — one named test per AC (slice 11).

The mechanics are covered in depth by ``tests/test_comments.py`` (the store and
the lifecycle), ``tests/test_anchors.py`` / ``tests/test_anchors_kernel.py``
(resolution, with and without geometry), ``tests/test_comments_api.py`` (the
five tools, the eight routes, ``comment_changed``),
``tests/test_comments_proposals.py`` (hunk anchors),
``tests/test_comments_notifications.py`` (mentions),
``tests/test_presence.py`` (the roster), ``tests/test_claims.py`` (the
write-guard seam) and ``tests/test_undo_authors.py`` (authorship, ``scope``,
``revert``). This file is the *contract* layer: it walks each acceptance
criterion of ``docs/prd/in-progress/PRD-008-review-threads-presence.md`` end to
end through the surfaces a human and an agent actually touch — the five tools,
the REST routes, the WebSocket, the real git history and a real kernel build —
so a reviewer can map AC → test without reading the unit suites.

| AC | Test |
|----|------|
| AC1 | ``test_ac1_the_review_loop_end_to_end`` (scripted agent half) +
        ``test_ac1_browser_half_evidence_is_recorded`` (the two-browser
        session, driven for real in slices 8-9 — the PRD-001 AC6 / PRD-002 AC1
        evidence-check precedent) +
        ``test_ac1_browser_half_is_wired_into_the_shipped_frontend`` (a
        source-level gate under that record: the modules must still define and
        wire the surfaces the changelogs claim) |
| AC2 | ``test_ac2_a_face_anchor_survives_or_says_it_did_not`` |
| AC3 | ``test_ac3_a_script_range_anchor_tracks_an_insert_above_it`` |
| AC4 | ``test_ac4_a_mention_delivers_an_event_and_an_unread_record`` |
| AC5 | ``test_ac5_a_claim_conflicts_overrides_and_leaves_other_parts_alone`` |
| AC6 | ``test_ac6_the_turn_lock_still_decides_first`` +
        ``test_ac6_the_lock_suite_is_unmodified`` |
| AC7 | ``test_ac7_per_user_undo_and_its_structured_conflict`` |
| AC8 | ``test_ac8_threads_survive_project_restore`` |
| AC9 | ``test_ac9_an_attachment_outside_exports_is_refused`` +
        ``test_ac9_the_full_suite_count_is_cited`` |

**AC2 is asserted at the wording the measurements support, not at the wording
the PRD was written with**, and there are now two measurements because there
are two classes.

*A parameter changed* — the slice-2 spike, re-measured after code review
(``docs/changelog/0123-prd008-review-fixes.md``, 2 693 face pairs against a
stricter ground-truth oracle): face ordinals move (about one in ten for a 1%
tweak), the matcher resolves 53.9% of surviving faces, orphans the rest, and
**mis-pins 2 of 2 693**.

*A feature was deleted* — measured separately
(``docs/changelog/0125-prd008-verifier-fixes.md``, 327 faces that no longer
exist, ground truth from a geometric oracle), because the sweep above never
deletes anything and therefore cannot speak for this at all: **98.8% correctly
orphaned, 4 mis-pinned**, where the area bar alone left 27. The four are a
square pad on a square plate, and
``test_a_square_pad_the_shape_of_the_face_under_it_still_re_pins`` in
``tests/test_anchors_kernel.py`` asserts that outcome rather than a comment
claiming it cannot happen.

So "never" is not a claim this suite makes in either direction. Slices 8-9
added two more ceilings (changelog 0119): a parameter change that moves a
face's position *relative to the shape's bounds* orphans it even though the
face still exists, and a closed curved face orphans on any edit at all. The
honest criterion — recorded as a divergence in the PRD's as-built section — is
**"survives a parameter tweak where the face's position within the shape's
bounds is stable, or says `orphaned` with a reason, and rarely points at the
wrong face — rarely, not never, in both classes"**, and this module tests
exactly that: `orphaned` is asserted as a *correct* outcome, and every
non-orphaned answer is verified geometrically rather than by trusting the
resolver's own reply.

Marks: ``integration`` + ``portability`` throughout (git, subprocesses, local
sockets); every case that builds geometry is additionally ``slow``.
"""

from __future__ import annotations

import queue
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentcad.core import anchors, locks
from agentcad.core.service import AgentCADService, EventBus
from agentcad.core.tools import build_registry
from agentcad.server.app import create_app

from .conftest import BOX_SCRIPT, make_test_service

pytestmark = [
    pytest.mark.integration,
    pytest.mark.portability,
    pytest.mark.skipif(shutil.which("git") is None, reason="git not found on PATH"),
]

REPO_ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = REPO_ROOT / "docs" / "changelog"

ALICE = {"X-Agent-Id": "browser:aaaaaaaa"}
BOB = {"X-Agent-Id": "browser:bbbbbbbb"}
AGENT = {"X-Agent-Id": "bot"}

# A plate with a boss on top: the boss's top face is the thing a reviewer
# points at, and `plate_w` widens the plate WITHOUT moving that face relative
# to the shape's bounds — the tweak AC2 can honestly claim survival for.
BOSS = '''\
import build123d as b3d

PARAMS = {
    "plate_w": {"default": 40.0, "min": 20.0, "max": 80.0, "unit": "mm"},
    "boss_r":  {"default": 8.0,  "min": 4.0,  "max": 15.0, "unit": "mm"},
}


def build(p):
    plate = b3d.Box(p.plate_w, 40, 10)
    boss = b3d.Cylinder(radius=p.boss_r, height=10).moved(
        b3d.Location((0, 0, 10)))
    return plate + boss
'''

# The agent's fix: the same part with the boss taken away — AC2's second half.
NO_BOSS = BOSS.replace(
    "    boss = b3d.Cylinder(radius=p.boss_r, height=10).moved(\n"
    "        b3d.Location((0, 0, 10)))\n    return plate + boss\n",
    "    return plate\n",
)
assert "boss = " not in NO_BOSS

# A wider boss: a real script edit that leaves the shape's bounds alone.
#
# r=9, not r=11, and the reason is a measured ceiling rather than a fudge. The
# boss's top face is the *only* candidate for its own signature (nothing else
# on this shape faces +Z at the top of the bounding box), so it is judged by
# ``anchors.LONE_AREA_REL`` — the absolute area-share bar a lone candidate has
# to clear now that "only survivor" no longer counts as proof. r=8 -> 9 moves
# that share by 0.20; r=8 -> 11 moves it by 0.45, further than ANY correct
# lone match in the 2 693-face measurement (max 0.432) and further than the
# mis-pin the bar exists to refuse (0.434). An edit that nearly doubles the
# commented face's share of the part now orphans the thread, honestly, and
# that ceiling is documented in the user guide.
WIDER_BOSS = BOSS.replace('"default": 8.0', '"default": 9.0')
assert WIDER_BOSS != BOSS


def _drain(subscription) -> list[dict]:
    events = []
    while True:
        try:
            events.append(subscription.get_nowait())
        except queue.Empty:
            return events


def _as(client_id: str) -> None:
    locks.set_client_id(client_id)


@pytest.fixture(autouse=True)
def _reset_identity():
    token = locks.client_id_var.set("browser")
    yield
    locks.client_id_var.reset(token)


@pytest.fixture
def geo(kernel, tmp_path):
    """A real service (real git history) with one built part carrying a face
    worth commenting on."""
    service = AgentCADService(tmp_path / "projects", kernel, EventBus())
    registry = build_registry(service)
    assert "error" not in registry.call("create_project", {"name": "demo"})
    created = registry.call(
        "create_part", {"project": "demo", "part_id": "boss", "script": BOSS})
    assert "error" not in created, created
    anchors.forget_tables()
    return service, registry


@pytest.fixture
def http(kernel, tmp_path):
    """Two script parts behind a real app — the route pack is what installs
    the claim guard, so the app has to exist for AC5/AC6."""
    service = make_test_service(tmp_path / "projects", kernel)
    registry = build_registry(service)
    app = create_app(service, registry, extra_allowed_hosts={"testserver"})
    client = TestClient(app, base_url="http://127.0.0.1")
    assert client.post("/api/projects", json={"name": "demo"}).status_code == 201
    for part in ("box", "lid"):
        assert client.post("/api/projects/demo/parts",
                           json={"id": part, "script": BOX_SCRIPT}
                           ).status_code == 201
    return service, registry, client


def _top_of_boss(service, part="boss"):
    """The boss's top face, identified by normal and height — never by a
    stored ordinal, which is exactly what slice 2 proved unstable."""
    _key, table = anchors.signature_table(service, "demo", part)
    up = [row for row in table if row["present"] and row["normal"][2] > 0.99]
    return max(up, key=lambda row: row["centroid"][2])


# ------------------------------------------------------------------- AC1


@pytest.mark.slow
@pytest.mark.timeout(900)
def test_ac1_the_review_loop_end_to_end(geo):
    """AC1 (scripted half) — a human points at a face and says what is wrong;
    an agent lists the thread, sees the anchor, edits the script, renders,
    replies with the render attached and resolves; a second client sees each
    step live on the WebSocket.
    """
    service, registry = geo
    app = create_app(service, registry, extra_allowed_hosts={"testserver"})
    http = TestClient(app, base_url="http://127.0.0.1")
    subscription = service.bus.subscribe()

    # 1. the human, in a browser, comments on the boss's top face.
    face = _top_of_boss(service)
    opened = http.post(
        "/api/projects/demo/comments",
        json={"anchor": {"kind": "face", "part": "boss",
                         "face_index": face["index"]},
              "body": "this boss needs to be wider"},
        headers=ALICE,
    )
    assert opened.status_code == 200, opened.text
    thread = opened.json()["thread"]
    assert thread["author_kind"] == "human"
    assert thread["state"] == "open"

    # 2. the agent reads its work queue and gets the anchor plus the evidence
    #    the server stamped on it — never a signature the caller asserted.
    _as("bot")
    listing = registry.call("list_comments", {"project": "demo"})
    assert "error" not in listing, listing
    mine = listing["threads"][0]
    assert mine["anchor"]["kind"] == "face"
    assert mine["anchor"]["part"] == "boss"
    assert mine["anchor"]["signature"]["area_mm2"] > 0
    assert mine["resolution"]["status"] == "ok"
    assert listing["counts"]["open"] == 1

    # 3. the agent edits the script and renders the result.
    edited = registry.call("update_part_script", {
        "project": "demo", "part_id": "boss", "script": WIDER_BOSS})
    assert edited.get("ok") is True, edited
    render = registry.call("render_view", {
        "project": "demo", "part_id": "boss", "width": 320, "height": 240})
    assert "error" not in render, render

    # 4. it replies with the render as evidence, then resolves the thread.
    replied = registry.call("add_comment", {
        "project": "demo", "thread": thread["id"],
        "body": "widened the boss to r=9; see the render",
        "attachments": [render["path"]]})
    assert "error" not in replied, replied
    reply = replied["thread"]["comments"][-1]
    assert reply["author_kind"] == "agent"
    assert reply["attachments"][0]["available"] is True

    # A second browser watches the resolve land live.
    with http.websocket_connect("/ws") as ws:
        resolved = registry.call(
            "resolve_thread", {"project": "demo", "thread": thread["id"]})
        assert "error" not in resolved, resolved
        event = ws.receive_json()
    assert event == {"type": "comment_changed", "project": "demo",
                     "thread": thread["id"], "state": "resolved",
                     "action": "resolved", "part": "boss"}

    # Every step was announced, in order, and none of them was a model change.
    actions = [e["action"] for e in _drain(subscription)
               if e["type"] == "comment_changed"]
    assert actions == ["created", "replied", "resolved"]

    # The thread still points at the same face after the edit, and the human
    # can see the whole exchange.
    after = registry.call("list_comments", {"project": "demo",
                                            "state": "resolved"})
    assert [t["id"] for t in after["threads"]] == [thread["id"]]
    assert after["threads"][0]["resolution"]["status"] in ("ok", "moved")
    assert len(after["threads"][0]["comments"]) == 2


def test_ac1_browser_half_evidence_is_recorded():
    """AC1 (browser half) — "browser B sees the pin, reply and resolution live
    with zero console errors" was driven for real in slices 8 and 9 (headless
    Chrome, two clients, screenshots). This is the evidence check: it asserts
    the session is on the record, so the criterion has a named check that fails
    if the record is removed, without re-driving a browser from the suite (the
    PRD-001 AC6 / PRD-002 AC1 pattern).
    """
    ui = (CHANGELOG / "0119-prd008-threads-ui.md").read_text(encoding="utf-8")
    presence = (CHANGELOG / "0120-prd008-presence-ui.md").read_text(
        encoding="utf-8")
    assert "AC2" in ui and "AC3" in ui
    for phrase in ("pin", "console", "screenshot"):
        assert phrase in ui.lower(), f"slice-8 evidence does not mention {phrase!r}"
    assert "AC1" in presence
    for phrase in ("two identities", "console", "override"):
        assert phrase in presence.lower(), \
            f"slice-9 evidence does not mention {phrase!r}"


def test_ac1_browser_half_is_wired_into_the_shipped_frontend():
    """The structural half of AC1, and the honest limit of it.

    The evidence check above asserts only that a changelog *says* a browser
    session happened, which is exactly as strong as the prose: deleting
    ``comments.init()``, the pin overlay or the claim dialog would have left it
    green. This one reads the shipped modules and asserts the surfaces the
    changelogs claim are actually defined and actually wired together, so
    removing the feature fails a test rather than only contradicting a
    document.

    What it does **not** prove: that any of it renders, that the pin lands in
    the right place, or that the console is clean. Those need a browser, they
    were driven for real in slices 8-9, and the changelog is the record. This
    is a structural gate under that record, not a replacement for it.
    """
    js = REPO_ROOT / "frontend" / "js"
    comments = (js / "comments.js").read_text(encoding="utf-8")
    main = (js / "main.js").read_text(encoding="utf-8")
    api = (js / "api.js").read_text(encoding="utf-8")
    viewport = (js / "viewport.js").read_text(encoding="utf-8")

    # The threads module and its pin overlay.
    for surface in ("export function init(", "export function meshChanged(",
                    "export function handleEvent(", "function syncPins(",
                    "function positionPins("):
        assert surface in comments, f"comments.js no longer defines {surface!r}"
    # A pin is placed from the CURRENT geometry, never from the stored anchor:
    # this is the mis-pin the whole feature is arranged around.
    assert "viewport.faceCentroid(" in comments
    assert "export function faceCentroid(" in viewport

    # …and the app wires both of them plus the claim-override dialog.
    assert "comments.init(" in main
    assert "comments.meshChanged(" in main
    assert '"comment_changed"' in main
    assert '"claim_changed"' in main
    assert "api.overrideClaim(" in main
    assert "overrideClaim:" in api


# ------------------------------------------------------------------- AC2


@pytest.mark.slow
@pytest.mark.timeout(900)
def test_ac2_a_face_anchor_survives_or_says_it_did_not(geo):
    """AC2, at the wording the measurements support (see the module docstring).

    Three claims, all of them checked here: a bounds-stable parameter tweak
    keeps the anchor pointing at the *same face* (verified geometrically, not
    by trusting the resolver); cutting the face away answers `orphaned` with a
    reason and **no** face index — a guess would be the one failure mode this
    feature must never have; and the thread stays listable, readable and
    resolvable either way, with its stored anchor untouched.
    """
    service, registry = geo
    manager = service.comments
    face = _top_of_boss(service)
    thread = manager.create(
        "demo", {"kind": "face", "part": "boss", "face_index": face["index"]},
        "fillet this")
    assert thread["resolution"]["status"] == "ok"

    # 1. a parameter tweak that leaves the face where it is, relative to the
    #    shape's bounds: it survives, and it is still the boss's top face.
    service.set_params("demo", "boss", {"plate_w": 60.0})
    resolution = manager.get("demo", thread["id"])["resolution"]
    assert resolution["status"] in ("ok", "moved"), resolution
    _key, table = anchors.signature_table(service, "demo", "boss")
    assert table[resolution["face_index"]]["index"] == \
        _top_of_boss(service)["index"], resolution

    # 2. the face is cut away: orphaned, with a reason and a hint, and never a
    #    plausible-looking ordinal.
    cut = registry.call("update_part_script", {
        "project": "demo", "part_id": "boss", "script": NO_BOSS})
    assert cut.get("ok") is True, cut
    view = manager.get("demo", thread["id"])
    assert view["resolution"]["status"] == "orphaned", view["resolution"]
    assert view["resolution"]["reason"] and view["resolution"]["hint"]
    assert "face_index" not in view["resolution"]

    # 3. an orphan is still a thread: listable, filterable, resolvable, and its
    #    stored anchor is evidence rather than a cursor.
    listing = registry.call("list_comments",
                            {"project": "demo", "anchor_status": "orphaned"})
    assert [t["id"] for t in listing["threads"]] == [thread["id"]]
    assert listing["counts"]["orphaned"] == 1
    assert manager.store.load("demo", thread["id"])["anchor"]["face_index"] == \
        face["index"]
    assert "error" not in registry.call(
        "resolve_thread", {"project": "demo", "thread": thread["id"]})


# ------------------------------------------------------------------- AC3


def test_ac3_a_script_range_anchor_tracks_an_insert_above_it(http):
    """AC3 — two lines inserted above a commented range and the thread follows,
    reporting `moved` with the NEW address (design Decision 6, tier 1: an exact
    snippet search, no git, no guessing)."""
    service, registry, client = http
    original = service.store.read_script("demo", "box").splitlines()
    target = next(i for i, line in enumerate(original, 1)
                  if "Box(" in line)

    opened = registry.call("add_comment", {
        "project": "demo",
        "anchor": {"kind": "script_range", "part": "box",
                   "start": target, "end": target},
        "body": "extract this into a helper"})
    assert "error" not in opened, opened
    tid = opened["thread"]["id"]
    assert opened["thread"]["resolution"]["status"] == "ok"

    edited = "\n".join(["# a note", "# another note", *original]) + "\n"
    assert client.put("/api/projects/demo/parts/box",
                      json={"script": edited}).status_code == 200

    row = registry.call("list_comments", {"project": "demo"})["threads"][0]
    assert row["id"] == tid
    assert row["resolution"]["status"] == "moved", row["resolution"]
    assert row["resolution"]["start"] == target + 2
    assert row["resolution"]["end"] == target + 2
    assert row["resolution"]["reason"] == "snippet_found_verbatim"
    # The stored anchor is untouched: resolution is a view, not a rewrite.
    assert row["anchor"]["start"] == target


# ------------------------------------------------------------------- AC4


def test_ac4_a_mention_delivers_an_event_and_an_unread_record(http):
    """AC4 — `@chat:main` in a body publishes a `notification` on the bus and
    leaves exactly one unread record for that identity, which a read marks
    off. The bus is a broadcast and clients filter on `to` (Decision 11)."""
    _service, registry, client = http

    with client.websocket_connect("/ws") as ws:
        posted = client.post(
            "/api/projects/demo/comments",
            json={"anchor": {"kind": "part", "part": "box"},
                  "body": "@chat:main can you fillet this? @nobody ignore"},
            headers=ALICE)
        assert posted.status_code == 200, posted.text
        events = [ws.receive_json(), ws.receive_json()]

    assert [e["type"] for e in events] == ["comment_changed", "notification"]
    assert events[1]["to"] == "chat:main"
    assert events[1]["from"] == "browser:aaaaaaaa"

    chat = {"X-Agent-Id": "chat:main"}
    inbox = client.get("/api/projects/demo/notifications?unread=true",
                       headers=chat).json()
    assert inbox["unread"] == 1
    assert [n["to"] for n in inbox["notifications"]] == ["chat:main"]
    # @nobody is not a plausible identity: it stays plain text and delivers
    # nothing, to nobody.
    assert "@nobody" in posted.json()["thread"]["comments"][0]["body"]
    assert posted.json()["thread"]["comments"][0]["mentions"] == ["chat:main"]

    marked = client.post("/api/projects/demo/notifications/read", json={},
                         headers=chat)
    assert marked.status_code == 200, marked.text
    assert client.get("/api/projects/demo/notifications?unread=true",
                      headers=chat).json()["unread"] == 0
    # ...and nobody else's inbox was ever involved.
    assert client.get("/api/projects/demo/notifications",
                      headers=ALICE).json()["notifications"] == []


# ------------------------------------------------------------- AC5 and AC6


def test_ac5_a_claim_conflicts_overrides_and_leaves_other_parts_alone(http):
    """AC5 — A is editing part X; B's write to X is a 409 naming A and saying
    `overridable: true`; B arms the override and the retry lands; B's write to
    part Y was never affected."""
    _service, _registry, client = http

    beat = client.post("/api/projects/demo/presence",
                       json={"part_id": "box", "surface": "editor",
                             "claim": True}, headers=ALICE)
    assert beat.status_code == 200, beat.text
    assert beat.json()["claims"]["box"]["holder"] == "browser:aaaaaaaa"

    refused = client.put("/api/projects/demo/parts/box",
                         json={"script": BOX_SCRIPT}, headers=BOB)
    assert refused.status_code == 409, refused.text
    details = refused.json()["error"]["details"]
    assert details["claim"]["holder"] == "browser:aaaaaaaa"
    assert details["claim"]["part"] == "box"
    assert details["overridable"] is True

    # The other part is untouched throughout: this is a PART claim, not a turn.
    assert client.put("/api/projects/demo/parts/lid",
                      json={"script": BOX_SCRIPT},
                      headers=BOB).status_code == 200

    armed = client.post("/api/projects/demo/claims/override",
                        json={"part": "box"}, headers=BOB)
    assert armed.status_code == 200, armed.text
    assert armed.json()["armed_until"] > 0
    assert client.put("/api/projects/demo/parts/box",
                      json={"script": BOX_SCRIPT},
                      headers=BOB).status_code == 200

    # An agent is never claim-blocked: the human→agent loop must not 409 on the
    # agent's very first write (design Decision 14).
    client.post("/api/projects/demo/presence",
                json={"part_id": "box", "surface": "editor", "claim": True},
                headers=ALICE)
    assert client.put("/api/projects/demo/parts/box",
                      json={"script": BOX_SCRIPT},
                      headers=AGENT).status_code == 200


def test_ac6_the_turn_lock_still_decides_first(http):
    """AC6 — with an agent holding the project turn, both browsers' writes fail
    exactly as the pre-existing turn lock makes them fail: the old message, and
    **no** claim details, because the turn decided before claims were asked."""
    _service, registry, client = http
    _as("bot")
    assert "error" not in registry.call("acquire_turn", {"project": "demo"})
    try:
        for who in (ALICE, BOB):
            refused = client.put("/api/projects/demo/parts/box",
                                 json={"script": BOX_SCRIPT}, headers=who)
            assert refused.status_code == 409, refused.text
            error = refused.json()["error"]
            assert "locked by" in error["message"]
            assert "claim" not in error["details"]
        # And the turn holder is never claim-checked, even on a part somebody
        # else claimed first (rule 2).
        client.post("/api/projects/demo/presence",
                    json={"part_id": "box", "surface": "editor",
                          "claim": True}, headers=ALICE)
        assert client.put("/api/projects/demo/parts/box",
                          json={"script": BOX_SCRIPT},
                          headers=AGENT).status_code == 200
    finally:
        assert "error" not in registry.call("release_turn", {"project": "demo"})


def test_ac6_the_lock_suite_is_unmodified():
    """AC6's actual gate is that the pre-existing lock suite passes
    **unmodified** — a claim about a diff, so this asks git for the diff.

    It used to assert only that slice 7's changelog *said so*, which is a test
    of a sentence rather than of the tree. ``git diff main...HEAD --
    tests/test_locks.py`` must be empty. Skipped, never silently passed, where
    the question cannot be asked: no git, not a repo, or no ``main`` to
    compare against (a shallow CI clone, a fork whose default branch is named
    something else).
    """
    if shutil.which("git") is None:
        pytest.skip("git not found on PATH")
    repo = Path(__file__).resolve().parents[1]

    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, capture_output=True,
                              text=True, timeout=30)

    if git("rev-parse", "--is-inside-work-tree").returncode != 0:
        pytest.skip("not a git work tree")
    if git("rev-parse", "--verify", "--quiet", "main").returncode != 0:
        pytest.skip("no 'main' branch to compare against")

    diff = git("diff", "main...HEAD", "--", "tests/test_locks.py")
    assert diff.returncode == 0, diff.stderr
    assert diff.stdout.strip() == "", (
        "tests/test_locks.py changed on this branch; AC6's gate is that the "
        f"pre-existing lock suite passes UNMODIFIED:\n{diff.stdout}")

    working = git("diff", "--", "tests/test_locks.py")
    assert working.stdout.strip() == "", (
        "tests/test_locks.py has uncommitted modifications:\n"
        f"{working.stdout}")


# ------------------------------------------------------------------- AC7


@pytest.mark.slow
def test_ac7_per_user_undo_and_its_structured_conflict(kernel, tmp_path):
    """AC7 — A edits part X, B edits part Y, A undoes only X; then B also edits
    X and A's undo of the X commit is a structured refusal naming B's commit.
    The default scope is deliberately unchanged (Decision 16)."""
    service = AgentCADService(tmp_path / "projects", kernel, EventBus())
    registry = build_registry(service)
    _as("browser:aaaaaaaa")
    assert "error" not in registry.call("create_project", {"name": "demo"})
    for part in ("box", "lid"):
        assert "error" not in registry.call(
            "create_part", {"project": "demo", "part_id": part,
                            "script": BOX_SCRIPT})

    taller = BOX_SCRIPT.replace("p.size, p.size, p.size)",
                                "p.size, p.size, p.size * 2)")
    wider = BOX_SCRIPT.replace("p.size, p.size, p.size)",
                               "p.size, p.size, p.size * 3)")
    path = service.store.path_of("demo")

    _as("browser:aaaaaaaa")
    assert "error" not in registry.call("update_part_script", {
        "project": "demo", "part_id": "box", "script": taller})
    a_commit = service.history.log(path, limit=1)[0]["id"]
    _as("browser:bbbbbbbb")
    assert "error" not in registry.call("update_part_script", {
        "project": "demo", "part_id": "lid", "script": wider})

    _as("browser:aaaaaaaa")
    undone = registry.call("undo", {"project": "demo", "scope": "mine"})
    assert "error" not in undone, undone
    assert service.store.read_script("demo", "box") == BOX_SCRIPT   # A's edit
    assert service.store.read_script("demo", "lid") == wider        # B's stands
    assert service.history.log(path, limit=1)[0]["author"] == "browser:aaaaaaaa"

    # Now the overlap: A edits X again, B edits X on top of it, and A's undo of
    # its own X commit is refused rather than clobbering B.
    _as("browser:aaaaaaaa")
    assert "error" not in registry.call("update_part_script", {
        "project": "demo", "part_id": "box", "script": taller})
    a_again = service.history.log(path, limit=1)[0]["id"]
    _as("browser:bbbbbbbb")
    assert "error" not in registry.call("update_part_script", {
        "project": "demo", "part_id": "box", "script": wider})
    b_commit = service.history.log(path, limit=1)[0]["id"]

    _as("browser:aaaaaaaa")
    refused = registry.call("undo", {"project": "demo", "scope": "mine"})
    assert refused["error"]["type"] == "conflict_error", refused
    details = refused["error"]["details"]
    assert details["commit"] == a_again
    assert details["reason"] == "overlapping_changes"
    assert "parts/box.py" in details["paths"]
    assert b_commit in details["blocked_by"]
    # Nothing landed: B's edit is still what is on disk, and A's entry is still
    # A's to retry (FR14 — a refusal, never a partial apply).
    assert service.store.read_script("demo", "box") == wider
    assert service.history.log(path, limit=1)[0]["id"] == b_commit
    assert service.undo_cursor.status("demo")["mine"]["undo"] >= 1
    assert a_commit


# ----------------------------------------------------------- AC8 and AC9


@pytest.mark.slow
def test_ac8_threads_survive_project_restore(geo):
    """AC8 — threads live inside GIT_DIR at `.history/agentcad/comments/`, so
    `project_restore` structurally cannot rewind one. True by construction;
    asserted at the tool seam a user would reach for."""
    service, registry = geo
    manager = service.comments
    history = registry.call("project_history", {"project": "demo"})
    assert history["available"], history
    earliest = history["history"][-1]["id"]

    opened = registry.call("add_comment", {
        "project": "demo", "anchor": {"kind": "part", "part": "boss"},
        "body": "check the draft angle"})
    assert "error" not in opened, opened
    tid = opened["thread"]["id"]
    registry.call("add_comment", {"project": "demo", "thread": tid,
                                  "body": "on it"})
    before = manager.store.load("demo", tid)

    assert "error" not in registry.call("update_part_script", {
        "project": "demo", "part_id": "boss", "script": WIDER_BOSS})
    restored = registry.call("project_restore",
                             {"project": "demo", "commit": earliest})
    assert "error" not in restored, restored

    listing = registry.call("list_comments", {"project": "demo"})
    assert [t["id"] for t in listing["threads"]] == [tid]
    assert manager.store.load("demo", tid) == before
    assert len(manager.store.audit("demo", tid)) == 2


def test_ac9_an_attachment_outside_exports_is_refused(http):
    """AC9 — no path disclosure through comments: everything that resolves
    outside the project's `exports/` is a validation_error, at the tool and at
    the route."""
    service, registry, client = http
    outside = service.store.canonical_path_of("demo") / "project.json"
    exports = service.store.exports_dir("demo")
    exports.mkdir(parents=True, exist_ok=True)
    (exports / "escape.png").symlink_to(outside)

    for value in ("../../etc/passwd", "exports/../project.json", str(outside),
                  "parts/box.py", "exports/escape.png", "exports/missing.png"):
        refused = registry.call("add_comment", {
            "project": "demo", "anchor": {"kind": "part", "part": "box"},
            "body": "see this", "attachments": [value]})
        assert refused["error"]["type"] == "validation_error", (value, refused)
        assert refused["error"]["message"], value

    at_the_route = client.post(
        "/api/projects/demo/comments",
        json={"anchor": {"kind": "part", "part": "box"}, "body": "see this",
              "attachments": ["../../etc/passwd"]}, headers=ALICE)
    assert at_the_route.status_code == 422, at_the_route.text
    # ...and a real export is accepted, so the rule is a boundary, not a ban.
    (exports / "renders").mkdir(parents=True, exist_ok=True)
    (exports / "renders" / "iso.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    ok = registry.call("add_comment", {
        "project": "demo", "anchor": {"kind": "part", "part": "box"},
        "body": "see this", "attachments": ["exports/renders/iso.png"]})
    assert "error" not in ok, ok


def test_ac9_the_full_suite_count_is_cited():
    """AC9's second half — "full suite green, count cited" is a claim about a
    run, so this is the evidence check that the count is on the record in the
    close-out changelog, per the PRD-004 AC10 precedent."""
    entry = CHANGELOG / "0122-prd008-completed.md"
    assert entry.is_file(), "the PRD-008 close-out changelog entry is missing"
    text = entry.read_text(encoding="utf-8")
    assert "make test" in text
    assert "passed" in text
    assert any(token.isdigit() and len(token) >= 4
               for token in text.replace(",", " ").split()), \
        "the close-out entry does not cite a suite count"

    # It stays an evidence check, deliberately. Recomputing the number here
    # would mean running the full suite from inside the full suite; collecting
    # it instead (`--collect-only`) counts *cases*, which is not what
    # `make test` reports (marks, skips and parametrization all move it). The
    # later entry that reports a NEW count is the thing that must not silently
    # contradict this one, so both are required to cite one.
    latest = max(CHANGELOG.glob("0[0-9][0-9][0-9]-*.md"))
    if latest != entry:
        recent = latest.read_text(encoding="utf-8")
        assert "make test" in recent and "passed" in recent, (
            f"{latest.name} is the newest changelog entry and cites no "
            "suite count; every entry that lands work must cite one")
