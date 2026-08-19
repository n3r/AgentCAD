# Copied from examples/construction/parts/angle_bracket.py into this project.
"""L-shaped erection angle bracket.

An L-bracket (leg_b along X, leg_a up Z, extruded to width along Y) with
a structural fillet at the inner corner and two bolt holes per leg,
placed across the width and centered between the fillet runout and the
free end of each leg.

Hole gauge and axial positions are clamped internally so extremes stay
manifold: holes keep >= 4 mm side margins, never merge, and always
clear the inner fillet and the leg ends.
"""

from build123d import *

from agentcad.toolkit import holes

PARAMS = {
    "leg_a": {"default": 90.0, "min": 50.0, "max": 150.0, "unit": "mm",
              "description": "Vertical leg length (Z)"},
    "leg_b": {"default": 90.0, "min": 50.0, "max": 150.0, "unit": "mm",
              "description": "Horizontal leg length (X)"},
    "width": {"default": 80.0, "min": 40.0, "max": 150.0, "unit": "mm",
              "description": "Bracket width (Y)"},
    "thk": {"default": 10.0, "min": 6.0, "max": 20.0, "unit": "mm",
            "description": "Leg thickness"},
    "hole_d": {"default": 14.0, "min": 8.0, "max": 20.0, "unit": "mm",
               "description": "Bolt hole diameter, two holes per leg"},
    "fillet_r": {"default": 6.0, "min": 2.0, "max": 12.0, "unit": "mm",
                 "description": "Inner corner fillet radius"},
}


def build(p):
    t, w = p.thk, p.width
    r = min(p.fillet_r, min(p.leg_a, p.leg_b) - t - 2.0)  # keep flat on each leg
    hole_r = p.hole_d / 2

    # Bolt gauge across the width: holes neither merge nor breach the sides.
    gauge = max(p.hole_d + 4.0, w - 2 * max(hole_r + 4.0, 12.0))
    ys = (-w / 2 - gauge / 2, -w / 2 + gauge / 2)

    def axis_pos(leg):
        """Hole center along a leg, clamped between fillet runout and end."""
        lo = t + r + hole_r + 2.0
        hi = leg - hole_r - 3.0
        return min(max((t + r + leg) / 2, lo), hi)

    hx, hz = axis_pos(p.leg_b), axis_pos(p.leg_a)

    with BuildPart() as part:
        with BuildSketch(Plane.XZ):
            with BuildLine():
                Polyline((0, 0), (p.leg_b, 0), (p.leg_b, t), (t, t),
                         (t, p.leg_a), (0, p.leg_a), close=True)
            make_face()
        extrude(amount=w)  # along -Y: bracket spans y in [-width, 0]
        inner = [e for e in part.edges().filter_by(Axis.Y)
                 if abs(e.center().X - t) < 1e-4 and abs(e.center().Z - t) < 1e-4]
        fillet(inner, radius=r)
    # Two drilled groups, one per leg. "top" is the extreme +Z planar face,
    # resolved by predicate on every rebuild — never by a face index, which
    # renumbers on any topology change — and its points read (x, y).
    bracket, _rec, _warn = holes.drill(part.part, [(hx, y) for y in ys],
                                       p.hole_d, plane="top")
    # The vertical leg is drilled through an explicit plane rather than the
    # named "left" face. Both cut the same holes, but a named face carries its
    # own frame (x_dir = -Y there), and rotating the tool about its own axis
    # re-tessellates the part: measured, "left" keeps every metric and changes
    # the mesh bytes. Sliding the workplane ALONG the hole axis is free, which
    # is why "top" above costs nothing. In this plane +v runs along -Y, hence
    # the negated width positions.
    leg = Plane(origin=(0, 0, hz), z_dir=(1, 0, 0))
    bracket, _rec, _warn = holes.drill(bracket, [(0, -y) for y in ys],
                                       p.hole_d, plane=leg)
    return bracket
