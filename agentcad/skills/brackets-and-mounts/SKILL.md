---
name: brackets-and-mounts
description: L/U/Z brackets, gussets, bolt patterns, adjustment slots and NEMA motor mounts — sizing, clearances, load path, and the build123d order that survives a rebuild.
triggers: [bracket, mount, gusset, nema, motor, flange, l-bracket, stand, foot, bolt-pattern, slot, standoff, angle]
version: 1.0.0
license: Apache-2.0
author: AgentCAD core
requires: []
---

A bracket is a load path with holes in it, and almost every bracket that
disappoints is either too thin at the corner or has its bolts too close to an
edge. This skill covers choosing between the L, U, Z and gusseted-plate forms,
sizing gussets, laying out bolt patterns and adjustment slots, and mounting
NEMA stepper motors (bolt square, pilot register, shaft clearance). Use it when
the part's job is to hold something else in place. It is *not* the process
manual: `fdm-design-rules` for printed parts, `sheet-metal` for bends and flat
patterns, `holes` for the wizard's full API, `fits-and-clearances` for a
shaft-in-bore fit, `fem-workflow` when a rule of thumb is not enough.

## Choose the form

| Form | Use when | Watch |
|---|---|---|
| **L** (90° corner) | one member meets a perpendicular one | weakest in the open direction — gusset it |
| **U** (base + two uprights) | a shaft, bearing or motor supported at both ends | the two uprights must be built from one profile or they will not stay parallel |
| **Z** (offset) | joining two parallel faces at different heights | *both* legs bend; keep the offset leg as short as the clash allows |
| **Flat plate + gusset** | a face mount whose load is mostly in-plane | stiffest per gram; needs a mating flat surface |
| **Cut angle stock** | one-off, metal, cheap | thickness and root radius are fixed by the stock |

## Load path: deflection goes as L³/t³

For a leg of width `b`, thickness `t` and free length `L` under a tip load `F`,
`δ = 4FL³/(E·b·t³)` (Roark Table 8.1, cantilever with `I = b·t³/12`).

- Doubling `t` → **1/8** the deflection. Cutting `L` by 20 % → 0.51×. Doubling
  `b` → only 1/2. Thickness and length dominate; width barely helps.
- The L's **open side is the compliant direction**. A gusset takes the bending
  term out of the path and replaces it with an axial one — in practice an order
  of magnitude, not a percent.
- Failure and fatigue start at the **inner corner**. Keep `r/t ≥ 0.5`, which
  puts the stress concentration near Kt ≈ 1.5; `r/t = 0.1` is near 2.5
  (Peterson's).
- Bolts do not add stiffness if the joint slips. Two properly torqued M5 beat
  four hand-tight M3.

## Gussets

- **Triangle legs = 0.6–0.8 × the free length of the leg they brace.** Below
  ~0.5 the gusset barely acts; above ~0.85 it only adds mass and blocks access
  to the fasteners.
- **Thickness = the wall/leg thickness.** Thicker buys no stiffness and costs a
  sink mark in moulding, a thermal mass in FDM, and a heavy cut in machining.
- **Two gussets on the outer faces**, or one on the centreline. The outer pair
  also resists twist, so prefer it whenever the load can be off-axis; the
  single central web is for narrow brackets whose bolts sit near the edges.
- A bolted gusset carries load over its **Whitmore section**: spread 30° each
  side from the outermost fastener of the first row to the last row, and check
  yield on *that* width, not on the whole plate (AISC Design Guide 29).
- End the gusset toe 1–2 mm short of the leg's free end, and keep 1.5 × head
  diameter clear of every bolt head and washer face.

## Bolt patterns

- **Edge distance ≥ 1.5 d** (hole centre to edge) in metal — AISC Table J3.4
  runs ≈1.25–1.5 d and EN 1993-1-8 sets `e₁ ≥ 1.2 d₀` as the floor, so 1.5 d
  clears both. Use **≥ 2 d in plastic and FDM**, with a washer under the head.
- **Pitch ≥ 3 d** centre-to-centre (AISC minimum 2⅔ d; EN `p₁ ≥ 2.2 d₀`), and
  ≤ 14 t so the plies stay closed between fasteners.
- Symmetric patterns only: two or four bolts on a rectangle, or a bolt circle.
  A three-bolt triangle is fine on a round flange and a mistake on a plate — it
  defines a rotation you did not mean to define.
- Put the bolts where the moment is: on an L, the base pair furthest from the
  corner takes the prying load, so push it as far out as edge distance allows.
- Generate positions with `patterns.grid` / `patterns.bolt_circle` — pure
  arithmetic, rounded to 9 dp — not `GridLocations`/`PolarLocations`, which
  also *rotate* each copy (see `patterns`).

```python
from agentcad.toolkit import holes, patterns

def build(p):
    ...
    pts = patterns.grid(2, 2, p.pitch, p.gauge)
    part, recs, warn = holes.clearance(part, pts, "M5", fit="medium",
                                       plane="top")
    return part
```

## Clearance holes and slots

- **Never type a clearance diameter.** `holes.clearance(...)` reads ISO 273
  (`fine`/`medium`/`coarse`) or, with `std="ansi"`, ASME B18.2.8
  (`close`/`normal`/`loose`): M3 → 3.2 / 3.4 / 3.6, M5 → 5.3 / 5.5 / 5.8,
  M6 → 6.4 / 6.6 / 7.0 mm. `medium` (normal) is the default; `fine` when the
  pattern must locate the part; `coarse` for weldments, printed parts and
  stacked tolerances.
- **Slot width = the clearance hole diameter; overall length = width +
  required travel.** Keep the length within **2–3 d**, with 2.5 d the code
  ceiling for a long-slotted hole (AISC Table J3.3). Beyond that, use two
  positions or a real adjuster.
- **One round hole + one slot** per pair gives a datum plus tolerance take-up;
  two slots give genuine adjustment and no datum. Decide which you meant.
- Every slot needs a **washer spanning it** (AISC J3.8) — and on a printed part
  the washer is also what stops the head sinking into the slot.
- The hole wizard has no slot family, so a slot carries **no hole record** and
  will not appear in drawing callouts. Cut it as sketch geometry:

```python
with BuildSketch(Plane.XY):
    with Locations((x_slot, gauge / 2), (x_slot, -gauge / 2)):
        SlotOverall(slot_w + travel, slot_w)
extrude(amount=t + 1.0, both=True, mode=Mode.SUBTRACT)
```

## NEMA motor mounts

Full table with inch originals, flange sizes, boss heights and tap depths:
[`tables/nema.json`](tables/nema.json). The frame number is the flange width in
tenths of an inch.

| Frame | Flange | Bolt square | Bolt circle | Pilot ⌀ | Screw | Shaft ⌀ |
|---|---|---|---|---|---|---|
| NEMA 8 | 20.3 | 16.0 | 22.63 | 15.0 | M2 | 4.0 |
| NEMA 11 | 28.2 | 23.0 | 32.53 | 22.0 | M2.5 | 5.0 |
| NEMA 14 | 35.2 | 26.0 | 36.77 | 22.0 | M3 | 5.0 |
| NEMA 17 | 42.3 | **31.0** | 43.84 | **22.0** | M3 | 5.0 |
| NEMA 23 | 56.4 | 47.14 | 66.67 | 38.1 | M5 (#10-24) | 6.35 |

All mm, from NEMA ICS 16 Part 4 (converted from inches).

- The pattern is a **square**, not a circle: `bolt_circle = square × √2`. Use
  the square with `patterns.grid(2, 2, s, s)` unless you are deliberately
  patterning polar.
- **Pilot register: bore = pilot ⌀ + 0.2–0.5 mm diametral** (0.2 machined or
  laser-cut, 0.3–0.5 FDM). The pilot locates the motor; the four screws only
  clamp. The boss is only ~1.5–2 mm tall, so a plate thicker than the boss
  still registers on the first 2 mm — that is enough, and a plain through bore
  is fine.
- **Shaft and coupler clearance:** the pilot bore already clears the shaft
  (⌀5 for NEMA 8–17, ⌀6.35 for 23) and a plain jaw coupler. When a pulley or a
  larger hub must pass through, size the bore to hub ⌀ + 1 mm — but keep at
  least 1.5 × wall thickness of material between the bore edge and the nearest
  bolt hole edge, or the four-bolt pattern turns into a ring of ligaments.
- **Screw length = plate thickness + 4–5 mm.** Motor face tappings are shallow
  (≈4.5 mm on a NEMA 17); a screw that bottoms out feels tight and clamps
  nothing.
- Keep the motor face **metal-to-metal against the bracket** — that joint is
  the motor's heat path. Do not slip a printed spacer in between.

## Process differences

- **FDM** — the corner is printed, not bent, so orientation *is* the design.
  Lay the L on its **side face**: the profile becomes the first layer, there
  are no overhangs at all, and the corner's stress runs along the extrusions
  instead of across the layer bond. Make the wall and gusset an integer number
  of perimeters, and add ≈ +0.2 mm to hole diameters. See `fdm-design-rules`.
- **Sheet metal** — the corner is a bend: inside radius ≥ t, bend relief at
  both ends, and every hole ≥ 1.5 t + r from the bend line or it pulls oval. A
  gusset has to be a separate welded piece or a formed stiffening bead. See
  `sheet-metal`.
- **Machined / plate** — the inner corner carries the cutter radius (≥ 3 mm is
  typical), gussets are free geometry but expensive material removal, and the
  cheapest stiff bracket is often two flat plates bolted together rather than
  one solid.

## Build order that survives a rebuild

Sketch the L profile on `Plane.XZ` and `extrude(amount=w/2, both=True)` so the
part is symmetric about Y. Then, in this order:

1. fillet the inner corner — **before** the gussets, because after them the
   corner edge is split into segments and the radius no longer fits;
2. fuse the gussets;
3. cut the slots;
4. **drill last**, so hole circles never contaminate a fillet's edge selection.

Failure modes and their recovery:

- A gusset placed on a plane whose normal you guessed wrong **fuses in mid-air**
  and OCCT reports success: `is_valid` True, volume up by exactly the right
  amount. Assert `n_solids == 1`, or build it with `features.rib(..., to=<mm>)`
  and read the guard's warning (`ribs-bosses-draft`).
- `features.rib(to="part")` intersects the part's **bounding solid**, not the
  part: on a convex bracket it adds 0 mm³ and always warns.
- Pass `holes` a real `Plane` or a `"top"`/`"left"` predicate, **never a face
  index**. The normal points *out* of the material, the drill runs along
  `-z_dir`, and `points` are `(u, v)` in that plane.
- Select the corner edge by coordinate, not by ordinal:

```python
inner = [e for e in part.edges().filter_by(Axis.Y)
         if abs(e.center().X - t) < 1e-4 and abs(e.center().Z - t) < 1e-4]
```

- Use `safe_fillet` for any radius near the leg thickness — it binary-searches
  down and returns the part unchanged instead of failing the build. More in
  `selectors-and-occt-failures` and `robust-parametrics`.

## Checklist

- [ ] Edge distance ≥ 1.5 d (2 d printed), pitch ≥ 3 d, slot length ≤ 3 d,
      washer over every slot.
- [ ] Gusset legs 0.6–0.8 × the free leg, thickness = wall, clear of every head.
- [ ] Inner corner `r/t ≥ 0.5`.
- [ ] Clearance diameters from `holes`, never typed in.
- [ ] Motor: pilot + 0.2–0.5 mm, coupler clears the bore, screw = t + 4–5 mm,
      face contact metal-to-metal.
- [ ] Rebuilt at both parameter extremes: `n_solids == 1`, `is_valid`, no
      dropped hole instances in the warnings.

## Snippet

`snippets/nema17_bracket.py` — an L-bracket carrying the NEMA 17 pattern
(31.0 mm square, ⌀22.3 pilot) drilled from the motor face, two adjustment slots
in the base, and a gusset on each outer face, all driven by one `thk`. Defaults
build one valid solid of 33 696 mm³ with no warnings; `bolt_sq`, `pilot_d` and
`screw` retarget it to any row of `tables/nema.json`.

## Sources

- NEMA ICS 16, *Motion/Position Control Motors, Controls, and Feedback
  Devices*, Part 4 — motor frame and face-mounting dimensions.
- Oriental Motor, *PKP Series Stepping Motors* catalogue — dimensional drawings
  for the 20/28/35/42/56 mm frames (vendor corroboration of the table).
- ISO 273:1979, *Fasteners — Clearance holes for bolts and screws*.
- ASME B18.2.8, *Clearance Holes for Bolts, Screws, and Studs*.
- AISC 360, *Specification for Structural Steel Buildings* — §J3.3 (spacing),
  §J3.4 and Table J3.4 (edge distance), Table J3.3 (hole and slot dimensions),
  §J3.8 (washers over slotted holes).
- EN 1993-1-8, *Design of joints*, Table 3.3 — `e₁`, `e₂`, `p₁`, `p₂` limits.
- AISC Design Guide 29, *Vertical Bracing Connections* — gusset plates and the
  Whitmore section.
- W. D. Pilkey & D. F. Pilkey, *Peterson's Stress Concentration Factors*, 3rd
  ed. — fillet radius versus Kt at a stepped section in bending.
- W. C. Young & R. G. Budynas, *Roark's Formulas for Stress and Strain*, 8th
  ed., Table 8.1 — cantilever deflection.
- R. G. Budynas & J. K. Nisbett, *Shigley's Mechanical Engineering Design* —
  bolted joints, preload and thread engagement.
