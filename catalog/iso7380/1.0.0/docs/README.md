# iso7380 — ISO 7380-1 button-head socket screws

Button-head socket screws to ISO 7380-1, M3 through M12, as one parametric
part: `button_screw`. A low domed head with a hex socket — what you reach for
when an ISO 4762 cap screw's head is too tall or too sharp-edged.

| | |
|---|---|
| part id | `button_screw` |
| parameters | `size` (enum, M3–M12), `length` (5–50 mm), `thread` (cosmetic \| real) |
| connectors | `head_seat` (rigid), `axis` (cylindrical) |
| specs | validity, head diameter, head height, length under the head |

## Origin and orientation

The **under-head bearing face is at local z = 0**; the dome rises to +z (to
`k`) and the shank runs down to `z = -length` — the same convention as
`iso4762` and `iso4014`, so the three are interchangeable at a mate.

## Cosmetic vs real threads

`cosmetic` (the default) draws the shank at the thread's **root** diameter:
fast, light, and it drops into a tapped hole interference-free. `real` builds
true ISO helical geometry at the nominal **major** diameter and costs roughly
9 000 triangles per thread. Outside the thread flanks the two are
dimensionally identical, so switching does not move a mate.

## What the specs assert

Measured from the built solid against the published ISO 7380-1 table (mm,
nominal), within 0.05 mm:

| size | `dk` head diameter | `k` head height |
|---|---|---|
| M3-0.5 | 5.7 | 1.65 |
| M4-0.7 | 7.6 | 2.2 |
| M5-0.8 | 9.5 | 2.75 |
| M6-1 | 10.5 | 3.3 |
| M8-1.25 | 14.0 | 4.4 |
| M10-1.5 | 17.5 | 5.5 |
| M12-1.75 | 21.0 | 6.6 |

plus `length under the head == length`. A screw whose size the check cannot
establish from the geometry fails rather than passing quietly.

## Licence and trust

Apache-2.0; bd_warehouse, which supplies the fastener geometry, is Apache-2.0.

The publish gate is a **correctness** gate, not a security boundary: it proves
that the geometry builds, that the specs pass and that the connectors mate.
Package scripts run in your kernel worker with your privileges. See
`docs/packages.md`.
