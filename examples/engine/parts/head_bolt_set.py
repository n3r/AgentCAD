"""Head-bolt set: six M10 socket screws for one head, one compound.

Local frame matches the head: heads seated in the head's spot-face wells
(z = 36), shanks down through the head and gasket into the block's tapped
deck bosses — the pattern all three parts share (x = -+36, y = -55/0/+55).
"""

from build123d import *

from agentcad.toolkit import threads

BOLT_X = 36.0
BOLT_YS = (-15.5, 15.5)
SEAT_Z = 36.0

PARAMS = {
    "length": {"default": 55.0, "min": 45.0, "max": 62.0, "unit": "mm",
               "description": "Screw length under the head"},
    "head_gap": {"default": 0.1, "min": 0.0, "max": 1.0, "unit": "mm",
                 "description": "Axial float above the spot-face seat"},
}


def _build(p, simple):
    screw = threads.cap_screw("M10-1.5", p.length, simple=simple)
    bolts = [Pos(bx, by, SEAT_Z + p.head_gap) * screw
             for bx in (-BOLT_X, BOLT_X) for by in BOLT_YS]
    return Compound(children=bolts)


def build(p):
    return _build(p, simple=False)


def analysis(p):
    """Conservative envelope for interference checking: cosmetic threads at
    nominal diameter strictly contain the real thread geometry."""
    return _build(p, simple=True)
