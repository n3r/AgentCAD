---
name: sketch-solver
description: The 2D constraint sketch solver - points, lines, arcs, ellipses, splines and slots under geometric constraints, reading dof and diagnostics, and emitting build123d source for the solved profile.
triggers: [sketch, constraint, solver, solve_sketch, tangent, coincident, dof, degrees of freedom, over-constrained, profile, 2d, geometry, dimension, slot, arc, spline, emit, buildline]
version: 1.0.0
license: Apache-2.0
author: AgentCAD core
requires: []
---

Some profiles are easier to *state* than to compute: a link arm whose two bores
are 84 mm apart with a tangent flank, a cam whose radius follows from three
tangencies. Writing those coordinates by hand means solving trigonometry that
changes every time a parameter moves. The sketch solver takes the constraints
instead and returns the coordinates — and, if you want, the build123d source
for the solved profile. Use it whenever a profile's numbers are *derived* from
tangency, symmetry or distance relationships rather than typed in. Do not use
it for a rectangle, a bolt circle or anything you can write down directly:
`Polyline`, `patterns.bolt_circle` and plain arithmetic are faster to read and
have no solver to under-constrain. It is a 2D solver — it produces a profile,
not a solid.

## The JSON front end

A first-party 2D constraint solver (scipy least-squares, ms-scale, machine
precision). Use it to COMPUTE exact coordinates from geometric constraints; it
can also EMIT the build123d source for the solved profile. Tool: `solve_sketch`.

```python
from agentcad.toolkit.sketch import solve_sketch
r = solve_sketch({
    "points":  [{"name": "a", "x": 0, "y": 0, "fixed": True},
                {"name": "b", "x": 30, "y": 5}],
    "lines":   [{"name": "ab", "p1": "a", "p2": "b"}],
    "circles": [{"name": "c", "center": "b", "r": 4}],
    "constraints": [
        {"type": "distance", "p": "a", "q": "b", "d": 32},
        {"type": "horizontal", "ln": "ab"},
        {"type": "radius", "c": "c", "r": 5},
    ],
})
# r -> {"ok", "max_residual", "rank", "dof", "n_residuals", "diagnostics",
#       "points": {"b": {"x": .., "y": ..}}, "circles": {"c": {"cx","cy","r"}}}
```

An object API exists too: `sk = sketch.Sketch(); sk.point(...);
sk.distance(...); sk.solve()`.

## Entities

Points, lines, circles, plus:

```
arcs     {name, center, r, start_deg, end_deg} or {name, start, mid, end}
ellipses {name, center, a, b, rotation} (+ start_deg/end_deg = elliptical arc)
splines  {name, points: [<point names>]}          (degree 3, through-points)
slots    {name, c1, c2, width}                    (compiled: 2 arcs + 2 lines)
```

Any entity may carry `"construction": True` — it constrains but never emits.
VIRTUAL HANDLES cost nothing and take the whole point vocabulary:
`<arc>.start`, `<arc>.end`, `<ellipse>.center/.major/.minor/.start/.end`,
`<spline>.start/.end`, `<slot>.arc_a/.side_1...`, plus the scalar handles
`<ellipse>.a` / `<ellipse>.b` for `radius`/`equal_radius` (an ellipse has two
radii; name the one you mean).

Construction geometry is the technique that makes hard sketches easy: a
construction line between two bore centres, then everything dimensioned off
*it*, so the profile follows when the centres move.

## Constraint types

`fixed`, `coincident`, `distance`, `distance_x`, `distance_y`, `horizontal`,
`vertical`, `parallel`, `perpendicular`, `angle(l1,l2,deg)`, `point_on_line`,
`point_on_circle`, `radius`, `equal_radius`, `midpoint`,
`tangent_line_circle(ln,c,at=None)`, `tangent_circles(c1,c2,kind="external")`,
`tangent(a,b,at?,kind?)`, `symmetric(a,b,about)`, `equal_length(l1,l2)`,
`concentric(a,b)`.

## Reading the result

`ok=False` or a large `max_residual` means the system is unsatisfiable;
`"diagnostics"` carries `status`/`dof`/`rank`/`free_entities` and, when
over-constrained, `redundant`/`conflicting` naming the dependent constraints by
their index (a redundant-but-consistent one is NOT an error; a conflicting one
raises). `dof = n_params - rank(J)` and is never negative; `dof>0` means
UNDER-constrained (the answer is not unique). Give points a good initial
`(x, y)`, or pass `"initial"` — it selects the solution BRANCH
(tangent/mirror problems have several), it is not the speed knob.

That last point is the one that costs the most time. A tangency has two
answers, a mirror has two, a circle-circle tangency has four; the solver
returns the one *nearest the initial guess*. If the profile comes back mirrored
or the arc bulges the wrong way, the constraints are right and the starting
point is wrong. Move the initial position to roughly where you want the answer
and re-solve — do not add constraints to fight it.

`dof > 0` on a profile you meant to be fully determined means a dimension is
missing, and the solved coordinates are one arbitrary member of a family. That
is a bug even when the picture looks right, because the next parameter change
can land on a different member.

## Emitting build123d source

`"emit": "function"|"buildline"` returns `{code, warnings}` — the same emitter
the GUI uses, at 9 decimals behind a 1e-8 mm closure gate. Add
`"persist": "<name>"` and the code is wrapped in a round-trip block carrying
the spec and a hash, so the sketch can be reopened; `"plane": {origin, x_dir,
normal, ...}` from the `sketch_plane` tool emits onto a picked face's plane.

The closure gate is worth knowing about: a profile whose ends miss each other
by more than 1e-8 mm is not closed, `make_face()` will refuse it, and the
emitter tells you *there* rather than letting the failure surface as an
unhelpful build123d error three steps later.

## Traps

- The solver produces **numbers**, not a solid. Feed the coordinates into a
  `BuildLine`/`make_face`/`extrude` chain as usual.
- Under-constrained is silent unless you read `dof`. Read `dof`.
- A redundant constraint is fine; a conflicting one raises. The diagnostics
  name the indices, so quote them rather than bisecting by hand.
- `construction: True` entities constrain and do not emit — use them freely,
  they cost nothing in the output.
- Scipy least-squares is millisecond-scale for sketches of this size; if a
  solve is slow, the sketch is probably enormous or the initials are far away,
  not the solver.
- An emitted profile is a *snapshot*. If the sketch is parametric, re-solve in
  `build(p)` from the parameters rather than pasting yesterday's coordinates.

## Checklist

- [ ] The profile genuinely has derived numbers; a directly-writable profile
      skips the solver.
- [ ] Every point has a plausible initial `(x, y)` in the branch you want.
- [ ] `ok` is True, `max_residual` is tiny, and `dof == 0` for a determined
      profile.
- [ ] Diagnostics checked for `conflicting` before adding more constraints.
- [ ] Construction geometry carries the relationships; dimensions hang off it.
- [ ] The emitted code is regenerated from parameters on every rebuild, not
      frozen into the script.

## Sources

- AgentCAD toolkit source: `agentcad/toolkit/sketch.py` — the entity and
  constraint vocabulary, the virtual handles, the residual/rank/dof
  diagnostics and the `Sketch` object API.
- AgentCAD source: `agentcad/core/sketch_emit.py` — the build123d emitter, the
  9-decimal rounding and the 1e-8 mm closure gate.
- SciPy documentation, `scipy.optimize.least_squares` (the Trust Region
  Reflective solver behind it): <https://docs.scipy.org/>
- build123d documentation, *Builder API* (`BuildLine`, `BuildSketch`,
  `make_face`): <https://build123d.readthedocs.io/>
