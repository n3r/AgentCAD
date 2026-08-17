import shutil

import pytest

from agentcad.core.service import AgentCADService, EventBus
from agentcad.kernel.client import KernelClient

PLATE_SCRIPT = '''\
from build123d import *

PARAMS = {
    "width":  {"default": 80.0, "min": 10.0, "max": 300.0, "unit": "mm",
               "description": "Plate width"},
    "hole_d": {"default": 12.0, "min": 1.0,  "max": 50.0,  "unit": "mm",
               "description": "Center hole diameter"},
}

def build(p):
    with BuildPart() as part:
        Box(p.width, 60, 8)
        with Locations((0, 0, 4)):
            Cylinder(radius=15, height=20, align=(Align.CENTER, Align.CENTER, Align.MIN))
        Hole(radius=p.hole_d / 2)
        with Locations((30, 20, 0), (-30, 20, 0), (30, -20, 0), (-30, -20, 0)):
            Hole(radius=3)
        fillet(part.edges().filter_by(Axis.Z), radius=2)
    return part.part
'''

BOX_SCRIPT = '''\
from build123d import *

PARAMS = {"size": {"default": 10.0, "min": 1.0, "max": 100.0, "unit": "mm"}}

def build(p):
    with BuildPart() as part:
        Box(p.size, p.size, p.size)
    return part.part
'''

# Numeric enum whose choices are ints and whose build needs a real int
# (range(p.n)): a caller-supplied 3.0 must canonicalize to the declared 3.
NUMERIC_ENUM_SCRIPT = '''\
import build123d as b3d

PARAMS = {
    "n": {"default": 2, "type": "enum", "choices": [2, 3, 4], "description": "hole count"},
}

def build(p):
    part = b3d.Box(24, 12, 6)
    for i in range(p.n):
        hole = b3d.Cylinder(1, 20).moved(b3d.Location((i * 5 - 5, 0, 0)))
        part = part - hole
    return part
'''

# One parameter of every supported type (number/bool/enum/string/int).
TYPED_SCRIPT = '''\
import build123d as b3d

PARAMS = {
    "size": {"default": 20.0, "min": 10.0, "max": 40.0, "unit": "mm", "description": "cube edge"},
    "holes": {"default": True, "type": "bool", "description": "drill the hole"},
    "grade": {"default": "std", "type": "enum", "choices": ["std", "wide"], "description": "width grade"},
    "label": {"default": "acme", "type": "string", "max_len": 10, "description": "engraving text"},
    "n": {"default": 2, "type": "int", "min": 1, "max": 4, "description": "hole count"},
}

def build(p):
    w = p.size * (2.0 if p.grade == "wide" else 1.0)
    part = b3d.Box(w, p.size, p.size)
    if p.holes:
        for i in range(p.n):
            hole = b3d.Cylinder(2, p.size * 2).moved(b3d.Location((i * 4 - 2, 0, 0)))
            part = part - hole
    assert isinstance(p.label, str)
    return part
'''


# A flange-like part: plate with a central bore and a bolt circle. Every
# parameter carries a unit and a description, so it is also the fixture for
# anything that reads a normalized PARAMS spec (PRD-012 configurations).
FLANGE_SCRIPT = '''\
from build123d import *

PARAMS = {
    "outer_d":  {"default": 140.0, "min": 40.0, "max": 400.0, "unit": "mm", "description": "OD"},
    "bore_d":   {"default": 80.0,  "min": 10.0, "max": 300.0, "unit": "mm", "description": "bore"},
    "thick":    {"default": 14.0,  "min": 4.0,  "max": 60.0,  "unit": "mm", "description": "thickness"},
    "n_bolts":  {"default": 8.0,   "min": 3.0,  "max": 16.0,  "unit": "ct", "description": "bolt count"},
    "bolt_d":   {"default": 9.0,   "min": 3.0,  "max": 30.0,  "unit": "mm", "description": "bolt hole dia"},
    "bc_d":     {"default": 118.0, "min": 20.0, "max": 360.0, "unit": "mm", "description": "bolt circle dia"},
}

def build(p):
    with BuildPart() as part:
        Cylinder(radius=p.outer_d / 2, height=p.thick)
        Cylinder(radius=p.bore_d / 2, height=p.thick, mode=Mode.SUBTRACT)
        with PolarLocations(radius=p.bc_d / 2, count=int(p.n_bolts)):
            Hole(radius=p.bolt_d / 2)
    return part.part
'''

# A three-member size family for FLANGE_SCRIPT, in family (insertion) order —
# the configuration map shape PRD-011 froze: {name: {params, label?}}.
THREE_SIZE_CONFIGS = {
    "s": {"params": {"outer_d": 100.0, "bore_d": 50.0, "bc_d": 80.0},
          "label": "Small"},
    "m": {"params": {"outer_d": 140.0, "bore_d": 80.0, "bc_d": 118.0},
          "label": "Medium"},
    "l": {"params": {"outer_d": 200.0, "bore_d": 120.0, "bc_d": 170.0},
          "label": "Large"},
}


def make_test_service(projects_dir, kernel, bus=None):
    """Build a service without synchronous git snapshots for unrelated tests."""
    bus = bus if bus is not None else EventBus()
    service = AgentCADService(projects_dir, kernel, bus)
    bus.on_publish = None
    return service


def clone_test_service(source_projects, dest_projects, kernel, bus=None):
    """Copy a prepared project tree so mutating tests retain isolation."""
    shutil.copytree(source_projects, dest_projects)
    return make_test_service(dest_projects, kernel, bus)


@pytest.fixture(scope="session")
def kernel():
    client = KernelClient()
    client.start()
    yield client
    client.stop()
