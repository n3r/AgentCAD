# benchmarks/tasks/fix_the_broken_part/fix_003_wall_red/specs/parts/enclosure_base.py
#
# The rubric. This block is appended to the END of the candidate's part script,
# so it must RE-BIND `SPECS` (never `+=`): the last module-level binding wins,
# and any `SPECS` the candidate authored for itself is discarded. Every
# constructor is imported under a `_bench_` alias because the candidate's own
# module namespace is in scope here — an alias makes a same-named module-level
# function in the candidate script irrelevant.
from agentcad.toolkit.specs import (
    check_bbox as _bench_check_bbox,
    check_mass as _bench_check_mass,
    check_valid as _bench_check_valid,
)

# There is deliberately NO `check_wall` here, and the reason is measured, not
# stylistic: this shell's four corner screw bosses are tangent to the inner
# walls (they are placed at `length/2 - wall - br + 0.5`, i.e. embedded 0.5 mm),
# and the ray sampler reads the vanishing thickness at that tangency line
# rather than the wall. Measured on the SHIPPED part at wall = 2.5:
# 0.0095 mm at grid 12, at (-46.67, 27.5, 3.65) — and the same artefact, to the
# same order, at wall = 1.2. A `check_wall` floor here is a row that can neither
# pass the right answer nor discriminate the wrong one.
#
# So the 2.5 mm wall is stated in the terms this part CAN be measured in: its
# mass. A shelled box's mass is very nearly linear in the wall, and the three
# candidate walls are far apart — measured, in ABS:
#   wall 1.2 -> 21.7708 g   wall 2.0 -> 33.3084 g   wall 2.5 -> 40.2343 g
# The window is +/-1.5 % of the reference, so it fails a 1.2 mm shell by 45 %
# and a 2.0 mm shell by 17 %, and it passes any answer that really is 2.5 mm.
SPECS = [
    _bench_check_valid(name="valid", requirement="FIX-003"),
    _bench_check_mass(min_g=39.631, max_g=40.838, name="shell_wall",
                      requirement="FIX-003"),
    _bench_check_bbox(within_mm=(100.2, 60.2, 30.2), name="envelope",
                      requirement="FIX-003"),
]
