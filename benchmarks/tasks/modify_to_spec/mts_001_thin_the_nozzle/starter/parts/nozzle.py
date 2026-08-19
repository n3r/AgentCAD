# Copied from examples/rocketry/parts/nozzle.py into this project.
"""Liquid rocket engine thrust chamber: revolved chamber/throat/bell contour.

The inner contour is a cylindrical combustion chamber, a tangent-arc
converging section blending into the throat, and a 15 deg conical
(near-bell) diverging section whose exit area is throat area times the
expansion ratio.  The wall is a constant radial offset of the inner
contour, revolved as one closed profile about the engine axis (Z).
"""

import math

from build123d import *

PARAMS = {
    "chamber_d": {"default": 80.0, "min": 40.0, "max": 200.0, "unit": "mm",
                  "description": "Combustion chamber inner diameter"},
    "chamber_l": {"default": 100.0, "min": 40.0, "max": 300.0, "unit": "mm",
                  "description": "Cylindrical combustion chamber length"},
    "throat_d": {"default": 30.0, "min": 10.0, "max": 120.0, "unit": "mm",
                 "description": "Throat inner diameter (kept below 90% of chamber)"},
    "expansion_ratio": {"default": 4.9, "min": 1.5, "max": 25.0, "unit": "ratio",
                        "description": "Nozzle area ratio: exit area / throat area"},
    "wall": {"default": 3.0, "min": 1.0, "max": 10.0, "unit": "mm",
             "description": "Chamber and nozzle wall thickness (radial)"},
}

DIV_HALF_ANGLE_DEG = 15.0  # conical diverging section half-angle


def build(p):
    rc = p.chamber_d / 2.0  # chamber inner radius
    wall = p.wall
    # Guard: the throat must stay well inside the chamber so the converging
    # section always has a positive radius change.
    rt = max(2.0, min(p.throat_d / 2.0, 0.9 * rc))
    # Exit radius from the area ratio: A_e = A_t * eps  =>  r_e = r_t * sqrt(eps)
    re_ = rt * math.sqrt(max(p.expansion_ratio, 1.0))
    lc = p.chamber_l
    dr = rc - rt
    # Converging length >= radius drop keeps the tangent arc monotonic
    # (sweep < 90 deg), i.e. an effective convergence half-angle <= 45 deg.
    l_conv = max(1.25 * dr, 2.0)
    l_div = max((re_ - rt) / math.tan(math.radians(DIV_HALF_ANGLE_DEG)), 0.5)
    z_throat = -(lc + l_conv)
    z_exit = z_throat - l_div

    with BuildPart() as part:
        with BuildSketch(Plane.XZ):
            with BuildLine():
                # inner contour, top (injector interface, z=0) to exit
                Line((rc, 0), (rc, -lc))
                TangentArc((rc, -lc), (rt, z_throat), tangent=(0, -1))
                Line((rt, z_throat), (re_, z_exit))
                # exit lip
                Line((re_, z_exit), (re_ + wall, z_exit))
                # outer contour, exit back up to the top rim
                Line((re_ + wall, z_exit), (rt + wall, z_throat))
                TangentArc((rc + wall, -lc), (rt + wall, z_throat), tangent=(0, -1))
                Line((rc + wall, -lc), (rc + wall, 0))
                # top rim closes the profile
                Line((rc + wall, 0), (rc, 0))
            make_face()
        revolve(axis=Axis.Z)
        # break the sharp exit-lip edges (scaled to wall so extremes stay valid)
        lip = part.edges().filter_by(GeomType.CIRCLE).group_by(Axis.Z)[0]
        chamfer(lip, length=0.2 * wall)
    return part.part


def connectors(p, part):
    """Named rigid connectors for assembly mates. The engine axis is Z and the
    injector-interface rim sits at z = 0 by construction, so the nozzle is the
    assembly datum: the flange and injector plate are each rigid-mated to it.

    A rigid connector is just a local frame (here a pure translation); mating
    makes the moving part's connector frame coincide with this one, so these
    two seats reproduce the deliberate stacked-face clearances exactly."""
    return {
        # flange slips over the barrel from just below the rim; its top face
        # lands 0.2 mm below the rim (a seal/gasket allowance)
        "flange_seat": {"type": "rigid", "location": (0, 0, -0.2)},
        # injector plate caps the stack 0.2 mm above the rim
        "injector_seat": {"type": "rigid", "location": (0, 0, 0.2)},
    }
