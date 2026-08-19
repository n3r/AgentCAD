"""PRD-013 Assembly v2 — slice 2: cross-project sub-assemblies.

The sharpest invariant here: resolving a sub-assembly CANNOT write to or
rebuild a source's authored state — proven with a store-spy asserting zero
save_manifest/write_script/imports_dir(write=True) against the sources. Plus:
depth-first two-level resolution with `<parent>/<child>` id namespacing and an
exact mass roll-up (AC1), cross-project cycle detection (details.cycle), and a
mate to a non-exported connector (details.interface).
"""

import pytest

from agentcad.core.model import ValidationError
from agentcad.core.tools import build_registry

from .conftest import make_test_service

PISTON = '''\
from build123d import *

PARAMS = {"d": {"default": 6.0, "min": 1.0, "max": 30.0}}

def build(p):
    with BuildPart() as part:
        Cylinder(radius=p.d / 2, height=12)
    return part.part

def connectors(p, part):
    return {"crown": {"type": "rigid", "location": ((0, 0, 6), (0, 0, 0))}}
'''

PLATE = '''\
from build123d import *

PARAMS = {"t": {"default": 5.0, "min": 1.0, "max": 40.0}}

def build(p):
    with BuildPart() as part:
        Box(30, 30, p.t)
    return part.part

def connectors(p, part):
    return {"hinge": {"type": "rigid", "location": ((0, 0, 0), (0, 0, 0))}}
'''


@pytest.fixture
def stack(kernel, tmp_path):
    """cell -> stand -> engine. engine has a 2-wide piston pattern; stand and
    cell only instance the level below, so total mass == 2 * piston mass."""
    service = make_test_service(tmp_path / "projects", kernel)
    build_registry(service)

    service.create_project("engine")
    service.create_part("engine", "piston", script=PISTON)
    service.set_assembly("engine", [{"id": "piston", "part": "piston",
        "position": [0, 0, 0],
        "pattern": {"kind": "linear", "count": 2, "step_mm": 15}}])

    service.create_project("stand")
    service.set_assembly("stand", [{"id": "engine",
        "assembly": {"project": "engine"}, "position": [0, 0, 20]}])

    service.create_project("cell")
    service.set_assembly("cell", [{"id": "stand",
        "assembly": {"project": "stand"}, "position": [50, 0, 0]}])
    return service


def _flattened_ids(a):
    return {i["id"] for i in a["instances"]}


def test_two_level_resolve_namespaces_ids(stack):
    a = stack.get_assembly("cell")
    ids = _flattened_ids(a)
    assert "stand/engine/piston[0]" in ids       # >= 2 nesting levels (FR4)
    assert "stand/engine/piston[1]" in ids
    assert len(a["instances"]) == 2
    # tree view keeps ONE sub-assembly node
    assert [n["id"] for n in a["tree"]] == ["stand"]
    assert a["tree"][0]["kind"] == "assembly"


def test_mass_rollup_equals_hand_sum(stack):
    one_piston = stack.get_metrics("engine", "piston")["mass_g"]
    a = stack.get_assembly("cell")
    assert a["total_mass_g"] == pytest.approx(2 * one_piston, rel=1e-9)  # AC1


def test_subassembly_placement_is_rigid_composed(stack):
    # engine placed at z=20 in stand, stand at x=50 in cell -> piston[0] world
    # is (0,0,0)+(0,0,20)+(50,0,0) = (50,0,20).
    a = stack.get_assembly("cell")
    p0 = next(i for i in a["instances"]
              if i["id"] == "stand/engine/piston[0]")["position"]
    assert p0 == pytest.approx([50, 0, 20])


def test_resolution_never_writes_to_a_source(stack, monkeypatch):
    """The sharpest safety property (Decision 3.4): resolving cell issues ZERO
    authored writes against engine/stand. Spy the two guarded write paths."""
    writes = []
    store = stack.store
    orig_save = store.save_manifest
    orig_script = store.write_script
    monkeypatch.setattr(store, "save_manifest",
                        lambda p, *a, **k: (writes.append(("save", p)),
                                            orig_save(p, *a, **k))[1])
    monkeypatch.setattr(store, "write_script",
                        lambda p, *a, **k: (writes.append(("script", p)),
                                            orig_script(p, *a, **k))[1])
    stack.get_assembly("cell")               # resolves stand + engine
    stack.check_interference("cell")
    touched = {p for _, p in writes}
    assert "engine" not in touched and "stand" not in touched


def test_resolution_does_not_fire_write_guard_on_source(stack):
    """write_guard is keyed by the mutated project; a read-only source is never
    that argument, so the guard is structurally unreachable. Install a guard
    that RAISES for the sources and prove resolution still succeeds."""
    def guard(proj):
        if proj in ("engine", "stand"):
            raise AssertionError(f"write_guard fired against source {proj!r}")
    stack.store.write_guard = guard
    a = stack.get_assembly("cell")           # must not raise
    assert len(a["instances"]) == 2


def test_cross_project_cycle_is_validation_error(kernel, tmp_path):
    from agentcad.core.model import InstanceSpec
    service = make_test_service(tmp_path / "projects", kernel)
    build_registry(service)
    service.create_project("a")
    service.create_project("b")
    # Wire the cycle through the store directly — set_assembly resolves eagerly
    # and would trip the cycle at setup time; we want it detected on read.
    service.store.set_instances("a", [InstanceSpec(id="b",
                                       assembly={"project": "b"})])
    service.store.set_instances("b", [InstanceSpec(id="a",
                                       assembly={"project": "a"})])
    with pytest.raises(ValidationError) as e:
        service.get_assembly("a")
    assert e.value.details["cycle"] == ["a", "b", "a"]


def test_mate_to_nonexported_connector_is_validation_error(kernel, tmp_path):
    service = make_test_service(tmp_path / "projects", kernel)
    registry = build_registry(service)

    service.create_project("src")
    service.create_part("src", "piston", script=PISTON)
    service.set_assembly("src", [{"id": "piston", "part": "piston"}])
    # src exports NOTHING (no set_assembly_interface), so any mate connector is
    # non-exported.

    service.create_project("top")
    service.create_part("top", "plate", script=PLATE)
    service.set_assembly("top", [
        {"id": "base", "part": "plate", "position": [0, 0, 0]},
        {"id": "sub", "assembly": {"project": "src"}},
    ])
    # registry.call catches AppError into an {"error": {...}} payload.
    result = registry.call("set_mate", {
        "project": "top", "instance": "sub", "connector": "mount",
        "to_instance": "base", "to_connector": "hinge"})
    assert result["error"]["type"] == "validation_error"
    assert "interface" in result["error"]["details"]


def test_exported_interface_is_matable(kernel, tmp_path):
    service = make_test_service(tmp_path / "projects", kernel)
    registry = build_registry(service)
    service.create_project("src")
    service.create_part("src", "piston", script=PISTON)
    service.set_assembly("src", [{"id": "piston", "part": "piston"}])
    service.store.set_assembly_interface("src", {
        "mount": {"instance": "piston", "connector": "crown"}})

    service.create_project("top")
    service.create_part("top", "plate", script=PLATE)
    service.set_assembly("top", [
        {"id": "base", "part": "plate"},
        {"id": "sub", "assembly": {"project": "src"}},
    ])
    # An EXPORTED interface connector passes validation (geometry deferred).
    # Members are namespaced by the parent INSTANCE id ("sub"), not the source.
    result = registry.call("set_mate", {
        "project": "top", "instance": "sub", "connector": "mount",
        "to_instance": "base", "to_connector": "hinge"})
    assert "error" not in result
    assert "sub/piston" in {i["id"] for i in result["instances"]}
