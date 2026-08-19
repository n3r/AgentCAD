# benchmarks/tasks/optimize_under_constraints/opt_002_stiffest_gusset/specs/parts/gusset_plate.py
#
# The rubric. This block is appended to the END of the candidate's part script,
# so it must RE-BIND `SPECS` (never `+=`): the last module-level binding wins,
# and any `SPECS` the candidate authored for itself is discarded. Every
# constructor is imported under a `_bench_` alias because the candidate's own
# module namespace is in scope here — an alias makes a same-named module-level
# function in the candidate script irrelevant.
#
# These rows are the CONSTRAINTS only; the objective (maximise the plate's
# thickness) lives in reference/metrics.json, where a graded one-sided window
# can express "thicker is better" and a pass/fail spec row cannot.
#
# `mass_budget` is the row that makes the task an optimisation rather than
# "type 25": the reference reads 2917.0270 g at 17 mm, and the next whole
# millimetre reads 3088.6168 g and is red. `reach` is the end-distance
# requirement restated as the thing a bounding box can actually see — the
# reference measures 234.7595 x 142.3797 mm, and dropping `edge_dist` from
# 27 mm to 26 mm reads 231.931 x 140.9655 mm and fails it.
#
# No `check_wall` here: on a flat plate the minimum wall IS the thickness, so
# a floor under it would restate the objective's own metric as a pass/fail row.
from agentcad.toolkit.specs import (
    check_bbox as _bench_check_bbox,
    check_mass as _bench_check_mass,
    check_that as _bench_check_that,
    check_valid as _bench_check_valid,
)


def _bench_reach(part, metrics):
    """The plate still spans its bolt groups plus the code end distance."""
    low, high = metrics["bbox"]["min"], metrics["bbox"]["max"]
    return bool(high[0] - low[0] >= 234.5 and high[1] - low[1] >= 142.2)


SPECS = [
    _bench_check_valid(name="valid", requirement="OPT-002"),
    _bench_check_mass(max_g=3000.0, name="mass_budget", requirement="OPT-002"),
    _bench_check_that(_bench_reach, name="reach", requirement="OPT-002"),
    _bench_check_bbox(within_mm=(273.2, 176.8, 25.2), name="envelope",
                      requirement="OPT-002"),
]
