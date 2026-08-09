"""Intake manifold: log plenum, four swept runners, one bolted flange per bank.

Built in the ENGINE frame (instance at the origin). What makes it a
manifold rather than stacked primitives:

- one full-length flange PLATE per bank (not a floating pad per runner),
  drilled with all eight stud clearance holes, seated 0.5 mm off the head
  bosses over the heads' real studs — the ``intake_nut_set`` clamps it;
- port bores drilled through the flange into each runner, so the casting
  is open where a gas path belongs;
- runners that leave the flange along the port normal and sweep tangent
  into the plenum through cast socket collars;
- the throttle body is a separate part bolted to the plenum's front flange
  (4 tapped holes + spigot register), like the real article.

Geometry couples to the head/block defaults through the constants below.
"""

import math

from build123d import *

C45 = math.cos(math.radians(45.0))
SEAT_S = 168.9                        # deck + 0.9 head-gasket stack
HEAD_W = 100.0
PORT_Z = 21.0
BOSS_LEN = 10.0
STUD_PITCH = 17.0
STUD_HOLE_R = 4.75
PLENUM_L = 140.0
FLANGE_T = 8.0
PORT_YS_A = (-49.0, 31.0)             # bank A ports; bank B mirrors in x, y
TB_BOLT_BC = 33.0                     # throttle flange bolt circle radius

PARAMS = {
    "runner_d": {"default": 30.0, "min": 24.0, "max": 34.0, "unit": "mm",
                 "description": "Runner tube outer diameter"},
    "plenum_d": {"default": 60.0, "min": 50.0, "max": 66.0, "unit": "mm",
                 "description": "Plenum log outer diameter"},
    "plenum_height": {"default": 192.0, "min": 188.0, "max": 212.0, "unit": "mm",
                      "description": "Plenum axis height above the crank axis"},
    "port_bore": {"default": 24.0, "min": 18.0, "max": 28.0, "unit": "mm",
                  "description": "Gas-path bore drilled through flange and runner"},
}


def build(p):
    # world XZ of a bank-A intake boss tip and the port normal / bank axis
    tip_x = (SEAT_S - (HEAD_W / 2 + BOSS_LEN) + PORT_Z) * C45
    tip_z = (SEAT_S + (HEAD_W / 2 + BOSS_LEN) + PORT_Z) * C45
    n = (-C45, C45)
    d = (C45, C45)

    manifold = Pos(0, 0, p.plenum_height) * Rot(X=-90) * Cylinder(
        radius=p.plenum_d / 2, height=PLENUM_L)

    for sx in (+1, -1):
        # one flange plate per bank, over both ports and all eight studs
        fc = (sx * (tip_x + (FLANGE_T / 2 + 0.5) * n[0]), -sx * 9.0,
              tip_z + (FLANGE_T / 2 + 0.5) * n[1])
        manifold += Pos(*fc) * Rot(Y=sx * 45) * Box(FLANGE_T, 132, 52)

        for ry_a in PORT_YS_A:
            ry = sx * ry_a if sx > 0 else -ry_a
            face = (sx * (tip_x + 0.5 * n[0]), ry, tip_z + 0.5 * n[1])
            p1 = (sx * (tip_x + (FLANGE_T + 10) * n[0]), ry,
                  tip_z + (FLANGE_T + 10) * n[1])
            p2 = (sx * (p.plenum_d / 2 + 2), ry, p.plenum_height)
            p3 = (sx * (p.plenum_d / 2 - 16), ry, p.plenum_height)
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

            # cast socket collar where the runner meets the plenum
            collar_at = (sx * (p.plenum_d / 2 + 4), ry, p.plenum_height)
            manifold += Pos(*collar_at) * Rot(Y=sx * 90) * Cylinder(
                radius=p.runner_d / 2 + 4, height=8)

            # stud clearance holes, then the gas-path bore into the runner
            for oy in (-STUD_PITCH, STUD_PITCH):
                for od in (-STUD_PITCH, STUD_PITCH):
                    hc = (face[0] + 4 * sx * n[0] + od * sx * d[0], ry + oy,
                          face[2] + 4 * n[1] + od * d[1])
                    manifold -= Pos(*hc) * Rot(Y=sx * 45) * Rot(Y=90) * \
                        Cylinder(radius=STUD_HOLE_R, height=FLANGE_T + 2)
            bore_c = (face[0] + 14 * sx * n[0], ry, face[2] + 14 * n[1])
            manifold -= Pos(*bore_c) * Rot(Y=sx * 45) * Rot(Y=90) * Cylinder(
                radius=p.port_bore / 2, height=30)

    # front flange for the separate throttle body: disc, spigot bore,
    # four tapped holes on the bolt circle
    tb_y = -PLENUM_L / 2
    manifold += Pos(0, tb_y - 2, p.plenum_height) * Rot(X=-90) * Cylinder(
        radius=41.0, height=8)
    manifold -= Pos(0, tb_y - 4, p.plenum_height) * Rot(X=-90) * Cylinder(
        radius=17.0, height=40)
    for k in range(4):
        a = math.radians(45 + k * 90)
        hx = TB_BOLT_BC * math.cos(a)
        hz = p.plenum_height + TB_BOLT_BC * math.sin(a)
        manifold -= Pos(hx, tb_y - 3, hz) * Rot(X=-90) * Cylinder(
            radius=2.7, height=14)

    # stepped end cap closes the rear of the log
    manifold += Pos(0, PLENUM_L / 2 + 2, p.plenum_height) * Rot(X=-90) * \
        Cylinder(radius=p.plenum_d / 2 - 5, height=4)
    return manifold
