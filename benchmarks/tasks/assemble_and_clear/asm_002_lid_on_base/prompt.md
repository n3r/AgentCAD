<!-- Reviewer note (stripped from the prompt the agent sees) — how the graded
     clearance window was derived:

       row       pair            measured   window
       seat_gap  lid_1 - base_1  0.100 mm   0.05-0.2 mm

     The floor is as shipped; the ceiling is twice the measurement. Two
     instances give one pair, so this row is the entire placement grade here.
     Measured against a lifted lid: 0.15 mm at Z = 30.5, 30.1+1 and 30.1+2
     (the closest approach saturates at the lip's own 0.15 mm radial
     clearance inside the cavity), 0.180 mm at Z = 33.1 and 2.105 mm at
     Z = 35.1. So the 0.2 mm ceiling reds a lid whose lip has left the cavity
     and every parked lid, and passes one floating within its lip engagement.
     A 0.14 mm ceiling would catch that too and was rejected: 0.04 mm between
     the reference and a red is a bound that grades noise, not placement. -->

The project holds the two mouldings of a snap-fit electronics enclosure —
`enclosure_base` (the open-top shell) and `enclosure_lid` — and **no assembly
at all**: the instance list is empty.

Assemble it. Place exactly **two** instances and give them these ids, because
the design specs name them:

| instance id | part |
|---|---|
| `base_1` | `enclosure_base` |
| `lid_1` | `enclosure_lid` |

Requirements:

- `base_1` sits at the **origin**, unrotated. Its underside is **Z = 0** and
  its rim is at **Z = 30**.
- `lid_1` sits **0.1 mm above that rim (Z = 30.1)**, on the same axis and
  unrotated — a snap fit, not an interference fit. The lid's local origin is
  the **underside of its top plate**, the face that faces the rim, and its lip
  hangs 3 mm below that, into the base's cavity.
- With the lid there, the two mouldings must stay **clear of each other by at
  least 0.05 mm**: the lip has to drop into the cavity, and the shipped
  design's 0.1 mm gives that allowance all round.
- That gap is graded as a **two-sided window — 0.05 mm to 0.2 mm** — because a
  lid parked clear of the base is not assembled: the lid has to be down on the
  rim with its lip inside the cavity, not merely somewhere in the project.
- **No two instances may overlap** by any volume.

Do not change any part script and do not change any part's parameters — this
task is the placement only.

Datum: world frame, no rotations. The base's underside is **Z = 0**, it is
centred on the origin in X and Y, and the stack grows into **+Z**.
