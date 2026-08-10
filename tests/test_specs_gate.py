"""``evaluate_specs`` and the fail-closed ``specs`` proposal gate (slice 5).

This is the one place PRD-003 changes another feature's outcome, so it carries
its own suite. The rules being pinned here, all of them deliberate:

* **The gate is evaluated against the proposal's SOURCE branch**, not a merge
  preview — the question a reviewer actually asks is *is the proposed state
  green?*
* **It is fail-closed.** A declared check that failed, errored, or was never
  evaluated (a kernel error, a source branch that will not build, an evaluation
  that blew ``GATE_BUDGET_S``) is RED. A declared-but-unmeasured spec is not
  evidence of green.
* **``allow_invalid`` does not waive it.** That flag is the caller's statement
  about *the kernel's verdict on geometry* (PRD-001's validation gate) and must
  not come to mean two things.
* **``pending`` is reserved** for the one condition a retry resolves: the source
  head moved during evaluation. A verdict never wears a commit it did not
  measure.
* **It is cheap on the second read** — a per-head memo, on top of the shared
  canonical ``.cache/``.

Sections: 1. ``evaluate_specs`` · 2. the gate provider · 3. the gated merge ·
4. cost.
"""

from __future__ import annotations

import shutil

import pytest

from agentcad.core import locks
from agentcad.core.branches import pinned_tree_var
from agentcad.core.model import ConflictError
from agentcad.core.proposals import ProposalManager
from agentcad.core.service import AgentCADService, EventBus
from agentcad.core.tools import build_registry
from agentcad.kernel.client import KernelError

from .conftest import BOX_SCRIPT

_GIT = [
    pytest.mark.integration,
    pytest.mark.portability,
    pytest.mark.skipif(shutil.which("git") is None, reason="git not found on PATH"),
]
pytestmark = _GIT + [pytest.mark.slow]


# A hollow box whose ``wall`` parameter is what drives ``check_wall`` red.
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

BROKEN_GATE_BOX = GATE_BOX.replace("return Box(p.size", "return no_such_name(p.size")

PROJECT_SPECS = '''\
from agentcad.toolkit.specs import check_interference_free

SPECS = [check_interference_free(requirement="INT-003")]
'''


@pytest.fixture(autouse=True)
def _reset_context():
    """Identity and the branch pin are ContextVars: rebind them per test so one
    test's client id can never leak into the next."""
    cid = locks.client_id_var.set("local")
    pin = pinned_tree_var.set(None)
    yield
    locks.client_id_var.reset(cid)
    pinned_tree_var.reset(pin)


@pytest.fixture
def stack(kernel, tmp_path):
    """The real service + registry (NOT make_test_service, which disables the
    snapshot hook): the specs pack appends the gate provider at register()."""
    service = AgentCADService(tmp_path / "projects", kernel, EventBus())
    registry = build_registry(service)
    assert getattr(service, "branches", None) is not None
    return service, registry


@pytest.fixture
def spec_demo(stack):
    """'demo' with one spec-declaring part on master and a 'feat' branch."""
    service, registry = stack
    assert "error" not in registry.call("create_project", {"name": "demo"})
    assert "error" not in registry.call(
        "create_part", {"project": "demo", "part_id": "box",
                        "script": GATE_BOX})
    service.branches.create("demo", "feat")
    return service, registry, ProposalManager(service)


@pytest.fixture
def bare_demo(stack):
    """The same shape with a part that declares nothing at all."""
    service, registry = stack
    assert "error" not in registry.call("create_project", {"name": "demo"})
    assert "error" not in registry.call(
        "create_part", {"project": "demo", "part_id": "box",
                        "script": BOX_SCRIPT})
    service.branches.create("demo", "feat")
    return service, registry, ProposalManager(service)


def _on(service, client: str, branch: str) -> None:
    locks.set_client_id(client)
    if service.branches.current("demo") != branch:
        service.branches.switch("demo", branch)


def _gate(gates: list[dict], name: str) -> dict:
    return next(g for g in gates if g["name"] == name)


def _create(manager, **kwargs) -> dict:
    payload = {"source": "feat", "title": "Thinner wall"}
    payload.update(kwargs)
    return manager.create("demo", **payload)["proposal"]


def _propose_and_approve(service, manager) -> str:
    locks.set_client_id("chat:main")
    pid = _create(manager)["id"]
    locks.set_client_id("browser")
    manager.review("demo", pid, "approve")
    return pid


def _wall(service, registry, value: float) -> None:
    """Set the wall on 'feat' — the one parameter that drives the spec."""
    _on(service, "agent_a", "feat")
    result = registry.call("set_params", {"project": "demo", "part_id": "box",
                                          "values": {"wall": value}})
    assert "error" not in result, result
    _on(service, "browser", "master")


def _move_head(service, text: str = "note\n") -> str:
    """Commit something on 'feat' with no effect on any spec."""
    tree = service.branches.tree_of("demo", "feat")
    (tree / "notes.txt").write_text(text, encoding="utf-8")
    service.history.snapshot(tree, "note")
    canonical = service.store.canonical_path_of("demo")
    return service.history.resolve_branch(canonical, "feat")


def _cold(service) -> None:
    """Drop everything that could answer a spec question without the kernel:
    the per-head memo, the declaration cache and the shared ``.cache/``
    sidecars. What is left is the cold path a fresh process would take."""
    service.specs._gate_memo.clear()
    service.specs._declaration_cache.clear()
    for sidecar in service.store.cache_dir("demo").glob("*.specs.json"):
        sidecar.unlink()


def _counting(service, monkeypatch) -> dict:
    calls: dict = {}
    original = service.kernel.request

    def counting(method, params, timeout_s=None, affinity=None):
        calls[method] = calls.get(method, 0) + 1
        return original(method, params, timeout_s=timeout_s, affinity=affinity)

    monkeypatch.setattr(service.kernel, "request", counting)
    return calls


# ------------------------------------------------------ 1. evaluate_specs


def test_evaluate_specs_is_green_for_a_good_branch_and_records_its_head(
        spec_demo):
    service, _registry, _manager = spec_demo
    canonical = service.store.canonical_path_of("demo")

    verdict = service.specs.evaluate_specs("demo", "feat")

    assert verdict["available"] is True
    assert verdict["status"] == "green"
    assert verdict["ref"] == "feat"
    assert verdict["head"] == service.history.resolve_branch(canonical, "feat")
    assert verdict["checked_at"].endswith("Z")
    assert verdict["summary"]["failed"] == 0 and verdict["summary"]["total"] == 2
    assert verdict["failures"] == [] and verdict["errors"] == []
    assert verdict["reason"] is None
    assert pinned_tree_var.get() is None            # the pin is released


def test_evaluate_specs_is_red_for_a_branch_with_a_broken_budget(spec_demo):
    """AC7's other half: the branch, not the caller's tree, is what is
    measured — master stays green while feat is red."""
    service, registry, _manager = spec_demo
    _wall(service, registry, 1.0)

    on_branch = service.specs.evaluate_specs("demo", "feat")

    assert on_branch["status"] == "red"
    assert [c["id"] for c in on_branch["failures"]] == ["box:wall_min"]
    assert on_branch["failures"][0]["measured"] == pytest.approx(1.0, abs=0.15)
    assert on_branch["failures"][0]["requirement"] == "ENG-014"
    assert service.specs.evaluate_specs("demo", "master")["status"] == "green"


def test_evaluate_specs_is_skip_when_the_ref_declares_nothing(bare_demo):
    service, _registry, _manager = bare_demo
    verdict = service.specs.evaluate_specs("demo", "feat")
    assert verdict["status"] == "skip"
    assert verdict["summary"]["total"] == 0
    assert verdict["available"] is True


def test_a_head_that_moves_during_evaluation_is_pending(spec_demo,
                                                        monkeypatch):
    """The one condition a retry resolves. A verdict must never wear a commit
    it did not measure."""
    service, _registry, _manager = spec_demo
    runner = service.specs
    original = runner._report

    def moving(*args, **kwargs):
        result = original(*args, **kwargs)
        _move_head(service)
        return result

    monkeypatch.setattr(runner, "_report", moving)
    verdict = runner.evaluate_specs("demo", "feat")

    assert verdict["status"] == "pending"
    assert verdict["available"] is False
    assert verdict["reason"] == "head_moved"


# ------------------------------------------------------ 2. the gate provider


def test_the_provider_replaces_the_placeholder_and_keeps_five_gates(
        spec_demo):
    service, _registry, manager = spec_demo
    assert [getattr(p, "__name__", None)
            for p in service.gate_providers] == ["specs"]
    pid = _create(manager)["id"]

    gates = manager.get("demo", pid)["gates"]

    assert [g["name"] for g in gates] == [
        "state", "approvals", "validation", "specs", "checks"]
    specs = _gate(gates, "specs")
    assert specs["state"] == "pass"
    assert specs["summary"] != "spec evaluation not installed"
    assert specs["details"]["ref"] == "feat"
    assert specs["details"]["source_head"]
    assert specs["details"]["status"] == "green"
    assert specs["details"]["specs_py_changed"] is False
    assert specs["details"]["reason"] is None


def test_a_ref_declaring_nothing_is_skipped_not_pass(bare_demo):
    service, _registry, manager = bare_demo
    pid = _create(manager)["id"]
    specs = _gate(manager.get("demo", pid)["gates"], "specs")
    assert specs["state"] == "skipped"
    assert "no design specs" in specs["summary"]


def test_a_skip_is_data_and_the_gate_still_passes(spec_demo):
    """A named skip (here: an interference check with nothing to overlap) is
    reported in the summary and never turns the gate red."""
    service, registry, manager = spec_demo
    _on(service, "agent_a", "feat")
    assert "error" not in registry.call(
        "set_project_specs", {"project": "demo", "script": PROJECT_SPECS})
    _on(service, "browser", "master")
    pid = _create(manager)["id"]

    specs = _gate(manager.get("demo", pid)["gates"], "specs")

    assert specs["state"] == "pass"
    assert [c["id"] for c in specs["details"]["skips"]] == [
        "project:no_interference"]
    assert "skip" in specs["summary"]


def test_a_provider_that_raises_internally_is_still_one_specs_gate(
        spec_demo, monkeypatch):
    """It catches everything itself: ``ProposalManager.gates``'s own
    except-branch would degrade to ``pending``, which is not fail-closed."""
    service, _registry, manager = spec_demo
    pid = _create(manager)["id"]

    def boom(proj, ref=None):
        raise RuntimeError("the runner exploded")

    monkeypatch.setattr(service.specs, "evaluate_specs", boom)
    gates = manager.get("demo", pid)["gates"]

    assert [g["name"] for g in gates].count("specs") == 1
    specs = _gate(gates, "specs")
    assert specs["state"] == "fail"
    assert specs["details"]["reason"] == "evaluation_failed"
    assert "run_specs" in specs["summary"]


def test_a_kernel_error_is_red_not_pending(spec_demo, monkeypatch):
    """Fail-closed: 'we do not know' is not 'it is fine'."""
    service, _registry, manager = spec_demo
    pid = _create(manager)["id"]
    original = service.kernel.request

    def failing(method, params, timeout_s=None, affinity=None):
        if method == "spec_eval":
            raise KernelError("kernel_error", "the worker died", {})
        return original(method, params, timeout_s=timeout_s, affinity=affinity)

    _cold(service)
    monkeypatch.setattr(service.kernel, "request", failing)
    specs = _gate(manager.get("demo", pid)["gates"], "specs")

    assert specs["state"] == "fail"
    assert specs["details"]["errors"]
    assert "run_specs" in specs["summary"]


def test_a_source_branch_that_does_not_build_is_red(spec_demo):
    service, registry, manager = spec_demo
    _on(service, "agent_a", "feat")
    assert registry.call("update_part_script",
                         {"project": "demo", "part_id": "box",
                          "script": BROKEN_GATE_BOX}).get("error")
    _on(service, "browser", "master")
    pid = _create(manager)["id"]

    specs = _gate(manager.get("demo", pid)["gates"], "specs")

    assert specs["state"] == "fail"
    assert specs["details"]["errors"]


def test_a_declared_check_that_was_never_evaluated_is_red(spec_demo,
                                                          monkeypatch):
    """The budget is the only way a declared check goes unmeasured without an
    error, and it is red — never a silent green, never a ``pending``."""
    service, _registry, manager = spec_demo
    pid = _create(manager)["id"]
    _cold(service)                     # the memo answers for an unmoved head
    monkeypatch.setattr("agentcad.core.specs.GATE_BUDGET_S", -1.0)

    specs = _gate(manager.get("demo", pid)["gates"], "specs")

    assert specs["state"] == "fail"
    assert specs["details"]["reason"] == "budget_exceeded"
    assert "run_specs" in specs["summary"]
    assert [c["status"] for c in specs["details"]["errors"]] == ["error"]


def test_a_head_that_moves_during_evaluation_makes_the_gate_pending(
        spec_demo, monkeypatch):
    service, _registry, manager = spec_demo
    pid = _create(manager)["id"]
    runner = service.specs
    runner._gate_memo.clear()          # the memo answers for an unmoved head
    original = runner._report

    def moving(*args, **kwargs):
        result = original(*args, **kwargs)
        _move_head(service)
        return result

    monkeypatch.setattr(runner, "_report", moving)
    specs = _gate(manager.get("demo", pid)["gates"], "specs")

    assert specs["state"] == "pending"
    assert specs["details"]["reason"] == "head_moved"


def test_specs_py_changed_says_a_proposal_touched_the_spec_file(spec_demo):
    """The cheap half of 'a proposal that weakens a spec must be visible':
    ``packet.py`` builds rows only for ``parts/*.py``, so a changed root
    ``specs.py`` would otherwise get no row and no validation."""
    service, registry, manager = spec_demo
    pid = _create(manager)["id"]
    assert _gate(manager.get("demo", pid)["gates"],
                 "specs")["details"]["specs_py_changed"] is False

    _on(service, "agent_a", "feat")
    assert "error" not in registry.call(
        "set_project_specs", {"project": "demo", "script": PROJECT_SPECS})
    _on(service, "browser", "master")

    assert _gate(manager.get("demo", pid)["gates"],
                 "specs")["details"]["specs_py_changed"] is True


def test_the_run_specs_description_states_the_gate_rule(spec_demo):
    """The tool description is the agent's only documentation at call time, so
    the decision most likely to be 'fixed' wrongly later is written into it."""
    _service, registry, _manager = spec_demo
    description = registry.get("run_specs").description.lower()
    assert "allow_invalid" in description
    assert "gate" in description and "merge" in description


# ------------------------------------------------------- 3. the gated merge


def test_a_red_specs_gate_blocks_the_merge_and_allow_invalid_cannot_waive_it(
        spec_demo):
    """PRD-002's rule (any ``fail`` refuses before anything is merged) meets
    PRD-003's semantics. ``allow_invalid`` reaches PRD-001's kernel gate and
    nothing else — asserted explicitly, because this is the decision most
    likely to be 'fixed' wrongly later."""
    service, registry, manager = spec_demo
    canonical = service.store.canonical_path_of("demo")
    _wall(service, registry, 1.0)
    pid = _propose_and_approve(service, manager)
    before = service.history.resolve_branch(canonical, "master")

    with pytest.raises(ConflictError) as excinfo:
        manager.merge("demo", pid)
    assert excinfo.value.details["failing"] == "specs"
    assert _gate(excinfo.value.details["gates"], "specs")["state"] == "fail"
    assert "wall_min" in excinfo.value.message

    with pytest.raises(ConflictError) as override:
        manager.merge("demo", pid, allow_invalid=True)
    assert override.value.details["failing"] == "specs"

    # Nothing was merged and no staged merge was left behind.
    assert service.history.resolve_branch(canonical, "master") == before
    assert registry.call("merge_status", {"project": "demo"})["merge"] is None
    assert manager.get("demo", pid)["proposal"]["state"] != "merged"


def test_fixing_the_geometry_turns_the_gate_green_and_the_merge_lands(
        spec_demo):
    service, registry, manager = spec_demo
    canonical = service.store.canonical_path_of("demo")
    before = service.history.resolve_branch(canonical, "master")
    _wall(service, registry, 1.0)
    pid = _propose_and_approve(service, manager)
    with pytest.raises(ConflictError):
        manager.merge("demo", pid)

    _wall(service, registry, 2.6)          # the fix is a commit on the source
    locks.set_client_id("browser")
    landed = manager.merge("demo", pid)

    assert "error" not in landed, landed
    assert _gate(landed["gates"], "specs")["state"] == "pass"
    detail = manager.get("demo", pid)
    assert detail["proposal"]["state"] == "merged"
    assert service.history.resolve_branch(canonical, "master") != before
    assert "merged" in [e["action"] for e in detail["audit"]]


# --------------------------------------------------------------- 4. cost


def test_a_second_read_at_the_same_head_costs_no_kernel_call(spec_demo,
                                                             monkeypatch):
    """The per-head memo. The gate runs on EVERY ``proposal_get`` — PRD-002
    caches none — so a repeated read must be free."""
    service, _registry, manager = spec_demo
    pid = _create(manager)["id"]
    assert _gate(manager.get("demo", pid)["gates"], "specs")["state"] == "pass"

    calls = _counting(service, monkeypatch)
    assert _gate(manager.get("demo", pid)["gates"], "specs")["state"] == "pass"

    assert calls == {}


def test_a_head_move_invalidates_the_memo(spec_demo):
    """A stale memo would still say 'pass' after the wall was thinned."""
    service, registry, manager = spec_demo
    pid = _create(manager)["id"]
    assert _gate(manager.get("demo", pid)["gates"], "specs")["state"] == "pass"

    _wall(service, registry, 1.0)

    assert _gate(manager.get("demo", pid)["gates"], "specs")["state"] == "fail"
