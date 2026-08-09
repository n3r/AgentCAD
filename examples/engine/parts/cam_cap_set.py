"""Cam-cap set: three bearing caps + their six M5 screws, one compound.

Local frame matches the head. Each cap closes one cam saddle from above
(joint face z = 62, half-bore over the journal), screwed down into the
saddle's tapped holes. Modeled as one compound per head so the assembly
carries every fastener without exploding the instance count.
"""

from build123d import *

from agentcad.toolkit import threads

SADDLE_YS = (-31.0, 0.0, 31.0)
CAP_X = 25.5
BOLT_XS = (10.0, 40.0)
CAM_Z = 62.0

PARAMS = {
    "bore_d": {"default": 22.2, "min": 20.0, "max": 26.0, "unit": "mm",
               "description": "Cam bearing bore (matches the head saddles)"},
    "height": {"default": 14.0, "min": 11.0, "max": 18.0, "unit": "mm",
               "description": "Cap height above the joint face"},
}


def build(p):
    screw = threads.cap_screw("M5-0.8", 16.0, simple=True)
    parts = None

    def fuse(s):
        nonlocal parts
        parts = s if parts is None else parts + s

    for ty in SADDLE_YS:
        cap = Pos(25.5, ty, CAM_Z + p.height / 2 + 0.05) * Box(
            37, 8, p.height)
        cap -= Pos(24, ty, CAM_Z) * Rot(X=-90) * Cylinder(
            radius=p.bore_d / 2, height=12)
        for bx in BOLT_XS:
            cap -= Pos(bx, ty, CAM_Z + p.height / 2) * Cylinder(
                radius=2.7, height=p.height + 2)
            cap -= Pos(bx, ty, CAM_Z + p.height - 2.4) * Cylinder(
                radius=4.8, height=5.2)
        fuse(cap)
        for bx in BOLT_XS:
            fuse(Pos(bx, ty, CAM_Z + p.height - 4.85) * screw)
    return parts
