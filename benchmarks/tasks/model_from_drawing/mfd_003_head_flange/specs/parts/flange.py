# benchmarks/tasks/model_from_drawing/mfd_003_head_flange/specs/parts/flange.py
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

# The example this task is derived from states the same requirement (INT-003):
# the bolt circle must keep real material between the bolt holes and both the
# bore and the rim. `check_wall` is a SAMPLED ray cast, so the limit is stated
# in MEASURED terms — on the reference the grid-4 sampler reports 6.50 mm,
# which is exactly the rim ligament (outer_r - bc_r - bolt_r = 70 - 59 - 4.5).
# A candidate that crowds the bolt circle outward loses it immediately, so the
# 5.0 mm floor is a real reading of the drawing and not a formality. `grid` is
# pinned at 4: a finer grid samples the rim and bore chamfers instead.
SPECS = [
    _bench_check_valid(name="valid", requirement="MFD-003"),
    _bench_check_wall(min_mm=5.0, grid=4, name="bolt_circle_ligament",
                      requirement="MFD-003"),
    _bench_check_bbox(within_mm=(140.3, 140.3, 14.2), name="envelope",
                      requirement="MFD-003"),
]
