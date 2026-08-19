# benchmarks/tasks/modify_to_spec/mts_001_thin_the_nozzle/specs/parts/nozzle.py
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

# The two halves of the task, as two checks that pull in opposite directions.
# `check_mass` is what the starter fails (1078 g at wall = 3.0) and `check_wall`
# is what an over-thinned answer fails: the grid-4 sampler lands on the exit-lip
# chamfer (0.2 * wall by construction) and reads 1.020 mm at wall 3.0,
# 0.867 mm at 2.5 and 0.707 mm at 2.0, so 0.8 mm is the example's own ENG-014
# floor and it makes wall = 2.0 a red rather than a cheaper answer. `grid` is
# pinned at 4 — the default grid of 8 lands on the lip chamfer and jitters.
SPECS = [
    _bench_check_valid(name="valid", requirement="MTS-001"),
    _bench_check_wall(min_mm=0.8, grid=4, name="exit_lip",
                      requirement="MTS-001"),
    _bench_check_mass(max_g=900.0, name="mass_budget", requirement="MTS-001"),
    _bench_check_bbox(within_mm=(86.5, 86.5, 199.5), name="envelope",
                      requirement="MTS-001"),
]
