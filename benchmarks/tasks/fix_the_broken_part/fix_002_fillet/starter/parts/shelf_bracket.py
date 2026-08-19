"""Shelf bracket -- AS SHIPPED, AND IT DOES NOT BUILD.

An L bracket: a 70 mm horizontal leg and a 55 mm upright, 40 mm wide and
6 mm thick, with a filleted inside heel and a break on both free leg ends.
"""
from build123d import *

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
        tips = part.edges().filter_by(Axis.Y).filter_by(
            lambda e: abs(e.center().X - p.leg_a) < 1e-6
            or abs(e.center().Z - p.leg_b) < 1e-6)
        fillet(tips, radius=p.thk * 2)
    return part.part
