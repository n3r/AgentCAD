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

:data:`TOUCHING` is how the two seated pairs are graded anyway. The worker's
test is ``distance >= min_mm - _slack(min_mm)`` with
``_slack(x) = max(1e-9, abs(x) * 1e-9)``, so a floor cannot fail exactly when
``min_mm - max(1e-9, min_mm * 1e-9) <= 0`` — that is, for any ``min_mm`` at or
below today's absolute slack floor of ``1e-9``. It is a **family** of values,
not a single admissible one: ``_positive`` accepts any positive float, and
``1e-9`` is the LARGEST member of that family, not the smallest. Sitting on
the boundary is what makes it fragile — ``1e-9`` cannot fail only because the
comparison lands on ``0.0 >= 0.0`` exactly — so the constant is ``1e-12``,
three orders inside the family, which keeps working for any absolute slack
floor down to ``1e-12`` and is semantically identical today.

The row is therefore a **ceiling with a floor that cannot fail**, declared that
way on purpose: the seating these two pairs state in words is *how close*,
never *how far*, and material sharing space is still caught — by
``no_interference``, which is the row that owns overlap.

The coupling, stated plainly: if ``core.specs._slack`` ever tightens below
``1e-12``, or if ``BRepExtrema_DistShapeShape`` stops returning exactly ``0.0``
for coincident faces, these two rows go red **on the reference itself**. The
tripwire is **manual**: no test pins the assembly references at 1.0
(``tests/test_bench_scoring.py``'s reference test covers the seed task only),
so such a change surfaces as a red reference in a scored run — ``agentcad bench
score <bundle>/reference/project`` — and **not** in ``make test``. The measured
numbers below are what that run is checked against.

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

#: "Touching is allowed here" — see the module docstring. Not a clearance: a
#: floor three orders inside the family of floors that cannot fail (anything
#: at or below ``_slack``'s absolute 1e-9), rather than the boundary value.
TOUCHING = 1e-12

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
