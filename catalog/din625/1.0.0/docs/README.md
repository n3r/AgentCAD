# din625 — deep-groove ball bearings

Deep-groove ball bearings to DIN 625-1, the 6xx miniature and 60xx light
series, as one parametric part: `ball_bearing`.

| | |
|---|---|
| part id | `ball_bearing` |
| parameters | `designation` (enum: 623, 624, 625, 626, 608, 628, 6000, 6001, 6002) |
| connectors | `bore` (cylindrical), `face` (rigid) |
| specs | validity, bore diameter, outside diameter, width |

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

The specs re-derive which row the built solid *is* from its own outside
diameter and width, then check the bore against that row — so a build that
ignored its parameter fails rather than agreeing with itself.

## Licence and trust

Apache-2.0.

The publish gate is a **correctness** gate, not a security boundary: it proves
that the geometry builds, that the specs pass and that the connectors mate.
Package scripts run in your kernel worker with your privileges. See
`docs/packages.md`.
