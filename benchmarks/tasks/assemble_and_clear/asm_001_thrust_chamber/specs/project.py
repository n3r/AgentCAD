"""Project-scope rubric for bench task assemble_and_clear/asm_001_thrust_chamber.

Copied from ``examples/rocketry/specs.py`` (the SPECS block, lines 20-29) with
the requirement id **INT-003 kept**: the bench measures the example's own
stated interface requirement, not a bench-invented one.

This file is copied WHOLESALE over ``<copy>/specs.py`` in the scoring cell, so
it re-binds the project-scope ``SPECS`` and any project block the candidate
wrote for itself is discarded.

INT-003 is the interface requirement: the stack is bolted through gaskets, so
every stacked face keeps a deliberate 0.2-0.5 mm allowance and no two
instances may ever touch. Every clearance is measured with the conservative
``analysis(p)`` envelope, so a reported gap is never larger than the real one.

Every row is a **two-sided window**: the floor is non-interference, the
ceiling is placement. A candidate that creates the three instances and parks
them 500 mm apart passes the floors and fails every ceiling.

Measured on the reference placement: interference 0.0 mm3 over 3 instances,
``flange_bore_gap`` 0.500 mm (in [0.3, 1.0]), ``injector_gasket_gap``
0.200 mm (in [0.15, 0.5]) and ``head_face_stack_gap`` 0.400 mm (in
[0.25, 1.0]).

All three pairs of the three instances are windowed, so **any** one of them
moved off the stack fails at least two rows.
"""

from agentcad.toolkit.specs import check_clearance, check_interference_free

SPECS = [
    check_interference_free(requirement="INT-003"),
    # the flange slips over the chamber barrel: 0.5 mm radial as shipped, and
    # it can only grow as the wall thins. The 1.0 mm ceiling is the placement
    # half — a flange that is not around the barrel is not seated on it.
    check_clearance("flange_1", "nozzle_1", min_mm=0.3, max_mm=1.0,
                    name="flange_bore_gap", requirement="INT-003"),
    # the injector plate caps the stack 0.2 mm above the rim (the head
    # gasket). The ceiling is INT-003's own stated allowance, 0.5 mm.
    check_clearance("injector_plate_1", "nozzle_1", min_mm=0.15, max_mm=0.5,
                    name="injector_gasket_gap", requirement="INT-003"),
    # the two gasket allowances back to back across the head face plane:
    # 0.2 mm below + 0.2 mm above = 0.400 mm measured. This is the row that
    # pins the flange's AXIAL placement — the bore gap is radial and blind to
    # it (a flange dropped 3 mm still measures 0.500 mm there, and 3.400 mm
    # here).
    check_clearance("flange_1", "injector_plate_1", min_mm=0.25, max_mm=1.0,
                    name="head_face_stack_gap", requirement="INT-003"),
]
