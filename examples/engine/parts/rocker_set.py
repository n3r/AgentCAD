"""Rocker gear for one head: shaft + eight finger followers, one compound.

The SOHC cam is OFFSET over the exhaust side (x = +24) — a cam directly
above the plugs is geometrically impossible, the tubes would pass through
it — so finger-follower rockers carry its motion back to the vertical
valves: pivot boss on the rocker shaft at x = -16 (head pedestals), beam
under the lobe at x = +24, valve tip contact at x = 0 (ratio 0.4).

Each beam is tilted per its own lobe's phase — the same table the camshaft
and valve_set bake — so the whole train is one consistent mid-cycle
snapshot: lobe presses beam presses valve, 0.2 mm running gaps throughout.
"""

import math

from build123d import *

CYL_YS = (-40.0, 40.0)
VALVE_OFF = 17.0
CAM_X, CAM_Z = 24.0, 62.0
SHAFT_X, SHAFT_Z = -16.0, 54.0
BASE_R = 11.0
CAM_ANGLE = 10.0
PHASES = {(-40.0, -1): 200.0, (-40.0, +1): 340.0,
          (40.0, -1): 20.0, (40.0, +1): 160.0}

PARAMS = {
    "shaft_d": {"default": 10.0, "min": 8.0, "max": 11.0, "unit": "mm",
                "description": "Rocker shaft diameter (pedestal bores are 10.4)"},
    "beam_w": {"default": 6.0, "min": 5.0, "max": 7.0, "unit": "mm",
               "description": "Follower beam width"},
    "lift": {"default": 3.5, "min": 2.0, "max": 5.0, "unit": "mm",
             "description": "Cam lift (must match the camshaft)"},
    "boss_d": {"default": 14.0, "min": 12.0, "max": 15.0, "unit": "mm",
               "description": "Pivot boss diameter"},
}


def _r_down(lift, phase):
    delta = math.radians((phase + CAM_ANGLE - 180.0) % 360.0)
    return BASE_R + lift * max(0.0, math.cos(delta)) ** 3


def build(p):
    parts = Pos(SHAFT_X, 0, SHAFT_Z) * Rot(X=-90) * Cylinder(
        radius=p.shaft_d / 2, height=128)

    for cy in CYL_YS:
        for sgn in (-1, +1):
            vy = cy + sgn * VALVE_OFF
            r_down = _r_down(p.lift, PHASES[(cy, sgn)])
            zb = CAM_Z - r_down - 1.6 - 2.5      # beam midline at the cam end
            ang = math.degrees(math.atan2(SHAFT_Z - zb, CAM_X - SHAFT_X))
            mid = ((SHAFT_X + 25.0) / 2, (SHAFT_Z + zb) / 2)

            boss = Pos(SHAFT_X, vy, SHAFT_Z) * Rot(X=-90) * Cylinder(
                radius=p.boss_d / 2, height=p.beam_w + 1)
            boss -= Pos(SHAFT_X, vy, SHAFT_Z) * Rot(X=-90) * Cylinder(
                radius=p.shaft_d / 2 + 0.2, height=p.beam_w + 3)
            beam = Pos(mid[0], vy, mid[1]) * Rot(Y=ang) * Box(
                41, p.beam_w, 5)
            parts += boss + beam
    return parts
