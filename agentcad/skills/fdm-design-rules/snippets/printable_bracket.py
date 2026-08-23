"""Support-free FDM bracket: printed flat, teardrop bore, elephant-foot chamfer.

Every overhang is 45 deg or steeper, the horizontal bore has a teardrop roof,
the bed-side edges carry a chamfer that eats the first-layer squish, and the
minimum wall is derived from the nozzle instead of guessed.
"""

from math import sqrt

from build123d import *

from agentcad.toolkit import holes, patterns

PARAMS = {
    "length":    {"default": 70.0, "min": 30.0, "max": 200.0, "unit": "mm",
                  "description": "Base plate length along X (this face lies on the bed)"},
    "width":     {"default": 34.0, "min": 15.0, "max": 150.0, "unit": "mm",
                  "description": "Bracket width along Y"},
    "plate_t":   {"default": 5.0, "min": 1.6, "max": 12.0, "unit": "mm",
                  "description": "Base plate thickness"},
    "height":    {"default": 46.0, "min": 20.0, "max": 150.0, "unit": "mm",
                  "description": "Upstand height above the bed"},
    "wall_t":    {"default": 4.0, "min": 0.8, "max": 12.0, "unit": "mm",
                  "description": "Upstand wall thickness (clamped to 2 line widths)"},
    "gusset":    {"default": 14.0, "min": 0.5, "max": 80.0, "unit": "mm",
                  "description": "Gusset run = rise, so its face prints at 45 deg"},
    "pad_t":     {"default": 5.0, "min": 0.5, "max": 12.0, "unit": "mm",
                  "description": "Extra bore-pad thickness, blended in at 45 deg"},
    "pad_h":     {"default": 20.0, "min": 5.0, "max": 100.0, "unit": "mm",
                  "description": "Height of the full-thickness bore pad"},
    "bore_d":    {"default": 8.0, "min": 2.0, "max": 40.0, "unit": "mm",
                  "description": "Nominal horizontal bore diameter (axis parallel to the bed)"},
    "hole_comp": {"default": 0.2, "min": 0.0, "max": 1.0, "unit": "mm",
                  "description": "Diametral compensation: printed holes come out small"},
    "nozzle":    {"default": 0.4, "min": 0.2, "max": 1.0, "unit": "mm",
                  "description": "Nozzle diameter; minimum wall is 2 line widths"},
    "foot_ch":   {"default": 0.4, "min": 0.0, "max": 1.5, "unit": "mm",
                  "description": "Elephant-foot chamfer on the bed-side edges"},
}


def _dedupe(points):
    """Drop repeated vertices so a clamped profile never emits a null segment."""
    out = []
    for pt in points:
        if not out or abs(pt[0] - out[-1][0]) > 1e-6 or abs(pt[1] - out[-1][1]) > 1e-6:
            out.append(pt)
    return out


def build(p):
    line_w = p.nozzle * 1.05                  # typical slicer extrusion width
    min_wall = 2.0 * line_w                   # two perimeters: nothing thinner prints
    wall_t = max(p.wall_t, min_wall)
    plate_t = max(p.plate_t, min_wall)
    length, width, height = p.length, p.width, p.height

    rise = height - plate_t                   # vertical budget above the plate
    room = length - wall_t - 2.0              # horizontal budget on the plate
    gusset = min(max(p.gusset, 0.5), rise * 0.45, room)
    pad_t = min(max(p.pad_t, 0.5), rise * 0.25, room)
    pad_h = min(max(p.pad_h, min_wall), rise - gusset - pad_t - 0.5)

    x_back = -length / 2.0                    # wall back face (also the bed edge)
    x_face = x_back + wall_t                  # wall inner face
    x_pad = x_face + pad_t                    # bore-pad front face
    x_end = length / 2.0
    z_ramp = height - pad_h                   # top of the 45 deg pad ramp

    # One closed side profile, extruded across the width: the gusset hypotenuse
    # and the pad ramp are authored at 45 deg rather than filleted in later.
    profile = _dedupe([
        (x_back, 0.0), (x_end, 0.0), (x_end, plate_t), (x_face + gusset, plate_t),
        (x_face, plate_t + gusset), (x_face, z_ramp - pad_t), (x_pad, z_ramp),
        (x_pad, height), (x_back, height),
    ])

    with BuildPart() as bracket:
        with BuildSketch(Plane.XZ):
            with BuildLine():
                Polyline(*profile, close=True)
            make_face()
        extrude(amount=width / 2.0, both=True)

        # Horizontal bore -> teardrop roof. A round horizontal hole droops at
        # the top; the 45 deg apex is self-supporting and prints to size.
        r = (p.bore_d + p.hole_comp) / 2.0
        r = min(r, (pad_h - 2.0 * min_wall) / 2.42)
        if r > 0.05:
            z_bore = z_ramp + min_wall + r
            tangent = r / sqrt(2.0)           # where the 45 deg roof meets the bore
            bore_plane = Plane(origin=(0.0, 0.0, z_bore),
                               x_dir=(0.0, 1.0, 0.0), z_dir=(1.0, 0.0, 0.0))
            with BuildSketch(bore_plane):
                Circle(radius=r)
                with BuildLine():
                    Polyline((-tangent, tangent), (0.0, r * sqrt(2.0)),
                             (tangent, tangent), close=True)
                make_face()
            extrude(amount=length, both=True, mode=Mode.SUBTRACT)

        # Elephant foot: the first layer spreads, so take it off geometrically.
        # A chamfer prints support-free; a fillet here would be a shallow overhang.
        foot = min(p.foot_ch, plate_t * 0.3, 0.6)
        if foot > 0.01:
            chamfer(bracket.edges().group_by(Axis.Z)[0], length=foot)

    # Fixing holes are vertical (bed-normal), so they need no teardrop.
    part = bracket.part
    centre = (x_face + gusset + x_end) / 2.0
    dx = max(x_end - x_face - gusset - 14.0, 6.0)
    dy = max(width - 16.0, 6.0)
    points = [(centre + u, v) for u, v in patterns.grid(2, 2, dx, dy)]
    top = Plane(origin=(0.0, 0.0, plate_t), z_dir=(0.0, 0.0, 1.0))
    part, _records, _warning = holes.clearance(part, points, "M4", plane=top)
    return part
