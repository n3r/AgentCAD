"""Exhaust flange nuts: eight M8 hex nuts for one bank, one compound.

Built in the ENGINE frame for bank A (+X side); the second instance serves
bank B by the same 180-degree turn about Z the exhaust manifold uses.
Seated 0.1 mm off the manifold's flange plates, over the heads' studs.
"""

import math

from build123d import *

C45 = math.cos(math.radians(45.0))
SEAT_S = 168.9
HEAD_W = 100.0
PORT_Z = 21.0
BOSS_LEN = 18.0
STUD_PITCH = 17.0
FLANGE_T = 8.0
PORT_YS = (-49.0, 31.0)

PARAMS = {
    "across_corners": {"default": 14.4, "min": 12.0, "max": 16.0, "unit": "mm",
                       "description": "Nut across-corners size (M8 is 14.4)"},
    "height": {"default": 6.5, "min": 5.0, "max": 8.0, "unit": "mm",
               "description": "Nut height"},
}


def _nut(p):
    with BuildPart() as n:
        with BuildSketch():
            RegularPolygon(radius=p.across_corners / 2, side_count=6)
        extrude(amount=p.height)
    return n.part - Cylinder(radius=4.2, height=3 * p.height)


def build(p):
    tip_x = (SEAT_S + (HEAD_W / 2 + BOSS_LEN) + PORT_Z) * C45
    tip_z = (SEAT_S - (HEAD_W / 2 + BOSS_LEN) + PORT_Z) * C45
    n = (C45, -C45)
    d = (C45, C45)
    seat = 0.5 + FLANGE_T + 0.1

    nut = _nut(p)
    nuts = None
    for ry in PORT_YS:
        for oy in (-STUD_PITCH, STUD_PITCH):
            for od in (-STUD_PITCH, STUD_PITCH):
                px = tip_x + seat * n[0] + od * d[0]
                pz = tip_z + seat * n[1] + od * d[1]
                m = Pos(px, ry + oy, pz) * Rot(Y=135) * nut
                nuts = m if nuts is None else nuts + m
    return nuts
