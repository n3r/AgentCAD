"""Flywheel bolt set: six M8 socket screws on the crank-flange circle.

Local frame matches the flywheel (friction face z = 0, +Z rearward):
heads seat on the flywheel's relief-recess floor, shanks forward through
its bolt holes into the crank flange's drilled circle. The ``hub``
connector lets the set rigid-mate to the crank's ``flange`` exactly like
the flywheel itself — so the bolts turn with the crank.
"""

import math

from build123d import *

from agentcad.toolkit import threads

BOLT_BC = 56.0
SEAT_Z = 9.0            # counterbore floors (thickness 22 - 13)

PARAMS = {
    "length": {"default": 16.0, "min": 12.0, "max": 20.0, "unit": "mm",
               "description": "Screw length under the head"},
    "head_gap": {"default": 0.1, "min": 0.0, "max": 1.0, "unit": "mm",
                 "description": "Axial float above the recess seat"},
}


def _build(p, simple):
    screw = threads.cap_screw("M8-1.25", p.length, simple=simple)
    bolts = [Pos(BOLT_BC / 2 * math.cos(math.radians(60 * k)),
                 BOLT_BC / 2 * math.sin(math.radians(60 * k)),
                 SEAT_Z + p.head_gap) * screw for k in range(6)]
    return Compound(children=bolts)


def connectors(p, part):
    """Same seat as the flywheel: rigid-mate to the crank's ``flange``."""
    return {"hub": {"type": "rigid", "location": ((0, 0, 0), (0, 0, 0))}}


def build(p):
    return _build(p, simple=False)


def analysis(p):
    """Conservative envelope for interference checking: cosmetic threads at
    nominal diameter strictly contain the real thread geometry."""
    return _build(p, simple=True)
