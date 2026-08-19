# Copied from examples/fasteners/parts/clamp_plate.py for bench task
# optimize_under_constraints/opt_005_shortest_screw. A derived task copies the
# script INTO the bundle: the runner registers no examples, so a run can never
# read the answer, and the starter and the reference are the SAME scripts at
# different parameters — the task is an optimisation over one parameter, not a
# rewrite. The rubric is injected from ../../../specs/, so this script declares
# no SPECS of its own.
"""Clamp plate: the top member of the bolted joint.

A square plate with a plain bolt *clearance* hole (no thread): the cap screw
drops through it and threads into the tapped base plate below. Corners are
filleted with the robust ``safe_fillet`` helper.
"""

from build123d import *

from agentcad.toolkit import safe_fillet

PARAMS = {
    "size": {"default": 40.0, "min": 24.0, "max": 150.0, "unit": "mm",
             "description": "Square plate side length (X and Y)"},
    "thickness": {"default": 8.0, "min": 4.0, "max": 40.0, "unit": "mm",
                  "description": "Clamp plate thickness"},
    "clearance_d": {"default": 9.0, "min": 8.5, "max": 14.0, "unit": "mm",
                    "description": "Bolt clearance hole diameter (an M8 shank clears it)"},
}


def build(p):
    corner_r = min(4.0, p.size / 8.0)
    # Keep at least a 3 mm ring of material around the clearance hole.
    hole_r = min(p.clearance_d / 2.0, p.size / 2.0 - 3.0)

    with BuildPart() as part:
        # Aligned MIN so the plate spans z = 0 .. thickness and stacks straight
        # onto the tapped plate's top face (which sits at z = 0).
        Box(p.size, p.size, p.thickness,
            align=(Align.CENTER, Align.CENTER, Align.MIN))
        with Locations(part.faces().sort_by(Axis.Z)[-1]):
            Hole(radius=hole_r)

    result, _radius, _warn = safe_fillet(
        part.part, part.part.edges().filter_by(Axis.Z).group_by(SortBy.LENGTH)[-1],
        radius=corner_r,
    )
    return result
