"""NEMA 17 L-bracket: motor face on the upright, slotted feet, side gussets.

The upright carries the NEMA 17 pattern (31.0 mm bolt square, 22 mm pilot
boss) drilled from the motor side; the base carries two adjustment slots; a
triangular gusset on each outer face turns the corner's bending into a
membrane load. Every thickness is driven by `thk`, so the gussets stay
one-wall-thick and the part prints/mills as one solid.

Defaults are NEMA 17 (see tables/nema.json); change bolt_sq / pilot_d /
screw to retarget the same bracket at another frame size.
"""

from build123d import *

from agentcad.toolkit import holes, patterns, safe_fillet

PARAMS = {
    "thk":        {"default": 5.0, "min": 2.0, "max": 12.0, "unit": "mm",
                   "description": "Wall thickness: legs and gussets"},
    "width":      {"default": 56.0, "min": 26.0, "max": 90.0, "unit": "mm",
                   "description": "Bracket width (Y), >= motor flange + 2 mm"},
    "upright_h":  {"default": 58.0, "min": 30.0, "max": 120.0, "unit": "mm",
                   "description": "Upright leg height (Z)"},
    "base_len":   {"default": 55.0, "min": 24.0, "max": 120.0, "unit": "mm",
                   "description": "Base leg length (X)"},
    "axis_h":     {"default": 31.0, "min": 12.0, "max": 80.0, "unit": "mm",
                   "description": "Motor shaft axis height above the base"},
    "bolt_sq":    {"default": 31.0, "min": 16.0, "max": 70.0, "unit": "mm",
                   "description": "Motor bolt square (NEMA 17 = 31.0)"},
    "pilot_d":    {"default": 22.0, "min": 12.0, "max": 45.0, "unit": "mm",
                   "description": "Motor pilot boss diameter (NEMA 17 = 22.0)"},
    "pilot_clr":  {"default": 0.3, "min": 0.1, "max": 1.0, "unit": "mm",
                   "description": "Diametral clearance added to the pilot bore"},
    "screw":      {"default": "M3", "type": "enum",
                   "choices": ["M2.5", "M3", "M5"],
                   "description": "Motor screw size (ISO 273 medium clearance)"},
    "slot_w":     {"default": 5.5, "min": 3.0, "max": 11.0, "unit": "mm",
                   "description": "Base slot width = bolt clearance hole"},
    "slot_travel": {"default": 8.0, "min": 2.0, "max": 40.0, "unit": "mm",
                    "description": "Adjustment travel; slot length = width + travel"},
    "gusset_frac": {"default": 0.7, "min": 0.0, "max": 0.85, "unit": "-",
                    "description": "Gusset leg as a fraction of each bracket leg"},
}


def build(p):
    t, w = p.thk, p.width
    uh, bl = p.upright_h, p.base_len

    # --- gusset: legs are gusset_frac of each free leg, thickness = wall ----
    gl = p.gusset_frac * (bl - t)
    gh = p.gusset_frac * (uh - t)

    # --- slots: length = width + travel, kept >= 1.5 d from both leg ends ---
    slot_len = p.slot_w + p.slot_travel
    edge = 1.5 * p.slot_w
    x_lo, x_hi = t + edge, bl - edge
    slot_len = max(p.slot_w, min(slot_len, x_hi - x_lo))
    x_slot = (x_lo + x_hi) / 2
    gauge = max(2.0 * p.slot_w, w - 2.0 * (t + 1.5 * p.slot_w))

    with BuildPart() as part:
        with BuildSketch(Plane.XZ):
            with BuildLine():
                Polyline((0, 0), (bl, 0), (bl, t), (t, t),
                         (t, uh), (0, uh), close=True)
            make_face()
        extrude(amount=w / 2, both=True)          # symmetric about Y = 0
        if gl > 1e-6 and gh > 1e-6:
            for y0 in (w / 2, -w / 2 + t):
                seat = Plane(origin=(0, y0, 0), x_dir=(1, 0, 0), z_dir=(0, -1, 0))
                with BuildSketch(seat):           # (u, v) = (x, z)
                    with BuildLine():
                        Polyline((t, t), (t + gl, t), (t, t + gh), close=True)
                    make_face()
                extrude(amount=t)                 # grows along -Y from y0
        with BuildSketch(Plane.XY):
            with Locations((x_slot, gauge / 2), (x_slot, -gauge / 2)):
                SlotOverall(slot_len, p.slot_w)
        extrude(amount=t + 1.0, both=True, mode=Mode.SUBTRACT)

    # Back corners eased. safe_fillet binary-searches DOWN on OCCT failure and
    # returns the part unchanged rather than killing the build.
    back = [e for e in part.part.edges().filter_by(Axis.Z)
            if abs(e.center().X) < 1e-6]
    bracket, _r, _warn = safe_fillet(part.part, back, min(3.0, t / 2 + 1.0))

    # Motor face: the outward normal at x = 0 is -X, so holes drill +X.
    # (u, v) = (-y, z) on this plane; the pattern is symmetric in y.
    face = Plane(origin=(0, 0, 0), x_dir=(0, -1, 0), z_dir=(-1, 0, 0))
    pattern = [(u, v + p.axis_h)
               for u, v in patterns.grid(2, 2, p.bolt_sq, p.bolt_sq)]
    bracket, _rec, _warn = holes.clearance(bracket, pattern, p.screw,
                                           fit="medium", plane=face)
    # The pilot bore clears the motor's register boss; no fastener table
    # applies to it, so it is a drilled diameter and its record says so.
    bracket, _rec, _warn = holes.drill(bracket, [(0.0, p.axis_h)],
                                       p.pilot_d + p.pilot_clr, plane=face)
    return bracket
