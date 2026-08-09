"""Intake manifold: a valley log plenum with four swept runners.

Built in the ENGINE frame (instance sits at the origin, unrotated): the
plenum log runs along Y above the V, one curved runner sweeps down to each
head's intake flange, and a throttle body hangs off the front of the log.
Each runner ends in a flange plate whose stud holes match the head's
17 mm-pitch stud pattern (the plate slides over the studs with clearance and
floats 0.5 mm off the head's boss face — the gasket).

Geometry couples to the head/block defaults through the constants below;
see the README coupling table before re-dimensioning the engine.
"""

import math

from build123d import *

C45 = math.cos(math.radians(45.0))    # default bank half-angle
SEAT_S = 168.4                        # deck + head gasket (block defaults)
HEAD_W = 100.0
PORT_Z = 21.0                         # port center above the gasket face
BOSS_LEN = 10.0                       # head intake boss protrusion
ROD_OFFSET = 9.0
STUD_PITCH = 17.0
STUD_HOLE_R = 4.75                    # slides over the heads' 8 mm studs
PLENUM_L = 140.0                      # log length along Y
FLANGE_T = 8.0

PARAMS = {
    "runner_d": {"default": 30.0, "min": 24.0, "max": 34.0, "unit": "mm",
                 "description": "Runner tube outer diameter"},
    "plenum_d": {"default": 56.0, "min": 48.0, "max": 64.0, "unit": "mm",
                 "description": "Plenum log outer diameter"},
    "plenum_height": {"default": 195.0, "min": 188.0, "max": 215.0, "unit": "mm",
                      "description": "Plenum axis height above the crank axis"},
    "tb_d": {"default": 48.0, "min": 40.0, "max": 56.0, "unit": "mm",
             "description": "Throttle body bore housing diameter"},
}

# runners: (bank sign, world y). Bank A ports at y=-49/+31, bank B mirrored.
RUNNERS = [(+1, -49.0), (-1, -31.0), (+1, 31.0), (-1, 49.0)]


def build(p):
    # world XZ of the bank-A intake boss tip: seat + R_y(45) . (-60, 0, PORT_Z)
    tip_x = (SEAT_S - (HEAD_W / 2 + BOSS_LEN) + PORT_Z) * C45
    tip_z = (SEAT_S + (HEAD_W / 2 + BOSS_LEN) + PORT_Z) * C45
    n = (-C45, C45)                    # outward normal of the bank-A port
    d = (C45, C45)                     # bank-A axis direction in XZ

    manifold = Pos(0, 0, p.plenum_height) * Rot(X=-90) * Cylinder(
        radius=p.plenum_d / 2, height=PLENUM_L)

    for sgn, ry in RUNNERS:
        sx = sgn  # mirror bank B in X
        face = ((tip_x + 0.5 * n[0]) * sx, ry, tip_z + 0.5 * n[1])
        p0 = ((tip_x + FLANGE_T * n[0]) * sx, ry, tip_z + FLANGE_T * n[1])
        p1 = ((tip_x + (FLANGE_T + 10) * n[0]) * sx, ry,
              tip_z + (FLANGE_T + 10) * n[1])
        p2 = (sx * (p.plenum_d / 2 + 2), ry, p.plenum_height)
        p3 = (sx * (p.plenum_d / 2 - 14), ry, p.plenum_height)
        tan1 = (sx * n[0], 0, n[1])
        with BuildPart() as runner:
            with BuildLine():
                Line(face, p1)
                TangentArc(p1, p2, tangent=tan1)
                Line(p2, p3)
            with BuildSketch(Plane(origin=face, z_dir=tan1)):
                Circle(p.runner_d / 2)
            sweep()
        manifold += runner.part

        # flange plate over the head studs, with its clearance holes
        fc = (face[0] + 4 * sx * n[0], ry, face[2] + 4 * n[1])
        manifold += Pos(*fc) * Rot(Y=sx * 45) * Box(FLANGE_T, 50, 50)
        for oy in (-STUD_PITCH, STUD_PITCH):
            for od in (-STUD_PITCH, STUD_PITCH):
                hc = (fc[0] + od * sx * d[0], ry + oy, fc[2] + od * d[1])
                manifold -= Pos(*hc) * Rot(Y=sx * 45) * Rot(Y=90) * Cylinder(
                    radius=STUD_HOLE_R, height=FLANGE_T + 2)

    # throttle body on the front of the log: housing, inlet flange, shaft
    tb_y0 = -PLENUM_L / 2
    manifold += Pos(0, tb_y0 - 14, p.plenum_height) * Rot(X=-90) * Cylinder(
        radius=p.tb_d / 2, height=36)
    manifold += Pos(0, tb_y0 - 33, p.plenum_height) * Rot(X=-90) * Cylinder(
        radius=p.tb_d / 2 + 6, height=6)
    manifold -= Pos(0, tb_y0 - 24, p.plenum_height) * Rot(X=-90) * Cylinder(
        radius=p.tb_d / 2 - 5, height=40)
    manifold += Pos(0, tb_y0 - 20, p.plenum_height) * Rot(Y=90) * Cylinder(
        radius=4, height=p.tb_d + 16)  # butterfly shaft stubs

    # stepped end caps close the log
    for ey, edir in ((PLENUM_L / 2, 1), (-PLENUM_L / 2, -1)):
        manifold += Pos(0, ey + edir * 2, p.plenum_height) * Rot(X=-90) * \
            Cylinder(radius=p.plenum_d / 2 - 5, height=4)
    return manifold
