"""Oil pan: an open-top tub with a bolting flange and a drain boss.

Local frame: the flange (rail) face is z = 0 with the tub hanging below.
In the assembly it is rigid-mated to the block's ``pan_rail`` connector,
0.4 mm under the block's rail (the gasket allowance). Its length is capped
below the block's so the flange clears the flywheel behind the rear face.
"""

from build123d import *

from agentcad.toolkit import safe_fillet

FLANGE_W = 12.0    # flange overhang per side
FLANGE_T = 6.0

PARAMS = {
    "length": {"default": 160.0, "min": 140.0, "max": 200.0, "unit": "mm",
               "description": "Tub length along the crank axis"},
    "width": {"default": 150.0, "min": 120.0, "max": 170.0, "unit": "mm",
              "description": "Tub width across the engine"},
    "depth": {"default": 45.0, "min": 30.0, "max": 60.0, "unit": "mm",
              "description": "Tub depth below the rail face"},
    "wall": {"default": 3.0, "min": 2.0, "max": 5.0, "unit": "mm",
             "description": "Stamped wall thickness"},
}


def build(p):
    pan = Pos(0, 0, -FLANGE_T / 2) * Box(
        p.width + 2 * FLANGE_W, p.length + 2 * FLANGE_W, FLANGE_T)
    pan += Pos(0, 0, -p.depth / 2) * Box(p.width, p.length, p.depth)

    # drain boss on the floor, then hollow the tub through the flange opening
    pan += Pos(20, -30, -p.depth - 2) * Cylinder(radius=7, height=4)
    inner = Pos(0, 0, (-p.depth + p.wall + 1) / 2) * Box(
        p.width - 2 * p.wall, p.length - 2 * p.wall, p.depth - p.wall + 1)
    pan -= inner
    pan -= Pos(20, -30, -p.depth - 3) * Cylinder(radius=2.5, height=8)

    # round every vertical corner (tub, flange, and interior)
    pan, _r, _warn = safe_fillet(pan, pan.edges().filter_by(Axis.Z),
                                 radius=8.0)
    return pan


def connectors(p, part):
    """Rail-face center: rigid-mate this to the block's ``pan_rail``."""
    return {"rail": {"type": "rigid", "location": ((0, 0, 0), (0, 0, 0))}}
