Model the toolmaker's vee block shown in the attached drawing.

Create a project part with the id `vee_block`. It is a 60 x 60 x 40 mm block.
A vee groove with a 90° included angle is cut into the top face and runs the
full 60 mm length along X: it is symmetric about the XZ plane, 15 mm deep, so
it opens to 30 mm wide at the top face and its apex line sits at Z = 25. Two
Ø8 clamp holes are drilled straight through the block along Y, on a 40 mm
pitch (X = ±20) with their axes 12 mm above the base.

Material: ASTM A36 steel (`steel_a36`).

Datum: the block's base lies on Z = 0 and the block is centred on the origin
in X and Y, so it spans X = -30 to +30, Y = -30 to +30 and Z = 0 to 40. The
vee groove runs along X and the clamp holes run along Y.
