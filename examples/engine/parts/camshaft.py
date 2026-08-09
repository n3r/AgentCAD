"""SOHC camshaft for one head: journals, eight lobes, drive-nose stub.

Local frame matches the head: axis along Y at (x = 0, z = 62), journals at
the head's three saddles. The lobes are BAKED at their firing phases turned
by the display cam angle (crank 20 deg -> cam 10 deg), so the shaft is a
mid-cycle snapshot; ``valve_set`` computes each valve's lift from the same
phase table, keeping lobe and valve consistent (shared constants below —
change them in both scripts together).

Assembly: the cam drops into the head's open saddles and ``cam_cap_set``
bolts over the journals — a one-piece cam with lobes larger than any
closed tunnel could never be installed.
"""

import math

from build123d import *

CAM_Z = 62.0
SADDLE_YS = (-31.0, 0.0, 31.0)
CYL_YS = (-40.0, 40.0)
VALVE_OFF = 17.0
BASE_R = 11.0              # lobe base-circle radius
NOSE_R = 4.0
CAM_ANGLE = 10.0           # display angle = crank 20 deg / 2
# lobe phase (deg) per (cylinder, -+valve): nose direction at cam angle 0,
# measured from straight up; 180 = pressing its valve fully open
PHASES = {(-40.0, -1): 200.0, (-40.0, +1): 340.0,
          (40.0, -1): 20.0, (40.0, +1): 160.0}

PARAMS = {
    "journal_d": {"default": 22.0, "min": 20.0, "max": 22.1, "unit": "mm",
                  "description": "Bearing journal diameter (saddle bore is 22.2)"},
    "shaft_d": {"default": 18.0, "min": 14.0, "max": 21.0, "unit": "mm",
                "description": "Shaft diameter between lobes"},
    "lift": {"default": 3.5, "min": 2.0, "max": 5.0, "unit": "mm",
             "description": "Valve lift (nose height over the base circle)"},
    "lobe_w": {"default": 7.0, "min": 5.0, "max": 8.0, "unit": "mm",
               "description": "Lobe width"},
}


def _lobe(p):
    """Base circle + nose circle joined by tangent hull, extruded."""
    with BuildPart() as lp:
        with BuildSketch():
            with Locations((0, 0)):
                Circle(BASE_R)
            with Locations((0, BASE_R + p.lift - NOSE_R)):
                Circle(NOSE_R)
            make_hull()
        extrude(amount=p.lobe_w / 2, both=True)
    return lp.part


def build(p):
    shaft = Rot(X=-90) * Cylinder(radius=p.shaft_d / 2, height=128)
    for jy in SADDLE_YS:
        shaft += Pos(0, jy, 0) * Rot(X=-90) * Cylinder(
            radius=p.journal_d / 2, height=8)

    # Rot(X=90) lays the lobe axis along Y with the nose at +Z (phase 0);
    # Rot(Y=phase) then swings the nose about the cam axis — phase 180
    # points it straight down at its valve.
    lobe = _lobe(p)
    for cy in CYL_YS:
        for sgn in (-1, +1):
            phase = PHASES[(cy, sgn)] + CAM_ANGLE
            placed = Rot(Y=phase) * Rot(X=90) * lobe
            shaft += Pos(0, cy + sgn * VALVE_OFF, 0) * placed
    return Pos(24, 0, CAM_Z) * shaft
