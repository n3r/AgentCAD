"""The spec runner: tiers, caching, the report and requirement grouping.

Section 1 is pure — plain strings and dicts, no kernel, no git — because the
presence scan, the grouping and the id assignment are pure by contract. The
presence scan in particular must never execute a script: it is the mechanism
that makes a spec-less part cost *nothing* (AC9), and executing to find out
would defeat it.

Section 2 drives the real runner over a kernel-backed project. Its rules:

* **A rebuild evaluates the shape tier only.** The assembly and FEM tiers come
  back ``skip``/``deferred`` — named, never silently dropped.
* **A failing spec is data.** ``ok`` stays ``True``, the mesh lands, the report
  degrades rather than raising.
* **The sidecar is the cache.** ``SPECS`` lives in the script, so the existing
  content-hash cache key already covers it; a second evaluation at the same
  key is a disk read and issues zero kernel calls.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agentcad.core import locks
from agentcad.core.branches import pinned_tree_var
from agentcad.core.model import NotFoundError, ValidationError
from agentcad.core.service import AgentCADService, EventBus
from agentcad.core.specs import (
    SPEC_RESULT_VERSION,
    SpecRunner,
    assign_ids,
    declares_specs,
    group_requirements,
    report_status,
    summarize,
)
from agentcad.core.tools import build_registry
from agentcad.core.tools_stackup import compute_stackup
from agentcad.kernel.client import KernelError

from .conftest import BOX_SCRIPT, clone_test_service, make_test_service

_GIT = [
    pytest.mark.integration,
    pytest.mark.portability,
    pytest.mark.skipif(shutil.which("git") is None, reason="git not found on PATH"),
]


@pytest.fixture(autouse=True)
def _reset_context():
    """Identity and the branch pin are ContextVars: rebind them per test."""
    cid = locks.client_id_var.set("local")
    pin = pinned_tree_var.set(None)
    yield
    locks.client_id_var.reset(cid)
    pinned_tree_var.reset(pin)


# ----------------------------------------------------- 1. the presence scan


def test_declares_specs_sees_a_plain_top_level_assignment():
    assert declares_specs("SPECS = [1, 2]\n") is True


def test_declares_specs_sees_an_annotated_assignment():
    assert declares_specs("SPECS: list = []\n") is True


def test_declares_specs_sees_a_conditionally_built_list():
    script = (
        "import os\n"
        "if os.name == 'posix':\n"
        "    SPECS = [1]\n"
        "else:\n"
        "    SPECS = [2]\n"
    )
    assert declares_specs(script) is True


def test_declares_specs_sees_a_loop_built_list():
    script = "SPECS = []\nfor i in range(3):\n    SPECS.append(i)\n"
    assert declares_specs(script) is True


def test_declares_specs_sees_an_augmented_assignment():
    assert declares_specs("SPECS += [1]\n") is True


def test_declares_specs_ignores_a_comment_a_string_and_a_local():
    assert declares_specs("# SPECS = [1]\n") is False
    assert declares_specs('"""SPECS = [1]"""\n') is False
    assert declares_specs('x = "SPECS = [1]"\n') is False
    assert declares_specs("def f():\n    SPECS = [1]\n    return SPECS\n") is False
    assert declares_specs("class C:\n    SPECS = [1]\n") is False


def test_declares_specs_is_false_for_a_script_that_does_not_parse():
    """A script that will not parse fails its build with a line number anyway;
    the scan never raises and never executes."""
    assert declares_specs("def build(p:\n") is False


def test_declares_specs_never_executes_the_script(tmp_path):
    marker = tmp_path / "ran"
    script = f"open({str(marker)!r}, 'w').close()\nSPECS = [1]\n"
    assert declares_specs(script) is True
    assert not marker.exists()


def test_declares_specs_is_memoized_by_content_hash():
    script = "SPECS = [1]\n" + "# unique memo probe\n"
    from agentcad.core import specs as specs_module

    specs_module._DECLARES_MEMO.clear()
    assert declares_specs(script) is True
    assert len(specs_module._DECLARES_MEMO) == 1
    assert declares_specs(script) is True
    assert len(specs_module._DECLARES_MEMO) == 1


_NO_KERNEL_PROBE = """
import importlib
import sys


class _Blocked:
    \"\"\"Refuse OCP/build123d so an accidental kernel import is a hard error.\"\"\"

    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in ("OCP", "build123d"):
            raise ImportError("blocked kernel import: " + name)
        return None


sys.meta_path.insert(0, _Blocked())
mod = importlib.import_module("agentcad.core.specs")
assert mod.declares_specs("SPECS = []") is True
assert mod.SpecRunner is not None
assert "OCP" not in sys.modules and "build123d" not in sys.modules
print("ok")
"""


@pytest.mark.integration
@pytest.mark.portability
def test_the_runner_imports_with_no_geometry_kernel_available():
    """The runner is server-process code: it talks to the worker over
    ``kernel.request`` and must import where OCP cannot."""
    repo = Path(__file__).resolve().parents[1]
    proc = subprocess.run([sys.executable, "-c", _NO_KERNEL_PROBE],
                          cwd=repo, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr


# ------------------------------------------------ 1b. grouping, ids, status


def _check(name: str, status: str, requirement: str | None = None,
           part: str | None = "box") -> dict:
    return {"name": name, "status": status, "requirement": requirement,
            "part": part, "kind": "mass", "scope": "part"}


def test_requirement_status_fails_when_any_check_failed_or_errored():
    checks = [dict(_check("a", "pass", "ENG-1"), id="box:a"),
              dict(_check("b", "fail", "ENG-1"), id="box:b"),
              dict(_check("c", "pass", "ENG-2"), id="box:c"),
              dict(_check("d", "error", "ENG-2"), id="box:d")]
    grouped = group_requirements(checks)
    assert grouped["ENG-1"]["status"] == "fail"
    assert grouped["ENG-2"]["status"] == "fail"
    assert grouped["ENG-1"]["checks"] == ["box:a", "box:b"]


def test_requirement_status_passes_with_a_pass_and_no_failure():
    checks = [dict(_check("a", "pass", "ENG-1"), id="box:a"),
              dict(_check("b", "skip", "ENG-1"), id="box:b")]
    assert group_requirements(checks)["ENG-1"]["status"] == "pass"


def test_requirement_status_skips_when_every_check_skipped():
    checks = [dict(_check("a", "skip", "ENG-1"), id="box:a"),
              dict(_check("b", "skip", "ENG-1"), id="box:b")]
    assert group_requirements(checks)["ENG-1"]["status"] == "skip"


def test_a_requirement_with_no_checks_does_not_exist(  # FR12
):
    checks = [dict(_check("a", "pass", None), id="box:a"),
              dict(_check("b", "pass", ""), id="box:b")]
    assert group_requirements(checks) == {}


def test_ids_are_scope_prefixed_and_duplicates_are_suffixed():
    warnings: list[str] = []
    seen: set[str] = set()
    records = [_check("wall_min", "pass"), _check("wall_min", "fail"),
               _check("wall_min", "skip")]
    assign_ids(records, "box", seen, warnings)
    assert [r["id"] for r in records] == ["box:wall_min", "box:wall_min#2",
                                          "box:wall_min#3"]
    assert len(warnings) == 2 and "wall_min" in warnings[0]

    project_records = [_check("no_interference", "pass", part=None)]
    assign_ids(project_records, "project", seen, warnings)
    assert project_records[0]["id"] == "project:no_interference"


def test_summarize_counts_every_status_and_the_total():
    checks = [_check("a", "pass"), _check("b", "fail"), _check("c", "skip"),
              _check("d", "error"), _check("e", "pass")]
    assert summarize(checks) == {"passed": 2, "failed": 1, "skipped": 1,
                                 "errors": 1, "total": 5}


def test_report_status_is_red_for_a_failure_or_an_error():
    assert report_status(summarize([_check("a", "fail")])) == "red"
    assert report_status(summarize([_check("a", "error")])) == "red"


def test_report_status_is_green_when_only_skips_accompany_passes():
    assert report_status(summarize([_check("a", "pass"),
                                    _check("b", "skip")])) == "green"


def test_report_status_is_skip_when_nothing_was_declared_at_all():
    assert report_status(summarize([])) == "skip"


# ------------------------------------------------------------- 2. the runner

# A hollow box: the cavity is what gives ``check_wall`` something to measure,
# and ``wall`` is the parameter that drives the check red.
SPEC_BOX = '''\
from build123d import *
from agentcad.toolkit.specs import (check_clearance, check_mass, check_that,
                                    check_wall)

PARAMS = {"size": {"default": 20.0, "min": 10.0, "max": 60.0, "unit": "mm",
                   "description": "outer edge"},
          "wall": {"default": 2.5, "min": 0.5, "max": 5.0, "unit": "mm",
                   "description": "wall thickness"}}

SPECS = [
    check_wall(min_mm=2.0, grid=4, requirement="ENG-014"),
    check_mass(max_g=500.0, requirement="SYS-042"),
    check_that(lambda part, metrics: metrics["n_solids"] == 1, "one_solid",
               requirement="SYS-042"),
    # A project-scope check declared in a part script: reported, never dropped.
    check_clearance("box_1", "box_2", min_mm=1.0, name="misplaced"),
]

def build(p):
    inner = p.size - 2 * p.wall
    return Box(p.size, p.size, p.size) - Box(inner, inner, inner)

def connectors(p, part):
    return {"left": {"type": "rigid", "location": ((-p.size / 2, 0, 0), (0, 0, 0))},
            "right": {"type": "rigid", "location": ((p.size / 2, 0, 0), (0, 0, 0))}}
'''

# box_1 and box_2 are 10 mm apart; box_3 is mated flush against box_2.
PROJECT_SPECS = '''\
from agentcad.toolkit.specs import (check_clearance, check_interference_free,
                                    check_stackup)

SPECS = [
    check_interference_free(requirement="INT-003"),
    check_clearance("box_1", "box_2", min_mm=1.0, requirement="INT-003"),
    check_clearance("box_2", "box_3", min_mm=0.5, name="touching"),
    check_stackup("box_2", "box_3", "x", 0.5, requirement="TOL-001"),
]
'''

FEM_BOX = '''\
from build123d import *
from agentcad.toolkit.specs import check_fem_static

PARAMS = {"size": {"default": 20.0, "min": 10.0, "max": 40.0, "unit": "mm"}}

SPECS = [
    check_fem_static({"axis": "z", "side": "min"}, {"axis": "z", "side": "max"},
                     50.0, max_disp_mm=10.0, requirement="STR-001"),
]

def build(p):
    return Box(p.size, p.size, p.size)
'''

RAISING_PREDICATE = '''\
from build123d import *
from agentcad.toolkit.specs import check_that, check_valid

PARAMS = {}

SPECS = [
    check_valid(requirement="ENG-001"),
    check_that(lambda part, metrics: 1 / 0, "boom", requirement="ENG-001"),
]

def build(p):
    return Box(10, 10, 10)
'''

BROKEN_SPECS_PY = "from agentcad.toolkit.specs import check_clearance\n" \
                  "SPECS = [check_clearance('a', 'b', min_mm='wide')]\n"

STRUCTURAL_SPECS = '''\
from build123d import *
PARAMS = {}
SPECS = "hello"
def build(p):
    return Box(10, 10, 10)
'''

WIDTH_PMI = {"dims": [{"id": "w1", "kind": "linear", "target": "width",
                       "plus": 0.1, "minus": 0.1}]}


def _counting(service, monkeypatch) -> dict:
    """Count kernel methods, keeping the affinity of every spec call."""
    calls: dict = {"methods": {}, "affinity": [], "timeouts": []}
    original = service.kernel.request

    def counting(method, params, timeout_s=None, affinity=None):
        calls["methods"][method] = calls["methods"].get(method, 0) + 1
        if method.startswith("spec_"):
            calls["affinity"].append(affinity)
            calls["timeouts"].append(timeout_s)
        return original(method, params, timeout_s=timeout_s, affinity=affinity)

    monkeypatch.setattr(service.kernel, "request", counting)
    return calls


@pytest.fixture(scope="module")
def specs_projects(kernel, tmp_path_factory):
    """Two projects, built once: 'demo' declares specs everywhere, 'bare'
    declares none. Nothing is built here, so every clone starts with a cold
    .cache and the sidecar tests see a real miss."""
    projects = tmp_path_factory.mktemp("specs_projects")
    service = make_test_service(projects, kernel)
    registry = build_registry(service)

    service.create_project("demo")
    service.create_part("demo", "box", script=SPEC_BOX)
    service.create_part("demo", "plain", script=BOX_SCRIPT)
    service.set_assembly("demo", [
        {"id": "box_1", "part": "box", "position": [0.0, 0.0, 0.0]},
        {"id": "box_2", "part": "box", "position": [30.0, 0.0, 0.0]},
        {"id": "box_3", "part": "box"},
    ])
    assert "error" not in registry.call("set_mate", {
        "project": "demo", "instance": "box_3", "connector": "left",
        "to_instance": "box_2", "to_connector": "right"})
    assert "error" not in registry.call("set_part_pmi", {
        "project": "demo", "part_id": "box", "pmi": WIDTH_PMI})
    (service.store.path_of("demo") / "specs.py").write_text(
        PROJECT_SPECS, encoding="utf-8")

    service.create_project("bare")
    service.create_part("bare", "plain", script=BOX_SCRIPT)

    # Slice 4's get_part wrapper evaluates the shape tier as each part is
    # created, so a clone would start with a WARM .cache/<key>.specs.json.
    # These tests measure the cold path — drop the spec sidecars only (a cold
    # mesh would make every clone rebuild from scratch).
    for sidecar in projects.rglob("*.specs.json"):
        sidecar.unlink()
    return projects


@pytest.fixture
def demo(kernel, tmp_path, specs_projects):
    service = clone_test_service(specs_projects, tmp_path / "projects", kernel)
    return service, SpecRunner(service)


def _ids(report: dict) -> list[str]:
    return [check["id"] for check in report["checks"]]


def _by_id(report: dict, ident: str) -> dict:
    return next(check for check in report["checks"] if check["id"] == ident)


# ---- AC9: a part that declares nothing costs nothing


@pytest.mark.slow
def test_a_spec_less_project_issues_no_spec_kernel_calls(demo, monkeypatch):
    """AC9. The presence scan is an ast.parse, so a spec-less project reaches
    no kernel call at all — and tier1 says 'none declared', not 'unevaluated'."""
    service, runner = demo
    assert service.get_metrics("bare", "plain")["volume_mm3"] > 0
    calls = _counting(service, monkeypatch)
    service._status.clear()          # force the full cache-key path

    result = service.set_params("bare", "plain", {"size": 12.0})
    assert result["ok"] is True
    assert runner.tier1("bare", "plain", result) is None

    report = runner.run("bare")
    assert report["status"] == "skip"
    assert report["declared"] == 0
    assert report["checks"] == []
    assert calls["methods"].get("spec_eval", 0) == 0
    assert calls["methods"].get("spec_declare", 0) == 0


# ---- tier 1 on rebuild


@pytest.mark.slow
def test_tier1_evaluates_the_shape_tier_in_one_call_with_part_affinity(
        demo, monkeypatch):
    service, runner = demo
    built = service._rebuild("demo", "box")
    assert built["ok"] is True
    calls = _counting(service, monkeypatch)

    summary = runner.tier1("demo", "box", built)

    assert calls["methods"] == {"spec_eval": 1}
    assert calls["affinity"] == ["box"]
    assert calls["timeouts"] == [300.0]
    assert summary["status"] == "green"
    assert summary["cached"] is False
    assert summary["summary"] == {"passed": 3, "failed": 0, "skipped": 1,
                                  "errors": 0, "total": 4}
    assert [c["id"] for c in summary["checks"]] == [
        "box:wall_min", "box:mass_max", "box:one_solid", "box:misplaced"]
    wall = summary["checks"][0]
    assert wall["status"] == "pass"
    assert wall["measured"] == pytest.approx(2.5, abs=0.15)
    assert wall["limit"] == {"min_mm": 2.0}
    assert wall["unit"] == "mm"
    assert wall["requirement"] == "ENG-014"
    assert wall["part"] == "box"
    assert wall["location"] is not None
    assert summary["requirements"]["ENG-014"]["status"] == "pass"


@pytest.mark.slow
def test_tier1_defers_the_assembly_tier_by_name_and_never_drops_it(demo):
    """A project-scope check declared in a part script is reported, with a
    reason and a hint — the one thing a spec must never do is disappear."""
    _service, runner = demo
    summary = runner.tier1("demo", "box")
    misplaced = summary["checks"][3]
    assert misplaced["status"] == "skip"
    assert misplaced["reason"] == "unsupported_scope"
    assert misplaced["hint"]
    assert summary["status"] == "green"     # a skip is not a failure


# ---- FR10: the sidecar


@pytest.mark.slow
def test_tier1_writes_a_sidecar_and_the_second_call_reads_it(demo, monkeypatch):
    service, runner = demo
    built = service._rebuild("demo", "box")
    runner.tier1("demo", "box", built)
    sidecar = service.store.cache_dir("demo") / f"{built['cache_key']}.specs.json"
    assert sidecar.is_file()
    stored = json.loads(sidecar.read_text(encoding="utf-8"))
    assert stored["version"] == SPEC_RESULT_VERSION
    assert len(stored["checks"]) == 4
    assert (service.store.cache_dir("demo")
            / f"{built['cache_key']}.metrics.json").is_file()

    calls = _counting(service, monkeypatch)
    again = runner.tier1("demo", "box", built)
    assert calls["methods"] == {}
    assert again["cached"] is True
    assert again["summary"]["total"] == 4


@pytest.mark.slow
def test_a_param_change_mints_a_new_key_and_re_evaluates(demo, monkeypatch):
    service, runner = demo
    first = service._rebuild("demo", "box")
    runner.tier1("demo", "box", first)
    calls = _counting(service, monkeypatch)

    second = service.set_params("demo", "box", {"wall": 1.0})
    assert second["cache_key"] != first["cache_key"]
    summary = runner.tier1("demo", "box", second)

    assert calls["methods"]["spec_eval"] == 1
    assert summary["status"] == "red"
    assert summary["checks"][0]["status"] == "fail"
    assert summary["checks"][0]["measured"] == pytest.approx(1.0, abs=0.15)
    assert summary["requirements"]["ENG-014"]["status"] == "fail"


@pytest.mark.slow
def test_a_corrupt_sidecar_is_discarded_and_re_evaluated(demo, monkeypatch):
    service, runner = demo
    built = service._rebuild("demo", "box")
    runner.tier1("demo", "box", built)
    sidecar = service.store.cache_dir("demo") / f"{built['cache_key']}.specs.json"
    sidecar.write_text("{not json", encoding="utf-8")

    calls = _counting(service, monkeypatch)
    summary = runner.tier1("demo", "box", built)

    assert calls["methods"]["spec_eval"] == 1
    assert summary["summary"]["total"] == 4
    assert json.loads(sidecar.read_text(encoding="utf-8"))["version"] == \
        SPEC_RESULT_VERSION


# ---- AC2 / AC5: a spec never breaks a build


@pytest.mark.slow
def test_a_failing_spec_never_fails_the_rebuild(demo):
    """AC2: the geometry lands, ``ok`` stays True, the failure is signal."""
    service, runner = demo
    result = service.set_params("demo", "box", {"wall": 1.0})
    assert result["ok"] is True
    assert (service.store.cache_dir("demo")
            / f"{result['cache_key']}.acm").is_file()
    summary = runner.tier1("demo", "box", result)
    assert summary["status"] == "red"
    assert summary["summary"]["failed"] == 1


@pytest.mark.slow
def test_a_raising_predicate_is_an_error_and_siblings_still_report(demo):
    """AC5: 'the check broke' is not 'the check failed', and it is not a
    dead worker either."""
    service, runner = demo
    service.create_part("demo", "boom", script=RAISING_PREDICATE)
    result = service._rebuild("demo", "boom")
    assert result["ok"] is True

    summary = runner.tier1("demo", "boom", result)
    valid, predicate = summary["checks"]
    assert valid["status"] == "pass"
    assert predicate["status"] == "error"
    assert predicate["details"].get("traceback")
    assert "ZeroDivisionError" in predicate["message"]
    assert summary["status"] == "red"
    assert summary["requirements"]["ENG-001"]["status"] == "fail"
    assert service.kernel.request("ping", {})           # worker still alive


@pytest.mark.slow
def test_a_structurally_broken_specs_list_is_data_not_a_failed_rebuild(demo):
    service, runner = demo
    service.create_part("demo", "structural", script=STRUCTURAL_SPECS)
    result = service._rebuild("demo", "structural")
    assert result["ok"] is True

    summary = runner.tier1("demo", "structural", result)
    assert summary["status"] == "error"
    assert summary["error"]["type"] == "contract_error"
    assert "agentcad.toolkit.specs" in summary["error"]["message"]
    assert summary["checks"][0]["status"] == "error"


@pytest.mark.slow
def test_a_kernel_error_turns_the_parts_checks_into_error_records(
        demo, monkeypatch):
    service, runner = demo
    service._rebuild("demo", "box")
    original = service.kernel.request

    def failing(method, params, timeout_s=None, affinity=None):
        if method == "spec_eval":
            raise KernelError("kernel_error", "worker exploded", {"stage": "x"})
        return original(method, params, timeout_s=timeout_s, affinity=affinity)

    monkeypatch.setattr(service.kernel, "request", failing)
    summary = runner.tier1("demo", "box")

    assert summary["status"] == "error"
    assert summary["error"]["message"] == "worker exploded"
    # The declarations are still known, so every check is named as an error.
    assert [c["id"] for c in summary["checks"]] == [
        "box:wall_min", "box:mass_max", "box:one_solid", "box:misplaced"]
    assert {c["status"] for c in summary["checks"]} == {"error"}
    assert summary["summary"]["errors"] == 4


# ---- AC6: declarations without a build


@pytest.mark.slow
def test_declarations_read_every_scope_without_building(demo, monkeypatch):
    """AC6. list_specs must work on a project that has never been built."""
    service, runner = demo
    calls = _counting(service, monkeypatch)

    payload = runner.declarations("demo")

    assert calls["methods"].get("build", 0) == 0
    assert calls["methods"]["spec_declare"] == 2      # one part + specs.py
    assert payload["declared"] == 8
    assert [d["name"] for d in payload["parts"]["box"]["specs"]] == [
        "wall_min", "mass_max", "one_solid", "misplaced"]
    assert "plain" not in payload["parts"]
    assert payload["project_specs"]["path"] == "specs.py"
    assert [d["name"] for d in payload["project_specs"]["specs"]] == [
        "no_interference", "clearance_box_1_box_2", "touching",
        "stackup_box_2_box_3_x"]
    assert payload["requirements"]["ENG-014"] == ["box:wall_min"]
    assert payload["requirements"]["INT-003"] == [
        "project:no_interference", "project:clearance_box_1_box_2"]
    assert payload["errors"] == []
    # The predicate never crosses the boundary.
    one_solid = payload["parts"]["box"]["specs"][2]
    assert one_solid["predicate"] is True and "fn" not in one_solid


@pytest.mark.slow
def test_declarations_are_memoized_by_script_hash(demo, monkeypatch):
    service, runner = demo
    runner.declarations("demo")
    calls = _counting(service, monkeypatch)
    runner.declarations("demo")
    assert calls["methods"] == {}


@pytest.mark.slow
def test_a_broken_specs_py_is_an_errors_entry_and_part_specs_still_read(demo):
    service, runner = demo
    (service.store.path_of("demo") / "specs.py").write_text(
        BROKEN_SPECS_PY, encoding="utf-8")

    payload = runner.declarations("demo")

    assert payload["project_specs"]["specs"] == []
    assert len(payload["errors"]) == 1
    assert payload["errors"][0]["scope"] == "project"
    assert payload["errors"][0]["error"]["type"] == "script_error"
    assert payload["parts"]["box"]["specs"]        # still readable


# ---- AC4: the project tier


@pytest.mark.slow
def test_run_measures_clearance_and_names_interference(demo):
    """AC4. Both are absent from a rebuild and present in a run."""
    _service, runner = demo
    report = runner.run("demo")

    assert report["project"] == "demo"
    assert report["ref"] is None
    assert report["generated"].endswith("Z")
    clearance = _by_id(report, "project:clearance_box_1_box_2")
    assert clearance["status"] == "pass"
    assert clearance["measured"] == pytest.approx(10.0, abs=1e-6)
    assert clearance["unit"] == "mm"
    assert clearance["details"]["point_a"] and clearance["details"]["point_b"]

    touching = _by_id(report, "project:touching")
    assert touching["status"] == "fail"
    assert touching["measured"] == pytest.approx(0.0, abs=1e-6)

    interference = _by_id(report, "project:no_interference")
    assert interference["status"] == "pass"
    assert interference["details"]["pairs"] == []
    assert report["project_checks"]["status"] == "red"     # the touching pair
    assert report["status"] == "red"


@pytest.mark.slow
def test_run_names_the_offending_pair_when_instances_overlap(demo):
    service, runner = demo
    service.set_assembly("demo", [
        {"id": "box_1", "part": "box", "position": [0.0, 0.0, 0.0]},
        {"id": "box_2", "part": "box", "position": [5.0, 0.0, 0.0]},
        {"id": "box_3", "part": "box", "position": [100.0, 0.0, 0.0]},
    ])
    report = runner.run("demo")

    interference = _by_id(report, "project:no_interference")
    assert interference["status"] == "fail"
    pairs = interference["details"]["pairs"]
    assert {pairs[0]["a"], pairs[0]["b"]} == {"box_1", "box_2"}
    assert interference["measured"] > 0.0


@pytest.mark.slow
def test_run_reports_the_stackup_from_the_mate_chain(demo):
    service, runner = demo
    report = runner.run("demo")

    stackup = _by_id(report, "project:stackup_box_2_box_3_x")
    assert stackup["status"] == "pass"
    assert stackup["measured"] == pytest.approx(0.2)
    assert stackup["limit"] == {"within_mm": 0.5}
    assert stackup["details"]["path"] == ["box_2", "box_3"]

    # The extracted entry point is what the check calls, directly.
    direct = compute_stackup(service, "demo", "x", "box_2", "box_3")
    assert direct["worst_case"] == {"plus": 0.2, "minus": 0.2}
    assert direct["nominal_mm"] == pytest.approx(20.0)


@pytest.mark.slow
def test_an_unknown_instance_id_is_an_error_naming_it(demo):
    service, runner = demo
    (service.store.path_of("demo") / "specs.py").write_text(
        "from agentcad.toolkit.specs import check_clearance, check_stackup\n"
        "SPECS = [check_clearance('box_1', 'ghost', min_mm=1.0),\n"
        "         check_stackup('box_1', 'ghost', 'x', 0.5)]\n",
        encoding="utf-8")

    report = runner.run("demo")

    clearance = _by_id(report, "project:clearance_box_1_ghost")
    assert clearance["status"] == "error"
    assert "ghost" in clearance["message"]
    stackup = _by_id(report, "project:stackup_box_1_ghost_x")
    assert stackup["status"] == "error"
    assert "ghost" in stackup["message"]
    assert report["status"] == "red"


@pytest.mark.slow
def test_run_groups_requirements_over_both_scopes(demo):
    _service, runner = demo
    report = runner.run("demo")

    assert report["declared"] == 8
    assert report["summary"]["total"] == 8
    assert set(report["requirements"]) == {"ENG-014", "SYS-042", "INT-003",
                                           "TOL-001"}
    assert report["requirements"]["ENG-014"] == {"status": "pass",
                                                 "checks": ["box:wall_min"]}
    assert report["requirements"]["SYS-042"]["checks"] == [
        "box:mass_max", "box:one_solid"]
    assert report["parts"]["box"]["status"] == "green"
    assert report["parts"]["box"]["checks"] == _ids(report)[:4]
    assert "plain" not in report["parts"]
    assert report["project_checks"]["checks"] == _ids(report)[4:]


@pytest.mark.slow
def test_run_for_one_part_skips_the_project_tier(demo, monkeypatch):
    service, runner = demo
    calls = _counting(service, monkeypatch)
    report = runner.run("demo", part_id="box")

    assert calls["methods"].get("clearance", 0) == 0
    assert calls["methods"].get("interference", 0) == 0
    assert report["project_checks"]["checks"] == []
    assert list(report["parts"]) == ["box"]


def test_run_rejects_an_unknown_project_and_part(demo):
    _service, runner = demo
    with pytest.raises(NotFoundError):
        runner.run("nope")
    with pytest.raises(NotFoundError):
        runner.run("demo", part_id="ghost")


# ---- AC3: FEM


@pytest.mark.slow
def test_fem_static_skips_without_the_extra(demo, monkeypatch):
    from agentcad.core import specs as specs_module

    service, runner = demo
    service.create_part("demo", "fem_box", script=FEM_BOX)
    monkeypatch.setattr(specs_module, "_fem_available", lambda: False)

    deferred = runner.tier1("demo", "fem_box")
    assert deferred["checks"][0]["status"] == "skip"
    assert deferred["checks"][0]["reason"] == "deferred"

    report = runner.run("demo", part_id="fem_box")
    check = report["checks"][0]
    assert check["status"] == "skip"
    assert check["reason"] == "fem_extra_missing"
    assert "fem" in check["hint"]
    assert report["status"] == "green"       # a skip is not a failure


@pytest.mark.slow
def test_fem_static_evaluates_with_the_extra(demo):
    pytest.importorskip("skfem")
    pytest.importorskip("gmsh")
    pytest.importorskip("meshio")

    service, runner = demo
    service.create_part("demo", "fem_box", script=FEM_BOX)
    report = runner.run("demo", part_id="fem_box")

    check = report["checks"][0]
    assert check["status"] in ("pass", "fail")
    assert check["measured"] is not None
    assert check["limit"] == {"max_disp_mm": 10.0}
    assert check["requirement"] == "STR-001"
    assert report["requirements"]["STR-001"]["checks"] == ["fem_box:fem_static"]


# ---- refs (PRD-001)


@pytest.fixture
def git_demo(kernel, tmp_path):
    """The real service (not make_test_service): the versioning pack installs
    service.branches and history snapshots must actually happen."""
    service = AgentCADService(tmp_path / "projects", kernel, EventBus())
    registry = build_registry(service)
    assert getattr(service, "branches", None) is not None
    assert "error" not in registry.call("create_project", {"name": "demo"})
    assert "error" not in registry.call(
        "create_part", {"project": "demo", "part_id": "box",
                        "script": SPEC_BOX})
    return service, registry, SpecRunner(service)


@pytest.mark.slow
def test_a_ref_without_git_is_a_validation_error_naming_git(demo, monkeypatch):
    """The pack deliberately does NOT self-disable without git — only ``ref``
    needs branches, and it says so."""
    service, runner = demo
    monkeypatch.setattr(service.history, "available", lambda: False)
    with pytest.raises(ValidationError) as excinfo:
        runner.run("demo", ref="feat")
    assert "git" in str(excinfo.value)


@pytest.mark.slow
def test_a_ref_on_a_project_with_no_branches_seam_is_a_validation_error(
        demo, monkeypatch):
    service, runner = demo
    monkeypatch.delattr(service, "branches", raising=False)
    with pytest.raises(ValidationError):
        runner.run("demo", ref="feat")


class TestRefs:
    """``ref`` resolves through PRD-001: a branch, never a tag (X1)."""

    pytestmark = _GIT + [pytest.mark.slow]

    def test_an_unknown_ref_is_a_notfound_error(self, git_demo):
        _service, _registry, runner = git_demo
        with pytest.raises(NotFoundError):
            runner.run("demo", ref="nope")

    def test_a_tag_named_like_a_branch_is_a_validation_error(self, git_demo):
        service, _registry, runner = git_demo
        service.branches.tag("demo", "shop-rev-a")
        with pytest.raises(ValidationError) as excinfo:
            runner.run("demo", ref="shop-rev-a")
        assert "tag" in str(excinfo.value)

    def test_run_evaluates_a_branch_under_its_pinned_tree(self, git_demo):
        service, registry, runner = git_demo
        service.branches.create("demo", "feat")
        locks.set_client_id("agent_a")
        service.branches.switch("demo", "feat")
        assert "error" not in registry.call(
            "set_params", {"project": "demo", "part_id": "box",
                           "values": {"wall": 1.0}})
        service.branches.switch("demo", "master")

        assert runner.run("demo")["status"] == "green"
        on_branch = runner.run("demo", ref="feat")
        assert on_branch["ref"] == "feat"
        assert on_branch["status"] == "red"
        assert _by_id(on_branch, "box:wall_min")["status"] == "fail"
        assert pinned_tree_var.get() is None          # the pin is released


# ---- the specs.py writer (slice 4)


class TestProjectSpecsWriter:
    """``specs.py`` has no other writer: ``update_part_script`` covers part
    scope and there is no generic file-write tool, so FR2 is unreachable for an
    agent without these two. The rules are ``update_part_script``'s verbatim —
    write unconditionally, report afterwards, return post-state — because you
    must be able to save a broken file in order to fix it."""

    pytestmark = _GIT + [pytest.mark.slow]

    def test_it_writes_the_file_and_returns_the_declarations(self, git_demo):
        service, registry, _runner = git_demo
        result = registry.call("set_project_specs",
                               {"project": "demo", "script": PROJECT_SPECS})

        assert "error" not in result, result
        assert result["path"] == "specs.py"
        assert result["exists"] is True
        assert result["declaration_error"] is None
        assert result["declared"] == 4
        assert [d["name"] for d in result["specs"]] == [
            "no_interference", "clearance_box_1_box_2", "touching",
            "stackup_box_2_box_3_x"]
        path = service.store.path_of("demo") / "specs.py"
        assert path.read_text(encoding="utf-8") == PROJECT_SPECS
        assert not list(path.parent.glob("*.tmp"))     # atomic, nothing left

        read = registry.call("get_project_specs", {"project": "demo"})
        assert read["script"] == PROJECT_SPECS
        assert [d["name"] for d in read["specs"]] == [
            d["name"] for d in result["specs"]]

    def test_it_is_refused_under_another_clients_turn(self, git_demo):
        service, registry, _runner = git_demo
        service.turnlock.acquire(service.store.lock_key("demo"), "agent_b")

        refused = registry.call("set_project_specs",
                                {"project": "demo", "script": PROJECT_SPECS})

        assert refused["error"]["type"] == "conflict_error"
        assert refused["error"]["details"]["holder"] == "agent_b"
        assert not (service.store.path_of("demo") / "specs.py").exists()

    def test_it_snapshots_the_project_so_the_file_rides_git(self, git_demo):
        service, registry, _runner = git_demo
        canonical = service.store.canonical_path_of("demo")
        before = len(service.history.log(canonical, limit=100))

        assert "error" not in registry.call(
            "set_project_specs", {"project": "demo", "script": PROJECT_SPECS})

        after = service.history.log(canonical, limit=100)
        assert len(after) == before + 1
        assert "specs" in after[0]["message"]
        committed = service.history._run(canonical, "show", "HEAD:specs.py")
        assert committed.stdout == PROJECT_SPECS
        assert registry.call("project_history", {"project": "demo"})[
            "history"][0]["id"] == after[0]["id"]

    def test_a_broken_script_is_written_and_reported_not_refused(self,
                                                                git_demo):
        service, registry, _runner = git_demo
        broken = registry.call("set_project_specs",
                               {"project": "demo", "script": BROKEN_SPECS_PY})

        assert "error" not in broken            # the TOOL did not fail
        assert broken["specs"] == []
        assert broken["declared"] == 0
        assert broken["declaration_error"]["type"] == "script_error"
        assert broken["declaration_error"]["details"].get("line")
        assert (service.store.path_of("demo") / "specs.py").read_text(
            encoding="utf-8") == BROKEN_SPECS_PY

        # ...and the same error is what a read reports, so it is fixable.
        assert registry.call("get_project_specs", {"project": "demo"})[
            "declaration_error"]["type"] == "script_error"

    def test_an_empty_script_deletes_the_file(self, git_demo):
        service, registry, _runner = git_demo
        registry.call("set_project_specs",
                      {"project": "demo", "script": PROJECT_SPECS})

        emptied = registry.call("set_project_specs",
                                {"project": "demo", "script": ""})

        assert emptied == {"path": "specs.py", "exists": False, "script": None,
                           "declared": 0, "specs": [],
                           "declaration_error": None, "warnings": []}
        assert not (service.store.path_of("demo") / "specs.py").exists()
        assert registry.call("get_project_specs", {"project": "demo"}) == {
            "path": "specs.py", "exists": False, "script": None,
            "declared": 0, "specs": [], "declaration_error": None,
            "warnings": []}

    def test_an_unknown_project_is_a_notfound_error(self, git_demo):
        _service, registry, _runner = git_demo
        assert registry.call("set_project_specs",
                             {"project": "ghost", "script": ""})[
            "error"]["type"] == "notfound_error"
        assert registry.call("get_project_specs", {"project": "ghost"})[
            "error"]["type"] == "notfound_error"

    def test_the_file_rides_branches(self, git_demo):
        """FR2 structurally: ``git add -A`` tracks it, so branching, restore
        and merge are free — nothing here had to be built for it."""
        service, registry, _runner = git_demo
        service.branches.create("demo", "feat")
        locks.set_client_id("agent_a")
        service.branches.switch("demo", "feat")
        assert "error" not in registry.call(
            "set_project_specs", {"project": "demo", "script": PROJECT_SPECS})
        assert registry.call("get_project_specs",
                             {"project": "demo"})["exists"] is True

        service.branches.switch("demo", "master")
        assert registry.call("get_project_specs", {"project": "demo"}) == {
            "path": "specs.py", "exists": False, "script": None,
            "declared": 0, "specs": [], "declaration_error": None,
            "warnings": []}

        service.branches.switch("demo", "feat")
        assert registry.call("get_project_specs",
                             {"project": "demo"})["script"] == PROJECT_SPECS
