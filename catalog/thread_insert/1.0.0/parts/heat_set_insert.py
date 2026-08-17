"""Heat-set threaded insert (ruthex catalogue): M2, M2.5, M3, M4, M6.

The knurled brass insert you melt into a 3D-printed boss so a plastic part can
take a real machine screw. Modelled from bd_warehouse's `HeatSetNut` at the
ruthex catalogue's dimensions.

**Five sizes, and the gap is real.** These are the ruthex rows the pinned
bd_warehouse 0.3.0 populates: its `HeatSetNut.fastener_data` carries M5
designations with every ruthex field empty, so there is **no M5 to build**, and
the M3 and M4 rows are ruthex's *short* 4.0 mm inserts (`GE-M3Sx40-002`,
`GE-M4Sx04-1`) — which is what the `-4.0` in the designation says. The sixth
populated row is a Voron variant of the short M3 and is deliberately not
offered.

Origin and orientation: the insert sits **from z = 0 up to z = h**, so ``seat``
is the flange face that ends flush with the printed surface and ``axis`` is
the bore centreline pointing **into** the boss (−z), which is the direction the
insert is pressed and the direction the screw enters from.

The size designation encodes three things — ``M<d>-<pitch>-<h>`` — so the
insert's own height is readable from its name. One of the SPECS below uses
exactly that: the built solid's height must equal the height its designation
claims, which is a check no separate table can drift away from.

Boss sizing, the number a consumer actually needs: drill or print the boss bore
at the insert's outer diameter **minus about 0.1–0.2 mm** and give it at least
1.5 mm of wall. The table is in docs/README.md.
"""

from build123d import *  # noqa: F401 — standard part-script preamble
from bd_warehouse.fastener import HeatSetNut

from agentcad.toolkit.specs import check_that, check_valid

#: ruthex heat-set inserts, mm: ``od`` is the knurled outer diameter and ``h``
#: the overall height. The height is also the last field of the designation.
#: Every row is a populated ruthex row of ``HeatSetNut.fastener_data``.
RUTHEX = {
    "M2-0.4-4": {"od": 3.6, "h": 4.0},
    "M2.5-0.45-5.7": {"od": 4.6, "h": 5.7},
    "M3-0.5-4.0": {"od": 4.6, "h": 4.0},
    "M4-0.7-4.0": {"od": 6.3, "h": 4.0},
    "M6-1-6.8": {"od": 8.7, "h": 6.8},
}

TOLERANCE_MM = 0.05

PARAMS = {
    "size": {"default": "M3-0.5-4.0", "type": "enum", "choices": list(RUTHEX),
             "description": "ruthex designation: thread-pitch-height"},
    "thread": {"default": "cosmetic", "type": "enum",
               "choices": ["cosmetic", "real"],
               "description": "cosmetic = plain bore (fast, light, the "
                              "default); real = true ISO helical thread "
                              "geometry in the bore (~9k triangles, slower)"},
}


def _designation(part):
    return getattr(part, "nut_size", None) or getattr(part, "size", None)


def _outer_diameter_is_catalogue(part, metrics) -> bool:
    row = RUTHEX.get(_designation(part))
    if row is None:
        return False
    bbox = metrics["bbox"]
    measured = max(bbox["max"][0] - bbox["min"][0],
                   bbox["max"][1] - bbox["min"][1])
    return bool(abs(measured - row["od"]) <= TOLERANCE_MM)


def _height_matches_its_designation(part, metrics) -> bool:
    """``M3-0.5-4.0`` is 4.0 mm tall. Read the claim out of the name and
    measure it — no table to drift."""
    name = _designation(part)
    if not isinstance(name, str) or name.count("-") != 2:
        return False
    try:
        declared = float(name.rsplit("-", 1)[1])
    except ValueError:
        return False
    bbox = metrics["bbox"]
    return bool(abs((bbox["max"][2] - bbox["min"][2]) - declared)
                <= TOLERANCE_MM)


def _sits_on_the_origin_plane(part, metrics) -> bool:
    return bool(abs(metrics["bbox"]["min"][2]) <= TOLERANCE_MM)


SPECS = [
    check_valid(requirement="INSERT-01"),
    check_that(_outer_diameter_is_catalogue, name="outer_diameter_ruthex",
               requirement="INSERT-02"),
    check_that(_height_matches_its_designation, name="height_matches_size",
               requirement="INSERT-03"),
    check_that(_sits_on_the_origin_plane, name="seats_on_z0",
               requirement="INSERT-04"),
]


def build(p):
    return HeatSetNut(size=p.size, fastener_type="ruthex",
                      simple=p.thread == "cosmetic")


def connectors(p, part):
    """``seat`` is the flange face that ends flush with the printed surface
    (rigid, so it can be the moving side of a mate onto a boss); ``axis`` is
    the bore centreline pointing into the boss."""
    return {
        "seat": {"type": "rigid", "location": ((0, 0, 0), (0, 0, 0))},
        "axis": {"type": "cylindrical", "axis": ((0, 0, 0), (0, 0, -1))},
    }
