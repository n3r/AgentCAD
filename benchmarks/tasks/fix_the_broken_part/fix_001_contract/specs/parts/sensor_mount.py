# benchmarks/tasks/fix_the_broken_part/fix_001_contract/specs/parts/sensor_mount.py
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

# `valid` is the row the whole task is about: the shipped script hands the
# worker `None`, so there is no solid at all. The wall floor is stated in
# MEASURED terms — the grid-4 sampler reads exactly 4.00 mm on the reference,
# which is the (Ø16 - Ø8) / 2 boss annulus, the thinnest section of the part —
# so 3.5 mm means "the boss wall is the 4 mm the drawing asks for, not
# thinner". `envelope` is the 60 x 40 x 17 outline with 0.2 mm of slack.
SPECS = [
    _bench_check_valid(name="valid", requirement="FIX-001"),
    _bench_check_wall(min_mm=3.5, grid=4, name="boss_wall",
                      requirement="FIX-001"),
    _bench_check_bbox(within_mm=(60.2, 40.2, 17.2), name="envelope",
                      requirement="FIX-001"),
]
