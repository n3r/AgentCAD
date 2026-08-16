"""NEMA 17 stepper motor outline (NEMA ICS 16-2001, 1.7 in frame).

An **interface model**, and it says so: the frame square, the chamfered
corners, the four tapped mounting holes on their bolt pattern, the pilot boss
and the shaft are what a bracket has to fit and clear. The stator laminations,
the wiring gland and the rear bearing cap are not modelled — the body is a
solid block, so this is an envelope for fit and interference, not a mass
model. ``body_length`` is the only thing that varies between motors of this
frame, which is why it is the only parameter.

Origin and orientation: the **mounting face is at z = 0**, the body runs back
to ``z = -body_length``, and the boss and shaft stand up in +z. So
``face_mount`` is the origin — mate it onto the bracket face and the motor
hangs behind it, exactly as it does in a machine.

SPECS measure the built solid against the frame's published interface: the
frame square, the shaft diameter, the pilot-boss diameter, and the bolt
pattern — the four mounting holes' own cylindrical faces, at their own
centres. A bracket that trusts this package is trusting those four numbers.
"""

from build123d import *  # noqa: F401 — standard part-script preamble

from agentcad.toolkit.specs import check_that, check_valid

#: NEMA 17 interface, mm.
FRAME = 42.3           # frame square, across flats
CORNER_CHAMFER = 3.0   # the corner cut every NEMA 17 body carries
BOLT_PATTERN = 31.0    # mounting-hole square, centre to centre
HOLE_D = 3.0           # M3 tapped mounting holes
HOLE_DEPTH = 4.5
BOSS_D = 22.0          # pilot boss that centres the motor in a bracket
BOSS_H = 2.0
SHAFT_D = 5.0
SHAFT_L = 24.0

TOLERANCE_MM = 0.05

PARAMS = {
    "body_length": {"default": 40.0, "min": 20.0, "max": 60.0, "unit": "mm",
                    "description": "Motor body length behind the mounting "
                                   "face (34, 40 and 48 are the common ones)"},
}


def build(p):
    half = BOLT_PATTERN / 2.0
    with BuildPart() as motor:
        # The body hangs BEHIND the mounting face at z = 0.
        Box(FRAME, FRAME, p.body_length,
            align=(Align.CENTER, Align.CENTER, Align.MAX))
        chamfer(motor.edges().filter_by(Axis.Z), length=CORNER_CHAMFER)
        # Pilot boss and shaft, in front of it.
        Cylinder(BOSS_D / 2.0, BOSS_H,
                 align=(Align.CENTER, Align.CENTER, Align.MIN))
        Cylinder(SHAFT_D / 2.0, SHAFT_L,
                 align=(Align.CENTER, Align.CENTER, Align.MIN))
        # Four tapped mounting holes, blind, into the mounting face.
        with Locations(Plane.XY):
            with Locations((half, half), (half, -half),
                           (-half, half), (-half, -half)):
                Cylinder(HOLE_D / 2.0, HOLE_DEPTH,
                         align=(Align.CENTER, Align.CENTER, Align.MAX),
                         mode=Mode.SUBTRACT)
    return motor.part


def _cylinder_radii(part):
    return [f.radius for f in part.faces().filter_by(GeomType.CYLINDER)]


def _has_radius(part, diameter) -> bool:
    return any(abs(2.0 * r - diameter) <= TOLERANCE_MM
               for r in _cylinder_radii(part))


def _frame_is_square(part, metrics) -> bool:
    bbox = metrics["bbox"]
    return bool(abs((bbox["max"][0] - bbox["min"][0]) - FRAME) <= TOLERANCE_MM
                and abs((bbox["max"][1] - bbox["min"][1]) - FRAME)
                <= TOLERANCE_MM)


def _shaft_diameter(part, metrics) -> bool:
    return _has_radius(part, SHAFT_D)


def _pilot_boss_diameter(part, metrics) -> bool:
    return _has_radius(part, BOSS_D)


def _bolt_pattern(part, metrics) -> bool:
    """The four mounting holes are on the published square.

    Found by their own radius, then measured at their own face centres — so
    this fails if a hole moves, if one is missing, or if a fifth appears.
    """
    holes = [f for f in part.faces().filter_by(GeomType.CYLINDER)
             if abs(2.0 * f.radius - HOLE_D) <= TOLERANCE_MM]
    if len(holes) != 4:
        return False
    want = BOLT_PATTERN / 2.0
    for face in holes:
        # The face's BOUNDING BOX centre, not `Face.center()`: the latter is
        # the parametric centre of the surface, which for a closed cylinder
        # sits on the wall (measured: x = -17.0 for a hole whose axis is at
        # -15.5). The bounding box of a full cylindrical face is centred on
        # its axis.
        centre = face.bounding_box().center()
        if (abs(abs(centre.X) - want) > TOLERANCE_MM
                or abs(abs(centre.Y) - want) > TOLERANCE_MM):
            return False
    return True


SPECS = [
    check_valid(requirement="NEMA17-01"),
    check_that(_frame_is_square, name="frame_42_3", requirement="NEMA17-02"),
    check_that(_bolt_pattern, name="bolt_pattern_31", requirement="NEMA17-03"),
    check_that(_shaft_diameter, name="shaft_5mm", requirement="NEMA17-04"),
    check_that(_pilot_boss_diameter, name="pilot_boss_22",
               requirement="NEMA17-05"),
]


def connectors(p, part):
    """``face_mount`` is the mounting face at the origin (rigid, so it can be
    the moving side of a mate onto a bracket); ``shaft`` is the output shaft
    axis (cylindrical: a coupling or pulley slides along it and turns on it)."""
    return {
        "face_mount": {"type": "rigid", "location": ((0, 0, 0), (0, 0, 0))},
        "shaft": {"type": "cylindrical", "axis": ((0, 0, 0), (0, 0, 1))},
    }
