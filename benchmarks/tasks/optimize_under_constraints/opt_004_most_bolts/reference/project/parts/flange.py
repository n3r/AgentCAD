# Copied from examples/rocketry/parts/flange.py for bench task
# optimize_under_constraints/opt_004_most_bolts. A derived task copies the
# script INTO the bundle: the runner registers no examples, so a run can never
# read the answer, and the starter and the reference are the SAME script at
# different parameters — the task is an optimisation over the parameters, not a
# rewrite. The example's own SPECS block (INT-003) and its check_wall import
# are STRIPPED here — the rubric is injected from ../../../specs/parts/flange.py
# and the reference is measured against exactly what every other submission is
# measured against (design section 1).
"""Chamber-head interface flange: annular ring with a bolt circle.

Slips over the combustion chamber barrel and gives the injector plate a
bolted interface.  The bore and bolt circle are clamped internally so the
ring always keeps material between bore, bolt holes, and rim at any
parameter extreme.
"""

from build123d import *

PARAMS = {
    "outer_d": {"default": 140.0, "min": 60.0, "max": 400.0, "unit": "mm",
                "description": "Flange outer diameter"},
    "inner_d": {"default": 87.0, "min": 20.0, "max": 380.0, "unit": "mm",
                "description": "Bore diameter (clears the chamber barrel)"},
    "flange_t": {"default": 14.0, "min": 5.0, "max": 50.0, "unit": "mm",
                 "description": "Flange thickness"},
    "n_bolts": {"default": 8.0, "min": 4.0, "max": 24.0, "unit": "count",
                "description": "Number of bolts on the bolt circle"},
    "bolt_d": {"default": 9.0, "min": 3.0, "max": 20.0, "unit": "mm",
               "description": "Bolt clearance hole diameter"},
    "bolt_circle_d": {"default": 118.0, "min": 30.0, "max": 380.0, "unit": "mm",
                      "description": "Bolt circle diameter (auto-clamped to fit)"},
}

def build(p):
    outer_r = p.outer_d / 2.0
    # Guard: keep at least a 12 mm annular ring
    bore_r = max(5.0, min(p.inner_d / 2.0, outer_r - 12.0))
    bolt_r = p.bolt_d / 2.0
    # Guard: bolt circle between bore and rim; the rim bound wins if crossed
    bc_r = max(p.bolt_circle_d / 2.0, bore_r + bolt_r + 2.0)
    bc_r = min(bc_r, outer_r - bolt_r - 2.0)
    n = int(round(p.n_bolts))

    with BuildPart() as part:
        Cylinder(outer_r, p.flange_t,
                 align=(Align.CENTER, Align.CENTER, Align.MIN))
        Hole(radius=bore_r)
        with PolarLocations(radius=bc_r, count=n):
            Hole(radius=bolt_r)
        # break the outer rim edges
        chamfer(
            part.edges().filter_by(GeomType.CIRCLE).filter_by(
                lambda e: abs(e.radius - outer_r) < 1e-4
            ),
            length=min(1.5, p.flange_t / 4.0),
        )
        # lead-in on the bore edges
        chamfer(
            part.edges().filter_by(GeomType.CIRCLE).filter_by(
                lambda e: abs(e.radius - bore_r) < 1e-4
            ),
            length=min(0.6, p.flange_t / 4.0),
        )
    return part.part


def connectors(p, part):
    """The injector-side interface is the top face (local z = flange_t, since
    the ring is aligned MIN at z = 0). Rigid so it can be the moving side of a
    mate to the nozzle's ``flange_seat``."""
    return {"top": {"type": "rigid", "location": (0, 0, p.flange_t)}}
