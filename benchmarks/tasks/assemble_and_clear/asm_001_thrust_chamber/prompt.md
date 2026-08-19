The project holds the three parts of a liquid-rocket thrust chamber —
`nozzle` (the chamber and bell), `flange` (the chamber-head interface ring) and
`injector_plate` — and **no assembly at all**: the instance list is empty.

Build the stack. Place exactly **three** instances and give them these ids,
because the design specs name them:

| instance id | part |
|---|---|
| `nozzle_1` | `nozzle` |
| `flange_1` | `flange` |
| `injector_plate_1` | `injector_plate` |

Requirements:

- `nozzle_1` sits at the **origin**, unrotated. It is the datum for the other
  two: its chamber head face is the plane **Z = 0** and the bell hangs down
  into **-Z**.
- `flange_1` slips over the chamber barrel from below, on the same axis and
  unrotated. Its **top face must sit 0.2 mm below** the chamber head face —
  a deliberate gasket allowance, not a fit. (Look at the part: its local
  origin is its underside.)
- `injector_plate_1` caps the stack on the same axis, unrotated, its
  **underside 0.2 mm above** the chamber head face — the head gasket.
- **No two instances may touch.** This is a bolted, gasketed joint: every
  stacked face keeps a 0.15-0.5 mm allowance, and an overlap of any volume is
  a failure.

Do not change any part script and do not change any part's parameters — this
task is the placement only.

Datum: world frame, no rotations anywhere. The chamber axis is **Z**, the
chamber head face is **Z = 0**, and the nozzle bell extends into **-Z**.
