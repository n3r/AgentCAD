"""PRD-003 acceptance criteria — one named test per AC (slice 7).

The feature's mechanics are covered in depth by ``tests/test_specs_toolkit.py``
(the ten constructors and their eager validation),
``tests/test_specs_kernel.py`` (``spec_declare`` / ``spec_eval`` /
``clearance``), ``tests/test_specs.py`` (the runner: tiers, sidecars, the
report), ``tests/test_specs_api.py`` (tools, the rebuild seam, routes) and
``tests/test_specs_gate.py`` (``evaluate_specs`` and the fail-closed gate).
This file is the *contract* layer: it walks each acceptance criterion of
``docs/prd/in-progress/PRD-003-design-specs-executable.md`` end to end through
the real stack — tools, the kernel, git and the shipped ``examples/rocketry``
(on a copy) — so a reviewer can map AC → test without reading the unit suites.

| AC | Test |
|----|------|
| AC1 | ``test_ac1_rocketry_ships_green_specs_and_thinning_turns_red`` — the
        rocketry example on a copy: green as shipped across the chamber mass
        budget, the nozzle wall minimum and the flange bolt-circle ligament,
        and ``set_params {"wall": 2.0}`` turns ``run_specs`` red naming
        ``check_wall`` with measured, limit and a non-null location |
| AC2 | ``test_ac2_failing_spec_still_lands_geometry`` |
| AC3 | ``test_ac3_fem_check_skips_without_extra_and_evaluates_with_it`` |
| AC4 | ``test_ac4_project_specs_measure_clearance_and_name_interference`` |
| AC5 | ``test_ac5_raising_predicate_is_an_error_not_a_crash`` |
| AC6 | ``test_ac6_requirements_group_and_list_specs_does_not_build`` |
| AC7 | ``test_ac7_evaluate_specs_green_for_a_good_branch_red_for_a_broken_one``
        — the plan worded this "green for a tag"; a tag ref is a
        ``validation_error`` **by design** (PRD-001 X1: a tag must never answer
        for a branch), so the tag half asserts that decision instead. See
        ``docs/changelog/0091-spec-gate.md`` |
| AC8 | ``test_ac8_spec_chips_verified_in_browser`` — the PRD-001 AC6 /
        PRD-002 precedent: slice 6's real browser session is asserted to be on
        the changelog record, not re-driven from the suite |
| AC9 | ``test_ac9_specless_parts_add_no_kernel_work`` plus the full-suite run
        and ``git diff --name-status main -- tests/`` cited in
        ``docs/changelog/0093-specs-docs-and-acceptance.md`` |

Everything here drives the real service (whose publish hook snapshots into
git), so the module carries ``integration`` + ``portability`` and skips without
git; the cases that build geometry are additionally ``slow``.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from agentcad.core import locks
from agentcad.core import specs as specs_module
from agentcad.core.branches import pinned_tree_var
from agentcad.core.model import ValidationError
from agentcad.core.service import AgentCADService, EventBus
from agentcad.core.tools import build_registry

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

_ROCKETRY_ONLY = pytest.mark.skipif(
    not (ROCKETRY / "project.json").is_file(), reason="rocketry example not present")


# A hollow box: the cavity is what gives ``check_wall`` something to measure,
# and ``wall`` is the parameter that drives it red.
HOLLOW_BOX = '''\
from build123d import *
from agentcad.toolkit.specs import check_mass, check_valid, check_wall

PARAMS = {"size": {"default": 20.0, "min": 10.0, "max": 60.0, "unit": "mm",
                   "description": "outer edge"},
          "wall": {"default": 2.5, "min": 0.5, "max": 5.0, "unit": "mm",
                   "description": "wall thickness"}}

SPECS = [
    check_valid(requirement="ENG-014"),
    check_wall(min_mm=2.0, grid=4, requirement="ENG-014"),
    check_mass(max_g=500.0, requirement="SYS-042"),
]

def build(p):
    inner = p.size - 2 * p.wall
    return Box(p.size, p.size, p.size) - Box(inner, inner, inner)
'''

RAISING_PREDICATE = '''\
from build123d import *
from agentcad.toolkit.specs import check_that, check_valid

PARAMS = {"size": {"default": 10.0, "min": 5.0, "max": 20.0, "unit": "mm",
                   "description": "edge"}}

SPECS = [
    check_valid(requirement="ENG-001"),
    check_that(lambda part, metrics: metrics["no_such_key"], "boom",
               requirement="ENG-001"),
]

def build(p):
    return Box(p.size, p.size, p.size)
'''

FEM_BOX = '''\
from build123d import *
from agentcad.toolkit.specs import check_fem_static

PARAMS = {"size": {"default": 20.0, "min": 10.0, "max": 40.0, "unit": "mm",
                   "description": "edge"}}

SPECS = [
    check_fem_static({"axis": "z", "side": "min"}, {"axis": "z", "side": "max"},
                     50.0, max_disp_mm=10.0, requirement="STR-001"),
]

def build(p):
    return Box(p.size, p.size, p.size)
'''

# ``cube_1`` and ``cube_2`` are 0.4 mm apart (the clearance wants 1.0) and
# ``cube_3`` sits half inside ``cube_1`` (the interference).
ASSEMBLY_SPECS = '''\
from agentcad.toolkit.specs import check_clearance, check_interference_free

SPECS = [
    check_interference_free(requirement="INT-003"),
    check_clearance("cube_1", "cube_2", min_mm=1.0, name="side_gap",
                    requirement="INT-003"),
]
'''


@pytest.fixture(autouse=True)
def _reset_context():
    """Identity and the branch pin are ContextVars: rebind them per test so one
    test's identity or pin can never leak into the next."""
    cid = locks.client_id_var.set("local")
    pin = pinned_tree_var.set(None)
    yield
    locks.client_id_var.reset(cid)
    pinned_tree_var.reset(pin)


@pytest.fixture
def stack(kernel, tmp_path):
    """The real service + registry (NOT make_test_service, which disables the
    snapshot hook): the spec pack installs ``service.specs``, the rebuild seam
    and the gate provider at ``build_registry`` time."""
    service = AgentCADService(tmp_path / "projects", kernel, EventBus())
    registry = build_registry(service)
    assert getattr(service, "specs", None) is not None
    return service, registry


def _counting(service, monkeypatch) -> dict:
    """Count kernel methods by name (the ``tests/test_specs.py`` probe)."""
    calls: dict = {}
    original = service.kernel.request

    def counting(method, params, timeout_s=None, affinity=None):
        calls[method] = calls.get(method, 0) + 1
        return original(method, params, timeout_s=timeout_s, affinity=affinity)

    monkeypatch.setattr(service.kernel, "request", counting)
    return calls


def _copy_rocketry(registry, tmp_path) -> tuple[str, Path]:
    """Open a COPY of the shipped example — it is never mutated in place."""
    dest = tmp_path / "ex" / "rocketry"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROCKETRY, dest, ignore=shutil.ignore_patterns(".cache", "exports"))
    opened = registry.call("open_project", {"path": str(dest)})
    assert "error" not in opened, opened
    return opened["name"], dest


def _by_id(report: dict, ident: str) -> dict:
    return next(check for check in report["checks"] if check["id"] == ident)


def _status(report: dict) -> dict:
    return {check["id"]: check["status"] for check in report["checks"]}


# ------------------------------------------------------------------- AC1


@pytest.mark.slow
@_ROCKETRY_ONLY
@pytest.mark.timeout(900)
def test_ac1_rocketry_ships_green_specs_and_thinning_turns_red(stack, tmp_path):
    """AC1 — ``examples/rocketry`` ships real design intent: the chamber mass
    budget (SYS-042), the nozzle wall minimum (ENG-014) and the flange
    bolt-circle ligament plus the assembly gaps (INT-003). Green as shipped;
    thinning the nozzle wall turns ``run_specs`` red naming ``check_wall`` with
    measured vs limit and the thin point's location.
    """
    service, registry = stack
    proj, _dest = _copy_rocketry(registry, tmp_path)

    report = registry.call("run_specs", {"project": proj})
    assert "error" not in report, report
    assert report["status"] == "green", _status(report)
    assert report["summary"]["failed"] == 0 and report["summary"]["errors"] == 0

    # The three subjects the criterion names, each measured, plus the assembly
    # gaps the project file declares.
    shipped = _status(report)
    for ident in ("nozzle:mass_max", "nozzle:wall_min",
                  "flange:bolt_circle_ligament", "project:no_interference",
                  "project:flange_bore_gap", "project:injector_gasket_gap"):
        assert shipped.get(ident) == "pass", (ident, shipped)
    assert _by_id(report, "nozzle:mass_max")["measured"] > 0
    assert _by_id(report, "flange:bolt_circle_ligament")["measured"] > 2.0
    # A part that declares nothing is absent, not silently green.
    assert "injector_plate" not in report["parts"]
    assert {req: block["status"] for req, block in report["requirements"].items()} \
        == {"ENG-014": "pass", "SYS-042": "pass", "INT-003": "pass"}

    # --- thin the wall past the budget
    thinned = registry.call(
        "set_params", {"project": proj, "part_id": "nozzle", "values": {"wall": 2.0}})
    assert "error" not in thinned, thinned

    report = registry.call("run_specs", {"project": proj})
    assert report["status"] == "red", _status(report)
    failed = [check for check in report["checks"] if check["status"] == "fail"]
    assert [check["id"] for check in failed] == ["nozzle:wall_min"]

    wall = failed[0]
    assert wall["kind"] == "wall"
    assert wall["requirement"] == "ENG-014"
    assert wall["measured"] < wall["limit"]["min_mm"]        # measured vs limit
    assert wall["unit"] == "mm"
    assert wall["location"] is not None and len(wall["location"]) == 3
    assert "wall" in wall["message"]
    assert report["requirements"]["ENG-014"]["status"] == "fail"
    # The mass budget the thinner wall was bought with is still green.
    assert _by_id(report, "nozzle:mass_max")["status"] == "pass"


# ------------------------------------------------------------------- AC2


@pytest.mark.slow
@_ROCKETRY_ONLY
@pytest.mark.timeout(900)
def test_ac2_failing_spec_still_lands_geometry(stack, tmp_path):
    """AC2 — a failing spec is signal, never a failed build: the same
    ``set_params`` returns ``ok: true``, the mesh lands on disk, and the failure
    rides in the post-state's ``specs`` block.
    """
    service, registry = stack
    proj, _dest = _copy_rocketry(registry, tmp_path)

    result = service.set_params(proj, "nozzle", {"wall": 2.0})
    assert result["ok"] is True, result
    assert result["metrics"]["volume_mm3"] > 0
    assert service.ensure_mesh(proj, "nozzle").is_file()

    summary = result["specs"]
    assert summary["status"] == "red"
    assert summary["summary"]["failed"] == 1
    failing = [c for c in summary["checks"] if c["status"] == "fail"]
    assert [c["name"] for c in failing] == ["wall_min"]
    # The part is readable, and the same verdict rides ``get_part`` (what the
    # inspector chips render) without a second build.
    detail = service.get_part(proj, "nozzle")
    assert detail["status"]["state"] == "ok"
    assert detail["specs"]["status"] == "red"


# ------------------------------------------------------------------- AC3


@pytest.mark.slow
def test_ac3_fem_check_skips_without_extra_and_evaluates_with_it(
        stack, monkeypatch):
    """AC3 — ``check_fem_static`` degrades honestly. Without the ``[fem]``
    extra it is ``skip`` with ``reason: "fem_extra_missing"`` and a hint (a
    skip is data, never a failure, and never a red report); with the extra it
    produces a real verdict. The suite is green **without** the extra: the
    second half runs only when the solver stack imports.
    """
    service, registry = stack
    assert "error" not in registry.call("create_project", {"name": "demo"})
    assert "error" not in registry.call(
        "create_part", {"project": "demo", "part_id": "fem_box", "script": FEM_BOX})

    # --- the half that must hold on every machine
    monkeypatch.setattr(specs_module, "_fem_available", lambda: False)
    report = registry.call("run_specs", {"project": "demo"})
    check = _by_id(report, "fem_box:fem_static")
    assert check["status"] == "skip"
    assert check["reason"] == "fem_extra_missing"
    assert check["hint"], "a skip always carries a hint"
    assert "fem" in check["hint"]
    assert report["status"] == "green"                # a skip is not a failure
    assert report["summary"]["skipped"] == 1 and report["summary"]["failed"] == 0

    # --- the paired half, on a machine that has the extra. A plain guard, not
    # ``pytest.importorskip``: skipping here would take the half above with it,
    # and AC3's whole point is that the criterion holds on a machine WITHOUT
    # the extra. The declaration is readable either way.
    monkeypatch.undo()
    declared = registry.call("list_specs", {"project": "demo"})
    assert [d["kind"] for d in declared["parts"]["fem_box"]["specs"]] \
        == ["fem_static"]
    if specs_module._fem_available():
        report = registry.call("run_specs", {"project": "demo"})
        check = _by_id(report, "fem_box:fem_static")
        assert check["status"] in ("pass", "fail")
        assert check["measured"]["max_disp_mm"] >= 0
        assert check["limit"] == {"max_disp_mm": 10.0}
        assert report["requirements"]["STR-001"]["checks"] == ["fem_box:fem_static"]


# ------------------------------------------------------------------- AC4


@pytest.mark.slow
def test_ac4_project_specs_measure_clearance_and_name_interference(stack):
    """AC4 — project scope, from a root ``specs.py``: ``check_clearance``
    reports the MEASURED minimum distance for a too-close pair (a number, not a
    verdict), and ``check_interference_free`` names the offending pair in
    ``details.pairs``.
    """
    service, registry = stack
    assert "error" not in registry.call("create_project", {"name": "demo"})
    assert "error" not in registry.call(
        "create_part", {"project": "demo", "part_id": "cube", "script": BOX_SCRIPT})
    service.set_assembly("demo", [
        {"id": "cube_1", "part": "cube", "position": [0.0, 0.0, 0.0]},
        {"id": "cube_2", "part": "cube", "position": [10.4, 0.0, 0.0]},
        {"id": "cube_3", "part": "cube", "position": [0.0, 5.0, 0.0]},
    ])
    written = registry.call(
        "set_project_specs", {"project": "demo", "script": ASSEMBLY_SPECS})
    assert "error" not in written, written
    assert written["declaration_error"] is None
    assert [d["name"] for d in written["specs"]] == ["no_interference", "side_gap"]

    report = registry.call("run_specs", {"project": "demo"})
    assert report["status"] == "red", _status(report)

    gap = _by_id(report, "project:side_gap")
    assert gap["status"] == "fail"
    assert gap["measured"] == pytest.approx(0.4, abs=1e-6)   # the real distance
    assert gap["limit"] == {"min_mm": 1.0}
    assert gap["unit"] == "mm"
    assert gap["location"] is not None
    assert gap["requirement"] == "INT-003"

    overlap = _by_id(report, "project:no_interference")
    assert overlap["status"] == "fail"
    pairs = overlap["details"]["pairs"]
    assert pairs, overlap
    named = {frozenset((pair["a"], pair["b"])) for pair in pairs}
    assert frozenset(("cube_1", "cube_3")) in named
    assert overlap["measured"] > 0                  # the overlap volume, mm^3
    # Both are project scope: a rebuild defers them rather than pretending.
    rebuilt = service._rebuild("demo", "cube")
    assert rebuilt["ok"] is True
    assert rebuilt["specs"] is None                 # the part declares nothing


# ------------------------------------------------------------------- AC5


@pytest.mark.slow
def test_ac5_raising_predicate_is_an_error_not_a_crash(stack):
    """AC5 — a ``check_that`` predicate that raises is ``status: "error"`` with
    a traceback: the rebuild still returns ``ok: true``, the sibling checks in
    the same part still report, and the worker survives to answer the next
    call.
    """
    service, registry = stack
    assert "error" not in registry.call("create_project", {"name": "demo"})
    created = registry.call(
        "create_part", {"project": "demo", "part_id": "boom",
                        "script": RAISING_PREDICATE})
    assert "error" not in created, created

    result = service.set_params("demo", "boom", {"size": 12.0})
    assert result["ok"] is True, result                 # geometry still lands
    assert result["metrics"]["volume_mm3"] > 0

    report = registry.call("run_specs", {"project": "demo", "part_id": "boom"})
    broken = _by_id(report, "boom:boom")
    assert broken["status"] == "error"
    assert broken["details"]["traceback"], broken
    assert broken["measured"] is None
    # The sibling check in the same part was still evaluated.
    assert _by_id(report, "boom:valid")["status"] == "pass"
    # An error is not a fail, and it is not green either: "we do not know".
    assert report["status"] == "red"
    assert report["summary"] == {"passed": 1, "failed": 0, "skipped": 0,
                                 "errors": 1, "total": 2}
    assert report["requirements"]["ENG-001"]["status"] == "fail"
    # The worker is still alive.
    assert service.get_metrics("demo", "boom")["volume_mm3"] > 0


# ------------------------------------------------------------------- AC6


@pytest.mark.slow
def test_ac6_requirements_group_and_list_specs_does_not_build(stack, monkeypatch):
    """AC6 — requirement strings flow declared → evaluated → grouped, and
    ``list_specs`` reads declared intent with **zero** ``build`` kernel calls
    (an ``ast`` presence scan plus one ``spec_declare`` per declaring file).
    """
    service, registry = stack
    assert "error" not in registry.call("create_project", {"name": "demo"})
    assert "error" not in registry.call(
        "create_part", {"project": "demo", "part_id": "box", "script": HOLLOW_BOX})
    assert "error" not in registry.call(
        "create_part", {"project": "demo", "part_id": "plain", "script": BOX_SCRIPT})

    calls = _counting(service, monkeypatch)
    declared = registry.call("list_specs", {"project": "demo"})
    assert "error" not in declared, declared
    assert calls.get("build", 0) == 0                    # AC6: no build at all
    assert calls.get("spec_eval", 0) == 0
    assert calls.get("spec_declare", 0) == 1             # only the declaring part

    assert sorted(declared["parts"]) == ["box"]          # 'plain' declares none
    assert [d["name"] for d in declared["parts"]["box"]["specs"]] == \
        ["valid", "wall_min", "mass_max"]
    assert declared["requirements"] == {
        "ENG-014": ["box:valid", "box:wall_min"], "SYS-042": ["box:mass_max"]}

    report = registry.call("run_specs", {"project": "demo"})
    grouped = {req: block["checks"] for req, block in report["requirements"].items()}
    assert grouped == declared["requirements"], "declared and evaluated must agree"
    assert report["requirements"]["ENG-014"]["status"] == "pass"
    # A requirement with zero checks does not exist to us (FR12).
    assert "TOL-001" not in report["requirements"]


# ------------------------------------------------------------------- AC7


@pytest.mark.slow
def test_ac7_evaluate_specs_green_for_a_good_branch_red_for_a_broken_one(stack):
    """AC7 — the gate seam over PRD-001 refs: ``evaluate_specs`` is green for a
    good state and red for a branch with a broken budget, each verdict pinned
    to the head it measured.

    The plan worded this "green for a TAG"; a tag ref is a ``validation_error``
    by design — ``git rev-parse`` searches tags before branches, so a tag named
    like a branch would otherwise answer for it (PRD-001 X1) — so the tag half
    asserts that decision instead of a green verdict it must never give.
    """
    service, registry = stack
    assert "error" not in registry.call("create_project", {"name": "demo"})
    assert "error" not in registry.call(
        "create_part", {"project": "demo", "part_id": "box", "script": HOLLOW_BOX})
    default = service.branches.default_branch("demo")
    tagged = registry.call(
        "version_tag", {"project": "demo", "name": "good", "message": "as shipped"})
    assert "error" not in tagged, tagged

    # --- the good state
    green = service.specs.evaluate_specs("demo", ref=default)
    assert green["status"] == "green", green
    assert green["available"] is True
    assert green["failures"] == [] and green["errors"] == []
    assert green["head"], "a verdict names the commit it measured"
    assert green["summary"]["passed"] == 3

    # --- a branch that breaks the wall budget
    assert "error" not in registry.call(
        "branch_create", {"project": "demo", "name": "thin"})
    assert "error" not in registry.call(
        "branch_switch", {"project": "demo", "name": "thin"})
    assert "error" not in registry.call(
        "set_params", {"project": "demo", "part_id": "box", "values": {"wall": 0.8}})
    registry.call("branch_switch", {"project": "demo", "name": default})

    red = service.specs.evaluate_specs("demo", ref="thin")
    assert red["status"] == "red", red
    assert red["available"] is True
    assert [f["id"] for f in red["failures"]] == ["box:wall_min"]
    assert red["failures"][0]["measured"] < red["failures"][0]["limit"]["min_mm"]
    assert red["head"] != green["head"], "the two refs are different states"
    # The default branch is untouched by reading another ref.
    assert service.branches.current("demo") == default
    assert service.specs.evaluate_specs("demo", ref=default)["status"] == "green"

    # --- the tag decision (the AC's reworded half)
    with pytest.raises(ValidationError) as excinfo:
        service.specs.evaluate_specs("demo", ref="good")
    assert "tag" in str(excinfo.value).lower()


# ------------------------------------------------------------------- AC8


def test_ac8_spec_chips_verified_in_browser():
    """AC8 — "spec chips render and live-update in a real browser session on
    rebuild, zero console errors" was driven for real in slice 6 (headless
    Chrome on a scratch server, a green → red → green wall check in both
    themes, screenshots). This is the evidence check: it asserts the session is
    on the record, so the criterion has a named check that fails if the record
    is removed, without re-driving a browser from the test suite (the PRD-001
    AC6 / PRD-002 AC1 pattern).
    """
    entry = CHANGELOG / "0092-spec-chips-ui.md"
    assert entry.is_file(), "slice 6 changelog entry is missing"
    text = entry.read_text(encoding="utf-8").lower()
    assert "ac8" in text
    for phrase in ("browser", "chip", "console", "light mode", "screenshot"):
        assert phrase in text, f"browser evidence does not mention {phrase!r}"
    # the live green -> red -> green flip, and a clean console
    assert "0 page errors" in text or "zero console errors" in text
    assert "spec-fail" in text and "spec-skip" in text


# ------------------------------------------------------------------- AC9


@pytest.mark.slow
def test_ac9_specless_parts_add_no_kernel_work(stack, monkeypatch):
    """AC9 — a project whose parts declare nothing costs nothing: a rebuild and
    a ``run_specs`` issue **zero** ``spec_declare`` / ``spec_eval`` calls, and
    the rebuild post-state says ``specs: null`` — "none declared", which is not
    "not evaluated".

    The presence scan is an ``ast.parse`` of the script, so the short-circuit
    happens before any kernel call can be made. The full-suite count is cited
    in ``docs/changelog/0093-specs-docs-and-acceptance.md``.
    """
    service, registry = stack
    assert "error" not in registry.call("create_project", {"name": "bare"})
    assert "error" not in registry.call(
        "create_part", {"project": "bare", "part_id": "plain", "script": BOX_SCRIPT})
    assert service.get_metrics("bare", "plain")["volume_mm3"] > 0
    # Force the full cache-key path rather than a status-cache hit.
    service._status.clear()

    calls = _counting(service, monkeypatch)
    rebuilt = service.set_params("bare", "plain", {"size": 12.0})
    assert rebuilt["ok"] is True
    assert rebuilt["specs"] is None                       # declared none

    report = registry.call("run_specs", {"project": "bare"})
    assert report["status"] == "skip"                     # nothing declared
    assert report["summary"]["total"] == 0
    assert report["parts"] == {}

    assert calls.get("spec_declare", 0) == 0, calls
    assert calls.get("spec_eval", 0) == 0, calls
    assert calls.get("clearance", 0) == 0, calls
    # ``get_part`` is the chip path and must stay free as well.
    detail = service.get_part("bare", "plain")
    assert detail["specs"] is None
    assert calls.get("spec_eval", 0) == 0, calls
