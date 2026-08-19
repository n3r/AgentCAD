"""Reference solution for bench task fix_the_broken_part/fix_005_invalid_shell.

A thin-wall swept coolant elbow: an annular section (Ø24 outside, 3 mm wall)
swept along a right-angle centre line with a filleted bend.

The script is the same on both sides of this task; the defect is the stored
``bend_r``. A swept shell self-intersects whenever the centre-line bend radius
is smaller than the swept section's outer radius: the inside of the bend folds
through itself, OCCT still hands back a shape, and ``shape.is_valid`` is
``False``. At the shipped ``bend_r = 6`` against a 12 mm outer radius that is
exactly what happens; at 24 mm (one tube diameter) the sweep is clean.

The rubric lives beside this file, in ``specs/parts/coolant_elbow.py``, and is
injected into every candidate — this script deliberately declares **no**
``SPECS`` of its own (design §1, consequence 3).
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
