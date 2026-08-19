# Copied from examples/prototyping/parts/enclosure_base.py for bench task modify_to_spec/mts_002_bigger_pcb.
# A derived task copies the script INTO the bundle: the runner registers no
# examples, so a run can never read the answer, and the starter and the
# reference are the SAME script at different parameters — the task is a
# parameter change, not a rewrite. The rubric is injected from
# ../../../specs/parts/, so this script declares no SPECS of its own.
"""Snap-fit electronics enclosure -- base shell.

Open-top shelled box with four corner screw bosses (pilot-holed, running
from the cavity floor to the rim), four PCB standoffs inset from the walls,
a ventilation slot pattern on one long wall, and filleted vertical corners.

Dependent dimensions are clamped inside build() (max()/min()) so the part
stays manifold at every parameter extreme, one parameter at a time.
"""

from build123d import *

PARAMS = {
    "length":     {"default": 100.0, "min": 60.0, "max": 200.0, "unit": "mm",
                   "description": "Outer shell length (X)"},
    "width":      {"default": 60.0,  "min": 40.0, "max": 150.0, "unit": "mm",
                   "description": "Outer shell width (Y)"},
    "height":     {"default": 30.0,  "min": 15.0, "max": 80.0,  "unit": "mm",
                   "description": "Outer shell height (Z), including the floor"},
    "wall":       {"default": 2.5,   "min": 1.2,  "max": 5.0,   "unit": "mm",
                   "description": "Wall and floor thickness"},
    "corner_r":   {"default": 3.0,   "min": 0.0,  "max": 8.0,   "unit": "mm",
                   "description": "Corner fillet radius on the vertical edges (0 = sharp)"},
    "boss_d":     {"default": 6.0,   "min": 4.0,  "max": 12.0,  "unit": "mm",
                   "description": "Corner screw boss outer diameter"},
    "pilot_d":    {"default": 2.2,   "min": 1.0,  "max": 4.0,   "unit": "mm",
                   "description": "Screw boss pilot hole diameter (self-tapping screws)"},
    "standoff_d": {"default": 6.0,   "min": 4.0,  "max": 10.0,  "unit": "mm",
                   "description": "PCB standoff outer diameter"},
    "standoff_h": {"default": 5.0,   "min": 3.0,  "max": 12.0,  "unit": "mm",
                   "description": "PCB standoff height above the cavity floor"},
    "pcb_margin": {"default": 8.0,   "min": 5.0,  "max": 15.0,  "unit": "mm",
                   "description": "PCB standoff center inset from the inner walls"},
    "n_vents":    {"default": 6.0,   "min": 0.0,  "max": 12.0,  "unit": "count",
                   "description": "Number of ventilation slots on the front long wall"},
}


def build(p):
    length, width, height = p.length, p.width, p.height
    # Wall must leave a cavity in every direction.
    wall = max(0.8, min(p.wall, length / 2 - 5.0, width / 2 - 5.0, height / 2 - 2.0))

    # Corner fillet: at least wall + 0.5 so the inward shell offset keeps a
    # positive inner corner radius; at most half the smallest side.
    r_out = 0.0
    if p.corner_r > 0.05:
        r_out = min(max(p.corner_r, wall + 0.5), min(length, width) / 2 - 1.0)

    # Corner screw bosses: tangent to both inner walls, embedded 0.5 mm so
    # the fuse is a real overlap, running floor -> rim.
    br = min(p.boss_d / 2, (min(length, width) / 2 - wall) / 2)
    bx = length / 2 - wall - br + 0.5
    by = width / 2 - wall - br + 0.5
    boss_h = height - wall
    pr = max(0.3, min(p.pilot_d, 2 * br - 1.6) / 2)  # keep >=0.8 mm boss wall
    pilot_depth = max(1.0, min(10.0, boss_h - 2.0))  # blind: never reach the floor

    # PCB standoffs: inset from the inner walls, clamped so they neither
    # touch the walls nor cross the centerline.
    margin = min(p.pcb_margin, 0.6 * (width / 2 - wall), 0.6 * (length / 2 - wall))
    sr = min(p.standoff_d / 2, margin - 0.5)
    sx = length / 2 - wall - margin
    sy = width / 2 - wall - margin
    sh = min(p.standoff_h, height - wall - 3.0)
    spr = min(1.1, sr - 0.8)                          # PCB screw pilot radius
    sp_depth = max(1.0, min(sh - 1.0, 6.0))

    # Ventilation slots on the y = -width/2 long wall, kept clear of the
    # corner bosses and fillets.
    n = int(round(p.n_vents))
    slot_w, pitch = 2.0, 6.0
    vent_h = height - wall - 7.0                      # 3 above floor, 4 below rim
    avail = length - 2 * (wall + 2 * br + max(r_out, wall) + 2.0)
    if vent_h < 2.0 or avail < slot_w:
        n = 0
    elif n > 1:
        n = min(n, int((avail - slot_w) // pitch) + 1)
    vent_zc = (wall + 3.0 + height - 4.0) / 2

    boss_pts = [(bx, by), (-bx, by), (bx, -by), (-bx, -by)]
    standoff_pts = [(sx, sy), (-sx, sy), (sx, -sy), (-sx, -sy)]

    with BuildPart() as part:
        Box(length, width, height, align=(Align.CENTER, Align.CENTER, Align.MIN))
        if r_out > 0:
            fillet(part.edges().filter_by(Axis.Z), radius=r_out)
        # Shell: open at the top, uniform wall + floor.
        offset(amount=-wall, openings=part.faces().sort_by(Axis.Z)[-1])

        # Corner screw bosses, floor to rim, with blind pilot holes.
        with Locations(*[(x, y, wall) for x, y in boss_pts]):
            Cylinder(radius=br, height=boss_h,
                     align=(Align.CENTER, Align.CENTER, Align.MIN))
        with Locations(*[(x, y, height) for x, y in boss_pts]):
            Cylinder(radius=pr, height=pilot_depth,
                     align=(Align.CENTER, Align.CENTER, Align.MAX),
                     mode=Mode.SUBTRACT)

        # PCB standoffs with screw pilot holes.
        with Locations(*[(x, y, wall) for x, y in standoff_pts]):
            Cylinder(radius=sr, height=sh,
                     align=(Align.CENTER, Align.CENTER, Align.MIN))
        with Locations(*[(x, y, wall + sh) for x, y in standoff_pts]):
            Cylinder(radius=spr, height=sp_depth,
                     align=(Align.CENTER, Align.CENTER, Align.MAX),
                     mode=Mode.SUBTRACT)

        # Ventilation slots through the front long wall.
        if n > 0:
            with Locations((0.0, -width / 2 + wall / 2, vent_zc)):
                with GridLocations(pitch, 1.0, n, 1):
                    Box(slot_w, wall + 2.0, vent_h, mode=Mode.SUBTRACT)

    return part.part
