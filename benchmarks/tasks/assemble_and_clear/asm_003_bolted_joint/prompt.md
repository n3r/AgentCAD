<!-- Reviewer note (stripped from the prompt the agent sees) — how the graded
     clearance windows were derived. Bounds come from the gap MEASURED on the
     reference placement:

       row               pair                          measured   window
       clamp_seat        clamp_plate_1 - tapped_plate_1 0.000 mm  (0, 0.5]
       head_seat         cap_screw_1 - clamp_plate_1    0.000 mm  (0, 0.5]
       thread_clearance  cap_screw_1 - tapped_plate_1   1.177 mm  0.5-2.0

     Two of the three pairs are SUPPOSED to touch, and `check_clearance`
     requires `min_mm > 0`, so `clamp_seat` and `head_seat` carry a floor
     that cannot fail (`specs/project.py`'s `TOUCHING`, 1e-12 mm). Any floor
     at or below `_slack`'s absolute 1e-9 works — 1e-9 is the LARGEST such
     value, not the smallest, so the constant sits three orders inside the
     family instead of on its boundary. They are ceilings; overlap stays
     `no_interference`'s row. Without them the clamp plate's placement was
     ungraded entirely. The tripwire is manual: no test pins these references
     at 1.0, so a `_slack` change shows up in a scored run, not in
     `make test`.

     Measured perturbations behind the ceilings: screw lifted 1 mm ->
     head_seat 1.000 mm (red), thread_clearance still 1.177 mm (its approach
     is radial inside the counterbore, so it is blind to a 1 mm lift and to a
     5 mm one); screw 8 mm up -> thread_clearance 3.222 mm (red); screw 2 mm
     down (bottomed) -> thread_clearance 0.000 mm (red). -->

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
- The screw must **not bottom out**: it has to keep **at least 0.5 mm** of
  clear space from the tapped plate everywhere, its tip included. What is
  measured is the closest approach between the two solids, which as shipped is
  about 1.2 mm — see the note under the graded gaps for where that 1.2 mm
  actually is.
- **No two instances may overlap** by any volume. Faces that touch are fine;
  material that shares space is not.

Seating is graded, not only non-interference — a part parked clear of the
joint is not assembled. The three graded gaps are:

- `clamp_plate_1` to `tapped_plate_1`: **seated — touching, and never more
  than 0.5 mm off** that top face.
- `cap_screw_1` to `clamp_plate_1`: **seated — touching, and never more than
  0.5 mm off** the clamp plate's top face. A screw left proud of its seat
  fails here.
- `cap_screw_1` to `tapped_plate_1`: **0.5 mm to 2.0 mm** — not bottomed out,
  and still down in the tapped hole. Note what that distance is: the closest
  approach between the two solids is **radial**, the screw's thread flank
  against the counterbore wall (4.5 mm counterbore radius against an M8 root
  radius of about 3.32 mm, so about 1.18 mm as shipped) — it is not the
  tip-to-hole-bottom depth, so do not model it as one. Seating depth is graded
  by the `cap_screw_1`-to-`clamp_plate_1` row above.

Do not change any part script and do not change any part's parameters — this
task is the placement only.

Datum: world frame, no rotations. The joint axis is **Z**, the tapped plate's
top face is **Z = 0**, and the stack grows into **+Z**.
