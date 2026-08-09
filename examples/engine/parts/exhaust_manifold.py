"""Exhaust manifold for one bank: hollow swept primaries into a hollow
collector with an open tail — a welded-tube header, not stacked primitives.

Built in the ENGINE frame for bank A (+X side, ports at y = -49 and +31);
the second instance serves bank B by a 180-degree turn about Z. Each
primary is an annulus sweep (a real tube); the collector and tail cone are
shelled; the gas channels are swept as solids, proud of the flange faces,
and subtracted last — so flange port, primary bore, collector chamber, and
tail all connect. Flange plates slide over the heads' studs (17 mm
pattern, 0.5 mm gasket float) and are clamped by ``exhaust_nut_set``.
"""

import math

from build123d import *

from agentcad.toolkit import safe_fillet

C45 = math.cos(math.radians(45.0))
SEAT_S = 168.9
HEAD_W = 100.0
PORT_Z = 21.0
BOSS_LEN = 18.0
STUD_PITCH = 17.0
STUD_HOLE_R = 4.75
FLANGE_T = 8.0
PORT_YS = (-49.0, 31.0)
COLLECT_X = 215.0
WALL = 3.5

PARAMS = {
    "primary_d": {"default": 34.0, "min": 28.0, "max": 38.0, "unit": "mm",
                  "description": "Primary tube outer diameter"},
    "collector_d": {"default": 60.0, "min": 52.0, "max": 68.0, "unit": "mm",
                    "description": "Collector barrel outer diameter"},
    "drop": {"default": -10.0, "min": -25.0, "max": 0.0, "unit": "mm",
             "description": "Collector axis height (z) relative to the crank axis"},
    "tail_d": {"default": 42.0, "min": 34.0, "max": 48.0, "unit": "mm",
               "description": "Tail stub outer diameter"},
}


def build(p):
    tip_x = (SEAT_S + (HEAD_W / 2 + BOSS_LEN) + PORT_Z) * C45
    tip_z = (SEAT_S - (HEAD_W / 2 + BOSS_LEN) + PORT_Z) * C45
    n = (C45, -C45)
    d = (C45, C45)
    r_out = p.primary_d / 2
    r_in = r_out - WALL
    col_r = p.collector_d / 2

    def path_pts(ry):
        face = (tip_x + 0.5 * n[0], ry, tip_z + 0.5 * n[1])
        p1 = (tip_x + (FLANGE_T + 15) * n[0], ry, tip_z + (FLANGE_T + 15) * n[1])
        p2 = (COLLECT_X, ry, p.drop + 27.0)
        tan1 = (n[0], 0, n[1])
        return face, p1, p2, tan1

    # hollow collector barrel (front cap kept) + shelled tail cone, open end
    manifold = Pos(COLLECT_X, -6.0, p.drop) * Rot(X=-90) * Cylinder(
        radius=col_r, height=100)
    manifold += Pos(COLLECT_X, 58.0, p.drop) * Rot(X=-90) * Cone(
        bottom_radius=col_r - 2, top_radius=p.tail_d / 2, height=36)
    manifold -= Pos(COLLECT_X, -4.0, p.drop) * Rot(X=-90) * Cylinder(
        radius=col_r - WALL, height=96)
    manifold -= Pos(COLLECT_X, 62.0, p.drop) * Rot(X=-90) * Cone(
        bottom_radius=col_r - 2 - WALL, top_radius=p.tail_d / 2 - WALL,
        height=36)
    manifold -= Pos(COLLECT_X, 70.0, p.drop) * Rot(X=-90) * Cylinder(
        radius=p.tail_d / 2 - WALL, height=24)

    for ry in PORT_YS:
        face, p1, p2, tan1 = path_pts(ry)
        with BuildPart() as tube:
            with BuildLine():
                Line(face, p1)
                TangentArc(p1, p2, tangent=tan1)
            with BuildSketch(Plane(origin=face, z_dir=tan1)):
                Circle(r_out)
                Circle(r_in, mode=Mode.SUBTRACT)
            sweep()
        manifold += tube.part

        # flange plate with stud clearance holes
        fc = (face[0] + 4 * n[0], ry, face[2] + 4 * n[1])
        manifold += Pos(*fc) * Rot(Y=45) * Box(FLANGE_T, 50, 50)
        for oy in (-STUD_PITCH, STUD_PITCH):
            for od in (-STUD_PITCH, STUD_PITCH):
                hc = (fc[0] + od * d[0], ry + oy, fc[2] + od * d[1])
                manifold -= Pos(*hc) * Rot(Y=45) * Rot(Y=90) * Cylinder(
                    radius=STUD_HOLE_R, height=FLANGE_T + 2)

    # gas channels: solid sweeps, proud of the flanges, ending inside the
    # collector chamber — subtracted last so every passage connects
    for ry in PORT_YS:
        face, p1, p2, tan1 = path_pts(ry)
        start = (face[0] - 3 * n[0], ry, face[2] - 3 * n[1])
        end = (COLLECT_X, ry, p.drop + 2.0)
        with BuildPart() as chan:
            with BuildLine():
                Line(start, p1)
                TangentArc(p1, p2, tangent=tan1)
                Line(p2, end)
            with BuildSketch(Plane(origin=start, z_dir=tan1)):
                Circle(r_in)
            sweep()
        manifold -= chan.part

    # blend the primary/collector junctions. Below ~Ø30 the junction edge
    # geometry provokes an OCCT fillet crash (not a catchable failure), so
    # the blend degrades to a plain intersection at small tube sizes.
    if p.primary_d >= 30.0:
        for ry in PORT_YS:
            near = [e for e in manifold.edges()
                    if (abs(e.center().Y - ry) < 14
                        and abs(e.center().X - COLLECT_X) < 20
                        and abs(e.center().Z - (p.drop + col_r)) < 16)]
            if near:
                manifold, _r, _warn = safe_fillet(manifold, near, radius=3.0)
    return manifold
