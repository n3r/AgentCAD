"""90-degree V4 engine block: two banks of two cylinders on a common crankcase.

Global frame (shared by the whole engine project): the crank axis is Y at the
origin, the V opens upward in the XZ plane, bank A tilts +bank_angle/2 from Z
(toward +X) and bank B tilts the same the other way. Fixed layout constants
below (pin spacing, rod offset, bulkheads) are shared with the crankshaft,
rod, and piston scripts — see the project README for the coupling table.

Casting realism on top of the core solid: crankcase stiffening ribs, engine
mount pads, core (freeze) plugs on the bank faces, head-bolt holes matching
the heads' pattern, a rear bellhousing flange with its bolt circle, and a
drilled pan rail. The oil-filter boss face on the -X side matches the
oil_filter part; the front face takes the timing cover.
"""

import math

from build123d import *

from agentcad.toolkit import safe_fillet

# --- shared layout constants (see README: "one crankshaft of numbers") ---
PIN_SPACING = 80.0     # crank pin centers at y = -40, +40
ROD_OFFSET = 9.0       # each rod sits this far from its pin center along Y
COMP_HEIGHT = 28.0     # piston pin center -> crown top (piston default)
CRANKCASE_R = 76.0     # crankcase barrel outer radius
CAVITY_R = 64.0        # crankcase bay (inner) radius (clears rod-bolt sweep)
PAN_RAIL_Z = -55.0     # pan-rail plane (block bottom face)
MAIN_BORE_D = 45.6     # main-bearing bore (crank journal 45.0 + 0.6 clearance)
BAY_SPAN = (8.0, 74.0)  # each crankcase bay spans this |y| range
BULKHEAD_YS = (-81.0, 0.0, 81.0)   # bulkhead centers (main_cap instances too)
CAP_WINDOW_W = 76.0    # saddle window width; main caps are 75.5 (0.25/side)
CAP_BOLT_X = 28.0      # main-cap bolt columns (tapped up into the bulkheads)
BORE_FLOOR = 56.0      # cylinder bores are cut from this far off the crank axis
DECK_WALL = 6.0        # end wall beyond the outermost bore edge
HEAD_BOLT_X = 36.0     # head-bolt pattern half-spacing across the bank
HEAD_BOLT_YS = (-15.5, 15.5)       # pattern along the bank (head-local)
DOWEL_PTS = ((30.0, 65.0), (-30.0, -65.0))  # deck dowels, head-local (x, y)
GASKET_T = 0.9         # head sits deck + 0.9: 0.8 gasket + 0.05 per face
BELL_OD = 240.0        # bellhousing flange
BELL_ID = 170.0
PAN_BOLT_X = 80.5      # pan-rail bolt columns
PAN_BOLT_YS = (-70.0, -25.0, 25.0, 70.0)
FRONT_BOLT_PTS = ((-68, -40), (0, -44), (68, -40), (68, 0), (-68, 0),
                  (-52, 52), (-26, 90), (24, 90))  # = timing cover BOSS_PTS
FRONT_DOWEL_PTS = ((50.0, -20.0), (-50.0, -20.0))

PARAMS = {
    "bore": {"default": 66.0, "min": 50.0, "max": 78.0, "unit": "mm",
             "description": "Cylinder bore diameter (piston diameter + 0.6 clearance)"},
    "stroke": {"default": 60.0, "min": 40.0, "max": 80.0, "unit": "mm",
               "description": "Crank stroke; sets the deck height with rod_length"},
    "rod_length": {"default": 110.0, "min": 90.0, "max": 130.0, "unit": "mm",
                   "description": "Connecting-rod center distance; sets the deck height"},
    "bank_angle": {"default": 90.0, "min": 60.0, "max": 100.0, "unit": "deg",
                   "description": "Included angle between the two cylinder banks"},
}

# cylinder centers: (bank sign, y). Bank A (+) rods lead their pin along -Y,
# bank B (-) rods trail along +Y, so the banks are offset by 2*ROD_OFFSET.
def _cylinders():
    p1, p2 = -PIN_SPACING / 2, PIN_SPACING / 2
    return [(+1, p1 - ROD_OFFSET), (-1, p1 + ROD_OFFSET),
            (+1, p2 - ROD_OFFSET), (-1, p2 + ROD_OFFSET)]


def build(p):
    half = p.bank_angle / 2.0
    deck_s = p.stroke / 2.0 + p.rod_length + COMP_HEIGHT  # crank axis -> deck
    slab_w = p.bore + 24.0
    y_face = PIN_SPACING / 2 + ROD_OFFSET + p.bore / 2 + DECK_WALL
    block_l = 2 * y_face

    # crankcase barrel (axis = Y) + pan-rail flange
    block = Rot(X=-90) * Cylinder(radius=CRANKCASE_R, height=block_l)
    block += Pos(0, 0, PAN_RAIL_Z + 3.0) * Box(170, block_l, 6)

    # bank slabs, from just above the crank bays up to the deck faces
    slab_s0 = 60.0
    for sgn in (+1, -1):
        slab = Pos(0, 0, (slab_s0 + deck_s) / 2) * Box(
            slab_w, block_l, deck_s - slab_s0)
        block += Rot(Y=sgn * half) * slab

    # crankcase stiffening ribs, radial plates low on the barrel. The -X rows
    # stop short of the oil-filter boss (the canister needs the clear face).
    for x_sgn, rows in ((+1, (-60, -36, -12, 12, 36, 60)),
                        (-1, (-60, -36, -12, 12))):
        for ry in rows:
            block += Pos(x_sgn * 72.5, ry, -30) * Box(13, 4, 40)

    # engine-mount pads (one only on -X: the oil filter needs the clear face)
    for x_sgn, mount_ys in ((+1, (-24.0, 24.0)), (-1, (-24.0,))):
        for my in mount_ys:
            pad = Pos(x_sgn * 82, my, -18) * Box(20, 26, 26)
            block += pad

    # oil-filter boss: a round pad on the -X barrel face (filter part mates
    # its canister 0.5 mm off this face)
    block += Pos(-79.5, 50, -18.75) * Rot(Y=-90) * Cylinder(radius=29, height=13)

    # bellhousing flange on the rear face, trimmed at the pan rail line
    ring = Cylinder(radius=BELL_OD / 2, height=8)
    ring -= Cylinder(radius=BELL_ID / 2, height=10)
    with BuildPart() as bell_holes:
        with BuildSketch(Plane.XY.offset(-5)):
            with PolarLocations((BELL_ID + BELL_OD) / 4 + 5, 8):
                Circle(5.5)
        extrude(amount=10)
    ring -= bell_holes.part
    ring = Pos(0, y_face + 4.5, 0) * Rot(X=-90) * ring
    ring -= Pos(0, y_face + 4.5, -50 - 100) * Box(300, 20, 200)
    block += ring

    # core (freeze) plugs: shallow discs proud of each bank's outboard face
    for sgn in (+1, -1):
        for py in (-40.0, 0.0, 40.0):
            plug = Pos(sgn * slab_w / 2, py, 100.0) * Rot(Y=90) * Cylinder(
                radius=15, height=5)
            block += Rot(Y=sgn * half) * plug

    # carve the two crankcase bays, leaving three main bulkheads standing
    lo, hi = BAY_SPAN
    for y0, y1 in ((-hi, -lo), (lo, hi)):
        bay = Pos(0, (y0 + y1) / 2, 0) * Rot(X=-90) * Cylinder(
            radius=CAVITY_R, height=y1 - y0)
        block -= bay

    # line-bore the main bearings through all three bulkheads
    block -= Rot(X=-90) * Cylinder(radius=MAIN_BORE_D / 2, height=block_l + 60)

    # open the saddles: cut a cap window below the crank centerline in each
    # bulkhead so the crankshaft drops in from underneath and the main caps
    # (separate parts) bolt up into the tapped holes left beside each saddle
    for yc, bw in zip(BULKHEAD_YS, (15.0, 17.0, 15.0)):
        block -= Pos(0, yc, -30) * Box(CAP_WINDOW_W, bw, 60)
        for bx in (-CAP_BOLT_X, CAP_BOLT_X):
            block -= Pos(bx, yc, 9) * Cylinder(radius=4.2, height=20)

    # deck dowel pins (the head gasket and head locate on these) — bank B's
    # head is R_z(180)-turned, so its dowels mirror through the deck center
    for sgn in (+1, -1):
        for hx, hy in DOWEL_PTS:
            t, y = sgn * hx, sgn * (hy - ROD_OFFSET)
            pin = Pos(t, y, deck_s + 1.0) * Cylinder(radius=3.0, height=10.0)
            block += Rot(Y=sgn * half) * pin

    # front face: tapped holes matching the timing cover's bolt bosses, and
    # two dowel pins the cover slides onto
    for bx, bz in FRONT_BOLT_PTS:
        block -= Pos(bx, -y_face + 6, bz) * Rot(X=-90) * Cylinder(
            radius=2.7, height=16)
    for dx, dz in FRONT_DOWEL_PTS:
        block += Pos(dx, -y_face - 2.5, dz) * Rot(X=-90) * Cylinder(
            radius=3.0, height=7.0)

    # cylinder bores, cut deep enough that piston skirts and rods run free
    for sgn, cy in _cylinders():
        bore_cut = Pos(0, cy, (BORE_FLOOR + deck_s + 1) / 2) * Cylinder(
            radius=p.bore / 2, height=deck_s + 1 - BORE_FLOOR)
        block -= Rot(Y=sgn * half) * bore_cut

    # head-bolt holes down each deck, matching the heads' 2x3 pattern
    bolt_x = min(HEAD_BOLT_X, slab_w / 2 - 5.0)
    for sgn in (+1, -1):
        off = -ROD_OFFSET if sgn > 0 else ROD_OFFSET
        for bx in (-bolt_x, bolt_x):
            for by in HEAD_BOLT_YS:
                hole = Pos(bx, by + off, deck_s - 17) * Cylinder(
                    radius=5.2, height=38)
                block -= Rot(Y=sgn * half) * hole

    # pan-rail bolt holes through the flange
    bolt_px = PAN_BOLT_X
    for bx in (-bolt_px, bolt_px):
        for by in PAN_BOLT_YS:
            block -= Pos(bx, by, PAN_RAIL_Z + 3) * Cylinder(radius=3.5,
                                                            height=10)

    # open the bottom at the pan rail
    block -= Pos(0, 0, PAN_RAIL_Z - 100) * Box(400, block_l + 60, 200)

    # soften the pan-rail flange corners (clamped if a size can't take it)
    corners = [e for e in block.edges().filter_by(Axis.Z)
               if abs(e.center().X) > 70 and e.center().Z < PAN_RAIL_Z + 7
               and abs(e.center().Y) > y_face - 12]
    if corners:
        block, _r, _warn = safe_fillet(block, corners, radius=10.0)
    return block


def connectors(p, part):
    """Mate seats derived from the same math as the geometry, so mated parts
    follow the block when bore/stroke/rod_length/bank_angle change. The heads
    and pan sit 0.4 mm off their faces (a gasket allowance); the crank axis is
    a revolute connector that ``set_mate``'s angle_deg drives."""
    half = p.bank_angle / 2.0
    th = math.radians(half)
    seat_s = p.stroke / 2.0 + p.rod_length + COMP_HEIGHT + GASKET_T
    return {
        "head_a_seat": {"type": "rigid",
                        "location": ((seat_s * math.sin(th), -ROD_OFFSET,
                                      seat_s * math.cos(th)), (0, half, 0))},
        # bank B's seat carries a 180-degree turn about the deck normal: the
        # V4 layout is symmetric under R_z(180), and only that pose puts the
        # head's intake ports toward the valley on both banks.
        "head_b_seat": {"type": "rigid",
                        "location": ((-seat_s * math.sin(th), ROD_OFFSET,
                                      seat_s * math.cos(th)),
                                     (0, -half, 180))},
        "pan_rail": {"type": "rigid",
                     "location": ((0, 0, PAN_RAIL_Z - 0.4), (0, 0, 0))},
        "crank_axis": {"type": "revolute", "axis": ((0, 0, 0), (0, 1, 0))},
    }
