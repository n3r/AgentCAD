"""ISO 4014 hex-head bolt, M4-M12, over ``agentcad.toolkit.threads``.

Partially threaded (ISO 4014, the bolt); the fully threaded screw is ISO 4017
and the toolkit helper takes it as ``standard="iso4017"``.

Origin and orientation: the **under-head bearing face is at local z = 0**, the
head rises to +z (to ``k``) and the shank runs down to ``z = -length``. The hex
is oriented flats-to-Y and corners-to-X, which is why the specs measure across
flats on the Y extent.

``thread`` is the cosmetic-vs-real choice `agentcad/toolkit/threads.py`
documents. ``cosmetic`` draws the shank at the thread's *root* diameter (fast,
light, drops into a tapped hole interference-free); ``real`` builds true ISO
helical geometry, reaches the nominal major diameter and costs roughly 9k
triangles per thread. They are dimensionally identical outside the flanks.

SPECS measure the built solid against the published ISO 4014 table: width
across flats ``s`` and head height ``k`` for the selected size, and the length
under the head. The predicate reads the size off the shape (a bd_warehouse
fastener carries its own ``screw_size``), so a screw whose size cannot be
established fails rather than passing quietly.
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


SPECS = [
    check_valid(requirement="ISO4014-01"),
    check_that(_across_flats_is_standard, name="across_flats_iso4014",
               requirement="ISO4014-02"),
    check_that(_head_height_is_standard, name="head_height_iso4014",
               requirement="ISO4014-03"),
    check_that(_length_under_head_is_the_length_asked_for,
               name="length_under_head", requirement="ISO4014-04"),
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
