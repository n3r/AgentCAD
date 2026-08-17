"""Strut that builds at its default and **breaks at `length=max`** — AC2a.

The failure is deliberate and deterministic: the point of the fixture is that
a package can look fine at its default and be wrong at an extreme nobody
tried, which is the whole reason the gate sweeps the declared range rather
than trusting the default.
"""

from build123d import *  # noqa: F401 — standard part-script preamble

PARAMS = {
    "length": {"default": 40.0, "min": 20.0, "max": 90.0, "unit": "mm",
               "description": "Strut length along X"},
}


def build(p):
    if p.length >= 90.0:
        raise ValueError(
            "the strut buckles above 80 mm: this family cannot be built at "
            "its own declared maximum"
        )
    return Box(p.length, 12.0, 8.0)


def connectors(p, part):
    return {"end": {"type": "rigid", "location": ((0, 0, 0), (0, 0, 0))}}
