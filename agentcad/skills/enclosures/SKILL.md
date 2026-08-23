---
name: enclosures
description: Two-part boxes, housings and cases - wall thickness per process, shelling, lid lips and grooves, screw bosses, PCB standoffs, vents, draft.
triggers: [enclosure, box, housing, case, lid, cover, lip, tongue, groove, boss, standoff, vent, slot, shell, cavity, pcb, wall thickness, parting line]
version: 1.0.0
license: Apache-2.0
author: AgentCAD core
requires: []
---

An enclosure is the part class where a plausible-looking model fails at
assembly: the wall is unprintable, the shell refuses, the lid binds, or the
boss splits on the first screw. This skill is the body/lid pair end to end -
wall thickness per process, shelling in the order OCCT tolerates, lid lips and
grooves with a real clearance number, screw bosses, PCB standoffs, vents, and
draft when the part is moulded. `snippets/two_part_enclosure.py` is the whole
pattern as a working script (body + lid as one two-solid `Compound`). Use
`snap-fits` instead for a screwless closure, `fits-and-clearances` for the
numbers behind a press fit or a heat-set insert, `sheet-metal` for a folded
chassis, and `fdm-design-rules` for printability in general.

## Wall thickness by process

| Process | Working wall | Absolute min | The constraint |
|---|---|---|---|
| FDM, 0.4 mm nozzle | 1.2-2.4 mm | 0.8 (2 perimeters) | a wall is an integer number of extrusion widths |
| SLA / DLP | 1.0-2.0 mm | 0.5 supported, 1.0 unsupported | thin walls cup and warp in post-cure |
| SLS (PA12) | 1.0-1.5 mm | 0.7 mm | powder removal and part handling |
| CNC, aluminium | >= 1.5 mm | 0.8 mm | tool deflection and chatter, not strength |
| CNC, plastic | >= 1.5 mm | 1.0 mm | clamping and springback |
| Injection moulding | 1.5-3.0 mm (ABS/PC/PA) | 0.9 mm (PP) | fill length and sink |

Rules and their limits:

- **Keep the wall uniform.** Moulded: variation beyond about +-10 % of nominal
  shows up as sink and warp. FDM: round the wall to n x extrusion width
  (0.8 / 1.2 / 1.6 / 2.0 / 2.4 on a 0.4 nozzle) - an off-multiple wall gets a
  gap-fill bead that is weaker than either neighbour.
- **Ribs on a moulded part are 0.5-0.6x the nominal wall**, height <= 3x wall,
  0.5-1.5 deg draft per side, base radius 0.25-0.5x wall. A full-wall rib is a
  sink mark on the show face. FDM has no sink, so a rib may be the full wall -
  the floor there is 2 extrusion widths.
- A tall thin box wants the **upper** end of the range: panel stiffness goes
  as t^3, so 1.2 -> 2.4 mm is 8x stiffer, not 2x.

## Shell the body (order of operations)

Box -> outer fillet -> shell -> bosses/standoffs/ribs -> holes -> cosmetic
fillets. Not taste: each step breaks the next if moved.

```python
from agentcad.toolkit import safe_fillet, safe_shell

body = Box(length, width, height, align=(Align.CENTER, Align.CENTER, Align.MIN))
r_out = max(r_out, wall + 0.5)                       # inner radius stays > 0
body, r_out, _w = safe_fillet(body, body.edges().filter_by(Axis.Z), r_out)
body, warn = safe_shell(body, wall, [body.faces().sort_by(Axis.Z)[-1]])
```

- `offset(amount=-wall)` **fails when an outer radius is <= the wall**: the
  inner corner radius `r_out - wall` would be negative. Clamp it, as above.
- **Shell before you add bosses.** Shelling a body that already carries bosses
  hollows the bosses too.
- After the shell, the topmost face is the **rim ring**, not the cavity floor.
  `plane="top"` in `agentcad.toolkit.holes` / `features` resolves to the
  largest outer face along the axis, so for anything inside the cavity pass an
  explicit `Plane.XY.offset(wall)`.
- `safe_shell`'s last fallback is an approximate boolean shell that is **not
  uniform on curved or slanted walls** (measured ~20 % thin on dome
  mid-sections). It says so in the warning - surface it, do not swallow it.
  The rest of the playbook: `selectors-and-occt-failures`.
- Prove the wall instead of asserting it:

```python
from agentcad.toolkit.specs import check_wall

SPECS = [check_wall(min_mm=1.2, grid=10, requirement="ENG-wall")]
```

`check_wall` is a sampled ray cast, so it finds fillet runouts: the measured
minimum sits below nominal by design. Pick the limit from a measurement.

## Lid interface: lip, tongue and groove

Three closures, cheapest first:

1. **Flat lid on a rim** - no location, no light seal. Only acceptable when
   four screws locate it.
2. **Tongue (lip)** - a ring on the lid underside dropping into the cavity.
   Depth 2-4 mm and at least 1.5x the wall; thickness 0.6-1.0x the wall. The
   default, and what the snippet builds.
3. **Tongue and groove** - a groove in the rim taking the lid's tongue. Needs
   a rim wide enough for `2 x groove_wall + tongue`, so the wall grows to ~3x
   nominal there; use it when a gasket or a light seal is needed.

**Clearance per side** - it is per side, so the box-to-box gap is twice it:

| Process | Sliding/seating clearance per side |
|---|---|
| FDM | 0.20-0.30 mm |
| SLA / SLS | 0.10-0.20 mm |
| CNC | 0.05-0.10 mm |
| Injection moulding | 0.05-0.10 mm, plus the draft loss |

The tongue must follow the cavity's **rounded** corners: the cavity inner
radius is `r_out - wall`, so the tongue's is `r_out - wall - clearance`. A
square tongue in a filleted cavity binds at the corners as soon as
`r_out - wall` exceeds ~3.4x the clearance.

```python
lo_x, lo_y = length / 2 - wall - clear, width / 2 - wall - clear
tongue = Box(2 * lo_x, 2 * lo_y, lip_h,
             align=(Align.CENTER, Align.CENTER, Align.MAX))
tongue, _r, _w = safe_fillet(tongue, tongue.edges().filter_by(Axis.Z),
                             max(0.4, r_out - wall - clear))
tongue -= Box(2 * (lo_x - lip_t), 2 * (lo_y - lip_t), lip_h + 2.0,
              align=(Align.CENTER, Align.CENTER, Align.MAX))
```

Gasket: for an O-ring face seal, groove width ~1.3x the cord and depth ~0.75x
the cord gives about 25 % squeeze; keep the groove corner radius >= 3x the
cord so the ring does not roll.

## Screw bosses

- **OD = 2x the screw nominal** (M3 -> 6 mm). Below ~1.8x the boss splits on
  the first drive; above ~2.5x a moulded boss sinks.
- **Self-tapping into plastic:** pilot = 0.8x nominal (M3 -> 2.4 mm),
  engagement >= 2x nominal, boss height = engagement + 1-2 mm so the screw
  never bottoms.
- **Heat-set insert:** boss bore = the insert's specified hole (usually its OD
  minus 0.1-0.3 mm), boss OD >= 2x the **insert** OD, 0.5-1 mm of boss below
  it. Numbers in `fits-and-clearances`.
- **Tapped or clearance holes:** use the hole wizard so the hole carries a
  record and reaches the drawing callout - `holes.tapped` bores the tap drill,
  `holes.clearance` the ISO 273 diameter (see `holes`).

```python
from agentcad.toolkit import holes

body, _recs, warn = holes.tapped(body, boss_pts, "M3",
                                 plane=Plane.XY.offset(height), depth=6.0)
```

- **A boss merged into a wall is a thick section.** Moulded: stand it off by
  >= 1 wall thickness and tie it with a rib 0.5-0.6x wall. FDM: merging into a
  corner is fine and stronger - embed it ~0.5 mm into the wall so the fuse is
  an overlap, never a tangency (a zero-area contact from which OCCT may hand
  back a non-manifold solid).
- For a free-standing boss, use the feature helpers - and note the plane:

```python
from agentcad.toolkit import features

floor = Plane.XY.offset(wall)                 # "top" can only be an OUTER face
body, w1 = features.boss(body, (bx, by), 6.0, boss_h, hole="M3", plane=floor)
body, w2 = features.rib(body, [(bx, by), (bx, width / 2 - wall)], 0.6 * wall,
                        to=boss_h - 1.0, plane=floor)   # gusset to the wall
```

`features.boss` warns that less volume arrived than the seed contains whenever
the boss overlaps existing material - the signature of a correctly merged boss,
not a fault. The warnings that *are* faults read "floating solid", "added no
material" or "N disjoint solids". More in `ribs-bosses-draft`.

## PCB standoffs and internal clearances

- Standoff OD 5-6 mm for a 3.2 mm (M3) board hole; pilot 2.4 mm for a
  self-tapping M3, or 0.2-0.4 mm radial clearance so the board drops in.
- **Height >= 3 mm** above the floor: through-hole solder fillets and clipped
  leads stand 1.5-2.5 mm proud of the board.
- Keep a standoff centre >= 1x its OD from a wall, or merge it in
  deliberately - a 0.3 mm slot between the two neither prints nor fills.
- Leave >= 2 mm over the tallest component and >= 1.5 mm around the board edge;
  a connector breaching a wall needs its cutout plus 0.3-0.5 mm all round.

## Vents and slots

- **Slot width sets the ingress rating.** IEC 60529's first digit is a probe
  diameter: IP3X excludes a 2.5 mm tool, IP4X a 1.0 mm wire. A 2 mm slot is
  IP3X and not IP4X; there is no arguing around it.
- FDM: slot width >= 2 extrusion widths (0.8 mm on a 0.4 nozzle; 1.5-2.0 mm
  prints cleanly). Ligament between slots >= the slot width and >= 2 walls.
- **Cut with a solid thicker than the wall** (`wall + 2 mm`), so the cutter
  passes clean through instead of landing tangent to the outer face; a
  tangential cut leaves slivers that fail the next boolean.
- Keep slots >= 3 mm clear of the floor, the rim and the corner bosses, so the
  wall keeps a continuous band.
- Vertical slots in a side wall need no support; slots in a top face are
  bridges - keep them under ~10 mm (`fdm-design-rules`).

## Draft and the parting line (moulded or cast only)

- **1 deg per side minimum** on any face parallel to the draw; 1.5-2 deg is
  the safe default. A textured face adds ~1 deg per 0.025 mm of texture depth.
- Deep bosses and ribs still need >= 0.5 deg per side; check the top does not
  close - a 6 mm boss 20 mm tall at 2 deg loses 1.4 mm of diameter.
- **Draft eats clearance.** A 4 mm tongue drafted 1 deg per side narrows
  0.07 mm root to tip. Specify the fit at the tightest section and add the
  draft loss at the other end.
- In build123d, **draft before you shell or fillet**. A shelled enclosure
  refuses `features.draft` above ~0.25 deg (measured through the worker), while
  a tapered extrusion (`features.rib(..., draft_deg=...)`, `extrude(taper=...)`)
  cannot fail that way.
- Put the parting line at the lid joint and pull every outer face from it.
  Anything undercut relative to that pull - a side port, a snap hook - needs a
  side action or a redesign; printed, the same feature only needs the right
  build orientation.

## Checklist

1. Wall from the table, uniform, rounded to n x extrusion width for FDM.
2. `r_out >= wall + 0.5` before shelling; clamp every dependent dimension
   inside `build()` (see `robust-parametrics`).
3. Shell, then features, then holes, then cosmetic fillets.
4. Lid clearance per side from the process table, applied to the tongue **and**
   to its corner radius.
5. Bosses at 2x nominal OD, pilot 0.8x nominal, engagement >= 2x nominal.
6. Tongue notched around every boss: notch radius = boss radius + clearance
   + ~0.3 mm.
7. Slot width matched to the ingress rating; ligaments >= slot width.
8. `check_wall` in `SPECS`; read every toolkit warning instead of discarding
   the second return value.
9. Ship body and lid as one `Compound` with `SOLID_LABELS = ["body", "lid"]`,
   so the fit is measurable in one part and each solid takes its own material.

## Sources

- IEC 60529:1989+A2:2013, *Degrees of protection provided by enclosures
  (IP Code)* - first-digit probe diameters (2.5 mm, 1.0 mm).
- DuPont, *General Design Principles for DuPont Engineering Polymers*,
  module I - nominal walls, rib proportions (0.5-0.6 t, h <= 3 t), boss and
  self-tapping-screw boss proportions.
- Covestro (Bayer MaterialScience), *Snap-Fit Joints for Plastics: A Design
  Manual* - boss/rib/draft proportions, undercut handling.
- Parker Hannifin, *Parker O-Ring Handbook* ORD 5700 - face-seal groove width
  and depth for ~25 % squeeze.
- Protolabs, *Injection Molding Design Guidelines* and *CNC Machining Design
  Guide* - resin nominal walls, draft minima, minimum machined wall.
- Formlabs, *Design Guide for SLA 3D Printing* - supported and unsupported
  minimum wall.
- UltiMaker, *Design for FFF*, and the Prusa Knowledge Base "wall thickness"
  article - extrusion-width multiples, minimum printable wall.
- SPIROL, *Inserts for Plastics - Design Guide* - boss OD vs insert OD and
  hole preparation for heat-set inserts.
- ISO 273 (clearance holes), ISO 261/262 (metric threads), vendored in
  `agentcad.toolkit.hole_standards`.
