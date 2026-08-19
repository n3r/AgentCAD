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
# part the thinnest thing it finds is the CLAMP SLIT, not a wall: a ray leaving
# one slit face lands on the other 3.00 mm away, well under the 10 mm radial
# wall (outer_r - bore_r). So the limit is stated in MEASURED terms — 2.5 mm is
# "the slit is the drawing's 3 mm, not narrower" with sampling slack, and a
# candidate that leaves the collar unsplit reads 10 mm and passes it on the
# wall instead. `grid` is pinned at 4: a finer grid also samples the
# pinch-screw bore and jitters.
SPECS = [
    _bench_check_valid(name="valid", requirement="MFD-004"),
    _bench_check_wall(min_mm=2.5, grid=4, name="slit_and_wall",
                      requirement="MFD-004"),
    _bench_check_bbox(within_mm=(40.2, 40.2, 15.2), name="envelope",
                      requirement="MFD-004"),
]
