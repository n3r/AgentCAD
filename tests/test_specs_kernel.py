"""Design-spec kernel pack: ``spec_declare``, ``spec_eval`` and ``clearance``.

Three contracts these tests pin, because each one is a trap the feature rests
on:

* **Predicates never leave the worker.** ``check_that``'s callable is read out
  of the script's own namespace and executed there; what crosses JSON-RPC is
  ``"predicate": true``. A predicate that raises is an ``error`` *record*, not
  a dead worker — every test that provokes one ends with a ``ping``.
* **The split between a structural and a script error is FR1's mechanism.**
  ``SPECS = "hello"`` is a ``contract_error`` naming ``agentcad.toolkit.specs``;
  ``check_wall(min_mm="x")`` raises while the module executes and is therefore
  a ``script_error`` carrying ``details.line``, byte-identically to a malformed
  ``PARAMS``.
* **``clearance`` measures the ``analysis(p)`` envelope**, so a reported gap is
  an under-estimate of the real one — conservative in the safe direction, and
  consistent with the interference check the same assembly is judged by.
"""

from __future__ import annotations

import json

import pytest

from agentcad.kernel.client import KernelError
from agentcad.toolkit.specs import PART_KINDS

from .conftest import BOX_SCRIPT

pytestmark = pytest.mark.portability


# One declaration of every part-scope constructor, in PART_KINDS order.
ALL_SPECS = '''\
from build123d import *
from agentcad.toolkit.specs import (check_bbox, check_fem_static, check_mass,
                                    check_that, check_valid, check_volume,
                                    check_wall)

PARAMS = {"s": {"default": 20.0, "min": 5.0, "max": 50.0, "unit": "mm",
                "description": "cube edge"}}

SPECS = [
    check_valid(requirement="ENG-001"),
    check_mass(max_g=100.0, requirement="https://tracker/SYS-042"),
    check_volume(min_mm3=1000.0),
    check_bbox(25.0),
    check_wall(min_mm=2.5, grid=4),
    check_that(lambda part, metrics: metrics["n_solids"] == 1, "one_solid"),
    check_fem_static({"axis": "z", "side": "min"}, {"axis": "z", "side": "max"},
                     100.0, max_disp_mm=1.0),
]

def build(p):
    return Box(p.s, p.s, p.s)
'''

# A project module: no PARAMS, no build(p) — spec_declare must not need them.
PROJECT_SPECS = '''\
from agentcad.toolkit.specs import (check_clearance, check_interference_free,
                                    check_stackup, check_wall)

SPECS = [
    check_interference_free(),
    check_clearance("flange_1", "plate_1", min_mm=0.5, requirement="INT-003"),
    check_stackup("flange_1", "plate_1", "z", 0.2),
    check_wall(min_mm=1.0),          # part scope in a project module
]
'''

NO_SPECS = '''\
from build123d import *
PARAMS = {}
def build(p):
    return Box(10, 10, 10)
'''

# build(p) explodes: spec_declare must still declare, because it never builds.
UNBUILDABLE = '''\
from agentcad.toolkit.specs import check_valid
PARAMS = {}
SPECS = [check_valid()]
def build(p):
    raise RuntimeError("build must not run for a declaration")
'''

BAD_SPECS_TYPE = '''\
PARAMS = {}
SPECS = "hello"
def build(p):
    pass
'''

BAD_SPECS_ENTRY = '''\
PARAMS = {}
SPECS = [{"not": "a spec"}]
def build(p):
    pass
'''

# The eager-validation path: line 3 raises while the module executes.
BAD_CONSTRUCTOR_ARG = '''\
from agentcad.toolkit.specs import check_wall
PARAMS = {}
SPECS = [check_wall(min_mm="thick")]
def build(p):
    pass
'''

# 2.5 mm shelled box — the known-thickness reference tests/test_analysis.py uses.
SHELLED = '''\
from build123d import *
from agentcad.toolkit.specs import check_wall

PARAMS = {"wall": {"default": 2.5, "min": 1.0, "max": 5.0, "unit": "mm",
                   "description": "wall"}}
SPECS = [check_wall(min_mm=2.0, requirement="ENG-014")]

def build(p):
    box = Box(50, 40, 30)
    shelled = offset(box, amount=-p.wall, openings=box.faces().sort_by(Axis.Z)[-1])
    return shelled.solids()[0]
'''

PREDICATES = '''\
from build123d import *
from agentcad.toolkit.specs import check_that

PARAMS = {}
SPECS = [
    check_that(lambda part, metrics: metrics["n_solids"] == 1, "true_bool"),
    check_that(lambda part, metrics: metrics["n_solids"] == 7, "false_bool"),
    check_that(lambda part, metrics: metrics["n_solids"], "not_a_bool"),
    check_that(lambda part, metrics: 1 / 0, "raises"),
    check_that(lambda part, metrics: float(part.volume) > 0.0, "sees_the_part"),
]

def build(p):
    return Box(10, 10, 10)
'''

# build(p) appends a line to the file named by the ``log`` param, so a second
# spec_eval that reuses the worker's shape LRU leaves the file at one line.
LOGGING_BUILD = '''\
from build123d import *
from agentcad.toolkit.specs import check_valid

PARAMS = {"log": {"default": "", "type": "string", "max_len": 200,
                  "description": "build log path"}}
SPECS = [check_valid()]

def build(p):
    with open(p.log, "a") as fh:
        fh.write("build\\n")
    return Box(10, 10, 10)
'''

CUBE10 = '''\
from build123d import *
PARAMS = {"s": {"default": 10.0, "min": 1.0, "max": 50.0, "unit": "mm",
                "description": "edge"}}
def build(p):
    return Box(p.s, p.s, p.s)
'''

# analysis(p) is deliberately FATTER than build(p): the measured clearance must
# come from the envelope (6 mm), not from the real geometry (10 mm).
FAT_ENVELOPE = '''\
from build123d import *
PARAMS = {}
def build(p):
    return Box(10, 10, 10)
def analysis(p):
    return Box(14, 10, 10)
'''

# A legal build(p) return with no solid in it: check_valid must say so, and the
# volume budget must fail rather than quietly measure 0 as "within".
SOLIDLESS = '''\
from build123d import *
from agentcad.toolkit.specs import check_valid, check_volume

PARAMS = {}
SPECS = [check_valid(), check_volume(min_mm3=1000.0)]

def build(p):
    return Compound(children=[Box(10, 10, 10).faces()[0]])
'''

# A project-scope check declared inside a part script: evaluable by nobody at
# part scope, so it is a named skip rather than a silent drop.
MIXED_SCOPE = '''\
from build123d import *
from agentcad.toolkit.specs import check_interference_free, check_valid

PARAMS = {}
SPECS = [check_valid(), check_interference_free()]

def build(p):
    return Box(10, 10, 10)
'''

# Builds (an empty Compound is a legal return) but has nothing to measure from.
EMPTY = '''\
from build123d import *
PARAMS = {}
def build(p):
    return Compound(children=[])
'''

# A predicate that writes to the metrics dict it is handed. Declared FIRST, so
# only a per-check copy (and evaluating ``that`` last) can keep mass_max honest.
POISON_PREDICATE = '''\
from build123d import *
from agentcad.toolkit.specs import check_mass, check_that

PARAMS = {}


def poison(part, metrics):
    metrics["mass_g"] = 0.0
    metrics["n_solids"] = 0
    return True


SPECS = [
    check_that(poison, "poison"),
    check_mass(max_g=1.0, name="mass_max"),
]

def build(p):
    return Box(50, 50, 50)
'''


def declare(kernel, script, scope="part"):
    return kernel.request("spec_declare", {"script": script, "scope": scope})


def evaluate(kernel, script, **params):
    return kernel.request("spec_eval", {"script": script, **params})


def clearance(kernel, a, b, **params):
    return kernel.request("clearance", {"a": a, "b": b, **params})


def item(script, position=(0, 0, 0), rotation_deg=(0, 0, 0), **extra):
    return {"script": script, "params": {}, "position": list(position),
            "rotation_deg": list(rotation_deg), **extra}


def by_name(result):
    return {check["name"]: check for check in result["checks"]}


# ------------------------------------------------------------- spec_declare


def test_declare_returns_every_part_constructor_json_safe(kernel):
    result = declare(kernel, ALL_SPECS)
    declared = result["declared"]
    assert [d["kind"] for d in declared] == list(PART_KINDS)
    assert all(d["spec"] == 1 and d["scope"] == "part" for d in declared)
    assert [d["name"] for d in declared] == [
        "valid", "mass_max", "volume_min", "bbox_within", "wall_min",
        "one_solid", "fem_static"]
    assert declared[0]["requirement"] == "ENG-001"
    assert declared[1]["requirement"] == "https://tracker/SYS-042"
    predicate = declared[5]
    assert predicate["predicate"] is True and "fn" not in predicate
    # the whole payload crosses JSON-RPC, so it already round-tripped; assert
    # it explicitly so a future non-JSON field fails here, not in the client
    assert json.loads(json.dumps(result)) == result
    assert result["warnings"] == []


def test_declare_never_builds(kernel):
    """A part whose build(p) raises still declares — which is the whole point
    of FR7/AC6, and is only true because spec_declare issues no build. The
    contrast is spec_eval on the same script, which must fail."""
    seen = []
    original = kernel.request

    def recording(method, params, timeout_s=None, affinity=None):
        seen.append(method)
        return original(method, params, timeout_s=timeout_s, affinity=affinity)

    result = recording("spec_declare", {"script": UNBUILDABLE, "scope": "part"})
    assert [d["kind"] for d in result["declared"]] == ["valid"]
    assert seen == ["spec_declare"]

    with pytest.raises(KernelError) as excinfo:
        evaluate(kernel, UNBUILDABLE, params={}, density_g_cm3=1.0)
    assert excinfo.value.type == "script_error"


def test_declare_project_scope_warns_on_a_part_scope_check(kernel):
    result = declare(kernel, PROJECT_SPECS, scope="project")
    kinds = [d["kind"] for d in result["declared"]]
    assert kinds == ["interference_free", "clearance", "stackup", "wall"]
    assert result["declared"][1]["requirement"] == "INT-003"
    # nothing is dropped silently: the mismatch is a warning naming the check
    assert len(result["warnings"]) == 1
    warning = result["warnings"][0]
    assert "wall_min" in warning and "part" in warning and "project" in warning


def test_declare_part_scope_warns_on_a_project_scope_check(kernel):
    result = declare(kernel, PROJECT_SPECS, scope="part")
    assert len(result["warnings"]) == 3
    assert all("project" in w for w in result["warnings"])


def test_script_without_specs_declares_nothing(kernel):
    assert declare(kernel, NO_SPECS) == {"declared": [], "warnings": []}


@pytest.mark.parametrize("script", [BAD_SPECS_TYPE, BAD_SPECS_ENTRY])
def test_structurally_bad_specs_is_a_contract_error(kernel, script):
    with pytest.raises(KernelError) as excinfo:
        declare(kernel, script)
    assert excinfo.value.type == "contract_error"
    assert "agentcad.toolkit.specs" in excinfo.value.message
    assert "SPECS" in excinfo.value.message


def test_bad_constructor_argument_is_a_script_error_with_a_line(kernel):
    with pytest.raises(KernelError) as excinfo:
        declare(kernel, BAD_CONSTRUCTOR_ARG)
    assert excinfo.value.type == "script_error"
    assert excinfo.value.details["line"] == 3
    assert "min_mm" in excinfo.value.message


# ---------------------------------------------------------------- spec_eval


def test_eval_metric_checks_pass(kernel):
    result = evaluate(kernel, ALL_SPECS, params={}, density_g_cm3=1.0)
    checks = by_name(result)
    assert checks["valid"]["status"] == "pass"
    assert checks["mass_max"]["status"] == "pass"
    assert checks["mass_max"]["measured"] == pytest.approx(8.0, rel=1e-6)
    assert checks["mass_max"]["limit"] == {"max_g": 100.0}
    assert checks["mass_max"]["unit"] == "g"
    assert checks["mass_max"]["requirement"] == "https://tracker/SYS-042"
    assert checks["volume_min"]["measured"] == pytest.approx(8000.0, rel=1e-6)
    assert checks["volume_min"]["unit"] == "mm3"
    assert checks["bbox_within"]["measured"] == pytest.approx([20.0, 20.0, 20.0])
    assert checks["bbox_within"]["status"] == "pass"
    assert all(check["message"] for check in result["checks"])
    assert [d["kind"] for d in result["declared"]] == list(PART_KINDS)


def test_eval_metric_checks_fail_with_measured_and_limit(kernel):
    result = evaluate(kernel, ALL_SPECS, params={"s": 50.0},
                      density_g_cm3=1.0)
    checks = by_name(result)
    assert checks["valid"]["status"] == "pass"
    assert checks["mass_max"]["status"] == "fail"
    assert checks["mass_max"]["measured"] == pytest.approx(125.0, rel=1e-6)
    assert "125" in checks["mass_max"]["message"]
    assert checks["bbox_within"]["status"] == "fail"
    assert checks["bbox_within"]["limit"] == {"within_mm": [25.0, 25.0, 25.0]}
    assert checks["volume_min"]["status"] == "pass"


def test_eval_valid_and_volume_fail_on_a_solidless_shape(kernel):
    result = evaluate(kernel, SOLIDLESS, params={}, density_g_cm3=1.0)
    checks = by_name(result)
    assert checks["valid"]["status"] == "fail"
    assert checks["valid"]["measured"] is False
    assert checks["valid"]["details"]["n_solids"] == 0
    assert "n_solids=0" in checks["valid"]["message"]
    assert checks["volume_min"]["status"] == "fail"
    assert checks["volume_min"]["measured"] == 0.0
    assert "below" in checks["volume_min"]["message"]


def test_eval_wall_reports_measured_and_location(kernel):
    result = evaluate(kernel, SHELLED, params={}, density_g_cm3=1.0)
    wall = by_name(result)["wall_min"]
    assert wall["status"] == "pass"
    assert wall["measured"] == pytest.approx(2.5, abs=0.15)
    assert wall["unit"] == "mm"
    assert wall["requirement"] == "ENG-014"
    assert wall["location"] is not None and len(wall["location"]) == 3

    thin = evaluate(kernel, SHELLED, params={"wall": 1.5}, density_g_cm3=1.0)
    wall = by_name(thin)["wall_min"]
    assert wall["status"] == "fail"
    assert wall["measured"] == pytest.approx(1.5, abs=0.15)
    assert wall["location"] is not None


def test_eval_predicates_run_in_the_worker(kernel):
    result = evaluate(kernel, PREDICATES, params={}, density_g_cm3=1.0)
    checks = by_name(result)
    assert checks["true_bool"]["status"] == "pass"
    assert checks["false_bool"]["status"] == "fail"
    assert checks["sees_the_part"]["status"] == "pass"

    not_bool = checks["not_a_bool"]
    assert not_bool["status"] == "error"
    assert "int" in not_bool["message"]

    raises = checks["raises"]
    assert raises["status"] == "error"
    assert "ZeroDivisionError" in raises["message"]
    assert "ZeroDivisionError" in raises["details"]["traceback"]
    assert raises["details"]["line"] == 9   # the lambda's own line

    # a predicate raising must not take the worker with it (AC5)
    assert kernel.request("ping", {})["ok"] is True


def test_a_mutating_predicate_cannot_change_another_checks_verdict(kernel):
    """Predicates are untrusted script code and ``metrics`` is shared state.

    Each check gets its own copy and every ``that`` check runs LAST, so a
    predicate that zeroes ``mass_g`` — accidentally or otherwise — cannot turn
    a failing built-in check green. Record ORDER is still the declared one."""
    result = evaluate(kernel, POISON_PREDICATE, params={}, density_g_cm3=1.0)

    assert [c["name"] for c in result["checks"]] == ["poison", "mass_max"]
    assert by_name(result)["poison"]["status"] == "pass"
    mass = by_name(result)["mass_max"]
    assert mass["status"] == "fail"
    assert mass["measured"] == pytest.approx(125.0, rel=1e-6)   # 50³ mm³ @ 1


def test_eval_indices_select_a_subset_in_order(kernel):
    result = evaluate(kernel, ALL_SPECS, params={}, density_g_cm3=1.0,
                      indices=[3, 1])
    assert [c["name"] for c in result["checks"]] == ["bbox_within", "mass_max"]
    assert [c["index"] for c in result["checks"]] == [3, 1]
    assert len(result["declared"]) == len(PART_KINDS)


def test_eval_defers_fem_static(kernel):
    result = evaluate(kernel, ALL_SPECS, params={}, density_g_cm3=1.0)
    fem = by_name(result)["fem_static"]
    assert fem["status"] == "skip"
    assert fem["reason"] == "deferred"
    assert fem["hint"]


def test_eval_skips_a_project_scope_declaration(kernel):
    result = evaluate(kernel, MIXED_SCOPE, params={}, density_g_cm3=1.0)
    assert by_name(result)["valid"]["status"] == "pass"
    check = by_name(result)["no_interference"]
    assert check["status"] == "skip"
    assert check["reason"] == "unsupported_scope"
    assert check["hint"]


def test_eval_reuses_the_worker_shape_cache(kernel, tmp_path):
    log = tmp_path / "builds.log"
    params = {"log": str(log)}
    first = evaluate(kernel, LOGGING_BUILD, params=params, density_g_cm3=1.0)
    second = evaluate(kernel, LOGGING_BUILD, params=params, density_g_cm3=1.0)
    assert first["checks"][0]["status"] == "pass"
    assert second["checks"][0]["status"] == "pass"
    # the shape LRU is keyed on (script, resolved values): one build, two evals
    assert log.read_text().count("build") == 1


def test_eval_reports_a_density_dependent_mass(kernel):
    result = evaluate(kernel, ALL_SPECS, params={}, density_g_cm3=8.19)
    assert by_name(result)["mass_max"]["measured"] == pytest.approx(65.52,
                                                                   rel=1e-6)
    assert by_name(result)["mass_max"]["status"] == "pass"


# ---------------------------------------------------------------- clearance


def test_clearance_measures_a_known_gap(kernel):
    result = clearance(kernel, item(CUBE10), item(CUBE10, position=(13, 0, 0)))
    assert result["distance_mm"] == pytest.approx(3.0, abs=1e-6)
    assert result["point_a"][0] == pytest.approx(5.0, abs=1e-6)
    assert result["point_b"][0] == pytest.approx(8.0, abs=1e-6)
    assert "ok" not in result
    assert "skipped_mesh" not in result


def test_clearance_honours_rotation(kernel):
    # a 10 mm cube spun 45 deg about Z reaches 5*sqrt(2) along X, so the gap
    # from a box whose near face sits at x = 8 shrinks by 5*(sqrt(2) - 1)
    straight = clearance(kernel, item(CUBE10),
                         item(CUBE10, position=(13, 0, 0)))
    rotated = clearance(kernel, item(CUBE10, rotation_deg=(0, 0, 45)),
                        item(CUBE10, position=(13, 0, 0)))
    assert straight["distance_mm"] == pytest.approx(3.0, abs=1e-6)
    assert rotated["distance_mm"] == pytest.approx(3.0 - 5 * (2 ** 0.5 - 1),
                                                  abs=1e-6)


def test_clearance_of_overlapping_solids_is_zero(kernel):
    result = clearance(kernel, item(CUBE10), item(CUBE10, position=(4, 0, 0)))
    assert result["distance_mm"] == 0.0
    touching = clearance(kernel, item(CUBE10), item(CUBE10, position=(10, 0, 0)))
    assert touching["distance_mm"] == pytest.approx(0.0, abs=1e-9)


def test_clearance_reports_ok_only_when_a_minimum_is_given(kernel):
    passing = clearance(kernel, item(CUBE10), item(CUBE10, position=(13, 0, 0)),
                        min_mm=1.0)
    assert passing["ok"] is True
    failing = clearance(kernel, item(CUBE10), item(CUBE10, position=(13, 0, 0)),
                        min_mm=5.0)
    assert failing["ok"] is False
    assert failing["distance_mm"] == pytest.approx(3.0, abs=1e-6)


def test_clearance_uses_the_analysis_envelope(kernel):
    # real geometry would leave 10 mm; the fatter envelope reports 6 mm, which
    # is the conservative direction
    result = clearance(kernel, item(FAT_ENVELOPE),
                       item(FAT_ENVELOPE, position=(20, 0, 0)))
    assert result["distance_mm"] == pytest.approx(6.0, abs=1e-6)


def test_clearance_skips_a_mesh_side(kernel, tmp_path):
    stl = tmp_path / "blob.stl"
    kernel.request("export", {"script": BOX_SCRIPT, "params": {}, "format": "stl",
                              "out_path": str(stl)})
    result = clearance(kernel, {"source": str(stl), "position": [0, 0, 0]},
                       item(CUBE10, position=(30, 0, 0)), min_mm=1.0)
    assert result["skipped_mesh"] == ["a"]
    assert result["distance_mm"] is None
    assert "ok" not in result

    both = clearance(kernel, {"source": str(stl)}, {"source": str(stl)})
    assert both["skipped_mesh"] == ["a", "b"]

    # no distance query was attempted on the mesh, so the worker is alive
    assert kernel.request("ping", {})["ok"] is True


def test_clearance_failure_is_a_structured_kernel_error(kernel):
    """A side that builds but cannot be measured degrades with a stage, never
    a crash or a hang. An empty Compound is legal to return and impossible to
    place, so it fails in the ``resolve`` stage; a ``distance`` stage failure
    carries the identical shape."""
    with pytest.raises(KernelError) as excinfo:
        clearance(kernel, item(EMPTY, name="left"),
                  item(CUBE10, position=(30, 0, 0), name="right"))
    assert excinfo.value.type == "kernel_error"
    assert "clearance unavailable" in excinfo.value.message
    assert excinfo.value.details["stage"] == "resolve"
    assert excinfo.value.details["side"] == "a"
    assert excinfo.value.details["a"] == "left"
    assert excinfo.value.details["b"] == "right"
    assert kernel.request("ping", {})["ok"] is True


def test_clearance_propagates_a_script_error_unchanged(kernel):
    broken = CUBE10.replace("return Box(p.s, p.s, p.s)", "raise ValueError('x')")
    with pytest.raises(KernelError) as excinfo:
        clearance(kernel, item(broken), item(CUBE10, position=(30, 0, 0)))
    assert excinfo.value.type == "script_error"
    assert excinfo.value.details["line"]


def test_pack_names_do_not_shadow_a_builtin_handler(kernel):
    """A colliding pack name is dropped with only a stderr warning, so the
    three method names must be absent from worker.HANDLERS before load."""
    import subprocess
    import sys

    code = ("from agentcad.kernel import worker;"
            "print(','.join(sorted(worker.HANDLERS)))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, check=True).stdout.strip()
    assert set(out.split(",")).isdisjoint(
        {"spec_declare", "spec_eval", "clearance"})
    # …and the loaded worker really does answer all three (nothing was dropped)
    assert declare(kernel, NO_SPECS)["declared"] == []
