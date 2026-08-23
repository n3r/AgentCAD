<!-- Reviewer note (stripped from the prompt the agent sees) — how the graded
     clearance windows were derived. Each bound comes from the gap MEASURED on
     the reference placement, with the floor kept as shipped and the ceiling
     set at roughly twice the measurement (or at INT-003's own stated 0.5 mm
     allowance where the prompt already names one):

       row                    pair                       measured   window
       flange_bore_gap        flange_1 - nozzle_1        0.500 mm   0.3-1.0
       injector_gasket_gap    injector_plate_1 - nozzle_1 0.200 mm  0.15-0.5
       head_face_stack_gap    flange_1 - injector_plate_1 0.400 mm  0.25-1.0

     `flange_bore_gap` is the RADIAL bore gap and is blind to the flange's
     axial position (dropped 3 mm it still measures 0.500 mm), which is why
     `head_face_stack_gap` exists: it is the two gasket allowances back to
     back across the head face plane and reads 3.400 mm for that same flange.
     All three pairs are windowed, so any one instance parked off the stack
     fails at least two rows. -->

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
  stacked face keeps a 0.2-0.5 mm allowance, and an overlap of any volume is
  a failure.

Every stated gap is graded as a **two-sided window** — a floor *and* a
ceiling, because a part parked clear of the stack is not assembled:

- `flange_1` to `nozzle_1`: **0.3 mm to 1.0 mm**. That is the radial gap of
  the flange bore over the chamber barrel — the flange has to be around the
  barrel, not beside it.
- `injector_plate_1` to `nozzle_1`: **0.15 mm to 0.5 mm** — the head gasket
  allowance, top of the stack.
- `flange_1` to `injector_plate_1`: **0.25 mm to 1.0 mm** — the two gasket
  allowances back to back across the head face, which is what fixes how far
  down the barrel the flange sits.

Do not change any part script and do not change any part's parameters — this
task is the placement only.

Datum: world frame, no rotations anywhere. The chamber axis is **Z**, the
chamber head face is **Z = 0**, and the nozzle bell extends into **-Z**.
