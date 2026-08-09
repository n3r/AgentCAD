"""Cam cover: bolts over the head's rail, enclosing cam, caps, and springs.

Local frame matches the head (rail face z = 42). A shelled box with six ear
bosses matching the head's tapped M5 rail, spark-plug tubes dropping onto
the head's tube bosses (coils seat on top), ribs, and an oil filler. A
separate part — unbolt it (hide the instance) and the whole valvetrain is
on display.
"""

from build123d import *

from agentcad.toolkit import safe_fillet

RAIL_Z = 42.0
COVER_BOLT_PTS = ((-40, -47), (40, -47), (-40, 47), (40, 47),
                  (32, -65), (-32, 65))
TUBE_YS = (-40.0, 40.0)

PARAMS = {
    "height": {"default": 42.0, "min": 38.0, "max": 48.0, "unit": "mm",
               "description": "Cover height over the rail (cam caps need 36)"},
    "wall": {"default": 4.5, "min": 3.5, "max": 7.0, "unit": "mm",
             "description": "Skirt and roof wall thickness"},
    "tube_d": {"default": 16.0, "min": 14.0, "max": 18.0, "unit": "mm",
               "description": "Spark-plug tube outer diameter"},
    "rib_h": {"default": 3.0, "min": 0.5, "max": 5.0, "unit": "mm",
              "description": "Roof rib height"},
}


def build(p):
    top = RAIL_Z + p.height
    cover = Pos(0, 0, (RAIL_Z + top) / 2) * Box(100, 150, p.height)
    cover -= Pos(0, 0, (RAIL_Z + top - p.wall) / 2 - 1) * Box(
        100 - 2 * p.wall, 150 - 2 * p.wall, p.height - p.wall + 2)

    # ear bosses over the head's tapped rail, with through-holes
    for cx, cy in COVER_BOLT_PTS:
        cover += Pos(cx, cy, RAIL_Z + 3) * Cylinder(radius=6.5, height=6)
        cover -= Pos(cx, cy, RAIL_Z + 4) * Cylinder(radius=2.75, height=12)

    # spark-plug tubes: land 0.5 over the head's tube bosses, open at the top
    for ty in TUBE_YS:
        cover += Pos(0, ty, (47.5 + top) / 2) * Cylinder(
            radius=p.tube_d / 2, height=top - 47.5)
        cover -= Pos(0, ty, (44.0 + top) / 2) * Cylinder(
            radius=5.5, height=top - 44.0 + 2)

    # roof ribs + oil filler cap
    for ry in (-60.0, -20.0, 20.0, 60.0):
        for rx in (-31.0, 31.0):
            cover += Pos(rx, ry, top + p.rib_h / 2) * Box(22, 3, p.rib_h)
    cover += Pos(22, 0, top + 2.5) * Cylinder(radius=13, height=5)

    top_edges = cover.edges().group_by(Axis.Z)[-1]
    if top_edges:
        cover, _r, _warn = safe_fillet(cover, top_edges, radius=1.5)
    # 0.1 mm gasket float over the head rail, like every other joint
    return Pos(0, 0, 0.1) * cover
