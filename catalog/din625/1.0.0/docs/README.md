# din625 — deep-groove ball bearings

Deep-groove ball bearings to DIN 625-1, the 6xx miniature and 60xx light
series, as one parametric part: `ball_bearing`.

| | |
|---|---|
| part id | `ball_bearing` |
| parameters | `designation` (enum: 623, 624, 625, 626, 608, 628, 6000, 6001, 6002) |
| connectors | `bore` (cylindrical), `face` (rigid) |
| specs | validity, bore diameter, outside diameter, width, ring faces |

## What this is, and what it is not

It is an **interface model**. The bore `d`, the outside diameter `D` and the
width `B` are the standard's, and the ring split is drawn where a real
bearing's is — that is what a bracket, a housing and an interference check
need. There are **no balls, no cage and no seal lip**: modelling the rolling
elements would multiply the triangle count of a part that usually appears four
times in an assembly, and it would imply a fidelity this does not have. The
ring-split grooves are 0.3 mm deep and cosmetic; do not measure a seal groove
off them.

## Origin and orientation

Centred on the Z axis, occupying **z = 0 to z = B**. `face` is the z = 0 end
face — mate it onto the shoulder the bearing seats against — and `bore` is the
shaft axis pointing +z, cylindrical so a shaft keeps its spin and its position
along the bore.

## The table

Dimensions in mm, DIN 625-1:

| designation | bore `d` | outside `D` | width `B` |
|---|---|---|---|
| 623 | 3 | 10 | 4 |
| 624 | 4 | 13 | 5 |
| 625 | 5 | 16 | 5 |
| 626 | 6 | 19 | 6 |
| 608 | 8 | 22 | 7 |
| 628 | 8 | 24 | 8 |
| 6000 | 10 | 26 | 8 |
| 6001 | 12 | 28 | 8 |
| 6002 | 15 | 32 | 9 |

The specs measure the built solid against the row the `designation`
**parameter** names. `build` records that parameter on the solid it returns —
the way a bd_warehouse fastener carries its own `screw_size` — and the checks
read it back, so a build that ignores its parameter is compared with the
bearing it was *asked* for.

That is a correction. The first version of these checks selected the row by
matching the built `D` and `B` against the table, which made the `D` and `B`
checks compare the geometry with the row it had just been used to pick, and
made the bore check compare the table with itself. Sabotaging the build to
produce a 608 whatever the parameter said left **77 of 77 gate spec rows
green** (77 rows over four stages, 56 of them spec rows); the same sabotage now
reddens 43 of the 70 spec rows — every designation except 608 itself, plus the
628, whose bore is also 8 mm and whose bore check therefore still passes. `ring_faces` is a second reading of the same row off the
cylindrical **faces** — bore, the two ring-split walls at 30% and 70% of the
radial section, and the outside diameter — sharing no input with the three
bounding-box checks.

## Licence and trust

Apache-2.0.

The publish gate is a **correctness** gate, not a security boundary: it proves
that the geometry builds, that the specs pass and that the connectors mate.
Package scripts run in your kernel worker with your privileges. See
`docs/packages.md`.
