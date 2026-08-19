# benchmarks/tasks/modify_to_spec/mts_005_m10_clamp/specs/parts/clamp_plate.py
#
# The rubric. This block is appended to the END of the candidate's part script,
# so it must RE-BIND `SPECS` (never `+=`): the last module-level binding wins,
# and any `SPECS` the candidate authored for itself is discarded. Every
# constructor is imported under a `_bench_` alias because the candidate's own
# module namespace is in scope here — an alias makes a same-named module-level
# function in the candidate script irrelevant.
from agentcad.toolkit.specs import (
    check_bbox as _bench_check_bbox,
    check_that as _bench_check_that,
    check_valid as _bench_check_valid,
    check_wall as _bench_check_wall,
)

# `footprint` and `plate_thickness` are the two rows the 40 x 40 x 8 starter
# fails; `envelope` is the row that stops "make it bigger still". `check_wall`
# reads 10.0 mm on the reference — the plate thickness, which is the thinnest
# section of a plate with one central hole — so its floor is the 10 mm the
# requirement asks for, stated in measured terms and with 0.5 mm of sampling
# slack. The interference half of this task is the `interference` subscore,
# which measures the whole assembly; nothing here duplicates it.
SPECS = [
    _bench_check_valid(name="valid", requirement="MTS-005"),
    _bench_check_that(lambda part, metrics:
                      metrics["bbox"]["max"][0] - metrics["bbox"]["min"][0]
                      >= 63.5
                      and metrics["bbox"]["max"][1] - metrics["bbox"]["min"][1]
                      >= 63.5,
                      name="footprint", requirement="MTS-005"),
    _bench_check_wall(min_mm=9.5, grid=4, name="plate_thickness",
                      requirement="MTS-005"),
    _bench_check_bbox(within_mm=(64.5, 64.5, 10.5), name="envelope",
                      requirement="MTS-005"),
]
