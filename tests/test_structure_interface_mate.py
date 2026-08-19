"""PRD-013 Assembly v2 — slice 4: interface-MATE geometry.

Slice 2 validated the exported-connector reference and placed a sub-assembly by
its explicit transform. This slice geometrically RESOLVES an interface mate: a
`set_mate` on a sub-assembly instance whose connector names an exported
interface computes the exported connector's world frame (through the resolved
sub-assembly) and mates the whole unit to the anchor exactly as a part connector
mates — so the unit sits at the MATED pose, not its explicit transform, and
`check_interference` sees the mated geometry.
"""

import pytest

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
def top(kernel, tmp_path):
    """`top` instances `src` (a single piston exporting `crown` as `mount`) and
    a plate `base` at the origin; `sub` mates its `mount` interface to
    `base.hinge`."""
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
        {"id": "base", "part": "plate", "position": [0, 0, 0]},
        {"id": "sub", "assembly": {"project": "src"}, "position": [0, 0, 0]},
    ])
    return service, registry


def _pos(a, iid):
    return next(i for i in a["instances"] if i["id"] == iid)["position"]


def _mate(registry):
    result = registry.call("set_mate", {
        "project": "top", "instance": "sub", "connector": "mount",
        "to_instance": "base", "to_connector": "hinge"})
    assert "error" not in result, result
    return result


def test_interface_mate_places_unit_at_mated_pose(top):
    """Without a mate the unit sits at its explicit transform (0,0,0), so the
    piston would be at 0,0,0. Mating `mount`->`base.hinge` puts the piston's
    crown (local +6 in z) onto the plate face at the origin, so the piston
    body sits at z=-6 — a pose only mating produces."""
    service, registry = top
    _mate(registry)
    a = service.get_assembly("top")
    assert _pos(a, "sub/piston") == pytest.approx([0, 0, -6], abs=1e-6)


def test_interface_mate_geometry_reaches_interference(top):
    """The mated geometry (not the explicit transform) is what interference
    sees: placed at z=-6 the piston sits just under the plate. The point is the
    resolved graph is the mated one — `checked` counts base + the mated unit."""
    service, registry = top
    _mate(registry)
    r = service.check_interference("top")
    assert r["checked"] == 2  # base plate + one mated piston member


def test_unmated_subassembly_stays_at_explicit_transform(top):
    """Negation: with no interface mate, the unit keeps its explicit transform
    (proving the mated pose above is the mate's doing, not a coincidence)."""
    service, _registry = top
    a = service.get_assembly("top")
    assert _pos(a, "sub/piston") == pytest.approx([0, 0, 0], abs=1e-6)
