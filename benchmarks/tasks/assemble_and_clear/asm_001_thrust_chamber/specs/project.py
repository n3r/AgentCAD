"""Project-scope rubric for bench task assemble_and_clear/asm_001_thrust_chamber.

Copied from ``examples/rocketry/specs.py`` (the SPECS block, lines 20-29) with
the requirement id **INT-003 kept**: the bench measures the example's own
stated interface requirement, not a bench-invented one.

This file is copied WHOLESALE over ``<copy>/specs.py`` in the scoring cell, so
it re-binds the project-scope ``SPECS`` and any project block the candidate
wrote for itself is discarded.

INT-003 is the interface requirement: the stack is bolted through gaskets, so
every stacked face keeps a deliberate 0.2-0.5 mm allowance and no two
instances may ever touch. Both clearances are measured with the conservative
``analysis(p)`` envelope, so a reported gap is never larger than the real one.

Measured on the reference placement: interference 0.0 mm3 over 3 instances,
``flange_bore_gap`` 0.500 mm, ``injector_gasket_gap`` 0.200 mm.
"""

from agentcad.toolkit.specs import check_clearance, check_interference_free

SPECS = [
    check_interference_free(requirement="INT-003"),
    # the flange slips over the chamber barrel: 0.5 mm radial as shipped, and
    # it can only grow as the wall thins
    check_clearance("flange_1", "nozzle_1", min_mm=0.3,
                    name="flange_bore_gap", requirement="INT-003"),
    # the injector plate caps the stack 0.2 mm above the rim (the head gasket)
    check_clearance("injector_plate_1", "nozzle_1", min_mm=0.15,
                    name="injector_gasket_gap", requirement="INT-003"),
]
