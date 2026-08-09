"""Head gasket: the 0.8 mm sealing sandwich between deck and head.

Local frame matches the head, sitting just below its base (z = -0.85 to
-0.05, i.e. 0.05 mm clearance to each face — the modeled squeeze gap).
Bore rings, all six head-bolt holes, both dowel holes, and a set of
coolant transfer holes, exactly like the real stamped part.
"""

from build123d import *

BOLT_X = 36.0
BOLT_YS = (-15.5, 15.5)
DOWEL_PTS = ((30.0, 65.0), (-30.0, -65.0))
COOLANT_PTS = ((22.0, -20.0), (-22.0, 20.0), (22.0, 60.0), (-22.0, -60.0))

PARAMS = {
    "bore": {"default": 66.0, "min": 50.0, "max": 78.0, "unit": "mm",
             "description": "Cylinder bore (gasket rings are bore + 1.5)"},
    "thickness": {"default": 0.8, "min": 0.5, "max": 1.5, "unit": "mm",
                  "description": "Compressed gasket thickness"},
}


def build(p):
    g = Pos(0, 0, -0.05 - p.thickness / 2) * Box(100, 150, p.thickness)
    for y in (-40.0, 40.0):
        g -= Pos(0, y, -0.5) * Cylinder(radius=p.bore / 2 + 0.75, height=3)
    for bx in (-BOLT_X, BOLT_X):
        for by in BOLT_YS:
            g -= Pos(bx, by, -0.5) * Cylinder(radius=5.5, height=3)
    for dx, dy in DOWEL_PTS:
        g -= Pos(dx, dy, -0.5) * Cylinder(radius=3.15, height=3)
    for cx, cy in COOLANT_PTS:
        g -= Pos(cx, cy, -0.5) * Cylinder(radius=3.0, height=3)
    return g
