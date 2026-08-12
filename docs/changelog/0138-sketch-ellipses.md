# 0138 — PRD-009 slice 11: ellipses, elliptical arcs, and the tangency spike

- **Commit:** pending
- **Date:** 2026-08-12
- **Author:** Nikita Fedorov

## Summary

Ellipses and elliptical arcs in the solver, the emitter and the GUI — **with
tangency**, which the plan made conditional on a spike. The spike passed at
100%, so it shipped; a second, unplanned measurement found that the
constructor the plan's task list named is **broken in the pinned build123d**,
which changed the emitted call.

## The spike (design risk 7), and what it decided

Point-to-ellipse distance has no closed form, so tangency carries the tangency
point's **eccentric anomaly** as an auxiliary parameter and two residuals: the
touch point lies on the other curve, and the two tangents agree there. The
plan's rule was ≥ 90% convergence over 20 randomized starts, or ship ellipses
without tangency. Measured (`scratchpad/spike_ellipse_tangency.py`, re-run as
`tests/test_sketch_ellipses.py::test_the_tangency_spike_converges_from_twenty_randomized_starts`):

```
                       converged   geometric error       nfev   solve
line-ellipse             20/20     p50 9.25e-13 mm       48     3.8 ms  (max 5.58e-10)
circle-ellipse           20/20     p50 1.32e-10 mm      120     7.8 ms  (max 9.08e-10)
```

The error is **geometric** — the true minimum distance from the other curve to
the ellipse, sampled then minimized — not the residual, because a residual can
be small for a formulation that is measuring the wrong thing.

Two more things the spike settled, both of which contradicted a plausible
assumption:

- **The geometric seed is not what makes it feasible.** The auxiliary anomaly
  is seeded with the nearest point on the ellipse (a 72-sample scan plus a
  local refinement). Measured, from a fixed `t = 0` seed both pairings still
  converge **20/20**, and from a uniformly random anomaly 20/20 and 19/20. The
  seeding stays — it costs microseconds and buys the one failing case — but it
  is documented as conditioning, not as feasibility.
- **A curve-ellipse tangency is the most expensive constraint in the
  vocabulary** (120 nfev, 7.8 ms). Worth knowing before someone puts one on a
  drag path.

## The library bug the spike found

The plan's task list said to emit
`EllipticalCenterArc(center, x_radius, y_radius, start_angle, end_angle,
rotation=…)`, "verified present in the pinned build123d 0.11.1". Present, yes —
**and broken**: passing `end_angle` without `angular_direction` raises
`UnboundLocalError: cannot access local variable 'direction'`, because 0.11.1's
deprecation branch reads a name only the *other* deprecated parameter binds
(`objects_curve.py:1202`). The working spelling is `arc_size`, the signed
sweep — which is what the solver reports anyway.

The same measurement confirmed the parametrization the whole feature rests on:
`start_angle`/`arc_size` are the **eccentric anomaly**, agreeing with
`c + R(phi)(a cos t, b sin t)` to **8.9e-16 mm**, and `rotation` rotates about
the centre. So the solved angles *are* the emitted ones, with no conversion.

## Changes

- **`ellipse` entity** — centre point + `a`, `b`, `rotation` (3 parameters),
  plus `start_deg`/`end_deg` for a bounded elliptical arc (2 more). Angles are
  degrees in the spec, radians internally, and — like an arc's — **never
  wrapped mid-solve**, normalized on output only.
- **Handles, so the existing vocabulary reaches it.** `<name>.center`;
  `<name>.major` / `<name>.minor`, the ends of the two semi-axes at anomaly 0
  and 90°; `<name>.start` / `<name>.end` when bounded. They are ordinary
  `PointRef`s, so `distance`, `coincident`, `point_on_line`, `midpoint` and
  the rest pin an ellipse's size and orientation **without a single new
  constraint type**. The semi-axes are also scalar handles `<name>.a` /
  `<name>.b`, which `radius` and `equal_radius` take — an ellipse has two
  radii and neither is "the" radius, so it is named rather than implied
  (naming the ellipse itself raises and says which two names to write).
- **Two new reference classes**, both with analytic derivatives proven against
  a central difference: `_EllipsePoint` (the point at an anomaly — used for
  the bounded handles, the axis handles, *and* the auxiliary tangency point)
  and `_EllipseTangent` (its unit tangent, which depends on `a` and `b` as
  well as the angle — an ellipse's tangent is not perpendicular to its
  radius, which is the whole reason tangency needs a parameter).
- **No new residual kinds.** Elliptical tangency reuses `point_on_line`,
  `point_on_circle`, `tangent_dir` and `tangent_point_perp` — the last
  generalized from "the radius meets the line square" to "(point − centre) is
  perpendicular to *any* direction", which is what "the circle's centre lies
  on the ellipse's normal" is.
- **Tangency at a pinned junction needs no auxiliary parameter at all**: a
  bounded elliptical arc's handles enter the same coincidence union-find
  changelog 0137 added, and the direction residual applies with the handle's
  own anomaly.
- **`concentric` accepts ellipses**; `equal` (the GUI's) deliberately does not
  mix an ellipse with a circle, because it would have to guess a semi-axis.
- **`initial` grows an `ellipses` section** (`{a, b, rotation, start_deg?,
  end_deg?}`), all-or-nothing like every other section — the drag path depends
  on it, and slice 10 measured what a silently incomplete seed costs.
- **Emission**: a full ellipse becomes
  `Ellipse(x_radius=…, y_radius=…, rotation=…)` under `Locations`; a bounded
  one becomes `EllipticalCenterArc(centre, a, b, start_angle=…, arc_size=…,
  rotation=…)` as a chain member.
- **The closure gate now measures elliptical arcs unconditionally.** There is
  **no endpoint-anchored elliptical constructor** in build123d (`RadiusArc`
  has no counterpart; `EllipticalStartArc` anchors one end and derives the
  other), so an elliptical arc's endpoints are *always* derived by the reader
  from rounded literals — which is exactly the failure mode slice 7 measured
  for centre-parametrized arcs. Measured on a half-ellipse profile at 9
  decimals: **3.15e-10 mm**, inside the 1e-8 mm gate; at 4 decimals the same
  profile is refused.
- **GUI**: an `Ellipse` tool (centre → major-axis end → a point across the
  axis, the same three-click shape as `Arc`), SVG `A`-command rendering with
  the real radii and rotation (no polyline approximation), selectable axis and
  bound handles, `Rad` prompting for both semi-axes, and `Tan`/`Conc`
  extended. `entitiesSpec`, `seedFromModel`, `applySolution`, the delete
  cascade and the local DOF count all learned the new entity.

## Out of scope, deliberately

Tangency **between two ellipses** (an auxiliary anomaly on each curve; not
measured, so not shipped — it raises and says so), on-ellipse point
constraints, and parabolas/hyperbolas (a PRD non-goal). Each is stated in the
tool description rather than discovered.

## Files

- `agentcad/toolkit/sketch.py` — `_Ellipse`, `_EllipsePoint`,
  `_EllipseTangent`, `Sketch.ellipse`, `_radius_of`, `_center_of`,
  `_tangent_ellipse`, `_perp_to_direction`, `_seed_anomaly`, the `initial`
  section, the result payload
- `agentcad/core/sketch_emit.py` — `_ellipse_arc_call`, `_ellipse_point`, the
  unconditional derived-endpoint gap, the mapping table
- `agentcad/core/tools_sketch.py` — the `_ELLIPSES` description, the schema
- `frontend/js/sketcher.js` — the ellipse tool, rendering, handles, palette
- `tests/test_sketch_ellipses.py` — **new** (30 tests: the spike, derivative
  coverage, the parametrization against the kernel, emission round trips)
- `docs/agent-api.md` — the ellipse paragraph in the `solve_sketch` row
- `docs/changelog/0138-sketch-ellipses.md` — this entry

## Verification

```
uv run pytest -q tests/test_sketch_ellipses.py                  30 passed
uv run pytest -q tests/test_sketch_*.py                        241 passed
make test-fast (-m "not slow")                        1381 passed, 1 skipped
```

**Real browser** (headless Chrome for Testing via Playwright, SwiftShader
WebGL, scratch server on port 52411 with a scratch projects dir — the user's
8630 was never touched, and the server was stopped afterwards). Every flow
driven through the real pointer handlers:

```
ellipse drawn (3 clicks)                       3 DOF
second ellipse                                 8 DOF
two ellipses selected      Conc enabled, Tan/Rad/Eq disabled (as designed)
after Conc                 chip 'conc e1,e2'                     6 DOF
one ellipse selected       Rad enabled
after Rad (24, 9)          chips 'rad e1.a = 24', 'rad e1.b = 9' 4 DOF
line + ellipse selected    Tan enabled
after Tan                  chip 'tan ln1,e1'                     7 DOF

CONSOLE ERRORS: NONE
```

Screenshots: `s11-a-ellipses.png`, `s11-b-radius.png`, `s11-c-tangent.png`.

`make test` in chunks (this sandbox caps a foreground command at 600 s) →
**1707 passed, 1 skipped** against the 1646/1 baseline; the per-chunk table is
in `docs/changelog/0139-sketch-on-face.md`, which lands with this one.

## Notes

- **`radius {c: "e1.a"}` is not a workaround, it is the honest surface.** The
  alternative — inventing `ellipse_radius` or making `radius` guess — either
  multiplies constraint types or hides a choice the user has to make anyway.
- **The GUI's local DOF count was wrong for every fixed entity, not just for
  ellipses.** `freeParamCount` counted every circle and arc as free; it now
  counts what the solver allocates (skipping `fixed_r` circles and wholly
  fixed arcs). Found because an unconstrained ellipse read "fully
  constrained".
- **The auxiliary anomaly is named `<ellipse>.tangency`** in `slot_owner`, so
  if it ever surfaces in `free_entities` a reader can tell it is machinery
  rather than something they wrote.
