# benchmarks/tasks/fix_the_broken_part/fix_005_invalid_shell/specs/parts/coolant_elbow.py
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

# `valid` is the row the task is about: at bend_r = 6 the swept shell crosses
# itself and `shape.is_valid` is False while the build still succeeds.
#
# `duct_wall` is the same fact from the other side and is stated in MEASURED
# terms: the grid-4 sampler reads exactly 3.00 mm on the reference (the tube
# wall, the only section this part has) and 0.0050 mm on the self-intersecting
# shipped part, where a ray leaves one folded face and lands on the fold. A
# 2.5 mm floor therefore means "the wall really is 3 mm all the way round".
#
# `swept_volume` pins the 24 mm bend: the reference measures 58.6216 g in 6061
# and the shipped 6 mm bend measures 62.6645 g (the fold is counted twice), so
# a +/-1 % window separates them by six times the band.
SPECS = [
    _bench_check_valid(name="valid", requirement="FIX-005"),
    _bench_check_wall(min_mm=2.5, grid=4, name="duct_wall",
                      requirement="FIX-005"),
    _bench_check_mass(min_g=58.036, max_g=59.208, name="swept_volume",
                      requirement="FIX-005"),
    _bench_check_bbox(within_mm=(72.2, 24.2, 72.2), name="envelope",
                      requirement="FIX-005"),
]
