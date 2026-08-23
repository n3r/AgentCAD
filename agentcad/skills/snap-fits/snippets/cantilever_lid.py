"""Snap-fit lid: flat plate with two opposed tapered cantilever hooks.

The hook is sized FROM THE MATERIAL, not from a guess: build() computes the
permissible tip deflection of a beam tapered 1:2 root->tip, spends 70 % of it
on the undercut, and cuts the nose to that number. The arms hang CLEARANCE
inside the base's inner wall and catch a window `wall` deep in it.

Insertion (`insert_deg`) and return (`return_deg`) angles are measured FROM
THE INSERTION AXIS: 90 deg is a square shoulder (permanent), 45 deg cams out
(releasable). One solid, no supports needed if printed lid-face-down.
"""

from math import radians, tan

from build123d import *

from agentcad.toolkit import safe_fillet
from agentcad.toolkit.specs import check_that, check_valid

# Allowable one-time-assembly strain; mirrors tables/material_strain.json.
STRAIN = {"pla": 0.012, "petg": 0.030, "abs": 0.040,
          "pa": 0.060, "pp": 0.080, "pc": 0.040}
TAPER = 0.5         # tip thickness / root thickness (the 1:2 taper)
TAPER_GAIN = 1.64   # permissible-deflection factor for a t -> t/2 taper
USE = 0.70          # fraction of the permissible deflection we spend
CLEARANCE = 0.15    # arm-to-base-wall gap

PARAMS = {
    "length":     {"default": 100.0, "min": 60.0, "max": 250.0, "unit": "mm",
                   "description": "Lid length (X) -- match the enclosure base"},
    "width":      {"default": 60.0, "min": 40.0, "max": 200.0, "unit": "mm",
                   "description": "Lid width (Y) -- match the enclosure base"},
    "lid_t":      {"default": 3.0, "min": 1.6, "max": 8.0, "unit": "mm",
                   "description": "Lid plate thickness"},
    "wall":       {"default": 2.5, "min": 1.2, "max": 5.0, "unit": "mm",
                   "description": "Base wall thickness the arm sits inside"},
    "corner_r":   {"default": 3.0, "min": 0.0, "max": 10.0, "unit": "mm",
                   "description": "Plate corner fillet radius (0 = sharp)"},
    "material":   {"default": "pla", "type": "enum",
                   "choices": ["pla", "petg", "abs", "pa", "pp", "pc"],
                   "description": "Sets the allowable strain the hook is sized from"},
    "beam_l":     {"default": 12.0, "min": 5.0, "max": 40.0, "unit": "mm",
                   "description": "Cantilever free length (raised to 5x root thickness)"},
    "beam_t":     {"default": 2.0, "min": 1.0, "max": 5.0, "unit": "mm",
                   "description": "Beam thickness at the root (tip is half of it)"},
    "beam_w":     {"default": 12.0, "min": 4.0, "max": 40.0, "unit": "mm",
                   "description": "Beam width"},
    "undercut":   {"default": 0.6, "min": 0.15, "max": 2.0, "unit": "mm",
                   "description": "Requested catch depth; clamped to the strain limit"},
    "insert_deg": {"default": 30.0, "min": 20.0, "max": 60.0, "unit": "deg",
                   "description": "Lead-in angle from the insertion axis"},
    "return_deg": {"default": 45.0, "min": 30.0, "max": 90.0, "unit": "deg",
                   "description": "Retention angle from the axis (90 = permanent)"},
}

SPECS = [
    check_valid(),
    check_that(lambda part, metrics: len(part.solids()) == 1, name="one_solid"),
]


def build(p):
    t_root = p.beam_t
    t_tip = TAPER * t_root
    beam_l = max(p.beam_l, 5.0 * t_root)          # beam theory wants L >= 5t
    eps = STRAIN[p.material]

    # Permissible tip deflection, tapered cantilever: y = 1.64 * eps*L^2/(1.5*t).
    y_perm = TAPER_GAIN * eps * beam_l ** 2 / (1.5 * t_root)
    # THE RULE: the undercut can never demand more than the beam may deflect.
    undercut = min(p.undercut, USE * y_perm, 0.8 * t_tip, p.wall - 0.3)
    undercut = max(undercut, 0.15)                # below this FDM cannot resolve it

    # Nose profile heights (angles from the insertion axis -> run/rise = tan).
    rise_in = undercut / tan(radians(p.insert_deg))
    rise_ret = undercut / tan(radians(min(p.return_deg, 88.0)))
    land = 0.25 * undercut                        # small flat crest
    beam_l = max(beam_l, rise_in + land + rise_ret + 2.0)   # nose clear of the plate

    x_out = p.length / 2 - p.wall - CLEARANCE     # beam outer face (flat: it carries the nose)
    z_root = min(0.6 * p.lid_t, 1.0)              # embed the root inside the plate
    root_r = min(0.45 * t_root, p.lid_t - 0.8)

    with BuildPart() as part:
        Box(p.length, p.width, p.lid_t, align=(Align.CENTER, Align.CENTER, Align.MIN))
        if p.corner_r > 0.05:                     # fillet the plate BEFORE the arms:
            fillet(part.edges().filter_by(Axis.Z),   # the arms have vertical edges too
                   radius=min(p.corner_r, min(p.length, p.width) / 2 - 1.0))
        for s in (1.0, -1.0):
            # Tapered beam: outer face flat at x_out, taper on the inner face.
            with BuildSketch(Plane.XY.offset(z_root)):
                with Locations((s * (x_out - t_root / 2), 0.0)):
                    Rectangle(t_root, p.beam_w)
            with BuildSketch(Plane.XY.offset(-beam_l)):
                with Locations((s * (x_out - t_tip / 2), 0.0)):
                    Rectangle(t_tip, p.beam_w)
            loft()
            # Nose: lead-in ramp, crest, retention face; overlaps the beam so it fuses.
            z0 = -beam_l
            pts = [(s * (x_out - 0.7 * t_tip), z0), (s * x_out, z0),
                   (s * (x_out + undercut), z0 + rise_in),
                   (s * (x_out + undercut), z0 + rise_in + land),
                   (s * x_out, z0 + rise_in + land + rise_ret),
                   (s * (x_out - 0.7 * t_tip), z0 + rise_in + land + rise_ret)]
            with BuildSketch(Plane.XZ):
                with BuildLine():
                    Polyline(*pts, close=True)
                make_face()
            extrude(amount=p.beam_w / 2, both=True)

    # Root fillet (>= 0.45t) on the beam/plate junction only -- the plate's own
    # underside perimeter is excluded by the x band AND the y band.
    solid = part.part
    junction = [e for e in solid.edges()
                if abs(e.center().Z) < 1e-6
                and x_out - t_root - 0.3 <= abs(e.center().X) <= x_out + 0.3
                and abs(e.center().Y) <= p.beam_w / 2 + 0.1]
    solid, _r, _warn = safe_fillet(solid, junction, root_r)
    return solid
