"""Two-cylinder head for one bank of the V4, with cam cover and dressed ports.

Local frame: the head-gasket face is z = 0 with the body above (+Z); the two
combustion chambers are recessed into it at y = -+40 (the block's 80 mm bore
pitch). One script serves both banks: the assembly mates a copy onto each
deck seat, and the block's connector rotation cants it to the bank angle. In
that mated pose local +X faces outboard (exhaust side) and -X faces the
valley (intake side).

Dress detail: machined port flange pads with real stud patterns (the intake
and exhaust manifolds carry matching clearance holes and slide over these
studs), spark-plug tubes through the ribbed cam cover (the ignition coils
seat on them), an oil filler cap, and twin cam-drive humps on the front face
toward the timing cover.
"""

from build123d import *

from agentcad.toolkit import safe_fillet

BORE_PITCH = 80.0
LENGTH = 150.0     # along Y (the crank axis)
WIDTH = 100.0      # across the bank
BASE_H = 42.0      # head casting height, gasket face to cover rail
COVER_W = 70.0
COVER_L = 130.0
BOLT_D = 9.0
STUD_PITCH = 17.0  # port studs at (-+17, -+17) around each port center
STUD_D = 8.0
PORT_Z = BASE_H / 2.0

PARAMS = {
    "bore": {"default": 66.0, "min": 50.0, "max": 78.0, "unit": "mm",
             "description": "Cylinder bore this head covers (chambers are bore - 6)"},
    "chamber_depth": {"default": 10.0, "min": 6.0, "max": 14.0, "unit": "mm",
                      "description": "Combustion chamber recess depth"},
    "cover_height": {"default": 18.0, "min": 14.0, "max": 26.0, "unit": "mm",
                     "description": "Cam cover height above the casting"},
    "port_d": {"default": 24.0, "min": 18.0, "max": 30.0, "unit": "mm",
               "description": "Intake / exhaust port bore diameter"},
}


def build(p):
    chamber_r = (p.bore - 6.0) / 2.0
    boss_d = p.port_d + 8.0

    head = Pos(0, 0, BASE_H / 2) * Box(WIDTH, LENGTH, BASE_H)
    head += Pos(0, 0, BASE_H + p.cover_height / 2) * Box(
        COVER_W, COVER_L, p.cover_height)

    for y in (-BORE_PITCH / 2, BORE_PITCH / 2):
        # exhaust side (+X): machined pad, boss, and four studs
        head += Pos(52.5, y, PORT_Z + 1) * Box(5, 52, 40)
        head += Pos(WIDTH / 2 + 9, y, PORT_Z) * Rot(Y=90) * Cylinder(
            radius=boss_d / 2, height=18)
        for sy in (-STUD_PITCH, STUD_PITCH):
            for sz in (-STUD_PITCH, STUD_PITCH):
                head += Pos(67, y + sy, PORT_Z + sz) * Rot(Y=90) * Cylinder(
                    radius=STUD_D / 2, height=24)
        # intake side (-X): pad, shorter boss, studs toward the valley
        head += Pos(-52.5, y, PORT_Z + 1) * Box(5, 52, 40)
        head += Pos(-WIDTH / 2 - 5, y, PORT_Z) * Rot(Y=90) * Cylinder(
            radius=boss_d / 2, height=10)
        for sy in (-STUD_PITCH, STUD_PITCH):
            for sz in (-STUD_PITCH, STUD_PITCH):
                head += Pos(-63.5, y + sy, PORT_Z + sz) * Rot(Y=90) * Cylinder(
                    radius=STUD_D / 2, height=17)
        # spark-plug tube up through the cam cover (coil seats on top)
        head += Pos(0, y, (BASE_H + p.cover_height + 6) / 2 + BASE_H / 2) * \
            Cylinder(radius=11, height=p.cover_height + 6)

    # cam-drive humps on the front face, running toward the timing cover
    # (kept within 4 mm of the casting so head A clears the cover's gasket)
    for hx in (-16.0, 16.0):
        head += Pos(hx, -LENGTH / 2 - 1, 30) * Rot(X=-90) * Cylinder(
            radius=18, height=6)

    # ribbed cam cover + oil filler cap
    for ry in (-45.0, -15.0, 15.0, 45.0):
        head += Pos(0, ry, BASE_H + p.cover_height + 1) * Box(COVER_W - 8, 3, 3)
    head += Pos(20, 0, BASE_H + p.cover_height + 2.5) * Cylinder(radius=13,
                                                                 height=5)

    # combustion chambers into the gasket face; port bores into the bosses;
    # plug wells down the tubes
    for y in (-BORE_PITCH / 2, BORE_PITCH / 2):
        head -= Pos(0, y, (p.chamber_depth - 1) / 2) * Cylinder(
            radius=chamber_r, height=p.chamber_depth + 1)
        head -= Pos(WIDTH / 2 + 4, y, PORT_Z) * Rot(Y=90) * Cylinder(
            radius=p.port_d / 2, height=30)
        head -= Pos(-WIDTH / 2 - 1, y, PORT_Z) * Rot(Y=90) * Cylinder(
            radius=p.port_d / 2, height=20)
        head -= Pos(0, y, BASE_H + p.cover_height + 2) * Cylinder(
            radius=7, height=10)

    # head-bolt holes through the casting, outside the chambers
    with BuildPart() as bolts:
        with BuildSketch(Plane.XY.offset(-1)):
            with Locations([(x, y) for x in (-38, 38) for y in (-55, 0, 55)]):
                Circle(BOLT_D / 2)
        extrude(amount=BASE_H + 2)
    head -= bolts.part

    # round the cam-cover top edges
    top = [e for e in head.edges().group_by(Axis.Z)[-1]]
    if top:
        head, _r, _warn = safe_fillet(head, top, radius=1.5)
    return head


def connectors(p, part):
    """The gasket-face center; rigid-mate it to a block ``head_*_seat`` and
    the seat's own rotation cants the head to the bank angle."""
    return {"deck": {"type": "rigid", "location": ((0, 0, 0), (0, 0, 0))}}
