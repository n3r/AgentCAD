"""Design-spec constructors (`agentcad.toolkit.specs`) — PRD-003 FR1/FR3/FR4.

Pure Python: declarations are data, so nothing here needs the kernel fixture,
a project store or git. The two contracts under test are the *shape* every
constructor produces (Slices 2-7 read these exact keys) and **eager
validation** — a bad argument must raise while the part script is executing,
so it surfaces as a `script_error` with `details.line` exactly like a
malformed `PARAMS`.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agentcad.toolkit import specs
from agentcad.toolkit.specs import (
    PART_KINDS,
    PROJECT_KINDS,
    SPEC_FORMAT,
    check_bbox,
    check_clearance,
    check_fem_static,
    check_interference_free,
    check_mass,
    check_stackup,
    check_that,
    check_valid,
    check_volume,
    check_wall,
)

BASE_KEYS = {"spec", "kind", "scope", "name", "limit", "requirement", "options"}

# one representative call per FR3 constructor: (declaration, expected kind)
PART_DECLS = [
    (check_valid(), "valid"),
    (check_mass(max_g=120.0), "mass"),
    (check_volume(min_mm3=10.0), "volume"),
    (check_bbox(80.0), "bbox"),
    (check_wall(2.5), "wall"),
    (check_fem_static({"axis": "z", "side": "min"}, {"axis": "z", "side": "max"},
                      500.0, max_vm_mpa=200.0), "fem_static"),
]
PROJECT_DECLS = [
    (check_interference_free(), "interference_free"),
    (check_clearance("flange_1", "injector_plate_1", 0.5), "clearance"),
    (check_stackup("nozzle_1", "chamber_1", "z", 0.4), "stackup"),
]


# ---- shape: every constructor produces the same seven keys -------------------

@pytest.mark.parametrize("decl,kind", PART_DECLS + PROJECT_DECLS)
def test_constructors_share_one_record_shape(decl, kind):
    assert set(decl) == BASE_KEYS
    assert decl["spec"] == SPEC_FORMAT == 1
    assert decl["kind"] == kind
    assert isinstance(decl["name"], str) and decl["name"]
    assert isinstance(decl["limit"], dict)
    assert isinstance(decl["options"], dict)
    assert decl["requirement"] is None


@pytest.mark.parametrize("decl,kind", PART_DECLS)
def test_part_scope_kinds(decl, kind):
    assert decl["scope"] == "part"
    assert kind in PART_KINDS


@pytest.mark.parametrize("decl,kind", PROJECT_DECLS)
def test_project_scope_kinds(decl, kind):
    assert decl["scope"] == "project"
    assert kind in PROJECT_KINDS


def test_check_that_is_part_scope_and_carries_the_callable():
    decl = check_that(lambda part, metrics: True, "fits_fairing")
    assert set(decl) == BASE_KEYS | {"fn"}
    assert decl["kind"] == "that" and decl["scope"] == "part"
    assert decl["name"] == "fits_fairing"
    assert callable(decl["fn"])
    assert "that" in PART_KINDS


def test_kind_tuples_cover_the_vocabulary_and_do_not_overlap():
    assert set(PART_KINDS) & set(PROJECT_KINDS) == set()
    kinds = {d["kind"] for d, _ in PART_DECLS + PROJECT_DECLS}
    kinds.add(check_that(lambda p, m: True, "x")["kind"])
    assert kinds == set(PART_KINDS) | set(PROJECT_KINDS)


# ---- names and requirements --------------------------------------------------

@pytest.mark.parametrize("decl,expected", [
    (check_valid(), "valid"),
    (check_mass(max_g=120.0), "mass_max"),
    (check_mass(min_g=1.0), "mass_min"),
    (check_mass(min_g=1.0, max_g=2.0), "mass_range"),
    (check_volume(max_mm3=5.0), "volume_max"),
    (check_bbox(80.0), "bbox_within"),
    (check_wall(2.5), "wall_min"),
    (check_interference_free(), "no_interference"),
    (check_clearance("flange_1", "injector_plate_1", 0.5),
     "clearance_flange_1_injector_plate_1"),
    (check_stackup("nozzle_1", "chamber_1", "z", 0.4),
     "stackup_nozzle_1_chamber_1_z"),
    (check_fem_static({"axis": "z", "side": "min"}, {"axis": "z", "side": "max"},
                      500.0, max_disp_mm=0.2), "fem_static"),
])
def test_default_names(decl, expected):
    assert decl["name"] == expected


def test_explicit_name_wins():
    assert check_wall(2.5, name="nozzle_wall")["name"] == "nozzle_wall"


@pytest.mark.parametrize("requirement", [
    "SYS-042",
    "https://reqs.example.com/issues/ENG-014#thin-wall",
])
def test_requirement_rides_through_verbatim(requirement):
    """The string is opaque — an id or a URL — and is never parsed."""
    decls = [
        check_valid(requirement=requirement),
        check_mass(max_g=1.0, requirement=requirement),
        check_volume(max_mm3=1.0, requirement=requirement),
        check_bbox(1.0, requirement=requirement),
        check_wall(1.0, requirement=requirement),
        check_that(lambda p, m: True, "x", requirement=requirement),
        check_fem_static({"axis": "z", "side": "min"},
                         {"axis": "z", "side": "max"}, 1.0,
                         max_vm_mpa=1.0, requirement=requirement),
        check_interference_free(requirement=requirement),
        check_clearance("a", "b", 1.0, requirement=requirement),
        check_stackup("a", "b", "z", 1.0, requirement=requirement),
    ]
    assert [d["requirement"] for d in decls] == [requirement] * 10


# ---- limits and options ------------------------------------------------------

def test_bbox_accepts_a_scalar_and_a_vector():
    assert check_bbox(80.0)["limit"] == {"within_mm": [80.0, 80.0, 80.0]}
    assert check_bbox([10, 20, 30])["limit"] == {"within_mm": [10.0, 20.0, 30.0]}
    assert check_bbox((10, 20, 30))["limit"] == {"within_mm": [10.0, 20.0, 30.0]}


def test_limits_name_their_bound():
    assert check_mass(max_g=120)["limit"] == {"max_g": 120.0}
    assert check_mass(min_g=1, max_g=2)["limit"] == {"min_g": 1.0, "max_g": 2.0}
    assert check_volume(min_mm3=3)["limit"] == {"min_mm3": 3.0}
    assert check_wall(2.5)["limit"] == {"min_mm": 2.5}
    assert check_valid()["limit"] == {}
    assert check_clearance("a", "b", 0.5)["limit"] == {"min_mm": 0.5}
    assert check_interference_free()["limit"] == {"min_volume_mm3": 0.001}
    assert check_stackup("a", "b", "z", 0.4)["limit"] == {"within_mm": 0.4}
    assert check_fem_static({"axis": "z", "side": "min"},
                            {"axis": "z", "side": "max"}, 500.0,
                            max_vm_mpa=200.0, max_disp_mm=0.2)["limit"] == {
        "max_vm_mpa": 200.0, "max_disp_mm": 0.2}


def test_options_carry_what_to_measure_not_the_threshold():
    assert check_wall(2.5)["options"] == {"grid": 8}
    assert check_wall(2.5, grid=16)["options"] == {"grid": 16}
    assert check_clearance("a", "b", 0.5)["options"] == {"a": "a", "b": "b"}
    assert check_stackup("a", "b", "z", 0.4)["options"] == {
        "from_instance": "a", "to_instance": "b", "axis": "z"}
    assert check_fem_static({"axis": "x", "side": "min"},
                            {"axis": "z", "side": "max"}, 500,
                            max_vm_mpa=200.0)["options"] == {
        "fixed_face": {"axis": "x", "side": "min"},
        "load_face": {"axis": "z", "side": "max"}, "load_N": 500.0}
    assert check_valid()["options"] == {}


def test_numbers_are_normalized_to_float():
    decl = check_mass(min_g=1, max_g=2)
    assert all(isinstance(v, float) for v in decl["limit"].values())
    assert isinstance(check_wall(2, grid=8.0)["options"]["grid"], int)


# ---- eager validation: FR1's mechanism --------------------------------------

@pytest.mark.parametrize("call,argument", [
    (lambda: check_wall(min_mm="thick"), "min_mm"),
    (lambda: check_wall(min_mm=0), "min_mm"),
    (lambda: check_wall(min_mm=-1.0), "min_mm"),
    (lambda: check_wall(2.5, grid=0), "grid"),
    (lambda: check_mass(), "min_g"),
    (lambda: check_mass(min_g=5, max_g=1), "max_g"),
    (lambda: check_mass(min_g="light"), "min_g"),
    (lambda: check_volume(), "min_mm3"),
    (lambda: check_volume(min_mm3=9, max_mm3=8), "max_mm3"),
    (lambda: check_bbox([1, 2]), "within_mm"),
    (lambda: check_bbox("big"), "within_mm"),
    (lambda: check_bbox(0), "within_mm"),
    (lambda: check_that("not callable", name="x"), "fn"),
    (lambda: check_that(lambda p, m: True, name=""), "name"),
    (lambda: check_clearance("a", "a", min_mm=1), "b"),
    (lambda: check_clearance("", "b", min_mm=1), "a"),
    (lambda: check_clearance("a", "b", min_mm=-1), "min_mm"),
    (lambda: check_stackup("a", "b", axis="w", within=1.0), "axis"),
    (lambda: check_stackup("a", "b", axis="z", within=-1.0), "within"),
    (lambda: check_stackup("a", "a", axis="z", within=1.0), "to_instance"),
    (lambda: check_interference_free(min_volume_mm3=-1), "min_volume_mm3"),
    (lambda: check_fem_static({"axis": "q", "side": "min"},
                              {"axis": "z", "side": "max"}, 1.0,
                              max_vm_mpa=1.0), "fixed_face"),
    (lambda: check_fem_static({"axis": "z", "side": "middle"},
                              {"axis": "z", "side": "max"}, 1.0,
                              max_vm_mpa=1.0), "fixed_face"),
    (lambda: check_fem_static({"axis": "z", "side": "min"}, "top", 1.0,
                              max_vm_mpa=1.0), "load_face"),
    (lambda: check_fem_static({"axis": "z", "side": "min"},
                              {"axis": "z", "side": "max"}, 0.0,
                              max_vm_mpa=1.0), "load_N"),
    (lambda: check_fem_static({"axis": "z", "side": "min"},
                              {"axis": "z", "side": "max"}, 1.0), "max_vm_mpa"),
    (lambda: check_wall(2.5, name=42), "name"),
    (lambda: check_wall(2.5, name="a:b"), "name"),
    (lambda: check_wall(2.5, requirement=42), "requirement"),
    (lambda: check_wall(2.5, requirement="  "), "requirement"),
])
def test_bad_arguments_raise_at_construction_naming_the_argument(call, argument):
    with pytest.raises(ValueError) as excinfo:
        call()
    assert argument in str(excinfo.value)


def test_booleans_are_not_numbers():
    with pytest.raises(ValueError):
        check_wall(min_mm=True)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_numbers_are_rejected_at_construction(value):
    """A NaN limit is false green: every ordered comparison against it is
    false, so ``_bounded`` never enters its over-limit branch and the check
    passes without measuring anything. An infinite limit is one no measurement
    can ever breach. Both are rejected where the argument is read."""
    with pytest.raises(ValueError) as excinfo:
        check_mass(max_g=value)
    assert "max_g" in str(excinfo.value)
    with pytest.raises(ValueError):
        check_wall(min_mm=value)
    with pytest.raises(ValueError):
        check_bbox([1.0, value, 3.0])
    with pytest.raises(ValueError):
        check_clearance("a", "b", min_mm=value)
    with pytest.raises(ValueError):
        check_stackup("a", "b", "z", value)
    with pytest.raises(ValueError):
        check_fem_static({"axis": "z", "side": "min"},
                         {"axis": "z", "side": "max"}, 1.0, max_vm_mpa=value)


# ---- the JSON boundary -------------------------------------------------------

@pytest.mark.parametrize("decl,_kind", PART_DECLS + PROJECT_DECLS)
def test_declarations_are_json_safe(decl, _kind):
    assert json.loads(json.dumps(decl)) == decl


def test_check_that_is_json_safe_once_the_callable_is_dropped():
    decl = check_that(lambda part, metrics: True, "fits_fairing",
                      requirement="SYS-011")
    with pytest.raises(TypeError):
        json.dumps(decl)
    stripped = {k: v for k, v in decl.items() if k != "fn"}
    assert json.loads(json.dumps(stripped)) == stripped
    # otherwise identical to any other declaration
    assert set(stripped) == BASE_KEYS


def test_json_safe_helper_strips_the_callable_and_marks_the_predicate():
    decl = check_that(lambda part, metrics: True, "fits_fairing")
    safe = specs.json_safe(decl)
    assert "fn" not in safe and safe["predicate"] is True
    assert json.loads(json.dumps(safe)) == safe
    # a declaration with no callable round-trips unchanged
    wall = check_wall(2.5)
    assert specs.json_safe(wall) == wall
    assert specs.json_safe(wall) is not wall


def test_is_declaration_recognizes_the_format_marker():
    assert specs.is_declaration(check_wall(2.5)) is True
    assert specs.is_declaration({"kind": "wall", "scope": "part"}) is False
    assert specs.is_declaration({"spec": 999, "kind": "wall",
                                 "scope": "part"}) is False
    assert specs.is_declaration({"spec": 1, "kind": "nope",
                                 "scope": "part"}) is False
    assert specs.is_declaration("hello") is False


# a hand-written dict with every key a constructor emits
FULL_DICT = {"spec": SPEC_FORMAT, "kind": "mass", "scope": "part",
             "name": "mass_max", "limit": {"max_g": 120.0},
             "requirement": "SYS-042", "options": {}}


def test_is_declaration_requires_every_key_a_constructor_emits():
    """A dict missing ``name``/``limit``/``options`` used to be accepted, and
    the readers downstream (``_record``, ``_residue``) read those keys without
    a guard — so an incomplete hand-written ``SPECS`` entry became a KeyError
    in the *server*, i.e. a 500, instead of structural residue."""
    assert specs.is_declaration(FULL_DICT) is True
    assert specs.is_declaration({"spec": 1, "kind": "mass",
                                 "scope": "part"}) is False
    for key, bad in (("name", 42), ("name", None), ("limit", None),
                     ("limit", 3), ("options", "none"), ("requirement", 7)):
        assert specs.is_declaration({**FULL_DICT, key: bad}) is False, key
    # requirement is the one optional key: None is what a constructor emits
    assert specs.is_declaration({**FULL_DICT, "requirement": None}) is True


def test_declaration_problem_names_the_key_that_is_wrong():
    """The rejection message is what a script author reads, so it must say
    which key — 'not a declaration' alone is not actionable."""
    assert specs.declaration_problem(check_wall(2.5)) is None
    assert specs.declaration_problem(FULL_DICT) is None
    for key in ("name", "limit", "options"):
        missing = {k: v for k, v in FULL_DICT.items() if k != key}
        assert key in specs.declaration_problem(missing)
    assert "spec" in specs.declaration_problem({"kind": "mass"})
    assert "kind" in specs.declaration_problem({**FULL_DICT, "kind": "nope"})
    assert "scope" in specs.declaration_problem({**FULL_DICT, "scope": "all"})
    assert specs.declaration_problem("hello")


# ---- packaging ---------------------------------------------------------------

def test_package_reexports_specs_lazily():
    import importlib

    import agentcad.toolkit as toolkit

    assert "specs" in toolkit.__all__
    assert toolkit.__getattr__("specs") is importlib.import_module(
        "agentcad.toolkit.specs")
    assert toolkit.specs.SPEC_FORMAT == 1


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
mod = importlib.import_module("agentcad.toolkit.specs")
assert mod.check_wall(2.5)["kind"] == "wall"
assert mod.check_fem_static({"axis": "z", "side": "min"},
                            {"axis": "z", "side": "max"}, 1.0,
                            max_vm_mpa=1.0)["kind"] == "fem_static"
assert "OCP" not in sys.modules and "build123d" not in sys.modules
print("ok")
"""


@pytest.mark.integration
@pytest.mark.portability
def test_module_imports_with_no_kernel_available():
    """FR4: declaring is data. It must work where OCP/build123d cannot import."""
    repo = Path(__file__).resolve().parents[1]
    proc = subprocess.run([sys.executable, "-c", _NO_KERNEL_PROBE],
                          cwd=repo, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().endswith("ok")
