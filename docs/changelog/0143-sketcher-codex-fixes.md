# 0143 — PRD-009 review 2: the tangency junction criterion stops being a list, and eleven more

- **Commit:** pending
- **Date:** 2026-08-13
- **Author:** Nikita Fedorov

## Summary

A second independent review (Codex, xhigh) of the PRD-009 branch returned
CHANGES-REQUIRED with fifteen findings against commit `0aad5a6` — i.e. against
the branch *before* changelog 0142's fixes. Twelve were still open on `3f8c5e4`
and are fixed here; two were closed by 0142 and are recorded as such with the
evidence; one (0142's part-switch fix) was half closed and its other half —
the asynchronous half — is fixed here.

The load-bearing one is the **fourth** instance of the tangency degeneracy.
0142 claimed to have fixed it "as a class" and had not: it replaced one
enumeration (entity handles) with another (constraint kinds), and the review
walked straight past it with two dimensional constraints. This entry replaces
the enumeration with a **criterion derived from the Jacobian**, which is the
thing that ends the recurrence.

## P1 — the junction criterion, and why this is the last time

**The property, restated.** Tangency's distance forms (`dist(centre, line) - r`,
`d(c1,c2) - (r1 ± r2)`) sit at an extremum of the manifold the *other*
constraints cut out whenever those constraints already hold a point on both
curves. A residual at an extremum has its gradient inside the span of the rows
that pin it: the row adds no rank, reports itself `redundant` while removing a
real DOF, and its value is the **square** of the geometric error.

**The four instances, and what each fix keyed off.**

| # | how the junction was written | the detector that missed it |
|---|---|---|
| 1 (0132) | a line built on `arc1.end` | — (the bug) |
| 2 (0137) | `coincident {p: "j", q: "arc1.end"}` | a list of entity handles |
| 3 (0142) | `point_on_circle {p, c}` | a union-find over `coincident` |
| 4 (**here**) | `distance_x(p, a1.start, 0)` + `distance_y(...)` | a table of constraint **kinds** (`ON_CURVE_ARGS`) |

Measured on instance 4 at the exact solution, before → after:

```
kinds     [distance_x, distance_y, tangent_line_circle] → [..., tangent_dir]
svals     10.05  1.005  1.61e-16   →   1.142  1.000  0.834
rank      2 of 3                   →   3 of 3
dof       7 (true 6)               →   6
status    over_constrained         →   under_constrained
redundant [tangent]                →   []
```

**The new criterion** (`Sketch.resolve_tangencies`). Let `R` be every residual
row *except* the tangency rows being decided, and let `x*` be a configuration
`R` solves (the seed is projected onto `R`'s own manifold first, so a rough
input is not mistaken for a rough sketch). A point handle `h` is **held on**
curve `c` when, at `x*`:

1. `phi_c(h) = 0`, where `phi` is the curve's own on-curve function
   (`|h − centre| − r`, or `cross(h − a, u_line)`), **and**
2. `grad phi_c(h)` lies in the **row space of `R`**.

Two curves share a junction when some handle is held on both. That is exactly
the condition under which the distance form is at an extremum, and **no
constraint kind appears anywhere in it** — any combination of rows that removes
both DOF of the junction, however spelled, puts `grad phi` in the row space,
because the direction it would have to move along is in `R`'s null space. A
structural handle passes trivially: `phi_arc(a1.start)` is identically zero
with an identically zero gradient.

Condition (1) is not decoration. `distance_x(p, a1.start, **5**)` pins the
offset exactly as firmly, to a point that is *not* on the arc; that is an
ordinary tangency and keeps the distance residual
(`test_a_pinned_but_offset_junction_is_not_a_junction`).

**Cost.** The symbolic detector (0142's, unchanged) still runs first, so every
sketch it already resolved pays nothing: the slot ring and the 50-segment
staircase never reach the new pass. Measured `tests/test_sketch_bench.py` p50
over three runs, against 0142's numbers:

```
                 0142      now
cam lobe         0.49 ms   0.54 - 0.61 ms
staircase 50     6.90 ms   6.62 - 7.10 ms
arc ring + slot  11.15 ms  10.89 - 11.78 ms   (FR6 budget 16 ms)
```

The spread is run-to-run noise on a loaded machine, which is exactly why the
FR6 assertion is on the *fastest* frame with a ceiling on the median (0142's
P15). Two things keep the pass cheap where it *is* reached: the value half of
the criterion runs first (one `hypot` per candidate handle, and it rejects
almost all of them before any projection is computed), and the row-space basis
is computed **once** for the whole sketch rather than per tangency.

Two numbers that had to be measured rather than guessed, both recorded because
the next person will hit them:

- **The projection is not fast.** An under-determined `trf` solve of the
  non-tangency rows, seeded 60 mm from the junction, needed **137** residual
  evaluations; at a 60-evaluation budget it stopped 21.8 mm short and the
  junction was missed for a purely numerical reason. `JUNCTION_PROJECT_NFEV`
  is 500.
- **The row-space test needs an absolute floor.** A *structural* incidence
  (`a1.start` on `a1`; a line's own endpoint on that line) has an analytically
  zero gradient and a numerically ~1e-16 one, and a purely relative test reads
  that dust as a full-size vector pointing somewhere random. Both on-curve
  functions are built from unit vectors, so a gradient that constrains anything
  has norm ~1 and the floor is 1. Without it the same junction was found at
  0 mm and 0.5 mm, missed at 3 mm and found again at 12 mm.

**Scope, stated rather than implied.** Ellipses and splines have no
closed-form on-curve function here, so they keep the symbolic detector. An
elliptical tangency's fallback is the auxiliary-anomaly form, which is already
a pair of *direction* residuals and was never in this class — a missed junction
there costs one parameter, not a wrong answer. And the criterion is evaluated
at a projection of the seed, so a spec whose coordinates are far from any
solution can still fall back to the distance form; the symbolic path covers
every idiomatic spelling seed-independently.

`CLASS_SPECS` in `tests/test_sketch_tangent_direction.py` grows from six
configurations to nine — the two dimensional spellings and the "on the circle
by `distance` + `radius`" one, which the incidence table also cannot see — and
`test_the_junction_criterion_is_the_jacobian_not_a_list_of_kinds` asserts the
property over all nine.

## P2 — internal tangency was operand-order dependent (C1)

`d − (r1 − r2)` instead of `d − |r1 − r2|`. Two internally tangent fixed
circles, r=10 and r=5, centres 5 apart:

```
tangent(big, small, internal)   ok: true   max_residual 0
tangent(small, big, internal)   ok: false  max_residual 10   conflicting
```

Which circle is inside which is a property of the pair, not of the argument
order. The residual and its derivative both changed. `|·|` has a kink at
`r1 == r2` (two internally tangent circles of equal radius are the *same*
circle): **the convention is `sgn(0) = 0`**, the minimum-norm element of the
Clarke subdifferential — and it is also the value a central difference
straddling the kink returns, so the derivative gate agrees with it rather than
having to avoid it. `test_equal_radii_are_the_kink_of_the_absolute_value`
asserts both halves at once.

## P3 — a length is not a direction (C2)

`_unit()` returns a zero direction for a degenerate segment. That is **right**
for `parallel`, `perpendicular`, `angle` and `point_on_line` — they are
functions of a *direction*, and a zero-length segment has none; inventing one
would make a `parallel` on a collapsed line unsatisfiable rather than vacuous.
It is wrong for `distance`, `point_on_circle`, `equal_length` and the
circle-circle tangency, which are functions of `|b − a|`: there the zero vector
is the one subgradient that makes the residual's **whole Jacobian row vanish**.

```
fixed a=(0,0), free b, distance(a, b, 0)
before   ok: true   rank 0   dof 2   redundant [distance]
after    ok: true   rank 2   dof 0   well_constrained
```

Two changes, and they answer different halves:

- `_norm_dir()` is the norm family's helper, with the documented **+x unit
  subgradient** at a coincidence. It is an honest element of the
  subdifferential, it is deterministic, and it stops a degenerate *seed* from
  producing a row the solver cannot move along. The residual **value** is
  untouched — only `df` reads a direction — so `max_residual` still measures
  the same millimetres.
- `distance(p, q, 0)` compiles to the **coincidence rows**. One subgradient can
  only ever remove one of the two degrees of freedom the geometry removes, so
  no convention fixes the rank; the two linear rows are the same solution set
  exactly. The constraint is still blamed as the `distance` the caller wrote.

## P4 — the drag frame that poisoned a later verdict (C10)

The diagnostics cache key is the residual *structure*, deliberately: the GUI
resends the whole spec every frame with its points at the last solution, so a
coordinate key would miss every time. But the rank of a nonlinear Jacobian is a
function of the configuration too, and the cache was serving the whole verdict.

```
parallel(l1, l2), four free points, identical structure key
drag frame, both lines collapsed      rank 0  dof 8  over_constrained   (cached)
later solve, two real lines, "cached" rank 0  dof 8  over_constrained   ← stale
                                 full rank 1  dof 7  under_constrained
```

The cache now holds the **greedy dependent-row set and the SVD rank it was
found at** — nothing else. `analyze` recomputes the rank on every frame and
reuses the cached set only when it matches; `status`, `dof`, `free_entities`
and the redundant/conflicting split are computed from *this* frame's Jacobian
and residuals either way. `diagnostics_source: "cached"` now means the greedy
pass was reused, not the answer. The rank is the cheap half — the greedy pass is
what the cache exists for — and the measurement holds: 156 rows, 3.32 ms/frame
full against 2.54 ms/frame cached.

## P5 — `initial` could collapse a slot (C5)

`slot()` rejects two coincident centres at declaration; `initial` runs
afterwards and `_reseed_slots()` did not look. A width-10 slot declared at
`(0,0)`/`(20,0)` and warm-started with both centres at `(0,0)` returned
`ok: true`, `rank 1`, `dof 8`, and whatever it emitted was not a slot. The seed
is now checked by the same rule the declaration is (`SLOT_MIN_SPAN_MM`), plus a
positive-radius check on the seeded radius.

## P6 — the emitter silently lost geometry (C7)

Every chain went into **one** `BuildLine` followed by **one** `make_face()` if
any chain closed — and `make_face()` consumes every pending edge in its builder
to make one face. Two disjoint 1×1 squares emitted syntactically valid code
carrying both polylines and rebuilt, through the real kernel, to **one face of
area 1** instead of two totalling 2. A closed square next to an open chain lost
the open one.

A chain is the unit of connectivity, so it is now the unit of emission: one
`BuildLine` per chain, `make_face()` after each closed one. Measured through
the kernel: `volume_mm3` 2.0 for the two squares (extruded 1 mm), 1.0 for the
mixed case.

Found while fixing it, and **reported rather than refused**: a sketch that
closes *nothing* produces a `BuildSketch` with no face, and build123d raises at
the block's own exit (`Unable to repositioned type <class 'NoneType'>`).
Drawing an open chain and pressing Insert is a legitimate half-finished state,
so it emits with a `no_closed_profile` warning naming the consequence.

## P7 — the arc constructors have a domain (C8)

The closure gate measures how far a junction's shared literal moved the
endpoints it stands for. It never asks whether the call the emitter just wrote
is one build123d will accept, and two ends of the sweep range are not:

- a **zero** sweep became `RadiusArc(v0, v0, r, …)` — the same vertex twice —
  and OCCT raises `Standard_ConstructionError`;
- **every** `|sweep| > 360` was classified as a full turn and passed verbatim
  to `CenterArc(…, 450.0)`, which raises `ValueError`. (As-built divergence 8
  said `CenterArc` survives "for a full turn only". That was the intent, not
  the code.)

Both are refused with a message naming the sweep. Exactly one turn still emits
`CenterArc` with its `arc_full_turn` warning, and
`test_every_emittable_sweep_rebuilds` walks 1°, 90°, 180°, 270°, 359°, 360°,
−90° and −359° through the real kernel.

## P8 — a radius is a positive length, at both layers (C9)

`radius {c: "C", r: -1}` solved `ok: true` and emitted `Circle(radius=-1.0)`,
which raises `gp_Circ() - radius should be positive number`; `r: 0` emitted a
circle that makes no face. Two layers, because they see different numbers:

- **where it is written** — `Sketch.circle`, `Sketch.arc` and `Sketch.radius`
  refuse a non-positive or non-finite radius (`_check_radius`);
- **where it is emitted** — every radius the emitter writes is checked *after
  formatting* (`_radius`), because a free radius can solve to zero and because
  nine decimals rounds `1e-11` to `Circle(radius=0.0)`.

Elliptical semi-axes, slot radii and a slot's centre separation go through the
same check.

## P9 — `ok` did not mean the spec produced the code (C11)

Two holes, one contract:

- the hash covered the **code** only, so editing a coordinate in the
  `# agentcad-sketch-spec:` comment left the block `ok` — and the sketcher then
  opened a sketch with no relationship to the geometry beneath it;
- `persist_spec` stored the submitted coordinates and **stripped `initial`**,
  so a sketch emitted on the branch a seed selected reopened on the *other*
  branch, also `ok` (measured on a mirror-symmetric triangle submitted with
  `c.y = +10` and seeded `c.y = −10`: emitted negative, reopened positive).

The hash now covers **the spec line and the code together**, and the block
records an `initial` taken from the **solution** — the seed that reproduces the
emitted geometry. Entities and constraints are still stored as submitted, which
is what FR10 is about. The spec format is version **2**; a version-1 block
reads `unverified` rather than `diverged`, because its hash covers something
else and "we cannot tell" is the honest verdict for that.

**What `ok` guarantees now:** this spec and this code are the pair the emitter
wrote, and re-solving the spec (with its recorded `initial`) reproduces the
branch the code was emitted from.

## P10 — unreadable has to mean `unverified` (C12)

`_read_spec` checked that the JSON was an object with an `entities` key of the
right version and nothing else, so `"entities": {"points": "not-a-list"}` came
back `status: ok` and `specToModel` threw a `TypeError` out of `.map()` — a
corrupt comment in a script taking the panel down, and AC7's zero-console-error
requirement with it. `_spec_shape_problem` validates the **shape**: every
section a known kind holding a list of named objects, every constraint an
object with a `type`, `plane`/`initial` objects if present. Ten broken specs
are pinned as `unverified`; three emitted ones are pinned as still `ok`.

Shape only, deliberately: a spec naming an unknown constraint type is
*readable*, and `parse_sketch` reports on it with a message about the
constraint rather than about the comment.

## P11 — `specToModel` and `entitiesSpec` were not inverses (C13)

Four silent losses across one GUI round trip: a **construction** spline or slot
came back as *emitted* geometry, an arc lost `fixed_r`, and a three-point arc
opened as `{start, mid, end}` and re-serialized as a centre form with `center`,
`r`, `start_deg` and `end_deg` all `undefined` — a validation error where the
user expected their sketch.

`entitiesSpec` now writes every flag the model can carry, per kind.
The three-point arc is **normalized**, once, on load: the canvas has exactly
one arc representation (a centre *point entity* plus radius and two angles), so
`specToModel` builds it and adds the circumcentre as a real point. Same
geometry; it re-emits as `RadiusArc`, which is the emitter's preferred
endpoint-anchored constructor anyway. As-built divergence 9 is corrected — the
3-point spec was fully supported on the solver and emitter surface, and not in
the sketcher.

`tests/test_sketch_frontend_roundtrip.py` runs the pair **in node** over a spec
carrying every entity kind and every flag, asserts it is idempotent, and feeds
what comes back through `solve_sketch` to prove the server accepts it.
`sketcher.js` exports `__roundTrip__` for that and nothing else.

## P12 — the async half of the part scoping (C14)

0142 fixed the *synchronous* half (a part switch resets the sketcher). The
asynchronous half was still open: `sketch_plane` and `/api/sketch/blocks` are
round trips the user can outrun.

- `openSketchOnFace` now records the project and part it asked for and drops
  the answer if either moved, and `sketcher.openOnFace` re-checks the same
  thing on its own side of the module boundary.
- `refreshBlocks` records `"<project>::<part>"` with the request and re-checks
  it on arrival, alongside the generation counter.
- And the deeper one, found in the browser: `state.selectedPart` changes on the
  *click* while `editor.setPart` lands a fetch later, so a lookup started in
  between read part **A**'s script under part **B**'s owner key and generation
  counter — both of which said everything was fine. `editor.partId()` is the
  missing question ("whose script is in the buffer"), and `editor.onPartChange`
  is how the lookup is retried once the buffer catches up.

## The two findings 0142 had already closed

- **Finding 6** (greedy `1e-8` vs SVD `max(shape) · s0 · 1e-10` could
  disagree). Closed: 0142 made the greedy pass's own count *be* the rank
  wherever it runs, so the two halves cannot disagree by construction. Verified
  on the review's exact matrix `[[1,0],[1,-5e-9],[0,-1]]` — reported rank 2,
  one dependent row, and a row independent by more than the greedy tolerance
  (`-5e-7`) is kept and the *declaration-order later* row blamed instead.
  Pinned by `test_the_greedy_pass_and_the_reported_rank_can_never_disagree`.
- **The synchronous half of finding 14** (sketcher state across a part switch).
  Closed by 0142's `sketchOwner`; browser-verified again here (the banner
  clears on the switch and comes back when the user returns).

## P13 — the derivative gate cannot detect a wrong residual (C15)

It compares each `df` against a central difference of **its own `f`**, so a
geometrically wrong residual passes every case — P2's tangency did, for two
slices, with a green suite over it. This cannot be closed from inside that
harness, so two things were done instead:

1. **Said plainly, everywhere the coverage is claimed** — `AGENTS.md`, the
   `toolkit/sketch.py` docstring, and this entry. "Every residual's derivative
   is proven" means exactly that and no more.
2. **The missing layer, where it is cheap.**
   `tests/test_sketch_semantics.py` asserts residual *semantics* independently:
   for each constraint it builds a configuration whose geometry it computes
   itself, and asserts `f == 0` where the constraint holds **and** that `f`
   tracks the geometric error with the right sign and scale. The scale half is
   the one that matters — a residual that is the *square* of the error is small
   at the answer for reasons that have nothing to do with the sketch being
   right, which is exactly how `max_residual` came to be 6.1e+05 times smaller
   than the error it was reporting (0142). 37 tests over the tangency family
   (C1's home), the distance/norm family (C2's) and radius/equality, plus the
   linear vocabulary.

   What it still does not cover: `symmetric`'s and `midpoint`'s multi-row
   forms are asserted structurally rather than by a slope, and the elliptical
   tangency's auxiliary-anomaly form is not in it at all (its "geometry
   computed from first principles" is a minimization, which
   `tests/test_sketch_ellipses.py` already does end-to-end).

## Files

- `agentcad/toolkit/sketch.py` — `resolve_tangencies`, `_junction_probe`,
  `_probe_junction`, `_held_on`, `_on_curve_residual`, `_curve_scale`,
  `_pending_tangency`, `_tangent_dir_residual`, `_on_line_residual`,
  `_on_circle_residual`; `_norm_dir` and the norm-family switch;
  `distance(…, 0)` → coincidence; `|r1 − r2|` and its derivative;
  `_circumcircle` in local coordinates; `_check_radius`, `MIN_RADIUS_MM`,
  `SLOT_MIN_SPAN_MM` and the `_reseed_slots` revalidation; `analyze`'s
  cache-verification signature and `_diagnostics`; module docstring.
- `agentcad/core/sketch_emit.py` — per-chain `BuildLine`/`make_face`;
  `_radius`; the sweep refusals in `_arc_call`/`_ellipse_arc_call`;
  `no_closed_profile`; `block_hash(code, spec_line)`, `spec_line`,
  `persist_spec(spec, solution)`, `_initial_from`, `wrap_block(…, solution)`;
  `_spec_shape_problem`; `SPEC_VERSION` 1 → 2.
- `frontend/js/sketcher.js` — `ownerKey`, `arcFromSpec`, the flag-complete
  `entitiesSpec`, `__roundTrip__`, the owner/`editor.partId()` guards in
  `refreshBlocks`, `openOnFace` and `checkReopenedFace`.
- `frontend/js/editor.js` — `partId()`, `onPartChange()`.
- `frontend/js/main.js` — `openSketchOnFace` drops a stale plane.
- `tests/test_sketch_semantics.py`, `tests/test_sketch_frontend_roundtrip.py` —
  new.
- `tests/test_sketch_tangent_direction.py` — three junction spellings in
  `CLASS_SPECS` and the criterion tests; `tests/test_sketch_arcs.py` — the
  operand-order pair, the kink convention, translation invariance, positive
  radii; `tests/test_sketch_diagnostics.py` — zero distance, the degenerate
  seed, the cache poisoning, the greedy/rank agreement;
  `tests/test_sketch_slots.py` — the collapsing warm start;
  `tests/test_sketch_emit.py` — the multi-chain rebuilds, the sweep range, the
  radius refusals; `tests/test_sketch_roundtrip.py` — the spec hash, the
  branch, ten broken specs; `tests/test_sketch_drag.py` — `without_ms`.
- `AGENTS.md`, `docs/agent-api.md`, `docs/part-authoring.md`,
  `docs/prd/in-progress/PRD-009-sketcher-v2.md` — the corrected claims
  (as-built divergences 6, 7, 8 and 9 each carry a **Corrected (0143)** note).

## Verification

`uv run pytest -q tests/test_sketch*.py tests/test_prd009_acceptance.py
tests/test_tools.py tests/test_server.py` — **531 passed**.

`make test-fast` — **1616 passed, 1 skipped** in 268 s.

**`make test` in two chunks** — a single run exceeds this sandbox's 600 s
foreground cap (`test_parts_build_at_param_extremes[engine]` alone is ~890 s),
so the same command is split by file:

```
uv run pytest -q -n 4 --dist loadscope tests/ --ignore=tests/test_examples.py
    1931 passed, 1 skipped in 334.19s (0:05:34)
uv run pytest -q -n 2 --dist loadscope tests/test_examples.py
    20 passed in 1135.29s (0:18:55)
```

**1951 passed, 1 skipped** over the two chunks.

**1952 tests collected** in total, against the branch's 1826 before this work
(1825 passed + 1 skipped): **+126 tests**, every one of them a regression for a
finding above.

Solver cost (`tests/test_sketch_bench.py`, p50 of the drag-frame harness, the
range over four runs on a loaded machine):

```
                 0142      now
cam lobe         0.49 ms   0.54 - 0.61 ms
staircase 50     6.90 ms   6.62 - 7.10 ms
arc ring + slot  11.15 ms  10.89 - 11.78 ms   (FR6 budget 16 ms)
```

**Real browser** (headless Chrome for Testing via Playwright in a scratch venv,
SwiftShader WebGL, scratch server on port **8741** with a scratch projects dir
under the session scratchpad; the user's 8630 and `~/AgentCAD/projects` were
never touched and the server was stopped afterwards). The scratch part carries
an **agent-authored** block with a construction spline, a construction slot and
a three-point arc:

```
C13  open the agent block on "bracket"   banner "spec and the code are in sync"
                                         chip "19 DOF"
     the solve it sends                  sp1 construction: true
                                         sl1 construction: true
                                         a9  {center: "a9_center", r: 5,
                                              start_deg: 180, end_deg: 0}
                                         (before: construction dropped on both,
                                          and a9 center/r/start_deg undefined)

C14b /api/sketch/blocks delayed 4 s,     banner: none · 0 entities
     switch bracket → shim in flight     chip "fully constrained"
     switch back to bracket              the block opens again (the retry works)

C14a sketch_plane delayed 5 s,           sketcher did NOT open
     switch bracket → shim in flight     no solve carries a `plane`
     the same click with no switch       sketcher opens on the face

CONSOLE ERRORS: NONE
```

Screenshots: `c13-block-open.png`, `c14b-after-switch.png`,
`c14a-plane-not-installed.png`, `c14a-happy-path.png`.

`node --check frontend/js/sketcher.js frontend/js/main.js
frontend/js/editor.js` — clean.

## Notes

**What could not be closed.** The derivative gate's blind spot (P13) is
structural: nothing inside a harness that differentiates `f` can tell you `f`
is the wrong function. `tests/test_sketch_semantics.py` is a second, independent
statement of what each residual means, and two residual forms are still outside
it (named in P13). A reviewer should treat "the derivative is proven" and "the
residual is right" as two different claims, because on this branch they were.

**On the recurrence.** The honest reading of instances 2, 3 and 4 is that each
fix was *correct* and each detector was *complete for the vocabulary it was
written against*. That is the failure mode: a criterion that enumerates cannot
be complete for a vocabulary that grows. The reason to believe this one is
different is not that it is more careful — it is that it never asks what the
constraints are, only what their rows span.
