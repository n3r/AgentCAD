import pytest

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


@pytest.fixture(scope="session")
def kernel():
    client = KernelClient()
    client.start()
    yield client
    client.stop()
