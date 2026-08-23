---
name: fdm-design-rules
description: Design rules for FDM/FFF 3D printing — wall and feature minimums, 45 deg overhangs, bridging, teardrop holes, elephant foot, warping, orientation, tolerances.
triggers: [fdm, 3d print, 3d-print, printed, printing, overhang, bridge, support, layer, nozzle, teardrop, warp]
version: 1.0.0
license: Apache-2.0
author: AgentCAD core
requires: []
---

FDM/FFF parts fail for geometric reasons a kernel can see long before a slicer
runs: a wall thinner than two extrusions, a face leaning past 45°, a round hole
lying on its side, a corner that peels off the bed. This skill is the rule set
for a part that prints right the first time with no support, plus the checks to
run before you ship an STL. Use it when the process is fused filament. Do
**not** use it for SLA/DLP (no overhang rule; drain holes and
cure shrinkage instead), SLS/MJF (no support at all, different minimums), or
any subtractive process. See `fits-and-clearances` for mating clearances,
`snap-fits` for latches, `enclosures` for housing walls, and
`selectors-and-occt-failures` for fillet failures.

## Rules at a glance

Everything derives from the extrusion line width `w` ≈ 1.0–1.2 × nozzle
(0.42 mm on a 0.4 mm nozzle). Values below assume 0.4 mm and 0.2 mm layers.

| Rule | Value | Limit / why |
| --- | --- | --- |
| Minimum wall | 0.8–1.2 mm (2–3 × w) | under 2 perimeters the slicer drops it |
| Load-bearing wall | ≥ 1.6 mm (4 × w) | 2 perimeters is a skin, not structure |
| Emboss / engrave | ≥ 0.8 mm wide, 0.6 mm deep | narrower than 2 × w is skipped |
| Pin, peg, rib | ≥ 2 mm dia (3 loaded), L/D ≤ 8 | thin towers wobble, delaminate |
| Vertical hole | ≥ 2 mm dia | smaller and the perimeter closes over |
| Overhang | ≥ 45° from the bed plane | shallower needs support or a redesign |
| Unsupported bridge | ≤ 10 mm clean, ≤ 25 mm sagging | anchored at both ends |
| Hole compensation | +0.2 mm on the **diameter** | printed holes come out small |
| Elephant-foot chamfer | 0.3–0.5 mm at 45°, bed edges | the first layer spreads |
| Bed-corner fillet | R ≥ 3 mm | sharp corners lift first |
| Tolerance | ±0.2 mm, ±0.1 mm calibrated | add ±0.2 % above 100 mm |
| Layer height | 0.1–0.3 mm (≤ 0.75 × nozzle) | quantises every Z dimension |

Derive them — a 0.6 mm nozzle moves every row:

```python
PARAMS = {
    "nozzle": {"default": 0.4, "min": 0.2, "max": 1.0, "unit": "mm",
               "description": "Nozzle diameter"},
}

def build(p):
    line_w = p.nozzle * 1.05     # slicer extrusion width
    min_wall = 2.0 * line_w      # two perimeters, the floor
    ...
```

## Walls, features and the nozzle

Keep a wall an integer multiple of `w` (0.84, 1.26, 1.68 …). A 1.0 mm wall at
0.42 line width prints as two lines plus a 0.16 mm gap the slicer either leaves
hollow or fills by over-extruding — a ridge, and weak either way. Step
thicknesses by a whole line width.

Engrave rather than emboss: raised text under two lines wide disappears, while
engraved text 0.6 mm deep with a 0.8 mm stroke and ≥ 4 mm cap height survives.
Never put text on the bed face — the first-layer squish fills it.

## Overhangs, bridging, support avoidance

Self-supporting means the downward-facing surface makes **≥ 45° with the bed
plane**, so each layer overlaps the one beneath by at least half a line width.
Below 45°, in order of preference: reorient the part; replace the overhang with
a 45° chamfer; add a self-supporting gusset; add a sacrificial bridge (a 1–2
layer web across the gap, cut off after printing); only then accept support.

**Chamfer, do not fillet, where material meets the bed or an overhang.** A
fillet's tangent leaves horizontal at 0°, so its first millimetre is an
unprintable overhang that droops; a 45° chamfer prints exactly as modelled.
Fillets stay welcome on vertical (Z-parallel) and upward-facing edges. Author
the 45° transitions into the profile rather than adding them afterwards — run
equals rise:

```python
def side_profile(x_face, x_pad, plate_t, gusset, z_ramp):
    pad = x_pad - x_face
    return [
        (x_face + gusset, plate_t),   # gusset toe on the plate
        (x_face, plate_t + gusset),   # run == rise -> 45 deg
        (x_face, z_ramp - pad),       # foot of the pad ramp
        (x_pad, z_ramp),              # run == rise -> 45 deg
    ]
```

Bridging: a span anchored on solid material at both ends prints to ~10 mm with
no visible sag, 10–25 mm with 0.1–0.5 mm of droop, and ropes beyond. A bridge
needs one direction and two anchors — exactly what a round horizontal hole
lacks at its widest point.

## Holes

**Printed holes come out small.** The perimeter over-extrudes at inner corners
and the arc is approximated from inside, so a bore measures 0.1–0.3 mm under
nominal. The error is near-constant in absolute terms, so it dominates small
holes — a 3.4 mm M3 clearance can measure 3.15 mm. Compensate +0.2 mm on the
diameter as a **parameter** you can recalibrate, never a constant in a radius.

**Horizontal holes need a teardrop or polygon top.** The roof of a bore whose
axis is parallel to the bed is a bridge over nothing. A teardrop — the circle
plus a 45° apex — keeps the bore round where a shaft or bearing touches and
prints support-free; a hexagonal top suits a captive nut.

```python
from math import sqrt

r = (bore_d + 0.2) / 2.0
t = r / sqrt(2.0)                       # where the 45 deg roof meets the bore
with BuildSketch(Plane.YZ) as teardrop:
    Circle(radius=r)
    with BuildLine():
        Polyline((-t, t), (0.0, r * sqrt(2.0)), (t, t), close=True)
    make_face()
```

Do not model FDM threads below M6 — use a heat-set insert boss or a
self-tapping pilot (≈ 0.8 × major dia in PLA/PETG); see
`threads-and-fasteners`. Counterbores print fine (their floor is a bridge no
wider than the head), and a 90° countersink is a 45° cone either way.

## First layer and warping

The first layer is squashed into the bed and spreads 0.1–0.4 mm past nominal —
the elephant foot. Slicers can compensate, but the setting is printer-specific
and invisible to your model; put the chamfer in the geometry and the part sits
flat and mates correctly on any machine.

```python
foot = min(p.foot_ch, plate_t * 0.3)          # 0.3-0.5 mm typical
if foot > 0.01:
    chamfer(part.edges().group_by(Axis.Z)[0], length=foot)
```

Warping is a cooling part's shrinkage concentrated at its corners. Geometric
fixes: fillet the footprint corners (R ≥ 3 mm — a sharp corner is the stress
concentration and lifts first); break a large flat base into ribs or a grid
rather than one slab; keep unbroken flat runs under ~100 mm in ABS/ASA/PA;
avoid abrupt cross-section changes along Z; add mouse-ears (sacrificial 8–12 mm
discs, 2 layers, at each corner) when the footprint cannot change.

The two rules look contradictory and are not — they act on different edges:
**fillet the vertical corner edges** (`edges().filter_by(Axis.Z)`), **chamfer
the horizontal bed edges** (`edges().group_by(Axis.Z)[0]`).

## Orientation for strength

The layer interface is the weak axis. Inter-layer (Z) tensile strength is
typically 30–70 % of in-plane, and Z is also the impact and fatigue weak
direction; Ahn et al. measured FDM ABS at 10–65 % of the injection-moulded
value depending on raster orientation. So:

- **Put bending and tensile loads in the layer plane.** An L-bracket printed
  standing up loads its corner straight across the layers and snaps there; laid
  flat — the whole L in one plane, load in XY — it is several times stronger
  *and* needs no support. That is the snippet's orientation.
- A screw pulling out of a boss loads layers apart: prefer a heat-set insert, a
  through-bolt, or a boss whose axis lies in the layer plane.
- Pick the largest flat face for the bed, then break ties by load direction,
  support-free, surface finish.
- Anisotropy is not a safety factor. If the load has to cross layers, size the
  part against the Z strength, not the datasheet tensile value.

## Tolerance envelope

Well-tuned desktop FDM holds ±0.2 mm under 100 mm, ±0.1 mm once flow and
shrinkage are calibrated, plus ±0.2 % of the dimension above 100 mm.
Industrial FDM is published at ±0.127 mm or ±0.0015 mm/mm, whichever is
greater. XY repeats better than Z, and Z is quantised by layer height — make
critical heights an integer multiple of it.

Design the clearance; do not rely on the tolerance. Per side: 0.2–0.3 mm
sliding, 0.4–0.5 mm loose or rotating, 0.05–0.1 mm interference for a press
fit. Anything tighter than ±0.1 mm gets machined or reamed after printing.
See `fits-and-clearances`.

## Materials at a glance

| Material | Nozzle °C | Bed °C | Shrink | Stiffness | Warp | Use for |
| --- | --- | --- | --- | --- | --- | --- |
| PLA | 200–220 | 55–60 | ~0.3 % | high, E ≈ 3.0–3.6 GPa, brittle | low | jigs, fit checks, indoor non-structural (Tg ≈ 60 °C) |
| PETG | 230–250 | 70–85 | ~0.4 % | medium, E ≈ 1.7–2.1 GPa, tough | low | functional brackets, outdoors, chemicals |
| ABS | 240–260 | 90–110 | 0.8–1.5 % | medium, E ≈ 1.8–2.3 GPa | high | heat and impact, vapour finishing (Tg ≈ 105 °C) |
| ASA | 240–260 | 90–110 | ~0.8 % | medium | high | UV-stable outdoor ABS |
| PA (nylon) | 250–290 | 80–110 | 0.8–2.0 % | medium-low, very tough | high | gears, living hinges, wear parts — dry it first |
| TPU 95A | 220–240 | 30–60 | 0.5–1.2 % | flexible | low | gaskets, bumpers, seals — slow, direct drive |

Class-typical; the filament's own datasheet wins. Shrink is cooling shrinkage
compensated on long dimensions — a different effect from the hole compensation
above, which is dominated by the extrusion path.

## Pre-print checklist

Run these before declaring a part printable; each is a real call on the part.

1. **Wall** — `analyze_part(kind="wall", min_required=2*line_w)` returns
   `min_thickness_mm` *and its `location`*. It is a sampled ray cast, so it
   also finds chamfer runouts: read the location before believing a red.
2. **Build volume** — `metrics.bbox` against the printer envelope.
3. **Footprint** — `analyze_part(kind="projected_area", axis="Z")` versus the
   real bed-contact area; a part standing on a knife edge will not stick.
4. **Overhangs** — `analyze_part(kind="section", plane="XZ")` for the
   silhouette; walk it for downward faces under 45°. Still manual today.
5. **One solid** — `metrics.solids` should be 1, or match `SOLID_LABELS`; a
   stray extra solid is material floating in mid-air.
6. **Extremes** — rebuild at every parameter's min and max
   (`robust-parametrics`).

Pin the checkable ones as intent so every rebuild re-runs them:

```python
from agentcad.toolkit.specs import check_bbox, check_wall

SPECS = [
    check_wall(min_mm=0.84, grid=12, requirement="FDM-min-wall"),
    check_bbox([250.0, 210.0, 210.0], name="fits_build_volume"),
]
```

PRD-021 will ship these rules as an FDM process profile — `check_dfm {process:
"fdm"}` returning *located* violations for overhang angle, bridge span and
minimum feature — so a rule written as a `SPECS` entry today becomes a pack
rule then, with the same read-fix-recheck loop.

`snippets/printable_bracket.py` puts it together: an L-bracket that prints flat
with no support, its side profile one closed polyline whose gusset and bore-pad
transitions are authored at 45°, a teardrop bore sized `bore_d + hole_comp`,
an elephant-foot chamfer on the bed edges, minimum wall clamped to two line
widths off `nozzle`, and fixing holes through `holes.clearance`.

## Sources

- ISO/ASTM 52910:2018, *Additive manufacturing — Design — Requirements,
  guidelines and recommendations*; ISO/ASTM 52900:2021 (terminology).
- Gibson, Rosen & Stucker, *Additive Manufacturing Technologies*, 3rd ed.,
  Springer, 2021 — design for AM, anisotropy, shrinkage.
- Redwood, Schöffer & Garret, *The 3D Printing Handbook*, 3D Hubs, 2017 —
  FDM feature minimums, overhang and bridging limits, tolerance bands.
- Ahn, Montero, Odell, Roundy & Wright, "Anisotropic material properties of
  fused deposition modeling ABS", *Rapid Prototyping Journal* 8(4), 2002.
- Stratasys, *FDM Best Practice: Designing for FDM*, and its accuracy
  statement (±0.127 mm or ±0.0015 mm/mm).
- UltiMaker, *Design for FDM 3D printing*; Prusa Research Knowledge Base —
  bridging, elephant-foot compensation, warping.
- Filament datasheets for the property table (UltiMaker PLA/PETG/ABS/Nylon/
  TPU 95A; Prusament PLA/PETG/ASA).
