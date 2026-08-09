"""V4 crankshaft: two 180-degree crank pins, each shared by two rods.

Modeled along its LOCAL Z axis (three main journals, four full-circle webs,
snout, and flywheel flange); in the assembly it is revolute-mated into the
block's ``crank_axis`` connector, which stands it up along global Y. With the
crank at angle 0 pin 1 points at global +Z; pin 1 is therefore offset toward
local -Y (the mate's frame maps local -Y to global +Z at angle 0).

Fixed axial layout (local z, mirroring the block's bulkhead/bay layout):
pins centered at -+40 (36-mm-long pins leave 1.5 mm rod end float per side),
webs flush against the pin ends, main journals between/outside the webs,
snout at the front (-Z), flange + flywheel pilot at the rear (+Z).
"""

from build123d import *

PIN_SPACING = 80.0
PIN_LEN = 36.0
JOURNAL_END = 90.0        # rear main journal runs to local z = +90
FRONT_JOURNAL_END = 87.0  # front journal stops short of the timing cover
SNOUT_LEN = 24.0          # long enough to carry the damper past the cover
SNOUT_D = 28.0
FLANGE_T = 6.0
FLANGE_D = 70.0
PILOT_D = 30.0         # flywheel register, slips into the flywheel's 32-bore
PILOT_LEN = 6.0
BOLT_N = 6
BOLT_D = 8.5
BOLT_BC = 56.0

PARAMS = {
    "stroke": {"default": 60.0, "min": 40.0, "max": 80.0, "unit": "mm",
               "description": "Piston stroke; crank pins orbit at stroke/2"},
    "pin_d": {"default": 40.0, "min": 30.0, "max": 48.0, "unit": "mm",
              "description": "Crank-pin (rod bearing) journal diameter"},
    "journal_d": {"default": 45.0, "min": 36.0, "max": 56.0, "unit": "mm",
                  "description": "Main-bearing journal diameter (block bore is 45.6)"},
    "web_t": {"default": 12.0, "min": 8.0, "max": 16.0, "unit": "mm",
              "description": "Crank web (cheek) thickness"},
}


def build(p):
    r = p.stroke / 2.0
    web_r = r + p.pin_d / 2.0 + 2.0  # full-circle webs always cover the pin
    pin_half = PIN_LEN / 2.0
    wt = p.web_t

    crank = None

    def fuse(shape):
        nonlocal crank
        crank = shape if crank is None else crank + shape

    def cyl(radius, z0, z1, y_off=0.0):
        return Pos(0, y_off, (z0 + z1) / 2) * Cylinder(radius=radius,
                                                       height=z1 - z0)

    for pc, y_sign in ((-PIN_SPACING / 2, -1.0), (PIN_SPACING / 2, +1.0)):
        # pin, then a counterweighted web flush against each pin end: a lobe
        # (full circle trimmed on the pin side) plus a round cheek that
        # always covers the pin circle
        fuse(cyl(p.pin_d / 2, pc - pin_half, pc + pin_half, y_off=y_sign * r))
        for z0 in (pc - pin_half - wt, pc + pin_half):
            lobe = cyl(web_r, z0, z0 + wt)
            trim = Pos(0, y_sign * (web_r + 26.0), (z0 + z0 + wt) / 2) * Box(
                2 * web_r + 20, 2 * web_r, wt + 2)
            lobe -= trim
            fuse(lobe)
            fuse(cyl(p.pin_d / 2 + 8.0, z0, z0 + wt, y_off=y_sign * r))

    # main journals: center (between the inner webs) and both ends
    inner = PIN_SPACING / 2 - pin_half - wt   # inner web faces at -+inner
    fuse(cyl(p.journal_d / 2, -inner, inner))
    outer = PIN_SPACING / 2 + pin_half + wt   # outer web faces at -+outer
    fuse(cyl(p.journal_d / 2, -FRONT_JOURNAL_END, -outer))
    fuse(cyl(p.journal_d / 2, outer, JOURNAL_END))

    # snout (pulley end) and flywheel flange with its pilot register
    fuse(cyl(SNOUT_D / 2, -FRONT_JOURNAL_END - SNOUT_LEN, -FRONT_JOURNAL_END))
    fuse(cyl(FLANGE_D / 2, JOURNAL_END, JOURNAL_END + FLANGE_T))
    fuse(cyl(PILOT_D / 2, JOURNAL_END + FLANGE_T,
             JOURNAL_END + FLANGE_T + PILOT_LEN))

    # pulley keyway in the snout
    crank -= Pos(0, SNOUT_D / 2 - 1, -FRONT_JOURNAL_END - 12) * Box(6, 4, 18)

    # flywheel bolt pattern through the flange
    with BuildPart() as holes:
        with BuildSketch(Plane.XY.offset(JOURNAL_END - 1)):
            with PolarLocations(BOLT_BC / 2, BOLT_N):
                Circle(BOLT_D / 2)
        extrude(amount=FLANGE_T + 2)
    crank -= holes.part

    # break the snout tip edge
    tip = crank.edges().filter_by(GeomType.CIRCLE).group_by(Axis.Z)[0]
    crank = chamfer(tip, length=1.5)
    return crank


def connectors(p, part):
    """``hub`` is the moving-side frame for the revolute mate into the block
    (identity: the mate frame IS the crank's local frame). ``flange`` carries
    the flywheel 0.5 mm off the flange face — and, being on the crank, spins
    the flywheel with it. ``snout`` is a spare seat for a future pulley."""
    z_flange = JOURNAL_END + FLANGE_T
    return {
        "hub": {"type": "rigid", "location": ((0, 0, 0), (0, 0, 0))},
        "flange": {"type": "rigid", "location": ((0, 0, z_flange + 1.0),
                                                 (0, 0, 0))},
        "snout": {"type": "rigid",
                  "location": ((0, 0, -FRONT_JOURNAL_END - SNOUT_LEN),
                               (0, 0, 0))},
    }
