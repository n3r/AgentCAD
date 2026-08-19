# benchmarks/tasks/optimize_under_constraints/opt_005_shortest_screw/specs/parts/cap_screw.py
#
# The rubric, part scope. This block is appended to the END of the candidate's
# part script, so it must RE-BIND `SPECS` (never `+=`): the last module-level
# binding wins, and any `SPECS` the candidate authored for itself is discarded.
# Every constructor is imported under a `_bench_` alias because the candidate's
# own module namespace is in scope here — an alias makes a same-named
# module-level function in the candidate script irrelevant.
#
# These rows are the CONSTRAINTS only; the objective (the shortest screw the
# joint allows) lives in reference/metrics.json, where a graded one-sided
# window can express "shorter is better" and a pass/fail spec row cannot.
#
# `shank_reach` is the prompt's "the shank must project at least 3 mm past the
# clamp plate's underside" restated in the terms the built screw can be
# measured in. The screw's bearing face sits at Z = 8 and the clamp plate's
# underside is Z = 0, so reaching Z = -3 means head + shank >= 11 + 8 = 19 mm.
# Measured: length 11 reads 19.0 mm and passes; length 10 reads 18.0 and fails.
# The 18.9 floor is 0.1 mm of slack under the requirement, not a different one.
#
# No `check_wall` here: the shank of a cosmetically-threaded screw has no wall,
# and a floor under its diameter would be a row that can neither fail nor
# discriminate as `length` moves.
from agentcad.toolkit.specs import (
    check_that as _bench_check_that,
    check_valid as _bench_check_valid,
)


def _bench_shank_reach(part, metrics):
    """Head + shank reach from Z = 8 down to Z = -3: at least 19 mm overall."""
    low, high = metrics["bbox"]["min"], metrics["bbox"]["max"]
    return bool(high[2] - low[2] >= 18.9)


SPECS = [
    _bench_check_valid(name="valid", requirement="OPT-005"),
    _bench_check_that(_bench_shank_reach, name="shank_reach",
                      requirement="OPT-005"),
]
