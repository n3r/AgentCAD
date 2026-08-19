"""Project-scope rubric for bench task assemble_and_clear/asm_002_lid_on_base.

This file is copied WHOLESALE over ``<copy>/specs.py`` in the scoring cell, so
it re-binds the project-scope ``SPECS`` and any project block the candidate
wrote for itself is discarded.

ENC-002 is the requirement the task states in words: the lid seats on the
base's rim and its lip plugs into the cavity, and the snap fit needs the two
mouldings to stay CLEAR of one another — a lid that presses into the base is
not a snap fit, it is an interference fit that will not close.

Both rows are measured against the conservative ``analysis(p)`` envelope where
a part declares one, so a reported gap is never larger than the real one.

Measured on the reference placement (`lid_1` at Z = 30.1): interference
0.0 mm3 over 2 instances, ``seat_gap`` 0.100 mm — twice the 0.05 mm floor.
"""

from agentcad.toolkit.specs import check_clearance, check_interference_free

SPECS = [
    check_interference_free(name="no_interference", requirement="ENC-002"),
    check_clearance("lid_1", "base_1", min_mm=0.05, name="seat_gap",
                    requirement="ENC-002"),
]
