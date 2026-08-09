"""Connecting rod: big end, small end, and an I-beam blade between them.

Local frame: the big-end bore axis is Y through the origin; the small end
sits at +Z * length. Both bores run along Y, so an instance rotated about Y
keeps them parallel to the crank axis. Bore diameters are the mating journal
plus a running clearance (crank pin 40 -> 40.5 here; wrist pin 18 -> 18.6),
which is what keeps the assembly interference-free.
"""

from build123d import *

BLADE_W = 14.0     # blade width across X
BLADE_T = 10.0     # blade thickness across Y (recessed from the 16 ends)
POCKET_D = 2.0     # I-beam side pocket depth

PARAMS = {
    "length": {"default": 110.0, "min": 90.0, "max": 130.0, "unit": "mm",
               "description": "Center-to-center length (big end to small end)"},
    "big_end_bore": {"default": 40.5, "min": 30.5, "max": 48.5, "unit": "mm",
                     "description": "Big-end bore (crank pin diameter + 0.5 clearance)"},
    "small_end_bore": {"default": 18.6, "min": 14.6, "max": 22.6, "unit": "mm",
                       "description": "Small-end bore (wrist pin diameter + 0.6 clearance)"},
    "width": {"default": 16.0, "min": 12.0, "max": 20.0, "unit": "mm",
              "description": "Bearing width of both ends along the bore axis"},
}


def build(p):
    big_od = p.big_end_bore + 12.0
    small_od = p.small_end_bore + 7.4

    def ring(od, bore, z_off):
        eye = Rot(X=-90) * Cylinder(radius=od / 2, height=p.width)
        eye -= Rot(X=-90) * Cylinder(radius=bore / 2, height=p.width + 2)
        return Pos(0, 0, z_off) * eye

    rod = ring(big_od, p.big_end_bore, 0)
    rod += ring(small_od, p.small_end_bore, p.length)

    # blade, overlapping both eyes so the fuse is always solid
    z0 = big_od / 2 - 2.0
    z1 = p.length - small_od / 2 + 2.0
    rod += Pos(0, 0, (z0 + z1) / 2) * Box(BLADE_W, BLADE_T, z1 - z0)

    # I-beam pockets milled into both blade faces
    span = z1 - z0 - 8.0
    if span > 4.0:
        for sgn in (+1, -1):
            rod -= Pos(0, sgn * (BLADE_T / 2 - POCKET_D / 2 + 1),
                       (z0 + z1) / 2) * Box(BLADE_W - 4.0, POCKET_D + 2, span)

    # cap split line and rod-bolt bosses. The bosses hug the bore — outside
    # the bearing radius (never onto the crank pin) yet radially tight so the
    # big end's swing envelope stays inside the crankcase bays.
    split = (Rot(X=-90) * Cylinder(radius=big_od / 2 + 1, height=1.2)
             - Rot(X=-90) * Cylinder(radius=big_od / 2 - 1.2, height=1.4))
    rod -= split
    bx = p.big_end_bore / 2 + 2.05
    for sgn in (+1, -1):
        rod += Pos(sgn * bx, 0, -7) * Box(4, p.width - 4, 14)
        rod -= Pos(sgn * bx, 0, -9) * Cylinder(radius=2, height=12)
    return rod
