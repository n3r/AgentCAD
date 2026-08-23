---
name: snap-fits
description: Cantilever and annular snap-fit design - beam length and taper, permissible strain per material, undercut, insertion and return angles, catch clearance, FDM orientation.
triggers: [snap, snap-fit, snapfit, cantilever, clip, latch, hook, lid, catch, undercut, retention]
version: 1.0.0
license: Apache-2.0
author: AgentCAD core
requires: []
---

A snap-fit holds a *position* by elastically deflecting a beam past an
undercut and letting it spring back. Everything else follows from one number:
the material's permissible strain. Size the beam from that, spend part of it
on the undercut, and the joint works; guess the undercut and the hook either
snaps off (too deep) or falls open (too shallow). Use this skill whenever a
lid, cover, clip, latch or press-together housing has to assemble without
fasteners. Do **not** use a snap to carry a *sustained* load — plastics creep,
and a beam held deflected loses most of its retention in weeks; use a screw
(`enclosures`) or a press fit (`fits-and-clearances`) for that. Do not use one
for a seal (the lip does the sealing, the snap only holds it shut), and think
twice about hundreds of open/close cycles in PLA.

## Size the beam before you draw anything

Rectangular cantilever, width `b`, thickness `t` at the root, free length `L`,
permissible strain `ε` (a fraction, not a percent).

- **`L ≥ 5·t`**, and 5–10 is the useful range. Below 5 the slender-beam
  equations overpredict the deflection you can get (root rotation and shear
  stop being negligible); above ~10 the beam is floppy and the retention force
  disappears.
- **Taper the thickness 1:2 root→tip.** Constant section puts all the strain
  at the root; a beam tapering `t` → `t/2` strains almost uniformly and takes
  **64 % more deflection at the same root strain and the same force**.

Permissible tip deflection (Bayer/BASF snap-fit manuals):

| beam | permissible deflection `y` |
| --- | --- |
| constant section `t` | `y = ε·L² / (1.5·t)` |
| thickness tapered `t → t/2` | `y = 1.64 · ε·L² / (1.5·t)` |
| width tapered `b → b/4` | `y = 1.28 · ε·L² / (1.5·t)` |

`ε·L²/(1.5·t)` and `2·ε·L²/(3·t)` are the *same* expression — both are the
**constant-section** formula. The taper is the 1.64 factor, never a rewrite of
the denominator; a source calling `ε·L²/(1.5·t)` the tapered formula has
dropped it. Invert to audit an existing hook: `ε = 1.5·y·t / (1.64·L²)`.

Deflection force at the root and the force you feel on assembly:

- `P = b·t²·E·ε / (6·L)` — the taper does **not** change this: the root
  section sets the force, the taper only buys deflection.
- `W = P·(μ + tan α) / (1 − μ·tan α)` — `α` is the lead angle (below),
  `μ ≈ 0.3` for plastic on plastic (0.2 for PP/POM, 0.4 for a dry rough
  printed surface).

Worked: PLA, `ε` 1.2 %, `t` 2 mm, `L` 12 mm, `b` 12 mm, `E` ≈ 3300 MPa →
`y = 1.64·0.012·144/(1.5·2) = 0.94 mm`, undercut 0.66 mm, `P = 26 N`,
`W ≈ 28 N` per hook at `α = 30°`. Two hooks = ~56 N to close the lid: firm,
one-handed, right.

## Permissible strain by material

Full table with a source per row: [tables/material_strain.json](tables/material_strain.json).

| material | ε one-time | design value | note |
| --- | --- | --- | --- |
| PLA | 1.0–1.5 % | 1.2 % | stiff, notch-sensitive — long beam, small undercut |
| PETG | 2.5–3 % | 3 % | best printed default: ductile, forgiving |
| ABS | 4–6 % | 4 % | the classic moulded snap resin |
| PA (nylon) | 4–6 % | 6 % | value is dry-as-moulded |
| PP | 6–8 % | 8 % | highest of the commodity resins |
| PC | 3–4 % | 4 % | tough but very notch-sensitive |

Deratings that are not optional: **halve** for a joint opened and closed
repeatedly; **halve again** outside ~0–40 °C; take **50–70 %** of the value
for FDM even when the layers lie in the bending plane, and **30 %** when they
do not (see below). Glass fill cuts permissible strain hard (a 30 % GF nylon
is nearer 1.5 % than 6 %) — never reuse an unfilled number for a filled grade.

## The undercut rule

**`undercut ≤ y_perm`** is the hard limit — the beam must deflect by the full
undercut to assemble. Design to **`undercut ≈ 0.7·y_perm`** so tolerance,
temperature and the ramp's own elasticity have somewhere to go. Then clamp:

- `undercut ≥ 0.3 mm` on a printed part, or the layer/tolerance noise
  (±0.15 mm typical) eats the retention. If 0.7·`y_perm` < 0.3 mm the beam is
  too short or too thick — lengthen `L` (deflection goes as `L²`) before you
  thin `t`.
- `undercut ≤ 0.8·t_tip`, or the nose is a spike that shears off.
- `undercut ≤` the catch wall thickness, minus 0.3 mm.

Free space **behind** the beam must be at least `y_perm + 0.3 mm`, or the beam
bottoms out on the wall before the hook clears and you snap the root.

## Insertion and return angles, and the catch

Both angles are measured **from the insertion axis** (the direction of
assembly), so 90° is a shoulder square to the pull.

| joint | insertion α | return β | behaviour |
| --- | --- | --- | --- |
| permanent | 30° | 90° | square shoulder — only releases if you push the beam aside by hand |
| releasable | 30° | 45° | pulling cams the beam out |

`α` below 20° makes a long fragile ramp; above 45° the insertion force nearly
doubles (`tan 60° = 1.73`). A permanent joint needs a **release tab** if it is
ever to be serviced: extend the beam past the nose by ≥ 3 mm so a fingernail
or a screwdriver can push it clear.

The mating catch — a window, a ledge or a groove in the other part:

- depth in the pull direction ≥ `undercut + 0.1–0.2 mm`, so the hook seats
  without preload. A preloaded shoulder creeps and the joint loosens.
- 0.2–0.3 mm clearance per side around the beam (FDM); 0.1 mm for SLA/CNC.
  `fits-and-clearances` has the process table.
- take rattle out with the lip or a gasket, never by preloading the snap.
- a through window makes engagement *visible and audible*; a blind ledge needs
  `undercut + 0.8 mm` of material left behind it.

## Annular (ring) snaps

A full 360° ring snapped over a shaft or into a bore strains in *hoop*, not in
bending. Stretching a ring of diameter `d` over an interference `y` grows its
circumference by `π·y`, so `ε = y/d` and

`y_max = ε_perm · d`

PP at 8 % on a 20 mm boss allows 1.6 mm of undercut; PLA at 1.2 % allows
0.24 mm — which is why full rings belong to compliant resins (PP, PE, TPU) and
almost never to PLA. Notes that decide the design:

- if both parts flex, they share the interference; a rigid shaft in a
  compliant hub puts all of it in the hub.
- a ring is far stiffer than a cantilever, so the assembly force is large even
  when the strain is legal — check `W` before choosing one.
- **slot it.** Three to six slots turn the ring into cantilever fingers; each
  finger then follows the cantilever math with `b` = its arc width. This is
  the right answer for FDM and for any stiff resin, and it is far easier to
  print than a continuous undercut.
- DuPont's handbook has geometry factors refining `y_max` for a thick hub.

## FDM: orientation is the whole game

- **Put the beam in the layer plane.** A hook printed with its axis vertical
  loads the layer bonds straight in tension at the root, where strain to
  failure is 30–50 % of the in-plane value. This is the single most common
  cause of a snapped hook. Print the lid flat with the beams pointing
  sideways, or split the part.
- **Root fillet `r ≥ 0.5·t`.** A sharp root is a stress concentration of
  2–3× and is exactly where every beam breaks; at `r/t = 0.5` the factor is
  down near 1.2. The fillet stiffens the root slightly, so do not count it as
  free length — measure `L` from the end of the fillet.
- The `β = 90°` retention face is a 90° overhang of depth `undercut`. Up to
  ~0.6 mm it bridges cleanly; deeper, flip the part so the ramp is the
  overhang, or chamfer the shoulder to 45° (which also makes it releasable —
  decide, do not discover).
- Elephant foot spreads the first layer 0.1–0.2 mm: a nose printed on the bed
  loses its clearance. Chamfer 0.4 mm on bed-side edges.
- Keep `t` a whole multiple of the extrusion width (2.0 mm at a 0.4 nozzle =
  5 lines) so the beam is solid perimeters and not sparse infill.

More process rules: `fdm-design-rules`. The mating housing: `enclosures`.

## Building it in build123d

Working part: [snippets/cantilever_lid.py](snippets/cantilever_lid.py) — a
parametric lid with two opposed tapered hooks that derives its undercut from
the material's strain. The order of operations matters:

1. plate, 2. plate corner fillets, 3. the beams, 4. the noses,
5. the root fillet **last**.

```python
from math import radians, tan

TAPER_GAIN = 1.64          # t -> t/2 taper

def hook_sizes(eps, t_root, beam_l, insert_deg, requested):
    beam_l = max(beam_l, 5.0 * t_root)
    y_perm = TAPER_GAIN * eps * beam_l ** 2 / (1.5 * t_root)
    undercut = max(min(requested, 0.70 * y_perm, 0.8 * (t_root / 2)), 0.15)
    rise = undercut / tan(radians(insert_deg))   # lead-in ramp height
    return beam_l, y_perm, undercut, rise
```

Traps, each of which has cost somebody a rebuild:

- `part.edges().filter_by(Axis.Z)` for the plate corners catches **the beams'
  vertical outer edges too**. Fillet the plate before the beams exist.
- Taper with `loft()` between two rectangles, not `extrude(taper=…)` — the
  taper argument draws in on all four sides, and you want the outer face flat
  because it carries the nose.
- **Start the beam root inside the plate** (0.5–1 mm). Two exactly coplanar
  touching faces are a degenerate boolean; an overlap always fuses.
- Select the root-fillet edges by an explicit band in *both* X and Y —
  `group_by(Axis.Z)[0]` hands you the plate's whole underside perimeter. Then
  use `safe_fillet`, which searches the radius down instead of failing
  (`selectors-and-occt-failures`).
- A beam that never reached the plate **fuses successfully as a second
  solid**. Assert it: `check_that(lambda part, m: len(part.solids()) == 1,
  name="one_solid")` (`design-specs`). `n_solids` in the build metrics is the
  same evidence.
- Clamp `β` at 88° before `tan` — `tan(90°)` gives a zero-height sliver face
  that OCCT will happily turn into an invalid solid.

## Checklist

- [ ] `L ≥ 5·t`, thickness tapered `t → t/2`.
- [ ] `y_perm` computed from the *material's* ε, derated for FDM and cycles.
- [ ] `0.3 mm ≤ undercut ≤ 0.7·y_perm` and `≤ 0.8·t_tip`.
- [ ] Free space behind the beam ≥ `y_perm + 0.3 mm`.
- [ ] Catch depth = undercut + 0.1–0.2 mm; 0.2–0.3 mm side clearance.
- [ ] α = 30°; β = 90° (permanent, with a release tab) or 45° (releasable).
- [ ] Root fillet `r ≥ 0.5·t`; beam axis in the layer plane.
- [ ] Assembly force `W` per hook × hook count is a force a hand can apply.
- [ ] Build reports one solid, `is_valid`, no dropped features.

## Sources

- Bayer MaterialScience (now Covestro), *Snap-Fit Joints for Plastics — A
  Design Manual* (1998): cantilever deflection formulas, the 1.64 / 1.28
  taper factors, permissible-strain table, the mating-force equation.
- BASF Corporation, *Snap-Fit Design Manual*: the same beam equations and
  permissible strains, independently published.
- DuPont, *General Design Principles for DuPont Engineering Polymers*,
  Module I — cantilever and cylindrical (annular) snap-fit sections,
  hub/shaft geometry factors.
- G. Erhard, *Designing with Plastics* (Hanser, 2006): permissible strain,
  notch sensitivity, creep under sustained deflection.
- NatureWorks *Ingeo* 3D-printing grade technical data sheets (4043D, 3D850)
  and Eastman Chemical copolyester (*Eastar*, *Amphora* 3D) technical data
  sheets, ISO 527 tensile data — the basis for the derated PLA and PETG rows.
