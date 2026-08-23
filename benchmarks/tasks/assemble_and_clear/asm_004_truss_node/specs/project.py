"""Project-scope rubric for bench task assemble_and_clear/asm_004_truss_node.

This file is copied WHOLESALE over ``<copy>/specs.py`` in the scoring cell, so
it re-binds the project-scope ``SPECS`` and any project block the candidate
wrote for itself is discarded.

STL-004 is the fabrication requirement: nothing in a welded node is modelled
in contact. Every member is drawn with a real root gap so the weld has
somewhere to go and so the erector can drop the pieces in — a model where two
members share material is a model that cannot be built.

Every root gap is graded as a **two-sided window**. The floor is the weld gap;
the ceiling is the placement, because a member parked clear of the node is not
set out, it is merely present.

Measured on the reference placement: interference 0.0 mm3 over 4 instances,
``gusset_seat`` 2.000 mm (in [1.0, 3.0]), ``left_bracket_seat`` 0.500 mm and
``right_bracket_seat`` 0.500 mm (in [0.25, 1.0]), ``left_web_gap`` 0.500 mm
and ``right_web_gap`` 0.500 mm (in [0.25, 1.0]).

``gusset_seat`` reads 2.000 mm and not the 1 mm the prompt asks for because the
gusset's lower edge sits at Z = 21 over the plate's 1 mm-deep column-footprint
recess, whose floor is Z = 19: `check_clearance` reports the closest approach
between the two solids, and here that is the recess and not the top face. The
1.0 mm floor is therefore satisfied with a full millimetre of margin by any
placement that does what the prompt says (edge at Z >= 21), and a gusset
dropped far enough to lose it is already touching the plate outside the recess,
which ``no_interference`` catches first. The 3.0 mm ceiling is the mirror: it
tracks the edge one for one (a gusset 1 mm higher measures 3.000 mm), so it
reds an edge above Z ~= 22 and every parked gusset.

The four instances give six pairs. ``no_interference`` covers all six; the
five ``check_clearance`` rows name the five gaps the prompt states in words,
so a candidate is never measured on a gap it was not told about. The sixth
pair — ``bracket_left`` to ``bracket_right``, measured 11.000 mm — carries no
row: the two brackets neither seat on nor weld to each other, the prompt
states no gap between them, and a window there would be a number invented by
the rubric. Each bracket's own placement is already two-sidedly graded
against the plate it stands on and the web it lies against.
"""

from agentcad.toolkit.specs import check_clearance, check_interference_free

SPECS = [
    check_interference_free(name="no_interference", requirement="STL-004"),
    check_clearance("gusset_1", "base_plate_1", min_mm=1.0, max_mm=3.0,
                    name="gusset_seat", requirement="STL-004"),
    # each bracket floats 0.5 mm above the plate's top face — stated in the
    # prompt, and until now measured by nothing
    check_clearance("bracket_left", "base_plate_1", min_mm=0.25, max_mm=1.0,
                    name="left_bracket_seat", requirement="STL-004"),
    check_clearance("bracket_right", "base_plate_1", min_mm=0.25, max_mm=1.0,
                    name="right_bracket_seat", requirement="STL-004"),
    # and each lies against its face of the gusset web across a root gap
    check_clearance("bracket_left", "gusset_1", min_mm=0.25, max_mm=1.0,
                    name="left_web_gap", requirement="STL-004"),
    check_clearance("bracket_right", "gusset_1", min_mm=0.25, max_mm=1.0,
                    name="right_web_gap", requirement="STL-004"),
]
