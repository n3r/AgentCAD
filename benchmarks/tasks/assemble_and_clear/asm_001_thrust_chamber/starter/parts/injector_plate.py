# Copied from examples/rocketry/parts/injector_plate.py into this project.
"""Injector plate: showerhead orifice ring around a center igniter boss.

A circular plate with a polar pattern of small propellant orifices at a
parametric pitch radius, and a raised center boss carrying the igniter
through-hole.  The orifice ring is clamped internally so it always clears
the boss fillet and the rim chamfer, whatever the parameter extremes.
"""

from build123d import *

PARAMS = {
    "plate_d": {"default": 132.0, "min": 40.0, "max": 300.0, "unit": "mm",
                "description": "Injector plate outer diameter"},
    "plate_t": {"default": 12.0, "min": 4.0, "max": 40.0, "unit": "mm",
                "description": "Injector plate thickness"},
    "n_orifices": {"default": 24.0, "min": 6.0, "max": 60.0, "unit": "count",
                   "description": "Number of injector orifices in the ring"},
    "orifice_d": {"default": 1.5, "min": 0.5, "max": 4.0, "unit": "mm",
                  "description": "Injector orifice diameter"},
    "pattern_r": {"default": 38.0, "min": 10.0, "max": 140.0, "unit": "mm",
                  "description": "Orifice ring pitch radius (auto-clamped to fit)"},
    "igniter_d": {"default": 6.0, "min": 2.0, "max": 12.0, "unit": "mm",
                  "description": "Center igniter port diameter"},
}

BOSS_WALL = 5.0  # radial wall around the igniter port
BOSS_H = 6.0     # igniter boss height above the plate face


def build(p):
    plate_r = p.plate_d / 2.0
    ign_r = p.igniter_d / 2.0
    boss_r = ign_r + BOSS_WALL
    orif_r = p.orifice_d / 2.0
    # Guard: keep the orifice ring off the boss fillet (inner bound) and
    # inside the rim chamfer (outer bound); outer bound wins if they cross.
    r_pat = max(p.pattern_r, boss_r + orif_r + 3.0)
    r_pat = min(r_pat, plate_r - orif_r - 1.5)
    n = int(round(p.n_orifices))

    with BuildPart() as part:
        Cylinder(plate_r, p.plate_t,
                 align=(Align.CENTER, Align.CENTER, Align.MIN))
        with Locations((0, 0, p.plate_t)):
            Cylinder(boss_r, BOSS_H,
                     align=(Align.CENTER, Align.CENTER, Align.MIN))
        # fillet where the boss meets the plate face
        fillet(
            part.edges().filter_by(GeomType.CIRCLE).filter_by(
                lambda e: abs(e.radius - boss_r) < 1e-4
                and abs(e.arc_center.Z - p.plate_t) < 1e-4
            ),
            radius=1.5,
        )
        # propellant orifices
        with PolarLocations(radius=r_pat, count=n):
            Hole(radius=orif_r)
        # igniter port through boss and plate
        Hole(radius=ign_r)
        # chamfer the boss top rim and the igniter lead-in
        chamfer(part.edges().filter_by(GeomType.CIRCLE).group_by(Axis.Z)[-1],
                length=0.8)
        # break the plate rim edges
        chamfer(
            part.edges().filter_by(GeomType.CIRCLE).filter_by(
                lambda e: abs(e.radius - plate_r) < 1e-4
            ),
            length=1.0,
        )
    return part.part


def connectors(p, part):
    """The chamber-side interface is the bottom face (local z = 0, since the
    plate is aligned MIN at z = 0). Rigid so it can be the moving side of a
    mate to the nozzle's ``injector_seat``."""
    return {"bottom": {"type": "rigid", "location": (0, 0, 0)}}
