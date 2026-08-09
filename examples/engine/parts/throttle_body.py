"""Throttle body: a separate casting bolted to the intake plenum's flange.

Built in the ENGINE frame. The spigot registers in the plenum's front bore
(0.2 mm radial), the flange floats 0.5 mm off the plenum flange face, and
four M6 screws (included in the compound) reach into the plenum's tapped
bolt circle. Inside the bore: a butterfly plate on a through-shaft with an
external lever — the one moving part a throttle needs.
"""

import math

from build123d import *

from agentcad.toolkit import threads

PLENUM_H = 192.0        # intake plenum axis height (intake default)
PLENUM_L = 140.0
TB_BOLT_BC = 33.0
FACE_Y = -76.5          # plenum flange face - 0.5 gasket float

PARAMS = {
    "bore": {"default": 38.0, "min": 32.0, "max": 44.0, "unit": "mm",
             "description": "Throttle bore diameter"},
    "body_l": {"default": 28.0, "min": 22.0, "max": 36.0, "unit": "mm",
               "description": "Body length ahead of the flange"},
    "flange_d": {"default": 82.0, "min": 74.0, "max": 88.0, "unit": "mm",
                 "description": "Mounting flange diameter"},
    "butterfly_deg": {"default": 25.0, "min": 0.0, "max": 80.0, "unit": "deg",
                      "description": "Throttle plate opening angle"},
}


def build(p):
    zc = PLENUM_H

    def disc(radius, y0, y1):
        return Pos(0, (y0 + y1) / 2, zc) * Rot(X=-90) * Cylinder(
            radius=radius, height=abs(y1 - y0))

    tb = disc(p.flange_d / 2, FACE_Y, FACE_Y - 8)                # flange
    tb += disc(16.8, FACE_Y + 10, FACE_Y)                        # spigot
    tb += disc(p.bore / 2 + 5, FACE_Y - 8, FACE_Y - 8 - p.body_l)
    inlet = FACE_Y - 8 - p.body_l
    tb += disc(p.bore / 2 + 9, inlet, inlet - 6)                 # trumpet lip
    tb -= disc(p.bore / 2, FACE_Y + 12, inlet - 8)               # the bore

    # butterfly on its shaft, opened butterfly_deg about X
    mid = FACE_Y - 8 - p.body_l / 2
    plate = Rot(X=-90) * Cylinder(radius=p.bore / 2 - 0.3, height=2)
    tb += Pos(0, mid, zc) * Rot(X=p.butterfly_deg) * plate
    tb += Pos(0, mid, zc) * Rot(Y=90) * Cylinder(radius=4,
                                                 height=p.bore + 22)
    # throttle lever on the +X shaft end
    tb += Pos(p.bore / 2 + 12, mid, zc + 9) * Box(6, 4, 26)

    # flange holes + the four M6 screws into the plenum's tapped circle
    screw = threads.cap_screw("M6-1", 16.0, simple=True)
    for k in range(4):
        a = math.radians(45 + k * 90)
        hx = TB_BOLT_BC * math.cos(a)
        hz = zc + TB_BOLT_BC * math.sin(a)
        tb -= Pos(hx, FACE_Y - 4, hz) * Rot(X=-90) * Cylinder(
            radius=3.2, height=10)
        tb += Pos(hx, FACE_Y - 8.1, hz) * Rot(X=90) * screw
    return tb
