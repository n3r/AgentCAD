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
import time

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

CLEARANCE_SPECS = '''\
from agentcad.toolkit.specs import check_clearance

SPECS = [check_clearance("box_1", "box_2", min_mm=1.0, requirement="INT-009")]
'''

# A project spec file whose constructor rejects its own argument: the module
# raises while it executes, so nothing is ever declared.
BROKEN_PROJECT_SPECS = '''\
from agentcad.toolkit.specs import check_clearance

SPECS = [check_clearance("box_1", "box_2", min_mm="wide")]
'''

# A block with connectors, so an instance of it can carry a declarative mate.
# It declares no SPECS of its own: the only kernel work such a report does is
# the project scope, which is where the mate pass lives.
MATE_BLOCK = '''\
from build123d import *

PARAMS = {}

def build(p):
    return Box(10, 10, 10)

def connectors(p, part):
    return {"top": {"type": "rigid", "location": ((0, 0, 5), (0, 0, 0))},
            "base": {"type": "rigid", "location": ((0, 0, -5), (0, 0, 0))}}
'''

MATED_CLEARANCE_SPECS = '''\
from agentcad.toolkit.specs import check_clearance

SPECS = [check_clearance("lower", "upper", min_mm=0.5, requirement="INT-011")]
'''

# A part that declares SPECS above a def that will not parse.
SYNTAX_BROKEN_BOX = GATE_BOX.replace("def build(p):", "def build(p:")

# Only a FEM check: on a machine without the [fem] extra nothing is measured.
FEM_PART = '''\
from build123d import *
from agentcad.toolkit.specs import check_fem_static

PARAMS = {}

SPECS = [check_fem_static({"axis": "z", "side": "min"},
                          {"axis": "z", "side": "max"}, 50.0,
                          max_disp_mm=10.0, requirement="STR-001")]

def build(p):
    return Box(10, 10, 10)
'''

# A project-scope check declared in a PART script: the worker can only report
# it as a named skip, because there is one shape and no assembly.
MISPLACED_SCOPE_PART = '''\
from build123d import *
from agentcad.toolkit.specs import check_clearance

PARAMS = {}

SPECS = [check_clearance("box_1", "box_2", min_mm=1.0, name="misplaced")]

def build(p):
    return Box(10, 10, 10)
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


@pytest.fixture
def mated_demo(stack):
    """'demo' with a mate-driven assembly and a project-scope clearance on
    'feat'. Every spec here is project-scope, so the report's kernel work is
    ``spec_declare`` + the mate pass + ``clearance``."""
    service, registry = stack
    assert "error" not in registry.call("create_project", {"name": "demo"})
    assert "error" not in registry.call(
        "create_part", {"project": "demo", "part_id": "block",
                        "script": MATE_BLOCK})
    service.branches.create("demo", "feat")
    _on(service, "agent_a", "feat")
    assert "error" not in registry.call("set_assembly", {
        "project": "demo",
        "instances": [{"id": "lower", "part": "block"},
                      {"id": "upper", "part": "block",
                       "mate": {"connector": "base", "to_instance": "lower",
                                "to_connector": "top"}}]})
    assert "error" not in registry.call(
        "set_project_specs", {"project": "demo",
                              "script": MATED_CLEARANCE_SPECS})
    _on(service, "browser", "master")
    return service, registry, ProposalManager(service)


def _on(service, client: str, branch: str) -> None:
    locks.set_client_id(client)
    if service.branches.current("demo") != branch:
        service.branches.switch("demo", branch)


def _gate(gates: list[dict], name: str) -> dict:
    return next(g for g in gates if g["name"] == name)


def _named_check(gate: dict, ident: str) -> dict:
    """One failing check row out of a gate's details, by id."""
    rows = gate["details"]["failures"] + gate["details"]["errors"]
    return next(row for row in rows if row["id"] == ident)


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
    sidecars — the part ones AND the assembly one. What is left is the cold
    path a fresh process would take."""
    service.specs._gate_memo.clear()
    service.specs._declaration_cache.clear()
    for sidecar in service.store.cache_dir("demo").glob("*specs.json"):
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


def test_evaluate_specs_without_a_ref_measures_the_callers_own_branch(
        spec_demo):
    """``ref=None`` is the CALLER's branch, all the way through.

    The report runs unpinned — on whatever tree the caller's client id resolves
    to — so the head it is stamped with, and the memo key it is filed under,
    must be that same branch's. Reading the canonical (default-branch) head
    instead hands one client another branch's verdict."""
    service, registry, _manager = spec_demo
    canonical = service.store.canonical_path_of("demo")
    _wall(service, registry, 1.0)               # feat is red, master is green
    service.specs._gate_memo.clear()

    _on(service, "agent_a", "feat")
    on_feat = service.specs.evaluate_specs("demo")
    _on(service, "browser", "master")
    on_master = service.specs.evaluate_specs("demo")

    assert on_feat["status"] == "red"
    assert [c["id"] for c in on_feat["failures"]] == ["box:wall_min"]
    assert on_master["status"] == "green"
    assert on_feat["head"] == service.history.resolve_branch(canonical, "feat")
    assert on_master["head"] == service.history.resolve_branch(canonical,
                                                               "master")
    # Two branches, two verdicts, two memo keys — never one shared entry.
    verdicts = [k for k in service.specs._gate_memo if len(k) == 3]
    assert len(verdicts) == 2 and len(set(verdicts)) == 2


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
    # PRD-004 appends a second provider, `checks`, from a pack that loads at
    # `r` — before this one at `s`. Both replace a placeholder by name.
    assert [getattr(p, "__name__", None)
            for p in service.gate_providers] == ["checks", "specs"]
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


def test_a_skip_is_data_in_a_report_and_red_in_the_gate(spec_demo):
    """The generalized fail-closed rule: **every** skip on a declared check is
    red in the GATE, whatever its reason (here: an interference check with
    nothing to overlap). A report an engineer reads keeps the named skip and
    its hint; a gate decides a merge, and 'declared but not measured' is
    exactly the hole it exists to close."""
    service, registry, manager = spec_demo
    _on(service, "agent_a", "feat")
    assert "error" not in registry.call(
        "set_project_specs", {"project": "demo", "script": PROJECT_SPECS})
    _on(service, "browser", "master")
    pid = _create(manager)["id"]

    specs = _gate(manager.get("demo", pid)["gates"], "specs")

    assert specs["state"] == "fail"
    assert specs["details"]["skips"] == []
    failure = _named_check(specs, "project:no_interference")
    assert failure["details"]["reason"] == "no_instances"
    assert "no_instances" in failure["message"]
    assert failure["details"]["skipped_in_report"] is True

    report = service.specs.run("demo", ref="feat")
    row = next(c for c in report["checks"] if c["kind"] == "interference_free")
    assert row["status"] == "skip" and row["reason"] == "no_instances"


def test_a_fem_check_that_cannot_be_measured_here_is_red_in_the_gate(
        spec_demo, monkeypatch):
    """The finding's own scenario: a proposal declaring only ``check_fem_static``
    on a reviewing machine without the ``[fem]`` extra used to pass the gate
    with zero structural measurement."""
    service, registry, manager = spec_demo
    _on(service, "agent_a", "feat")
    assert "error" not in registry.call(
        "create_part", {"project": "demo", "part_id": "beam",
                        "script": FEM_PART})
    _on(service, "browser", "master")
    pid = _create(manager)["id"]
    monkeypatch.setattr("agentcad.core.specs._fem_available", lambda: False)
    _cold(service)

    specs = _gate(manager.get("demo", pid)["gates"], "specs")

    assert specs["state"] == "fail"
    failure = _named_check(specs, "beam:fem_static")
    assert failure["details"]["reason"] == "fem_extra_missing"
    assert "fem_extra_missing" in failure["message"]
    assert failure["details"]["hint"]

    report = service.specs.run("demo", ref="feat")
    row = next(c for c in report["checks"] if c["kind"] == "fem_static")
    assert row["status"] == "skip" and row["reason"] == "fem_extra_missing"


def test_an_unsupported_scope_skip_is_red_in_the_gate(spec_demo):
    """A project-scope check declared in a part script is a named skip in the
    worker's records — and an unmeasured declared check at the gate."""
    service, registry, manager = spec_demo
    _on(service, "agent_a", "feat")
    assert "error" not in registry.call(
        "create_part", {"project": "demo", "part_id": "misplaced",
                        "script": MISPLACED_SCOPE_PART})
    _on(service, "browser", "master")
    pid = _create(manager)["id"]

    specs = _gate(manager.get("demo", pid)["gates"], "specs")

    assert specs["state"] == "fail"
    failure = _named_check(specs, "misplaced:misplaced")
    assert failure["details"]["reason"] == "unsupported_scope"
    assert "unsupported_scope" in failure["message"]


def test_a_mesh_only_clearance_skip_is_red_in_the_gate_and_a_skip_in_a_report(
        spec_demo, monkeypatch):
    """The one skip the gate refuses to accept (S7).

    Swapping a STEP reference for an STL turns a declared clearance into a
    ``mesh_only`` skip — the distance is simply not measured. A report says so
    and stays a skip (an engineer reading it can see the reason); the GATE's
    contract is fail-closed, so an unmeasured clearance blocks the merge rather
    than passing it."""
    service, registry, manager = spec_demo
    _on(service, "agent_a", "feat")
    assert "error" not in registry.call("set_assembly", {
        "project": "demo",
        "instances": [{"id": "box_1", "part": "box"},
                      {"id": "box_2", "part": "box",
                       "position": [40.0, 0.0, 0.0]}]})
    assert "error" not in registry.call(
        "set_project_specs", {"project": "demo", "script": CLEARANCE_SPECS})
    _on(service, "browser", "master")
    pid = _create(manager)["id"]
    original = service.kernel.request

    def meshy(method, params, timeout_s=None, affinity=None):
        if method == "clearance":
            return {"distance_mm": None, "point_a": None, "point_b": None,
                    "skipped_mesh": ["b"]}
        return original(method, params, timeout_s=timeout_s, affinity=affinity)

    monkeypatch.setattr(service.kernel, "request", meshy)
    _cold(service)
    specs = _gate(manager.get("demo", pid)["gates"], "specs")

    assert specs["state"] == "fail"
    assert [c["id"] for c in specs["details"]["failures"]] == [
        "project:clearance_box_1_box_2"]
    assert "not measured" in specs["details"]["failures"][0]["message"]
    assert specs["details"]["failures"][0]["details"]["reason"] == "mesh_only"
    assert "clearance_box_1_box_2" in specs["summary"]

    # The same evaluation through run_specs keeps it a named skip.
    report = service.specs.run("demo", ref="feat")
    row = next(c for c in report["checks"] if c["kind"] == "clearance")
    assert row["status"] == "skip" and row["reason"] == "mesh_only"


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


def test_a_specs_py_that_will_not_declare_is_red_and_names_the_file(spec_demo):
    """A ``specs.py`` whose constructor rejects its argument declares nothing,
    so the gate used to see an empty project scope and pass — the declaration
    failure went to ``errors[]``, which neither the status nor the gate reads."""
    service, registry, manager = spec_demo
    _on(service, "agent_a", "feat")
    assert "error" not in registry.call(
        "set_project_specs", {"project": "demo",
                              "script": BROKEN_PROJECT_SPECS})
    _on(service, "browser", "master")
    pid = _create(manager)["id"]

    specs = _gate(manager.get("demo", pid)["gates"], "specs")

    assert specs["state"] == "fail"
    failure = _named_check(specs, "project:specs")
    assert failure["kind"] == "declaration"
    assert "specs.py" in failure["message"]
    assert service.specs.run("demo", ref="feat")["status"] == "red"


def test_a_syntax_broken_script_that_declares_specs_is_red_not_skipped(
        spec_demo):
    """``declares_specs`` fails closed. A script that will not parse but
    visibly binds ``SPECS`` used to be classified spec-less, so the gate
    skipped the part and its declared checks never became red."""
    service, _registry, manager = spec_demo
    tree = service.branches.tree_of("demo", "feat")
    (tree / "parts" / "box.py").write_text(SYNTAX_BROKEN_BOX, encoding="utf-8")
    service.history.snapshot(tree, "a script that will not parse")
    pid = _create(manager)["id"]

    specs = _gate(manager.get("demo", pid)["gates"], "specs")

    assert specs["state"] == "fail"
    assert specs["details"]["errors"]
    report = service.specs.run("demo", ref="feat")
    assert report["status"] == "red"
    row = next(c for c in report["checks"] if c["part"] == "box")
    assert row["status"] == "error"
    assert "syntax" in row["message"].lower()


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


def test_a_head_that_moves_during_evaluation_blocks_the_merge_and_retries(
        spec_demo, monkeypatch):
    """The specs gate never returns ``pending``.

    ``ProposalManager.merge`` blocks a ``fail`` and nothing else, so a
    ``pending`` gate — the state a moved head produced — merged content that
    was never evaluated (an external git process can move a head regardless of
    the turn lock). The verdict is still not memoized, so the retry is a real
    re-evaluation rather than a cached refusal."""
    service, _registry, manager = spec_demo
    pid = _propose_and_approve(service, manager)
    runner = service.specs
    runner._gate_memo.clear()          # the memo answers for an unmoved head
    original = runner._report
    moving_on = {"yes": True}

    def moving(*args, **kwargs):
        result = original(*args, **kwargs)
        if moving_on["yes"]:
            _move_head(service, f"note {time.monotonic()}\n")
        return result

    monkeypatch.setattr(runner, "_report", moving)
    specs = _gate(manager.get("demo", pid)["gates"], "specs")

    assert specs["state"] == "fail"
    assert specs["details"]["reason"] == "head_moved"
    assert "retry" in specs["summary"]

    locks.set_client_id("browser")
    with pytest.raises(ConflictError) as excinfo:
        manager.merge("demo", pid)
    assert excinfo.value.details["failing"] == "specs"

    moving_on["yes"] = False           # the source stops moving: retry lands
    landed = manager.merge("demo", pid)
    assert "error" not in landed, landed
    assert _gate(landed["gates"], "specs")["state"] == "pass"


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


def test_specs_py_changed_ignores_a_target_that_moved_after_the_branch(
        spec_demo):
    """The flag is about the PROPOSAL, so it is measured from the merge base.

    A plain ``target..source`` diff reports every file the target gained since
    the branch point as if the source had removed it — here master writes a
    ``specs.py`` the proposal never saw."""
    service, registry, manager = spec_demo
    pid = _create(manager)["id"]
    _on(service, "browser", "master")
    assert "error" not in registry.call(
        "set_project_specs", {"project": "demo", "script": PROJECT_SPECS})

    specs = _gate(manager.get("demo", pid)["gates"], "specs")

    assert specs["details"]["specs_py_changed"] is False


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


def test_a_kernel_call_under_the_gate_is_bounded_by_the_remaining_budget(
        spec_demo, monkeypatch):
    """The budget is a DEADLINE, not a suggestion between parts.

    ``spec_eval`` asks for 300 s and ``fem_static`` for 600; under the gate
    every one of them gets the time the budget has left, so a cold source
    cannot make ``proposal_get`` block for minutes while ``proposal_merge``
    holds the turn lock."""
    service, _registry, _manager = spec_demo
    _cold(service)
    monkeypatch.setattr("agentcad.core.specs.GATE_BUDGET_S", 2.0)
    timeouts: list[float] = []

    def slow(method, params, timeout_s=None, affinity=None):
        timeouts.append(timeout_s)
        time.sleep(min(timeout_s or 0.0, 20.0))
        raise KernelError("timeout", f"{method} timed out", {})

    monkeypatch.setattr(service.kernel, "request", slow)
    started = time.monotonic()
    verdict = service.specs.evaluate_specs("demo", "feat")
    elapsed = time.monotonic() - started

    assert timeouts, "no kernel call was made"
    assert max(timeouts) <= 2.0                # never the 300 s spec_eval one
    assert elapsed < 10.0                      # near the budget, not the call
    assert verdict["status"] == "red"


def test_the_mate_pass_under_the_gate_is_bounded_by_the_remaining_budget(
        mated_demo, monkeypatch):
    """The same rule for the one kernel call the gate does NOT reach through
    ``_kernel``.

    ``_project_key`` and ``_eval_clearance`` resolve the assembly's mates
    through ``service._resolved_instances``, which asked ``resolve_mates`` for
    a flat 120 s — four times the whole gate budget. On a large mated assembly
    that is ``proposal_get`` blocking for two minutes while ``proposal_merge``
    holds the source turn lock."""
    service, _registry, _manager = mated_demo
    _cold(service)
    monkeypatch.setattr("agentcad.core.specs.GATE_BUDGET_S", 2.0)
    issued: list[float | None] = []
    original = service.kernel.request

    def slow_mates(method, params, timeout_s=None, affinity=None):
        if method != "resolve_mates":
            return original(method, params, timeout_s=timeout_s,
                            affinity=affinity)
        issued.append(timeout_s)
        # A resolution that outlasts any timeout it is given, exactly as the
        # kernel client would report it.
        time.sleep(min(5.0 if timeout_s is None else timeout_s, 5.0))
        raise KernelError("timeout", "resolve_mates timed out", {})

    monkeypatch.setattr(service.kernel, "request", slow_mates)
    started = time.monotonic()
    verdict = service.specs.evaluate_specs("demo", "feat")
    elapsed = time.monotonic() - started

    assert issued, "the gate never resolved the assembly's mates"
    assert max(issued) <= 2.0            # never the flat 120 s ceiling
    assert min(issued) >= 0.5            # ...nor a timeout by construction
    assert elapsed < 10.0                # near the budget, not the call
    assert verdict["status"] == "red"
    assert verdict["reason"] == "budget_exceeded"


def test_every_project_evaluator_bounds_the_mate_pass(mated_demo, monkeypatch):
    """The sidecar key is not the only place the assembly is resolved.

    ``clearance`` resolves it directly and ``stackup`` resolves it through
    ``compute_stackup``; both run under the deadline, so neither may ask
    ``resolve_mates`` for its flat ceiling."""
    service, _registry, _manager = mated_demo
    _on(service, "agent_a", "feat")
    issued: list[float | None] = []
    original = service.kernel.request

    def capturing(method, params, timeout_s=None, affinity=None):
        if method == "resolve_mates":
            issued.append(timeout_s)
        return original(method, params, timeout_s=timeout_s, affinity=affinity)

    monkeypatch.setattr(service.kernel, "request", capturing)
    deadline = time.monotonic() + 5.0
    runner = service.specs
    clearance = runner._eval_clearance(
        "demo", {"kind": "clearance", "scope": "project",
                 "options": {"a": "lower", "b": "upper"},
                 "limit": {"min_mm": 0.5}}, deadline)
    stackup = runner._eval_stackup(
        "demo", {"kind": "stackup", "scope": "project",
                 "options": {"axis": "z", "from_instance": "lower",
                             "to_instance": "upper"},
                 "limit": {"within_mm": 1.0}}, deadline)

    assert clearance["status"] in ("pass", "fail")
    assert stackup["status"] in ("pass", "fail")
    assert len(issued) == 2, "one evaluator never resolved the mates"
    assert max(issued) <= 5.0            # never the flat 120 s ceiling
    assert min(issued) >= 0.5


def test_run_specs_resolves_mates_at_the_flat_ceiling(mated_demo, monkeypatch):
    """The other half of the deadline: ``run_specs`` carries none (it is the
    documented exit from an exhausted budget), so the mate pass keeps its own
    120 s ceiling."""
    service, _registry, _manager = mated_demo
    _cold(service)
    issued: list[float | None] = []
    original = service.kernel.request

    def capturing(method, params, timeout_s=None, affinity=None):
        if method == "resolve_mates":
            issued.append(timeout_s)
        return original(method, params, timeout_s=timeout_s, affinity=affinity)

    monkeypatch.setattr(service.kernel, "request", capturing)
    report = service.specs.run("demo", ref="feat")

    assert issued, "run_specs never resolved the assembly's mates"
    assert set(issued) == {120.0}
    assert any(c["kind"] == "clearance" for c in report["checks"])


def test_a_budget_exceeded_verdict_is_memoized_and_run_specs_is_its_exit(
        spec_demo, monkeypatch):
    """Fail-closed AND cheap: a red-with-a-reason verdict is stable for a head.

    Re-paying an exhausted budget on every ``proposal_get`` is the worst of
    both worlds, so the verdict is memoized and stays red until the head moves
    or ``run_specs`` — which is unbounded by design — warms the sidecars."""
    service, registry, manager = spec_demo
    pid = _create(manager)["id"]
    _cold(service)
    monkeypatch.setattr("agentcad.core.specs.GATE_BUDGET_S", -1.0)
    first = _gate(manager.get("demo", pid)["gates"], "specs")
    assert first["state"] == "fail"
    assert first["details"]["reason"] == "budget_exceeded"

    monkeypatch.setattr("agentcad.core.specs.GATE_BUDGET_S", 30.0)
    calls = _counting(service, monkeypatch)
    again = _gate(manager.get("demo", pid)["gates"], "specs")

    assert again["state"] == "fail"                     # the memo answers
    assert again["details"]["reason"] == "budget_exceeded"
    assert calls == {}

    report = registry.call("run_specs", {"project": "demo", "ref": "feat"})
    assert report["status"] == "green", report
    assert _gate(manager.get("demo", pid)["gates"], "specs")["state"] == "pass"


def test_a_head_move_invalidates_the_memo(spec_demo):
    """A stale memo would still say 'pass' after the wall was thinned."""
    service, registry, manager = spec_demo
    pid = _create(manager)["id"]
    assert _gate(manager.get("demo", pid)["gates"], "specs")["state"] == "pass"

    _wall(service, registry, 1.0)

    assert _gate(manager.get("demo", pid)["gates"], "specs")["state"] == "fail"
