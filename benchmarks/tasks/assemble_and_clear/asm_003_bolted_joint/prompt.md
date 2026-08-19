The project holds the three parts of a bolted joint — `tapped_plate` (a base
plate with a blind M8 tapped hole and a counterbore), `clamp_plate` (the plate
being clamped, with a clearance hole) and `cap_screw` (an M8 socket-head cap
screw) — and **no assembly at all**: the instance list is empty.

Make the joint. Place exactly **three** instances and give them these ids,
because the design specs name them:

| instance id | part |
|---|---|
| `tapped_plate_1` | `tapped_plate` |
| `clamp_plate_1` | `clamp_plate` |
| `cap_screw_1` | `cap_screw` |

Requirements:

- `tapped_plate_1` sits at the **origin**, unrotated. Its **top face is
  Z = 0** and its 20 mm body hangs into **-Z**.
- `clamp_plate_1` lands **flat on that top face**, on the same axis and
  unrotated: its underside on **Z = 0**, its 8 mm growing into **+Z**. The two
  plates are meant to be in contact — a bolted joint clamps, it does not
  float.
- `cap_screw_1` goes in from above on the same axis, unrotated, with its
  **head seated flat on the clamp plate's top face**. The screw's local origin
  is the underside of its head — the seating plane.
- The screw must **not bottom out**: its tip has to keep **at least 0.5 mm**
  of clear space from the tapped plate. As shipped, the thread engagement and
  the screw length leave about 1.2 mm.
- **No two instances may overlap** by any volume. Faces that touch are fine;
  material that shares space is not.

Do not change any part script and do not change any part's parameters — this
task is the placement only.

Datum: world frame, no rotations. The joint axis is **Z**, the tapped plate's
top face is **Z = 0**, and the stack grows into **+Z**.
