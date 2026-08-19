# benchmarks/tasks/fix_the_broken_part/fix_002_fillet/specs/parts/shelf_bracket.py
#
# The rubric. This block is appended to the END of the candidate's part script,
# so it must RE-BIND `SPECS` (never `+=`): the last module-level binding wins,
# and any `SPECS` the candidate authored for itself is discarded. Every
# constructor is imported under a `_bench_` alias because the candidate's own
# module namespace is in scope here — an alias makes a same-named module-level
# function in the candidate script irrelevant.
from agentcad.toolkit.specs import (
    check_bbox as _bench_check_bbox,
    check_mass as _bench_check_mass,
    check_valid as _bench_check_valid,
    check_wall as _bench_check_wall,
)

# The wall floor is stated in MEASURED terms: the grid-4 sampler reads exactly
# 6.00 mm on the reference — the leg thickness, which is the thinnest section
# of an L bracket — so 5.5 mm means "the legs are the 6 mm the drawing asks
# for". A candidate that "fixes" the build by thinning the legs, or by letting
# the oversized break eat into them, loses this row. `envelope` is the
# 70 x 40 x 55 outline with 0.2 mm of slack.
#
# `end_breaks` is the row that measures the R4 breaks THEMSELVES, and it is
# here because the obvious wrong fix is to delete the offending fillet rather
# than reduce it: that answer builds, is valid, keeps the 6 mm legs and the
# 70 x 40 x 55 envelope, and differs from the reference only in mass. Measured
# in A36: **226.1617 g** with both R4 breaks, **228.5086 g** with none — a
# 2.347 g / 1.04 % difference, which is the two quarter-round corners. The
# window is +/-0.5 % of the reference, so the right answer sits with half a
# per cent of margin on each side and the no-break answer misses by 0.54 %.
# (At the seeded +/-1 % the no-break answer missed by 0.085 g, i.e. 0.04 %,
# which is a coincidence and not a measurement.)
SPECS = [
    _bench_check_valid(name="valid", requirement="FIX-002"),
    _bench_check_wall(min_mm=5.5, grid=4, name="leg_thickness",
                      requirement="FIX-002"),
    _bench_check_mass(min_g=225.031, max_g=227.293, name="end_breaks",
                      requirement="FIX-002"),
    _bench_check_bbox(within_mm=(70.2, 40.2, 55.2), name="envelope",
                      requirement="FIX-002"),
]
