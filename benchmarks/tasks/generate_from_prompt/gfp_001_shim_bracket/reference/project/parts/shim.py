"""Reference solution for bench task generate_from_prompt/gfp_001_shim_bracket.

The rubric lives beside this file, in ``specs/parts/shim.py``, and is injected
into every candidate — this script deliberately declares **no** ``SPECS`` of its
own, so the reference is measured against exactly what every other submission is
measured against (design §1, consequence 3).
"""
from build123d import *

PARAMS = {
    "length": {"default": 60.0, "min": 20.0, "max": 200.0},
    "width": {"default": 24.0, "min": 10.0, "max": 120.0},
    "thickness": {"default": 4.0, "min": 1.0, "max": 30.0},
    "corner_r": {"default": 3.0, "min": 0.0, "max": 15.0},
    "bore_d": {"default": 8.0, "min": 1.0, "max": 40.0},
}


def build(p):
    with BuildPart() as part:
        with BuildSketch() as base:
            RectangleRounded(p.length, p.width, p.corner_r)
        extrude(amount=p.thickness)
        with Locations(part.faces().sort_by(Axis.Z)[-1]):
            Hole(radius=p.bore_d / 2)
    return part.part
