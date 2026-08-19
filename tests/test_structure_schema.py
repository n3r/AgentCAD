"""PRD-013 Assembly v2 — slice 1: manifest schema bump (pattern/assembly),
key-wise interface/couplings merge, structure_problems referential check.

Pure/near-pure tests (no kernel) — the schema round-trips and validates, old
files load unchanged, and the merge treats the new maps key-wise.
"""

import json

import pytest

from agentcad.core.manifest_merge import merge_manifests, structure_problems
from agentcad.core.model import InstanceSpec, ValidationError

from .conftest import BOX_SCRIPT, make_test_service


@pytest.fixture
def store_proj(kernel, tmp_path):
    service = make_test_service(tmp_path / "projects", kernel)
    service.create_project("demo")
    service.create_part("demo", "cube", script=BOX_SCRIPT)
    return service.store, "demo"


# ------------------------------------------------------------ round-trip

def test_instance_pattern_and_assembly_roundtrip(store_proj):
    store, proj = store_proj
    store.set_instances(proj, [
        InstanceSpec(id="bolt", part="cube", position=[40, 0, 0],
                     pattern={"kind": "polar", "count": 8,
                              "angle_step_deg": 45}),
        InstanceSpec(id="sub", assembly={"project": "engine"}),
    ])
    got = {i.id: i for i in store.instances(proj)}
    assert got["bolt"].pattern["count"] == 8
    assert got["bolt"].assembly is None
    assert got["sub"].assembly["project"] == "engine"
    assert got["sub"].part == ""  # assembly instance carries no part


def test_v1_manifest_without_new_keys_loads(store_proj):
    store, proj = store_proj
    store.set_instances(proj, [InstanceSpec(id="a", part="cube")])
    insts = store.instances(proj)
    assert all(i.pattern is None and i.assembly is None for i in insts)
    # pattern/assembly omitted from the on-disk manifest (byte-clean v1 shape)
    raw = json.loads((store.canonical_path_of(proj) / "project.json")
                     .read_text())
    entry = raw["assembly"]["instances"][0]
    assert "pattern" not in entry and "assembly" not in entry


# ------------------------------------------------------------- validation

def test_pattern_on_a_subassembly_reference_is_allowed(store_proj):
    # spec §1.1: a pattern may decorate an `assembly` instance (repeat a
    # sub-assembly). What is refused is `part` + `assembly` together.
    store, proj = store_proj
    store.set_instances(proj, [InstanceSpec(
        id="x", assembly={"project": "engine"},
        pattern={"kind": "linear", "count": 2, "step_mm": 5})])
    got = store.instances(proj)[0]
    assert got.assembly["project"] == "engine" and got.pattern["count"] == 2


def test_set_instances_rejects_assembly_with_part(store_proj):
    store, proj = store_proj
    with pytest.raises(ValidationError):
        store.set_instances(proj, [InstanceSpec(
            id="x", part="cube", assembly={"project": "engine"})])


def test_set_instances_rejects_unknown_pattern_kind(store_proj):
    store, proj = store_proj
    with pytest.raises(ValidationError):
        store.set_instances(proj, [InstanceSpec(
            id="x", part="cube", pattern={"kind": "spiral", "count": 2})])


def test_set_instances_rejects_pattern_count_below_one(store_proj):
    store, proj = store_proj
    with pytest.raises(ValidationError):
        store.set_instances(proj, [InstanceSpec(
            id="x", part="cube",
            pattern={"kind": "linear", "count": 0, "step_mm": 5})])


def test_linear_pattern_requires_step(store_proj):
    store, proj = store_proj
    with pytest.raises(ValidationError):
        store.set_instances(proj, [InstanceSpec(
            id="x", part="cube", pattern={"kind": "linear", "count": 3})])


def test_polar_pattern_requires_angle_step(store_proj):
    store, proj = store_proj
    with pytest.raises(ValidationError):
        store.set_instances(proj, [InstanceSpec(
            id="x", part="cube", pattern={"kind": "polar", "count": 3})])


# ------------------------------------------------- interface store round-trip

def test_assembly_interface_roundtrip_and_referential(store_proj):
    store, proj = store_proj
    store.set_instances(proj, [InstanceSpec(id="a", part="cube")])
    store.set_assembly_interface(proj, {"mount": {"instance": "a",
                                                  "connector": "top"}})
    assert store.assembly_interface(proj) == {
        "mount": {"instance": "a", "connector": "top"}}
    # export naming a missing instance is refused at write time
    with pytest.raises(ValidationError):
        store.set_assembly_interface(proj, {"bad": {"instance": "gone",
                                                    "connector": "c"}})


# ------------------------------------------------------- key-wise merge

def _asm(instances=None, interface=None, couplings=None):
    a = {"instances": instances or []}
    if interface is not None:
        a["interface"] = interface
    if couplings is not None:
        a["couplings"] = couplings
    return {"name": "p", "parts": [], "assembly": a}


def test_two_branches_add_different_interface_names_merge_clean():
    base = _asm(interface={})
    ours = _asm(interface={"a": {"instance": "p", "connector": "c"}})
    theirs = _asm(interface={"b": {"instance": "q", "connector": "d"}})
    merged, conflicts = merge_manifests(base, ours, theirs)
    assert conflicts == []
    assert set(merged["assembly"]["interface"]) == {"a", "b"}


def test_same_interface_name_edited_both_sides_conflicts():
    base = _asm(interface={"a": {"instance": "p", "connector": "c"}})
    ours = _asm(interface={"a": {"instance": "p", "connector": "X"}})
    theirs = _asm(interface={"a": {"instance": "p", "connector": "Y"}})
    merged, conflicts = merge_manifests(base, ours, theirs)
    assert len(conflicts) == 1
    assert conflicts[0]["key"] == "assembly.interface.a"


def test_two_branches_add_different_couplings_merge_clean():
    base = _asm(couplings={})
    ours = _asm(couplings={"g": {"a_instance": "x", "b_instance": "y"}})
    theirs = _asm(couplings={"h": {"a_instance": "m", "b_instance": "n"}})
    merged, conflicts = merge_manifests(base, ours, theirs)
    assert conflicts == []
    assert set(merged["assembly"]["couplings"]) == {"g", "h"}


def test_structure_problems_flags_dangling_interface():
    m = _asm(instances=[], interface={"a": {"instance": "gone",
                                            "connector": "c"}})
    kinds = {p["kind"] for p in structure_problems(m)}
    assert "dangling_interface" in kinds


def test_structure_problems_flags_dangling_coupling():
    m = _asm(instances=[{"id": "x", "part": "cube"}],
             couplings={"g": {"a_instance": "x", "b_instance": "gone"}})
    kinds = {p["kind"] for p in structure_problems(m)}
    assert "dangling_coupling" in kinds


def test_structure_problems_silent_on_healthy():
    m = _asm(instances=[{"id": "x", "part": "cube"}],
             interface={"a": {"instance": "x", "connector": "c"}},
             couplings={"g": {"a_instance": "x", "b_instance": "x"}})
    assert structure_problems(m) == []
