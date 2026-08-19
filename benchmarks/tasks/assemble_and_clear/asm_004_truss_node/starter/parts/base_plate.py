# Copied from examples/construction/parts/base_plate.py into this project.
"""Column base plate with anchor slots and a column footprint marking.

A rectangular steel base plate (plate_w x plate_l x plate_t, corner
radius 10) with four rounded anchor-bolt slots near the corners
(SlotOverall, slot_w x slot_l, long axis along X so the column can be
plumbed during erection) and a shallow 1 mm recess marking the square
HSS column footprint at the center — a setting-out aid for the welder.

Slot centers and the recess size are clamped internally so extremes
stay manifold: slots keep >= 3 mm edge margins and never merge; the
recess shrinks to stay clear of the slots and is skipped entirely if
less than 20 mm would remain.
"""

from build123d import *

from agentcad.toolkit import patterns

PARAMS = {
    "plate_w": {"default": 300.0, "min": 150.0, "max": 450.0, "unit": "mm",
                "description": "Base plate width (X)"},
    "plate_l": {"default": 300.0, "min": 150.0, "max": 450.0, "unit": "mm",
                "description": "Base plate length (Y)"},
    "plate_t": {"default": 20.0, "min": 10.0, "max": 40.0, "unit": "mm",
                "description": "Base plate thickness"},
    "hss_w": {"default": 150.0, "min": 80.0, "max": 250.0, "unit": "mm",
              "description": "Square HSS column width marked by the footprint recess"},
    "slot_w": {"default": 22.0, "min": 14.0, "max": 30.0, "unit": "mm",
               "description": "Anchor slot width (anchor bolt plus clearance)"},
    "slot_l": {"default": 45.0, "min": 25.0, "max": 60.0, "unit": "mm",
               "description": "Anchor slot overall length (erection adjustment along X)"},
    "anchor_offset": {"default": 50.0, "min": 30.0, "max": 60.0, "unit": "mm",
                      "description": "Anchor slot center distance in from each plate edge"},
}


def build(p):
    w, l, t = p.plate_w, p.plate_l, p.plate_t
    slot_l = max(p.slot_l, p.slot_w + 2.0)  # a slot is longer than it is wide

    # Slot centers: nominally anchor_offset in from each edge, clamped so
    # every slot keeps >= 3 mm to the plate edge and to the plate center.
    cx = min(max(w / 2 - p.anchor_offset, slot_l / 2 + 3.0),
             w / 2 - 3.0 - slot_l / 2)
    cy = min(max(l / 2 - p.anchor_offset, p.slot_w / 2 + 3.0),
             l / 2 - 3.0 - p.slot_w / 2)

    # Column footprint recess, clamped clear of the slots (at least one
    # axis keeps a 2 mm gap) and of the plate edges.
    rec_max = 2 * max(cx - slot_l / 2 - 2.0, cy - p.slot_w / 2 - 2.0)
    rec = min(p.hss_w, min(w, l) - 8.0, rec_max)

    with BuildPart() as part:
        Box(w, l, t, align=(Align.CENTER, Align.CENTER, Align.MIN))
        fillet(part.edges().filter_by(Axis.Z), radius=10.0)
        with BuildSketch(Plane.XY):
            # The four anchor slots are a 2 x 2 grid about the plate centre.
            # patterns.grid is pure arithmetic and feeds a plain Locations
            # block as readily as it feeds holes.*: these are slots, not
            # holes, so there is no hole record to carry here.
            with Locations(*patterns.grid(2, 2, 2 * cx, 2 * cy)):
                SlotOverall(slot_l, p.slot_w)
        extrude(amount=t, mode=Mode.SUBTRACT)
        if rec >= 20.0:
            with BuildSketch(Plane.XY.offset(t)):
                Rectangle(rec, rec)
            extrude(amount=-1.0, mode=Mode.SUBTRACT)
    return part.part
