# iso4762 — ISO 4762 socket-head cap screws

Socket-head cap screws to ISO 4762, M3 through M12, as one parametric part:
`cap_screw`. Built on `agentcad.toolkit.threads.cap_screw`, which wraps
bd_warehouse's `SocketHeadCapScrew`.

## The part

| | |
|---|---|
| part id | `cap_screw` |
| parameters | `size` (enum, M3–M12), `length` (5–60 mm), `thread` (cosmetic \| real) |
| connectors | `head_seat` (rigid), `axis` (cylindrical) |
| specs | validity, head diameter, head height, length under the head |

### Origin and orientation

The **under-head bearing face is at local z = 0**. The head rises to +z (to
`k`, the head height for the size) and the threaded shank runs down to
`z = -length`. Mate `head_seat` onto the face the screw clamps and the shank
goes into the material; the `axis` connector is the shank centreline pointing
the same way, for when you want the screw to keep its spin and depth.

```
use_part {"project": "rig", "package": "iso4762", "part": "cap_screw",
          "part_id": "screw_1", "preset": "m5x16"}
set_mate {"project": "rig", "instance": "screw_1_1", "connector": "head_seat",
          "to_instance": "plate_1", "to_connector": "bolt_seat"}
```

## Cosmetic vs real threads

`agentcad/toolkit/threads.py` documents the choice and this package exposes it
rather than deciding for you, as the `thread` parameter:

* **`cosmetic`** (the default) draws the shank as a plain cylinder at the
  nominal diameter. About 0.05 s to build and roughly 1 000 triangles. This is
  what you want for assembly views, fit checks, interference and anything where
  the screw is a placeholder for a fastener that exists.
* **`real`** builds true ISO helical thread geometry — roughly **9 000
  triangles per thread**, and the cost grows with the number of turns: a
  measured M8 × 16 takes ~0.13 s where an M3 × 60 takes ~1.9 s. Use it for a
  manufacturing drawing, a render where the thread is visible, or when the
  thread itself has to mate against a tapped hole's thread solid.

The two are dimensionally identical everywhere outside the thread flanks — same
head, same length, same bearing face — so switching one for the other does not
move a mate or change a connector. The bounding boxes are equal to within
1e-6 mm; only the volume differs.

### What it changes, and it is not cosmetic

The shank diameters are **not** the same, and this is the one thing to know
before you run an interference check (all figures measured on M5-0.8):

| | shank diameter |
|---|---|
| `cosmetic` | **⌀4.134** — the thread's *root* (minor) diameter |
| `real` | **⌀5.000** — the nominal *major* diameter, at the flank crests |

So a cosmetic screw **drops cleanly into a PRD-010 tapped hole**, whose tap
drill for M5 is ⌀4.2: `check_interference` reports nothing. The same screw with
`thread: "real"` overlaps that hole — which is exactly what thread engagement
is, and not a modelling error. Use `cosmetic` when you want an assembly to come
back interference-clean.

To tap the hole the screw goes into: `holes.tapped(part, points, "M5",
depth=…)` from PRD-010's toolkit, or `agentcad.toolkit.threads` directly (bore
at `internal_thread(...).min_radius` and fuse `tapped_hole_thread(...)`).
Note the two size vocabularies — PRD-010's hole standards name the thread
`"M5"`, bd_warehouse and this package's `size` enum name it `"M5-0.8"`. They
are the same thread.

## Lengths

`length` is continuous over 5–60 mm rather than restricted to the catalogue
increments, because a bounded numeric range is what lets the publish gate build
this part at both of its extremes. The catalogue combinations ship as
**presets** — `m3x10`, `m4x12`, `m5x16`, `m6x20`, `m8x25`, `m10x30`, `m12x40`,
plus `m5x16_real` — and those are the ones the gate builds as declared
configurations.

## What the specs assert

The checks measure the built solid and compare it with the published ISO 4762
table (dimensions in mm, the standard's max column):

| size | `dk` head diameter | `k` head height |
|---|---|---|
| M3-0.5 | 5.68 | 3.0 |
| M4-0.7 | 7.22 | 4.0 |
| M5-0.8 | 8.72 | 5.0 |
| M6-1 | 10.22 | 6.0 |
| M8-1.25 | 13.27 | 8.0 |
| M10-1.5 | 16.27 | 10.0 |
| M12-1.75 | 18.27 | 12.0 |

within 0.05 mm, plus `length under the head == length`. A screw whose size the
check cannot establish from the geometry fails rather than passing quietly.

## Licence and trust

Apache-2.0. bd_warehouse, which supplies the fastener geometry, is Apache-2.0
as well.

The publish gate is a **correctness** gate, not a security boundary: it proves
that the geometry builds, that the specs pass and that the connectors mate.
Package scripts run in your kernel worker with your privileges. See
`docs/packages.md`.
