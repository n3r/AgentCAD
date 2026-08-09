"""Timing-cover bolt set: eight M5 socket screws, one compound.

Built in the ENGINE frame: heads on the cover's cast bosses, shanks
rearward through the cover into the block's tapped front pattern (the
shared FRONT/BOSS point list).
"""

from build123d import *

from agentcad.toolkit import threads

BOSS_PTS = ((-68, -40), (0, -44), (68, -40), (68, 0), (-68, 0),
            (-52, 52), (-26, 90), (24, 90))
SEAT_Y = -101.4         # cover boss faces

PARAMS = {
    "length": {"default": 22.0, "min": 16.0, "max": 26.0, "unit": "mm",
               "description": "Screw length under the head"},
    "head_gap": {"default": 0.1, "min": 0.0, "max": 1.0, "unit": "mm",
                 "description": "Axial float off the boss seat"},
}


def build(p):
    screw = threads.cap_screw("M5-0.8", p.length, simple=True)
    bolts = None
    for bx, bz in BOSS_PTS:
        b = Pos(bx, SEAT_Y - p.head_gap, bz) * Rot(X=90) * screw
        bolts = b if bolts is None else bolts + b
    return bolts
