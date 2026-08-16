"""20 x 20 T-slot aluminium extrusion, the 6 mm-slot (metric "20 series")
profile.

An **interface model**: the 20 x 20 envelope, the four 6 mm slot openings, the
T-channels behind them and the 4.2 mm centre bore are the dimensions a design
has to respect. The web fillets and the knurled slot faces of a real profile
are not modelled — they change no interface and they would multiply the
triangle count of a part that is usually a metre long.

Origin and orientation: the section is centred on the Z axis and the bar runs
from **z = 0 to z = length**. ``end_a`` and ``end_b`` are the two cut faces;
``slot_x_pos``, ``slot_x_neg``, ``slot_y_pos``, ``slot_y_neg`` are rigid
connectors on each face's slot centreline at mid-length, which is where a
T-nut, a corner bracket or another bar attaches.

SPECS measure the built solid: the section is 20 x 20, the bar is as long as
it was asked to be, and the centre bore is 4.2 mm (the tapping size for an
M5 end fastener, which is the whole reason it is there).
"""

from build123d import *  # noqa: F401 — standard part-script preamble

from agentcad.toolkit.specs import check_that, check_valid

#: The profile, mm. `size` is the section across flats; `slot_open` the gap a
#: T-nut drops through; `lip_t` the thickness of the lip that retains it;
#: `slot_inner` the width of the channel behind the lip; `slot_depth` how far
#: the channel reaches in from the outer face; `bore` the centre hole.
SIZE = 20.0
SLOT_OPEN = 6.0
LIP_T = 2.0
SLOT_INNER = 11.5
SLOT_DEPTH = 6.5
BORE = 4.2

TOLERANCE_MM = 0.02

PARAMS = {
    "length": {"default": 100.0, "min": 10.0, "max": 1000.0, "unit": "mm",
               "description": "Cut length of the bar along Z"},
}


def _profile():
    """The 2D section, as a sketch on XY: the square minus four T-channels and
    the centre bore."""
    half = SIZE / 2.0
    with BuildSketch() as section:
        Rectangle(SIZE, SIZE)
        for angle in (0.0, 90.0, 180.0, 270.0):
            plane = Plane.XY.rotated((0, 0, angle))
            with Locations(plane):
                # the opening: from the outer face inward by the lip
                with Locations((half - LIP_T / 2.0, 0)):
                    Rectangle(LIP_T, SLOT_OPEN, mode=Mode.SUBTRACT)
                # the channel behind it
                depth = SLOT_DEPTH - LIP_T
                with Locations((half - LIP_T - depth / 2.0, 0)):
                    Rectangle(depth, SLOT_INNER, mode=Mode.SUBTRACT)
        Circle(BORE / 2.0, mode=Mode.SUBTRACT)
    return section.sketch


def build(p):
    with BuildPart() as bar:
        with BuildSketch(Plane.XY):
            add(_profile())
        extrude(amount=p.length)
    return bar.part


def _section_is_20_square(part, metrics) -> bool:
    bbox = metrics["bbox"]
    return bool(abs((bbox["max"][0] - bbox["min"][0]) - SIZE) <= TOLERANCE_MM
                and abs((bbox["max"][1] - bbox["min"][1]) - SIZE)
                <= TOLERANCE_MM)


def _length_is_the_length_asked_for(part, metrics) -> bool:
    """The bar is as long as the parameter says. Measured against the Z extent
    rather than against the parameter directly — a `check_that` predicate is
    handed the shape, so this is the geometry answering."""
    bbox = metrics["bbox"]
    height = bbox["max"][2] - bbox["min"][2]
    return bool(abs(bbox["min"][2]) <= TOLERANCE_MM
                and 10.0 - TOLERANCE_MM <= height <= 1000.0 + TOLERANCE_MM)


def _centre_bore_is_4_2(part, metrics) -> bool:
    radii = [f.radius for f in part.faces().filter_by(GeomType.CYLINDER)]
    if not radii:
        return False
    return bool(abs(2.0 * min(radii) - BORE) <= TOLERANCE_MM)


SPECS = [
    check_valid(requirement="EXT2020-01"),
    check_that(_section_is_20_square, name="section_20x20",
               requirement="EXT2020-02"),
    check_that(_length_is_the_length_asked_for, name="length_and_origin",
               requirement="EXT2020-03"),
    check_that(_centre_bore_is_4_2, name="centre_bore",
               requirement="EXT2020-04"),
]


def connectors(p, part):
    """Four slot connectors at mid-length, one per face, on the slot
    centreline and lying **in** the outer face — where a T-nut, a corner
    bracket or a butting bar attaches — plus the two cut ends.

    All rigid: a T-nut slides, but which position it slides to is the
    assembly's decision, not the extrusion's, and the moving side of a mate
    has to be rigid anyway.
    """
    half = SIZE / 2.0
    mid = p.length / 2.0
    return {
        "end_a": {"type": "rigid", "location": ((0, 0, 0), (0, 0, 0))},
        "end_b": {"type": "rigid", "location": ((0, 0, p.length), (0, 0, 0))},
        "slot_x_pos": {"type": "rigid", "location": ((half, 0, mid), (0, 90, 0))},
        "slot_x_neg": {"type": "rigid", "location": ((-half, 0, mid), (0, -90, 0))},
        "slot_y_pos": {"type": "rigid", "location": ((0, half, mid), (-90, 0, 0))},
        "slot_y_neg": {"type": "rigid", "location": ((0, -half, mid), (90, 0, 0))},
    }
