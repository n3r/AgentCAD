"""Intake manifold: a hollow casting — plenum shell, tubular runners, one
bolted flange per bank, with a continuous gas path you can see through.

Built in the ENGINE frame (instance at the origin). Construction order is
what makes it a real part instead of stacked primitives:

1. the plenum is a SHELL (4 mm wall, capped ends), not a solid log;
2. each runner is an annulus swept along its path — a tube, hollow along
   its whole length;
3. the gas channels are then swept as solids along the same paths, extended
   proud of the flange faces, and SUBTRACTED — opening flange port,
   runner bore, and plenum wall into one continuous passage;
4. the runner/plenum junctions are blended with fillets (clamped by
   safe_fillet when a size can't take them) — no fake collar discs;
5. the throttle body is a separate part bolted to the tapped front flange.

Geometry couples to the head/block defaults through the constants below.
"""

import math

from build123d import *

from agentcad.toolkit import safe_fillet

C45 = math.cos(math.radians(45.0))
SEAT_S = 168.9
HEAD_W = 100.0
PORT_Z = 21.0
BOSS_LEN = 10.0
STUD_PITCH = 17.0
STUD_HOLE_R = 4.75
PLENUM_L = 140.0
FLANGE_T = 8.0
PORT_YS_A = (-49.0, 31.0)
TB_BOLT_BC = 33.0
WALL = 4.0

PARAMS = {
    "runner_d": {"default": 30.0, "min": 24.0, "max": 34.0, "unit": "mm",
                 "description": "Runner tube outer diameter"},
    "plenum_d": {"default": 60.0, "min": 50.0, "max": 66.0, "unit": "mm",
                 "description": "Plenum log outer diameter"},
    "plenum_height": {"default": 192.0, "min": 188.0, "max": 212.0, "unit": "mm",
                      "description": "Plenum axis height above the crank axis"},
    "port_bore": {"default": 23.0, "min": 18.0, "max": 27.0, "unit": "mm",
                  "description": "Gas-channel bore through flange, runner, and wall"},
}


def build(p):
    tip_x = (SEAT_S - (HEAD_W / 2 + BOSS_LEN) + PORT_Z) * C45
    tip_z = (SEAT_S + (HEAD_W / 2 + BOSS_LEN) + PORT_Z) * C45
    n = (-C45, C45)
    d = (C45, C45)
    r_out = p.runner_d / 2
    r_in = min(p.port_bore, p.runner_d - 5.0) / 2
    pl_r = p.plenum_d / 2

    # plenum shell with capped ends
    manifold = Pos(0, 0, p.plenum_height) * Rot(X=-90) * Cylinder(
        radius=pl_r, height=PLENUM_L)
    manifold -= Pos(0, 0, p.plenum_height) * Rot(X=-90) * Cylinder(
        radius=pl_r - WALL, height=PLENUM_L - 8)

    def path_pts(sx, ry):
        face = (sx * (tip_x + 0.5 * n[0]), ry, tip_z + 0.5 * n[1])
        p1 = (sx * (tip_x + (FLANGE_T + 10) * n[0]), ry,
              tip_z + (FLANGE_T + 10) * n[1])
        p2 = (sx * (pl_r + 2), ry, p.plenum_height)
        p3 = (sx * (pl_r - 16), ry, p.plenum_height)
        tan1 = (sx * n[0], 0, n[1])
        return face, p1, p2, p3, tan1

    junctions = []
    for sx in (+1, -1):
        # one flange plate per bank, over both ports and all eight studs
        fc = (sx * (tip_x + (FLANGE_T / 2 + 0.5) * n[0]), -sx * 9.0,
              tip_z + (FLANGE_T / 2 + 0.5) * n[1])
        manifold += Pos(*fc) * Rot(Y=sx * 45) * Box(FLANGE_T, 132, 52)

        for ry_a in PORT_YS_A:
            ry = sx * ry_a
            face, p1, p2, p3, tan1 = path_pts(sx, ry)
            with BuildPart() as tube:
                with BuildLine():
                    Line(face, p1)
                    TangentArc(p1, p2, tangent=tan1)
                    Line(p2, p3)
                with BuildSketch(Plane(origin=face, z_dir=tan1)):
                    Circle(r_out)
                    Circle(r_in, mode=Mode.SUBTRACT)
                sweep()
            manifold += tube.part
            junctions.append((sx * pl_r, ry, p.plenum_height))

            # stud clearance holes through the flange
            for oy in (-STUD_PITCH, STUD_PITCH):
                for od in (-STUD_PITCH, STUD_PITCH):
                    hc = (face[0] + 4 * sx * n[0] + od * sx * d[0], ry + oy,
                          face[2] + 4 * n[1] + od * d[1])
                    manifold -= Pos(*hc) * Rot(Y=sx * 45) * Rot(Y=90) * \
                        Cylinder(radius=STUD_HOLE_R, height=FLANGE_T + 2)

    # gas channels: solid sweeps along the same paths, proud of the flange,
    # subtracted last — they open flange, runner, and plenum wall through
    for sx in (+1, -1):
        for ry_a in PORT_YS_A:
            ry = sx * ry_a
            face, p1, p2, p3, tan1 = path_pts(sx, ry)
            start = (face[0] - 3 * sx * n[0], ry, face[2] - 3 * n[1])
            with BuildPart() as chan:
                with BuildLine():
                    Line(start, p1)
                    TangentArc(p1, p2, tangent=tan1)
                    Line(p2, p3)
                with BuildSketch(Plane(origin=start, z_dir=tan1)):
                    Circle(r_in)
                sweep()
            manifold -= chan.part

    # front flange for the separate throttle body: disc, spigot bore into
    # the plenum interior, four tapped holes on the bolt circle
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

    # stepped rear end cap detail
    manifold += Pos(0, PLENUM_L / 2 + 2, p.plenum_height) * Rot(X=-90) * \
        Cylinder(radius=pl_r - 5, height=4)

    # blend the runner/plenum junctions — a cast neck, not an intersection
    for jx, jy, jz in junctions:
        near = [e for e in manifold.edges()
                if (abs(e.center().Y - jy) < 14
                    and abs(e.center().Z - jz) < 24
                    and abs(abs(e.center().X) - abs(jx)) < 14)]
        if near:
            manifold, _r, _warn = safe_fillet(manifold, near, radius=3.0)
    return manifold
