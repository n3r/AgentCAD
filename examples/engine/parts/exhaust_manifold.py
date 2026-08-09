"""Exhaust manifold for one bank: two swept primaries into a collector.

Built in the ENGINE frame for bank A (+X side, ports at y = -49 and +31);
the second instance serves bank B by a 180-degree rotation about Z — the V4
layout is exactly symmetric under that turn, so one script dresses both
banks (bank B's collector then exits over the front, like a transverse
engine's front bank).

Each primary starts in a flange plate that slides over the head's exhaust
studs (17 mm pattern, 0.5 mm gasket float), dives outboard-down along the
port normal, and turns down into a common collector with a tail stub.
Coupled to the head/block defaults through the constants below.
"""

import math

from build123d import *

C45 = math.cos(math.radians(45.0))
SEAT_S = 168.9          # deck + 0.9 head-gasket stack
HEAD_W = 100.0
PORT_Z = 21.0
BOSS_LEN = 18.0                       # head exhaust boss protrusion
STUD_PITCH = 17.0
STUD_HOLE_R = 4.75
FLANGE_T = 8.0
PORT_YS = (-49.0, 31.0)
COLLECT_X = 215.0

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
    # world XZ of the bank-A exhaust boss tip: seat + R_y(45) . (+68, 0, PORT_Z)
    tip_x = (SEAT_S + (HEAD_W / 2 + BOSS_LEN) + PORT_Z) * C45
    tip_z = (SEAT_S - (HEAD_W / 2 + BOSS_LEN) + PORT_Z) * C45
    n = (C45, -C45)                    # outward normal (outboard-down)
    d = (C45, C45)                     # bank-A axis direction in XZ

    manifold = None
    for ry in PORT_YS:
        face = (tip_x + 0.5 * n[0], ry, tip_z + 0.5 * n[1])
        p1 = (tip_x + (FLANGE_T + 15) * n[0], ry, tip_z + (FLANGE_T + 15) * n[1])
        p2 = (COLLECT_X, ry, p.drop + 25.0)
        p3 = (COLLECT_X, ry, p.drop - 5.0)
        tan1 = (n[0], 0, n[1])
        with BuildPart() as primary:
            with BuildLine():
                Line(face, p1)
                TangentArc(p1, p2, tangent=tan1)
                Line(p2, p3)
            with BuildSketch(Plane(origin=face, z_dir=tan1)):
                Circle(p.primary_d / 2)
            sweep()
        tube = primary.part

        # flange plate with stud clearance holes
        fc = (face[0] + 4 * n[0], ry, face[2] + 4 * n[1])
        tube += Pos(*fc) * Rot(Y=45) * Box(FLANGE_T, 50, 50)
        for oy in (-STUD_PITCH, STUD_PITCH):
            for od in (-STUD_PITCH, STUD_PITCH):
                hc = (fc[0] + od * d[0], ry + oy, fc[2] + od * d[1])
                tube -= Pos(*hc) * Rot(Y=45) * Rot(Y=90) * Cylinder(
                    radius=STUD_HOLE_R, height=FLANGE_T + 2)
        # gas-path bore through the flange into the primary
        bc = (face[0] + 12 * n[0], ry, face[2] + 12 * n[1])
        tube -= Pos(*bc) * Rot(Y=45) * Rot(Y=90) * Cylinder(
            radius=p.primary_d / 2 - 4, height=26)
        manifold = tube if manifold is None else manifold + tube

    # collector barrel swallowing both primary ends, plus the tail stub
    manifold += Pos(COLLECT_X, -6.0, p.drop) * Rot(X=-90) * Cylinder(
        radius=p.collector_d / 2, height=100)
    manifold += Pos(COLLECT_X, 58.0, p.drop) * Rot(X=-90) * Cone(
        bottom_radius=p.collector_d / 2 - 2, top_radius=p.tail_d / 2,
        height=36)
    manifold -= Pos(COLLECT_X, 70.0, p.drop) * Rot(X=-90) * Cylinder(
        radius=p.tail_d / 2 - 3, height=20)

    # break the tail lip
    lip = manifold.edges().filter_by(GeomType.CIRCLE).group_by(Axis.Y)[-1]
    manifold = chamfer(lip, length=1.0)
    return manifold
