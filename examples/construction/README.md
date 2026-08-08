# Construction — Steel Truss Gusset Node

A bolted structural-steel connection family, all in ASTM A36 steel
(`steel_a36`): the gusset plate of a truss panel point, the base plate of
the supporting column, and the erection angle brackets that hold the
gusset plumb while the bolts go in. The assembly stages the three parts
as a shop mock-up: the gusset plate stands vertically (rotated 90° about
X) above the column base plate, flanked by one angle bracket on each
face. Every mating gap is at least 0.5 mm, so the interference check is
clean by construction.

## Parts

### `gusset_plate` — truss gusset plate

The plate joins a horizontal bottom chord and two diagonal web members.
Its outline is computed as the convex hull of the three member
footprints — a chord band along X and two diagonal strips rising at
`diag_angle_deg` from the horizontal — then extruded to `plate_t`. Each
member gets a bolt group of 2 columns × `n_rows`, drilled `hole_d`,
spaced `pitch` along the member axis, with `edge_dist` from the outer
hole centers to the plate edge.

| Param | Default | Range | Meaning |
|---|---|---|---|
| `plate_t` | 10 | 6–25 mm | plate thickness |
| `diag_angle_deg` | 45 | 30–60° | diagonal angle from the chord |
| `chord_w` | 80 | 50–120 mm | bottom chord width (lap band height) |
| `diag_w` | 60 | 40–100 mm | diagonal member width |
| `hole_d` | 18 | 12–24 mm | bolt hole (18 = M16 + clearance) |
| `pitch` | 50 | 35–90 mm | bolt row spacing along each axis |
| `n_rows` | 3 | 2–5 | bolt rows per member group |
| `edge_dist` | 30 | 20–50 mm | end distance to the plate edge |

The project overrides `n_rows` to 2 and `pitch` to 45 so the default
node fits neatly over the base plate in the staged assembly.

### `base_plate` — column base plate

A `plate_w` × `plate_l` × `plate_t` plate (corner radius 10) with four
rounded anchor-bolt slots (`slot_w` × `slot_l`, `SlotOverall`) set
`anchor_offset` in from the corners — slotted along X so the column can
be plumbed during erection — and a shallow 1 mm recess marking the
`hss_w` square column footprint as a setting-out aid.

| Param | Default | Range | Meaning |
|---|---|---|---|
| `plate_w`, `plate_l` | 300 | 150–450 mm | plate plan dimensions |
| `plate_t` | 20 | 10–40 mm | plate thickness |
| `hss_w` | 150 | 80–250 mm | HSS column width (recess) |
| `slot_w` | 22 | 14–30 mm | anchor slot width |
| `slot_l` | 45 | 25–60 mm | anchor slot overall length |
| `anchor_offset` | 50 | 30–60 mm | slot center inset from each edge |

### `angle_bracket` — erection angle bracket

An L-bracket: `leg_b` along X, `leg_a` up Z, `width` deep, `thk` thick,
with a structural fillet (`fillet_r`) at the inner corner and two
`hole_d` bolt holes per leg, placed across the width and centered
between the fillet runout and the free end of the leg.

| Param | Default | Range | Meaning |
|---|---|---|---|
| `leg_a`, `leg_b` | 90 | 50–150 mm | leg lengths |
| `width` | 80 | 40–150 mm | bracket width |
| `thk` | 10 | 6–20 mm | leg thickness |
| `hole_d` | 14 | 8–20 mm | bolt hole diameter |
| `fillet_r` | 6 | 2–12 mm | inner corner fillet radius |

## Internal clamping (why extremes never fail)

Every script re-clamps its derived hole layout inside `build()`: bolt
gauges never drop below `hole_d + 4`, end/edge distances never drop
below `hole_d/2 + 4`, pitches never drop below `hole_d + 6`, the base
plate slots keep ≥ 3 mm margins and the footprint recess shrinks (or is
skipped) to stay clear of them. Setting any parameter to its min or max
therefore still produces a valid, manifold solid — the geometry adapts
instead of erroring, which is exactly what an agent iterating on the
model needs.

## Iterating with an agent

This project is meant to be driven through the AgentCAD tool surface.
Typical refinement loop: ask for more bolt shear capacity and the agent
raises `n_rows` (each extra row adds two bolts per member — the plate
grows along each member axis automatically); ask for a shallower truss
and it lowers `diag_angle_deg`, with the hull outline and hole groups
re-solving to the new member axes; switch from M16 to M20 bolts by
setting `hole_d` to 22 and `pitch` to 60, then read the returned metrics
to confirm the plate mass. On the base plate, a larger column is just
`hss_w`, and more anchor adjustment is `slot_l` — after each change the
`check_interference` tool proves the staged assembly still fits.
