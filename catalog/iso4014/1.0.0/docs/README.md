# iso4014 — hex-head bolts with the ISO 4014 head

Hex-head bolts carrying the ISO 4014 head, M4 through M12, as one parametric
part: `hex_bolt`. Built on `agentcad.toolkit.threads.hex_bolt`, which wraps
bd_warehouse's `HexHeadScrew` at `fastener_type="iso4014"`.

> **The shank is threaded end to end, and a real ISO 4014 bolt is not.**
> Read "What this is not" below before you use one where the plain shank
> matters.

| | |
|---|---|
| part id | `hex_bolt` |
| parameters | `size` (enum, M4–M12), `length` (10–100 mm), `thread` (cosmetic \| real) |
| connectors | `head_seat` (rigid), `axis` (cylindrical) |
| specs | validity, width across flats, head height, length under the head, full-length root-diameter shank |

## Origin and orientation

The **under-head bearing face is at local z = 0**; the head rises to +z (to
`k`) and the shank runs down to `z = -length`. The hexagon is oriented
**flats along Y, corners along X**, so the width across flats `s` is the Y
extent and the width across corners `e = 2s/√3` is the X extent.

Mate `head_seat` onto the face the bolt clamps; `axis` is the same centreline
as a cylindrical connector for when the bolt should keep its spin and depth.

## What this is not: the thread is full length

ISO 4014 is the *partially* threaded bolt — an M8 × 30 has b = 22 mm of thread
and an 8.000 mm plain shank at the nominal diameter. **This part does not build
that, and the pinned bd_warehouse cannot.** Measured on the shipped `m8x30`
preset: one cylindrical face, ⌀6.647, running the whole 30 mm, and
`thread_length == 30.0`.

The cause is in the library, not in this package: bd_warehouse 0.3.0's
`Screw.__init__` sets `thread_length = length - length_offset` with no way to
override it, no screw class accepts a thread length, and the `iso4014` rows of
`HexHeadScrew.fastener_data` hold only `k`, `s` and the length limits — there
is no `b` column to build a partial thread from.

So what ships is the **ISO 4014 head** (`s` and `k` from the table below, both
measured by specs) on a fully threaded shank: geometrically an ISO 4017-shaped
screw with ISO 4014 head heights, which are not the same (`k` for M8 is 5.30 in
ISO 4014 and 5.54 in ISO 4017). Good for an envelope, a clearance model, a
mate and a bill of materials line. **Wrong for a bolt in shear, a reamed fit or
anything that bears on the unthreaded shank.**

The `shank_full_length_root` spec pins exactly that: one cylindrical face at
the basic minor diameter `d1 = d − 1.0825 P`, spanning the full length under
the head. A synthetic ISO 4014 bolt with a real 8 mm plain shank fails it, and
so does a full-length shank drawn at the nominal ⌀8 — so the check discriminates
in both directions rather than merely agreeing with the geometry it measures.
If a later bd_warehouse builds the partial thread, this check goes red and this
page is what has to change.

## Cosmetic vs real threads

Neither value adds a plain shank. `cosmetic` (the default) leaves the shank the
bare **root**-diameter cylinder — fast, light, and it drops into a tapped hole
(the tap drill is larger than the root) reporting no interference. `real` adds
true ISO helical geometry on top of it, reaching the nominal **major**
diameter, at roughly 9 000 triangles per thread; a real thread in a tapped hole
overlaps it, which is what thread engagement is. Outside the flanks the two are
dimensionally identical, so switching does not move a mate.

For the fully threaded ISO 4017 screw *as such* — with the ISO 4017 head
heights — call `threads.hex_bolt(..., standard="iso4017")` in your own part.

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

plus `length under the head == length` and the full-length root-diameter shank described above.

## Licence and trust

Apache-2.0; bd_warehouse, which supplies the fastener geometry, is Apache-2.0.

The publish gate is a **correctness** gate, not a security boundary: it proves
that the geometry builds, that the specs pass and that the connectors mate.
Package scripts run in your kernel worker with your privileges. See
`docs/packages.md`.
