"""ISO 4762 socket-head cap screw, M3–M12, over ``agentcad.toolkit.threads``.

Origin and orientation, which are what a mate depends on: the **under-head
bearing face is at local z = 0**, the head rises to +z (to ``k``, the head
height) and the threaded shank runs down to ``z = -length``. So ``head_seat``
is the origin itself and the shank goes into the material.

``thread`` is the cosmetic-vs-real choice `agentcad/toolkit/threads.py`
documents, exposed as a parameter rather than decided for you:

* ``"cosmetic"`` — the shank is drawn as a plain cylinder. Fast (~0.05 s), light
  (~1k triangles). This is what you want for assembly views, fit checks and
  interference, and it is the default.
* ``"real"`` — true ISO helical thread geometry (~9k triangles per thread, and
  the build cost grows with the number of turns: an M3 × 60 takes ~2 s where an
  M8 × 16 takes ~0.13 s). Use it for a manufacturing drawing or when the thread
  itself has to mate.

The two are dimensionally identical outside the thread flanks — same head, same
length, same bearing face — so switching does not move a mate. The **shank
diameter** is the exception and it matters: cosmetic is drawn at the thread
*root* (⌀4.134 on M5), real reaches the nominal *major* diameter (⌀5.000). A
cosmetic screw therefore drops into a PRD-010 tapped hole (tap drill ⌀4.2)
interference-free, and a real one engages it. See docs/README.md.

SPECS check the built screw against the **published ISO 4762 table** below, not
against whatever bd_warehouse happened to produce: the head diameter ``dk``, the
head height ``k`` and the length under the head are each measured from the built
solid and compared with the standard for the selected size. A ``check_that``
predicate is handed the shape ``build`` returned, and a bd_warehouse fastener
carries its own ``screw_size`` and ``length``, so the checks know which row of
the table they are entitled to — no module-level state and no guessing.
"""

from build123d import *  # noqa: F401 — standard part-script preamble

from agentcad.toolkit import threads
from agentcad.toolkit.specs import check_that, check_valid

#: ISO 4762, dimensions in mm: ``dk`` is the head diameter (the standard's
#: **max** column, which is what a solid model of a nominal screw measures) and
#: ``k`` is the head height (max). Source: ISO 4762:2004 table 1.
ISO4762 = {
    "M3-0.5": {"d": 3.0, "dk": 5.68, "k": 3.0},
    "M4-0.7": {"d": 4.0, "dk": 7.22, "k": 4.0},
    "M5-0.8": {"d": 5.0, "dk": 8.72, "k": 5.0},
    "M6-1": {"d": 6.0, "dk": 10.22, "k": 6.0},
    "M8-1.25": {"d": 8.0, "dk": 13.27, "k": 8.0},
    "M10-1.5": {"d": 10.0, "dk": 16.27, "k": 10.0},
    "M12-1.75": {"d": 12.0, "dk": 18.27, "k": 12.0},
}

#: How far a measured dimension may sit from the table before the check fails.
#: 0.05 mm is two orders below the smallest feature here (the M3 head is
#: 5.68 mm) and an order above the tessellation-free B-rep bounding box's own
#: error, which is exact — the tolerance exists for the standard's rounding,
#: not for the kernel.
TOLERANCE_MM = 0.05

PARAMS = {
    "size": {
        "default": "M5-0.8",
        "type": "enum",
        "choices": list(ISO4762),
        "description": "ISO metric thread designation (diameter-pitch)",
    },
    "length": {
        "default": 16.0, "min": 5.0, "max": 60.0, "unit": "mm",
        "description": "Length under the head; continuous, not restricted to "
                       "the catalogue increments (the presets carry those)",
    },
    "thread": {
        "default": "cosmetic",
        "type": "enum",
        "choices": ["cosmetic", "real"],
        "description": "cosmetic = plain shank cylinder (fast, light, the "
                       "default); real = true ISO helical thread geometry "
                       "(~9k triangles, slower)",
    },
}


def _row(part):
    """The standard's row for the screw ``part`` actually is, or ``None``.

    Read off the built solid (`bd_warehouse` fasteners carry ``screw_size``),
    so a check states what the *geometry* claims to be rather than what the
    caller asked for. ``None`` fails every check below: a screw whose size we
    cannot establish has not been measured against anything.
    """
    return ISO4762.get(getattr(part, "screw_size", None))


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
    # The bearing face is the origin, so the head height is the +z extent.
    return bool(abs(metrics["bbox"]["max"][2] - row["k"]) <= TOLERANCE_MM)


def _length_under_head_is_the_length_asked_for(part, metrics) -> bool:
    length = getattr(part, "length", None)
    if not isinstance(length, (int, float)):
        return False
    return bool(abs(metrics["bbox"]["min"][2] + float(length)) <= TOLERANCE_MM)


SPECS = [
    check_valid(requirement="ISO4762-01"),
    check_that(_head_diameter_is_standard, name="head_diameter_iso4762",
               requirement="ISO4762-02"),
    check_that(_head_height_is_standard, name="head_height_iso4762",
               requirement="ISO4762-03"),
    check_that(_length_under_head_is_the_length_asked_for,
               name="length_under_head", requirement="ISO4762-04"),
]


def build(p):
    return threads.cap_screw(p.size, p.length, simple=p.thread == "cosmetic")


def connectors(p, part):
    """``head_seat`` is the under-head bearing face (rigid, so it can be the
    moving side of a mate onto a tapped or counterbored face); ``axis`` is the
    shank centreline pointing **into** the material (cylindrical, so a screw
    anchored on it keeps its spin and its depth)."""
    return {
        "head_seat": {"type": "rigid", "location": ((0, 0, 0), (0, 0, 0))},
        "axis": {"type": "cylindrical", "axis": ((0, 0, 0), (0, 0, -1))},
    }
