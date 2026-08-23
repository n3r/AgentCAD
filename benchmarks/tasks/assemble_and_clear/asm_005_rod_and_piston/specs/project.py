"""Project-scope rubric for bench task assemble_and_clear/asm_005_rod_and_piston.

This file is copied WHOLESALE over ``<copy>/specs.py`` in the scoring cell, so
it re-binds the project-scope ``SPECS`` and any project block the candidate
wrote for itself is discarded.

ENG-021 is the running-clearance requirement for one piston-and-rod set. The
wrist pin is a FLOATING pin: it turns in the piston bosses and in the rod's
small end, so neither joint may be modelled in contact, and the rod cap is
drawn 0.05 mm off the body's joint face because that gap is the bearing
shell's crush height. The bolts run through both halves and must not share
material with either.

Every row is a **two-sided window**: the floor is the running clearance, the
ceiling is the placement — a part parked clear of the set is not assembled.

Measured on the reference placement: interference 0.0 mm3 over 5 instances,
``pin_bore_gap`` 0.125 mm (in [0.05, 0.25]), ``big_end_joint`` 0.050 mm (in
[0.02, 0.15]), ``pin_boss_gap`` 0.100 mm (in [0.05, 0.2]), ``bolt_body_gap``
0.550 mm (in [0.3, 1.1]), ``bolt_cap_gap`` 0.200 mm (in [0.1, 0.4]) and
``small_end_gap`` 2.000 mm (in [1.0, 3.0]).

Which side of each window bites, measured: ``pin_bore_gap`` and
``pin_boss_gap`` are annular, so a pin moved off centre loses the FLOOR (the
near side closes) and a pin taken out of its bores loses the ceiling — the
piston lifted 1 mm reads 0.000 mm at ``pin_boss_gap``. ``small_end_gap`` is a
lateral approach between the piston skirt and the rod blade: it holds at
2.000 mm for a piston lifted 1 mm and reds only once the piston is off the
small end, which is exactly the "created it and parked it" case it exists for.

The five instances give ten pairs. ``no_interference`` covers all ten; the six
``check_clearance`` rows name the six relationships the prompt states in
words. The four remaining pairs — cap-to-piston (88.050 mm), cap-to-pin
(102.898 mm), bolts-to-piston (78.085 mm) and bolts-to-pin (93.176 mm) — carry
no row: those parts are at opposite ends of the rod, they neither seat nor run
on each other, and the distances between them are geometry rather than a
stated fit. Bounding them would be inventing numbers, and each of those four
instances is already two-sidedly graded by a row that does name it.
"""

from agentcad.toolkit.specs import check_clearance, check_interference_free

SPECS = [
    check_interference_free(name="no_interference", requirement="ENG-021"),
    check_clearance("wrist_pin_1", "rod_1", min_mm=0.05, max_mm=0.25,
                    name="pin_bore_gap", requirement="ENG-021"),
    check_clearance("rod_cap_1", "rod_1", min_mm=0.02, max_mm=0.15,
                    name="big_end_joint", requirement="ENG-021"),
    # the pin turns in the piston bosses too — stated in the prompt, and until
    # now measured by nothing
    check_clearance("wrist_pin_1", "piston_1", min_mm=0.05, max_mm=0.2,
                    name="pin_boss_gap", requirement="ENG-021"),
    # the bolt pair runs through both halves of the big end
    check_clearance("rod_bolts_1", "rod_1", min_mm=0.3, max_mm=1.1,
                    name="bolt_body_gap", requirement="ENG-021"),
    check_clearance("rod_bolts_1", "rod_cap_1", min_mm=0.1, max_mm=0.4,
                    name="bolt_cap_gap", requirement="ENG-021"),
    # and the piston sits over the rod's small end, skirt clear of the blade
    check_clearance("piston_1", "rod_1", min_mm=1.0, max_mm=3.0,
                    name="small_end_gap", requirement="ENG-021"),
]
