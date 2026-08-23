---
name: robust-parametrics
description: safe_fillet, safe_shell and safe_bool - the toolkit helpers that survive OCCT's sharp edges - plus the guards that keep a parametric script building across its whole parameter range.
triggers: [safe_fillet, safe_shell, safe_bool, robust, robustness, parametric, clamp, fillet fails, shell fails, boolean fails, fuzzy, warning, guard, derived dimension, min max, range, toolkit]
version: 1.0.0
license: Apache-2.0
author: AgentCAD core
requires: []
---

A parametric part is not one part: it is every part its PARAMS can produce. A
script that builds at the defaults and dies at `thickness = 0.5` is broken, and
the failure is almost always OCCT refusing an operation the *numbers* made
impossible — a fillet bigger than its edge, a shell thicker than its wall, a
fuse across a sub-tolerance gap. This skill covers the three `agentcad.toolkit`
helpers that degrade instead of raising, and the guard patterns that stop the
numbers going bad in the first place. Use it whenever a dimension is derived
from another dimension, whenever a radius or a wall comes off a slider, and any
time a build is green at the default and red somewhere else in the range. It is
not the selector guide (`selectors-and-occt-failures` — which edge, and what
OCCT's error text means), and it is not a substitute for `design-specs`, which
is how you assert that the geometry you got is the geometry you meant.

## The robustness toolkit

`from agentcad.toolkit import safe_fillet, safe_shell, safe_bool`

The blessed way to write parts that survive OCCT's sharp edges. Each returns a
tuple ending in a warning (None when nothing went wrong) — read it and, if the
part is user-facing, surface it. Importing these needs the agentcad package
(part scripts run in the app venv, so it is available; plain build123d scripts
that must stay portable should not import them).

```
part, r, warn = safe_fillet(part, edges, radius, *, min_radius=0.05)
    # fillets at `radius`; on OCCT failure binary-searches DOWN to the
    # largest radius that works (uses .max_fillet as a hint). `r` is what
    # was actually applied; if even min_radius fails you get the part back
    # unchanged with a warning. Use instead of bare fillet() on any radius
    # that might be too big for the edge.

part, warn = safe_shell(part, thickness, opening_faces=None, *, kind=Kind.ARC)
    # hollows the solid, opening `opening_faces` (a list of faces). Falls
    # back through Kind.INTERSECTION, then fewer opened faces, then an
    # APPROXIMATE boolean-subtract shell. That last fallback is NOT uniform
    # on curved/slanted walls (can be ~20% thin on dome mid-sections) --
    # the warning says so; pass it on. Raises only if every strategy fails.

shape, warn = safe_bool(a, b, op="fuse"|"cut"|"common", *, fuzzy=1e-4)
    # boolean with automatic fuzzy-tolerance escalation for faces that
    # should touch but sit a sub-tolerance gap apart (the classic "fuse
    # leaves two disjoint solids" / invalid-cut failure). Tries the plain
    # operator, then raw OCCT at fuzzy and 10x fuzzy. Raises if all fail.
```

The warning is the product, not the noise. `safe_fillet` returning `r` smaller
than the radius you asked for means the part on screen is not the part the
parameter describes — a user who dragged the slider to 8 mm and got 3.1 mm has
to be told. Passing the warning up is one line:

```python
def build(p):
    warnings = []
    part = _rough_shape(p)
    part, radius, warn = safe_fillet(part, part.edges().filter_by(Axis.Z),
                                     p.corner_r)
    if warn:
        warnings.append(warn)
    return part
```

## Parametric guards

The helpers catch what OCCT refuses. These patterns stop the parameters from
asking for it.

**Clamp every derived dimension, at the point of use.** `min`/`max` in PARAMS
bound each parameter *independently*; nothing there knows that a corner radius
must fit inside half the smaller side, or that a wall must fit inside the box.
Derive and clamp in `build`:

```python
def build(p):
    radius = min(p.corner_r, min(p.length, p.width) / 2 - 0.1)
    wall = min(p.wall, min(p.length, p.width, p.height) / 2 - 0.2)
    boss_h = max(0.0, min(p.boss_h, p.height - p.floor))
    ...
```

The `- 0.1` is not superstition: a fillet radius at *exactly* half the side
produces a zero-length remaining edge, which is the degenerate case OCCT
either refuses or turns into an invalid face. Leave a sliver.

**State the relationship in the description.** PARAMS descriptions are the only
place a reader (human or agent) learns that two sliders are coupled, and the
clamp warning quotes nothing. Write the relationship down:

```python
PARAMS = {
    "width":    {"default": 60.0, "min": 10.0, "max": 300.0, "unit": "mm",
                 "description": "Outer width (X)"},
    "wall":     {"default": 2.4,  "min": 0.8,  "max": 10.0,  "unit": "mm",
                 "description": "Wall thickness; clamped to width/2 - 0.2"},
    "corner_r": {"default": 5.0,  "min": 0.0,  "max": 40.0,  "unit": "mm",
                 "description": "Corner radius; clamped to min(w, d)/2 - 0.1"},
}
```

**Order fillets and chamfers last.** Every fillet changes the topology the next
selector sees, and a filleted edge is no longer selectable by the axis it used
to be parallel to. Build all the material, subtract all the cuts, then round.
The one exception is a fillet that a later boolean depends on (a rib blending
into a wall before the wall is shelled) — and then shell *before* the fillet,
because `offset` on a filleted solid is the classic "Failed to offset" case.

**Prefer `safe_*` on any parameter-driven edge.** A bare `fillet()` on a radius
that came from a slider is a build that will go red for somebody. Reach for
`fillet()` only where the radius is a constant you chose against a dimension
you also chose.

**Guard zero and negative.** `if p.corner_r > 0:` before a fillet, `if p.count
>= 1:` before a pattern, `max(0.0, …)` on any subtraction of two parameters.
A zero-radius fillet and a zero-height extrude both raise, and the traceback
names build123d's internals, not your line.

**Prove the result, do not assume it.** OCCT does not fail on a badly placed
feature: a cut entirely off the part succeeds in ~1 ms, leaves the volume
exactly unchanged and reports `is_valid True`. The counter-measure is a spec
(`design-specs`) or an explicit assertion:

```python
from agentcad.toolkit.specs import check_that, check_valid

SPECS = [
    check_valid(),
    check_that(lambda part, metrics: len(part.solids()) == 1,
               name="one_solid"),
]
```

`n_solids` in the build metrics is the same evidence: a feature that missed
the part fuses as a *second* solid and every other number looks right.

## When a helper is the wrong answer

- If `safe_fillet` routinely searches down, the radius is wrong, not the
  operation. Fix the parameter's `max` or the clamp — shipping a part whose
  radius silently differs from its parameter is worse than a red build.
- If `safe_shell` falls back to the approximate boolean subtract on a curved
  wall, treat the wall thickness as a nominal, not a guarantee; the warning
  says ~20 % thin on dome mid-sections and a `check_wall` spec will find it.
- If `safe_bool` needs 10× fuzzy to fuse two faces, the two features are not
  actually touching. Overlap them by 0.5–1 mm instead: an overlap always
  fuses, two exactly coplanar faces are a degenerate boolean
  (`selectors-and-occt-failures`).

## Checklist

- [ ] Every derived dimension is clamped at the point of use, with a sliver.
- [ ] Every coupled parameter says so in its `description`.
- [ ] Fillets and chamfers are the last operations; shell precedes fillet.
- [ ] Any radius, wall or thickness that came from PARAMS uses a `safe_*`
      helper, and its warning is collected.
- [ ] Zero/negative guards on radius, count, height and every difference.
- [ ] A `check_valid()` and a solid-count assertion prove the build.
- [ ] The script was built at the minimum and the maximum of its two most
      coupled parameters, not only at the defaults.

## Sources

- AgentCAD toolkit source: `agentcad/toolkit/fillet.py` (`safe_fillet`, the
  downward radius search and the `max_fillet` hint).
- AgentCAD toolkit source: `agentcad/toolkit/shell.py` (`safe_shell`, the
  `Kind.ARC` → `Kind.INTERSECTION` → fewer-faces → boolean-subtract fallback
  ladder and the non-uniform-wall warning).
- AgentCAD toolkit source: `agentcad/toolkit/boolean.py` (`safe_bool` and the
  fuzzy-tolerance escalation).
- AgentCAD toolkit source: `agentcad/toolkit/specs.py` (`check_valid`,
  `check_that` — the assertions that make a silent no-op visible).
- build123d documentation, *Builder API* and *Objects and operations*:
  <https://build123d.readthedocs.io/> — `fillet`, `chamfer`, `offset` and the
  operation ordering rules the guards above work around.
- Open CASCADE Technology documentation, *Boolean Operations* and *Modeling
  Algorithms* (fuzzy Boolean tolerance, the degenerate-coplanar case).
