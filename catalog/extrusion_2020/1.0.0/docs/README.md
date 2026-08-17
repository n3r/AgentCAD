# extrusion_2020 — 20 x 20 T-slot aluminium extrusion

Metric T-slot framing extrusion, 20 x 20 mm with 6 mm slots, as one
parametric part: `extrusion`. The only thing that varies between two bars of
this profile is where they were cut, which is why `length` is the only
parameter.

| | |
|---|---|
| part id | `extrusion` |
| parameters | `length` (10–1000 mm) |
| connectors | `end_a`, `end_b`, `slot_x_pos`, `slot_x_neg`, `slot_y_pos`, `slot_y_neg` (all rigid) |
| specs | validity, 20 x 20 section, length and origin, 4.2 mm centre bore, one connected solid, section area |

## What this is, and what it is not

An **interface model**. The 20 x 20 envelope, the four 6 mm slot
openings, the T-channels behind them and the 4.2 mm centre bore (the
tapping size for a M5 end fastener) are the dimensions a bracket, a T-nut or
a machined end has to respect. The web fillets, the corner chamfers and the
knurled slot faces of a real profile are **not** modelled: they change no
interface and they would multiply the triangle count of a part that is
routinely a metre long.

T-slot framing is a **vendor convention rather than a standard**; these are
the commodity metric "20 series / slot 6" dimensions.

## The section, and why it is one solid

The profile is the outer skin, a central bore boss, four diagonal webs joining
the boss to the four corners, and the four T-channels that are the voids left
between them:

| | |
|---|---|
| slot mouth | 6 mm wide, 2.75 mm of retaining ledge each side behind the lip |
| lip thickness | 2.0 mm |
| channel width behind the lip | 11.5 mm |
| channel bottom | the boss's own surface, 6.5 mm in from the outer face |
| centre boss | ⌀7.0, bored 4.2 mm |
| diagonal webs | 2.0 mm thick, constant from the boss to the ledge |
| section area | **182.32 mm² (0.492 kg/m in 6063)** |

The envelope, the bore, the length and the section area each have a spec of
their own; the rows between them are the constants the profile is built from,
and the last row is what they add up to. **Being one connected solid** has a
spec too — because a profile whose webs have been cut through still measures
20 x 20, still has its 4.2 mm bore and still has four 6 mm openings. This part
used to build **five loose pieces** (151.40 mm² of section) and the gate was
green: the envelope, the bore and the openings were all still right.

The area window the spec enforces is **165–205 mm²**. There is no dimensional
standard to check a T-slot profile against, so the reference is mass:
commodity 20-series bar is published at roughly 0.45–0.55 kg/m, which at
6063 aluminium's 2.70 g/cm³ is 167–204 mm².

The area window caught this break here — 151.40 mm² is under the floor — but
it is not what the connectivity claim rests on: the same break on the 30 x 30
profile measured 382.93 mm², which is *inside* that profile's band.
`one_connected_solid` is the direct statement, and on the 30-series it was the
only check that saw anything.

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
