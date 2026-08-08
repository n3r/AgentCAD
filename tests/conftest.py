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


@pytest.fixture(scope="session")
def kernel():
    client = KernelClient()
    client.start()
    yield client
    client.stop()
