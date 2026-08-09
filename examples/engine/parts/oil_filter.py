"""Spin-on oil filter: cans onto the block's -X filter boss.

Built in the ENGINE frame (instance at the origin): the block's boss face is
at x = -81 (y = 50, z = -5) and the filter's base plate floats 0.5 mm off it,
canister growing outward along -X with a domed end and a wrench-flat crown.
"""

from build123d import *

from agentcad.toolkit import safe_fillet

BOSS_FACE_X = -86.0
CENTER = (50.0, -18.75)  # (y, z) of the filter axis: centered in the pocket
                         # between the bank-B slab corner and the pan flange

PARAMS = {
    "diameter": {"default": 62.0, "min": 54.0, "max": 68.0, "unit": "mm",
                 "description": "Canister diameter"},
    "length": {"default": 70.0, "min": 55.0, "max": 85.0, "unit": "mm",
               "description": "Canister length off the base plate"},
    "base_d": {"default": 66.0, "min": 58.0, "max": 74.0, "unit": "mm",
               "description": "Base (seal) plate diameter"},
    "dome": {"default": 10.0, "min": 6.0, "max": 14.0, "unit": "mm",
             "description": "End dome corner radius"},
}


def build(p):
    cy, cz = CENTER
    x0 = BOSS_FACE_X - 0.5

    def can(radius, length, x_start):
        return Pos(x_start - length / 2, cy, cz) * Rot(Y=-90) * Cylinder(
            radius=radius, height=length)

    flt = can(p.base_d / 2, 4.0, x0)
    flt += can(p.diameter / 2, p.length, x0 - 4.0)

    # dome the canister end (safe_fillet clamps if the dome can't take it)
    dome_r = min(p.dome, p.diameter / 4 - 2)
    end = [e for e in flt.edges().filter_by(GeomType.CIRCLE)
           if abs(e.radius - p.diameter / 2) < 1e-6]
    end = sorted(end, key=lambda e: e.center().X)[:1]
    if end:
        flt, _r, _warn = safe_fillet(flt, end, radius=dome_r)

    # wrench flats: a 12-sided crown proud of the domed end
    with BuildPart() as crown:
        with BuildSketch(Plane.YZ.offset(x0 - 4.0 - p.length)):
            with Locations((cy, cz)):
                RegularPolygon(radius=p.diameter / 2 - 10, side_count=12)
        extrude(amount=-4)
    flt += crown.part
    return flt
