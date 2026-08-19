The project already holds the enclosure part `enclosure_base` sized for a small
board. Modify it to take a bigger one.

Requirement: the inner cavity must measure **at least 134 mm x 84 mm** — a
128 x 78 mm PCB with 3 mm of clearance on every side.

Constraints:

- `wall` stays 2.5 mm, so an outer dimension of D gives an inner cavity of
  D - 5 mm. `height` stays 30 mm.
- `length` and `width` are the only parameters you may change, and each must
  land on a **whole 10 mm** value. Keep the shell as small as the cavity
  requirement allows — an oversized box is a rejected design.
- The corner screw bosses, the PCB standoffs and the ventilation slots are all
  derived from the shell dimensions in the script; they must follow the new
  size rather than be re-placed by hand.

Datum: unchanged. The shell's outside floor lies on Z = 0, the box is centred
on the origin in X and Y, the open top faces +Z and the length runs along X.
