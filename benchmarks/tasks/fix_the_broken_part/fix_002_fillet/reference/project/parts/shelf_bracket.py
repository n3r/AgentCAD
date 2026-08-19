"""Reference solution for bench task fix_the_broken_part/fix_002_fillet.

The corrected script. The shipped one hard-coded the end break as
``p.thk * 2`` — 12 mm on a 6 mm leg — and OCCT refused the whole build with
``Failed creating a fillet with radius of 12.0``. Two things change:

* the break radius comes from ``edge_r`` (4 mm, the drawing value) instead of
  an expression that ignored the parameter the script already declares;
* the break goes through ``toolkit.fillet.safe_fillet``, which binary-searches
  the largest radius OCCT will accept rather than failing the build outright,
  so an over-large value degrades into a smaller fillet and a warning instead
  of an empty part.

The rubric lives beside this file, in ``specs/parts/shelf_bracket.py``, and is
injected into every candidate — this script deliberately declares **no**
``SPECS`` of its own (design §1, consequence 3).
"""
from build123d import *

from agentcad.toolkit.fillet import safe_fillet

PARAMS = {
    "leg_a": {"default": 70.0, "min": 30.0, "max": 150.0, "unit": "mm",
              "description": "Horizontal leg length (X)"},
    "leg_b": {"default": 55.0, "min": 30.0, "max": 150.0, "unit": "mm",
              "description": "Upright leg length (Z)"},
    "width": {"default": 40.0, "min": 20.0, "max": 120.0, "unit": "mm",
              "description": "Bracket width (Y)"},
    "thk": {"default": 6.0, "min": 3.0, "max": 20.0, "unit": "mm",
            "description": "Leg thickness"},
    "heel_r": {"default": 8.0, "min": 0.0, "max": 30.0, "unit": "mm",
               "description": "Inside heel fillet radius"},
    "edge_r": {"default": 4.0, "min": 0.0, "max": 30.0, "unit": "mm",
               "description": "Break radius on the two free leg ends"},
}


def build(p):
    with BuildPart() as part:
        with BuildSketch(Plane.XZ):
            with BuildLine():
                Polyline((0, 0), (p.leg_a, 0), (p.leg_a, p.thk),
                         (p.thk, p.thk), (p.thk, p.leg_b), (0, p.leg_b),
                         close=True)
            make_face()
        extrude(amount=-p.width)
        inner = part.edges().filter_by(Axis.Y).filter_by(
            lambda e: abs(e.center().X - p.thk) < 1e-6
            and abs(e.center().Z - p.thk) < 1e-6)
        fillet(inner, radius=p.heel_r)
    shape = part.part
    tips = shape.edges().filter_by(Axis.Y).filter_by(
        lambda e: abs(e.center().X - p.leg_a) < 1e-6
        or abs(e.center().Z - p.leg_b) < 1e-6)
    shape, _achieved, _warning = safe_fillet(shape, tips, p.edge_r)
    return shape
