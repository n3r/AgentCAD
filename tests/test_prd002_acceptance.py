"""PRD-002 acceptance criteria — one named test per AC (slice 6).

The feature's mechanics are covered in depth by ``tests/test_proposals.py``
(store, state machine, audit, policy, gates, gated merge),
``tests/test_proposals_api.py`` (tools, routes, events),
``tests/test_geom_diff.py`` / ``tests/test_render_frame.py`` (the two kernel /
render primitives) and ``tests/test_packet.py`` (the packet builder). This file
is the *contract* layer: it walks each acceptance criterion of
``docs/prd/in-progress/PRD-002-change-proposals-geometric-diff.md`` end to end
through the real stack — tools, routes, git and the kernel — so a reviewer can
map AC → test without reading the unit suites.

| AC | Test |
|----|------|
| AC1 | ``test_ac1_roundtrip_agent_proposes_human_merges`` (the rocketry
        example, on a copy) — the agent half through tools, the human half
        through the HTTP routes; plus
        ``test_ac1_browser_half_evidence_is_recorded``, which asserts slice 5's
        real browser session is on the record rather than re-driving a browser |
| AC2 | ``test_ac2_packet_generates_warm_under_10s`` (timed, rocketry copy) |
| AC3 | ``test_ac3_drilled_hole_reports_removed_volume`` plus
        ``test_ac3_browser_overlay_evidence_is_recorded`` for the overlay half |
| AC4 | ``test_ac4_instance_move_does_no_per_part_kernel_work`` |
| AC5 | ``test_ac5_failed_validation_blocks_then_overrides`` |
| AC6 | ``test_ac6_self_approval_does_not_satisfy_policy`` |
| AC7 | ``test_ac7_unbuildable_side_degrades_honestly`` |
| AC8 | ``test_ac8_second_client_sees_proposal_changed_live`` |
| AC9 | ``test_ac9_project_restore_does_not_rewind_proposals`` plus the full
        suite run and ``git diff --name-status main -- tests/`` cited in
        ``docs/changelog/0082-proposals-docs-and-acceptance.md`` |

Everything here touches git, so the module carries ``integration`` +
``portability`` and skips without git; the cases that build geometry are
additionally ``slow``.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentcad.core import locks
from agentcad.core.branches import pinned_tree_var
from agentcad.core.service import AgentCADService, EventBus
from agentcad.core.tools import build_registry
from agentcad.kernel import acm
from agentcad.server.app import create_app

from .conftest import BOX_SCRIPT

REPO_ROOT = Path(__file__).resolve().parent.parent
ROCKETRY = REPO_ROOT / "examples" / "rocketry"
CHANGELOG = REPO_ROOT / "docs" / "changelog"

_GIT = [
    pytest.mark.integration,
    pytest.mark.portability,
    pytest.mark.skipif(shutil.which("git") is None, reason="git not found on PATH"),
]
pytestmark = _GIT

CUBE = '''\
from build123d import *
PARAMS = {"s": {"default": 20.0, "min": 5.0, "max": 50.0, "unit": "mm",
                "description": "cube edge"}}
def build(p):
    return Box(p.s, p.s, p.s)
'''

CUBE_HOLE = CUBE.replace(
    "    return Box(p.s, p.s, p.s)\n",
    "    return Box(p.s, p.s, p.s) - Cylinder(3.0, p.s * 2)\n",
)

BROKEN_SCRIPT = BOX_SCRIPT.replace("return part.part", "return no_such_name")
BOX_V2_SCRIPT = BOX_SCRIPT.replace(
    "Box(p.size, p.size, p.size)", "Box(p.size, p.size, p.size * 2)"
)

HOLE_MM3 = 3.14159265358979 * 3.0 ** 2 * 20.0

AGENT = "chat:main"      # the chat dock drives an agent -> actor_kind 'agent'
HUMAN = "browser"        # the browser UI is the one surface a human drives


@pytest.fixture(autouse=True)
def _reset_context():
    """Identity and the merge pin are ContextVars: rebind them per test so one
    test's identity switch can never leak into the next."""
    cid = locks.client_id_var.set("local")
    pin = pinned_tree_var.set(None)
    yield
    locks.client_id_var.reset(cid)
    pinned_tree_var.reset(pin)


@pytest.fixture
def stack(kernel, tmp_path):
    """The real service + registry (NOT make_test_service, which disables the
    snapshot hook): the packs install ``service.branches``, ``service.merges``,
    ``service.proposals`` and ``service.packets``."""
    service = AgentCADService(tmp_path / "projects", kernel, EventBus())
    registry = build_registry(service)
    assert getattr(service, "proposals", None) is not None
    assert getattr(service, "packets", None) is not None
    return service, registry


@pytest.fixture
def demo(stack):
    """'demo' with one buildable part on master and a 'feat' branch forked from
    it — the fast fixture for AC3–AC9."""
    service, registry = stack
    assert "error" not in registry.call("create_project", {"name": "demo"})
    assert "error" not in registry.call(
        "create_part", {"project": "demo", "part_id": "box", "script": BOX_SCRIPT})
    assert "error" not in registry.call(
        "branch_create", {"project": "demo", "name": "feat"})
    return service, registry


def _on(service, client: str, branch: str, proj: str = "demo") -> None:
    """Put a client identity on a branch (identity + checkout)."""
    locks.set_client_id(client)
    if service.branches.current(proj) != branch:
        service.branches.switch(proj, branch)


def _script(registry, part: str, text: str, proj: str = "demo") -> dict:
    return registry.call(
        "update_part_script", {"project": proj, "part_id": part, "script": text})


def _propose(registry, source: str, proj: str = "demo", **extra) -> str:
    """Open a proposal as the AGENT identity and return its id."""
    locks.set_client_id(AGENT)
    args = {"project": proj, "source": source, "title": "Thinner wall",
            "description": "the mass budget needs it", **extra}
    created = registry.call("proposal_create", args)
    assert "error" not in created, created
    return created["proposal"]["id"]


def _copy_rocketry(registry, tmp_path) -> tuple[str, Path]:
    dest = tmp_path / "ex" / "rocketry"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROCKETRY, dest, ignore=shutil.ignore_patterns(".cache", "exports"))
    opened = registry.call("open_project", {"path": str(dest)})
    assert "error" not in opened, opened
    return opened["name"], dest


def _thin_the_nozzle(registry, proj: str, dest: Path) -> None:
    """The PRD's worked example: a nozzle wall-thickness change (script comment
    + the ``wall`` override), made by whoever is the current identity."""
    nozzle = (dest / "parts" / "nozzle.py").read_text(encoding="utf-8")
    assert "error" not in registry.call(
        "update_part_script",
        {"project": proj, "part_id": "nozzle",
         "script": nozzle + "\n# thinner wall for the mass budget\n"})
    assert "error" not in registry.call(
        "set_params",
        {"project": proj, "part_id": "nozzle", "values": {"wall": 2.6}})


def _actions(registry, proj: str, pid: str) -> list[tuple[str, str]]:
    detail = registry.call("proposal_get", {"project": proj, "id": pid})
    assert "error" not in detail, detail
    return [(e["action"], e["actor_kind"]) for e in detail["audit"]]


# ------------------------------------------------------------------- AC1


@pytest.mark.slow
@pytest.mark.skipif(not (ROCKETRY / "project.json").is_file(),
                    reason="rocketry example not present")
@pytest.mark.timeout(900)
def test_ac1_roundtrip_agent_proposes_human_merges(stack, tmp_path):
    """AC1 — the roadmap round trip on a copy of ``examples/rocketry``: an
    agent branches, edits and opens a proposal *through tools*; a human reads
    the packet, approves and merges *through the HTTP routes* the browser uses
    (no service or manager call anywhere in the human half); and every action
    in ``proposal_get``'s audit carries the right ``actor_kind``.
    """
    service, registry = stack
    proj, dest = _copy_rocketry(registry, tmp_path)
    app = create_app(service, registry, extra_allowed_hosts={"testserver"})
    http = TestClient(app, base_url="http://127.0.0.1")

    # --- the agent half: branch -> edit -> propose, all through tools
    locks.set_client_id(AGENT)
    assert "error" not in registry.call(
        "branch_create", {"project": proj, "name": "nozzle-thinner"})
    assert "error" not in registry.call(
        "branch_switch", {"project": proj, "name": "nozzle-thinner"})
    _thin_the_nozzle(registry, proj, dest)
    pid = _propose(registry, "nozzle-thinner", proj=proj,
                   title="Thin the nozzle wall to 2.6 mm")
    packet = registry.call("proposal_packet", {"project": proj, "id": pid})
    assert "error" not in packet, packet
    assert packet["ok"] is True
    assert [p["part"] for p in packet["parts"]] == ["nozzle"]

    # The agent leaves the branch: the human works from the default branch, as
    # a second client would.
    _on(service, HUMAN, service.branches.default_branch(proj), proj=proj)

    # --- the human half: everything through the routes the browser calls
    listing = http.get(f"/api/projects/{proj}/proposals").json()
    assert [p["id"] for p in listing["proposals"]] == [pid]
    assert listing["proposals"][0]["author_kind"] == "agent"
    assert listing["counts"]["open"] == 1

    detail = http.get(f"/api/projects/{proj}/proposals/{pid}").json()
    assert detail["proposal"]["source"] == "nozzle-thinner"
    assert detail["proposal"]["target"] == service.branches.default_branch(proj)
    assert [g["name"] for g in detail["gates"]][:2] == ["state", "approvals"]

    read = http.get(f"/api/projects/{proj}/proposals/{pid}/packet").json()
    section = read["parts"][0]
    assert section["script_diff"]["unified"]
    assert section["metrics"]["mass_g"]["delta"] < 0
    image = http.get(f"/api/projects/{proj}/proposals/{pid}/render/new/nozzle")
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"

    approved = http.post(f"/api/projects/{proj}/proposals/{pid}/review",
                         json={"verdict": "approve", "summary": "mass budget ok"})
    assert approved.status_code == 200, approved.text
    assert approved.json()["proposal"]["state"] == "approved"

    merged = http.post(f"/api/projects/{proj}/proposals/{pid}/merge", json={})
    assert merged.status_code == 200, merged.text
    body = merged.json()
    assert "error" not in body, body
    assert body["proposal"]["state"] == "merged"
    assert body["validation"]["ok"] is True
    assert body["proposal"]["merge"]["commit"] == body["commit"]

    # the source's edit really is on the target branch now
    assert "thinner wall for the mass budget" in \
        (dest / "parts" / "nozzle.py").read_text(encoding="utf-8")

    # --- FR13: every action attributed, agent and human distinguished
    actions = _actions(registry, proj, pid)
    by_action = dict(actions)
    assert by_action["created"] == "agent"
    assert by_action["packet_generated"] == "agent"
    assert by_action["reviewed"] == "human"
    assert by_action["merged"] == "human"
    assert [a for a, _kind in actions][-1] == "merged"
    assert all(kind in ("agent", "human") for _a, kind in actions)


def test_ac1_browser_half_evidence_is_recorded():
    """AC1 (browser half) — "a human reviews and merges it in the browser with
    zero terminal use" was driven for real in slice 5 (headless Chrome, a
    scratch projects dir, screenshots, zero console errors). This is the
    evidence check: it asserts the session is on the record, so the criterion
    has a named check that fails if the record is removed, without re-driving a
    browser from the test suite (the PRD-001 AC6 pattern).
    """
    entry = CHANGELOG / "0081-proposals-ui.md"
    assert entry.is_file(), "slice 5 changelog entry is missing"
    text = entry.read_text(encoding="utf-8")
    assert "AC1" in text
    for phrase in ("browser", "approve", "merge", "Console"):
        assert phrase.lower() in text.lower(), \
            f"browser evidence does not mention {phrase!r}"


# ------------------------------------------------------------------- AC2


@pytest.mark.slow
@pytest.mark.skipif(not (ROCKETRY / "project.json").is_file(),
                    reason="rocketry example not present")
@pytest.mark.timeout(900)
def test_ac2_packet_generates_warm_under_10s(stack, tmp_path):
    """AC2 — the packet for a nozzle wall-thickness change generates warm in
    under 10 s and carries all five kinds of evidence: the script diff, the
    PARAMS diff, metric deltas, before/after renders sharing ONE camera frame,
    and the kernel-computed geometric-diff volumes.
    """
    service, registry = stack
    proj, dest = _copy_rocketry(registry, tmp_path)

    locks.set_client_id(AGENT)
    assert "error" not in registry.call(
        "branch_create", {"project": proj, "name": "nozzle-thinner"})
    assert "error" not in registry.call(
        "branch_switch", {"project": proj, "name": "nozzle-thinner"})
    _thin_the_nozzle(registry, proj, dest)
    pid = _propose(registry, "nozzle-thinner", proj=proj,
                   title="Thin the nozzle wall to 2.6 mm")

    cold = registry.call("proposal_packet", {"project": proj, "id": pid})
    assert "error" not in cold, cold
    started = time.monotonic()
    packet = registry.call(
        "proposal_packet", {"project": proj, "id": pid, "regenerate": True})
    elapsed = time.monotonic() - started
    assert "error" not in packet, packet

    section = next(p for p in packet["parts"] if p["part"] == "nozzle")
    assert "@@" in section["script_diff"]["unified"]
    assert section["script_diff"]["added_lines"] > 0
    assert section["params_diff"]["changed"] == [
        {"name": "wall", "field": "value", "old": 3.0, "new": 2.6}]
    for key in ("volume_mm3", "mass_g", "area_mm2"):
        assert set(section["metrics"][key]) == {"old", "new", "delta", "pct"}
    assert section["metrics"]["mass_g"]["delta"] < 0
    assert section["metrics"]["center_of_mass"]["delta"]
    renders = section["renders"]
    assert renders["old"] and renders["new"]
    # identical camera framing is one shared frame, not two computed ones
    assert renders["frame"]["min"] and renders["frame"]["max"]
    assert (renders["width"], renders["height"]) == (640, 480)
    assert section["geom_diff"]["available"] is True
    assert section["geom_diff"]["removed_mm3"] > 0

    assert elapsed < 10.0, f"warm packet took {elapsed:.2f}s"


# ------------------------------------------------------------------- AC3


@pytest.mark.slow
def test_ac3_drilled_hole_reports_removed_volume(demo):
    """AC3 — drilling a 6 mm through hole in a 20 mm cube reports
    ``removed_mm3`` within 1 % of the analytic hole volume, adds nothing, and
    writes a parseable ACM1 diff solid for the viewport overlay."""
    service, registry = demo
    assert "error" not in _script(registry, "box", CUBE)
    assert "error" not in registry.call(
        "branch_create", {"project": "demo", "name": "hole"})
    _on(service, "agent_a", "hole")
    assert "error" not in _script(registry, "box", CUBE_HOLE)
    pid = _propose(registry, "hole")

    packet = registry.call("proposal_packet", {"project": "demo", "id": pid})
    assert "error" not in packet, packet

    diff = packet["parts"][0]["geom_diff"]
    assert diff["available"] is True and diff["unchanged"] is False
    assert diff["removed_mm3"] == pytest.approx(HOLE_MM3, rel=0.01)
    assert diff["added_mm3"] == 0.0
    assert diff["added_mesh"] is None
    assert diff["removed_mesh"] == \
        f"/api/projects/demo/proposals/{pid}/diff/box/removed.acm"

    # the overlay's payload: a real ACM1 mesh, served by the asset route
    mesh = service.proposals.store.asset_dir("demo", pid, "diff") / "box.removed.acm"
    assert mesh.read_bytes()[:4] == b"ACM1"
    assert len(acm.read(mesh)["indices"]) > 0
    app = create_app(service, registry, extra_allowed_hosts={"testserver"})
    http = TestClient(app, base_url="http://127.0.0.1")
    served = http.get(diff["removed_mesh"])
    assert served.status_code == 200
    assert served.content[:4] == b"ACM1"


def test_ac3_browser_overlay_evidence_is_recorded():
    """AC3 (browser half) — "the red overlay renders in the Geometry tab" was
    checked in a real browser in slice 5 (shot ``09-overlay``, with the
    measured 565.5 mm³ matching π·3²·20). Evidence check, not a re-drive."""
    entry = CHANGELOG / "0081-proposals-ui.md"
    assert entry.is_file(), "slice 5 changelog entry is missing"
    text = entry.read_text(encoding="utf-8")
    assert "AC3" in text
    for phrase in ("overlay", "removed", "Geometry"):
        assert phrase.lower() in text.lower(), \
            f"overlay evidence does not mention {phrase!r}"


# ------------------------------------------------------------------- AC4


class _CountingKernel:
    """Records every method that reaches the kernel (AC4's assertion)."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.methods: list[str] = []

    def request(self, method, params, **kwargs):
        self.methods.append(method)
        return self._inner.request(method, params, **kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


@pytest.mark.slow
def test_ac4_instance_move_does_no_per_part_kernel_work(demo, monkeypatch):
    """AC4 — a move-an-instance-only change produces assembly deltas and zero
    per-part kernel diff work.

    An instance move on its own produces **no part rows at all**, which would
    make "every part's short circuit fired" vacuously true. So the change also
    carries a manifest-only part edit (a relabel): that yields a real part row
    whose two content hashes match, and the row's ``geom_diff`` says
    ``unchanged`` while the kernel is never asked to build or diff anything.
    """
    service, registry = demo
    assert "error" not in registry.call(
        "set_assembly",
        {"project": "demo",
         "instances": [{"id": "box_1", "part": "box", "position": [0, 0, 0],
                        "rotation_deg": [0, 0, 0]}]})
    _on(service, "agent_a", "feat")
    assert "error" not in registry.call(
        "set_assembly",
        {"project": "demo",
         "instances": [{"id": "box_1", "part": "box", "position": [5, 0, 0],
                        "rotation_deg": [0, 0, 0]}]})
    assert "error" not in registry.call(
        "update_part_script", {"project": "demo", "part_id": "box",
                               "label": "Box v2"})
    pid = _propose(registry, "feat")

    counter = _CountingKernel(service.kernel)
    monkeypatch.setattr(service, "kernel", counter)
    packet = registry.call("proposal_packet", {"project": "demo", "id": pid})
    assert "error" not in packet, packet

    # FR5: the assembly delta is there...
    assembly = packet["assembly"]
    assert assembly["changed"] is True
    moved = assembly["instances_moved"]
    assert [i["id"] for i in moved] == ["box_1"]
    assert moved[0]["old"]["position"] == [0.0, 0.0, 0.0]
    assert moved[0]["new"]["position"] == [5.0, 0.0, 0.0]

    # ...and FR7's content-hash short circuit did the per-part work: a real
    # part row, marked unchanged, with no kernel call behind it.
    assert [p["part"] for p in packet["parts"]] == ["box"]
    section = packet["parts"][0]
    assert section["changed_by"] == ["manifest"]
    assert section["geom_diff"]["unchanged"] is True
    assert section["geom_diff"]["available"] is True
    assert (section["geom_diff"]["added_mm3"],
            section["geom_diff"]["removed_mm3"]) == (0.0, 0.0)
    assert not {"build", "build_reference", "geom_diff"} & set(counter.methods), \
        counter.methods


# ------------------------------------------------------------------- AC5


@pytest.mark.slow
def test_ac5_failed_validation_blocks_then_overrides(demo):
    """AC5 — a proposal whose merge validation fails is blocked by
    ``proposal_merge``; ``allow_invalid: true`` lands it, and the override is
    recorded in all three places: the audit log, the proposal, and the merge
    commit message."""
    service, registry = demo
    canonical = service.store.canonical_path_of("demo")
    _on(service, "agent_a", "feat")
    # written and snapshotted even though the rebuild fails
    _script(registry, "box", BROKEN_SCRIPT)
    pid = _propose(registry, "feat")
    _on(service, HUMAN, "master")
    # the target moves too, so the merge is a real two-parent merge with a
    # message to record the override in (a fast-forward has no commit of its own)
    assert "error" not in registry.call(
        "create_part", {"project": "demo", "part_id": "pin", "script": BOX_SCRIPT})
    assert "error" not in registry.call(
        "proposal_review", {"project": "demo", "id": pid, "verdict": "approve"})

    blocked = registry.call("proposal_merge", {"project": "demo", "id": pid})
    error = blocked["error"]
    assert error["type"] == "validation_error"
    validation = error["details"]["validation"]
    assert validation["ok"] is False and validation["blocked"] is True
    assert [f["part"] for f in validation["failures"]] == ["box"]
    assert error["details"]["proposal"] == pid
    # blocked means blocked: the proposal is untouched and nothing landed
    detail = registry.call("proposal_get", {"project": "demo", "id": pid})
    assert detail["proposal"]["state"] == "approved"
    assert detail["proposal"]["merge"] is None
    assert [e for e in detail["audit"]
            if e["action"] == "merge_attempted"][-1]["details"]["outcome"] \
        == "blocked"

    landed = registry.call(
        "proposal_merge", {"project": "demo", "id": pid, "allow_invalid": True})
    assert "error" not in landed, landed
    assert landed["proposal"]["state"] == "merged"
    assert landed["proposal"]["merge"]["allow_invalid"] is True
    assert landed["validation"]["ok"] is False
    assert landed["validation"]["blocked"] is False

    after = registry.call("proposal_get", {"project": "demo", "id": pid})
    override = [e for e in after["audit"] if e["action"] == "override"]
    assert override, [e["action"] for e in after["audit"]]
    assert override[-1]["actor_kind"] == "human"
    message = service.history._run(
        canonical, "log", "-1", "--pretty=%B", "master").stdout
    assert "Validation: FAILED" in message and "allow_invalid" in message


# ------------------------------------------------------------------- AC6


def test_ac6_self_approval_does_not_satisfy_policy(demo):
    """AC6 — under the default policy (``approvals_required: 1``,
    ``self_approve: false``) merging with no approval and merging with only the
    author's own approval are both ``conflict_error``s naming the policy, and
    ``allow_invalid`` — which is about the kernel's verdict on geometry — does
    not waive it. A second, non-author approval does."""
    _service, registry = demo
    pid = _propose(registry, "feat")          # author: chat:main (the agent)

    unapproved = registry.call("proposal_merge", {"project": "demo", "id": pid})
    error = unapproved["error"]
    assert error["type"] == "conflict_error"
    assert error["details"]["failing"] == "approvals"
    assert "1 approval required, 0 recorded" in error["message"]
    # the policy itself is named in the failing gate, not just in the prose
    approvals = next(g for g in error["details"]["gates"]
                     if g["name"] == "approvals")
    assert approvals["state"] == "fail"
    assert approvals["details"] == {"approvals_required": 1, "approvals": 0,
                                    "self_approve": False, "author": AGENT}

    # the author approves their own proposal: still zero counted approvals
    locks.set_client_id(AGENT)
    assert "error" not in registry.call(
        "proposal_review", {"project": "demo", "id": pid, "verdict": "approve"})
    self_approved = registry.call("proposal_merge", {"project": "demo", "id": pid})
    assert self_approved["error"]["details"]["failing"] == "approvals"
    assert self_approved["error"]["details"]["gates"]
    forced = registry.call(
        "proposal_merge", {"project": "demo", "id": pid, "allow_invalid": True})
    assert forced["error"]["type"] == "conflict_error"
    assert forced["error"]["details"]["failing"] == "approvals"
    assert registry.call("proposal_get", {"project": "demo", "id": pid})[
        "proposal"]["merge"] is None

    # a different identity's approval satisfies the policy
    locks.set_client_id(HUMAN)
    assert "error" not in registry.call(
        "proposal_review", {"project": "demo", "id": pid, "verdict": "approve"})
    merged = registry.call("proposal_merge", {"project": "demo", "id": pid})
    assert "error" not in merged, merged
    assert merged["proposal"]["state"] == "merged"


# ------------------------------------------------------------------- AC7


@pytest.mark.slow
def test_ac7_unbuildable_side_degrades_honestly(demo):
    """AC7 — an unbuildable source side yields a packet embedding the
    structured script error for that part, with the rest of the packet intact
    and ``ok: true``: a packet is evidence, and "the new side does not build"
    is the most important evidence there is."""
    _service, registry = demo
    _on(_service, "agent_a", "feat")
    _script(registry, "box", BROKEN_SCRIPT)
    pid = _propose(registry, "feat")

    packet = registry.call("proposal_packet", {"project": "demo", "id": pid})
    assert "error" not in packet, packet          # generation is not a failure
    assert packet["ok"] is True

    section = packet["parts"][0]
    assert section["build"]["old"]["ok"] is True
    assert section["build"]["new"]["ok"] is False
    error = section["build"]["new"]["error"]
    assert error["type"] and error["message"]
    assert error["details"]["traceback"] and error["details"]["line"]

    # the rest of the packet is intact
    assert section["script_diff"]["unified"]
    assert section["metrics"]["volume_mm3"]["old"] > 0
    assert section["metrics"]["volume_mm3"]["new"] is None
    assert section["renders"]["old"] and section["renders"]["new"] is None
    assert section["geom_diff"]["available"] is False
    assert packet["summary"]["parts_changed"] == 1


# ------------------------------------------------------------------- AC8


def test_ac8_second_client_sees_proposal_changed_live(demo):
    """AC8 — a second browser watching the WebSocket sees every proposal
    transition live: create, review and merge each publish
    ``proposal_changed`` with the post-state."""
    service, registry = demo
    app = create_app(service, registry, extra_allowed_hosts={"testserver"})
    http = TestClient(app, base_url="http://127.0.0.1")

    def _drain(ws, expected: int) -> list[dict]:
        seen: list[dict] = []
        for _ in range(40):
            event = ws.receive_json()
            if event["type"] == "proposal_changed":
                seen.append(event)
                if len(seen) == expected:
                    break
        return seen

    with http.websocket_connect("/ws") as ws:
        # the agent opens it (X-Agent-Id is the identity plumbing FR13 uses),
        # so the browser's approval is a second party's and satisfies the policy
        created = http.post("/api/projects/demo/proposals",
                            json={"source": "feat", "title": "Thinner wall"},
                            headers={"X-Agent-Id": AGENT})
        assert created.status_code == 200, created.text
        assert created.json()["proposal"]["author_kind"] == "agent"
        pid = created.json()["proposal"]["id"]
        assert _drain(ws, 1) == [{"type": "proposal_changed", "project": "demo",
                                  "id": pid, "state": "open",
                                  "reason": "created"}]

        reviewed = http.post(f"/api/projects/demo/proposals/{pid}/review",
                             json={"verdict": "approve"})
        assert reviewed.status_code == 200, reviewed.text
        assert _drain(ws, 1) == [{"type": "proposal_changed", "project": "demo",
                                  "id": pid, "state": "approved",
                                  "reason": "review"}]

        merged = http.post(f"/api/projects/demo/proposals/{pid}/merge", json={})
        assert merged.status_code == 200, merged.text
        assert _drain(ws, 1) == [{"type": "proposal_changed", "project": "demo",
                                  "id": pid, "state": "merged",
                                  "reason": "merged"}]


# ------------------------------------------------------------------- AC9


def test_ac9_project_restore_does_not_rewind_proposals(demo):
    """AC9 (FR3) — proposals are workflow metadata, not model state: restoring
    the project to a pre-proposal snapshot rewinds the model and leaves
    ``proposal.json`` and ``audit.jsonl`` byte-identical.

    (The other half — the full suite green, and no pre-existing test file
    edited — is a command, cited in slice 6's changelog: ``make test`` and
    ``git diff --name-status main -- tests/``.)
    """
    service, registry = demo
    canonical = service.store.canonical_path_of("demo")
    before = service.history.head(canonical)

    pid = _propose(registry, "feat")
    locks.set_client_id(HUMAN)
    assert "error" not in registry.call(
        "proposal_review", {"project": "demo", "id": pid, "verdict": "approve",
                            "summary": "ship it"})
    proposal_dir = service.proposals.store.dir_of("demo") / pid
    frozen = {name: (proposal_dir / name).read_bytes()
              for name in ("proposal.json", "audit.jsonl")}

    # a model change made after the proposal, so the restore has work to do
    assert "error" not in registry.call(
        "update_part_script",
        {"project": "demo", "part_id": "box", "script": BOX_V2_SCRIPT})
    restored = registry.call(
        "project_restore", {"project": "demo", "commit": before})
    assert "error" not in restored, restored

    assert service.store.read_script("demo", "box") == BOX_SCRIPT   # rewound
    assert {name: (proposal_dir / name).read_bytes()
            for name in frozen} == frozen                           # untouched
    detail = registry.call("proposal_get", {"project": "demo", "id": pid})
    assert detail["proposal"]["state"] == "approved"
    assert [e["action"] for e in detail["audit"]] == ["created", "reviewed"]
    # and the audit really is JSON lines, not a rewritten document
    lines = (proposal_dir / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["seq"] for line in lines] == [1, 2]
