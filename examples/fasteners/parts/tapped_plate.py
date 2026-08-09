"""Tapped base plate: a square plate with a blind M8x1.25 tapped hole.

Built with ``agentcad.toolkit.threads``. The hole is a plain clearance
counterbore near the top face (so a bolt shank slips in without touching the
thread) opening into a *real* ISO internal thread cut deeper down.

Modeling note: a real tapped hole is bored at the thread ROOT radius
(major/2) and the internal-thread ridges then protrude inward to the minor
(crest) radius. Boring at the minor radius instead — as if drilling the
physical tap-drill — would bury the ridges in the wall (zero added volume,
no visible thread), so we bore at ``root_radius`` here.
"""

from build123d import *

from agentcad.toolkit import safe_fillet, threads

NOM = 8.0        # M8 nominal (major) diameter, mm
PITCH = 1.25     # M8 coarse pitch, mm
CLEAR_R = 4.5    # counterbore radius: an M8 shank (r=4) slips in with clearance
FLOOR = 2.0      # solid material left under the blind hole, mm

# Reference thread just to read the ISO radii (cheap; not added to the part).
_REF = threads.internal_thread(NOM, PITCH, 4.0)
ROOT_R = _REF.root_radius   # ~4.0  -> bore the thread region here (major/2)
MIN_R = _REF.min_radius     # ~3.32 -> thread crest / tap-drill radius

PARAMS = {
    "size": {"default": 40.0, "min": 24.0, "max": 150.0, "unit": "mm",
             "description": "Square plate side length (X and Y)"},
    "thickness": {"default": 20.0, "min": 16.0, "max": 80.0, "unit": "mm",
                  "description": "Plate thickness; the blind-hole depth budget"},
    "thread_engage": {"default": 8.0, "min": 4.0, "max": 20.0, "unit": "mm",
                      "description": "Tapped thread engagement length (auto-clamped to fit)"},
    "counterbore": {"default": 6.0, "min": 2.0, "max": 20.0, "unit": "mm",
                    "description": "Plain clearance counterbore depth above the thread"},
}


def build(p):
    # Everything below the top face has to fit inside (thickness - FLOOR).
    max_hole = p.thickness - FLOOR
    cb = max(1.5, min(p.counterbore, max_hole - 3.0))
    eng = max(2.0, min(p.thread_engage, max_hole - cb - 0.5))
    corner_r = min(4.0, p.size / 8.0)

    thr = threads.tapped_hole_thread(NOM, PITCH, eng)  # internal thread solid

    with BuildPart() as part:
        # Top face sits at z = 0; the body hangs below (mates cleanly with a
        # clamp plate stacked on top at z = 0).
        Box(p.size, p.size, p.thickness,
            align=(Align.CENTER, Align.CENTER, Align.MAX))
        with Locations(part.faces().sort_by(Axis.Z)[-1]):
            Hole(radius=ROOT_R, depth=cb + eng)   # thread-region bore (root/major)
            Hole(radius=CLEAR_R, depth=cb)        # clearance counterbore near top
        # Add the internal thread so its ridges protrude inward to the crest.
        add(Pos(0, 0, -(cb + eng)) * thr)

    # Break the outer corners; safe_fillet clamps the radius rather than dying
    # if a corner can't take the full radius at a small plate size.
    result, _radius, _warn = safe_fillet(
        part.part, part.part.edges().filter_by(Axis.Z).group_by(SortBy.LENGTH)[-1],
        radius=corner_r,
    )
    return result
