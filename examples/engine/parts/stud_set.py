"""Manifold stud set for one head: sixteen fully-threaded M8 studs.

Local frame matches the head. Real ISO thread geometry over the whole
length — these are the studs the intake and exhaust flanges slide over and
the flange nuts wind onto. Eight per side, screwed into the head's tapped
Ø8.4 holes: exhaust studs reach through the manifold flange into its nut,
intake likewise on the valley side. A separate part (a Compound — studs
are hardware, not casting features), two instances, one per head.
"""

from build123d import *

from agentcad.toolkit import threads

BORE_PITCH = 80.0
STUD_PITCH = 17.0
PORT_Z = 21.0

PARAMS = {
    "exhaust_len": {"default": 30.0, "min": 24.0, "max": 34.0, "unit": "mm",
                    "description": "Exhaust-side stud length (from x = +51)"},
    "intake_len": {"default": 26.0, "min": 20.0, "max": 30.0, "unit": "mm",
                   "description": "Intake-side stud length (from x = -51)"},
}


def _rod(length, simple):
    if simple:
        return Pos(0, 0, length / 2) * Cylinder(radius=4.0, height=length)
    return threads.threaded_rod(8.0, 1.25, length)


def _build(p, simple):
    rod_e = _rod(p.exhaust_len, simple)
    rod_i = _rod(p.intake_len, simple)
    studs = []
    for y in (-BORE_PITCH / 2, BORE_PITCH / 2):
        for sy in (-STUD_PITCH, STUD_PITCH):
            for sz in (-STUD_PITCH, STUD_PITCH):
                studs.append(Pos(51, y + sy, PORT_Z + sz) * Rot(Y=90) * rod_e)
                studs.append(Pos(-51, y + sy, PORT_Z + sz) * Rot(Y=-90) * rod_i)
    return Compound(children=studs)


def build(p):
    return _build(p, simple=False)


def analysis(p):
    """Conservative envelope for interference checking: cosmetic threads at
    nominal diameter strictly contain the real thread geometry."""
    return _build(p, simple=True)
