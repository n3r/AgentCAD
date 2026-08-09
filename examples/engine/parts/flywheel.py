"""Flywheel: a stepped disc that bolts to the crankshaft's rear flange.

Local frame: the friction (clutch) face is z = 0 and the disc extends +Z.
The center bore is 32 mm — it slips over the crank's 30 mm pilot register
with 1 mm radial clearance — and the bolt pattern matches the crank flange
(six 8.5 mm holes on a 56 mm circle at the defaults). In the assembly it is
rigid-mated to the crank's ``flange`` connector, so it turns with the crank.
"""

from build123d import *

PILOT_BORE = 32.0

PARAMS = {
    "diameter": {"default": 200.0, "min": 140.0, "max": 240.0, "unit": "mm",
                 "description": "Flywheel outer diameter"},
    "thickness": {"default": 22.0, "min": 16.0, "max": 30.0, "unit": "mm",
                  "description": "Rim thickness"},
    "bolt_circle_d": {"default": 56.0, "min": 44.0, "max": 68.0, "unit": "mm",
                      "description": "Crank-bolt circle diameter (crank flange is 56)"},
    "n_bolts": {"default": 6.0, "min": 4.0, "max": 8.0, "unit": "count",
                "description": "Number of crank bolts (crank flange has 6)"},
}


def build(p):
    r = p.diameter / 2.0
    n = int(round(p.n_bolts))

    wheel = Pos(0, 0, p.thickness / 2) * Cylinder(radius=r, height=p.thickness)

    # weight-relief recess on the crank side, leaving the rim and hub full
    recess_r = (p.diameter - 50.0) / 2.0
    hub_r = max(p.bolt_circle_d / 2 + 9.0, PILOT_BORE / 2 + 12.0)
    if recess_r > hub_r + 4.0:
        ring = (Cylinder(radius=recess_r, height=4.0)
                - Cylinder(radius=hub_r, height=5.0))
        wheel -= Pos(0, 0, p.thickness - 2.0) * ring

    # counterbores so the crank-bolt heads seat below the rear face
    with BuildPart() as cbs:
        with BuildSketch(Plane.XY.offset(p.thickness + 1)):
            with PolarLocations(p.bolt_circle_d / 2, n):
                Circle(7.0)
        extrude(amount=-(p.thickness - 8.0))
    wheel -= cbs.part

    # pilot bore and crank-bolt pattern
    wheel -= Cylinder(radius=PILOT_BORE / 2, height=3 * p.thickness)
    with BuildPart() as bolts:
        with BuildSketch(Plane.XY.offset(-1)):
            with PolarLocations(p.bolt_circle_d / 2, n):
                Circle(8.5 / 2)
        extrude(amount=p.thickness + 2)
    wheel -= bolts.part

    # break the rim edges, then cut the starter ring-gear teeth
    rim = [e for e in wheel.edges().filter_by(GeomType.CIRCLE)
           if abs(e.radius - r) < 1e-6]
    wheel = chamfer(rim, length=1.5)
    with BuildPart() as teeth:
        with BuildSketch(Plane.XY.offset(p.thickness + 1)):
            with PolarLocations(r, 36):
                Rectangle(6, r / 12)
        extrude(amount=-9)
    wheel -= teeth.part
    return wheel


def connectors(p, part):
    """Friction-face center: rigid-mate this to the crank's ``flange``."""
    return {"hub": {"type": "rigid", "location": ((0, 0, 0), (0, 0, 0))}}
