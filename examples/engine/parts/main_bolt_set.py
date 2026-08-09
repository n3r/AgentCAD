"""Main-cap bolt set: six M8 socket screws, one compound part.

Built in the ENGINE frame (instance at the origin): two screws per main cap
at the block's tapped columns (x = -+28) under each bulkhead (y = -81, 0,
+81), pointing upward — heads seated in the caps' counterbores, shanks
running through the caps into the block. One compound keeps the assembly's
instance count sane while every fastener is really there.
"""

from build123d import *

from agentcad.toolkit import threads

BOLT_X = 28.0
BULKHEAD_YS = (-81.0, 0.0, 81.0)
SEAT_Z = -26.0          # counterbore seat in a 40 mm cap

PARAMS = {
    "length": {"default": 40.0, "min": 30.0, "max": 48.0, "unit": "mm",
               "description": "Screw length under the head"},
    "head_gap": {"default": 0.2, "min": 0.0, "max": 1.0, "unit": "mm",
                 "description": "Axial float of each head above its seat"},
}


def build(p):
    screw = threads.cap_screw("M8-1.25", p.length, simple=True)
    bolts = None
    for yc in BULKHEAD_YS:
        for bx in (-BOLT_X, BOLT_X):
            b = Pos(bx, yc, SEAT_Z + p.head_gap) * Rot(X=180) * screw
            bolts = b if bolts is None else bolts + b
    return bolts
