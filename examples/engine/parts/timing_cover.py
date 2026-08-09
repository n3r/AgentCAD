"""Timing cover: front case plate with water pump and crank seal boss.

Built in the ENGINE frame (instance at the origin): a plate following the
block's front silhouette — the crankcase circle trimmed at the pan rail,
rising in a trapezoid over the valley where the (imagined) cam chain runs.
It floats 0.4 mm off the block's front face (the gasket), carries a ring of
cast bolt bosses, a crank-snout seal boss with its clearance bore, and the
water pump volute with its pulley — placed up-and-right so the pump and
crank pulleys are belt-coplanar yet radially clear of the damper.
"""

from build123d import *

BLOCK_FACE_Y = -88.0
GASKET = 0.4
CASE_R = 76.0          # block crankcase barrel radius
RAIL_Z = -50.0         # profile floor (just above the pan rail)
PUMP_XZ = (52.0, 62.0)
BOSS_PTS = ((-68, -40), (0, -44), (68, -40), (68, 0), (-68, 0),
            (-52, 52), (-26, 90), (24, 90))

PARAMS = {
    "thickness": {"default": 10.0, "min": 8.0, "max": 14.0, "unit": "mm",
                  "description": "Cover plate thickness"},
    "pump_d": {"default": 60.0, "min": 48.0, "max": 66.0, "unit": "mm",
               "description": "Water pump volute diameter"},
    "snout_hole": {"default": 34.0, "min": 30.0, "max": 40.0, "unit": "mm",
                   "description": "Crank snout clearance bore (snout is 28)"},
    "boss_d": {"default": 56.0, "min": 46.0, "max": 64.0, "unit": "mm",
               "description": "Crank seal boss diameter"},
}


def build(p):
    y0 = BLOCK_FACE_Y - GASKET         # rear (gasket) face
    t = p.thickness

    # silhouette: crankcase circle + valley trapezoid, floored at the rail.
    # Plane.XZ sketches extrude along -Y, i.e. away from the block: correct.
    with BuildSketch(Plane.XZ) as sk:
        Circle(CASE_R)
        Polygon((-CASE_R, 10), (-42, 105), (42, 105), (CASE_R, 10),
                align=None)
    cover = Pos(0, y0, 0) * extrude(sk.sketch, amount=t)
    cover -= Pos(0, y0 - t / 2, RAIL_Z - 100) * Box(400, t + 40, 200)

    # cast bolt bosses proud of the front face, each with its bolt bore
    for bx, bz in BOSS_PTS:
        cover += Pos(bx, y0 - t - 1, bz) * Rot(X=-90) * Cylinder(radius=6,
                                                                 height=4)
        cover -= Pos(bx, y0 - t / 2, bz) * Rot(X=-90) * Cylinder(
            radius=2.8, height=t + 12)

    # crank seal boss + snout clearance bore
    cover += Pos(0, y0 - t - 2, 0) * Rot(X=-90) * Cylinder(
        radius=p.boss_d / 2, height=6)
    cover -= Pos(0, y0 - t / 2, 0) * Rot(X=-90) * Cylinder(
        radius=p.snout_hole / 2, height=t + 30)

    # water pump volute, pulley, and nose
    px, pz = PUMP_XZ
    cover += Pos(px, y0 - t - 4, pz) * Rot(X=-90) * Cylinder(
        radius=p.pump_d / 2, height=10)
    cover += Pos(px, y0 - t - 11, pz) * Rot(X=-90) * Cylinder(
        radius=28.0, height=6)
    cover += Pos(px, y0 - t - 16, pz) * Rot(X=-90) * Cylinder(
        radius=9.0, height=8)
    return cover
