"""Main bearing cap: closes one crankshaft saddle from below.

Local frame: the machined joint face is z = 0 with the cap body hanging
below; the half-bore for the main journal is centered on the origin. Three
instances sit in the block's saddle windows (75.5 wide in a 76 window —
the sides are the register, like a real cap), each held by two M8 socket
screws from below into the tapped holes beside the saddle. Install order:
crank into the saddles first, then caps, then bolts (see the README's
assembly guide).
"""

from build123d import *

BOLT_X = 28.0          # matches the block's tapped cap-bolt columns

PARAMS = {
    "bore_d": {"default": 45.6, "min": 40.0, "max": 52.0, "unit": "mm",
               "description": "Main bearing bore (matches the block line-bore)"},
    "width": {"default": 75.5, "min": 70.0, "max": 80.0, "unit": "mm",
              "description": "Cap width across the saddle window (window is 76)"},
    "height": {"default": 40.0, "min": 32.0, "max": 48.0, "unit": "mm",
               "description": "Cap depth below the joint face"},
    "thickness": {"default": 13.5, "min": 11.0, "max": 15.5, "unit": "mm",
                  "description": "Cap thickness along the crank axis"},
}


def build(p):
    cap = Pos(0, 0, -p.height / 2) * Box(p.width, p.thickness, p.height)
    # half-bore for the journal (the block's line-bore forms the other half)
    cap -= Rot(X=-90) * Cylinder(radius=p.bore_d / 2,
                                 height=p.thickness + 2)
    # bolt through-holes with counterbores for the socket heads
    for bx in (-BOLT_X, BOLT_X):
        cap -= Pos(bx, 0, -p.height / 2) * Cylinder(radius=4.2,
                                                    height=p.height + 2)
        cap -= Pos(bx, 0, -p.height + 6) * Cylinder(radius=7.0, height=16.4)
    return cap
