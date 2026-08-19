Model the clamping shaft collar shown in the attached drawing.

Create a project part with the id `shaft_collar`. It is a Ø40 outside, Ø20
bore ring, 15 mm long, split by a 3 mm wide clamp slit that runs the full
15 mm length and cuts right through the wall on the +X side (the slit is
centred on the XZ plane, so it removes the material between Y = -1.5 and
Y = +1.5 out to the rim). One Ø5 pinch-screw hole goes straight through both
clamp lugs: its axis is parallel to Y, 15 mm out from the collar axis on +X,
at mid-length (Z = 7.5).

Material: ASTM A36 steel (`steel_a36`).

Datum: the collar's bottom face lies on Z = 0 and its bore axis is the Z axis,
so the part spans Z = 0 to Z = 15. The clamp slit opens toward +X and the
pinch-screw hole runs along Y.
