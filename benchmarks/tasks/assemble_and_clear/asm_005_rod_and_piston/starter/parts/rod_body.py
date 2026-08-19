# Copied from examples/engine/parts/rod_body.py for bench task
# assemble_and_clear/asm_005_rod_and_piston.
# A derived task copies the script INTO the bundle: the runner registers no
# examples, so a run can never read the answer.
# The rubric is injected from the bundle's specs/, so this script declares no
# SPECS of its own.
"""Connecting-rod body: small end, I-beam blade, and the UPPER big-end half.

A one-piece rod can never be installed on a one-piece crankshaft — the big
end must split. This body carries the upper bearing half and two bolt
bosses tapped from the joint face (z = 0 in the local frame, the big-end
bore axis along Y through the origin); ``rod_cap`` closes the bore from
below with ``rod_bolt_pair`` screws. The small end takes the floating
``wrist_pin`` with running clearance.
"""

from build123d import *

BLADE_W = 14.0
BLADE_T = 10.0
POCKET_D = 2.0

PARAMS = {
    "length": {"default": 110.0, "min": 90.0, "max": 130.0, "unit": "mm",
               "description": "Center-to-center length (big end to small end)"},
    "big_end_bore": {"default": 40.5, "min": 30.5, "max": 48.5, "unit": "mm",
                     "description": "Big-end bore (crank pin diameter + 0.5 clearance)"},
    "small_end_bore": {"default": 18.25, "min": 14.2, "max": 22.4, "unit": "mm",
                       "description": "Small-end bore (wrist pin diameter + 0.25)"},
    "width": {"default": 16.0, "min": 12.0, "max": 20.0, "unit": "mm",
              "description": "Bearing width of both ends along the bore axis"},
}


def build(p):
    big_od = p.big_end_bore + 12.0
    small_od = p.small_end_bore + 7.4

    def ring(od, z_off):
        return Pos(0, 0, z_off) * Rot(X=-90) * Cylinder(radius=od / 2,
                                                        height=p.width)

    rod = ring(big_od, 0)
    rod += ring(small_od, p.length)

    # blade, overlapping both eyes so the fuse is always solid
    z0 = big_od / 2 - 2.0
    z1 = p.length - small_od / 2 + 2.0
    rod += Pos(0, 0, (z0 + z1) / 2) * Box(BLADE_W, BLADE_T, z1 - z0)
    span = z1 - z0 - 8.0
    if span > 4.0:
        for sgn in (+1, -1):
            rod -= Pos(0, sgn * (BLADE_T / 2 - POCKET_D / 2 + 1),
                       (z0 + z1) / 2) * Box(BLADE_W - 4.0, POCKET_D + 2, span)

    # bolt bosses on the joint plane, tapped for the cap screws (M6: the
    # hole is nominal + 0.4 for the cosmetic thread convention)
    bx = p.big_end_bore / 2 + 2.55
    for sgn in (+1, -1):
        rod += Pos(sgn * bx, 0, 6) * Box(7, p.width - 4, 12)

    # big-end bore, then discard everything below the split plane
    rod -= Rot(X=-90) * Cylinder(radius=p.big_end_bore / 2, height=p.width + 2)
    rod -= Pos(0, 0, -big_od / 2 - 1) * Box(big_od + 12, p.width + 2,
                                            big_od + 2)
    # small-end bore and the tapped bolt holes
    rod -= Pos(0, 0, p.length) * Rot(X=-90) * Cylinder(
        radius=p.small_end_bore / 2, height=p.width + 2)
    for sgn in (+1, -1):
        rod -= Pos(sgn * bx, 0, 5) * Cylinder(radius=2.7, height=11)
    return rod
