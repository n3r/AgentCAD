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
# THE THREE LIGAMENTS ARE MEASURED BY TWO DIFFERENT ROWS, and which one sees
# which is the whole reason this task binds where it does:
#
# * `bolt_circle_ligament` is the example's own INT-003 check at a 3 mm floor
#   instead of 2 mm, and what the grid-4 sampler actually reads on this part is
#   the RIM (and, on a small bolt circle, the bore): it walks radially outward
#   from the bolt circle, through the 1.5 mm rim chamfer, so the number it
#   reports runs about 0.4 mm under the nominal rim ligament. Measured, at
#   n_bolts = 32: Ø122.5 -> 4.154, Ø123.5 -> 3.447, Ø124 -> 3.094,
#   Ø124.5 -> 2.740 (red), Ø125 -> 2.387 (red). That is the row that stops the
#   bolt circle walking outward, and it is the reason a 33rd bolt cannot be
#   bought with a bigger circle.
#   grid=4 is pinned for the reason the example pins it — grid=16 reads
#   0.022 mm on EVERY variant, reference included, because it samples the
#   chamfer itself and would be a row that can neither pass nor discriminate.
#
# * `bolt_spacing` is the NEIGHBOUR ligament, and it is a `check_that` because
#   the wall sampler does not see it at all: measured, a Ø124 circle carrying
#   42 holes leaves 0.27 mm between neighbours and `check_wall(3.0, grid=4)`
#   still reports 4.064 and passes it. The predicate measures the thing
#   directly — centre-to-centre spacing on the built part — so the row is the
#   requirement rather than a proxy for it.
#
# `bolt_pattern` is what keeps the objective honest. The objective counts
# `n_faces`, which on this ring is 8 + one cylindrical face per bolt hole, so a
# candidate that buys faces with geometry that is not a Ø9 bolt hole — a
# counterbore, a lightening pocket, or holes so tight they merge into a slot
# (n_bolts 48 on the shipped Ø118 circle reads 106 faces) — is measured here
# rather than rewarded there.
# `bore_preserved` counts the bore's own circular edges rather than trusting a
# parameter: at inner_d = 100 it reads 0 matching edges and fails, which is
# also the cheapest way to buy room for more bolts.
from agentcad.toolkit.specs import (
    check_bbox as _bench_check_bbox,
    check_that as _bench_check_that,
    check_valid as _bench_check_valid,
    check_wall as _bench_check_wall,
)

#: Ø9 bolt holes, so radius 4.5, and 3 mm of material between two of them is
#: 4.5 + 3 + 4.5 = 12 mm centre to centre. The row is written 0.05 mm under
#: that — measurement slack on the requirement, not a different requirement.
#: What the predicate measures is the straight-line distance between centres,
#: i.e. the CHORD `D * sin(pi/n)`, which is the honest reading of "3 mm of
#: material between two holes"; the arc `pi*D/n` is longer and would let a
#: pattern through that the metal does not.  At the reference (32 on Ø123.5)
#: the chord is 12.105 and the arc would have said 12.125.
_BENCH_BOLT_R = 4.5
_BENCH_MIN_PITCH = 11.95

#: The ring's own faces: top, bottom, rim and bore cylinders, two rim chamfers
#: and two bore chamfers. Measured constant across every variant built.
_BENCH_RING_FACES = 8


def _bench_bolt_centres(part):
    """Every Ø9 hole's centre on the built part, deduped over its two edges.

    `arc_center`, never `center()`: a hole that has merged into its neighbour
    survives as a trimmed arc, and an arc's centre of mass is a point ON the
    arc rather than the circle's centre — which would make a merged pattern
    look well spaced.
    """
    from build123d import GeomType
    centres: list = []
    for edge in part.edges().filter_by(GeomType.CIRCLE):
        if abs(edge.radius - _BENCH_BOLT_R) > 0.05:
            continue
        point = edge.arc_center
        key = (round(float(point.X), 3), round(float(point.Y), 3))
        if key not in centres:
            centres.append(key)
    return centres


def _bench_bolt_pattern(part, metrics):
    """Ø9 holes, and nothing on the ring but the ring and those holes."""
    centres = _bench_bolt_centres(part)
    return bool(len(centres) >= 4
                and int(metrics["n_faces"]) == _BENCH_RING_FACES + len(centres))


def _bench_bolt_spacing(part, metrics):
    """3 mm of material between neighbours: centres at least 12 mm apart."""
    centres = _bench_bolt_centres(part)
    if len(centres) < 2:
        return False
    closest = min(
        ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5
        for i, (ax, ay) in enumerate(centres)
        for bx, by in centres[i + 1:])
    return bool(closest >= _BENCH_MIN_PITCH)


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
    _bench_check_that(_bench_bolt_spacing, name="bolt_spacing",
                      requirement="OPT-004"),
    _bench_check_that(_bench_bolt_pattern, name="bolt_pattern",
                      requirement="OPT-004"),
    _bench_check_that(_bench_bore_preserved, name="bore_preserved",
                      requirement="OPT-004"),
    _bench_check_that(_bench_outer_diameter, name="outer_diameter",
                      requirement="OPT-004"),
    _bench_check_bbox(within_mm=(140.3, 140.3, 14.2), name="envelope",
                      requirement="OPT-004"),
]
