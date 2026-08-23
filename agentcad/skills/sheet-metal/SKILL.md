---
name: sheet-metal
description: SheetPart - one spec yielding both the folded solid and the flat pattern, with bend allowance and K-factor, partial flanges, automatic bend relief, hems, corners and SVG/DXF export.
triggers: [sheet metal, sheetmetal, bend, bends, flange, flat pattern, unfold, fold, k-factor, bend allowance, bend relief, hem, corner, brake, blank, dxf, gauge]
version: 1.0.0
license: Apache-2.0
author: AgentCAD core
requires: []
---

A sheet-metal part is two drawings that must agree: the folded part the
assembly sees, and the flat blank the laser and the press brake see. Model them
separately and they *will* diverge — a flange length changed on one, a bend
allowance not applied to the other. `agentcad.toolkit.sheetmetal.SheetPart`
takes one declarative spec and produces both, so disagreement is impossible.
Use this skill for any bracket, chassis, enclosure panel or cover that will be
cut and bent from flat stock. Do not use it for a machined or printed part that
merely *looks* like folded sheet: a solid modelled with `Box` and fillets has
no flat pattern and needs none. Thickness is uniform by construction — a part
with two thicknesses is two sheet parts.

## The declarative spec

`from agentcad.toolkit.sheetmetal import SheetPart`; tool: `flat_pattern`.

One declarative spec yields BOTH the folded solid and the manufacturing flat
pattern, so they can never disagree. Base plate centered on the origin, width
along X, depth along Y, z in `[0, t]`; edges `left/right/front/back`
(`x=-w/2`, `x=+w/2`, `y=-d/2`, `y=+d/2`). Flanges bend UP (+Z); angle in
(0, 180) exclusive; `inner_radius` defaults to the thickness. `start`/`width`
place a PARTIAL flange (start from the edge's low-coordinate end, X- or Y-;
`width=None` = whole edge); several per edge as long as their spans do not
overlap. Bend allowance `BA = radians(angle) * (R + K*t)`; each flange adds
`BA + length` of flat stock beyond its edge (K=0.44 default suits air-bent
steel/aluminum).

```python
def _sheet(p):
    return (SheetPart(p.thick, k_factor=0.44)
            .base(p.width, p.depth)
            .flange("front", 90, p.flange_len, inner_radius=p.bend_r))

def build(p):
    return _sheet(p).fold()          # single valid folded solid

def flat_pattern(p):                 # optional contract -> enables the
    sp = _sheet(p)                   # flat_pattern export tool
    return sp.unfold(), sp.bend_lines()
```

Note the shape of that script: **one builder function, two contracts**. `build`
and `flat_pattern` both call `_sheet(p)`, so there is exactly one place a
dimension lives. Writing the spec twice is the mistake this design exists to
prevent.

## What you can ask the spec for

```
sp.unfold()       -> flat blank as a solid (base + BA+length tab per edge)
sp.flat_outline() -> [(x, y), ...] CCW outline polygon of the blank; it is
                     a discretization of unfold()'s OWN top face, not a
                     second model, so it cannot disagree with the blank
                     (it therefore COSTS an unfold(), 11-70 ms, not free)
sp.flat_outline_edges() -> the same outline as exact lines and arcs
sp.bend_lines()   -> [{"edge","a","b","angle_deg","inner_radius"}, ...]
                     midlines spanning each flange's own extent, flat coords
```

## The volume difference is the model, not a bug

`fold()` and `unfold()` differ in volume by EXACTLY
`radians(angle)*(0.5-K)*t^2*span` per bend — the solid puts the neutral fibre
at `t/2`, the flat model at `K*t` — and by nothing else. That is the
bend-allowance model's own tolerance, it does not accumulate with feature
count, and it is not a bug to report.

The K-factor is a *shop* number: 0.44 suits air-bent steel and aluminium, but a
bottoming or coining operation, a different alloy, or a tight radius all move
it. If the shop has a value, pass theirs. If a `check_volume` spec compares
fold and unfold, it must allow for the formula above rather than expecting
equality.

## Bend relief, hems and corners

Bend relief is cut automatically wherever a partial flange stops in the middle
of an edge, in BOTH `fold()` and `unfold()`:
`relief="auto"|"rect"|"round"|"tear"` or `{"kind","width","depth"}`. The
default size (1.5*t wide, R+t past the bend line) is a SHOP RULE, not a
standard. `"tear"` removes nothing and warns.

```
sp.hem(edge, kind="open"|"closed", length, start=0.0, width=None)
    a 180 deg bend folding the leaf back over the sheet; the air gap is
    2R -- open R=t (gap 2t), closed R=t/2 (gap t), both shop defaults.
    kind="teardrop" RAISES: past 180 deg the leaf descends into the sheet
    after R*(1-cos a)/-sin a (2.41*R at 225 deg) while a hem leaf needs
    >= 4t, and the fuse swallows the overlap silently. Not approximated.

sp.corner(edge_a, edge_b, "close"|"gap"|"rip")
    close mitres the two leaves on the 45 deg bisector; gap opens one
    thickness; rip is the untreated corner. Declare the flanges first.
```

## Export, and what raises

The `flat_pattern` tool renders the unfolded blank to SVG (outline + dashed
bend lines with angle/radius callouts) or DXF (layers OUTLINE and BEND) at
`exports/<part>_flat.<ext>`. Overlapping spans on one edge, angle 0/180 in
`flange()`, or `flange()` before `base()` raise `ValueError`; read
`sp.warnings` after `fold()` — it records fusion fallbacks AND a fold that did
not come out as one valid solid, because OCCT reporting success is not evidence
that it did.

DXF is not byte-stable between runs, so a determinism check compares the SVG.
The DXF is the manufacturing artefact; the SVG is the one to diff.

## Design rules worth knowing before you write the spec

- **Minimum flange length** ≈ `4·t + R` measured from the bend line; shorter
  and the brake's die cannot hold it.
- **Minimum inner radius** ≈ `1·t` for soft aluminium and mild steel, more for
  hard tempers; the default `inner_radius = t` is the common floor, not a
  guarantee for every alloy.
- **Hole to bend** ≥ `2.5·t + R` from the bend line, or the hole distorts into
  an oval.
- **Bend relief** on every partial flange — it is automatic here, but the
  *width* (1.5·t default) is what stops the tear at the end of the bend.
- **Grain direction** matters for a tight radius on a hard temper; the model
  cannot express it, so it belongs in a note on the drawing.

## Checklist

- [ ] One `_sheet(p)` builder feeds both `build` and `flat_pattern`.
- [ ] K-factor matches the shop's process, or 0.44 is a stated assumption.
- [ ] Every flange is at least `4·t + R` long and every hole `2.5·t + R` from
      its bend line.
- [ ] Partial flanges have their relief kind and width chosen deliberately.
- [ ] `sp.warnings` is read after `fold()`; a fold that is not one valid solid
      is reported, not shipped.
- [ ] The exported blank was looked at, not just generated.

## Sources

- AgentCAD toolkit source: `agentcad/toolkit/sheetmetal.py` — `SheetPart`,
  `base`, `flange`, `hem`, `corner`, `fold`, `unfold`, `flat_outline`,
  `bend_lines`, the bend-allowance formula and the relief defaults.
- AgentCAD source: `agentcad/core/tools_drawing.py` and the `flat_pattern`
  tool — the SVG/DXF export and its layers.
- ASM International, *Metals Handbook*, Vol. 14 (Forming and Forging):
  bend allowance, the neutral-axis K-factor and minimum bend radii.
- Machinery's Handbook (Industrial Press), sheet-metal bending allowance
  tables.
- build123d documentation, *Objects and operations*:
  <https://build123d.readthedocs.io/>
