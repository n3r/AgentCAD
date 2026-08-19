# Copied from examples/prototyping/parts/enclosure_lid.py for bench task
# optimize_under_constraints/opt_003_thinnest_lid. A derived task copies the
# script INTO the bundle: the runner registers no examples, so a run can never
# read the answer, and the starter and the reference are the SAME script at
# different parameters — the task is an optimisation over the parameters, not a
# rewrite. The rubric is injected from ../../../specs/parts/, so this script
# declares no SPECS of its own.
"""Snap-fit electronics enclosure -- lid.

Flat plate matching the base footprint with an inner lip that seats into
the base cavity at LIP_CLEARANCE from the inner walls. The lip is notched
around the base's corner screw bosses; four countersunk holes align with
the boss pilot holes. Optional shallow logo recess on the top face.

length/width/wall/corner_r/boss_d must match the enclosure_base values for
the assembly to seat correctly (defaults do).
"""

from build123d import *

PARAMS = {
    "length":   {"default": 100.0, "min": 60.0, "max": 200.0, "unit": "mm",
                 "description": "Lid length (X) -- match enclosure_base length"},
    "width":    {"default": 60.0,  "min": 40.0, "max": 150.0, "unit": "mm",
                 "description": "Lid width (Y) -- match enclosure_base width"},
    "wall":     {"default": 2.5,   "min": 1.2,  "max": 5.0,   "unit": "mm",
                 "description": "Base wall thickness the lip is offset from -- match enclosure_base wall"},
    "lid_t":    {"default": 3.0,   "min": 2.0,  "max": 6.0,   "unit": "mm",
                 "description": "Lid plate thickness"},
    "lip_h":    {"default": 3.0,   "min": 1.5,  "max": 6.0,   "unit": "mm",
                 "description": "Lip depth below the plate underside"},
    "lip_t":    {"default": 2.0,   "min": 1.0,  "max": 4.0,   "unit": "mm",
                 "description": "Lip wall thickness"},
    "corner_r": {"default": 3.0,   "min": 0.0,  "max": 8.0,   "unit": "mm",
                 "description": "Plate corner fillet radius -- match enclosure_base corner_r"},
    "boss_d":   {"default": 6.0,   "min": 4.0,  "max": 12.0,  "unit": "mm",
                 "description": "Base screw boss diameter (locates screw holes and lip notches)"},
    "screw_d":  {"default": 3.0,   "min": 2.0,  "max": 5.0,   "unit": "mm",
                 "description": "Clearance hole diameter for the lid screws"},
    "emboss":   {"default": 1.0,   "min": 0.0,  "max": 1.0,   "unit": "flag",
                 "description": "Shallow 0.6 mm logo recess on the top face (1 = on, 0 = off)"},
}

LIP_CLEARANCE = 0.15   # radial gap between lip and base inner wall
EMBOSS_DEPTH = 0.6
BOSS_RELIEF = 0.2      # recess into the plate underside above each boss


def build(p):
    length, width, lid_t, lip_h = p.length, p.width, p.lid_t, p.lip_h
    wall = p.wall

    # Lip outline: inner cavity of the base, inset by the fit clearance.
    lo_x = length / 2 - wall - LIP_CLEARANCE
    lo_y = width / 2 - wall - LIP_CLEARANCE
    lip_t = min(p.lip_t, lo_x - 2.0, lo_y - 2.0)

    # Screw boss centers -- same formula as enclosure_base.
    br = min(p.boss_d / 2, (min(length, width) / 2 - wall) / 2)
    bx = length / 2 - wall - br + 0.5
    by = width / 2 - wall - br + 0.5
    notch_r = br + 0.5                      # lip clears each boss by 0.5 mm

    screw_r = p.screw_d / 2
    csk_r = max(screw_r + 0.3, min(p.screw_d, wall + br - 1.1))

    r_out = 0.0
    if p.corner_r > 0.05:
        r_out = min(p.corner_r, min(length, width) / 2 - 1.0)

    boss_pts = [(bx, by), (-bx, by), (bx, -by), (-bx, -by)]

    with BuildPart() as part:
        # Plate: underside at z=0 (rests just above the base rim).
        Box(length, width, lid_t, align=(Align.CENTER, Align.CENTER, Align.MIN))
        if r_out > 0:
            fillet(part.edges().filter_by(Axis.Z), radius=r_out)

        # Lip ring extruded downward from the plate underside.
        with BuildSketch(Plane.XY):
            RectangleRounded(2 * lo_x, 2 * lo_y, radius=1.0)
            Rectangle(2 * (lo_x - lip_t), 2 * (lo_y - lip_t), mode=Mode.SUBTRACT)
        extrude(amount=-lip_h)

        # Notch the lip corners (plus a shallow plate relief) so the lid
        # clears the base's full-height corner screw bosses.
        with Locations(*[(x, y, BOSS_RELIEF) for x, y in boss_pts]):
            Cylinder(radius=notch_r, height=lip_h + 1.0 + BOSS_RELIEF,
                     align=(Align.CENTER, Align.CENTER, Align.MAX),
                     mode=Mode.SUBTRACT)

        # Countersunk screw holes aligned with the boss pilot holes.
        with Locations(*[(x, y, lid_t) for x, y in boss_pts]):
            CounterSinkHole(radius=screw_r, counter_sink_radius=csk_r,
                            counter_sink_angle=90)

        # Optional shallow logo recess on the top face.
        if p.emboss >= 0.5:
            el, ew = 0.32 * length, 0.22 * width
            with BuildSketch(Plane.XY.offset(lid_t)):
                RectangleRounded(el, ew, radius=min(2.0, ew / 2 - 0.3))
            extrude(amount=-EMBOSS_DEPTH, mode=Mode.SUBTRACT)

    return part.part
