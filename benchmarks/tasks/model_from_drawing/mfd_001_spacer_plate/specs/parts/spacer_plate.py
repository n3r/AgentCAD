# benchmarks/tasks/model_from_drawing/mfd_001_spacer_plate/specs/parts/spacer_plate.py
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

SPECS = [
    _bench_check_valid(name="valid", requirement="MFD-001"),
    _bench_check_wall(min_mm=3.0, grid=4, name="ligament", requirement="MFD-001"),
    _bench_check_bbox(within_mm=(80.2, 50.2, 6.2), name="envelope",
                      requirement="MFD-001"),
]
