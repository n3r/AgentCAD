# benchmarks/tasks/modify_to_spec/mts_004_lighter_flywheel/specs/parts/flywheel.py
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
    check_that as _bench_check_that,
    check_valid as _bench_check_valid,
)

# `mass_budget` is the row the starter fails (4684 g at thickness = 22) and
# `od_preserved` is the row that stops the cheap answer of shrinking the disc
# instead of thinning it: at thickness = 19 the wheel is 3985 g, and every
# whole millimetre above that (20 -> 4218 g) is over budget.
#
# No `check_wall` here: the grid-4 sampler reports 4.0 mm — the weight-relief
# recess depth — at every thickness in the parameter's range, so a floor under
# it would be a row that can neither fail nor discriminate.
SPECS = [
    _bench_check_valid(name="valid", requirement="MTS-004"),
    _bench_check_mass(max_g=4200.0, name="mass_budget", requirement="MTS-004"),
    _bench_check_that(lambda part, metrics:
                      metrics["bbox"]["max"][0] - metrics["bbox"]["min"][0]
                      >= 199.5
                      and metrics["bbox"]["max"][1] - metrics["bbox"]["min"][1]
                      >= 199.5,
                      name="od_preserved", requirement="MTS-004"),
    _bench_check_bbox(within_mm=(200.5, 200.5, 22.5), name="envelope",
                      requirement="MTS-004"),
]
