# Copied from examples/construction/parts/gusset_plate.py for bench task
# optimize_under_constraints/opt_002_stiffest_gusset. A derived task copies the
# script INTO the bundle: the runner registers no examples, so a run can never
# read the answer, and the starter and the reference are the SAME script at
# different parameters — the task is an optimisation over the parameters, not a
# rewrite. The rubric is injected from ../../../specs/parts/, so this script
# declares no SPECS of its own.
"""Steel truss gusset plate.

Joins a horizontal bottom chord and two diagonal web members at a truss
panel point. The plate outline is the convex hull of the three member
footprints (a chord band along X and two diagonal strips rising at
±diag_angle_deg from the horizontal), extruded to plate_t. Each member
gets a bolt group of 2 columns x n_rows along its axis.

All hole-layout dimensions are re-clamped internally (gauge, pitch, edge
distances) so that any parameter extreme still yields a manifold plate:
holes never merge with each other and never breach a plate edge.
"""

import math

from build123d import *

from agentcad.toolkit import holes, patterns

PARAMS = {
    "plate_t": {"default": 10.0, "min": 6.0, "max": 25.0, "unit": "mm",
                "description": "Gusset plate thickness"},
    "diag_angle_deg": {"default": 45.0, "min": 30.0, "max": 60.0, "unit": "deg",
                       "description": "Angle of each diagonal member measured from the horizontal chord"},
    "chord_w": {"default": 80.0, "min": 50.0, "max": 120.0, "unit": "mm",
                "description": "Bottom chord member width (height of the plate lap band)"},
    "diag_w": {"default": 60.0, "min": 40.0, "max": 100.0, "unit": "mm",
               "description": "Diagonal member width (width of each diagonal strip)"},
    "hole_d": {"default": 18.0, "min": 12.0, "max": 24.0, "unit": "mm",
               "description": "Bolt hole diameter (bolt plus clearance, e.g. 18 for M16)"},
    "pitch": {"default": 50.0, "min": 35.0, "max": 90.0, "unit": "mm",
              "description": "Bolt row spacing along each member axis"},
    "n_rows": {"default": 3, "min": 2, "max": 5, "unit": "count",
               "description": "Bolt rows per member group (each row has 2 columns)"},
    "edge_dist": {"default": 30.0, "min": 20.0, "max": 50.0, "unit": "mm",
                  "description": "End distance from the outer hole centers to the plate edge"},
}


def _hull(points):
    """Monotone-chain convex hull of 2D points, counter-clockwise."""
    pts = sorted(set((round(x, 6), round(y, 6)) for x, y in points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for pt in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], pt) <= 0:
            lower.pop()
        lower.append(pt)
    for pt in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], pt) <= 0:
            upper.pop()
        upper.append(pt)
    return lower[:-1] + upper[:-1]


def _group_layout(member_w, hole_d):
    """Across-member bolt layout: (column gauge, effective strip width).

    The gauge (distance between the two bolt columns) is derived from the
    member width; if the member is too narrow for the hole diameter the
    gauge is held at hole_d + 4 and the strip widened to keep side edge
    distances — extremes stay manifold instead of erroring.
    """
    e_side = max(hole_d / 2 + 4.0, 0.2 * member_w)
    gauge = member_w - 2 * e_side
    if gauge < hole_d + 4.0:
        gauge = hole_d + 4.0
    w_eff = max(member_w, gauge + 2 * e_side)
    return gauge, w_eff


def build(p):
    a = math.radians(p.diag_angle_deg)
    n = int(round(p.n_rows))
    hole_r = p.hole_d / 2

    # Internal clamps that keep every parameter extreme manifold.
    pitch = max(p.pitch, p.hole_d + 6.0)      # rows can never merge
    e_end = max(p.edge_dist, hole_r + 4.0)    # holes never breach an end edge
    group_len = (n - 1) * pitch

    gauge_c, w_chord = _group_layout(p.chord_w, p.hole_d)
    gauge_d, w_diag = _group_layout(p.diag_w, p.hole_d)

    # Chord band along X spanning y in [0, w_chord]; work point at its center.
    yc = w_chord / 2
    chord_half = group_len / 2 + e_end
    corners = [
        (-chord_half, 0.0), (chord_half, 0.0),
        (-chord_half, w_chord), (chord_half, w_chord),
    ]

    # Chord bolt group: n rows along X x 2 columns across the band, centred
    # on the work point. That is exactly patterns.grid's arithmetic.
    points = [(x, yc + dy) for x, dy in patterns.grid(n, 2, pitch, gauge_c)]

    # Diagonal strips radiate from the work point; the first bolt row is
    # pushed one chord half-width + end distance + hole diameter up the
    # axis so diagonal holes always clear the chord group.
    s0 = w_chord / 2 + e_end + p.hole_d
    s_start, s_end = s0 - e_end, s0 + group_len + e_end
    s_mid = s0 + group_len / 2
    for side in (1.0, -1.0):
        ux, uy = side * math.cos(a), math.sin(a)
        vx, vy = -side * math.sin(a), math.cos(a)
        for s in (s_start, s_end):
            for sg in (1.0, -1.0):
                corners.append(
                    (ux * s + vx * sg * w_diag / 2,
                     yc + uy * s + vy * sg * w_diag / 2)
                )
        # The same 2 x n grid, laid out in the member's own (along, across)
        # frame and rotated onto the diagonal axis. Building the grid in the
        # local frame keeps the trig where it belongs: the rotated
        # coordinates are irrational and must not be rounded, and
        # patterns.grid rounds its output to 9 decimals.
        for ds, dt in patterns.grid(n, 2, pitch, gauge_d):
            s = s_mid + ds
            points.append((ux * s + vx * dt, yc + uy * s + vy * dt))

    outline = _hull(corners)
    with BuildPart() as part:
        with BuildSketch(Plane.XY):
            Polygon(*outline, align=None)
        extrude(amount=p.plate_t)
    # One drilled group for every bolt: the record carries the diameter and
    # the count to the drawing callouts, and the helper's guard names any
    # instance that misses the plate (a misplaced cut is a silent no-op in
    # OCCT, not an error). hole_d is a structural clearance in millimetres,
    # not an ISO 273 row, so this is holes.drill and not holes.clearance.
    plate, _records, _warn = holes.drill(part.part, points, p.hole_d,
                                         plane="top")
    return plate
