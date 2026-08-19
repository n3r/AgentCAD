"""``release_finalize`` — the tag/finalize path and record immutability
(PRD-015 slice 4, FR9/FR12).

``release_finalize`` takes an ``in_review`` release whose proposal has been
approved, creates the immutable ``release/<rev>`` tag at the approved head,
registers the referrer in ``tags.json``, transitions the record to
``released`` (copying the approve reviews' principals into ``approvals``),
supersedes the immediately-prior released rev and emits ``release_changed``.
It is idempotent (a second call on a ``released`` rev is a no-op) and the
finalized record is append-only: re-finalizing a terminal rev is a
``conflict_error`` (FR12). The tag's tree is structurally immutable — you
cannot switch to a tag, only ``branch_create(from_ref=tag)`` then edit on the
new branch.

Pure git + manifest: no kernel calls in finalize. Skips without git.
"""

from __future__ import annotations

import shutil

import pytest

from agentcad.core import locks, releases
from agentcad.core.branches import pinned_tree_var
from agentcad.core.model import ConflictError, ValidationError
from agentcad.core.service import AgentCADService, EventBus
from agentcad.core.tools import build_registry

_GIT = [
    pytest.mark.integration,
    pytest.mark.portability,
    pytest.mark.skipif(shutil.which("git") is None, reason="git not found on PATH"),
]
pytestmark = _GIT + [pytest.mark.slow]


# A well-formed part whose specs pass at the default wall (green gate); driving
# ``wall`` below 2.0 mm turns the specs gate red (mirrors test_releases.py).
GATE_BOX = '''\
from build123d import *
from agentcad.toolkit.specs import check_mass, check_wall

PARAMS = {"size": {"default": 20.0, "min": 10.0, "max": 60.0, "unit": "mm",
                   "description": "outer edge"},
          "wall": {"default": 2.5, "min": 0.5, "max": 5.0, "unit": "mm",
                   "description": "wall thickness"}}

SPECS = [
    check_wall(min_mm=2.0, grid=4, requirement="ENG-014"),
    check_mass(max_g=500.0, requirement="SYS-042"),
]

def build(p):
    inner = p.size - 2 * p.wall
    return Box(p.size, p.size, p.size) - Box(inner, inner, inner)
'''


@pytest.fixture(autouse=True)
def _reset_context():
    cid = locks.client_id_var.set("local")
    pin = pinned_tree_var.set(None)
    yield
    locks.client_id_var.reset(cid)
    pinned_tree_var.reset(pin)


@pytest.fixture
def stack(kernel, tmp_path):
    service = AgentCADService(tmp_path / "projects", kernel, EventBus())
    registry = build_registry(service)
    assert getattr(service, "branches", None) is not None
    assert registry.get("release_finalize") is not None
    return service, registry


@pytest.fixture
def demo(stack):
    """'demo' with a spec-declaring part on master and a 'rel' release branch."""
    service, registry = stack
    assert "error" not in registry.call("create_project", {"name": "demo"})
    assert "error" not in registry.call(
        "create_part", {"project": "demo", "part_id": "box", "script": GATE_BOX})
    service.branches.create("demo", "rel")
    return service, registry


def _on(service, client: str, branch: str) -> None:
    locks.set_client_id(client)
    if service.branches.current("demo") != branch:
        service.branches.switch("demo", branch)


def _set_wall(service, registry, value: float) -> None:
    _on(service, "agent_a", "rel")
    result = registry.call("set_params", {"project": "demo", "part_id": "box",
                                          "values": {"wall": value}})
    assert "error" not in result, result


def _approve(service, registry, pid, reviewer: str = "agent_b") -> None:
    """Approve the release proposal as a DIFFERENT principal (self-approval does
    not count under the default policy)."""
    prev = locks.current_client_id()
    locks.set_client_id(reviewer)
    try:
        res = registry.call("proposal_review",
                            {"project": "demo", "id": pid, "verdict": "approve"})
        assert "error" not in res, res
    finally:
        locks.set_client_id(prev)


def _canonical(service):
    return service.store.canonical_path_of("demo")


def _tag_commit(service, name: str):
    return service.history.resolve_tag(_canonical(service), name)


def _drain(q) -> list[dict]:
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def _start_green(service, registry) -> str:
    _on(service, "agent_a", "rel")
    started = releases.release_start(service, "demo")
    assert started["gate"]["status"] == "green"
    assert started["status"] == "in_review"
    return started["proposal"]


# --------------------------------------------------------- 1. finalize happy path


def test_finalize_releases_tags_and_records_approvals(demo):
    service, registry = demo
    pid = _start_green(service, registry)
    _approve(service, registry, pid)

    q = service.bus.subscribe()
    _on(service, "agent_a", "rel")
    record = releases.release_finalize(service, "demo", "A")

    assert record["status"] == "released"
    assert record["tag"] == "release/a"
    # The tag exists in the repo and points at a real commit.
    assert _tag_commit(service, "release/a") is not None
    # The referrer is registered against the tag in tags.json.
    version = next(v for v in service.branches.versions("demo")
                   if v["name"] == "release/a")
    assert {"release": "A"} in version["referrers"]
    # Approvals carry the reviewer principal + the review ts (not the author).
    assert [a["principal"] for a in record["approvals"]] == ["agent_b"]
    assert record["approvals"][0]["ts"].endswith("Z")
    # release_changed fired.
    events = _drain(q)
    assert any(e.get("type") == "release_changed" and e.get("rev") == "A"
               and e.get("status") == "released" for e in events)
    # And the persisted record reflects the release.
    got = releases.get_release(service, "demo", "A")["release"]
    assert got["status"] == "released"
    assert got["tag"] == "release/a"


# --------------------------------------------------------------- 2. idempotency


def test_finalize_is_idempotent(demo):
    service, registry = demo
    pid = _start_green(service, registry)
    _approve(service, registry, pid)

    _on(service, "agent_a", "rel")
    first = releases.release_finalize(service, "demo", "A")
    commit = _tag_commit(service, "release/a")

    second = releases.release_finalize(service, "demo", "A")
    assert second["status"] == "released"
    assert second["tag"] == first["tag"]
    # No duplicate tag, same commit.
    assert _tag_commit(service, "release/a") == commit
    tags = [v["name"] for v in service.branches.versions("demo")]
    assert tags.count("release/a") == 1
    # The referrer was not appended twice.
    version = next(v for v in service.branches.versions("demo")
                   if v["name"] == "release/a")
    assert version["referrers"].count({"release": "A"}) == 1


# --------------------------------------------------- 3. approval is required


def test_finalize_before_approval_is_refused_and_makes_no_tag(demo):
    service, registry = demo
    _start_green(service, registry)  # in_review, but NOT approved

    _on(service, "agent_a", "rel")
    with pytest.raises(ConflictError):
        releases.release_finalize(service, "demo", "A")
    # No tag was created.
    assert _tag_commit(service, "release/a") is None
    assert releases.get_release(service, "demo", "A")["release"]["status"] \
        == "in_review"


def test_finalize_a_draft_reports_the_gate_did_not_pass(demo):
    service, registry = demo
    _set_wall(service, registry, 1.0)          # red specs gate
    _on(service, "agent_a", "rel")
    started = releases.release_start(service, "demo")
    assert started["status"] == "draft"

    with pytest.raises(ValidationError):
        releases.release_finalize(service, "demo", "A")
    assert _tag_commit(service, "release/a") is None


# --------------------------------------------------------------- 4. supersede


def test_finalizing_the_next_rev_supersedes_the_prior(demo):
    service, registry = demo

    pid_a = _start_green(service, registry)
    _approve(service, registry, pid_a)
    _on(service, "agent_a", "rel")
    releases.release_finalize(service, "demo", "A")
    # Close A's proposal so a second release can be cut from the same pair.
    service.proposals.update("demo", pid_a, state="closed")

    pid_b = _start_green(service, registry)
    _approve(service, registry, pid_b)
    _on(service, "agent_a", "rel")
    rec_b = releases.release_finalize(service, "demo", "B")

    assert rec_b["status"] == "released"
    assert releases.get_release(service, "demo", "A")["release"]["status"] \
        == "superseded"
    assert releases.get_release(service, "demo", "B")["release"]["status"] \
        == "released"


# ------------------------------------------------- 5. immutability (FR12 / AC5)


def test_ensure_mutable_rejects_terminal_records():
    for status in ("released", "superseded"):
        with pytest.raises(ConflictError):
            releases._ensure_mutable({"rev": "A", "status": status})
    # A draft or in_review record is mutable (does not raise).
    releases._ensure_mutable({"rev": "A", "status": "in_review"})
    releases._ensure_mutable({"rev": "A", "status": "draft"})


def test_refinalizing_a_superseded_rev_is_a_conflict(demo):
    service, registry = demo

    pid_a = _start_green(service, registry)
    _approve(service, registry, pid_a)
    _on(service, "agent_a", "rel")
    releases.release_finalize(service, "demo", "A")
    service.proposals.update("demo", pid_a, state="closed")

    pid_b = _start_green(service, registry)
    _approve(service, registry, pid_b)
    _on(service, "agent_a", "rel")
    releases.release_finalize(service, "demo", "B")   # A -> superseded

    # A is terminal now: re-finalizing it (a record mutation) is refused.
    result = registry.call("release_finalize", {"project": "demo", "rev": "A"})
    assert result["error"]["type"] == "conflict_error"


def test_a_released_tag_is_only_editable_via_a_branch_off_it(demo):
    service, registry = demo
    pid = _start_green(service, registry)
    _approve(service, registry, pid)
    _on(service, "agent_a", "rel")
    releases.release_finalize(service, "demo", "A")

    # You cannot switch to a tag (it is not a branch) — the only way to evolve
    # a released state is to branch off the tag, then edit on that branch.
    with pytest.raises(Exception):
        service.branches.switch("demo", "release/a")

    service.branches.create("demo", "hotfix", from_ref="release/a")
    _on(service, "agent_a", "hotfix")
    edited = registry.call("set_params", {"project": "demo", "part_id": "box",
                                          "values": {"wall": 3.0}})
    assert "error" not in edited, edited


# --------------------------------------------------------- 6. the tool surface


def test_release_finalize_over_the_registry(demo):
    service, registry = demo
    pid = _start_green(service, registry)
    _approve(service, registry, pid)
    _on(service, "agent_a", "rel")

    result = registry.call("release_finalize", {"project": "demo", "rev": "A"})
    assert "error" not in result, result
    assert result["status"] == "released"
    assert result["tag"] == "release/a"
