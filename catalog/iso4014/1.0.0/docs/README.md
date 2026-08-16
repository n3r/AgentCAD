# iso4014 — ISO 4014 hex-head bolts

Partially threaded hex-head bolts to ISO 4014, M4 through M12, as one
parametric part: `hex_bolt`. Built on `agentcad.toolkit.threads.hex_bolt`,
which wraps bd_warehouse's `HexHeadScrew` at `fastener_type="iso4014"`.

| | |
|---|---|
| part id | `hex_bolt` |
| parameters | `size` (enum, M4–M12), `length` (10–100 mm), `thread` (cosmetic \| real) |
| connectors | `head_seat` (rigid), `axis` (cylindrical) |
| specs | validity, width across flats, head height, length under the head |

## Origin and orientation

The **under-head bearing face is at local z = 0**; the head rises to +z (to
`k`) and the shank runs down to `z = -length`. The hexagon is oriented
**flats along Y, corners along X**, so the width across flats `s` is the Y
extent and the width across corners `e = 2s/√3` is the X extent.

Mate `head_seat` onto the face the bolt clamps; `axis` is the same centreline
as a cylindrical connector for when the bolt should keep its spin and depth.

## Cosmetic vs real threads

`cosmetic` (the default) draws the shank at the thread's **root** diameter —
fast, light, and it drops into a tapped hole (the tap drill is larger than the
root) reporting no interference. `real` builds true ISO helical geometry,
reaches the nominal **major** diameter, and costs roughly 9 000 triangles per
thread; a real thread in a tapped hole overlaps it, which is what thread
engagement is. Outside the flanks the two are dimensionally identical, so
switching does not move a mate.

ISO 4014 is the *partially* threaded bolt. For the fully threaded screw, call
`threads.hex_bolt(..., standard="iso4017")` in your own part.

## What the specs assert

Measured from the built solid against the published ISO 4014 table (mm,
nominal), within 0.05 mm:

| size | `s` across flats | `k` head height |
|---|---|---|
| M4-0.7 | 7.0 | 2.8 |
| M5-0.8 | 8.0 | 3.5 |
| M6-1 | 10.0 | 4.0 |
| M8-1.25 | 13.0 | 5.3 |
| M10-1.5 | 16.0 | 6.4 |
| M12-1.75 | 18.0 | 7.5 |

plus `length under the head == length`.

## Licence and trust

Apache-2.0; bd_warehouse, which supplies the fastener geometry, is Apache-2.0.

The publish gate is a **correctness** gate, not a security boundary: it proves
that the geometry builds, that the specs pass and that the connectors mate.
Package scripts run in your kernel worker with your privileges. See
`docs/packages.md`.
