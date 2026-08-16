"""Hex-head bolt with the ISO 4014 head, M4-M12, over
``agentcad.toolkit.threads``.

**Threaded over its whole length, and ISO 4014 is not.** The real ISO 4014
bolt is *partially* threaded — an M8 x 30 carries b = 22 mm of thread and an
8.000 mm plain shank — and the pinned **bd_warehouse 0.3.0 cannot build
that**: ``Screw.__init__`` sets ``thread_length = length - length_offset``
unconditionally, no screw class takes a thread-length argument, and the
``iso4014`` rows of ``HexHeadScrew.fastener_data`` carry only ``k``, ``s`` and
the length limits — there is no ``b`` column to build one from. So what this
package builds is the **ISO 4014 head** (width across flats ``s``, head height
``k``) on a shank that is threaded end to end, which is an ISO 4017-shaped
screw with ISO 4014 head heights (they differ: ``k`` is 5.30 for M8 in
ISO 4014 and 5.54 in ISO 4017).

Use it as an envelope, a clearance model and a mate. **Do not use it where the
plain shank is the point** — a bolt in shear, a reamed fit, a shoulder
bearing on the unthreaded portion.

``shank_full_length_root`` is the spec that pins that claim: it measures one
cylindrical face at the thread's basic minor diameter d1 = d - 1.0825 P
(⌀6.647 for M8 x 1.25) running the whole length under the head. If a future
bd_warehouse builds the partial thread, that check goes red — which is the
notification that this docstring has to change.

Origin and orientation: the **under-head bearing face is at local z = 0**, the
head rises to +z (to ``k``) and the shank runs down to ``z = -length``. The hex
is oriented flats-to-Y and corners-to-X, which is why the specs measure across
flats on the Y extent.

``thread`` is the cosmetic-vs-real choice `agentcad/toolkit/threads.py`
documents, and neither value adds a plain shank. ``cosmetic`` leaves the shank
the bare root-diameter cylinder (fast, light, drops into a tapped hole
interference-free); ``real`` adds true ISO helical geometry on top of it,
reaching the nominal major diameter, at roughly 9k triangles per thread. They
are dimensionally identical outside the flanks.

SPECS measure the built solid against the published ISO 4014 table: width
across flats ``s`` and head height ``k`` for the selected size, the length
under the head, and the shank. The predicate reads the size off the shape (a
bd_warehouse fastener carries its own ``screw_size``), so a screw whose size
cannot be established fails rather than passing quietly.
"""

from build123d import *  # noqa: F401 — standard part-script preamble

from agentcad.toolkit import threads
from agentcad.toolkit.specs import check_that, check_valid

#: ISO 4014, mm. ``s`` is the width across flats (nominal) and ``k`` the head
#: height (nominal). Source: ISO 4014:2011 table 1.
ISO4014 = {
    "M4-0.7": {"s": 7.0, "k": 2.8},
    "M5-0.8": {"s": 8.0, "k": 3.5},
    "M6-1": {"s": 10.0, "k": 4.0},
    "M8-1.25": {"s": 13.0, "k": 5.3},
    "M10-1.5": {"s": 16.0, "k": 6.4},
    "M12-1.75": {"s": 18.0, "k": 7.5},
}

TOLERANCE_MM = 0.05

PARAMS = {
    "size": {"default": "M8-1.25", "type": "enum", "choices": list(ISO4014),
             "description": "ISO metric thread designation (diameter-pitch)"},
    "length": {"default": 30.0, "min": 10.0, "max": 100.0, "unit": "mm",
               "description": "Length under the head; continuous, not "
                              "restricted to the catalogue increments (the "
                              "presets carry those)"},
    "thread": {"default": "cosmetic", "type": "enum",
               "choices": ["cosmetic", "real"],
               "description": "cosmetic = plain shank cylinder (fast, light, "
                              "the default); real = true ISO helical thread "
                              "geometry (~9k triangles, slower)"},
}


def _row(part):
    """The standard's row for the bolt ``part`` actually is, or ``None`` —
    which fails every check below."""
    return ISO4014.get(getattr(part, "screw_size", None))


def _across_flats_is_standard(part, metrics) -> bool:
    row = _row(part)
    if row is None:
        return False
    bbox = metrics["bbox"]
    return bool(abs((bbox["max"][1] - bbox["min"][1]) - row["s"]) <= TOLERANCE_MM)


def _head_height_is_standard(part, metrics) -> bool:
    row = _row(part)
    if row is None:
        return False
    return bool(abs(metrics["bbox"]["max"][2] - row["k"]) <= TOLERANCE_MM)


def _length_under_head_is_the_length_asked_for(part, metrics) -> bool:
    length = getattr(part, "length", None)
    if not isinstance(length, (int, float)):
        return False
    return bool(abs(metrics["bbox"]["min"][2] + float(length)) <= TOLERANCE_MM)


def _thread_root_diameter(size: str):
    """The basic minor diameter of the external thread, d1 = d - 1.0825 P.

    1.0825 is 2 x (5/8)H with H = (sqrt(3)/2)P, the ISO 68-1 profile height:
    M8 x 1.25 -> 8 - 1.0825 x 1.25 = 6.6469. Parsed out of the designation, so
    it adds no hand-typed table to drift — and it is the same number
    bd_warehouse draws the cosmetic shank at (measured ⌀6.6468 on M8 x 30).
    """
    try:
        diameter, pitch = size.lstrip("Mm").split("-")
        return float(diameter) - 1.0825 * float(pitch)
    except (AttributeError, ValueError):
        return None


def _shank_is_the_full_length_root_cylinder(part, metrics) -> bool:
    """Exactly one cylindrical face spanning z = -length to z = 0, at the
    thread root diameter: the shank is threaded end to end and there is no
    plain nominal-diameter portion. Red if either half stops being true."""
    size = getattr(part, "screw_size", None)
    length = getattr(part, "length", None)
    root = _thread_root_diameter(size) if size in ISO4014 else None
    if root is None or not isinstance(length, (int, float)):
        return False
    spanning = []
    for face in part.faces().filter_by(GeomType.CYLINDER):
        box = face.bounding_box()
        if (abs(box.min.Z + float(length)) <= TOLERANCE_MM
                and abs(box.max.Z) <= TOLERANCE_MM):
            spanning.append(face)
    return bool(len(spanning) == 1
                and abs(2.0 * spanning[0].radius - root) <= TOLERANCE_MM)


SPECS = [
    check_valid(requirement="ISO4014-01"),
    check_that(_across_flats_is_standard, name="across_flats_iso4014",
               requirement="ISO4014-02"),
    check_that(_head_height_is_standard, name="head_height_iso4014",
               requirement="ISO4014-03"),
    check_that(_length_under_head_is_the_length_asked_for,
               name="length_under_head", requirement="ISO4014-04"),
    check_that(_shank_is_the_full_length_root_cylinder,
               name="shank_full_length_root", requirement="ISO4014-05"),
]


def build(p):
    return threads.hex_bolt(p.size, p.length, simple=p.thread == "cosmetic")


def connectors(p, part):
    """``head_seat`` is the under-head bearing face (rigid, so it can be the
    moving side of a mate); ``axis`` is the shank centreline pointing into the
    material (cylindrical)."""
    return {
        "head_seat": {"type": "rigid", "location": ((0, 0, 0), (0, 0, 0))},
        "axis": {"type": "cylindrical", "axis": ((0, 0, 0), (0, 0, -1))},
    }
