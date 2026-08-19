"""Reference solution for bench task model_from_drawing/mfd_005_vee_block.

A toolmaker's vee block: a rectangular block with a 90 deg included-angle vee
groove running the full length of the top face along X, and two clamp holes
drilled through the block along Y below the groove.

The rubric lives beside this file, in ``specs/parts/vee_block.py``, and is
injected into every candidate — this script deliberately declares **no**
``SPECS`` of its own, so the reference is measured against exactly what every
other submission is measured against (design §1, consequence 3).
"""
import math

from build123d import *

PARAMS = {
    "length": {"default": 60.0, "min": 30.0, "max": 200.0, "unit": "mm",
               "description": "Block length (X), the direction the vee runs"},
    "width": {"default": 60.0, "min": 30.0, "max": 200.0, "unit": "mm",
              "description": "Block width (Y)"},
    "height": {"default": 40.0, "min": 20.0, "max": 150.0, "unit": "mm",
               "description": "Block height (Z), base on Z = 0"},
    "vee_depth": {"default": 15.0, "min": 3.0, "max": 60.0, "unit": "mm",
                  "description": "Vee groove depth below the top face"},
    "vee_angle_deg": {"default": 90.0, "min": 30.0, "max": 150.0, "unit": "deg",
                      "description": "Included angle of the vee groove"},
    "hole_d": {"default": 8.0, "min": 3.0, "max": 25.0, "unit": "mm",
               "description": "Clamp hole diameter, drilled through along Y"},
    "hole_pitch": {"default": 40.0, "min": 10.0, "max": 180.0, "unit": "mm",
                   "description": "Distance between the two clamp hole axes (X)"},
    "hole_z": {"default": 12.0, "min": 3.0, "max": 140.0, "unit": "mm",
               "description": "Clamp hole axis height above the base"},
}


def build(p):
    # Half-width of the vee at the top face. A 90 deg included angle makes
    # this equal to the depth; the parameter keeps the relation explicit
    # instead of hiding it in a literal.
    half = p.vee_depth * math.tan(math.radians(p.vee_angle_deg / 2.0))
    # Guards: the vee never eats the whole block and the clamp holes always
    # stay inside the material below it, at every parameter extreme.
    half = min(half, p.width / 2.0 - 1.0)
    depth = min(p.vee_depth, p.height - 5.0)
    hole_r = min(p.hole_d / 2.0, (p.height - depth) / 2.0 - 1.0)
    hole_z = min(max(p.hole_z, hole_r + 2.0),
                 p.height - depth - hole_r - 2.0)
    pitch = min(p.hole_pitch, p.length - 2.0 * (hole_r + 3.0))

    with BuildPart() as part:
        Box(p.length, p.width, p.height,
            align=(Align.CENTER, Align.CENTER, Align.MIN))
        # The vee, sketched in the YZ section and swept the full length. The
        # profile overruns the top face by 2 mm so the cut is clean.
        with BuildSketch(Plane.YZ):
            with BuildLine():
                Polyline((-half, p.height), (0.0, p.height - depth),
                         (half, p.height), (half, p.height + 2.0),
                         (-half, p.height + 2.0), close=True)
            make_face()
        extrude(amount=p.length / 2.0 + 1.0, both=True, mode=Mode.SUBTRACT)
        # Two clamp holes through the block along Y, below the vee.
        with Locations((pitch / 2.0, 0.0, hole_z), (-pitch / 2.0, 0.0, hole_z)):
            Cylinder(radius=hole_r, height=p.width + 4.0,
                     rotation=(90, 0, 0), mode=Mode.SUBTRACT)
    return part.part
