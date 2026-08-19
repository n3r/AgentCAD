"""Reference solution for bench task fix_the_broken_part/fix_001_contract.

The corrected script. Two defects separate it from ``starter/``:

* ``PARAMS`` declared the plate thickness as ``"thicknes"`` while ``build``
  read ``p.thickness``, so the very first use of the parameter raised
  ``AttributeError`` — the part never reached OCCT at all.
* ``build`` fell off the end without returning the shape, so even a
  spelling-clean run handed the worker ``None``.

The rubric lives beside this file, in ``specs/parts/sensor_mount.py``, and is
injected into every candidate — this script deliberately declares **no**
``SPECS`` of its own (design §1, consequence 3).
"""
from build123d import *

PARAMS = {
    "plate_l": {"default": 60.0, "min": 30.0, "max": 120.0, "unit": "mm",
                "description": "Plate length (X)"},
    "plate_w": {"default": 40.0, "min": 20.0, "max": 100.0, "unit": "mm",
                "description": "Plate width (Y)"},
    "thickness": {"default": 5.0, "min": 2.0, "max": 15.0, "unit": "mm",
                  "description": "Plate thickness"},
    "corner_r": {"default": 5.0, "min": 0.0, "max": 15.0, "unit": "mm",
                 "description": "Corner break on the four vertical edges"},
    "boss_d": {"default": 16.0, "min": 8.0, "max": 30.0, "unit": "mm",
               "description": "Sensor boss outer diameter"},
    "boss_h": {"default": 12.0, "min": 4.0, "max": 30.0, "unit": "mm",
               "description": "Boss height above the plate's top face"},
    "bore_d": {"default": 8.0, "min": 3.0, "max": 20.0, "unit": "mm",
               "description": "Sensor bore through the boss and the plate"},
    "hole_d": {"default": 5.0, "min": 2.0, "max": 12.0, "unit": "mm",
               "description": "Mounting hole diameter"},
    "hole_dx": {"default": 44.0, "min": 10.0, "max": 100.0, "unit": "mm",
                "description": "Mounting hole spacing along X"},
}


def build(p):
    with BuildPart() as part:
        Box(p.plate_l, p.plate_w, p.thickness,
            align=(Align.CENTER, Align.CENTER, Align.MIN))
        if p.corner_r > 0.05:
            fillet(part.edges().filter_by(Axis.Z), radius=p.corner_r)
        with Locations((0, 0, p.thickness)):
            Cylinder(radius=p.boss_d / 2, height=p.boss_h,
                     align=(Align.CENTER, Align.CENTER, Align.MIN))
        with Locations((0, 0, 0)):
            Cylinder(radius=p.bore_d / 2, height=p.thickness + p.boss_h,
                     align=(Align.CENTER, Align.CENTER, Align.MIN),
                     mode=Mode.SUBTRACT)
        with Locations((p.hole_dx / 2, 0, 0), (-p.hole_dx / 2, 0, 0)):
            Cylinder(radius=p.hole_d / 2, height=p.thickness,
                     align=(Align.CENTER, Align.CENTER, Align.MIN),
                     mode=Mode.SUBTRACT)
    return part.part
