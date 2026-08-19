# benchmarks/tasks/model_from_drawing/mfd_004_shaft_collar/specs/parts/shaft_collar.py
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

# `check_wall` is a SAMPLED ray cast along the inward face normal, and on this
# part the thinnest thing it FINDS at grid 4 is the CLAMP SLIT, not a wall: a
# ray leaving one slit face lands on the other 3.00 mm away, well under the
# 10 mm radial wall (outer_r - bore_r).
#
# The part's true minimum ligament is thinner than that and the sampler does
# not land on it: the pinch-screw bore stands off the rim by
# outer_r - screw_offset - screw_r = 20 - 15 - 2.5 = **2.50 mm**. The floor is
# therefore 2.2 and not 2.5 — a floor sitting exactly on a ligament the
# CURRENT grid happens to miss is a check that flips on a sampling change
# rather than on the geometry, which is the opposite of what a spec is for.
# At 2.2 a narrower-than-drawn slit (2.0 mm) is still red, and the real claim
# that the slit reaches the rim is carried by the `slit_opens_the_rim` metric
# window (bbox_x = sqrt(20^2 - 1.5^2) = 39.9437, where an unsplit collar reads
# 40.0). `grid` is pinned at 4: changing it changes the measurement.
SPECS = [
    _bench_check_valid(name="valid", requirement="MFD-004"),
    _bench_check_wall(min_mm=2.2, grid=4, name="slit_and_wall",
                      requirement="MFD-004"),
    _bench_check_bbox(within_mm=(40.2, 40.2, 15.2), name="envelope",
                      requirement="MFD-004"),
]
