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
- **No two instances may overlap** by any volume.

Do not change any part script and do not change any part's parameters — this
task is the placement only.

Datum: world frame, no rotations. The base's underside is **Z = 0**, it is
centred on the origin in X and Y, and the stack grows into **+Z**.
