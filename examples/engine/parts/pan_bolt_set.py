"""Oil-pan bolt set: eight M6 socket screws up through the pan flange.

Built in the ENGINE frame: heads under the pan flange, shanks up through
its drilled holes into the block's tapped pan rail (shared pattern
x = -+72, y = -70/-25/+25/+70).
"""

from build123d import *

from agentcad.toolkit import threads

BOLT_X = 80.5
BOLT_YS = (-70.0, -25.0, 25.0, 70.0)
SEAT_Z = -61.5          # pan flange underside

PARAMS = {
    "length": {"default": 16.0, "min": 12.0, "max": 20.0, "unit": "mm",
               "description": "Screw length under the head"},
    "head_gap": {"default": 0.1, "min": 0.0, "max": 1.0, "unit": "mm",
                 "description": "Axial float below the flange seat"},
}


def _build(p, simple):
    screw = threads.cap_screw("M6-1", p.length, simple=simple)
    bolts = [Pos(bx, by, SEAT_Z - p.head_gap) * Rot(X=180) * screw
             for bx in (-BOLT_X, BOLT_X) for by in BOLT_YS]
    return Compound(children=bolts)


def build(p):
    return _build(p, simple=False)


def analysis(p):
    """Conservative envelope for interference checking: cosmetic threads at
    nominal diameter strictly contain the real thread geometry."""
    return _build(p, simple=True)
