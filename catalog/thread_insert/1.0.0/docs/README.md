# thread_insert — heat-set threaded inserts

Knurled brass heat-set inserts to the ruthex catalogue, as one parametric
part: `heat_set_insert`. These are the inserts you melt into a 3D-printed boss
so a plastic part can take a real machine screw.

**Five sizes ship — M2, M2.5, M3, M4 and M6 — and not every size in between.**
They are the ruthex rows the pinned bd_warehouse 0.3.0 actually populates:
`HeatSetNut.fastener_data` lists M5 designations, but every ruthex field on
them is empty, so **there is no M5 insert to build**. The M3 and M4 rows are
ruthex's **short** 4.0 mm inserts (part numbers `GE-M3Sx40-002` and
`GE-M4Sx04-1`), which is what the `-4.0` in their designations says; ruthex's
longer M3 is not in the pinned data either. The sixth populated row, a Voron
variant of the short M3 (⌀5.0), is left out on purpose — one M3 in the enum,
named the way ruthex names it.

| | |
|---|---|
| part id | `heat_set_insert` |
| parameters | `size` (enum: M2, M2.5, M3, M4, M6), `thread` (cosmetic \| real) |
| connectors | `seat` (rigid), `axis` (cylindrical) |
| specs | validity, outer diameter, height matches the designation, seats on z = 0 |

## Origin and orientation

The insert occupies **z = 0 up to z = h**. `seat` is the flange face that ends
flush with the printed surface, so mating it onto a boss's top face lands the
insert where a soldering iron would push it; `axis` is the bore centreline
pointing **into** the boss, which is both the press direction and the direction
the screw arrives from.

## Sizing the boss

The designation is `M<thread>-<pitch>-<height>` and the last field is the
insert's own height, which is what one of the specs checks. For the boss:

| size | outer ⌀ | height | suggested boss bore | minimum boss wall |
|---|---|---|---|---|
| M2-0.4-4 | 3.6 | 4.0 | 3.4–3.5 | 1.5 |
| M2.5-0.45-5.7 | 4.6 | 5.7 | 4.4–4.5 | 1.5 |
| M3-0.5-4.0 | 4.6 | 4.0 | 4.4–4.5 | 1.5 |
| M4-0.7-4.0 | 6.3 | 4.0 | 6.1–6.2 | 2.0 |
| M6-1-6.8 | 8.7 | 6.8 | 8.5–8.6 | 2.5 |

All in mm, and all five rows are the ones bd_warehouse's ruthex data
populates. Bore about 0.1–0.2 mm under the knurl so the brass has material to
melt into; give the boss at least the wall in the last column or it splits.

## Cosmetic vs real threads

`cosmetic` (the default) leaves the bore a plain cylinder — right for
assembly views, fit checks and interference, and it is what keeps a
fifty-insert enclosure light. `real` builds true ISO helical geometry inside
the bore at roughly 9 000 triangles per thread. The outside of the insert is
identical either way, so switching does not move a mate.

## Licence and trust

Apache-2.0; bd_warehouse, which supplies the insert geometry and the ruthex
dimensions, is Apache-2.0. "ruthex" names the catalogue these dimensions come
from; no affiliation is claimed and no vendor geometry is redistributed.

The publish gate is a **correctness** gate, not a security boundary: it proves
that the geometry builds, that the specs pass and that the connectors mate.
Package scripts run in your kernel worker with your privileges. See
`docs/packages.md`.
