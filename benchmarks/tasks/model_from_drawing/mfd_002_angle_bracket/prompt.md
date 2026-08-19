Model the erection angle bracket shown in the attached drawing.

Create a project part with the id `angle_bracket`. It is an L-section bracket:
both legs are 90 mm long measured from the outside faces, the section is
80 mm wide, and both legs are 10 mm thick. The inside corner carries an R6
structural fillet. Each leg is drilled with two Ø14 bolt holes through its
thickness; the two holes of a leg are on a 56 mm gauge across the 80 mm width
and their axes sit 53 mm from the outside face of the opposite leg — X = 53
on the horizontal leg, Z = 53 on the vertical leg.

Material: ASTM A36 structural steel (`steel_a36`).

Datum: the inside corner of the L is at the origin. The horizontal leg runs
along +X from X = 0 to X = 90 with its underside on Z = 0, the vertical leg
rises along +Z from that same corner, and the bracket is 80 mm wide along -Y,
so it spans Y = -80 to Y = 0.
