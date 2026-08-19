# benchmarks/tasks/fix_the_broken_part/fix_004_hole_pattern/specs/parts/base_plate.py
#
# The rubric. This block is appended to the END of the candidate's part script,
# so it must RE-BIND `SPECS` (never `+=`): the last module-level binding wins,
# and any `SPECS` the candidate authored for itself is discarded. Every
# constructor is imported under a `_bench_` alias because the candidate's own
# module namespace is in scope here — an alias makes a same-named module-level
# function in the candidate script irrelevant. `Box` and `Pos` are aliased for
# the same reason.
from build123d import Box as _bench_Box, Pos as _bench_Pos

from agentcad.toolkit.specs import (
    check_bbox as _bench_check_bbox,
    check_mass as _bench_check_mass,
    check_that as _bench_check_that,
    check_valid as _bench_check_valid,
)


def _bench_anchor_slots_open(part, metrics):
    """A slot is open at each of the four anchor positions.

    This is the requirement itself, measured rather than inferred: a 4 x 4 mm
    probe column is pushed straight down the Z axis at each nominal slot
    centre, and any material it meets means the slot is not there. The
    intersection uses the `&` OPERATOR — `Shape.intersect()` returns a
    `ShapeList`, not a shape (AGENTS.md's trap).
    """
    for x in (100.0, -100.0):
        for y in (100.0, -100.0):
            probe = _bench_Pos(x, y, 10.0) * _bench_Box(4.0, 4.0, 40.0)
            if (part & probe).volume > 1e-6:
                return False
    return True


# `anchor_slots` is the row the task is about and it names the requirement in
# the requirement's own terms. `plate_mass` is the arithmetic half of the same
# claim, stated in measured terms: four full slots take 278.25 g more out of a
# 300 x 300 x 20 A36 plate than the two the off-by-one leaves, and the window
# is +/-0.5 % of the reference's 13383.41 g — a 2.1 % miss, so the wrong answer
# is outside it by four times the band. There is no `check_wall`: the thinnest
# section of a flat plate is its 20 mm thickness, which no wrong slot layout
# moves.
SPECS = [
    _bench_check_valid(name="valid", requirement="FIX-004"),
    _bench_check_that(_bench_anchor_slots_open, name="anchor_slots",
                      requirement="FIX-004"),
    _bench_check_mass(min_g=13316.49, max_g=13450.32, name="plate_mass",
                      requirement="FIX-004"),
    _bench_check_bbox(within_mm=(300.2, 300.2, 20.2), name="envelope",
                      requirement="FIX-004"),
]
