"""Reference solution for bench task model_from_drawing/mfd_004_shaft_collar.

A one-piece clamping shaft collar: an annular ring revolved about Z, split on
the +X side by a full-height clamp slit, with one cross hole through the two
clamp lugs for the pinch screw.

The rubric lives beside this file, in ``specs/parts/shaft_collar.py``, and is
injected into every candidate — this script deliberately declares **no**
``SPECS`` of its own, so the reference is measured against exactly what every
other submission is measured against (design §1, consequence 3).
"""
from build123d import *

PARAMS = {
    "outer_d": {"default": 40.0, "min": 20.0, "max": 120.0, "unit": "mm",
                "description": "Collar outside diameter"},
    "bore_d": {"default": 20.0, "min": 6.0, "max": 100.0, "unit": "mm",
               "description": "Shaft bore diameter"},
    "length": {"default": 15.0, "min": 5.0, "max": 60.0, "unit": "mm",
               "description": "Collar length along the shaft axis (Z)"},
    "slit_w": {"default": 3.0, "min": 1.0, "max": 8.0, "unit": "mm",
               "description": "Clamp slit width, cut through the +X wall"},
    "screw_d": {"default": 5.0, "min": 2.0, "max": 12.0, "unit": "mm",
                "description": "Cross hole diameter through the clamp lugs"},
    "screw_offset": {"default": 15.0, "min": 5.0, "max": 55.0, "unit": "mm",
                     "description": "Cross hole axis distance from the collar axis"},
}


def build(p):
    outer_r = p.outer_d / 2.0
    # Guards: the bore always leaves a ring, and the pinch screw always lands
    # in the wall between bore and rim, so every parameter extreme stays
    # manifold instead of erroring.
    bore_r = max(1.0, min(p.bore_d / 2.0, outer_r - 2.0))
    screw_r = min(p.screw_d / 2.0, (outer_r - bore_r) / 2.0 - 0.5)
    offset = min(max(p.screw_offset, bore_r + screw_r + 1.0),
                 outer_r - screw_r - 1.0)

    with BuildPart() as part:
        # The ring itself: a rectangular section revolved about the shaft axis.
        with BuildSketch(Plane.XZ):
            with BuildLine():
                Polyline((bore_r, 0.0), (outer_r, 0.0),
                         (outer_r, p.length), (bore_r, p.length), close=True)
            make_face()
        revolve(axis=Axis.Z)
        # Clamp slit: a full-height slot through the +X wall. It starts on the
        # axis so it always reaches the bore, and overruns the rim and both
        # ends so the cut is a clean through-slot at every extreme.
        with Locations(((outer_r + 2.0) / 2.0, 0.0, p.length / 2.0)):
            Box(outer_r + 2.0, p.slit_w, p.length + 2.0, mode=Mode.SUBTRACT)
        # Pinch-screw cross hole, axis along Y, through both clamp lugs.
        with Locations((offset, 0.0, p.length / 2.0)):
            Cylinder(radius=screw_r, height=p.outer_d + 4.0,
                     rotation=(90, 0, 0), mode=Mode.SUBTRACT)
    return part.part
