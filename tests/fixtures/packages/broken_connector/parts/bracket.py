"""Bracket whose `connectors(p, part)` returns a **malformed axis** — AC2b.

It builds at every extreme; what it cannot do is mate. The gate's `connectors`
stage is the first server-side consumer of the kernel's `connectors` handler,
and this fixture is what proves the stage reports the connector rather than
the package as a whole.
"""

from build123d import *  # noqa: F401 — standard part-script preamble

PARAMS = {
    "leg": {"default": 30.0, "min": 15.0, "max": 60.0, "unit": "mm",
            "description": "Leg length"},
}


def build(p):
    return Box(p.leg, 20.0, 6.0)


def connectors(p, part):
    # `axis` must be a build123d Axis or ((point), (direction)); a bare string
    # is refused by the kernel's connector contract.
    return {
        "pivot": {"type": "cylindrical", "axis": "up"},
    }
