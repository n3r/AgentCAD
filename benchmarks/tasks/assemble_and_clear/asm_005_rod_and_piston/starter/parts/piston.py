# Copied from examples/engine/parts/piston.py into this project.
"""Slipper-style piston with an integral (pressed) wrist pin.

Local frame: the wrist-pin axis is Y through the origin; the crown tops out
at +comp_height and the skirt runs down to -skirt. The interior is hollowed
to a fixed ceiling 20 mm above the pin so the rod's small end (which rides
the exposed pin section between the two bosses) always has headroom.

The wrist pin is modeled as part of the piston (a pressed pin): the rod's
small-end bore wraps it with running clearance, so the assembly stays
interference-free with no separate pin part.
"""

from build123d import *

BOSS_GAP = 20.0     # clear span between the pin bosses (rod is 16 wide)
CEILING = 20.0      # interior roof height above the pin axis
WALL = 5.5          # skirt wall thickness (radial)
GROOVE_W = 1.8      # ring groove width
GROOVE_D = 2.5      # ring groove depth

PARAMS = {
    "diameter": {"default": 65.4, "min": 48.0, "max": 78.0, "unit": "mm",
                 "description": "Piston diameter (block bore minus 0.6 running clearance)"},
    "comp_height": {"default": 28.0, "min": 24.0, "max": 34.0, "unit": "mm",
                    "description": "Compression height: pin center to crown top"},
    "pin_d": {"default": 18.0, "min": 14.0, "max": 22.0, "unit": "mm",
              "description": "Wrist pin diameter (rod small-end bore minus clearance)"},
    "skirt": {"default": 22.0, "min": 16.0, "max": 28.0, "unit": "mm",
              "description": "Skirt length below the pin center"},
}


def build(p):
    outer_r = p.diameter / 2.0
    inner_r = outer_r - WALL
    ceiling = min(CEILING, p.comp_height - 4.0)  # keep >= 4 mm of crown
    pin_half = inner_r - 2.0                     # pin ends embed in the bosses
    boss_r = p.pin_d / 2.0 + 7.0

    # body, hollowed from the open bottom up to the ceiling
    piston = Pos(0, 0, (p.comp_height - p.skirt) / 2) * Cylinder(
        radius=outer_r, height=p.comp_height + p.skirt)
    piston -= Pos(0, 0, (ceiling - p.skirt - 2) / 2) * Cylinder(
        radius=inner_r, height=ceiling + p.skirt + 2)

    # pin bosses flanking the rod gap, bored for the floating wrist pin
    # (a separate part — an integral pin could never accept the rod's
    # one-piece small end)
    boss_len = pin_half - BOSS_GAP / 2
    for sgn in (+1, -1):
        piston += Pos(0, sgn * (BOSS_GAP / 2 + boss_len / 2), 0) * Rot(
            X=-90) * Cylinder(radius=boss_r, height=boss_len)
    piston -= Rot(X=-90) * Cylinder(radius=p.pin_d / 2 + 0.1,
                                    height=2 * pin_half + 2)

    # two compression-ring grooves and a wider oil-ring groove below them
    for drop, gw in ((8.0, GROOVE_W), (12.0, GROOVE_W), (16.0, 2.8)):
        ring = (Cylinder(radius=outer_r + 0.5, height=gw)
                - Cylinder(radius=outer_r - GROOVE_D, height=gw + 1))
        piston -= Pos(0, 0, p.comp_height - drop) * ring

    # shallow valve-relief pockets in the crown, tilted with the valves
    for sgn in (+1, -1):
        piston -= Pos(sgn * 13, 0, p.comp_height + 0.5) * Rot(
            Y=-sgn * 12) * Cylinder(radius=11, height=4)

    # break the crown edge
    crown = piston.edges().filter_by(GeomType.CIRCLE).group_by(Axis.Z)[-1]
    piston = chamfer(crown, length=0.8)
    return piston
