"""Two-part enclosure: shelled body + lid returned as one two-solid part.

A uniform parametric wall, an open-top shell cut with ``safe_shell``, four
corner screw bosses tapped through the hole wizard, a ventilation slot row,
and a lid whose tongue seats inside the body wall at a stated clearance.
Body and lid ship as one ``Compound``, so the fit is modelled and measurable.
"""

from build123d import Align, Axis, Box, Compound, Cylinder, Plane, Pos

from agentcad.toolkit import holes, patterns, safe_fillet, safe_shell

PARAMS = {
    "length":    {"default": 100.0, "min": 50.0, "max": 250.0, "unit": "mm",
                  "description": "Outer length (X)"},
    "width":     {"default": 60.0,  "min": 40.0, "max": 200.0, "unit": "mm",
                  "description": "Outer width (Y)"},
    "height":    {"default": 30.0,  "min": 14.0, "max": 120.0, "unit": "mm",
                  "description": "Body height (Z), floor included"},
    "wall":      {"default": 2.4,   "min": 1.2,  "max": 4.0,   "unit": "mm",
                  "description": "Wall and floor thickness (FDM 1.2-2.4)"},
    "corner_r":  {"default": 3.0,   "min": 0.0,  "max": 12.0,  "unit": "mm",
                  "description": "Outer vertical corner radius (0 = sharp)"},
    "lid_t":     {"default": 2.4,   "min": 1.2,  "max": 6.0,   "unit": "mm",
                  "description": "Lid plate thickness"},
    "lip_h":     {"default": 4.0,   "min": 1.5,  "max": 10.0,  "unit": "mm",
                  "description": "Lid tongue depth into the body cavity"},
    "lip_t":     {"default": 1.6,   "min": 0.8,  "max": 4.0,   "unit": "mm",
                  "description": "Lid tongue thickness"},
    "lip_clear": {"default": 0.25,  "min": 0.05, "max": 0.6,   "unit": "mm",
                  "description": "Tongue-to-wall gap per side (FDM 0.2-0.3)"},
    "screw":     {"default": "M3", "type": "enum",
                  "choices": ["M2.5", "M3", "M4"],
                  "description": "Lid screw; boss OD is 2x its nominal"},
    "vents":     {"default": 6, "type": "int", "min": 0, "max": 20,
                  "unit": "count", "description": "Slots in the front wall"},
}

SOLID_LABELS = ["body", "lid"]

NOMINAL = {"M2.5": 2.5, "M3": 3.0, "M4": 4.0}
UP = (Align.CENTER, Align.CENTER, Align.MIN)     # grows +Z from z
DOWN = (Align.CENTER, Align.CENTER, Align.MAX)   # grows -Z from z


def build(p):
    length, width, height = p.length, p.width, p.height
    # Dependent dimensions are clamped HERE, not in PARAMS: these are
    # relationships between parameters, which min/max cannot express.
    wall = max(0.8, min(p.wall, length / 2 - 8.0, width / 2 - 8.0,
                        height / 2 - 3.0))
    d_nom = NOMINAL[p.screw]
    # Outer radius >= wall + 0.5 keeps the shelled inner corner positive.
    r_out = 0.0
    if p.corner_r > 0.05:
        r_out = min(max(p.corner_r, wall + 0.5), min(length, width) / 2 - 1.0)

    # Bosses: OD = 2x screw nominal, tangent to both inner walls and pushed
    # 0.5 mm into them, so the fuse is an overlap and never a tangency.
    br = min(d_nom, (min(length, width) / 2 - wall) / 2 - 0.5)
    bx, by = length / 2 - wall - br + 0.5, width / 2 - wall - br + 0.5
    boss_pts = [(bx, by), (-bx, by), (bx, -by), (-bx, -by)]
    boss_h = height - wall
    tap_depth = max(1.5, min(2.0 * d_nom, boss_h - 1.5))

    body = Box(length, width, height, align=UP)
    if r_out > 0:
        body, r_out, _w = safe_fillet(body, body.edges().filter_by(Axis.Z),
                                      r_out)
    body, _w = safe_shell(body, wall, [body.faces().sort_by(Axis.Z)[-1]])
    for x, y in boss_pts:
        body += Pos(x, y, wall) * Cylinder(br, boss_h, align=UP)

    # Slots: 2 mm wide, >= 3 mm of wall left above the floor and below the rim.
    slot_w, pitch, vent_h = 2.0, 3.0 * d_nom, height - wall - 7.0
    span = length - 2 * (wall + 2 * br + max(r_out, wall) + 2.0)
    n = int(p.vents)
    if vent_h < 2.0 or span < slot_w:
        n = 0
    elif n > 1:
        n = min(n, int((span - slot_w) // pitch) + 1)
    if n > 0:
        cut = Box(slot_w, wall + 2.0, vent_h)
        z_c = (wall + 3.0 + height - 4.0) / 2
        for x, _y in patterns.grid(n, 1, pitch, 1.0):
            body -= Pos(x, -width / 2 + wall / 2, z_c) * cut

    # Tapped from the rim down. An explicit Plane, because the named "top"
    # resolves to the largest outer face -- the rim ring, not the bosses.
    body, _recs, _w = holes.tapped(body, boss_pts, p.screw,
                                   plane=Plane.XY.offset(height),
                                   depth=tap_depth)

    # Lid: plate on the rim, tongue into the cavity at lip_clear a side,
    # notched around each boss.
    clear = p.lip_clear
    lo_x, lo_y = length / 2 - wall - clear, width / 2 - wall - clear
    lip_t = min(p.lip_t, lo_x - 2.0, lo_y - 2.0)
    lip_h = min(p.lip_h, height - wall - 2.0)
    lid = Pos(0, 0, height) * Box(length, width, p.lid_t, align=UP)
    if r_out > 0:
        lid, _r, _w = safe_fillet(lid, lid.edges().filter_by(Axis.Z), r_out)
    tongue = Pos(0, 0, height) * Box(2 * lo_x, 2 * lo_y, lip_h, align=DOWN)
    r_in = max(0.4, r_out - wall - clear) if r_out > 0 else 0.0
    if r_in > 0:
        tongue, _r, _w = safe_fillet(tongue, tongue.edges().filter_by(Axis.Z),
                                     r_in)
    tongue -= Pos(0, 0, height) * Box(2 * (lo_x - lip_t),
                                      2 * (lo_y - lip_t), lip_h + 2.0,
                                      align=DOWN)
    for x, y in boss_pts:
        tongue -= Pos(x, y, height) * Cylinder(br + clear + 0.3,
                                               lip_h + 2.0, align=DOWN)
    lid += tongue
    lid, _recs, _w = holes.clearance(lid, boss_pts, p.screw, fit="medium",
                                     plane=Plane.XY.offset(height + p.lid_t))
    return holes.carry(Compound(children=[body, lid]),
                       body, holes.records(lid))
