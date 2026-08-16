"""Mounting block — the PRD-011 publish-gate fixture.

A bored, chamfered block: real B-rep work (a through bore and a chamfer ring),
cheap enough to build a dozen variants of, and shaped so that **every**
parameter extreme is reachable — the widest bore still leaves a wall in the
narrowest block, which is what lets this package be green rather than merely
lucky.
"""

from build123d import *  # noqa: F401 — standard part-script preamble

from agentcad.toolkit.specs import check_valid, check_volume

PARAMS = {
    "length": {"default": 40.0, "min": 24.0, "max": 80.0, "unit": "mm",
               "description": "Block length along X"},
    "bore_d": {"default": 8.0, "min": 3.0, "max": 16.0, "unit": "mm",
               "description": "Through-bore diameter"},
    "grade": {"default": "std", "type": "enum", "choices": ["std", "wide"],
              "description": "Width grade; 'wide' doubles the block width"},
    "chamfered": {"default": True, "type": "bool",
                  "description": "Break the top edges"},
}

SPECS = [
    check_valid(requirement="PKG-001"),
    check_volume(min_mm3=1000.0, name="not_hollowed_out",
                 requirement="PKG-002"),
]

WIDTH = {"std": 20.0, "wide": 40.0}
HEIGHT = 16.0


def build(p):
    with BuildPart() as block:
        Box(p.length, WIDTH[p.grade], HEIGHT,
            align=(Align.CENTER, Align.CENTER, Align.MIN))
        Cylinder(radius=p.bore_d / 2.0, height=HEIGHT * 3,
                 align=(Align.CENTER, Align.CENTER, Align.CENTER),
                 mode=Mode.SUBTRACT)
        if p.chamfered:
            # The OUTER top edges only. Chamfering the bore ring as well fails
            # at `bore_d=max` in a `std` block, where the wall is 2 mm and two
            # 1 mm chamfers eat it — the gate found that, which is the whole
            # reason a package declares its extremes.
            chamfer(block.edges().group_by(Axis.Z)[-1].filter_by(GeomType.LINE),
                    length=1.0)
    return block.part


def connectors(p, part):
    """`seat` is the bottom face centre (rigid, so it can be the moving side
    of a mate); `bore` is the through-bore axis (cylindrical)."""
    return {
        "seat": {"type": "rigid", "location": ((0, 0, 0), (0, 0, 0))},
        "bore": {"type": "cylindrical",
                 "axis": ((0, 0, HEIGHT / 2.0), (0, 0, 1))},
    }
