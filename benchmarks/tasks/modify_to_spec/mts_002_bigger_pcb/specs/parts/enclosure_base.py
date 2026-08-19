# benchmarks/tasks/modify_to_spec/mts_002_bigger_pcb/specs/parts/enclosure_base.py
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

# The cavity requirement, measured. `check_wall` is deliberately NOT here: on
# this shelled, vented, filleted box the grid-4 sampler finds 0.029 mm at a
# ventilation-slot corner, which is a sampling artefact and not a wall — a
# floor drawn under it would be a check that means nothing.
#
# The requirement is a cavity, and the cavity is the shell minus two walls, so
# the two `check_that` rows read the OUTER extents and state the arithmetic
# out loud: 134 + 2 * 2.5 = 139 and 84 + 2 * 2.5 = 89. They are the rows the
# starter (100 x 60) fails; `envelope` is the row that stops "make it huge".
SPECS = [
    _bench_check_valid(name="valid", requirement="MTS-002"),
    _bench_check_that(lambda part, metrics:
                      metrics["bbox"]["max"][0] - metrics["bbox"]["min"][0]
                      >= 139.0,
                      name="cavity_length", requirement="MTS-002"),
    _bench_check_that(lambda part, metrics:
                      metrics["bbox"]["max"][1] - metrics["bbox"]["min"][1]
                      >= 89.0,
                      name="cavity_width", requirement="MTS-002"),
    _bench_check_bbox(within_mm=(140.5, 90.5, 30.5), name="envelope",
                      requirement="MTS-002"),
]
