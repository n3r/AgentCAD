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
| specs | validity, 20 x 20 section, length and origin, 4.2 mm centre bore |

## What this is, and what it is not

An **interface model**. The 20 x 20 envelope, the four 6 mm slot
openings, the T-channels behind them and the 4.2 mm centre bore (the
tapping size for a M5 end fastener) are the dimensions a bracket, a T-nut or
a machined end has to respect. The web fillets and the knurled slot faces of a
real profile are **not** modelled: they change no interface and they would
multiply the triangle count of a part that is routinely a metre long.

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
