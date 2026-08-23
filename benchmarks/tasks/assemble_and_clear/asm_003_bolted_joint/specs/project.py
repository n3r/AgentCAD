"""Project-scope rubric for bench task assemble_and_clear/asm_003_bolted_joint.

This file is copied WHOLESALE over ``<copy>/specs.py`` in the scoring cell, so
it re-binds the project-scope ``SPECS`` and any project block the candidate
wrote for itself is discarded.

FAS-001 is the joint requirement, and this bundle is the awkward one: two of
its three pairs are SUPPOSED to touch. The clamp plate lands flat on the
tapped plate and the screw head lands flat on the clamp plate, so both pairs
measure exactly **0.000 mm** — and ``check_clearance`` requires ``min_mm > 0``.
Left at that, the clamp plate's placement was ungraded and a candidate could
create it and park it 500 mm away for full marks (the v1 limitation).

:data:`TOUCHING` is how the two seated pairs are graded anyway. It is the
smallest positive floor the evaluator's own tolerance admits — the worker's
test is ``distance >= min_mm - _slack(min_mm)`` and ``_slack(1e-9)`` is
``1e-9``, so the comparison is ``0.0 >= 0.0`` and contact passes. The row is
therefore a **ceiling with a floor that cannot fail**, declared that way on
purpose: the seating these two pairs state in words is *how close*, never *how
far*, and material sharing space is still caught — by ``no_interference``,
which is the row that owns overlap.

If ``core.specs._slack`` ever stops admitting an exact zero at this floor, the
two seated rows go red on the reference itself; the numbers below are the
tripwire.

Measured on the reference placement: interference 0.0 mm3 over 3 instances,
``clamp_seat`` 0.000 mm and ``head_seat`` 0.000 mm (both in
(0, 0.5] by contact), ``thread_clearance`` 1.177 mm (in [0.5, 2.0]).

What each ceiling grades, measured:

* ``head_seat`` — the screw lifted 1 mm off the clamp plate measures 1.000 mm
  and reds. This is the row that grades the screw's seating depth, because
  ``thread_clearance`` cannot: its 1.177 mm is a *radial* approach inside the
  counterbore and it reads the same 1.177 mm for a screw lifted 1 mm and for
  one lifted 5 mm.
* ``thread_clearance`` — the floor is the screw bottoming out (0.000 mm two
  millimetres down); the 2.0 mm ceiling is the screw leaving the tapped
  plate's bore altogether (3.222 mm eight millimetres up).
* ``clamp_seat`` — the only row that says the clamp plate is in the joint at
  all.
"""

from agentcad.toolkit.specs import check_clearance, check_interference_free

#: "Touching is allowed here" — see the module docstring. Not a clearance.
TOUCHING = 1e-9

SPECS = [
    check_interference_free(name="no_interference", requirement="FAS-001"),
    # the clamp plate lands flat on the tapped plate's top face: contact, and
    # in no case more than half a millimetre off it
    check_clearance("clamp_plate_1", "tapped_plate_1", min_mm=TOUCHING,
                    max_mm=0.5, name="clamp_seat", requirement="FAS-001"),
    # the screw head lands flat on the clamp plate's top face, same reading
    check_clearance("cap_screw_1", "clamp_plate_1", min_mm=TOUCHING,
                    max_mm=0.5, name="head_seat", requirement="FAS-001"),
    # the screw must not bottom out in the blind hole, and must be in it
    check_clearance("cap_screw_1", "tapped_plate_1", min_mm=0.5, max_mm=2.0,
                    name="thread_clearance", requirement="FAS-001"),
]
