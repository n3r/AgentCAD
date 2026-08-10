"""Change proposals: store, lifecycle, audit, policy and gates (PRD-002 slice 1).

The core semantics only — no tools, no routes, no review packet (slices 2 and
4). Everything here resolves branches through git and asserts the sidecar's
durability guarantee, so the module carries ``integration`` + ``portability``
and skips without git; almost nothing here needs geometry, because a proposal
is about a branch pair, not about a shape.

Sections: 1. store and identity · 2. the state machine · 3. creation rules ·
4. attribution and the audit log · 5. durability · 6. policy and gates.
"""

from __future__ import annotations

import json
import shutil

import pytest

from agentcad.core import locks
from agentcad.core.branches import pinned_tree_var
from agentcad.core.model import ConflictError, NotFoundError, ValidationError
from agentcad.core.proposals import (
    ACTIVE,
    DEFAULT_POLICY,
    STATES,
    TERMINAL,
    ProposalManager,
    ProposalStore,
    actor_kind,
)
from agentcad.core.service import AgentCADService, EventBus
from agentcad.core.tools import build_registry

from .conftest import BOX_SCRIPT

_GIT = [
    pytest.mark.integration,
    pytest.mark.portability,
    pytest.mark.skipif(shutil.which("git") is None, reason="git not found on PATH"),
]
pytestmark = _GIT


@pytest.fixture(autouse=True)
def _reset_context():
    """Identity and the merge pin are ContextVars: rebind them per test so one
    test's set_client_id can never leak into the next."""
    cid = locks.client_id_var.set("local")
    pin = pinned_tree_var.set(None)
    yield
    locks.client_id_var.reset(cid)
    pinned_tree_var.reset(pin)


@pytest.fixture
def stack(kernel, tmp_path):
    """The real service + registry (NOT make_test_service, which disables the
    snapshot hook): the versioning pack installs ``service.branches``."""
    service = AgentCADService(tmp_path / "projects", kernel, EventBus())
    registry = build_registry(service)
    assert getattr(service, "branches", None) is not None
    return service, registry


@pytest.fixture
def demo(stack):
    """A project with a 'feat' branch and no parts — proposals are branch-pair
    objects, so the kernel is not involved."""
    service, registry = stack
    assert "error" not in registry.call("create_project", {"name": "demo"})
    service.branches.create("demo", "feat")
    return service, registry, ProposalManager(service)


def _create(manager, **kwargs) -> dict:
    payload = {"source": "feat", "title": "Thinner wall"}
    payload.update(kwargs)
    return manager.create("demo", **payload)["proposal"]


def _move_source_head(service, text: str = "note\n") -> str:
    """Commit something on 'feat' so its head moves (no geometry involved)."""
    tree = service.branches.tree_of("demo", "feat")
    (tree / "notes.txt").write_text(text, encoding="utf-8")
    service.history.snapshot(tree, "note")
    canonical = service.store.canonical_path_of("demo")
    return service.history.resolve_branch(canonical, "feat")


# ------------------------------------------------- 1. store and identity


def test_the_store_lives_in_the_prd001_sidecar(demo):
    service, _registry, manager = demo
    canonical = service.store.canonical_path_of("demo")

    assert manager.store.dir_of("demo") == (
        canonical / ".history" / "agentcad" / "proposals"
    )
    proposal = _create(manager)
    assert (manager.store.dir_of("demo") / "1" / "proposal.json").is_file()
    assert (manager.store.dir_of("demo") / "1" / "audit.jsonl").is_file()
    assert manager.store.packet_path("demo", proposal["id"]).name == "packet.json"
    for kind in ("renders", "diff"):
        assert manager.store.asset_dir("demo", "1", kind).name == kind


def test_ids_are_decimal_strings_from_one_and_are_never_reused(demo):
    service, _registry, manager = demo
    ids = []
    for index in range(3):
        proposal = _create(manager, title=f"p{index}")
        ids.append(proposal["id"])
        manager.update("demo", proposal["id"], state="closed")
    assert ids == ["1", "2", "3"]

    # A directory removed by hand must not hand its id to the next proposal.
    shutil.rmtree(manager.store.dir_of("demo") / "3")
    assert _create(manager, title="p3")["id"] == "4"


def test_the_index_is_a_cache_rebuilt_from_the_directories(demo):
    _service, _registry, manager = demo
    first = _create(manager)
    manager.update("demo", first["id"], state="closed")
    second = _create(manager, title="second")
    index = manager.store.dir_of("demo") / "index.json"

    index.unlink()
    assert [p["id"] for p in manager.store.list("demo")] == ["1", "2"]
    assert index.is_file()  # rebuilt on read
    assert manager.get("demo", second["id"])["proposal"]["title"] == "second"

    index.write_text("{not json", encoding="utf-8")
    assert [p["id"] for p in manager.store.list("demo")] == ["1", "2"]
    # next_id still only ever increments
    manager.update("demo", second["id"], state="closed")
    assert _create(manager, title="third")["id"] == "3"


def test_an_unknown_proposal_is_a_notfound_error(demo):
    _service, _registry, manager = demo
    with pytest.raises(NotFoundError):
        manager.get("demo", "7")
    with pytest.raises(NotFoundError):
        manager.store.load("demo", "../../etc/passwd")


# ------------------------------------------------------ 2. the state machine

_LEGAL = [
    ("draft", "open"), ("draft", "closed"),
    ("open", "approved"), ("open", "changes_requested"),
    ("open", "closed"), ("open", "merged"),
    ("approved", "changes_requested"), ("approved", "open"),
    ("approved", "closed"), ("approved", "merged"),
    ("changes_requested", "open"), ("changes_requested", "approved"),
    ("changes_requested", "closed"), ("changes_requested", "merged"),
    ("closed", "open"),
]


def test_the_state_constants_match_the_design(demo):
    assert STATES == ("draft", "open", "approved", "changes_requested",
                      "merged", "closed")
    assert TERMINAL == ("merged", "closed")
    assert ACTIVE == ("draft", "open", "approved", "changes_requested")
    assert DEFAULT_POLICY == {"approvals_required": 1, "self_approve": False}


def test_every_legal_transition_in_the_table_succeeds(demo):
    _service, _registry, manager = demo
    proposal = _create(manager, draft=True)
    for origin, target in _LEGAL:
        proposal["state"] = origin
        manager.store.save("demo", proposal)
        after = manager.transition(proposal, target, action="state_changed")
        assert after["state"] == target, (origin, target)
        assert manager.store.load("demo", proposal["id"])["state"] == origin
        manager.store.save("demo", after)


@pytest.mark.parametrize(
    "origin,target", [("merged", "open"), ("draft", "approved"),
                      ("draft", "merged"), ("merged", "closed")]
)
def test_an_illegal_transition_is_a_validation_error(demo, origin, target):
    _service, _registry, manager = demo
    proposal = _create(manager, draft=True)
    proposal["state"] = origin
    manager.store.save("demo", proposal)

    with pytest.raises(ValidationError) as excinfo:
        manager.transition(proposal, target, action="state_changed")
    details = excinfo.value.details
    assert details["from"] == origin
    assert details["to"] == target
    assert isinstance(details["allowed"], list)
    assert target not in details["allowed"]
    # nothing was written: neither the state nor a phantom audit entry
    assert manager.store.load("demo", proposal["id"])["state"] == origin
    assert [e["action"] for e in manager.store.audit("demo", proposal["id"])] \
        == ["created"]


def test_update_only_drives_the_transitions_it_owns(demo):
    _service, _registry, manager = demo
    proposal = _create(manager, draft=True)
    pid = proposal["id"]

    for state in ("approved", "changes_requested", "merged", "draft"):
        with pytest.raises(ValidationError) as excinfo:
            manager.update("demo", pid, state=state)
        assert excinfo.value.details["to"] == state
    assert manager.update("demo", pid, state="open")["proposal"]["state"] == "open"


def test_reviewing_a_closed_proposal_is_a_validation_error(demo):
    _service, _registry, manager = demo
    proposal = _create(manager)
    pid = proposal["id"]
    manager.update("demo", pid, state="closed")

    for verdict in ("approve", "request_changes", "comment"):
        with pytest.raises(ValidationError) as excinfo:
            manager.review("demo", pid, verdict)
        assert excinfo.value.details["from"] == "closed"
    assert manager.get("demo", pid)["proposal"]["reviews"] == []


def test_a_comment_review_records_without_changing_state(demo):
    _service, _registry, manager = demo
    pid = _create(manager)["id"]

    result = manager.review("demo", pid, "comment", summary="looks plausible")
    assert result["proposal"]["state"] == "open"
    assert result["proposal"]["reviews"][-1]["verdict"] == "comment"
    assert [e["action"] for e in manager.get("demo", pid)["audit"]] \
        == ["created", "reviewed"]


def test_an_unknown_verdict_is_a_validation_error(demo):
    _service, _registry, manager = demo
    pid = _create(manager)["id"]
    with pytest.raises(ValidationError):
        manager.review("demo", pid, "lgtm")


# --------------------------------------------------------- 3. creation rules


def test_a_second_active_proposal_for_the_same_pair_is_a_conflict(demo):
    _service, _registry, manager = demo
    first = _create(manager)

    for state in ACTIVE:
        stored = manager.store.load("demo", first["id"])
        stored["state"] = state
        manager.store.save("demo", stored)
        with pytest.raises(ConflictError) as excinfo:
            _create(manager, title="again")
        assert excinfo.value.details["existing_id"] == first["id"]
        assert excinfo.value.details["source"] == "feat"

    stored = manager.store.load("demo", first["id"])
    stored["state"] = "merged"
    manager.store.save("demo", stored)
    assert _create(manager, title="again")["id"] == "2"


def test_a_closed_proposal_does_not_block_a_new_one(demo):
    _service, _registry, manager = demo
    first = _create(manager)
    manager.update("demo", first["id"], state="closed")
    assert _create(manager, title="again")["id"] == "2"


def test_target_defaults_to_the_project_default_branch(demo):
    service, _registry, manager = demo
    default = service.branches.default_branch("demo")
    service.branches.create("demo", "other")
    # The caller sits on a NON-default branch: the target must not follow it.
    service.branches.switch("demo", "other")
    assert service.branches.current("demo") == "other"

    proposal = _create(manager)
    assert proposal["target"] == default
    assert proposal["source"] == "feat"


def test_source_equal_to_target_is_a_validation_error(demo):
    service, _registry, manager = demo
    default = service.branches.default_branch("demo")
    with pytest.raises(ValidationError):
        manager.create("demo", default, target=default, title="x")


def test_an_unknown_source_branch_is_a_notfound_error(demo):
    _service, _registry, manager = demo
    with pytest.raises(NotFoundError):
        manager.create("demo", "ghost", title="x")
    with pytest.raises(NotFoundError):
        manager.create("demo", "feat", target="ghost", title="x")


def test_a_tag_named_like_a_branch_does_not_answer_for_it(demo):
    service, _registry, manager = demo
    service.branches.tag("demo", "shipped", "the shop revision")
    with pytest.raises(NotFoundError):
        manager.create("demo", "shipped", title="x")


def test_list_filters_by_state_and_counts_every_state(demo):
    _service, _registry, manager = demo
    first = _create(manager)
    manager.update("demo", first["id"], state="closed")
    _create(manager, title="second")

    listing = manager.list("demo")
    assert [p["id"] for p in listing["proposals"]] == ["1", "2"]
    assert listing["counts"]["open"] == 1
    assert listing["counts"]["closed"] == 1
    assert set(listing["counts"]) == set(STATES)
    assert [p["id"] for p in manager.list("demo", state="closed")["proposals"]] \
        == ["1"]
    with pytest.raises(ValidationError):
        manager.list("demo", state="bogus")


def test_create_publishes_proposal_changed(demo):
    service, _registry, manager = demo
    queue_ = service.bus.subscribe()
    proposal = _create(manager)
    events = []
    while not queue_.empty():
        events.append(queue_.get_nowait())
    published = [e for e in events if e["type"] == "proposal_changed"]
    assert published == [{"type": "proposal_changed", "project": "demo",
                          "id": proposal["id"], "state": "open",
                          "reason": "created"}]


# ------------------------------------------- 4. attribution and the audit log


def test_actor_kind_is_human_only_for_the_browser():
    assert actor_kind("browser") == "human"
    assert actor_kind("browser:2") == "human"
    for identity in ("chat", "chat:main", "mcp", "agent_a", "local", ""):
        assert actor_kind(identity) == "agent", identity


def test_every_action_is_attributed_to_the_calling_client(demo):
    _service, _registry, manager = demo
    locks.set_client_id("chat:main")
    proposal = _create(manager)
    pid = proposal["id"]
    assert proposal["author"] == "chat:main"
    assert proposal["author_kind"] == "agent"

    locks.set_client_id("browser")
    manager.review("demo", pid, "approve", summary="ship it")
    review = manager.get("demo", pid)["proposal"]["reviews"][-1]
    assert (review["actor"], review["actor_kind"]) == ("browser", "human")

    audit = manager.get("demo", pid)["audit"]
    assert [(e["action"], e["actor"], e["actor_kind"]) for e in audit] == [
        ("created", "chat:main", "agent"),
        ("reviewed", "browser", "human"),
    ]


def test_the_audit_log_is_append_only_and_ordered(demo):
    _service, _registry, manager = demo
    pid = _create(manager, draft=True)["id"]
    manager.update("demo", pid, title="Thin the nozzle wall")
    manager.update("demo", pid, state="open")
    manager.review("demo", pid, "comment")
    manager.review("demo", pid, "approve")
    manager.review("demo", pid, "request_changes")
    manager.update("demo", pid, state="open")
    manager.review("demo", pid, "approve")
    manager.update("demo", pid, state="closed")
    manager.update("demo", pid, state="open")

    path = manager.store.dir_of("demo") / pid / "audit.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 10
    entries = [json.loads(line) for line in lines]
    assert [e["seq"] for e in entries] == list(range(1, 11))
    assert [e["action"] for e in entries] == [
        "created", "updated", "state_changed", "reviewed", "reviewed",
        "reviewed", "state_changed", "reviewed", "closed", "reopened",
    ]
    assert manager.store.audit("demo", pid) == entries

    # There is no public method that edits or removes an entry (FR14).
    api = {name for name in dir(manager.store)
           if not name.startswith("_") and "audit" in name}
    assert api == {"append_audit", "audit"}


def test_a_corrupt_audit_line_is_skipped_not_raised(demo):
    _service, _registry, manager = demo
    pid = _create(manager)["id"]
    path = manager.store.dir_of("demo") / pid / "audit.jsonl"
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("{half a line\n")

    assert [e["action"] for e in manager.store.audit("demo", pid)] == ["created"]
    manager.review("demo", pid, "approve")
    assert [e["action"] for e in manager.store.audit("demo", pid)] \
        == ["created", "reviewed"]


# ------------------------------------------------------------ 5. durability


def test_proposals_are_invisible_to_the_working_tree(demo):
    service, _registry, manager = demo
    canonical = service.store.canonical_path_of("demo")
    _create(manager)

    status = service.history._run(canonical, "status", "--porcelain")
    assert status.stdout.strip() == ""


def test_project_restore_does_not_rewind_a_proposal(demo):
    service, registry, manager = demo
    canonical = service.store.canonical_path_of("demo")
    before = service.history.head(canonical)

    created = registry.call(
        "create_part", {"project": "demo", "part_id": "box", "script": BOX_SCRIPT}
    )
    assert "error" not in created, created
    pid = _create(manager)["id"]
    manager.review("demo", pid, "approve")
    proposal_path = manager.store.dir_of("demo") / pid / "proposal.json"
    audit_path = manager.store.dir_of("demo") / pid / "audit.jsonl"
    proposal_bytes = proposal_path.read_bytes()
    audit_bytes = audit_path.read_bytes()

    restored = registry.call(
        "project_restore", {"project": "demo", "commit": before}
    )
    assert "error" not in restored, restored
    # the manifest really did rewind (project_restore overlays tracked bytes)
    assert registry.call("get_project", {"project": "demo"})["parts"] == []
    assert proposal_path.read_bytes() == proposal_bytes
    assert audit_path.read_bytes() == audit_bytes
    assert manager.get("demo", pid)["proposal"]["state"] == "approved"


# ------------------------------------------------------- 6. policy and gates


def _gate(gates: list[dict], name: str) -> dict:
    match = [g for g in gates if g["name"] == name]
    assert match, f"no {name!r} gate in {[g['name'] for g in gates]}"
    return match[0]


def _write_policy(manager, **policy) -> None:
    path = manager.store.dir_of("demo") / "policy.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(policy), encoding="utf-8")


def test_default_policy_requires_one_non_author_approval(demo):
    _service, _registry, manager = demo
    assert manager.store.policy("demo") == DEFAULT_POLICY

    locks.set_client_id("chat:main")
    pid = _create(manager)["id"]
    gates = manager.get("demo", pid)["gates"]
    approvals = _gate(gates, "approvals")
    assert approvals["state"] == "fail"
    assert approvals["details"] == {"approvals_required": 1, "approvals": 0,
                                    "self_approve": False, "author": "chat:main"}

    # the author's own approval does not count
    manager.review("demo", pid, "approve")
    assert _gate(manager.get("demo", pid)["gates"], "approvals")["state"] == "fail"

    locks.set_client_id("browser")
    result = manager.review("demo", pid, "approve")
    assert _gate(result["gates"], "approvals")["state"] == "pass"


def test_policy_json_overrides_the_defaults(demo):
    _service, _registry, manager = demo
    locks.set_client_id("chat:main")
    pid = _create(manager)["id"]

    _write_policy(manager, approvals_required=0)
    assert manager.store.policy("demo")["approvals_required"] == 0
    assert _gate(manager.get("demo", pid)["gates"], "approvals")["state"] == "pass"

    _write_policy(manager, approvals_required=1, self_approve=True)
    manager.review("demo", pid, "approve")  # still 'chat:main', the author
    assert _gate(manager.get("demo", pid)["gates"], "approvals")["state"] == "pass"


def test_the_latest_verdict_per_actor_is_the_one_that_counts(demo):
    _service, _registry, manager = demo
    locks.set_client_id("chat:main")
    pid = _create(manager)["id"]

    locks.set_client_id("browser")
    manager.review("demo", pid, "approve")
    assert _gate(manager.get("demo", pid)["gates"], "approvals")["state"] == "pass"

    manager.review("demo", pid, "request_changes")
    gates = manager.get("demo", pid)["gates"]
    assert _gate(gates, "approvals")["state"] == "fail"
    assert _gate(gates, "state")["state"] == "fail"
    assert manager.get("demo", pid)["proposal"]["state"] == "changes_requested"

    # the author re-requests review: the state gate clears again
    manager.update("demo", pid, state="open")
    assert _gate(manager.get("demo", pid)["gates"], "state")["state"] == "pass"


def test_specs_and_checks_are_skipped_with_no_providers(demo):
    service, _registry, manager = demo
    assert getattr(service, "gate_providers", None) in (None, [])
    pid = _create(manager)["id"]

    gates = manager.get("demo", pid)["gates"]
    assert [g["name"] for g in gates][:5] == [
        "state", "approvals", "validation", "specs", "checks"
    ]
    assert _gate(gates, "specs")["state"] == "skipped"
    assert _gate(gates, "checks")["state"] == "skipped"
    assert _gate(gates, "validation")["state"] == "pending"


def test_a_gate_provider_can_add_or_replace_a_gate(demo):
    service, _registry, manager = demo
    pid = _create(manager)["id"]
    service.gate_providers = [
        lambda proj, proposal: {"name": "specs", "state": "pass",
                                "summary": "3 specs met"},
        lambda proj, proposal: None,
        lambda proj, proposal: {"name": "dfm", "state": "fail",
                                "summary": "wall too thin"},
    ]

    gates = manager.get("demo", pid)["gates"]
    assert _gate(gates, "specs") == {"name": "specs", "state": "pass",
                                     "summary": "3 specs met"}
    assert _gate(gates, "dfm")["state"] == "fail"
    assert len([g for g in gates if g["name"] == "specs"]) == 1


def test_a_broken_gate_provider_degrades_to_pending(demo):
    service, _registry, manager = demo
    pid = _create(manager)["id"]

    def exploding(proj, proposal):
        raise RuntimeError("boom")

    service.gate_providers = [exploding]
    gates = manager.get("demo", pid)["gates"]
    degraded = _gate(gates, "exploding")
    assert degraded["state"] == "pending"
    assert "errored" in degraded["summary"]
    assert _gate(gates, "approvals")["state"] == "fail"  # the rest still work


def test_a_review_goes_stale_when_the_source_head_moves_but_still_counts(demo):
    service, _registry, manager = demo
    locks.set_client_id("chat:main")
    pid = _create(manager)["id"]

    locks.set_client_id("browser")
    manager.review("demo", pid, "approve")
    canonical = service.store.canonical_path_of("demo")
    head = service.history.resolve_branch(canonical, "feat")
    assert manager.get("demo", pid)["proposal"]["reviews"][-1]["source_head"] == head
    assert manager.get("demo", pid)["proposal"]["reviews"][-1]["stale"] is False

    moved = _move_source_head(service)
    assert moved != head
    detail = manager.get("demo", pid)
    assert detail["proposal"]["reviews"][-1]["stale"] is True
    approvals = _gate(detail["gates"], "approvals")
    assert approvals["state"] == "pass"  # still counts in v1
    assert "stale" in approvals["summary"]
