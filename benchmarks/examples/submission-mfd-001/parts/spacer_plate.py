"""Spacer plate — modelled from the three-view drawing in the task prompt.

80 x 50 x 6 mm plate, rounded corners, four clearance holes for M6 on a
60 x 30 mm rectangular pattern. Bottom face on Z = 0, centred on the origin,
80 mm along +X (the datum the drawing states).
"""
from build123d import *

PARAMS = {
    "length": {"default": 80.0, "min": 40.0, "max": 200.0, "unit": "mm",
               "description": "Overall length, along +X"},
    "width": {"default": 50.0, "min": 20.0, "max": 200.0, "unit": "mm",
              "description": "Overall width, along +Y"},
    "thickness": {"default": 6.0, "min": 2.0, "max": 25.0, "unit": "mm",
                  "description": "Plate thickness"},
    "corner_r": {"default": 4.0, "min": 0.0, "max": 15.0, "unit": "mm",
                 "description": "Corner round radius"},
    "hole_d": {"default": 6.6, "min": 3.0, "max": 12.0, "unit": "mm",
               "description": "Clearance hole diameter for M6"},
    "hole_pitch_x": {"default": 60.0, "min": 20.0, "max": 180.0, "unit": "mm",
                     "description": "Hole pattern pitch along X"},
    "hole_pitch_y": {"default": 30.0, "min": 10.0, "max": 180.0, "unit": "mm",
                     "description": "Hole pattern pitch along Y"},
}


def build(p):
    with BuildPart() as plate:
        with BuildSketch(Plane.XY) as profile:
            RectangleRounded(p.length, p.width, p.corner_r)
        extrude(amount=p.thickness)
        with Locations(plate.faces().sort_by(Axis.Z)[-1]):
            with GridLocations(p.hole_pitch_x, p.hole_pitch_y, 2, 2):
                Hole(radius=p.hole_d / 2)
    return plate.part
