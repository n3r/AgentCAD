# benchmarks/tasks/model_from_drawing/mfd_005_vee_block/specs/parts/vee_block.py
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
# 7.35 mm, the material left between a Ø8 clamp hole and the flank of the vee
# above it. The 6.0 mm floor is therefore "the groove is 15 mm deep and the
# holes sit 12 mm above the base, not deeper and not higher" — sink the vee or
# raise the holes and the ligament goes with it. `grid` is pinned at 4:
# changing it changes the measurement.
SPECS = [
    _bench_check_valid(name="valid", requirement="MFD-005"),
    _bench_check_wall(min_mm=6.0, grid=4, name="hole_to_vee_ligament",
                      requirement="MFD-005"),
    _bench_check_bbox(within_mm=(60.2, 60.2, 40.2), name="envelope",
                      requirement="MFD-005"),
]
