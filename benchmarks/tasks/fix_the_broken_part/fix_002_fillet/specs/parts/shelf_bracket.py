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
    check_valid as _bench_check_valid,
    check_wall as _bench_check_wall,
)

# The wall floor is stated in MEASURED terms: the grid-4 sampler reads exactly
# 6.00 mm on the reference — the leg thickness, which is the thinnest section
# of an L bracket — so 5.5 mm means "the legs are the 6 mm the drawing asks
# for". A candidate that "fixes" the build by thinning the legs, or by letting
# the oversized break eat into them, loses this row. `envelope` is the
# 70 x 40 x 55 outline with 0.2 mm of slack.
SPECS = [
    _bench_check_valid(name="valid", requirement="FIX-002"),
    _bench_check_wall(min_mm=5.5, grid=4, name="leg_thickness",
                      requirement="FIX-002"),
    _bench_check_bbox(within_mm=(70.2, 40.2, 55.2), name="envelope",
                      requirement="FIX-002"),
]
