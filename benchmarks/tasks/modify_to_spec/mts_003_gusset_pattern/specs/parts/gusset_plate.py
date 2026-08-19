# benchmarks/tasks/modify_to_spec/mts_003_gusset_pattern/specs/parts/gusset_plate.py
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
)

# The bolt pattern is not directly measurable, but the plate outline is the
# convex hull of the three member footprints, so the pattern is what SETS the
# outline and the outline is the honest measurement of it.
#
# Chord reach: 2 * ((n_rows - 1) * pitch / 2 + edge_dist) with the diagonal
# corners beyond it — 478.00 mm on the reference, 272.94 mm on the starter.
# Diagonal reach: 279.00 mm on the reference, 176.47 mm on the starter. The
# floors sit ~8 mm under each reference value: close enough that a three-row or
# a 50 mm-pitch answer misses them, loose enough that a legitimately different
# hull rounding does not.
SPECS = [
    _bench_check_valid(name="valid", requirement="MTS-003"),
    _bench_check_that(lambda part, metrics:
                      metrics["bbox"]["max"][0] - metrics["bbox"]["min"][0]
                      >= 470.0,
                      name="chord_reach", requirement="MTS-003"),
    _bench_check_that(lambda part, metrics:
                      metrics["bbox"]["max"][1] - metrics["bbox"]["min"][1]
                      >= 272.0,
                      name="diagonal_reach", requirement="MTS-003"),
    _bench_check_bbox(within_mm=(482.0, 283.0, 10.5), name="envelope",
                      requirement="MTS-003"),
]
