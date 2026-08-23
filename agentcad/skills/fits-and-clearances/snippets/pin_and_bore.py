"""Pin + bore sized by a named fit: ISO 286 hole basis when machined, a
process clearance when printed. The fit is a PARAM, never a hard-coded number.
"""

from build123d import *

from agentcad.toolkit import holes

PARAMS = {
    "nominal": {"default": 10.0, "min": 3.0, "max": 50.0, "unit": "mm",
                "description": "Basic diameter shared by bore and pin"},
    "fit": {"default": "sliding", "type": "enum",
            "choices": ["sliding", "locational", "transition", "press"],
            "description": "H7/g6, H7/h6, H7/k6, H7/p6 (machined) or the printed equivalent"},
    "process": {"default": "machined", "type": "enum",
                "choices": ["machined", "fdm", "sla", "sls"],
                "description": "machined = ISO 286; printed rows use process clearances"},
    "block": {"default": 30.0, "min": 10.0, "max": 120.0, "unit": "mm",
              "description": "Block width and depth"},
    "thickness": {"default": 12.0, "min": 3.0, "max": 60.0, "unit": "mm",
                  "description": "Block thickness (bore length)"},
    "pin_length": {"default": 24.0, "min": 5.0, "max": 120.0, "unit": "mm",
                   "description": "Pin length"},
}

SOLID_LABELS = ["block", "pin"]

# ISO 286-1:2010 / ISO 286-2:2010, micrometres, for nominal 3-50 mm.
_RANGES = ((3.0, 6.0), (6.0, 10.0), (10.0, 18.0), (18.0, 30.0), (30.0, 50.0))
_IT6 = (8, 9, 11, 13, 16)
_IT7 = (12, 15, 18, 21, 25)
_SHAFT = {                       # letter -> (which deviation, value per range)
    "g": ("es", (-4, -5, -6, -7, -9)),
    "h": ("es", (0, 0, 0, 0, 0)),
    "k": ("ei", (1, 1, 1, 2, 2)),
    "p": ("ei", (12, 15, 18, 22, 26)),
}
_FIT_LETTER = {"sliding": "g", "locational": "h", "transition": "k", "press": "p"}

# Printed parts: diametral clearance in mm (negative = interference).
_PRINTED = {
    "fdm": {"sliding": 0.30, "locational": 0.20, "transition": 0.05, "press": -0.12},
    "sla": {"sliding": 0.15, "locational": 0.10, "transition": 0.03, "press": -0.06},
    "sls": {"sliding": 0.30, "locational": 0.20, "transition": 0.05, "press": -0.10},
}
# As-printed hole shrink, added back to the modelled bore.
_HOLE_COMP = {"fdm": 0.20, "sla": 0.05, "sls": 0.10}


def _band(nominal):
    """Index of the ISO 286 nominal range ('over ... up to and including')."""
    for i, (lo, hi) in enumerate(_RANGES):
        if lo < nominal <= hi:
            return i
    return 0 if nominal <= _RANGES[0][1] else len(_RANGES) - 1


def _iso_sizes(nominal, fit):
    """Mid-band H7 bore and mid-band shaft diameter for the chosen fit."""
    i = _band(nominal)
    it7 = _IT7[i] / 1000.0
    it6 = _IT6[i] / 1000.0
    which, values = _SHAFT[_FIT_LETTER[fit]]
    dev = values[i] / 1000.0
    ei = dev if which == "ei" else dev - it6
    return nominal + it7 / 2.0, nominal + ei + it6 / 2.0


def _printed_sizes(nominal, fit, process):
    """Modelled bore (shrink-compensated) and pin for a printed pair."""
    return nominal + _HOLE_COMP[process], nominal - _PRINTED[process][fit]


def build(p):
    if p.process == "machined":
        bore_d, pin_d = _iso_sizes(p.nominal, p.fit)
    else:
        bore_d, pin_d = _printed_sizes(p.nominal, p.fit, p.process)

    span = max(p.block, bore_d + 12.0)   # keep >= 6 mm of wall around the bore
    with BuildPart() as blank:
        Box(span, span, p.thickness)
    block, _records, _warn = holes.drill(
        blank.part, [(0.0, 0.0)], bore_d, plane="top"
    )

    # Lead-in chamfer at the bore mouth: an assembly aid, not a fit feature.
    lead_in = min(0.6, bore_d / 12.0)
    mouth = block.edges().filter_by(GeomType.CIRCLE).group_by(Axis.Z)[-1]
    try:
        block = chamfer(mouth, length=lead_in)
    except Exception:  # OCCT refused the chamfer; the bore is still correct
        pass

    pin = Cylinder(radius=pin_d / 2.0, height=p.pin_length)
    nose = min(0.6, pin_d / 12.0)
    try:
        pin = chamfer(pin.edges().filter_by(GeomType.CIRCLE), length=nose)
    except Exception:  # noqa: BLE001 — keep the pin rather than lose the part
        pass
    pin = pin.moved(Location((span * 0.9, 0.0, p.pin_length / 2.0)))

    return Compound(children=[block, pin])
