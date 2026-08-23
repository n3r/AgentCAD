---
name: ribs-bosses-draft
description: features.rib, features.boss and features.draft - stiffening ribs, screw bosses and mould draft, the plane predicate, draft's low ceilings, and the silent fuse when a feature misses.
triggers: [rib, ribs, gusset, boss, bosses, screw boss, standoff, draft, draft angle, taper, mould, molding, stiffener, features, neutral plane, plane]
version: 1.0.0
license: Apache-2.0
author: AgentCAD core
requires: []
---

Ribs stiffen, bosses receive screws, and draft lets a part leave a mould. All
three are `agentcad.toolkit.features` one-liners, and all three share the same
failure: they fuse happily onto nothing. A rib that misses the part and a boss
standing in mid-air both raise the volume by exactly the right amount, report
`is_valid True` and leave you a *second solid* nobody looked at. Use this skill
when adding stiffening to a thin wall, a screw boss to a housing, or draft to
anything that will be injection moulded or cast. The `enclosures` skill covers
where bosses belong in a housing; this one covers how to make them and how to
know they landed. Draft in particular has measured ceilings far lower than
intuition suggests — read that section before choosing an angle.

## The three helpers

`from agentcad.toolkit import features`

```
part, warn = features.rib(part, profile, thickness, to=8.0, plane="top",
                          draft_deg=None)
    # `profile` is a polyline of (u, v) points in the plane's coordinates,
    # traced to `thickness` and extruded away from the seat. to=<mm> is
    # exact. to="part" intersects the part's BOUNDING SOLID -- an envelope,
    # not the part: on a convex part it adds 0 mm3, on a shelled one it
    # runs to the bbox top. It always warns. draft_deg TAPERS the
    # extrusion; it never calls the draft operation (see below).

part, warn = features.boss(part, at, d, h, hole="M3", draft_deg=None)
    # a cylinder standing h above the seat; hole="M3" bores the tap drill
    # through holes.tapped so the screw boss carries a RECORD.

part, achieved_deg, warn = features.draft(part, faces, angle_deg,
                                          neutral_plane, min_angle=0.25)
    # `faces` is a list of Face objects or a selector callable f(part) ->
    # faces. NEVER indices. On failure it binary-searches DOWN.
```

## The plane predicate, and which way things grow

`plane` is holes' predicate and the normal points OUT of the material: holes
drill along -z_dir, ribs and bosses grow along +z_dir. After you add a rib,
"top" resolves to the RIB's top face — pass an explicit `Plane` for anything
inside a cavity.

That last sentence is the ordering trap in one line. Adding three ribs with
`plane="top"` in a loop puts the second rib on top of the first. Resolve the
plane once, before the loop, and pass the `Plane` object:

```python
from agentcad.toolkit import features

def build(p):
    part = _shell(p)
    seat = Plane(part.faces().filter_by(Plane.XY).sort_by(Axis.Z)[0])
    for u in (-20.0, 0.0, 20.0):
        part, warn = features.rib(part, [(u, -30.0), (u, 30.0)],
                                  p.rib_t, to=p.rib_h, plane=seat)
    return part
```

`to="part"` deserves its warning: the bounding solid is an *envelope*, so on a
convex part the rib is entirely inside the envelope and adds 0 mm³, and on a
shelled one it runs all the way to the bbox top rather than to the wall you had
in mind. Use an explicit `to=<mm>` whenever you know the height, which is
nearly always.

## Draft's ceilings are low and its failure is quiet

DRAFT'S CEILINGS ARE LOW AND ITS FAILURE IS QUIET. Measured through the worker:
box 35 deg, box+boss 15, box+fillets 10, shelled box 2.5, gusset_plate 17.5,
prototyping/enclosure_base 0.25, and rocketry/nozzle and
construction/angle_bracket refuse EVERY angle. Failure is monotone in the angle
(no islands). Only the extreme angles raise — most failing angles RETURN a
shape with `is_valid False` and a plausible volume, so a hand-written `draft()`
must check `is_valid` itself. Draft before you shell or fillet; when nothing
works `features.draft` returns the part unchanged and says so.

Read that table as a design constraint, not a bug report. The moulding rule of
thumb — 1° per side, 3° on a textured face — is *below* every ceiling above
except the shelled box and the enclosure base, and those two are exactly the
parts a real mould tool would be drafted on the sketch rather than by a draft
operation. The reliable pattern for a moulded part is to build the taper into
the profile (`extrude(taper=…)`, a lofted section, or `draft_deg=` on a rib)
and reserve `features.draft` for touching up a face you cannot reach that way.

`features.draft` returns `achieved_deg` for exactly this reason: it is what was
actually applied, and it can be less than you asked for or zero. Read it, and
if the part is user-facing, say so.

## Both a rib that misses and a boss in the air fuse happily

Both a rib that misses the part and a boss placed in the air FUSE HAPPILY:
volume rises by exactly the right amount, `is_valid` True, nothing raised. The
only tells are the solid count and an intersection probe, which is what the
helpers' warnings report.

So: read the warning, and back it with a spec.

```python
from agentcad.toolkit.specs import check_that, check_valid

SPECS = [
    check_valid(),
    check_that(lambda part, metrics: len(part.solids()) == 1,
               name="one_solid"),
]
```

## Sizing, in short

- **Rib thickness**: 0.5–0.6 × the wall it stiffens for injection moulding
  (thicker sinks the outer surface), and up to 1.0 × the wall for FDM, where
  there is no sink mark to worry about and the rib is only as strong as its
  perimeters.
- **Rib height**: up to ~3 × the wall. Beyond that the rib buckles before the
  wall yields, and two shorter ribs beat one tall one.
- **Rib spacing**: no closer than 2 × the wall, or the two heat-affected zones
  merge into one thick section.
- **Boss outer diameter**: ~2 × the screw's major diameter (`enclosures` has
  the table), wall 0.6 × the nominal wall, and never a solid post — a cored
  boss cools evenly and prints without a blob.
- **Boss to wall**: connect with a rib or a gusset rather than merging into the
  wall, so the thick junction does not sink.
- **Root fillet** on every rib and boss, `r ≈ 0.25–0.5 ×` the rib thickness.
  Add it *last*, after all the ribs exist, and use `safe_fillet`.

## Checklist

- [ ] The seat `Plane` is resolved once, before any loop that adds features.
- [ ] `to=` is an explicit millimetre value, or the `to="part"` warning was
      read and accepted.
- [ ] A boss that receives a screw uses `hole="M…"` so it carries a record.
- [ ] Draft angle is at or below the measured ceiling for a part of this
      shape, and `achieved_deg` was checked, not assumed.
- [ ] Draft runs before shell and fillet.
- [ ] The build reports one solid and `is_valid`, proving nothing fused into
      the air.

## Sources

- AgentCAD toolkit source: `agentcad/toolkit/features.py` — `rib`, `boss`,
  `draft`, the plane predicate, the binary search down on draft angle and the
  per-feature engagement warnings.
- AgentCAD toolkit source: `agentcad/toolkit/holes.py` — the record a bossed
  screw hole carries.
- AgentCAD toolkit source: `agentcad/toolkit/specs.py` — `check_valid` and
  `check_that`, the assertions that catch a silent fuse.
- build123d documentation, *Objects and operations* (`extrude(taper=…)`,
  `loft`, `fillet`): <https://build123d.readthedocs.io/>
- Open CASCADE Technology documentation, *Modeling Algorithms* —
  `BRepOffsetAPI_DraftAngle` and its failure behaviour.
- Injection-moulding rib and boss proportions: DuPont, *General Design
  Principles for DuPont Engineering Polymers*, Module I; G. Erhard,
  *Designing with Plastics* (Hanser, 2006).
