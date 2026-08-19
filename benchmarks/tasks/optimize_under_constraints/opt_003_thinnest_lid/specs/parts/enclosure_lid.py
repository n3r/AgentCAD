# benchmarks/tasks/optimize_under_constraints/opt_003_thinnest_lid/specs/parts/enclosure_lid.py
#
# The rubric. This block is appended to the END of the candidate's part script,
# so it must RE-BIND `SPECS` (never `+=`): the last module-level binding wins,
# and any `SPECS` the candidate authored for itself is discarded. Every
# constructor is imported under a `_bench_` alias because the candidate's own
# module namespace is in scope here — an alias makes a same-named module-level
# function in the candidate script irrelevant.
#
# These rows are the CONSTRAINTS only; the objective (minimise material) lives
# in reference/metrics.json, where a graded one-sided window can express "less
# is better" and a pass/fail spec row cannot.
#
# No `check_wall`: measured on this part at grid=4 the sampler reports
# 0.200 mm on EVERY variant in range — it lands on the 0.2 mm BOSS_RELIEF
# recess in the plate underside, not on a wall — so a floor under it would be
# red on the reference itself and could neither pass nor discriminate.
# `lip_depth` and `plate_thickness` are the two halves of the fit stated in the
# terms a bounding box can actually see: the reference reads z in
# [-1.5, +2.0], and both floors sit 0.05 mm inside that.
# `screw_holes` counts the Ø3 hole edges directly (one circular edge per
# countersunk hole, four holes) rather than trusting a parameter: at
# screw_d = 5 it reads 0 matching edges and fails.
from agentcad.toolkit.specs import (
    check_bbox as _bench_check_bbox,
    check_that as _bench_check_that,
    check_valid as _bench_check_valid,
)


def _bench_screw_holes(part, metrics):
    """Four Ø3 countersunk screw holes — four circular edges of radius 1.5."""
    from build123d import GeomType
    return bool(len([edge for edge in part.edges().filter_by(GeomType.CIRCLE)
                     if abs(edge.radius - 1.5) < 0.05]) >= 4)


def _bench_footprint(part, metrics):
    """The lid still covers the base it seats on."""
    low, high = metrics["bbox"]["min"], metrics["bbox"]["max"]
    return bool(high[0] - low[0] >= 99.5 and high[1] - low[1] >= 59.5)


def _bench_lip_depth(part, metrics):
    """The lip still reaches 1.5 mm into the base cavity."""
    return bool(metrics["bbox"]["min"][2] <= -1.45)


def _bench_plate_thickness(part, metrics):
    """The plate above the underside is still 2.0 mm of material."""
    return bool(metrics["bbox"]["max"][2] >= 1.95)


SPECS = [
    _bench_check_valid(name="valid", requirement="OPT-003"),
    _bench_check_that(_bench_footprint, name="footprint",
                      requirement="OPT-003"),
    _bench_check_that(_bench_lip_depth, name="lip_depth",
                      requirement="OPT-003"),
    _bench_check_that(_bench_plate_thickness, name="plate_thickness",
                      requirement="OPT-003"),
    _bench_check_that(_bench_screw_holes, name="screw_holes",
                      requirement="OPT-003"),
    _bench_check_bbox(within_mm=(100.3, 60.3, 6.3), name="envelope",
                      requirement="OPT-003"),
]
