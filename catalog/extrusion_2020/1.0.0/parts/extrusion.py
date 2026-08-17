"""20 x 20 T-slot aluminium extrusion, the 6 mm-slot (metric "20 series")
profile.

An **interface model**: the 20 x 20 envelope, the four 6 mm slot openings, the
T-channels behind them and the 4.2 mm centre bore are the dimensions a design
has to respect. The web fillets, the corner chamfers and the knurled slot
faces of a real profile are not modelled — they change no interface and they
would multiply the triangle count of a part that is usually a metre long.

**The section is one connected solid, and a SPEC says so.** A T-slot profile
is a closed outer skin, a central bore boss, four diagonal webs joining the
boss to the four corners, and the four T-channels that are the voids left
between them. Cut those channels as plain rectangles and adjacent channels
meet: the profile falls into five loose pieces that still measure 20 x 20 and
still have a 4.2 mm bore. That is exactly what this part used to build (five
solids, 151.40 mm^2 of section), so ``one_connected_solid`` and
``section_area`` are here to keep it from happening again.

Origin and orientation: the section is centred on the Z axis and the bar runs
from **z = 0 to z = length**. ``end_a`` and ``end_b`` are the two cut faces;
``slot_x_pos``, ``slot_x_neg``, ``slot_y_pos``, ``slot_y_neg`` are rigid
connectors on each face's slot centreline at mid-length, which is where a
T-nut, a corner bracket or another bar attaches.

SPECS measure the built solid: it is one connected bar, the section is 20 x 20
and its area is in the band a real profile's published mass implies, the bar
is as long as its ``length`` parameter asked for, and the centre bore is
4.2 mm (the tapping size for an M5 end fastener, which is the whole reason it
is there).
"""

import math

from build123d import *  # noqa: F401 — standard part-script preamble

from agentcad.toolkit.specs import check_that, check_valid

#: The profile, mm. `size` is the section across flats; `slot_open` the gap a
#: T-nut drops through; `lip_t` the thickness of the lip that retains it;
#: `slot_inner` the width of the channel behind the lip; `slot_depth` how far
#: the channel reaches in from the outer face; `bore` the centre hole; `web_t`
#: the thickness of the four diagonal webs. T-slot framing is a **vendor
#: convention, not a standard** — these are the commodity metric "20 series /
#: slot 6" dimensions.
SIZE = 20.0
SLOT_OPEN = 6.0
LIP_T = 2.0
SLOT_INNER = 11.5
SLOT_DEPTH = 6.5
BORE = 4.2
WEB_T = 2.0

#: The centre boss's radius is *derived*: the boss surface **is** the bottom
#: of the four channels, so it sits exactly `slot_depth` in from each face.
BOSS_R = SIZE / 2.0 - SLOT_DEPTH

#: The plausible section-area band, mm^2. There is no dimensional standard for
#: T-slot profiles, so the reference is mass: commodity 20-series slot-6 bar is
#: published at roughly **0.45-0.55 kg/m**, and at 6063 aluminium's
#: **2.70 g/cm^3** that is 167-204 mm^2. Rounded outward to 165-205. This model
#: measures **182.32 mm^2 (0.492 kg/m)** — mid-band, and it would be a little
#: lighter still with the corner chamfers and web fillets it does not model.
SECTION_MIN_MM2 = 165.0
SECTION_MAX_MM2 = 205.0

TOLERANCE_MM = 0.02

PARAMS = {
    "length": {"default": 100.0, "min": 10.0, "max": 1000.0, "unit": "mm",
               "description": "Cut length of the bar along Z"},
}


def _channel_void():
    """One T-channel, as the polygon subtracted from the square.

    Walking out from the boss and back, on the +X face (the other three are
    this one rotated by 90 degrees):

    * ``(half, +/- slot_open/2)`` — the mouth, in the outer face;
    * ``(half - lip_t, +/- slot_open/2)`` — the back of the retaining lip;
    * ``(half - lip_t, +/- slot_inner/2)`` — the ledge a T-nut pulls against;
    * ``(x_c, +/- slot_inner/2)`` — where the channel meets the web face;
    * ``(bx, +/- by)`` — where that web face meets the boss circle.

    The two tapering edges lie **on the faces of the diagonal webs**: both
    endpoints sit `web_t / 2` from the 45-degree centreline, so the edge is
    parallel to it and every web is `web_t` thick from the boss to the ledge.
    That is what keeps adjacent channels apart — cut them as rectangles
    `slot_inner` wide and `slot_depth` deep and they intersect, because
    `slot_inner / 2` (5.75) is larger than `size / 2 - slot_depth` (3.5).
    """
    half = SIZE / 2.0
    # Web faces are the lines y = x -/+ k, at perpendicular distance web_t/2
    # from the y = x centreline.
    k = WEB_T / math.sqrt(2.0)
    x_c = SLOT_INNER / 2.0 + k
    phi = math.radians(45.0) - math.asin((WEB_T / 2.0) / BOSS_R)
    bx, by = BOSS_R * math.cos(phi), BOSS_R * math.sin(phi)
    return [
        (half, SLOT_OPEN / 2.0),
        (half - LIP_T, SLOT_OPEN / 2.0),
        (half - LIP_T, SLOT_INNER / 2.0),
        (x_c, SLOT_INNER / 2.0),
        (bx, by),
        (bx, -by),
        (x_c, -SLOT_INNER / 2.0),
        (half - LIP_T, -SLOT_INNER / 2.0),
        (half - LIP_T, -SLOT_OPEN / 2.0),
        (half, -SLOT_OPEN / 2.0),
    ]


def _quarter_turns(points, turns):
    """`points` rotated by `turns` * 90 degrees about the origin.

    Integer quarter turns are done on the coordinates — exactly, and with no
    trigonometry — rather than by building each void on a rotated workplane.
    A nested `BuildSketch(Plane.XY.rotated(...))` subtracted **silently
    nothing** for one of the four faces here (400 -> 351.51 -> 351.51 mm^2, no
    error raised): OCCT succeeding is not evidence, so the rotation that is
    exact is the one that ships.
    """
    for _ in range(turns % 4):
        points = [(-y, x) for x, y in points]
    return points


def _profile():
    """The 2D section, as a sketch on XY: the square, minus the four
    T-channels, plus the centre boss they are cut against, minus the bore."""
    void = _channel_void()
    with BuildSketch() as section:
        Rectangle(SIZE, SIZE)
        for turns in range(4):
            Polygon(*_quarter_turns(void, turns), align=None,
                    mode=Mode.SUBTRACT)
        # The channels are cut past the boss and the boss is added back, so
        # the bottom of every channel is the boss's own cylindrical surface.
        Circle(BOSS_R, mode=Mode.ADD)
        Circle(BORE / 2.0, mode=Mode.SUBTRACT)
    return section.sketch


def build(p):
    with BuildPart() as bar:
        with BuildSketch(Plane.XY):
            add(_profile())
        extrude(amount=p.length)
    part = bar.part
    # The length the CALLER asked for, carried on the solid so a spec can
    # measure the geometry against it (bd_warehouse fasteners carry their
    # `screw_size` and `length` the same way). A build that ignored its
    # parameter then fails `length_and_origin` instead of agreeing with the
    # declared range.
    part.cut_length_mm = float(p.length)
    return part


def _section_is_20_square(part, metrics) -> bool:
    bbox = metrics["bbox"]
    return bool(abs((bbox["max"][0] - bbox["min"][0]) - SIZE) <= TOLERANCE_MM
                and abs((bbox["max"][1] - bbox["min"][1]) - SIZE)
                <= TOLERANCE_MM)


def _is_one_connected_bar(part, metrics) -> bool:
    """One solid, not five. The bounding box, the bore and the slot openings
    are all still right on a profile whose webs have been cut through, so this
    is the only check that sees it."""
    return len(part.solids()) == 1


def _section_area_is_plausible(part, metrics) -> bool:
    """Volume / length against the band a real profile's mass implies.

    The volume is summed over `part.solids()` — a nested `Compound.volume`
    undercounts — so this reads the whole section even while the connectivity
    check is red.
    """
    bbox = metrics["bbox"]
    height = bbox["max"][2] - bbox["min"][2]
    if height <= 0.0:
        return False
    area = sum(solid.volume for solid in part.solids()) / height
    return bool(SECTION_MIN_MM2 <= area <= SECTION_MAX_MM2)


def _length_is_the_length_asked_for(part, metrics) -> bool:
    """The bar starts at z = 0 and is as long as its `length` parameter.

    Measured against the value `build` recorded on the solid, not against the
    parameter's declared range: `10 <= height <= 1000` is true for every legal
    value of `length`, so it is not a check.
    """
    asked = getattr(part, "cut_length_mm", None)
    if not isinstance(asked, (int, float)):
        return False
    bbox = metrics["bbox"]
    height = bbox["max"][2] - bbox["min"][2]
    return bool(abs(bbox["min"][2]) <= TOLERANCE_MM
                and abs(height - float(asked)) <= TOLERANCE_MM)


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
    check_that(_is_one_connected_bar, name="one_connected_solid",
               requirement="EXT2020-05"),
    check_that(_section_area_is_plausible, name="section_area",
               requirement="EXT2020-06"),
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
