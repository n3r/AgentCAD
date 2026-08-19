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

The five instances give ten pairs, and ``no_interference`` covers all ten; the
two ``check_clearance`` rows name the two gaps the prompt states in words.

Measured on the reference placement: interference 0.0 mm3 over 5 instances,
``pin_bore_gap`` 0.125 mm (the small end's pin + 0.25 bore) and
``big_end_joint`` 0.050 mm. The other measured gaps, for the record, are
pin-to-piston 0.100 mm, bolts-to-body 0.550 mm, bolts-to-cap 0.200 mm and
piston-to-rod 2.000 mm.
"""

from agentcad.toolkit.specs import check_clearance, check_interference_free

SPECS = [
    check_interference_free(name="no_interference", requirement="ENG-021"),
    check_clearance("wrist_pin_1", "rod_1", min_mm=0.05, name="pin_bore_gap",
                    requirement="ENG-021"),
    check_clearance("rod_cap_1", "rod_1", min_mm=0.02, name="big_end_joint",
                    requirement="ENG-021"),
]
