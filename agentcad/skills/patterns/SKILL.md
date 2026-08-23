---
name: patterns
description: Repeating features with agentcad.toolkit.patterns - bolt circles and grids as point sets, linear/polar/mirror shape patterns, the seed-counting convention, and why every helper returns a warning.
triggers: [pattern, array, repeat, bolt circle, grid, linear pattern, polar, circular pattern, mirror, instances, count, spacing, locations, seed, copies]
version: 1.0.0
license: Apache-2.0
author: AgentCAD core
requires: []
---

Repeating a feature is two entirely different operations that look the same on
a drawing. Placing eight holes on a circle is *arithmetic* — you want the
coordinates, and then one hole call with all eight points. Copying a rib that
is already fused into the part is a *shape* operation — you want OCCT to
transform and re-fuse a solid seven more times. `agentcad.toolkit.patterns`
covers both, and the traps that follow are almost all about confusing them, or
about a copy that landed in the air and fused anyway. Use this skill whenever a
part has a bolt circle, a grid of holes, a row of ribs, a ring of lugs, or a
mirrored feature. For the hole geometry itself — clearance, tapped, counterbore
and the records they carry — use `holes`; this skill supplies the points it
takes.

## Two kinds of thing

`from agentcad.toolkit import patterns`

POINT SETS are pure arithmetic and are what you want for holes; SHAPE PATTERNS
copy a solid that is already fused into the part.

```
patterns.bolt_circle(r, n, start_deg=0.0)   -> [(x, y), ...]
patterns.grid(nx, ny, dx, dy, center=True)  -> [(x, y), ...] row-major
    # rounded to 9 decimals so trig noise never reaches a coordinate.
    # NOT PolarLocations: these translate, that also ROTATES each copy,
    # and the two tessellate differently. Build a rotated group in its
    # own frame and rotate the result, or the rounding bites.
```

```
part, warn = patterns.linear(part, seed, direction, count, spacing)
part, warn = patterns.polar(part, seed, axis=Axis.Z, count=4,
                            radius=None, span_deg=360.0)
part, warn = patterns.mirror(part, plane=Plane.YZ, seed=None)
    # `seed` is a Shape ALREADY in the part, so `count` is the TOTAL
    # instance count (CAD convention) and the helper adds 1..count-1;
    # count=1 is a no-op with a warning. span_deg < 360 is inclusive.
    # THAT HOLDS ONLY WHERE A PLACEMENT LEAVES THE SEED WHERE IT IS, which
    # is a transform the helper tests for and never an index:
    # polar(..., radius=r>0) translates EVERY instance onto the circle, so
    # none of them is the seed -- all `count` are added, the seed stays
    # where you built it as an extra feature, and the helper says so. For
    # exactly `count` on a circle, build the seed where the first instance
    # goes and pass radius=None.
```

## Why every helper returns a warning

OCCT does not fail on a badly placed feature. A cut placed entirely off the
part succeeds in ~1 ms, leaves the volume EXACTLY unchanged and reports
`is_valid True`. So the helpers measure engagement themselves, per instance,
and the warnings name INSTANCE INDICES. Read `patterns.instances(part)` for the
per-instance report. Impossible arguments (count < 1, spacing <= 0, span
outside (0, 360]) RAISE.

The practical consequence: a green build is not evidence that a pattern
landed. The three things that are — the warning, `patterns.instances(part)`,
and the solid count — cost nothing to check.

```python
from agentcad.toolkit import patterns

def build(p):
    part = _plate(p)
    part, warn = patterns.polar(part, seed=_lug(p), axis=Axis.Z,
                                count=int(p.lugs), radius=None)
    report = patterns.instances(part)
    return part
```

## The seed convention, in one paragraph

Every CAD system counts a pattern's instances *including* the original, and so
does this toolkit: `count=4` means four lugs exist afterwards, not five. The
helper therefore adds `count - 1` copies — **when the transform leaves the seed
in place**. A polar pattern about the seed's own axis does; a polar pattern
with `radius=r > 0` does **not**, because it translates every instance out onto
the circle including the first, so all `count` are added and the seed you built
stays where it was as an extra feature. The helper tests the transform (it
never guesses by index) and says so in the warning. If you want exactly `count`
features on a circle: build the seed at the position of the first instance and
pass `radius=None`.

## Choosing between point sets and shape patterns

- **Holes: always the point set.** `holes.clearance(part, patterns.bolt_circle(45, 8), "M5")`
  is one group record covering eight instances, one verification pass, and one
  drawing callout (`8x ...`). Eight separate calls are eight records.
- **A feature already fused: the shape pattern.** Ribs, lugs, bosses, cooling
  fins — build one, prove it, then repeat it.
- **A feature not yet fused: `Locations`.** Inside `BuildPart`,
  `with Locations(*patterns.grid(4, 3, 10, 10)):` places each object as it is
  created, which is cheaper than fusing and then copying.
- **`GridLocations`/`PolarLocations` are build123d's own**, and they *rotate*
  each copy as well as translating it. That is what you want for a ring of
  radial ribs and exactly what you do not want for a ring of round holes,
  where the rotation is invisible but the tessellation is not.

## Traps

- `patterns.grid` and `bolt_circle` round to 9 decimals so trig noise never
  reaches a coordinate; build a rotated group in its own frame and rotate the
  *result*, otherwise you rotate already-rounded numbers and the rounding
  bites.
- `span_deg < 360` is **inclusive** of both ends: `count=5, span_deg=180` puts
  instances at 0, 45, 90, 135 and 180 degrees. A full 360 does not double the
  seed.
- `count=1` is a no-op with a warning, not an error — it is what a parameter
  at its minimum produces, and it must not turn a build red.
- A patterned cut that leaves the part is *dropped* from the count, not
  silently succeeded; read the indices in the warning before assuming the
  numbers are the numbers.
- Booleans do not preserve attached records. If you pattern a wizard hole's
  solid with a raw build123d op, the hole records go with the old shape — see
  `holes.carry(new, old)` in the `holes` skill.

## Checklist

- [ ] Holes take a point set; already-fused solids take a shape pattern.
- [ ] `count` is the total, and the `radius=None` rule is honoured for polar.
- [ ] The helper's warning is read, and `patterns.instances(part)` inspected
      whenever a copy might miss the part.
- [ ] `count=1` and `count` at its parameter maximum both build green.
- [ ] The final solid count is what the design says it is.

## Sources

- AgentCAD toolkit source: `agentcad/toolkit/patterns.py` — `bolt_circle`,
  `grid`, `linear`, `polar`, `mirror`, `instances`, the seed-transform test
  and the per-instance engagement measurement.
- AgentCAD toolkit source: `agentcad/toolkit/holes.py` — the point-set
  consumer, and why one call over N points is one record.
- build123d documentation, *Location contexts* (`Locations`, `GridLocations`,
  `PolarLocations`) and *Objects and operations*:
  <https://build123d.readthedocs.io/>
