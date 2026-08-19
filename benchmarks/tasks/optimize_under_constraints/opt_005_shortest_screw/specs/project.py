# benchmarks/tasks/optimize_under_constraints/opt_005_shortest_screw/specs/project.py
#
# The rubric, project scope. This file REPLACES `<copy>/specs.py` in every
# candidate — reference included — so a candidate cannot inflate the `specs`
# subscore by writing project checks of its own.
#
# The `optimize_under_constraints` weight row puts 0.00 on `interference`
# (design section 7.6), so the assembly half of this task is carried here, in
# rows the `specs` subscore owns. That is deliberate and not a workaround: the
# `interference` subscore measures clean instance PAIRS, while what this task
# needs is a named minimum clearance between two named instances, which only
# `check_clearance` states.
#
# Measured on the reference (length 11) and on the example's own screw
# (length 13): both read a 1.1766 mm minimum clearance to the tapped plate —
# the radial gap between the shank and the plain counterbore, which does not
# move with length. The starter (length 20) reads 0.0 mm and a 0.00549 mm³
# overlap with the tapped thread, and both rows are red. So the 1.0 mm floor
# is the "never reaches the thread" requirement, with 0.18 mm of margin on the
# answer and no margin at all for a screw that touches.
from agentcad.toolkit.specs import check_clearance, check_interference_free

SPECS = [
    check_interference_free(0.001, name="no_interference",
                            requirement="OPT-005"),
    check_clearance("cap_screw_1", "tapped_plate_1", 1.0,
                    name="thread_clearance", requirement="OPT-005"),
]
