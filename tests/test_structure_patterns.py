"""PRD-013 Assembly v2 — slice 1: pattern expansion, the single expansion point.

The invariant this file exists to guard: `mates.expand` REPLACES a patterned
base id with `<id>[0..count-1]` (never alongside it), it is the ONE place
patterns are flattened, and every consumer that reads `_resolved_instances`
therefore sees exactly N members — mass rolls up N×, interference counts C(N,2),
and changing `count` updates all three. Tests catch both failure directions:
double-counting (base + members both present) and under-counting (a consumer
that did not expand).
"""

import math

import pytest

from agentcad.core import mates
from agentcad.core.tools import build_registry

from .conftest import make_test_service

# A small bracket with a rigid connector, so a pattern of it has real mass.
BRACKET = '''\
from build123d import *

PARAMS = {"s": {"default": 8.0, "min": 1.0, "max": 50.0}}

def build(p):
    with BuildPart() as part:
        Box(p.s, p.s, p.s)
    return part.part

def connectors(p, part):
    return {"seat": {"type": "rigid", "location": ((0, 0, 0), (0, 0, 0))}}
'''


@pytest.fixture
def svc(kernel, tmp_path):
    """A service with all tool packs registered (installs the expansion
    wrappers on _resolved_instances / get_assembly)."""
    service = make_test_service(tmp_path / "projects", kernel)
    build_registry(service)  # loads tools_structure -> installs wrappers
    service.create_project("demo")
    service.create_part("demo", "cube", script=BRACKET)
    return service


# --------------------------------------------------- replace-not-add (pure)

def test_linear_pattern_expands_replace_not_add(svc):
    svc.set_assembly("demo", [{"id": "b", "part": "cube",
        "position": [0, 0, 0],
        "pattern": {"kind": "linear", "count": 3, "step_mm": 10}}])
    flat, warn = mates.expand(svc, "demo", svc.store.instances("demo"))
    ids = [i.id for i in flat]
    assert ids == ["b[0]", "b[1]", "b[2]"]          # base 'b' is ABSENT
    assert [i.position[0] for i in flat] == [0.0, 10.0, 20.0]
    assert all(i.pattern is None for i in flat)      # members are not re-expanded


def test_count_equals_sum_over_patterns_plus_singletons(svc):
    svc.set_assembly("demo", [
        {"id": "b", "part": "cube",
         "pattern": {"kind": "linear", "count": 4, "step_mm": 5}},
        {"id": "solo", "part": "cube", "position": [99, 0, 0]},
    ])
    flat, _ = mates.expand(svc, "demo", svc.store.instances("demo"))
    assert len(flat) == 4 + 1                        # Σ count + non-patterned
    assert "b" not in {i.id for i in flat}


def test_linear_expansion_makes_no_kernel_call(svc):
    """Linear patterns compose server-side (translation only) — a 1000-member
    bolt strip never round-trips to the kernel for placement."""
    calls = []
    inner = svc.kernel
    svc.kernel = _SpyKernel(inner, calls)
    try:
        svc.set_assembly("demo", [{"id": "b", "part": "cube",
            "pattern": {"kind": "linear", "count": 6, "step_mm": 3}}])
        calls.clear()
        flat, _ = mates.expand(svc, "demo", svc.store.instances("demo"))
    finally:
        svc.kernel = inner
    assert len(flat) == 6
    assert "resolve_assembly" not in calls           # no placement round-trip


class _SpyKernel:
    def __init__(self, inner, calls):
        self._inner = inner
        self._calls = calls

    def request(self, op, *a, **k):
        self._calls.append(op)
        return self._inner.request(op, *a, **k)

    def __getattr__(self, name):
        return getattr(self._inner, name)


# ------------------------------------------------------- polar (kernel)

def test_polar_pattern_reaims_members(svc):
    svc.set_assembly("demo", [{"id": "b", "part": "cube", "position": [20, 0, 0],
        "pattern": {"kind": "polar", "count": 4, "angle_step_deg": 90,
                    "axis": [[0, 0, 0], [0, 0, 1]], "center": [0, 0, 0]}}])
    flat, _ = mates.expand(svc, "demo", svc.store.instances("demo"))
    p = {i.id: i.position for i in flat}
    assert set(p) == {"b[0]", "b[1]", "b[2]", "b[3]"}
    # member 1 rotated 90 deg about +Z: (20,0,0) -> (0,20,0)
    assert p["b[1]"][0] == pytest.approx(0, abs=1e-6)
    assert p["b[1]"][1] == pytest.approx(20, abs=1e-6)
    # member 1's own orientation carries the 90 deg spin (re-aim)
    r = {i.id: i.rotation_deg for i in flat}
    assert r["b[1]"][2] == pytest.approx(90, abs=1e-6)


# ----------------------------------------- every consumer sees N (AC2 core)

def _one_cube_mass(svc):
    return svc.get_metrics("demo", "cube")["mass_g"]


def test_get_assembly_rolls_up_pattern_mass_and_tree(svc):
    one = _one_cube_mass(svc)
    svc.set_assembly("demo", [{"id": "b", "part": "cube", "position": [20, 0, 0],
        "pattern": {"kind": "polar", "count": 8, "angle_step_deg": 45,
                    "axis": [[0, 0, 0], [0, 0, 1]], "center": [0, 0, 0]}}])
    a = svc.get_assembly("demo")
    assert a["total_mass_g"] == pytest.approx(8 * one, rel=1e-9)
    assert len(a["instances"]) == 8                  # flattened view is N members
    # tree view keeps ONE node with a count badge
    node = next(n for n in a["tree"] if n["id"] == "b")
    assert node["count"] == 8 and node["kind"] == "polar"


def test_interference_counts_expanded_pairs(svc):
    svc.set_assembly("demo", [{"id": "b", "part": "cube", "position": [20, 0, 0],
        "pattern": {"kind": "polar", "count": 8, "angle_step_deg": 45,
                    "axis": [[0, 0, 0], [0, 0, 1]], "center": [0, 0, 0]}}])
    r = svc.check_interference("demo")
    assert r["checked"] == 8                          # C(8,2) candidate pairs
    # (checked = instance count; the kernel forms C(n,2) pairs from it)


def test_changing_count_updates_mass_and_interference(svc):
    one = _one_cube_mass(svc)
    registry = build_registry(svc)
    svc.set_assembly("demo", [{"id": "b", "part": "cube", "position": [20, 0, 0],
        "pattern": {"kind": "polar", "count": 8, "angle_step_deg": 45,
                    "axis": [[0, 0, 0], [0, 0, 1]], "center": [0, 0, 0]}}])
    assert svc.get_assembly("demo")["total_mass_g"] == pytest.approx(8 * one,
                                                                     rel=1e-9)
    assert svc.check_interference("demo")["checked"] == 8
    # focused set_pattern verb re-counts N EVERYWHERE from one edit
    registry.call("set_pattern", {"project": "demo", "instance": "b",
        "pattern": {"kind": "polar", "count": 6, "angle_step_deg": 60,
                    "axis": [[0, 0, 0], [0, 0, 1]], "center": [0, 0, 0]}})
    assert svc.get_assembly("demo")["total_mass_g"] == pytest.approx(6 * one,
                                                                     rel=1e-9)
    assert svc.check_interference("demo")["checked"] == 6
    node = next(n for n in svc.get_assembly("demo")["tree"] if n["id"] == "b")
    assert node["count"] == 6


def test_set_pattern_clear_reverts_to_single_instance(svc):
    registry = build_registry(svc)
    svc.set_assembly("demo", [{"id": "b", "part": "cube",
        "pattern": {"kind": "linear", "count": 3, "step_mm": 5}}])
    assert len(svc.get_assembly("demo")["instances"]) == 3
    registry.call("set_pattern", {"project": "demo", "instance": "b",
                                  "pattern": None})
    a = svc.get_assembly("demo")
    assert len(a["instances"]) == 1 and a["instances"][0]["id"] == "b"


def test_v1_flat_project_short_circuits_unchanged(svc):
    """No pattern / no assembly / no mate -> byte-identical passthrough (AC8):
    _resolved_instances returns the raw store instances object-for-object."""
    svc.set_assembly("demo", [{"id": "a", "part": "cube", "position": [1, 2, 3]}])
    resolved = svc._resolved_instances("demo")
    raw = svc.store.instances("demo")
    assert [i.id for i in resolved] == [i.id for i in raw]
    assert [i.position for i in resolved] == [[1, 2, 3]]
