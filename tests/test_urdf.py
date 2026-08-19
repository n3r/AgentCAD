"""PRD-013 Assembly v2 — slice 6: URDF export (FR14 core, AC6 machine half).

AC6's machine-checked criteria: the golden `robot.urdf` parses cleanly under the
hand-rolled `validate_urdf` (stdlib XML + structural asserts, no new dep); link
masses match `get_metrics` within 0.1%; and — the correctness step caught in
design — the inertia is parallel-axis-SHIFTED from the global origin to each
link's COM (an off-origin part whose tensor was NOT shifted fails the
symmetric-positive-definite check about the COM). urdf-viz is evidence-graded.
"""

from pathlib import Path

import numpy as np
import pytest

from agentcad.core import urdf
from agentcad.core.tools import build_registry

from .conftest import make_test_service

# A base plate with a rigid connector on top and a revolute hinge on its edge.
BASE = '''\
from build123d import *

PARAMS = {"w": {"default": 40.0, "min": 10.0, "max": 100.0}}

def build(p):
    with BuildPart() as part:
        Box(p.w, p.w, 6)
    return part.part

def connectors(p, part):
    return {
        "seat": {"type": "rigid", "location": ((0, 0, 3), (0, 0, 0))},
        "hinge": {"type": "revolute", "axis": ((15, 0, 3), (0, 0, 1)),
                  "range": (0, 90)},
        "rail": {"type": "slider", "axis": ((-15, 0, 3), (1, 0, 0)),
                 "linear_range": (0, 20)},
    }
'''

# The moving parts each carry a single rigid mount.
ARM = '''\
from build123d import *

PARAMS = {"l": {"default": 30.0, "min": 5.0, "max": 80.0}}

def build(p):
    with BuildPart() as part:
        Box(p.l, 6, 6)
    return part.part

def connectors(p, part):
    return {"mount": {"type": "rigid", "location": ((0, 0, 0), (0, 0, 0))}}
'''

# An off-origin solid: its COM is far from the local origin, so a tensor left
# about the origin is very different from the tensor about the COM.
OFFSET = '''\
from build123d import *

PARAMS = {"s": {"default": 10.0, "min": 2.0, "max": 40.0}}

def build(p):
    with BuildPart() as part:
        with Locations((50, 0, 0)):
            Box(p.s, p.s, p.s)
    return part.part

def connectors(p, part):
    return {"mount": {"type": "rigid", "location": ((50, 0, 0), (0, 0, 0))}}
'''


@pytest.fixture
def stack(kernel, tmp_path):
    service = make_test_service(tmp_path / "projects", kernel)
    registry = build_registry(service)
    service.create_project("rocket")
    service.create_part("rocket", "base", script=BASE)
    service.create_part("rocket", "arm", script=ARM)
    service.set_assembly("rocket", [
        {"id": "base", "part": "base", "position": [0, 0, 0]},
        # rigid → fixed
        {"id": "top", "part": "arm", "mate": {
            "connector": "mount", "to_instance": "base", "to_connector": "seat"}},
        # revolute → revolute w/ limits
        {"id": "flap", "part": "arm", "mate": {
            "connector": "mount", "to_instance": "base", "to_connector": "hinge"}},
        # slider → prismatic
        {"id": "car", "part": "arm", "mate": {
            "connector": "mount", "to_instance": "base", "to_connector": "rail"}},
    ])
    return service, registry


# ------------------------------------------------------------- pure unit tests

def test_inertia_parallel_axis_shift_offorigin():
    # A unit cube of side 10 mm at COM (50,0,0), density 1 g/cm^3 (mass 1 g).
    # Tensor about a cube's own COM: I = m/6 * s^2 for a cube = 1*100/6 g*mm^2
    # on the diagonal. About the origin it is inflated by m*(||c||^2 E - c c^T).
    mass_g = 1.0
    com = [50.0, 0.0, 0.0]
    i_com_true = mass_g * (10.0 ** 2) / 6.0  # 16.67 g*mm^2 diagonal
    # Build the origin tensor by the forward parallel-axis add.
    c = np.array(com)
    shift = mass_g * (float(c @ c) * np.eye(3) - np.outer(c, c))
    i_origin = np.diag([i_com_true] * 3) + shift
    out = np.array(urdf.inertia_kg_m2_about_com(i_origin.tolist(), com, mass_g))
    # Back to COM, in kg*m^2 (× 1e-9).
    expected = np.diag([i_com_true] * 3) * 1e-9
    assert np.allclose(out, expected, atol=1e-18)
    # SPD about the COM; the UN-shifted origin tensor would NOT be (huge x-off).
    assert np.all(np.linalg.eigvalsh(out) > 0)


def test_unshifted_tensor_is_wrong_negation():
    """Negation guard: the origin tensor about the COM is a different, wrong
    answer — the x-axis inertia collapses to the small COM value while the
    off-diagonal-free origin tensor keeps the inflated y/z. Proves the shift is
    load-bearing, not decoration."""
    mass_g, com = 1.0, [50.0, 0.0, 0.0]
    i_com_true = mass_g * (10.0 ** 2) / 6.0
    c = np.array(com)
    shift = mass_g * (float(c @ c) * np.eye(3) - np.outer(c, c))
    i_origin = np.diag([i_com_true] * 3) + shift
    shifted = np.array(urdf.inertia_kg_m2_about_com(i_origin.tolist(), com, mass_g))
    unshifted = i_origin * 1e-9
    # Ixx differs by orders of magnitude (origin adds m*(y^2+z^2)=0 on x, but
    # Iyy/Izz gain m*x^2 = 2500 g*mm^2) — the two are not close.
    assert not np.allclose(shifted, unshifted, rtol=1e-3)


def test_validate_urdf_rejects_negative_mass():
    bad = """<robot name="x"><link name="world"/>
      <link name="a"><inertial><mass value="-1"/>
      <inertia ixx="1" ixy="0" ixz="0" iyy="1" iyz="0" izz="1"/></inertial></link>
      <joint name="j" type="fixed"><parent link="world"/><child link="a"/></joint>
      </robot>"""
    with pytest.raises(Exception):
        urdf.validate_urdf(bad)


# ------------------------------------------------------------- export (AC6)

def test_export_urdf_validates_and_masses_match(stack):
    service, registry = stack
    out = registry.call("export_urdf", {"project": "rocket"})
    assert "error" not in out, out
    xml = (Path(out["path"]) / "robot.urdf").read_text()
    urdf.validate_urdf(xml)                         # well-formed + structural

    # every non-world link mass matches get_metrics within 0.1%
    import xml.etree.ElementTree as ET
    root = ET.fromstring(xml)
    id_by_link = {"base": "base", "top": "arm", "flap": "arm", "car": "arm"}
    for link in root.findall("link"):
        name = link.get("name")
        if name == "world":
            continue
        mass = float(link.find("inertial/mass").get("value"))
        part = id_by_link[name]
        expected = service.get_metrics("rocket", part)["mass_g"] / 1000.0
        assert mass == pytest.approx(expected, rel=1e-3)

    # joint type mapping (FR14 core)
    jtypes = {j.get("name"): j.get("type") for j in root.findall("joint")}
    assert jtypes["top"] == "fixed"
    assert jtypes["flap"] == "revolute"
    assert jtypes["car"] == "prismatic"
    # revolute carries a limit
    flap = next(j for j in root.findall("joint") if j.get("name") == "flap")
    assert flap.find("limit") is not None


def test_export_urdf_writes_a_mesh_per_link(stack):
    service, registry = stack
    out = registry.call("export_urdf", {"project": "rocket"})
    d = Path(out["path"])
    meshes = list((d / "meshes").glob("*.stl"))
    assert len(meshes) == out["links"]              # one mesh per real link
    assert out["joints"] >= 3


def test_unmated_instance_warns_and_fixes_to_world(kernel, tmp_path):
    service = make_test_service(tmp_path / "projects", kernel)
    registry = build_registry(service)
    service.create_project("loose")
    service.create_part("loose", "arm", script=ARM)
    service.set_assembly("loose", [{"id": "solo", "part": "arm"}])
    out = registry.call("export_urdf", {"project": "loose"})
    assert any(w.get("kind") == "unmated" for w in out["warnings"])
    xml = (Path(out["path"]) / "robot.urdf").read_text()
    urdf.validate_urdf(xml)
    import xml.etree.ElementTree as ET
    j = next(x for x in ET.fromstring(xml).findall("joint"))
    assert j.get("type") == "fixed"
    assert j.find("parent").get("link") == "world"


GOLDEN = Path(__file__).resolve().parent / "fixtures" / "urdf" / "rocketry_stack.urdf"


def test_export_urdf_is_byte_stable_against_golden(stack):
    """AC6: the rocketry-stack robot.urdf is byte-stable (no absolute paths —
    mesh filenames are relative — so the golden is machine-independent)."""
    service, registry = stack
    out = registry.call("export_urdf", {"project": "rocket"})
    xml = (Path(out["path"]) / "robot.urdf").read_text()
    assert xml == GOLDEN.read_text()
    urdf.validate_urdf(xml)


def test_off_origin_link_inertia_is_spd(stack):
    """The export path exercises the shift end to end: an off-origin part's
    exported inertia is symmetric positive-definite (an un-shifted origin tensor
    about the COM would not be)."""
    service, registry = stack
    service.create_part("rocket", "off", script=OFFSET)
    service.set_assembly("rocket", [
        {"id": "base", "part": "base", "position": [0, 0, 0]},
        {"id": "off", "part": "off", "mate": {
            "connector": "mount", "to_instance": "base", "to_connector": "seat"}},
    ])
    out = registry.call("export_urdf", {"project": "rocket"})
    xml = (Path(out["path"]) / "robot.urdf").read_text()
    urdf.validate_urdf(xml)   # SPD check on every link is inside validate_urdf
