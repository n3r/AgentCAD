<!-- Reviewer note (stripped from the prompt the agent sees) — how the graded
     clearance windows were derived. Every bound comes from the gap MEASURED
     on the reference placement; floors are as shipped, ceilings are about
     twice the measurement (three times for the 0.05 mm crush height, so a
     candidate that rounds it to 0.1 mm still passes):

       row             pair                       measured    window
       pin_bore_gap    wrist_pin_1 - rod_1        0.125 mm    0.05-0.25
       big_end_joint   rod_cap_1 - rod_1          0.050 mm    0.02-0.15
       pin_boss_gap    wrist_pin_1 - piston_1     0.100 mm    0.05-0.2
       bolt_body_gap   rod_bolts_1 - rod_1        0.550 mm    0.3-1.1
       bolt_cap_gap    rod_bolts_1 - rod_cap_1    0.200 mm    0.1-0.4
       small_end_gap   piston_1 - rod_1           2.000 mm    1.0-3.0

     The last four rows are new; the prompt already stated the pin/boss and
     bolt relationships in words and nothing measured them. `small_end_gap` is
     a lateral approach (piston skirt to rod blade): it reads 2.000 mm for a
     piston lifted 1 mm, so it grades "the piston is over the small end", not
     its height — the height is `pin_boss_gap`'s (0.000 mm, red, for that same
     lifted piston).

     Left unbounded on purpose: cap-to-piston 88.050 mm, cap-to-pin
     102.898 mm, bolts-to-piston 78.085 mm, bolts-to-pin 93.176 mm. Those
     parts are at opposite ends of the rod and no fit relates them; every one
     of the five instances is already two-sidedly graded by a row that names
     it. -->

The project holds the five parts of one piston-and-connecting-rod set —
`piston`, `wrist_pin`, `rod_body` (the upper big-end half and the blade),
`rod_cap` (the lower big-end half) and `rod_bolt_pair` — and **no assembly at
all**: the instance list is empty.

Assemble the set. Place exactly **five** instances and give them these ids,
because the design specs name them:

| instance id | part |
|---|---|
| `rod_1` | `rod_body` |
| `rod_cap_1` | `rod_cap` |
| `rod_bolts_1` | `rod_bolt_pair` |
| `piston_1` | `piston` |
| `wrist_pin_1` | `wrist_pin` |

All five parts are drawn on the **same local frame convention**, which is why
this set assembles with **no rotations at all**: the big-end bore axis is
**Y through the local origin**, and the rod's joint face is the plane
**Z = 0**. Read each part's docstring and parameters — they say so.

Requirements:

- `rod_1` sits at the **origin**, unrotated. It is the datum.
- `rod_cap_1` closes the big-end bore from below, unrotated, **dropped 0.05 mm
  along the rod axis** (i.e. in -Z) from the body's joint face. That gap is the
  bearing shell's crush height and it is modelled, not assumed.
- `rod_bolts_1` takes the **rod's own pose** — same position, same rotation —
  because the bolt pair is drawn in the rod's frame.
- `piston_1` and `wrist_pin_1` both sit at the rod's **small-end centre**,
  unrotated. The small end is the rod's `length` parameter up the **+Z** axis
  from the big-end centre.
- The wrist pin is a **floating** pin: it must stay clear of the rod's small
  end by at least **0.05 mm** (the small-end bore is the pin plus 0.25) and
  clear of the piston bosses too.
- **No two of the five instances may overlap** by any volume.

Every fit above is graded as a **two-sided window** — a floor *and* a ceiling,
because a part parked clear of the set is not assembled:

- `wrist_pin_1` to `rod_1`: **0.05 mm to 0.25 mm** (the small-end bore).
- `wrist_pin_1` to `piston_1`: **0.05 mm to 0.2 mm** (the piston bosses).
- `rod_cap_1` to `rod_1`: **0.02 mm to 0.15 mm** (the 0.05 mm crush height).
- `rod_bolts_1` to `rod_1`: **0.3 mm to 1.1 mm**, and `rod_bolts_1` to
  `rod_cap_1`: **0.1 mm to 0.4 mm** — the bolts run through both halves
  without touching either.
- `piston_1` to `rod_1`: **1.0 mm to 3.0 mm** — the piston sits over the small
  end, its skirt clear of the rod blade.

Do not change any part script and do not change any part's parameters — this
task is the placement only.

Datum: world frame, **no rotations anywhere**. The rod's big-end bore axis is
**Y** through the origin, its joint face is **Z = 0**, and the small end,
piston and pin are up the **+Z** axis.
