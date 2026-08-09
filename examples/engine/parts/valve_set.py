"""Valve set for one head: eight valves, springs, and retainers, one compound.

Local frame matches the head. Each valve's lift is computed from the SAME
phase table and display cam angle as ``camshaft`` (constants duplicated
there — change together): a lobe pointing near its valve holds that valve
open, spring compressed, with a 0.3 mm running gap to the lobe surface.
The set is a consistent mid-cycle snapshot of the valvetrain at the
assembly's 20-degree crank angle.
"""

import math

from build123d import *

CYL_YS = (-40.0, 40.0)
VALVE_OFF = 17.0
CAM_Z = 62.0
BASE_R = 11.0
CAM_ANGLE = 10.0
PHASES = {(-40.0, -1): 200.0, (-40.0, +1): 340.0,
          (40.0, -1): 20.0, (40.0, +1): 160.0}
TIP_Z = 47.2               # stem tip, valve closed (rockers ride above)
SEAT_TOP = 11.0            # valve-head top face, closed, in the seat recess
POCKET_FLOOR = 34.0        # spring pocket floor in the head


def _drop(lift, phase):
    """Lift of a valve whose lobe sits at `phase` (180 = nose straight down)."""
    delta = math.radians((phase + CAM_ANGLE - 180.0) % 360.0)
    reach = BASE_R + lift * max(0.0, math.cos(delta)) ** 3
    return 0.4 * max(0.0, reach - BASE_R)   # finger-follower ratio 16/40


PARAMS = {
    "valve_d": {"default": 19.0, "min": 15.0, "max": 21.0, "unit": "mm",
                "description": "Valve head diameter (head seats add 0.4)"},
    "stem_d": {"default": 5.4, "min": 4.5, "max": 5.6, "unit": "mm",
               "description": "Stem diameter (guides are 5.8)"},
    "lift": {"default": 3.5, "min": 2.0, "max": 5.0, "unit": "mm",
             "description": "Full valve lift (must match the camshaft)"},
    "spring_d": {"default": 15.0, "min": 12.0, "max": 17.0, "unit": "mm",
                 "description": "Spring coil mean diameter"},
}


def build(p):
    pieces = []

    def fuse(s):
        pieces.append(s)

    for cy in CYL_YS:
        for sgn in (-1, +1):
            vy = cy + sgn * VALVE_OFF
            drop = _drop(p.lift, PHASES[(cy, sgn)])
            head_top = SEAT_TOP - drop
            tip = TIP_Z - drop

            # valve: head disc, back cone, stem
            v = Pos(0, vy, head_top - 1.25) * Cylinder(radius=p.valve_d / 2,
                                                       height=2.5)
            v += Pos(0, vy, head_top + 1.0) * Cone(
                bottom_radius=p.valve_d / 2 - 1.5, top_radius=p.stem_d / 2,
                height=4.5)
            v += Pos(0, vy, (head_top + tip) / 2) * Cylinder(
                radius=p.stem_d / 2, height=tip - head_top)
            fuse(v)

            # spring: helical sweep from the pocket floor to the retainer;
            # the turn count adapts so compressed coils never self-touch
            r_top = tip - 3.2          # retainer underside
            height = r_top - POCKET_FLOOR
            turns = max(2.0, min(4.0, height / 2.9))
            helix = Helix(pitch=height / turns, height=height,
                          radius=p.spring_d / 2)
            with BuildPart() as sp:
                with BuildLine():
                    add(helix)
                with BuildSketch(Plane(origin=helix @ 0, z_dir=helix % 0)):
                    Circle(1.2)
                sweep()
            fuse(Pos(0, vy, POCKET_FLOOR + 1.2) * sp.part)

            # retainer + keeper cone at the stem top
            fuse(Pos(0, vy, tip - 2.0) * Cylinder(radius=8.0, height=2.4))
            fuse(Pos(0, vy, tip - 1.8) * Cone(bottom_radius=4.5,
                                              top_radius=p.stem_d / 2 + 0.3,
                                              height=1.6))
    # a true compound — these are separate parts riding together, not a fuse
    return Compound(children=pieces)
