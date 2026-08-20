"""Release records, the revision state machine and the release gate (PRD-015
slice 3, FR6-8).

``core/releases.py`` is pure Python and makes **no kernel calls of its own**:
``release_start`` opens a ``release``-kind PRD-002 proposal and reads the specs
and checks gates that provider list already evaluated for free, then adds its
own zero-kernel release checks (working tree clean, sub-assembly refs pinned,
drawings regenerable). A red gate leaves the release ``draft`` with the failing
check named; an explicit ``waive`` records a durable waiver and proceeds.

Everything here resolves branches through git, so the module carries
``integration`` + ``portability`` and skips without git. Section 8 is a pure
``manifest_merge`` unit test (no git) that still lives here because it pins the
per-rev merge granularity the record shape depends on.
"""

from __future__ import annotations

import shutil

import pytest

from agentcad.core import locks, releases
from agentcad.core.branches import pinned_tree_var
from agentcad.core.manifest_merge import merge_manifests
from agentcad.core.model import NotFoundError, ValidationError
from agentcad.core.service import AgentCADService, EventBus
from agentcad.core.tools import build_registry

_GIT = [
    pytest.mark.integration,
    pytest.mark.portability,
    pytest.mark.skipif(shutil.which("git") is None, reason="git not found on PATH"),
]
pytestmark = _GIT + [pytest.mark.slow]


# A hollow box whose ``wall`` parameter drives ``check_wall`` red below 2.0 mm —
# the same shape ``test_specs_gate`` uses to make one branch's specs fail.
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
    """The real service + registry (NOT make_test_service): the snapshot hook
    commits each mutation, so the working tree is clean after a tool call — the
    state the release gate's clean-tree check expects."""
    service = AgentCADService(tmp_path / "projects", kernel, EventBus())
    registry = build_registry(service)
    assert getattr(service, "branches", None) is not None
    assert registry.get("release_start") is not None
    return service, registry


@pytest.fixture
def demo(stack):
    """'demo' with a spec-declaring part on master and a 'rel' release branch
    off it (a release is cut from a branch other than the default)."""
    service, registry = stack
    assert "error" not in registry.call("create_project", {"name": "demo"})
    assert "error" not in registry.call(
        "create_part", {"project": "demo", "part_id": "box",
                        "script": GATE_BOX})
    service.branches.create("demo", "rel")
    return service, registry


def _on(service, client: str, branch: str) -> None:
    locks.set_client_id(client)
    if service.branches.current("demo") != branch:
        service.branches.switch("demo", branch)


def _set_wall(service, registry, value: float, branch: str = "rel") -> None:
    _on(service, "agent_a", branch)
    result = registry.call("set_params", {"project": "demo", "part_id": "box",
                                          "values": {"wall": value}})
    assert "error" not in result, result


def _check(gate: dict, name: str) -> dict:
    return next(c for c in gate["checks"] if c["name"] == name)


# ---------------------------------------------------- 1. a green release start


def test_release_start_on_a_clean_branch_drafts_rev_a_in_review(demo):
    service, _registry = demo
    _on(service, "agent_a", "rel")

    result = releases.release_start(service, "demo")

    assert result["rev"] == "A"
    assert result["status"] == "in_review"
    assert result["gate"]["status"] == "green"
    assert _check(result["gate"], "specs")["status"] == "pass"
    assert _check(result["gate"], "checks")["status"] == "skip"
    assert _check(result["gate"], "working_tree_clean")["status"] == "pass"
    # The record is persisted and reachable.
    record = releases.get_release(service, "demo", "A")["release"]
    assert record["rev"] == "A"
    assert record["status"] == "in_review"
    assert record["proposal"] == result["proposal"]
    assert "waiver" not in record


def test_release_start_opens_a_release_kind_proposal(demo):
    service, registry = demo
    _on(service, "agent_a", "rel")

    result = releases.release_start(service, "demo")

    detail = registry.call("proposal_get",
                           {"project": "demo", "id": result["proposal"]})
    assert detail["proposal"]["kind"] == "release"
    # And the list-view row carries it too.
    rows = registry.call("proposal_list", {"project": "demo"})["proposals"]
    assert any(r["kind"] == "release" for r in rows)


def test_a_release_must_be_cut_from_a_non_default_branch(demo):
    service, _registry = demo
    _on(service, "browser", "master")           # the default branch
    with pytest.raises(ValidationError):
        releases.release_start(service, "demo")


# ------------------------------------------------ 2. a red gate + a waiver (AC4)


def test_a_failing_spec_leaves_the_release_draft_and_names_the_check(demo):
    service, registry = demo
    _set_wall(service, registry, 1.0)           # feat/rel spec goes red

    _on(service, "agent_a", "rel")
    result = releases.release_start(service, "demo")

    assert result["gate"]["status"] == "red"
    assert result["status"] == "draft"
    specs = _check(result["gate"], "specs")
    assert specs["status"] == "fail"
    failing_ids = [f["id"] for f in specs["gate"]["details"]["failures"]]
    assert "box:wall_min" in failing_ids
    # The record stays draft (not released) and the report is on it.
    record = releases.get_release(service, "demo", "A")["release"]
    assert record["status"] == "draft"
    assert record["gate"]["status"] == "red"


def test_a_waiver_proceeds_past_a_red_gate_and_is_recorded(demo):
    service, registry = demo
    _set_wall(service, registry, 1.0)

    _on(service, "agent_a", "rel")
    result = releases.release_start(
        service, "demo", notes="ship it", waive={"reason": "cosmetic only"})

    assert result["gate"]["status"] == "green"     # the waiver unblocks it
    assert result["status"] == "in_review"
    specs = _check(result["gate"], "specs")
    assert specs["status"] == "fail" and specs["waived"] is True
    # The waiver is a durable, attributed object visible from get_release.
    release = releases.get_release(service, "demo", "A")["release"]
    assert release["waiver"]["reason"] == "cosmetic only"
    assert release["waiver"]["principal"] == "agent_a"
    assert release["waiver"]["ts"].endswith("Z")


def test_a_waiver_needs_a_reason(demo):
    service, _registry = demo
    _on(service, "agent_a", "rel")
    with pytest.raises(ValidationError):
        releases.release_start(service, "demo", waive={})


# ------------------------------------------------ 3. rev auto-sequence per proj


def test_rev_auto_sequences_a_then_b(demo):
    service, _registry = demo
    _on(service, "agent_a", "rel")

    first = releases.release_start(service, "demo")
    assert first["rev"] == "A"
    # Close the first proposal so a second start on the same pair is allowed;
    # the record it wrote is still in the branch manifest, so the next rev is B.
    service.proposals.update("demo", first["proposal"], state="closed")

    second = releases.release_start(service, "demo")
    assert second["rev"] == "B"

    revs = [r["rev"] for r in releases.list_releases(service, "demo")["releases"]]
    assert revs == ["A", "B"]


def test_next_rev_helper_rolls_over_z():
    assert releases._next_rev({}) == "A"
    assert releases._next_rev({"A": {}}) == "B"
    assert releases._next_rev({"A": {}, "Z": {}}) == "AA"
    assert releases._next_rev({"AA": {}, "AZ": {}}) == "BA"


# --------------------------------------------------- 4. list / get round-trip


def test_get_release_round_trips_the_record_and_its_gate(demo):
    service, _registry = demo
    _on(service, "agent_a", "rel")
    releases.release_start(service, "demo")

    got = releases.get_release(service, "demo", "A")
    assert got["release"]["rev"] == "A"
    assert got["gate"]["status"] == "green"
    assert got["gate"] is got["release"]["gate"]


def test_get_release_unknown_rev_is_not_found(demo):
    service, _registry = demo
    _on(service, "agent_a", "rel")
    releases.release_start(service, "demo")
    with pytest.raises(NotFoundError):
        releases.get_release(service, "demo", "Z")


# ---------------------------------------------------------- 5. the tool surface


def test_release_start_and_reads_over_the_registry(demo):
    service, registry = demo
    _on(service, "agent_a", "rel")

    started = registry.call("release_start", {"project": "demo"})
    assert "error" not in started, started
    assert started["rev"] == "A"

    listed = registry.call("list_releases", {"project": "demo"})
    assert [r["rev"] for r in listed["releases"]] == ["A"]

    got = registry.call("get_release", {"project": "demo", "rev": "A"})
    assert got["release"]["status"] == "in_review"


# ---------------------------------------- 6. proposal kind is additive (Dec 9)


def test_proposal_kind_defaults_to_change(demo):
    service, _registry = demo
    service.branches.create("demo", "feat")
    _on(service, "chat:main", "master")
    created = service.proposals.create("demo", "feat", title="ordinary work")
    assert created["proposal"]["kind"] == "change"


def test_unknown_proposal_kind_is_refused(demo):
    service, _registry = demo
    service.branches.create("demo", "feat")
    with pytest.raises(ValidationError):
        service.proposals.create("demo", "feat", title="x", kind="banana")


# ----------------------------------------- 7. manifest_merge: releases per-rev


def _manifest(releases_map: dict) -> dict:
    return {"schema_version": 3, "name": "demo", "units": "mm", "parts": [],
            "assembly": {"instances": []}, "releases": releases_map}


def test_two_branches_releasing_different_revs_merge_clean():
    base = _manifest({})
    ours = _manifest({"A": {"rev": "A", "status": "released"}})
    theirs = _manifest({"B": {"rev": "B", "status": "in_review"}})

    merged, conflicts = merge_manifests(base, ours, theirs)

    assert conflicts == []
    assert set(merged["releases"]) == {"A", "B"}
    assert merged["releases"]["A"]["status"] == "released"
    assert merged["releases"]["B"]["status"] == "in_review"


def test_a_same_rev_edit_on_both_sides_conflicts_on_the_whole_record():
    base = _manifest({"A": {"rev": "A", "status": "draft"}})
    ours = _manifest({"A": {"rev": "A", "status": "released"}})
    theirs = _manifest({"A": {"rev": "A", "status": "superseded"}})

    _merged, conflicts = merge_manifests(base, ours, theirs)

    assert [c["key"] for c in conflicts] == ["releases.A"]
