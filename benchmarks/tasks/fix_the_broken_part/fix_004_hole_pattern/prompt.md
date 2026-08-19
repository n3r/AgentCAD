The project holds one part, `base_plate` — a 300 x 300 x 20 mm column base
plate. It builds, it is a perfectly good solid, and it is wrong: the erector
says the anchor bolts do not line up with anything. Looking at the part, the
four anchor slots that should sit near the four corners are not there. Two
slots appear instead, on the plate's centre line.

**Fixed** means: `base_plate` builds, comes back as **one valid solid**, and
carries its **four anchor slots, one near each corner**. Keep the part id
`base_plate` and keep every stored parameter exactly as it is — the plate, the
slots and the column footprint recess are all correctly dimensioned already.

The layout the drawing calls for, with the shipped parameters:

- the plate is **300 mm (X) x 300 mm (Y) x 20 mm**, corners broken R10;
- **four** anchor slots, one per corner, each **45 mm long (X) x 22 mm wide**,
  their centres **50 mm in from each edge** — i.e. at
  **(±100, ±100)**;
- a **1 mm deep, 150 mm square** column-footprint recess in the middle of the
  top face.

Material: A36 steel (unchanged).

Datum: the plate's underside lies on **Z = 0** and the plate is centred on the
origin in X and Y; the slots run long along **X**.
