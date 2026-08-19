"""Coolant elbow -- AS SHIPPED, AND IT IS NOT A VALID SOLID.

A thin-wall swept elbow: an annular section swept along a right-angle
centre line with a filleted bend.
"""
from build123d import *

PARAMS = {
    "tube_d": {"default": 24.0, "min": 10.0, "max": 60.0, "unit": "mm",
               "description": "Tube outside diameter"},
    "wall": {"default": 3.0, "min": 1.0, "max": 8.0, "unit": "mm",
             "description": "Tube wall thickness"},
    "run": {"default": 60.0, "min": 30.0, "max": 150.0, "unit": "mm",
            "description": "Centre-line leg length, inlet and outlet alike"},
    "bend_r": {"default": 24.0, "min": 4.0, "max": 80.0, "unit": "mm",
               "description": "Centre-line bend radius at the corner"},
}


def build(p):
    with BuildPart() as part:
        with BuildLine() as path:
            Polyline((0, 0, 0), (p.run, 0, 0), (p.run, 0, p.run))
            fillet(path.vertices().group_by(Axis.X)[-1].sort_by(Axis.Z)[0:1],
                   radius=p.bend_r)
        with BuildSketch(Plane.YZ):
            Circle(p.tube_d / 2)
            Circle(p.tube_d / 2 - p.wall, mode=Mode.SUBTRACT)
        sweep(path=path.line)
    return part.part
