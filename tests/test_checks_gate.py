"""The proposal slot and the ``checks`` gate (PRD-004, slice 6).

A check report becomes a *decision* here: ``agentcad check --proposal 3`` writes
``proposals/3/checks.json`` beside the packet, appends one audit line, and the
gate provider named ``checks`` replaces PRD-002's built-in placeholder so a
reviewer sees the verdict where the decision is made.

The rules pinned here, all of them deliberate:

* **Absent evidence is ``skipped``.** A proposal nobody posted a check to is
  byte-identical to today's placeholder — the gate can only ever block a
  proposal someone *did* post a check to. Opting in is the post.
* **Stale evidence is ``fail``, not ``pending``** — PRD-003's X8 lesson applied
  to this gate. ``ProposalManager.merge`` blocks a ``fail`` and *nothing else*,
  so a ``pending`` would have been merge-**permissive**: a green posted against
  an older head would silently wave through commits it never measured.
  ``fail`` with a re-run sentence is the only state that cannot lie.
* **Unreadable or unfinished evidence is ``fail``** for the same reason: a
  report that would not parse, or one whose budget ran out, is not a green.
* **The provider never raises and never answers ``pending``.**

Sections: 1. posting · 2. the gate · 3. installation · 4. the tool and the
route · 5. the CLI.
"""

from __future__ import annotations

import json
import queue
import shutil
from argparse import Namespace

import pytest

from agentcad import cli
from agentcad.core import locks
from agentcad.core.branches import pinned_tree_var
from agentcad.core.checks import (
    CHECKS_SCHEMA,
    CheckRunner,
    finalize_report,
    make_item,
    make_stage,
)
from agentcad.core.model import ConflictError, NotFoundError, ValidationError
from agentcad.core.proposals import ProposalManager
from agentcad.core.service import AgentCADService, EventBus
from agentcad.core.tools import build_registry

_GIT = [
    pytest.mark.integration,
    pytest.mark.portability,
    pytest.mark.skipif(shutil.which("git") is None, reason="git not found on PATH"),
]
pytestmark = _GIT


@pytest.fixture(autouse=True)
def _reset_context():
    """Identity and the branch pin are ContextVars: rebind them per test so one
    test's client id can never leak into the next (``cmd_check`` sets ``ci``)."""
    cid = locks.client_id_var.set("local")
    pin = pinned_tree_var.set(None)
    yield
    locks.client_id_var.reset(cid)
    pinned_tree_var.reset(pin)


@pytest.fixture
def stack(kernel, tmp_path):
    """The real service + registry (NOT make_test_service, which disables the
    snapshot hook): the pack appends the gate provider at register()."""
    service = AgentCADService(tmp_path / "projects", kernel, EventBus())
    registry = build_registry(service)
    assert getattr(service, "branches", None) is not None
    return service, registry


@pytest.fixture
def demo(stack):
    """'demo' with a 'feat' branch and NO parts: a proposal is a branch-pair
    object and this whole suite is about the slot, not about geometry."""
    service, registry = stack
    assert "error" not in registry.call("create_project", {"name": "demo"})
    service.branches.create("demo", "feat")
    return service, registry, ProposalManager(service)


# ------------------------------------------------------------------ helpers

def _create(manager, **kwargs) -> str:
    payload = {"source": "feat", "title": "Thinner wall"}
    payload.update(kwargs)
    return manager.create("demo", **payload)["proposal"]["id"]


def _on(service, client: str, branch: str) -> None:
    locks.set_client_id(client)
    if service.branches.current("demo") != branch:
        service.branches.switch("demo", branch)


def _head(service, branch: str = "feat") -> str:
    canonical = service.store.canonical_path_of("demo")
    return service.history.resolve_branch(canonical, branch)


def _move_head(service, branch: str = "feat") -> str:
    """Commit something on *branch* — the head the posted report certified is
    then no longer the head the proposal points at."""
    tree = service.branches.tree_of("demo", branch)
    (tree / "notes.txt").write_text("note\n", encoding="utf-8")
    service.history.snapshot(tree, "note")
    return _head(service, branch)


def _report(head: str | None, *, row: str = "pass", complete: bool = True,
            branch: str = "feat", items: bool = True) -> dict:
    """A valid ``schema: 1`` report certifying *head* — the shape a real run
    produces, without paying for a real run."""
    rows = [make_item("build", "part", "widget", row, "…")] if items else []
    return finalize_report(
        "demo", [make_stage("build", rows)],
        source={"kind": "branch", "ref": branch, "sha": head, "label": None,
                "host_sha": None, "dirty": False},
        host={"platform": "test"}, started="2026-01-01T00:00:00Z",
        complete=complete)


def _gate(manager, pid: str, name: str = "checks") -> dict:
    proposal = manager.reconcile("demo", pid)
    return next(g for g in manager.gates("demo", proposal) if g["name"] == name)


def _slot(service, pid: str):
    return service.checks._check_store().path("demo", pid)


def _audit(service, pid: str) -> list[dict]:
    return service.proposals.store.audit("demo", pid)


def _post(service, pid: str, report: dict) -> dict:
    return service.checks.post_to_proposal("demo", pid, report)


# ------------------------------------------------------------- 1. posting


def test_posting_writes_the_slot_beside_the_packet(demo):
    service, _registry, manager = demo
    pid = _create(manager)
    locks.set_client_id("ci")

    receipt = _post(service, pid, _report(_head(service)))

    path = _slot(service, pid)
    assert path.name == "checks.json"
    assert path.parent == service.proposals.store.packet_path("demo", pid).parent
    record = json.loads(path.read_text())
    assert record["schema"] == CHECKS_SCHEMA
    assert record["head"] == _head(service)
    assert record["source"] == "feat"
    assert record["status"] == "green" and record["exit_code"] == 0
    assert record["complete"] is True and record["strict"] is False
    assert record["summary"]["passed"] == 1
    assert [s["name"] for s in record["stages"]] == ["build"]
    # The whole report is embedded: the slot is the durable copy, and the
    # in-memory `last` cache is not.
    assert record["report"]["schema"] == 1
    # `ci` is an agent (only the browser is a human) — PRD-002's actor_kind.
    assert record["posted_by"] == "ci" and record["actor_kind"] == "agent"
    assert receipt["ok"] is True and receipt["id"] == pid
    assert receipt["path"] == str(path)


def test_posting_appends_exactly_one_audit_line_and_rewrites_none(demo):
    service, _registry, manager = demo
    pid = _create(manager)
    before = [json.dumps(e, sort_keys=True) for e in _audit(service, pid)]

    _post(service, pid, _report(_head(service), row="fail"))

    after = _audit(service, pid)
    assert [json.dumps(e, sort_keys=True) for e in after[:len(before)]] == before
    assert len(after) == len(before) + 1
    entry = after[-1]
    assert entry["action"] == "checks_posted"
    assert entry["details"]["status"] == "red"
    assert entry["details"]["exit_code"] == 1
    assert entry["details"]["head"] == _head(service)


def test_posting_publishes_proposal_changed_with_reason_checks(demo):
    service, _registry, manager = demo
    pid = _create(manager)
    subscription = service.bus.subscribe()

    _post(service, pid, _report(_head(service)))

    changed = []
    while True:
        try:
            event = subscription.get_nowait()
        except queue.Empty:
            break
        if event.get("type") == "proposal_changed":
            changed.append(event)
    assert changed and changed[-1]["reason"] == "checks"
    assert changed[-1]["id"] == pid and changed[-1]["project"] == "demo"


def test_a_second_post_replaces_the_first(demo):
    service, _registry, manager = demo
    pid = _create(manager)
    _post(service, pid, _report(_head(service), row="fail"))
    _post(service, pid, _report(_head(service)))

    record = json.loads(_slot(service, pid).read_text())
    assert record["status"] == "green"
    assert len(_audit(service, pid)) >= 2


@pytest.mark.parametrize("state", ["merged", "closed"])
def test_posting_to_a_terminal_proposal_is_refused(demo, state):
    service, _registry, manager = demo
    pid = _create(manager)
    proposal = manager.reconcile("demo", pid)
    proposal["state"] = state          # the store is the seam; the lifecycle
    manager.store.save("demo", proposal)  # itself is PRD-002's and unedited

    with pytest.raises(ConflictError) as exc:
        _post(service, pid, _report(_head(service)))
    assert state in str(exc.value)
    assert not _slot(service, pid).exists()


def test_posting_to_an_unknown_proposal_is_not_found(demo):
    service, _registry, _manager = demo
    with pytest.raises(NotFoundError):
        _post(service, "404", _report(_head(service)))


def test_posting_without_proposals_is_a_validation_error_naming_git(
        kernel, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "agentcad.core.history.ProjectHistory.available", lambda self: False)
    service = AgentCADService(tmp_path / "projects", kernel, EventBus())
    registry = build_registry(service)
    assert "error" not in registry.call("create_project", {"name": "demo"})
    assert getattr(service, "proposals", None) is None

    with pytest.raises(ValidationError) as exc:
        service.checks.post_to_proposal("demo", "1", _report(None))
    assert "git" in str(exc.value)


# ------------------------------------------------------------- 2. the gate


def test_nothing_posted_is_the_placeholder_verdict(demo):
    service, _registry, manager = demo
    pid = _create(manager)

    gate = _gate(manager, pid)

    # Byte-identical to PRD-002's built-in placeholder: installing this feature
    # changes nothing until a report is actually posted.
    assert gate["state"] == "skipped"
    assert gate["summary"] == "no checks posted"
    assert gate["details"]["posted"] is False
    assert service.checks is not None


def test_a_green_report_on_the_current_head_passes(demo):
    service, _registry, manager = demo
    pid = _create(manager)
    _post(service, pid, _report(_head(service)))

    gate = _gate(manager, pid)

    assert gate["state"] == "pass"
    assert _head(service)[:7] in gate["summary"]
    assert gate["details"]["head"] == gate["details"]["source_head"]
    assert gate["details"]["status"] == "green"


def test_a_red_report_fails_and_blocks_the_merge(demo):
    service, _registry, manager = demo
    locks.set_client_id("chat:main")
    pid = _create(manager)
    locks.set_client_id("browser")
    manager.review("demo", pid, "approve")
    _post(service, pid, _report(_head(service), row="fail"))

    gate = _gate(manager, pid)
    assert gate["state"] == "fail"
    assert "red" in gate["summary"]
    assert gate["details"]["reason"] == "red"
    assert [f["id"] for f in gate["details"]["failures"]] == ["build:widget"]

    with pytest.raises(ConflictError) as exc:
        manager.merge("demo", pid)
    assert exc.value.details["failing"] == "checks"


def test_a_stale_report_fails_with_a_retry_rather_than_pending(demo):
    """PRD-003's X8 lesson, applied to this gate.

    ``merge`` blocks a ``fail`` and nothing else, so the design spec's
    ``pending`` for a moved head was merge-**permissive**: the green would have
    stood for a commit it never measured. It is ``fail`` with a re-run
    sentence, and it blocks — exactly like the specs gate's moved head.
    """
    service, _registry, manager = demo
    locks.set_client_id("chat:main")
    pid = _create(manager)
    locks.set_client_id("browser")
    manager.review("demo", pid, "approve")
    certified = _head(service)
    _post(service, pid, _report(certified))
    moved = _move_head(service)
    assert moved != certified

    gate = _gate(manager, pid)

    assert gate["state"] == "fail"          # NOT pending
    assert gate["state"] != "pending"
    assert certified[:7] in gate["summary"] and moved[:7] in gate["summary"]
    assert "re-run" in gate["summary"]
    assert gate["details"]["reason"] == "stale_head"
    assert gate["details"]["status"] == "green"   # honest about what it says

    with pytest.raises(ConflictError) as exc:
        manager.merge("demo", pid)
    assert exc.value.details["failing"] == "checks"


def test_an_incomplete_report_fails(demo):
    service, _registry, manager = demo
    pid = _create(manager)
    _post(service, pid, _report(_head(service), complete=False))

    gate = _gate(manager, pid)

    assert gate["state"] == "fail"
    assert gate["details"]["reason"] == "incomplete"
    assert "budget" in gate["summary"]


def test_a_report_that_measured_nothing_is_skipped(demo):
    service, _registry, manager = demo
    pid = _create(manager)
    _post(service, pid, _report(_head(service), items=False))

    gate = _gate(manager, pid)

    assert gate["state"] == "skipped"
    assert gate["details"]["status"] == "skip"
    assert "nothing" in gate["summary"]


def test_an_unreadable_posted_report_fails_rather_than_disappearing(demo):
    """A file that exists and will not parse is not "no checks posted"."""
    service, _registry, manager = demo
    pid = _create(manager)
    _post(service, pid, _report(_head(service)))
    _slot(service, pid).write_text("{not json", encoding="utf-8")

    gate = _gate(manager, pid)

    assert gate["state"] == "fail"
    assert gate["details"]["posted"] is True
    assert gate["details"]["reason"] == "unreadable"


def test_the_provider_never_raises_and_never_answers_pending(demo,
                                                             monkeypatch):
    service, _registry, manager = demo
    pid = _create(manager)
    _post(service, pid, _report(_head(service)))
    monkeypatch.setattr(
        CheckRunner, "_source_head",
        lambda self, proj, branch: (_ for _ in ()).throw(RuntimeError("boom")))

    gate = _gate(manager, pid)

    assert gate["state"] == "fail"
    assert gate["state"] != "pending"
    assert "boom" in json.dumps(gate)


# ----------------------------------------------------- 3. the installation


def test_both_providers_are_installed_and_the_gate_appears_once(demo):
    service, _registry, manager = demo
    names = [getattr(p, "__name__", None) for p in service.gate_providers]
    assert names == ["checks", "specs"]   # `r` before `s`, both after `p`
    pid = _create(manager)

    gates = manager.gates("demo", manager.reconcile("demo", pid))

    assert [g["name"] for g in gates] == [
        "state", "approvals", "validation", "specs", "checks"]
    assert len([g for g in gates if g["name"] == "checks"]) == 1


def test_installing_twice_replaces_rather_than_duplicates(demo):
    service, _registry, _manager = demo
    build_registry(service)   # the versioning pack's precedent: it may run twice
    names = [getattr(p, "__name__", None) for p in service.gate_providers]
    assert names.count("checks") == 1


def test_install_tolerates_a_service_with_no_gate_providers(kernel, tmp_path,
                                                            monkeypatch):
    """``tools_proposals`` self-disables without git, so ``gate_providers`` is
    ABSENT — the pack that installs the gate must not assume it."""
    monkeypatch.setattr(
        "agentcad.core.history.ProjectHistory.available", lambda self: False)
    service = AgentCADService(tmp_path / "projects", kernel, EventBus())
    registry = build_registry(service)

    assert getattr(service, "gate_providers", None) is None
    assert registry.get("run_checks") is not None


# ------------------------------------------------- 4. the tool and the route


def test_the_tool_posts_and_returns_a_receipt(demo):
    service, registry, manager = demo
    pid = _create(manager)
    _on(service, "ci", "feat")

    report = registry.call("run_checks", {"project": "demo", "proposal": pid})

    assert "error" not in report, report
    assert report["posted"]["ok"] is True and report["posted"]["id"] == pid
    record = json.loads(_slot(service, pid).read_text())
    assert record["report"]["status"] == report["status"]
    # The stored copy is the measurement, not the delivery note.
    assert "posted" not in record["report"]


def test_the_tool_refuses_an_unknown_proposal_before_measuring(demo):
    _service, registry, _manager = demo
    result = registry.call("run_checks", {"project": "demo",
                                          "proposal": "404"})
    assert result["error"]["type"] == "notfound_error"


def test_the_tool_reports_a_terminal_proposal_as_a_conflict(demo):
    service, registry, manager = demo
    pid = _create(manager)
    proposal = manager.reconcile("demo", pid)
    proposal["state"] = "merged"
    manager.store.save("demo", proposal)

    result = registry.call("run_checks", {"project": "demo", "proposal": pid})

    assert result["error"]["type"] == "conflict_error"


def test_the_route_reads_the_posted_report(demo):
    from fastapi.testclient import TestClient

    from agentcad.server.app import create_app

    service, registry, manager = demo
    pid = _create(manager)
    _post(service, pid, _report(_head(service)))
    app = create_app(service, registry, extra_allowed_hosts={"testserver"})
    http = TestClient(app, base_url="http://127.0.0.1")

    posted = http.get(f"/api/projects/demo/checks?proposal={pid}")
    assert posted.status_code == 200
    assert posted.json()["head"] == _head(service)
    assert posted.json()["report"]["schema"] == 1

    assert http.get("/api/projects/demo/checks?proposal=404").status_code == 404


# ------------------------------------------------------------- 5. the CLI


def _args(**overrides):
    defaults = dict(project="demo", projects_dir=None, ref=None, stages=None,
                    report=None, md=None, strict=False,
                    verify_determinism=False, budget=None, min_volume=0.001,
                    work_dir=None, proposal=None, auto_proposal=False,
                    sha=None, ref_label=None, quiet=True, json=False)
    defaults.update(overrides)
    return Namespace(**defaults)


@pytest.fixture
def wired_cli(demo, monkeypatch):
    """``cmd_check`` over the REAL service — with the session kernel's ``stop``
    neutered, because the command stops the kernel it was handed and this one
    is shared by the whole test session."""
    service, _registry, manager = demo
    monkeypatch.setattr(service.kernel, "stop", lambda: None)
    monkeypatch.setattr(cli, "_build_service",
                        lambda projects_dir, extra_writable=None: service)
    _on(service, "ci", "feat")
    return service, manager


def test_auto_proposal_posts_to_the_one_matching_proposal(wired_cli, capsys):
    service, manager = wired_cli
    pid = _create(manager)

    assert cli.cmd_check(_args(auto_proposal=True)) == 0

    record = json.loads(_slot(service, pid).read_text())
    assert record["source"] == "feat"
    # `cmd_check` sets the identity to `ci` before it measures, so the post is
    # attributed to CI and classifies as an AGENT action (only the browser is a
    # human) — and a CI run never takes a human's per-client checkout.
    assert record["posted_by"] == "ci" and record["actor_kind"] == "agent"
    assert f"proposal {pid}" in capsys.readouterr().err


def test_auto_proposal_refuses_to_guess_between_two(wired_cli, capsys):
    service, manager = wired_cli
    service.branches.create("demo", "release")
    first = _create(manager)
    second = _create(manager, target="release")

    assert cli.cmd_check(_args(auto_proposal=True)) == 2

    assert not _slot(service, first).exists()
    assert not _slot(service, second).exists()
    assert "--proposal" in capsys.readouterr().err


def test_auto_proposal_with_no_match_warns_and_keeps_the_verdict(wired_cli,
                                                                 capsys):
    service, _manager = wired_cli
    assert cli.cmd_check(_args(auto_proposal=True)) == 0
    assert "no active proposal" in capsys.readouterr().err


def test_an_explicit_terminal_proposal_fails_fast(wired_cli, capsys):
    service, manager = wired_cli
    pid = _create(manager)
    proposal = manager.reconcile("demo", pid)
    proposal["state"] = "merged"
    manager.store.save("demo", proposal)

    assert cli.cmd_check(_args(proposal=pid)) == 2

    assert not _slot(service, pid).exists()
    assert "merged" in capsys.readouterr().err


def test_an_explicit_proposal_posts_and_the_report_is_written(wired_cli,
                                                              tmp_path):
    service, manager = wired_cli
    pid = _create(manager)
    out = tmp_path / "report.json"

    assert cli.cmd_check(_args(proposal=pid, report=str(out))) == 0

    written = json.loads(out.read_text())
    record = json.loads(_slot(service, pid).read_text())
    # The file on disk and the posted copy are the same document: the CLI never
    # edits a report it has already measured.
    assert record["report"] == written
