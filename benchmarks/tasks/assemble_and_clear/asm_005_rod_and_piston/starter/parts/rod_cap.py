# Copied from examples/engine/parts/rod_cap.py into this project.
"""Connecting-rod cap: the lower big-end half, bolted up into the rod body.

Local frame matches ``rod_body``: the joint face is z = 0, the bore axis is
Y through the origin, the cap hangs below. In the assembly each cap keeps
its rod's pose, dropped 0.05 mm along the rod axis (the modeled joint gap).
The bolt heads seat in spot-faces on the cap's underside — the whole
cap-and-bolt envelope stays inside the crankcase bays through a full crank
revolution (that constraint sized the bays at R64).
"""

from build123d import *

PARAMS = {
    "big_end_bore": {"default": 40.5, "min": 30.5, "max": 48.5, "unit": "mm",
                     "description": "Big-end bore (matches rod_body)"},
    "width": {"default": 16.0, "min": 12.0, "max": 20.0, "unit": "mm",
              "description": "Bearing width (matches rod_body)"},
}


def build(p):
    big_od = p.big_end_bore + 12.0
    bx = p.big_end_bore / 2 + 2.55

    cap = Rot(X=-90) * Cylinder(radius=big_od / 2, height=p.width)
    for sgn in (+1, -1):
        cap += Pos(sgn * bx, 0, -6) * Box(7, p.width - 4, 12)

    cap -= Rot(X=-90) * Cylinder(radius=p.big_end_bore / 2, height=p.width + 2)
    cap -= Pos(0, 0, big_od / 2 + 1) * Box(big_od + 12, p.width + 2,
                                           big_od + 2)
    for sgn in (+1, -1):
        cap -= Pos(sgn * bx, 0, -6) * Cylinder(radius=2.7, height=14)
        cap -= Pos(sgn * bx, 0, -17.0) * Cylinder(radius=5.0, height=10.4)
    return cap
