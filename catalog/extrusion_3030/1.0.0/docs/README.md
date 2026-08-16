# extrusion_3030 — 30 x 30 T-slot aluminium extrusion

Metric T-slot framing extrusion, 30 x 30 mm with 8 mm slots, as one
parametric part: `extrusion`. The only thing that varies between two bars of
this profile is where they were cut, which is why `length` is the only
parameter.

| | |
|---|---|
| part id | `extrusion` |
| parameters | `length` (10–1000 mm) |
| connectors | `end_a`, `end_b`, `slot_x_pos`, `slot_x_neg`, `slot_y_pos`, `slot_y_neg` (all rigid) |
| specs | validity, 30 x 30 section, length and origin, 6.8 mm centre bore, one connected solid, section area |

## What this is, and what it is not

An **interface model**. The 30 x 30 envelope, the four 8 mm slot
openings, the T-channels behind them and the 6.8 mm centre bore (the
tapping size for a M8 end fastener) are the dimensions a bracket, a T-nut or
a machined end has to respect. The web fillets, the corner chamfers and the
knurled slot faces of a real profile are **not** modelled: they change no
interface and they would multiply the triangle count of a part that is
routinely a metre long.

T-slot framing is a **vendor convention rather than a standard**; these are
the commodity metric "30 series / slot 8" dimensions.

## The section, and why it is one solid

The profile is the outer skin, a central bore boss, four diagonal webs joining
the boss to the four corners, and the four T-channels that are the voids left
between them:

| | |
|---|---|
| slot mouth | 8 mm wide, 4.25 mm of retaining ledge each side behind the lip |
| lip thickness | 2.0 mm |
| channel width behind the lip | 16.5 mm |
| channel bottom | the boss's own surface, 8.5 mm in from the outer face |
| centre boss | ⌀13.0, bored 6.8 mm |
| diagonal webs | 2.5 mm thick, constant from the boss to the ledge |
| section area | **404.99 mm² (1.094 kg/m in 6063)** |

The envelope, the bore, the length and the section area each have a spec of
their own; the rows between them are the constants the profile is built from,
and the last row is what they add up to. **Being one connected solid** has a
spec too — because a profile whose webs have been cut through still measures
30 x 30, still has its 6.8 mm bore and still has four 8 mm openings. This part
used to build **five loose pieces** (382.93 mm² of section) and the gate was
green: the envelope, the bore and the openings were all still right.

The area window the spec enforces is **350–465 mm²**. There is no dimensional
standard to check a T-slot profile against, so the reference is mass:
commodity 30-series bar is published at roughly 0.95–1.25 kg/m, which at
6063 aluminium's 2.70 g/cm³ is 352–463 mm².

**That window did not catch this break, and it is written here rather than
tuned away**: the five-piece profile measured 382.93 mm², which is inside the
band. `one_connected_solid` is the check that saw it, and the area window is
kept honest to the reference rather than narrowed until it would have.

## Origin and orientation

The section is centred on the Z axis and the bar runs from **z = 0 to
z = length**. `end_a` and `end_b` are the two cut faces. The four slot
connectors sit on each face's slot centreline at mid-length, lying **in** the
outer face, oriented so that +Z points out of the bar — mate a bracket's rigid
connector onto one and it lands flat on the slot.

They are all rigid, including the slot ones. A T-nut does slide, but *where*
it slides to is the assembly's decision rather than the extrusion's, and the
moving side of a mate has to be rigid in any case. Offset a bracket along a
slot by giving its instance a position, or by mating to `end_a` and placing
it explicitly.

## Licence and trust

Apache-2.0.

The publish gate is a **correctness** gate, not a security boundary: it proves
that the geometry builds, that the specs pass and that the connectors mate.
Package scripts run in your kernel worker with your privileges. See
`docs/packages.md`.
