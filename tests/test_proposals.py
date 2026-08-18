"""Change proposals: store, lifecycle, audit, policy, gates and gated merge.

The core semantics only — the tool and route surface is
``tests/test_proposals_api.py`` and the review packet is slice 4. Everything
here resolves branches through git and asserts the sidecar's durability
guarantee, so the module carries ``integration`` + ``portability`` and skips
without git; only section 8's merge cases need geometry, because a proposal is
about a branch pair, not about a shape.

Sections: 1. store and identity · 2. the state machine · 3. creation rules ·
4. attribution and the audit log · 5. durability · 6. policy and gates ·
7. the branch-delete guard · 8. proposal_merge over PRD-001.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import time
from datetime import datetime, timedelta, timezone

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

BOX_V2_SCRIPT = BOX_SCRIPT.replace(
    "Box(p.size, p.size, p.size)", "Box(p.size, p.size, p.size * 2)"
)
BOX_V3_SCRIPT = BOX_SCRIPT.replace(
    "Box(p.size, p.size, p.size)", "Box(p.size, p.size, p.size * 3)"
)
BROKEN_SCRIPT = BOX_SCRIPT.replace("return part.part", "return no_such_name")


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


@pytest.fixture
def parts(stack):
    """'demo' with two buildable parts on master and a 'feat' branch forked
    from them: the shape section 8's merges validate."""
    service, registry = stack
    assert "error" not in registry.call("create_project", {"name": "demo"})
    for part in ("box", "pin"):
        created = registry.call(
            "create_part",
            {"project": "demo", "part_id": part, "script": BOX_SCRIPT},
        )
        assert "error" not in created, created
    service.branches.create("demo", "feat")
    return service, registry, ProposalManager(service)


def _create(manager, **kwargs) -> dict:
    payload = {"source": "feat", "title": "Thinner wall"}
    payload.update(kwargs)
    return manager.create("demo", **payload)["proposal"]


def _on(service, client: str, branch: str) -> None:
    """Put a client on a branch (identity + checkout)."""
    locks.set_client_id(client)
    if service.branches.current("demo") != branch:
        service.branches.switch("demo", branch)


def _script(registry, part: str, text: str, *, builds: bool = True) -> None:
    result = registry.call(
        "update_part_script",
        {"project": "demo", "part_id": part, "script": text},
    )
    # A script that does not build is still written and snapshotted; only the
    # rebuild result reports the failure.
    assert ("error" not in result) is builds, result


def _propose_and_approve(service, manager) -> str:
    """The AC1 shape: an agent proposes, a human approves."""
    locks.set_client_id("chat:main")
    pid = _create(manager)["id"]
    locks.set_client_id("browser")
    manager.review("demo", pid, "approve")
    return pid


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


def test_an_id_is_never_reused_even_when_the_index_is_lost(demo):
    """C10: the never-reuse contract used to rest on ``index.json``, which is
    explicitly a rebuildable cache — rebuild it from the directories after the
    newest proposal's directory was deleted and the highest id came round
    again. The high-water mark is its own file, written before the directory
    it names."""
    _service, _registry, manager = demo
    for index in range(10):
        proposal = _create(manager, title=f"p{index}")
        manager.update("demo", proposal["id"], state="closed")
    assert (manager.store.dir_of("demo") / "next_id").read_text().strip() == "11"

    shutil.rmtree(manager.store.dir_of("demo") / "10")
    (manager.store.dir_of("demo") / "index.json").write_text(
        "{not json", encoding="utf-8")

    assert _create(manager, title="after")["id"] == "11"


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


def test_every_timestamp_is_zone_aware_utc(demo):
    """Slice 6 fold-back: stamps are ISO-8601 *with* the ``Z`` designator, so
    any consumer (``Date.parse`` in the browser, ``datetime.fromisoformat``
    here) reads them as UTC instead of as local time. Second resolution, no
    microseconds — the shape is otherwise exactly what slices 1-4 wrote."""
    _service, _registry, manager = demo
    locks.set_client_id("chat:main")
    proposal = _create(manager)
    pid = proposal["id"]
    manager.review("demo", pid, "approve", summary="ship it")

    detail = manager.get("demo", pid)
    stamps = [detail["proposal"]["created"], detail["proposal"]["updated"],
              detail["proposal"]["reviews"][-1]["ts"],
              *[entry["ts"] for entry in detail["audit"]]]
    assert stamps
    for stamp in stamps:
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", stamp), stamp
        parsed = datetime.fromisoformat(stamp)
        assert parsed.tzinfo is not None
        assert parsed.utcoffset() == timedelta(0)
        # ...and it really is *now*, not a local time labelled as UTC.
        assert abs((datetime.now(timezone.utc) - parsed).total_seconds()) < 120


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


def test_a_comment_never_retracts_an_approval(demo):
    """A 'comment' changes no state, so it must not change the count either.
    Taking the latest verdict per actor *including* comments let one nit
    silently un-approve an approved proposal — and the gate said so while the
    state still read 'approved'."""
    _service, _registry, manager = demo
    locks.set_client_id("chat:main")
    pid = _create(manager)["id"]

    locks.set_client_id("browser")
    manager.review("demo", pid, "approve")
    after = manager.review("demo", pid, "comment", "one nit about the fillet")

    assert after["proposal"]["state"] == "approved"
    assert _gate(after["gates"], "approvals")["state"] == "pass"
    assert _gate(after["gates"], "approvals")["details"]["approvals"] == 1
    # the comment is still recorded, on the proposal and in the audit log
    assert [r["verdict"] for r in after["proposal"]["reviews"]] == [
        "approve", "comment"]
    assert [e["details"]["verdict"] for e in manager.get("demo", pid)["audit"]
            if e["action"] == "reviewed"] == ["approve", "comment"]

    landed = manager.merge("demo", pid)
    assert "error" not in landed, landed
    assert landed["proposal"]["state"] == "merged"


def test_a_comment_does_not_lift_a_request_for_changes(demo):
    """The other direction: only a new verdict clears a verdict."""
    _service, _registry, manager = demo
    locks.set_client_id("chat:main")
    pid = _create(manager)["id"]

    locks.set_client_id("browser")
    manager.review("demo", pid, "request_changes")
    manager.review("demo", pid, "comment", "and one more thing")

    gates = manager.get("demo", pid)["gates"]
    assert _gate(gates, "approvals")["state"] == "fail"
    assert _gate(gates, "state")["state"] == "fail"
    assert manager.get("demo", pid)["proposal"]["state"] == "changes_requested"
    with pytest.raises(ConflictError):
        manager.merge("demo", pid)


def test_specs_and_checks_are_skipped_with_no_providers(demo):
    service, _registry, manager = demo
    service.gate_providers = []   # PRD-003 appends one; this is the no-provider case
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


# ------------------------------------------- 7. the branch-delete guard (FR2)


def test_deleting_a_branch_an_active_proposal_names_is_refused(demo):
    """Either side of the pair: a proposal whose source or target vanished
    could never be reviewed, diffed or merged."""
    service, registry, manager = demo
    service.branches.create("demo", "release")
    pid = _create(manager, target="release")["id"]

    for name in ("feat", "release"):
        refused = registry.call("branch_delete", {"project": "demo", "name": name})
        assert refused["error"]["type"] == "conflict_error", refused
        assert refused["error"]["details"]["proposal"] == pid
        assert refused["error"]["details"]["branch"] == name
        assert pid in refused["error"]["message"]
    assert {b["name"] for b in service.branches.list("demo")["branches"]} == {
        "master", "feat", "release"}

    # A terminal proposal does not hold a branch hostage.
    manager.update("demo", pid, state="closed")
    assert "error" not in registry.call(
        "branch_delete", {"project": "demo", "name": "feat"})


def test_the_guard_and_proposal_creation_serialize(demo, monkeypatch):
    """The guard's check and the deletion are ONE critical section, and so is
    creation: otherwise a proposal opens against a branch in the window
    between 'no proposal names it' and the branch going away, and the guard
    protects a proposal that did not exist yet."""
    service, registry, _manager = demo
    manager = service.proposals  # the instance the installed guard calls
    service.branches.create("demo", "spare")
    manager.ensure_branch_guard()

    checking = threading.Event()
    inner = manager._check_branch_free

    def slow_check(proj: str, name: str) -> None:
        inner(proj, name)
        checking.set()
        time.sleep(0.3)  # the window a racing create used to slip through

    monkeypatch.setattr(manager, "_check_branch_free", slow_check)

    outcome: dict = {}

    def create_against_spare() -> None:
        assert checking.wait(30)
        locks.set_client_id("chat:main")
        try:
            outcome["proposal"] = manager.create("demo", "spare", title="race")
        except Exception as exc:  # noqa: BLE001 — the point of the test
            outcome["error"] = exc

    racer = threading.Thread(target=create_against_spare)
    racer.start()
    try:
        assert "error" not in registry.call(
            "branch_delete", {"project": "demo", "name": "spare"})
    finally:
        racer.join(60)

    assert isinstance(outcome.get("error"), NotFoundError), outcome
    assert {b["name"] for b in service.branches.list("demo")["branches"]} == {
        "master", "feat"}
    assert manager.list("demo")["proposals"] == []


def test_the_guard_is_idempotent_and_leaves_free_branches_alone(demo):
    service, registry, manager = demo
    service.branches.create("demo", "spare")
    _create(manager)  # first proposal-manager use installs the guard

    wrapped = service.branches.delete
    manager.ensure_branch_guard()
    manager.ensure_branch_guard()
    assert service.branches.delete is wrapped  # wrapping twice is a no-op

    assert "error" not in registry.call(
        "branch_delete", {"project": "demo", "name": "spare"})


# ---------------------------------------- 8. proposal_merge over PRD-001


def test_a_draft_cannot_be_merged(demo):
    """Well-formed, refused by workflow state: a conflict_error, not a
    validation_error."""
    _service, _registry, manager = demo
    pid = _create(manager, draft=True)["id"]

    with pytest.raises(ConflictError) as excinfo:
        manager.merge("demo", pid)
    assert "draft" in excinfo.value.message
    assert manager.get("demo", pid)["proposal"]["state"] == "draft"

    manager.update("demo", pid, state="open")
    manager.update("demo", pid, state="closed")
    with pytest.raises(ConflictError):
        manager.merge("demo", pid)


def test_policy_blocks_the_merge_and_allow_invalid_does_not_bypass_it(demo):
    """AC6. ``allow_invalid`` is the caller's statement about the KERNEL's
    verdict on geometry; letting it also waive a human approval would make one
    field mean two unrelated things."""
    _service, _registry, manager = demo
    locks.set_client_id("chat:main")
    pid = _create(manager)["id"]

    with pytest.raises(ConflictError) as excinfo:
        manager.merge("demo", pid)
    details = excinfo.value.details
    assert details["failing"] == "approvals"
    assert [g["name"] for g in details["gates"]][:2] == ["state", "approvals"]
    assert "1 approval required, 0 recorded" in excinfo.value.message

    manager.review("demo", pid, "approve")  # the author's own approval
    with pytest.raises(ConflictError):
        manager.merge("demo", pid)
    with pytest.raises(ConflictError):
        manager.merge("demo", pid, allow_invalid=True)
    assert manager.get("demo", pid)["proposal"]["state"] == "approved"
    assert manager.get("demo", pid)["proposal"]["merge"] is None

    locks.set_client_id("browser")
    manager.review("demo", pid, "approve")
    result = manager.merge("demo", pid)
    # 'feat' never diverged from master, so PRD-001 reports the no-op merge;
    # the proposal is still resolved by it.
    assert result["already_up_to_date"] is True
    assert result["proposal"]["state"] == "merged"


def test_a_changes_requested_proposal_is_refused_by_the_state_gate(demo):
    _service, _registry, manager = demo
    locks.set_client_id("chat:main")
    pid = _create(manager)["id"]
    _write_policy(manager, approvals_required=0)

    locks.set_client_id("browser")
    manager.review("demo", pid, "request_changes")
    with pytest.raises(ConflictError) as excinfo:
        manager.merge("demo", pid)
    assert excinfo.value.details["failing"] == "state"


def test_the_packet_is_frozen_by_the_merge(demo):
    """FR12: the evidence a decision was made on must not be regenerated
    afterwards. Slice 4's proposal_packet refuses to regenerate a frozen one."""
    _service, _registry, manager = demo
    locks.set_client_id("chat:main")
    pid = _create(manager)["id"]
    packet = manager.store.packet_path("demo", pid)
    packet.parent.mkdir(parents=True, exist_ok=True)
    packet.write_text(json.dumps({"proposal": pid, "ok": True, "stale": True}),
                      encoding="utf-8")

    locks.set_client_id("browser")
    manager.review("demo", pid, "approve")
    assert "error" not in manager.merge("demo", pid)

    stored = json.loads(packet.read_text(encoding="utf-8"))
    assert stored["frozen"] is True
    summary = manager.get("demo", pid)["packet"]
    assert summary["frozen"] is True and summary["stale"] is False


def test_a_merge_with_no_packet_freezes_the_absence(demo):
    """FR12 covers the absence too. A proposal merged before anyone opened its
    packet has nothing to show — and a packet built afterwards would measure
    the merged target (and everyone else's commits) and present that as the
    change that was reviewed. So the absence is recorded, durably."""
    _service, _registry, manager = demo
    locks.set_client_id("chat:main")
    pid = _create(manager)["id"]
    assert not manager.store.packet_path("demo", pid).exists()

    locks.set_client_id("browser")
    manager.review("demo", pid, "approve")
    assert "error" not in manager.merge("demo", pid)

    stored = json.loads(
        manager.store.packet_path("demo", pid).read_text(encoding="utf-8"))
    assert stored["frozen"] is True and stored["generated"] is None
    assert stored["ok"] is False and stored["parts"] == []
    assert "no review packet was generated" in stored["note"]
    assert manager.get("demo", pid)["packet"] == {
        "generated": None, "stale": False, "stale_at_merge": False,
        "ok": False, "frozen": True}


def _unhold(service, proj: str = "demo") -> None:
    """Strip ``held_by`` from the staged merge — a merge staged by a proposal
    BEFORE the hold existed, which is the only case the reconciler still
    answers for."""
    path = (service.store.canonical_path_of(proj) / ".history" / "agentcad"
            / "merge.json")
    state = json.loads(path.read_text(encoding="utf-8"))
    state.pop("held_by", None)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


@pytest.mark.slow
def test_a_merge_a_proposal_staged_is_not_landed_by_resolve_merge(parts):
    """C1: the legacy resolver used to finalize the moment the last conflict
    was resolved — landing the branch with nobody re-checking the proposal's
    gates, which makes the gate something you can walk past by conflicting. A
    merge a proposal HOLDS records resolutions and stops there."""
    service, registry, manager = parts
    canonical = service.store.canonical_path_of("demo")
    _on(service, "agent_a", "feat")
    _script(registry, "box", BOX_V2_SCRIPT)
    _on(service, "browser", "master")
    _script(registry, "box", BOX_V3_SCRIPT)
    pid = _propose_and_approve(service, manager)
    before = service.history.resolve_branch(canonical, "master")

    conflict = manager.merge("demo", pid)
    assert conflict["error"]["details"]["held_by"] == f"proposal:{pid}"

    resolved = registry.call("resolve_merge", {
        "project": "demo", "choices": {"parts/box.py": {"take": "theirs"}}})

    assert "error" not in resolved, resolved
    assert resolved["held"] is True and resolved["merged"] is False
    assert resolved["outstanding"] == 0
    assert resolved["held_by"] == f"proposal:{pid}"
    assert "proposal_merge" in resolved["hint"]
    # nothing landed: the target is where it was and the merge is still staged
    assert service.history.resolve_branch(canonical, "master") == before
    status = registry.call("merge_status", {"project": "demo"})["merge"]
    assert status["outstanding"] == 0 and status["held"] is True
    assert manager.get("demo", pid)["proposal"]["state"] == "approved"


@pytest.mark.slow
def test_a_held_merge_refuses_when_a_gate_turned_red_while_it_waited(parts):
    """The whole point of the hold: between staging and completion someone
    requested changes, so the merge that the last resolution would have landed
    must not land."""
    service, registry, manager = parts
    canonical = service.store.canonical_path_of("demo")
    _on(service, "agent_a", "feat")
    _script(registry, "box", BOX_V2_SCRIPT)
    _on(service, "browser", "master")
    _script(registry, "box", BOX_V3_SCRIPT)
    pid = _propose_and_approve(service, manager)
    before = service.history.resolve_branch(canonical, "master")

    assert manager.merge("demo", pid)["error"]["type"] == "merge_conflict"
    assert "error" not in registry.call("resolve_merge", {
        "project": "demo", "choices": {"parts/box.py": {"take": "theirs"}}})
    locks.set_client_id("browser")
    manager.review("demo", pid, "request_changes")

    with pytest.raises(ConflictError) as excinfo:
        manager.merge("demo", pid)

    assert excinfo.value.details["failing"] == "state"
    assert [g["name"] for g in excinfo.value.details["gates"]]
    assert service.history.resolve_branch(canonical, "master") == before
    assert manager.get("demo", pid)["proposal"]["state"] == "changes_requested"
    assert registry.call("merge_status", {"project": "demo"})["merge"]["held"]


@pytest.mark.slow
def test_a_held_merge_is_landed_by_proposal_merge_with_its_own_override(parts):
    """The other side of the hold: gates still green, so ``proposal_merge``
    finishes the merge the resolver left staged — through PRD-001's own
    validation and finalization — and records the override it really ran
    under."""
    service, registry, manager = parts
    canonical = service.store.canonical_path_of("demo")
    _on(service, "agent_a", "feat")
    _script(registry, "box", BROKEN_SCRIPT, builds=False)
    _on(service, "browser", "master")
    _script(registry, "box", BOX_V3_SCRIPT)
    pid = _propose_and_approve(service, manager)
    heads = [service.history.resolve_branch(canonical, "master"),
             service.history.resolve_branch(canonical, "feat")]

    assert manager.merge("demo", pid, allow_invalid=True)["error"]["type"] \
        == "merge_conflict"
    assert "error" not in registry.call("resolve_merge", {
        "project": "demo", "choices": {"parts/box.py": {"take": "theirs"}}})

    landed = manager.merge("demo", pid)  # no override passed: the staged one holds

    assert "error" not in landed, landed
    assert landed["merged"] is True and landed["parents"] == heads
    assert landed["validation"]["ok"] is False  # it landed under the override
    merge = manager.get("demo", pid)["proposal"]["merge"]
    assert merge["allow_invalid"] is True and merge["commit"] == landed["commit"]
    assert merge.get("reconciled") is None  # first-hand, not reconstructed
    actions = [e["action"] for e in manager.get("demo", pid)["audit"]]
    assert "override" in actions and actions[-1] in ("merged", "override")
    assert registry.call("merge_status", {"project": "demo"})["merge"] is None


@pytest.mark.slow
def test_a_conflicted_merge_finished_by_resolve_merge_is_reconciled(parts):
    """FR10/AC5 across the conflict path, for a merge staged BEFORE the hold
    existed (the reconciler is now a safety net for that history, not the
    primary path): ``resolve_merge`` completes the merge knowing nothing about
    proposals, so the proposal recognises its OWN staged merge in the commit
    that landed and records the truth — the real commit, its real parents, and
    the ``allow_invalid`` the staged merge really ran under."""
    service, registry, manager = parts
    canonical = service.store.canonical_path_of("demo")
    _on(service, "agent_a", "feat")
    _script(registry, "box", BROKEN_SCRIPT, builds=False)
    _on(service, "browser", "master")
    _script(registry, "box", BOX_V3_SCRIPT)
    pid = _propose_and_approve(service, manager)
    heads = [service.history.resolve_branch(canonical, "master"),
             service.history.resolve_branch(canonical, "feat")]

    conflict = manager.merge("demo", pid, allow_invalid=True)
    assert conflict["error"]["type"] == "merge_conflict"
    _unhold(service)
    staged = manager.get("demo", pid)["proposal"]["staged_merge"]
    assert staged["merge_id"] == conflict["error"]["details"]["merge_id"]
    assert staged["allow_invalid"] is True
    assert [staged["target_head"], staged["source_head"]] == heads

    landed = registry.call("resolve_merge", {
        "project": "demo", "choices": {"parts/box.py": {"take": "theirs"}}})
    assert "error" not in landed, landed
    assert landed["validation"]["ok"] is False  # it landed under the override

    detail = manager.get("demo", pid)
    merge = detail["proposal"]["merge"]
    assert detail["proposal"]["state"] == "merged"
    assert "staged_merge" not in detail["proposal"]
    assert merge["commit"] == landed["commit"] and merge["parents"] == heads
    assert merge["allow_invalid"] is True and merge["reconciled"] is True
    assert merge["validation"] == {**merge["validation"], "ok": False,
                                   "recovered": True}
    actions = [e["action"] for e in detail["audit"]]
    assert "merged" in actions and "override" in actions
    override = [e for e in detail["audit"] if e["action"] == "override"][-1]
    assert override["details"]["commit"] == landed["commit"]
    assert override["details"]["via"] == "resolve_merge"
    assert detail["packet"]["frozen"] is True
    assert [g["state"] for g in detail["gates"] if g["name"] == "validation"] \
        == ["fail"]


@pytest.mark.slow
def test_a_source_commit_between_the_gates_and_the_merge_is_refused(
        parts, monkeypatch):
    """C4/FR11: a gate result is a statement about a specific source head. The
    source branch's turn is held across gate evaluation and the merge, so a
    second client cannot slip a commit in between and have it land under
    somebody else's green gates."""
    service, registry, manager = parts
    canonical = service.store.canonical_path_of("demo")
    _on(service, "agent_b", "feat")
    _script(registry, "box", BOX_V2_SCRIPT)
    _on(service, "browser", "master")
    _script(registry, "pin", BOX_V3_SCRIPT)
    pid = _propose_and_approve(service, manager)
    head = service.history.resolve_branch(canonical, "feat")

    refused = []
    inner = service.merges.merge

    def racing(*args, **kwargs):
        # Another client commits on the source between the gates and the merge
        # — the interleaving that used to land content no gate ever saw.
        locks.set_client_id("agent_b")
        try:
            refused.append(registry.call(
                "update_part_script",
                {"project": "demo", "part_id": "box",
                 "script": BOX_V3_SCRIPT}))
        finally:
            locks.set_client_id("browser")
        return inner(*args, **kwargs)

    monkeypatch.setattr(service.merges, "merge", racing)
    result = manager.merge("demo", pid)

    assert refused and refused[0].get("error"), refused
    assert refused[0]["error"]["type"] == "conflict_error"
    assert "locked by browser" in refused[0]["error"]["message"]
    assert service.history.resolve_branch(canonical, "feat") == head
    assert "error" not in result, result
    assert result["parents"][1] == head
    assert result["proposal"]["merge"]["gates_source_head"] == head
    assert [e["action"] for e in manager.get("demo", pid)["audit"]
            if e["action"] == "gate_head_mismatch"] == []


@pytest.mark.slow
def test_a_source_turn_held_by_someone_else_is_a_clean_conflict_error(parts):
    """The deadlock question, answered by ``TurnLock``: it never blocks, it
    raises. The proposal takes the SOURCE turn and the orchestrator takes the
    TARGET's, so two proposals merging in opposite directions cannot wait on
    each other — the loser gets an ordinary ``conflict_error``, at once."""
    service, _registry, manager = parts
    pid = _propose_and_approve(service, manager)
    tree = service.branches.tree_of("demo", "feat")
    with service.branches.pinned("demo", tree):
        key = service.store.lock_key("demo")
    service.turnlock.acquire(key, "agent_b")

    started = time.monotonic()
    with pytest.raises(ConflictError) as excinfo:
        manager.merge("demo", pid)

    assert time.monotonic() - started < 5.0  # it raised, it did not wait
    assert excinfo.value.details["holder"] == "agent_b"
    assert manager.get("demo", pid)["proposal"]["state"] == "approved"
    assert service.merges.status("demo")["merge"] is None


@pytest.mark.slow
def test_an_aborted_staged_merge_leaves_the_proposal_where_it_was(parts):
    """The other end of the reconciler: a merge that never landed must not be
    mistaken for one that did, and must not keep the proposal waiting on it."""
    service, registry, manager = parts
    _on(service, "agent_a", "feat")
    _script(registry, "box", BOX_V2_SCRIPT)
    _on(service, "browser", "master")
    _script(registry, "box", BOX_V3_SCRIPT)
    pid = _propose_and_approve(service, manager)

    assert manager.merge("demo", pid)["error"]["type"] == "merge_conflict"
    assert registry.call("merge_abort", {"project": "demo"})["aborted"] is True

    detail = manager.get("demo", pid)
    assert detail["proposal"]["state"] == "approved"
    assert detail["proposal"]["merge"] is None
    assert "staged_merge" not in detail["proposal"]
    assert [e["action"] for e in detail["audit"]][-1] == "merge_discarded"


def _rehold(service, holder: str, proj: str = "demo") -> None:
    """Point the staged merge's ``held_by`` at another workflow — the shape a
    merge held by a DIFFERENT proposal has on disk."""
    path = (service.store.canonical_path_of(proj) / ".history" / "agentcad"
            / "merge.json")
    state = json.loads(path.read_text(encoding="utf-8"))
    state["held_by"] = holder
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _held_by(service, proj: str = "demo") -> str | None:
    path = (service.store.canonical_path_of(proj) / ".history" / "agentcad"
            / "merge.json")
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8")).get("held_by")


def _stage_held_conflict(service, registry, manager) -> str:
    """A proposal whose merge conflicted (so it holds the staged merge) with
    one resolution recorded against it."""
    _on(service, "agent_a", "feat")
    _script(registry, "box", BOX_V2_SCRIPT)
    _on(service, "browser", "master")
    _script(registry, "box", BOX_V3_SCRIPT)
    pid = _propose_and_approve(service, manager)
    assert manager.merge("demo", pid)["error"]["type"] == "merge_conflict"
    assert "error" not in registry.call("resolve_merge", {
        "project": "demo", "choices": {"parts/box.py": {"take": "theirs"}}})
    return pid


@pytest.mark.slow
def test_closing_a_proposal_aborts_the_merge_it_held(parts):
    """D2: a hold outliving its proposal is unfinishable — nothing may complete
    it, and the resolutions recorded against it sit there waiting to be
    inherited by whoever merges the pair next. Closing takes it with it."""
    service, registry, manager = parts
    canonical = service.store.canonical_path_of("demo")
    pid = _stage_held_conflict(service, registry, manager)
    before = service.history.resolve_branch(canonical, "master")

    locks.set_client_id("browser")
    manager.update("demo", pid, state="closed")

    assert registry.call("merge_status", {"project": "demo"})["merge"] is None
    assert _held_by(service) is None
    assert service.history.resolve_branch(canonical, "master") == before
    detail = manager.get("demo", pid)
    assert detail["proposal"]["state"] == "closed"
    assert "staged_merge" not in detail["proposal"]
    discarded = [e for e in detail["audit"] if e["action"] == "merge_discarded"]
    assert len(discarded) == 1
    assert discarded[0]["details"]["reason"] == "closed"
    assert discarded[0]["details"]["source"] == "feat"


@pytest.mark.slow
def test_the_next_proposal_for_the_pair_starts_a_clean_merge(parts):
    """H6, end to end: the second proposal for a pair whose first proposal was
    closed must meet no hold and inherit none of its resolutions — its
    approvers never saw them."""
    service, registry, manager = parts
    canonical = service.store.canonical_path_of("demo")
    pid_a = _stage_held_conflict(service, registry, manager)
    locks.set_client_id("browser")
    manager.update("demo", pid_a, state="closed")
    locks.set_client_id("chat:main")
    pid_b = manager.create("demo", source="feat", title="B")["proposal"]["id"]
    locks.set_client_id("browser")
    manager.review("demo", pid_b, "approve")
    before = service.history.resolve_branch(canonical, "master")

    conflict = manager.merge("demo", pid_b)

    details = conflict["error"]["details"]
    assert conflict["error"]["type"] == "merge_conflict"
    assert details["held_by"] == f"proposal:{pid_b}"
    assert details["proposal"] == pid_b
    assert details["outstanding"] == 1  # nothing was inherited from A
    status = registry.call("merge_status", {"project": "demo"})["merge"]
    assert status["resolved"] == [] and status["outstanding"] == 1
    assert service.history.resolve_branch(canonical, "master") == before
    assert manager.get("demo", pid_a)["proposal"]["state"] == "closed"


@pytest.mark.slow
def test_a_merge_held_by_another_proposal_is_refused_coherently(parts):
    """The refusal names the holder and changes nothing. It used to claim the
    merge belonged to the caller ("belongs to proposal:2, not to proposal 2")
    while rewriting ``held_by`` to the caller — so the *second* call resumed
    and landed another proposal's resolutions."""
    service, registry, manager = parts
    canonical = service.store.canonical_path_of("demo")
    pid = _stage_held_conflict(service, registry, manager)
    _rehold(service, "proposal:99")
    before = service.history.resolve_branch(canonical, "master")

    with pytest.raises(ConflictError) as excinfo:
        manager.merge("demo", pid)

    message = excinfo.value.message
    assert "proposal:99" in message and "not to proposal" not in message
    assert excinfo.value.details["held_by"] == "proposal:99"
    assert _held_by(service) == "proposal:99"  # nothing was rewritten
    # ...so the retry is the same refusal, not a landing
    with pytest.raises(ConflictError):
        manager.merge("demo", pid)
    assert service.history.resolve_branch(canonical, "master") == before
    assert manager.get("demo", pid)["proposal"]["state"] == "approved"


@pytest.mark.slow
def test_an_approved_proposal_merges_through_prd001(parts):
    service, registry, manager = parts
    canonical = service.store.canonical_path_of("demo")
    _on(service, "agent_a", "feat")
    _script(registry, "box", BOX_V2_SCRIPT)
    _on(service, "browser", "master")
    _script(registry, "pin", BOX_V3_SCRIPT)
    pid = _propose_and_approve(service, manager)

    result = manager.merge("demo", pid)

    assert "error" not in result, result
    assert result["merged"] is True and result["fast_forward"] is False
    assert result["validation"]["ok"] is True
    assert len(result["parents"]) == 2
    assert result["proposal"]["state"] == "merged"
    assert result["proposal"]["merge"]["commit"] == result["commit"]
    assert result["proposal"]["merge"]["allow_invalid"] is False
    assert [g["state"] for g in result["gates"] if g["name"] == "validation"] \
        == ["pass"]

    stored = manager.get("demo", pid)
    assert stored["proposal"]["merge"]["validation"]["ok"] is True
    assert [e["action"] for e in stored["audit"]][-1] == "merged"
    parents = service.history._run(
        canonical, "rev-list", "--parents", "-n", "1", "master"
    ).stdout.split()[1:]
    assert len(parents) == 2
    assert (canonical / "parts" / "box.py").read_text() == BOX_V2_SCRIPT


@pytest.mark.slow
def test_a_blocked_validation_refuses_then_lands_with_allow_invalid(parts):
    """AC5: the kernel gate blocks, the override lands it, and the override is
    recorded in all three places (audit, proposal, merge commit message)."""
    service, registry, manager = parts
    canonical = service.store.canonical_path_of("demo")
    _on(service, "agent_a", "feat")
    _script(registry, "box", BROKEN_SCRIPT, builds=False)
    _on(service, "browser", "master")
    _script(registry, "pin", BOX_V2_SCRIPT)
    pid = _propose_and_approve(service, manager)

    with pytest.raises(ValidationError) as excinfo:
        manager.merge("demo", pid)
    details = excinfo.value.details
    assert details["validation"]["ok"] is False
    assert details["validation"]["blocked"] is True
    assert [f["part"] for f in details["validation"]["failures"]] == ["box"]
    assert details["proposal"] == pid

    blocked = manager.get("demo", pid)
    assert blocked["proposal"]["state"] == "approved"
    assert blocked["proposal"]["merge"] is None
    attempts = [e for e in blocked["audit"] if e["action"] == "merge_attempted"]
    assert attempts[-1]["details"]["outcome"] == "blocked"

    landed = manager.merge("demo", pid, allow_invalid=True)
    assert "error" not in landed, landed
    assert landed["proposal"]["state"] == "merged"
    assert landed["proposal"]["merge"]["allow_invalid"] is True
    assert landed["validation"]["ok"] is False
    actions = [e["action"] for e in manager.get("demo", pid)["audit"]]
    assert "override" in actions
    message = service.history._run(
        canonical, "log", "-1", "--pretty=%B", "master").stdout
    assert "Validation: FAILED" in message and "allow_invalid" in message


@pytest.mark.slow
def test_a_conflicting_merge_passes_through_verbatim(parts):
    service, registry, manager = parts
    _on(service, "agent_a", "feat")
    _script(registry, "box", BOX_V2_SCRIPT)
    _on(service, "browser", "master")
    _script(registry, "box", BOX_V3_SCRIPT)
    pid = _propose_and_approve(service, manager)

    result = manager.merge("demo", pid)

    assert set(result) == {"error"}  # PRD-001's payload, nothing wrapped round it
    error = result["error"]
    assert error["type"] == "merge_conflict"
    assert set(error["details"]) >= {"merge_id", "source", "target", "base",
                                     "outstanding", "conflicts", "hint",
                                     "proposal"}
    assert error["details"]["proposal"] == pid
    assert error["details"]["conflicts"][0]["path"] == "parts/box.py"

    detail = manager.get("demo", pid)
    assert detail["proposal"]["state"] == "approved"  # unchanged
    assert [e["details"]["outcome"] for e in detail["audit"]
            if e["action"] == "merge_attempted"] == ["conflict"]
    assert service.merges.status("demo")["merge"]["source"] == "feat"

    resolved = registry.call("resolve_merge", {
        "project": "demo", "choices": {"parts/box.py": {"take": "theirs"}}})
    assert "error" not in resolved, resolved

    done = manager.merge("demo", pid)
    assert "error" not in done, done
    assert done["proposal"]["state"] == "merged"
    assert done["proposal"]["merge"]["commit"]


def test_the_two_summary_producers_agree_on_the_key_set():
    """PRD-012 follow-up 3 (P10): `proposals._absent_packet` hand-writes a
    packet stub for a proposal closed out before a packet existed. Two shapes
    of `summary` in one API is a browser reading `undefined` from one of
    them."""
    from agentcad.core.packet import _summary
    from agentcad.core.proposals import _absent_packet

    stub = _absent_packet({"id": "p1", "source": "a", "target": "b"})
    assert set(stub["summary"]) == set(_summary([], None))
    assert stub["summary"]["mates_changed"] == 0
    assert stub["summary"]["configs_changed"] == 0
