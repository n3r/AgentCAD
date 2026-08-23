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
# `leg_thickness` is the row that makes this an optimisation rather than "read
# PARAMS, type the minimum". `thk` declares `min` 3.0 and the 4 mm floor binds
# a whole millimetre inside it, so the answer is the constraint rather than the
# end of the slider. Stated in measured terms, because grid=4 reads about
# 0.2 mm over the leg thickness itself: thk 3.0 -> 3.176 (red),
# thk 3.8 -> 3.992 (red), thk 4.0 -> 4.196 (green, the reference),
# thk 6.0 -> 6.235 (green). It is also the floor for a candidate that REWRITES
# the script, which is the cheapest way to lose mass and the one way this part
# can be made unbuildable. `bolt_pattern` counts the Ø14 hole edges directly
# (two circular edges per through hole, four holes) rather than trusting a
# parameter: at hole_d = 10 it reads 0 matching edges and fails.
#
# `material_density` is the row that stops the cheapest wrong answer of all.
# The objective is a mass, `mass_g = volume x density`, and the density comes
# from the manifest's material — which `update_part_script(material=...)` or
# `set_project_materials` changes in one call, no geometry touched. Measured:
# the starter re-materialled to `al6061` weighs 352.2 g and would clear both
# mass rungs. So the rubric measures the density the part is made of (A36 steel
# is 0.00785 g/mm³, 1% tolerance) AND reference/metrics.json carries a
# `volume_mm3` twin of each mass rung at the same ratios, which is
# density-invariant by construction. Either alone would leave the hole open.
from agentcad.toolkit.specs import (
    check_bbox as _bench_check_bbox,
    check_that as _bench_check_that,
    check_valid as _bench_check_valid,
    check_wall as _bench_check_wall,
)

#: A36 structural steel, g/mm³. The material the mass budget is written against.
_BENCH_DENSITY = 0.00785


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


def _bench_material_density(part, metrics):
    """Still made of A36 steel — mass over volume, to 1%."""
    volume = float(metrics["volume_mm3"])
    if volume <= 0.0:
        return False
    density = float(metrics["mass_g"]) / volume
    return bool(abs(density - _BENCH_DENSITY) <= _BENCH_DENSITY * 0.01)


SPECS = [
    _bench_check_valid(name="valid", requirement="OPT-001"),
    _bench_check_wall(min_mm=4.0, grid=4, name="leg_thickness",
                      requirement="OPT-001"),
    _bench_check_that(_bench_bolt_pattern, name="bolt_pattern",
                      requirement="OPT-001"),
    _bench_check_that(_bench_footprint, name="footprint",
                      requirement="OPT-001"),
    _bench_check_that(_bench_material_density, name="material_density",
                      requirement="OPT-001"),
    _bench_check_bbox(within_mm=(90.3, 80.3, 90.3), name="envelope",
                      requirement="OPT-001"),
]
