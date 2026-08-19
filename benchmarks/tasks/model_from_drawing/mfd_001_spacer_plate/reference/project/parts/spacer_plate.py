"""Reference solution for bench task model_from_drawing/mfd_001_spacer_plate.

The rubric lives beside this file, in ``specs/parts/spacer_plate.py``, and is
injected into every candidate — this script deliberately declares **no**
``SPECS`` of its own, so the reference is measured against exactly what every
other submission is measured against (design §1, consequence 3).
"""
from build123d import *

PARAMS = {
    "length": {"default": 80.0, "min": 20.0, "max": 200.0},
    "width": {"default": 50.0, "min": 20.0, "max": 200.0},
    "thickness": {"default": 6.0, "min": 1.0, "max": 30.0},
    "corner_r": {"default": 5.0, "min": 0.0, "max": 20.0},
    "hole_d": {"default": 6.0, "min": 1.0, "max": 20.0},
    "hole_dx": {"default": 60.0, "min": 10.0, "max": 180.0},
    "hole_dy": {"default": 30.0, "min": 10.0, "max": 180.0},
}


def build(p):
    with BuildPart() as part:
        with BuildSketch() as plate:
            RectangleRounded(p.length, p.width, p.corner_r)
        extrude(amount=p.thickness)
        with Locations(part.faces().sort_by(Axis.Z)[-1]):
            with GridLocations(p.hole_dx, p.hole_dy, 2, 2):
                Hole(radius=p.hole_d / 2)
    return part.part
