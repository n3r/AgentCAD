# Copied from examples/engine/parts/wrist_pin.py for bench task
# assemble_and_clear/asm_005_rod_and_piston.
# A derived task copies the script INTO the bundle: the runner registers no
# examples, so a run can never read the answer.
# The rubric is injected from the bundle's specs/, so this script declares no
# SPECS of its own.
"""Floating wrist pin: slides through piston boss - rod small end - boss.

Local frame: the pin axis is Y through the origin (the same axis its piston
instance uses), hollow like a real pin, ends chamfered for insertion. The
piston's bores run pin + 0.1 and the rod's small end pin + 0.25, so the pin
genuinely assembles: piston over rod, pin pushed through from either side.
"""

from build123d import *

PARAMS = {
    "diameter": {"default": 18.0, "min": 14.0, "max": 22.0, "unit": "mm",
                 "description": "Pin outer diameter (piston/rod bores add clearance)"},
    "length": {"default": 48.0, "min": 40.0, "max": 54.0, "unit": "mm",
               "description": "Pin length (stays inside the piston diameter)"},
    "bore": {"default": 10.0, "min": 6.0, "max": 13.0, "unit": "mm",
             "description": "Hollow core diameter"},
}


def build(p):
    bore = min(p.bore, p.diameter - 5.0)
    pin = Rot(X=-90) * Cylinder(radius=p.diameter / 2, height=p.length)
    pin -= Rot(X=-90) * Cylinder(radius=bore / 2, height=p.length + 2)
    ends = pin.edges().filter_by(GeomType.CIRCLE).filter_by(
        lambda e: abs(e.radius - p.diameter / 2) < 1e-6)
    return chamfer(ends, length=1.2)
