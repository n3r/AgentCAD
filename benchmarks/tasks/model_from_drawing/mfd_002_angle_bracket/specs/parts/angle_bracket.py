# benchmarks/tasks/model_from_drawing/mfd_002_angle_bracket/specs/parts/angle_bracket.py
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

# `check_wall` is a SAMPLED ray cast along the inward face normal, so the limit
# is stated in MEASURED terms: on the reference the grid-4 sampler reports
# 10.31 mm, which is the 10 mm leg thickness plus the fillet's contribution.
# The 9.0 mm floor is therefore "the legs are the drawing's 10 mm, not thinner"
# with a millimetre of sampling slack. `grid` is pinned: changing it changes the
# measurement.
SPECS = [
    _bench_check_valid(name="valid", requirement="MFD-002"),
    _bench_check_wall(min_mm=9.0, grid=4, name="leg_thickness",
                      requirement="MFD-002"),
    _bench_check_bbox(within_mm=(90.2, 80.2, 90.2), name="envelope",
                      requirement="MFD-002"),
]
