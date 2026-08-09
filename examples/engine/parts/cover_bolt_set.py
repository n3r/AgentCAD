"""Cam-cover bolt set: six M5 socket screws for one cover, one compound.

Local frame matches the head: heads on the cover's ear bosses (z = 48),
shanks down through the ears into the head's tapped M5 rail.
"""

from build123d import *

from agentcad.toolkit import threads

COVER_BOLT_PTS = ((-40, -47), (40, -47), (-40, 47), (40, 47),
                  (32, -65), (-32, 65))
SEAT_Z = 48.0

PARAMS = {
    "length": {"default": 14.0, "min": 10.0, "max": 18.0, "unit": "mm",
               "description": "Screw length under the head"},
    "head_gap": {"default": 0.1, "min": 0.0, "max": 1.0, "unit": "mm",
                 "description": "Axial float above the ear-boss seat"},
}


def _build(p, simple):
    screw = threads.cap_screw("M5-0.8", p.length, simple=simple)
    bolts = [Pos(cx, cy, SEAT_Z + p.head_gap) * screw
             for cx, cy in COVER_BOLT_PTS]
    return Compound(children=bolts)


def build(p):
    return _build(p, simple=False)


def analysis(p):
    """Conservative envelope for interference checking: cosmetic threads at
    nominal diameter strictly contain the real thread geometry."""
    return _build(p, simple=True)
