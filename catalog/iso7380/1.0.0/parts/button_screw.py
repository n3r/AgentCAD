"""ISO 7380-1 button-head socket screw, M3-M12.

A low, domed head with a hex socket — the fastener you reach for when an
ISO 4762 cap screw's head is too tall or too sharp-edged.

Origin and orientation: the **under-head bearing face is at local z = 0**, the
dome rises to +z (to ``k``) and the threaded shank runs down to
``z = -length``, so ``head_seat`` is the origin and the shank goes into the
material — the same convention as `iso4762` and `iso4014`.

``thread`` is the cosmetic-vs-real choice `agentcad/toolkit/threads.py`
documents: ``cosmetic`` draws the shank at the thread's *root* diameter (fast,
light, drops into a tapped hole interference-free), ``real`` builds true ISO
helical geometry at the nominal major diameter (~9k triangles per thread).

SPECS measure the built solid against the published ISO 7380-1 table — head
diameter ``dk`` and head height ``k`` for the selected size, plus the length
under the head. The size is read off the shape, so a screw whose size cannot
be established fails rather than passing quietly.
"""

from build123d import *  # noqa: F401 — standard part-script preamble
from bd_warehouse.fastener import ButtonHeadScrew

from agentcad.toolkit.specs import check_that, check_valid

#: ISO 7380-1, mm: ``dk`` head diameter, ``k`` head height (nominal).
ISO7380 = {
    "M3-0.5": {"dk": 5.7, "k": 1.65},
    "M4-0.7": {"dk": 7.6, "k": 2.2},
    "M5-0.8": {"dk": 9.5, "k": 2.75},
    "M6-1": {"dk": 10.5, "k": 3.3},
    "M8-1.25": {"dk": 14.0, "k": 4.4},
    "M10-1.5": {"dk": 17.5, "k": 5.5},
    "M12-1.75": {"dk": 21.0, "k": 6.6},
}

TOLERANCE_MM = 0.05

PARAMS = {
    "size": {"default": "M5-0.8", "type": "enum", "choices": list(ISO7380),
             "description": "ISO metric thread designation (diameter-pitch)"},
    "length": {"default": 12.0, "min": 5.0, "max": 50.0, "unit": "mm",
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
    return ISO7380.get(getattr(part, "screw_size", None))


def _head_diameter_is_standard(part, metrics) -> bool:
    row = _row(part)
    if row is None:
        return False
    bbox = metrics["bbox"]
    measured = max(bbox["max"][0] - bbox["min"][0],
                   bbox["max"][1] - bbox["min"][1])
    return bool(abs(measured - row["dk"]) <= TOLERANCE_MM)


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
    check_valid(requirement="ISO7380-01"),
    check_that(_head_diameter_is_standard, name="head_diameter_iso7380",
               requirement="ISO7380-02"),
    check_that(_head_height_is_standard, name="head_height_iso7380",
               requirement="ISO7380-03"),
    check_that(_length_under_head_is_the_length_asked_for,
               name="length_under_head", requirement="ISO7380-04"),
]


def build(p):
    return ButtonHeadScrew(size=p.size, length=p.length,
                           fastener_type="iso7380_1",
                           simple=p.thread == "cosmetic")


def connectors(p, part):
    """``head_seat`` is the under-head bearing face (rigid); ``axis`` is the
    shank centreline pointing into the material (cylindrical)."""
    return {
        "head_seat": {"type": "rigid", "location": ((0, 0, 0), (0, 0, 0))},
        "axis": {"type": "cylindrical", "axis": ((0, 0, 0), (0, 0, -1))},
    }
