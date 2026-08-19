# Copied from examples/engine/parts/rod_bolt_pair.py into this project.
"""Rod-bolt pair: two M6 socket screws clamping one rod cap to its body.

Local frame matches ``rod_body``/``rod_cap``: screws point up (+Z), heads
seated in the cap's underside spot-faces at z = -12, shanks passing the
split plane into the body's tapped bosses. One instance per rod, posed with
the rod's own transform.
"""

from build123d import *

from agentcad.toolkit import threads

PARAMS = {
    "length": {"default": 22.0, "min": 16.0, "max": 28.0, "unit": "mm",
               "description": "Screw length under the head"},
    "spacing": {"default": 22.8, "min": 17.8, "max": 26.8, "unit": "mm",
                "description": "Bolt column offset from the bore axis"},
}


def _build(p, simple):
    screw = threads.cap_screw("M5-0.8", p.length, simple=simple)
    pair = [Pos(sgn * p.spacing, 0, -12.05) * Rot(X=180) * screw
            for sgn in (+1, -1)]
    return Compound(children=pair)


def build(p):
    return _build(p, simple=False)


def analysis(p):
    """Conservative envelope for interference checking: cosmetic threads at
    nominal diameter strictly contain the real thread geometry."""
    return _build(p, simple=True)
