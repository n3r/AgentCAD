"""Project-scope design specs for the thrust chamber assembly.

Part-scope intent (wall minima, mass budgets, predicates) lives in each part
script's ``SPECS``; this file holds only what spans parts — the checks that
need the *placed* assembly. It is an ordinary tracked file, so it branches,
merges, restores and undoes with the geometry it governs.

INT-003 is the interface requirement: the stack is bolted through gaskets, so
every stacked face keeps a deliberate 0.2-0.5 mm allowance and no two
instances may ever touch. Both clearances are measured with the conservative
``analysis(p)`` envelope, so a reported gap is never larger than the real one.

Assembly-scope checks are deferred at rebuild time (they are reported as
``skip``/``deferred`` there) and evaluated by ``run_specs`` and the proposal
gate.
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
