# benchmarks/tasks/optimize_under_constraints/opt_001_lightest_bracket/specs/parts/angle_bracket.py
#
# The rubric. This block is appended to the END of the candidate's part script,
# so it must RE-BIND `SPECS` (never `+=`): the last module-level binding wins,
# and any `SPECS` the candidate authored for itself is discarded. Every
# constructor is imported under a `_bench_` alias because the candidate's own
# module namespace is in scope here — an alias makes a same-named module-level
# function in the candidate script irrelevant.
#
# These rows are the CONSTRAINTS only; the objective (minimise mass) lives in
# reference/metrics.json, where a graded one-sided window can express "lighter
# is better" and a pass/fail spec row cannot.
#
# `leg_thickness` is stated in measured terms: the reference (thk = 6.0) reads
# 6.235 mm at grid=4, so the 4 mm floor is not reachable by moving `thk`, whose
# declared minimum is 6.0 — it is the floor for a candidate that REWRITES the
# script, which is the cheapest way to lose mass and the one way this part can
# be made unbuildable. `bolt_pattern` counts the Ø14 hole edges directly (two
# circular edges per through hole, four holes) rather than trusting a
# parameter: at hole_d = 10 it reads 0 matching edges and fails.
from agentcad.toolkit.specs import (
    check_bbox as _bench_check_bbox,
    check_that as _bench_check_that,
    check_valid as _bench_check_valid,
    check_wall as _bench_check_wall,
)


def _bench_bolt_pattern(part, metrics):
    """Two Ø14 through holes per leg — eight circular edges of radius 7."""
    from build123d import GeomType
    return bool(len([edge for edge in part.edges().filter_by(GeomType.CIRCLE)
                     if abs(edge.radius - 7.0) < 0.05]) >= 8)


def _bench_footprint(part, metrics):
    """The connection's own size: the bracket may not be shrunk to lose mass."""
    low, high = metrics["bbox"]["min"], metrics["bbox"]["max"]
    return bool(high[0] - low[0] >= 89.5
                and high[1] - low[1] >= 79.5
                and high[2] - low[2] >= 89.5)


SPECS = [
    _bench_check_valid(name="valid", requirement="OPT-001"),
    _bench_check_wall(min_mm=4.0, grid=4, name="leg_thickness",
                      requirement="OPT-001"),
    _bench_check_that(_bench_bolt_pattern, name="bolt_pattern",
                      requirement="OPT-001"),
    _bench_check_that(_bench_footprint, name="footprint",
                      requirement="OPT-001"),
    _bench_check_bbox(within_mm=(90.3, 80.3, 90.3), name="envelope",
                      requirement="OPT-001"),
]
