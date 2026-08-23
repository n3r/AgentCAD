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

``seat_gap`` is a **two-sided window**: the floor is the snap fit, the ceiling
is the placement. Two instances give exactly one pair, so this single row is
the whole of what the rubric can say about where the lid is.

Measured on the reference placement (`lid_1` at Z = 30.1): interference
0.0 mm3 over 2 instances, ``seat_gap`` 0.100 mm — twice the 0.05 mm floor and
half the 0.2 mm ceiling.

What the ceiling grades, honestly: once the lid lifts off the rim the closest
approach stops being the 0.1 mm rim gap and becomes the lip's own 0.15 mm
radial clearance inside the cavity (`enclosure_lid.LIP_CLEARANCE`), so the
measurement SATURATES at 0.15 mm for a lid lifted anywhere between ~0.05 mm
and the 3 mm lip depth. The 0.2 mm ceiling therefore fails a lid whose lip has
come out of the cavity (measured 2.105 mm at Z = 35.1) and every parked lid,
and passes a lid floating within its own lip engagement. A tighter ceiling
(0.14 mm) would catch that too, and was rejected for an AUTHORING-tolerance
reason rather than a measurement one: the measurement is exact and repeatable
(0.10000000000000142 mm), but 0.14 mm leaves an agent 0.04 mm of room around a
seat height the prompt states as 0.1 mm — so a candidate that reasons its way
to a 0.15 mm seat, or rounds the snap gap up, reds a placement that is
substantially right. The 0.2 mm ceiling asks the question the task is about
(is the lid on the base, lip in the cavity?) and leaves the last two decimal
places to the reviewer.
"""

from agentcad.toolkit.specs import check_clearance, check_interference_free

SPECS = [
    check_interference_free(name="no_interference", requirement="ENC-002"),
    check_clearance("lid_1", "base_1", min_mm=0.05, max_mm=0.2,
                    name="seat_gap", requirement="ENC-002"),
]
