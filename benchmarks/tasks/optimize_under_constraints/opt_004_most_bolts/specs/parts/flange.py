# benchmarks/tasks/optimize_under_constraints/opt_004_most_bolts/specs/parts/flange.py
#
# The rubric. This block is appended to the END of the candidate's part script,
# so it must RE-BIND `SPECS` (never `+=`): the last module-level binding wins,
# and any `SPECS` the candidate authored for itself is discarded. Every
# constructor is imported under a `_bench_` alias because the candidate's own
# module namespace is in scope here — an alias makes a same-named module-level
# function in the candidate script irrelevant.
#
# These rows are the CONSTRAINTS only; the objective (as many bolt holes as
# will fit) lives in reference/metrics.json, where a graded one-sided window
# can express "more is better" and a pass/fail spec row cannot.
#
# `bolt_circle_ligament` is the example's own INT-003 check at a 3 mm floor
# instead of 2 mm, stated in measured terms: the reference reads 6.500 mm at
# grid=4 and a candidate that pushes the bolt circle out to Ø130 reads
# 0.972 mm and is red. grid=4 is pinned for the reason the example pins it —
# a finer grid samples the rim and bore chamfers instead of the ligament.
# `bore_preserved` counts the bore's own circular edges rather than trusting a
# parameter: at inner_d = 100 it reads 0 matching edges and fails, which is
# also the cheapest way to buy room for more bolts.
from agentcad.toolkit.specs import (
    check_bbox as _bench_check_bbox,
    check_that as _bench_check_that,
    check_valid as _bench_check_valid,
    check_wall as _bench_check_wall,
)


def _bench_bore_preserved(part, metrics):
    """The Ø87 bore is still there — two circular edges of radius 43.5."""
    from build123d import GeomType
    return bool(len([edge for edge in part.edges().filter_by(GeomType.CIRCLE)
                     if abs(edge.radius - 43.5) < 0.05]) >= 2)


def _bench_outer_diameter(part, metrics):
    """The ring still measures Ø140 across and 14 mm thick."""
    low, high = metrics["bbox"]["min"], metrics["bbox"]["max"]
    return bool(high[0] - low[0] >= 139.85
                and high[1] - low[1] >= 139.85
                and high[2] - low[2] >= 13.9)


SPECS = [
    _bench_check_valid(name="valid", requirement="OPT-004"),
    _bench_check_wall(min_mm=3.0, grid=4, name="bolt_circle_ligament",
                      requirement="OPT-004"),
    _bench_check_that(_bench_bore_preserved, name="bore_preserved",
                      requirement="OPT-004"),
    _bench_check_that(_bench_outer_diameter, name="outer_diameter",
                      requirement="OPT-004"),
    _bench_check_bbox(within_mm=(140.3, 140.3, 14.2), name="envelope",
                      requirement="OPT-004"),
]
