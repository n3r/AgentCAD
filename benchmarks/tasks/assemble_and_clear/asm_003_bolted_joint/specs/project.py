"""Project-scope rubric for bench task assemble_and_clear/asm_003_bolted_joint.

This file is copied WHOLESALE over ``<copy>/specs.py`` in the scoring cell, so
it re-binds the project-scope ``SPECS`` and any project block the candidate
wrote for itself is discarded.

FAS-001 is the joint requirement. Two faces in a bolted joint are SUPPOSED to
touch — the clamp plate lands flat on the tapped plate, and the screw head
lands flat on the clamp plate — so those two pairs are measured only by
``no_interference`` (contact is 0 mm of clearance, and 0 mm is not an
overlap). The one pair that must NOT close is the screw tip against the
bottom of the tapped hole: a screw that bottoms out cannot clamp anything,
and that is the row this rubric adds.

Measured on the reference placement: interference 0.0 mm3 over 3 instances,
``thread_clearance`` 1.177 mm (`cap_screw_1` to `tapped_plate_1`), against a
0.5 mm floor. The two seated pairs measure 0.000 mm, which is exactly why
neither is a ``check_clearance`` row.
"""

from agentcad.toolkit.specs import check_clearance, check_interference_free

SPECS = [
    check_interference_free(name="no_interference", requirement="FAS-001"),
    check_clearance("cap_screw_1", "tapped_plate_1", min_mm=0.5,
                    name="thread_clearance", requirement="FAS-001"),
]
