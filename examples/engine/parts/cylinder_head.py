"""SOHC two-valve-per-cylinder head for one bank of the V4.

Local frame: the head-gasket face is z = 0, body above (+Z); combustion
chambers at y = -+40 (the block's bore pitch). One script serves both banks
via the layout's R_z(180) symmetry (bank B's seat connector carries the
turn). Local +X faces outboard (exhaust), -X the valley (intake).

This is a real assembly platform, not a display solid:
- valve seats, guide bosses and spring pockets for eight vertical valves
  (`valve_set`), Ø19 heads at y = cylinder -+17.5;
- three cam saddles (y = -70, 0, +70) with half-bores at z = 62 — the
  camshaft drops in and `cam_cap_set` bolts over it, main-bearing style;
- central spark-plug wells (Ø12) between the valves, with tube bosses the
  cam cover's tubes land over;
- head-bolt wells and through-holes (M10 pattern shared with the block),
  dowel holes matching the block's deck pins;
- a tapped M5 rail for the separate `cam_cover`;
- port flange pads with real stud patterns for the manifolds (v1-proven).
"""

from build123d import *

BORE_PITCH = 80.0
LENGTH = 150.0
WIDTH = 100.0
BASE_H = 42.0
BOLT_X = 36.0                 # head-bolt columns (block matches)
BOLT_YS = (-15.5, 15.5)
STUD_PITCH = 17.0
STUD_D = 8.0
PORT_Z = BASE_H / 2.0
VALVE_OFF = 17.0              # valve centers at cylinder -+17.5 along Y
CAM_Z = 62.0                  # camshaft axis height
SADDLE_YS = (-31.0, 0.0, 31.0)
COVER_BOLT_PTS = ((-40, -47), (40, -47), (-40, 47), (40, 47),
                  (32, -65), (-32, 65))  # tapped M5 rail (cam_cover matches)
DOWEL_PTS = ((30.0, 65.0), (-30.0, -65.0))

PARAMS = {
    "bore": {"default": 66.0, "min": 50.0, "max": 78.0, "unit": "mm",
             "description": "Cylinder bore this head covers (chambers are bore - 6)"},
    "chamber_depth": {"default": 10.0, "min": 6.0, "max": 14.0, "unit": "mm",
                      "description": "Combustion chamber recess depth"},
    "valve_d": {"default": 19.0, "min": 15.0, "max": 21.0, "unit": "mm",
                "description": "Valve head diameter (seat recess adds 0.4)"},
    "port_d": {"default": 24.0, "min": 18.0, "max": 30.0, "unit": "mm",
               "description": "Intake / exhaust port bore diameter"},
}


def build(p):
    chamber_r = (p.bore - 6.0) / 2.0
    boss_d = p.port_d + 8.0

    head = Pos(0, 0, BASE_H / 2) * Box(WIDTH, LENGTH, BASE_H)

    # cam saddles (cam offset over the exhaust side) and rocker-shaft
    # pedestals on the intake side, both between the valve groups
    for ty in SADDLE_YS:
        head += Pos(25.5, ty, (BASE_H + CAM_Z) / 2) * Box(37, 8, CAM_Z - BASE_H)
        head += Pos(-16, ty, 50) * Box(12, 8, 16)

    # port pads, bosses, and studs (both sides), plug tube bosses
    for y in (-BORE_PITCH / 2, BORE_PITCH / 2):
        head += Pos(52.5, y, PORT_Z + 1) * Box(5, 52, 40)
        head += Pos(WIDTH / 2 + 9, y, PORT_Z) * Rot(Y=90) * Cylinder(
            radius=boss_d / 2, height=18)
        head += Pos(-52.5, y, PORT_Z + 1) * Box(5, 52, 40)
        head += Pos(-WIDTH / 2 - 5, y, PORT_Z) * Rot(Y=90) * Cylinder(
            radius=boss_d / 2, height=10)
        for sy in (-STUD_PITCH, STUD_PITCH):
            for sz in (-STUD_PITCH, STUD_PITCH):
                # tapped stud holes; the threaded studs are stud_set parts
                head -= Pos(50, y + sy, PORT_Z + sz) * Rot(Y=90) * Cylinder(
                    radius=4.2, height=14)
                head -= Pos(-50, y + sy, PORT_Z + sz) * Rot(Y=90) * Cylinder(
                    radius=4.2, height=14)
        head += Pos(0, y, BASE_H + 2) * Cylinder(radius=8, height=4)

    # combustion chambers, port bores
    for y in (-BORE_PITCH / 2, BORE_PITCH / 2):
        head -= Pos(0, y, (p.chamber_depth - 1) / 2) * Cylinder(
            radius=chamber_r, height=p.chamber_depth + 1)
        head -= Pos(WIDTH / 2 + 4, y, PORT_Z) * Rot(Y=90) * Cylinder(
            radius=p.port_d / 2, height=30)
        head -= Pos(-WIDTH / 2 - 1, y, PORT_Z) * Rot(Y=90) * Cylinder(
            radius=p.port_d / 2, height=20)
        # spark-plug well straight down the chamber center
        head -= Pos(0, y, BASE_H / 2 + 4) * Cylinder(radius=6, height=BASE_H + 8)
        # valve seats, guides, spring pockets
        for sgn in (+1, -1):
            vy = y + sgn * VALVE_OFF
            head -= Pos(0, vy, p.chamber_depth) * Cylinder(
                radius=p.valve_d / 2 + 0.2, height=4.2)
            head -= Pos(0, vy, 12.2) * Cylinder(radius=6.0, height=8.6)
            head += Pos(0, vy, BASE_H - 5) * Cylinder(radius=6, height=6)
            head -= Pos(0, vy, BASE_H / 2) * Cylinder(radius=2.9,
                                                      height=BASE_H + 4)
            head -= Pos(0, vy, BASE_H - 3.9) * Cylinder(radius=13, height=8.2)
            # spring-clearance counterbore machined up past the towers
            head -= Pos(0, vy, 51.95) * Cylinder(radius=9.4, height=19.9)

    # cam half-bores + tapped cap-bolt holes; rocker-shaft bore through
    # the pedestals
    head -= Rot(X=-90) * Pos(24, -CAM_Z, 0) * Cylinder(radius=11.1,
                                                       height=LENGTH + 20)
    head -= Rot(X=-90) * Pos(-16, -54, 0) * Cylinder(radius=5.2,
                                                     height=LENGTH + 20)
    for ty in SADDLE_YS:
        for bx in (10.0, 40.0):
            head -= Pos(bx, ty, CAM_Z - 6) * Cylinder(radius=2.7, height=14)

    # head-bolt wells + through-holes (block pattern), dowels, cover rail
    for bx in (-BOLT_X, BOLT_X):
        for by in BOLT_YS:
            head -= Pos(bx, by, BASE_H - 2.9) * Cylinder(radius=8.5, height=6.2)
            head -= Pos(bx, by, BASE_H / 2) * Cylinder(radius=5.5,
                                                       height=BASE_H + 4)
    for dx, dy in DOWEL_PTS:
        head -= Pos(dx, dy, 3.5) * Cylinder(radius=3.15, height=9.2)
    # relief pockets under the rocker-beam cam ends (open valves dip the
    # beams below the deck line, exactly like a real casting relief)
    for cy in (-BORE_PITCH / 2, BORE_PITCH / 2):
        for sgn in (+1, -1):
            head -= Pos(27, cy + sgn * VALVE_OFF, 39.9) * Box(26, 7, 4.4)
    for cx, cy in COVER_BOLT_PTS:
        head -= Pos(cx, cy, BASE_H - 4.9) * Cylinder(radius=2.7, height=10)
    return head


def connectors(p, part):
    """The gasket-face center; rigid-mate it to a block ``head_*_seat`` and
    the seat's own rotation cants the head to the bank angle."""
    return {"deck": {"type": "rigid", "location": ((0, 0, 0), (0, 0, 0))}}
