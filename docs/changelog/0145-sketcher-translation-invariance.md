# 0145 — the junction verdict is translation invariant, and its cache dies with its configuration

- **Commit:** pending
- **Date:** 2026-08-13
- **Author:** Nikita Fedorov

## Summary

Final verification of 0144 found the recurring junction defect for the sixth
time, on a new axis. 0144 made the verdict independent of the **unit** the
sketch was drawn in; it was still a function of **where** the sketch sat,
because `_configuration_scale` measured a position rather than a length. A
small drawing far from the origin got a proportionally looser manifold gate,
`_junction_probe` took its fast path, and the criterion was read at a seed that
is not on the manifold — the very thing 0144 deleted the projection to prevent.
Second fix: the criterion's cached start configuration outlived the
configuration it was read at, so the documented object API crashed when a
sketch was drawn on after a solve.

## P1 — `_configuration_scale` measured a position, not a length (N1, HIGH)

`_configuration_scale` returned `max(|x|, |y|)` over the coordinates, while its
own docstring called it "the sketch's own largest length". A coordinate is a
position. Move a drawing and every length in it is unchanged, but that number
grows without bound — and `_junction_probe`'s manifold gate is written as a
fraction of it:

```python
tol = JUNCTION_MANIFOLD_TOL * self._configuration_scale(x0)
if float(np.max(np.abs(fun(x0)))) > tol:
    ...   # the seed is not on the manifold; solve for one that is
```

Under that gate a seed only had to be *proportionally to its distance from the
origin* close to the other rows' manifold to be trusted as the solved
configuration. The gate is 2.0e-06 at the origin, 1.0e-03 at 1e4 mm, 3e-03 at
5e4 mm — a silent band growing linearly with the translation.

Measured on `distance(p, c, r(1 + d))` + `radius(C, r)` + `tangent(L, C)`,
r = 10, `d = 1e-4` (so `p` is genuinely **off** the circle and there is no
junction; the distance form is the right answer):

```
shift    compiled form         ok     rank   warning   true tangency error
0        tangent_line_circle   true   3/3    none      +1.5e-09 mm   correct
1e2      tangent_line_circle   true   3/3    none      +6.0e-10 mm   correct
1e4      tangent_dir           true   3/3    none      +1.0e-03 mm   WRONG, silent
1e6      tangent_dir           false  3/3    none      +1.0e-03 mm   WRONG, loud
```

The whole verdict table, swept over the relative offset and the translation:

```
            before (max |coordinate|)          after (extent)
rel delta   0    1e2   1e4   1e6           0    1e2   1e4   1e6
0e+00      dir   dir   dir   dir          dir   dir   dir   dir
1e-09      dir   dir   dir   dir          dir   dir   dir   dir
1e-08      dir   dir   dir   dir          dir   dir   dir   dir
1e-07      dir   dir   dir   dir          dir   dir   dir   dir
1e-06     FLAT   dir   dir   dir         FLAT  FLAT  FLAT  FLAT
1e-05     FLAT  FLAT   dir   dir         FLAT  FLAT  FLAT  FLAT
1e-04     FLAT  FLAT   dir   dir         FLAT  FLAT  FLAT  FLAT
1e-03     FLAT  FLAT  FLAT   dir         FLAT  FLAT  FLAT  FLAT
1e-02     FLAT  FLAT  FLAT   dir         FLAT  FLAT  FLAT  FLAT
1e-01     FLAT  FLAT  FLAT  FLAT         FLAT  FLAT  FLAT  FLAT
```

**The fix** is to make the number an *extent* — how far apart the sketch's
coordinates are, plus how big its radii are — instead of how far they sit from
(0, 0):

```python
lo_x = lo_y = math.inf
hi_x = hi_y = -math.inf
for ref in self._refs.values():
    x, y = ref.value(xs)
    x, y = float(x), float(y)
    lo_x, hi_x = min(lo_x, x), max(hi_x, x)
    lo_y, hi_y = min(lo_y, y), max(hi_y, y)
best = max(hi_x - lo_x, hi_y - lo_y) if hi_x >= lo_x else 0.0
for rad in self._rads.values():
    best = max(best, abs(float(rad.value(xs))))
return best if best > 0.0 else 1.0
```

The radius term stays, because a lone circle has an extent of 0 between its two
coordinates and a real size. The `1.0` floor stays: it is the only number left
when a sketch has no lengths at all. The unit sweep (`x1e-6 … x1e6`) is
byte-identical before and after — on the sketches it tests the extent and the
max coordinate are the same number, which is why 0144's fix did not surface
this one.

**The guard that was missing.** There was a test sweeping the *scale* axis
(`test_the_junction_verdict_does_not_depend_on_the_unit_it_was_drawn_in`) and
nothing sweeping the *translation* axis, which is how five previous instances
of this defect could each be fixed on one axis and survive on another. Its twin
now exists —
`test_the_junction_verdict_does_not_depend_on_where_the_sketch_sits`, same
drawing, same relative-offset table, swept at shifts 0 / 1e2 / 1e4 / 1e6.

**And the identity test needed the same treatment, in a way worth naming.**
`test_detection_reaches_exactly_as_far_as_the_solve_does` asserts
`flat == undecided == unsolved` over a 1000 mm sweep — at the origin. Swept
along the translation axis, only two of those three terms are properties of
this code:

```
shift    flat == undecided    flat  unsolved   worst max_residual, direction-form solves
0        True                  22      22      6.84e-09
1e2      True                  17      17      7.40e-09
1e4      True                   2      16      4.05e-07
1e6      True                   3      79      2.47e-05
```

`flat == undecided` — the pass is never quiet about refusing — now holds at
every offset, and `test_the_flat_form_is_always_the_loud_one_wherever_the_
sketch_sits` asserts exactly that at four shifts. `== unsolved` is **not**
asserted away from the origin, and deliberately: `ok` is an *absolute*
`max_residual < 1e-7` while the criterion is relative, so at 1e4 mm coordinates
a solve converged to 4e-11 *relative* scores 4e-07 absolute and fails a gate the
junction pass does not own. That is `solve`'s threshold to answer for; naming it
here so the next reader does not mistake the split for an oversight.

## P2 — a stale `_junction_x0` crashed `solve()` after a mutation (N2, MEDIUM)

`solve()` starts `least_squares` from `self._junction_x0` when the criterion set
one. It was captured once and `_tangencies_resolved` blocked recomputation, so
the vector outlived the parameter vector it was a configuration of. Through the
documented object API (`agentcad/core/templates.py:191` — `sk = Sketch();
sk.point(...); sk.solve()`), a part script that keeps drawing after a look at
the answer got an `IndexError` out of `least_squares` — `index 8 is out of
bounds for axis 0 with size 6` as reported, `index 6 ... size 6` on the
regression test's own spec; the index is whichever slot the new entity claimed.

Same for declaring a tangency after a solve: it was never asked the junction
question at all, because the pass had already run. The HTTP route and the MCP
tool build a fresh `Sketch` per call and were unaffected, so this was the
agent / part-script surface only.

Fixed by invalidation rather than by a length guard, because the cache is a fact
about a configuration and it should die with the configuration:
`_invalidate_junction_cache` clears `_tangencies_resolved`, `_junction_x0` and
`_provisional_x`, and is called from `_claim` (every entity) and `_add` (every
row) — the two chokepoints every declaration already passes through. Neither is
on the criterion's own path, so it cannot re-arm itself.

Re-deciding then had to be made honest, which is two more pieces:

- each `_pending_tangency` entry now carries the **distance-form residual it
  was compiled as** (`"flat"`), and `resolve_tangencies` restores every pending
  row from it before re-deciding. Otherwise a row swapped to `tangent_dir` by
  the first pass would survive a second verdict that no longer holds — quietly
  compiling a form the criterion did not choose, the one thing this pass must
  never do. A no-op on the first pass.
- a re-decision drops the previous pass's `tangency_junction_undecided` warning
  instead of stacking a second copy of it on the result.

## P3 — `JUNCTION_MANIFOLD_TOL` does two jobs (N4, LOW)

It is read twice in two different units, which is correct and was undocumented:
scaled by `_configuration_scale` in `_junction_probe` (those rows carry the
sketch's length unit), and **unscaled** in `resolve_tangencies`'s choice of
`start` (the `tangent_dir` row there is a cross product of two *unit* tangents,
a pure number). Both readings are now spelled out at the definition, with the
note that multiplying the dimensionless one by a millimetre would be the bug
the constant already has a fix for.

## P4 — two prose corrections (LOW)

- **0142**, the P3 aside: the `point_on_line` residual on the 1e-9 mm line was
  written as `3.6e-04`; measured, it is `3.68e-07`. No test asserts either
  number — this is a changelog-only correction, in line with the "fix a factual
  error" exception in `docs/changelog/README.md`.
- **0143 P8** is correct as written and needs no change: `1e-11` is a positive,
  finite radius, so `_check_radius` accepts it where it is *written*
  (`Sketch.circle` takes `r = 1e-11` without complaint, verified) and it is the
  emitter's post-formatting check (`agentcad/core/sketch_emit.py:501`,
  `_radius`) that refuses it, because nine decimals round it to
  `Circle(radius=0.0)`. Recorded here because an earlier brief stated the
  declaration layer refuses it too, and the code does not.

## Cost — the cold number, re-measured (N3, LOW)

Unchanged by this commit, and 0144's own cold table still holds. Re-measured on
the same bench (staircase-50 plus a junction seeded 300 mm off, p50 of 7,
compile and solve separated by `parse_sketch` / `Sketch.solve`):

```
                             0144 measured        now
seeded on the junction    8.6 +  8.3 =  16.9   7.9 +  8.0 =  16.0 ms
seeded 3 mm off           9.3 +  9.0 =  18.2   9.1 +  8.6 =  17.8 ms
seeded 300 mm off       316.5 +  8.1 = 324.6 292.4 +  7.6 = 300.0 ms
  pre-0143 baseline       0.4 +418.8 = 419.2   0.4 +455.1 = 455.5 ms
```

A **cold** compile of a large sketch whose junction the criterion has to decide
pays one extra full solve, and it is paid in the *compile*, not the solve — for
the 300 mm case that is 292 ms of the 300 ms total. It is inside FR6's 250 ms
cold budget only because FR6 measures the drag frame; the cold case is the worst
number on the page and is named as such in 0144.

A **warm drag frame still costs nothing** — 5.4 ms best / 6.9 ms p50 for the
whole compile + solve against a 16 ms budget — because the frame arrives with
`initial` set to the previous solution, which already solves the rows the
criterion is about.

One measurement trap worth recording, because it produced a spurious "the cold
cost regressed to 286 + 314" report during verification: `p11_cost.py`'s
"solve" column calls `solve_sketch`, which **re-compiles**, so on this sketch it
counts the 292 ms compile a second time. That is the same double-count 0144
already flagged in 0143's table. `p12_cold.py` separates the two and is the
number to quote.

## Changes

- `_configuration_scale` returns the sketch's coordinate **extent** (plus its
  largest radius) instead of its largest absolute coordinate; docstring
  corrected to say what it measures and why.
- `_invalidate_junction_cache` added and called from `_claim` and `_add`;
  `_tangencies_resolved`, `_junction_x0` and `_provisional_x` are cleared on
  every entity and every row the sketch gains.
- `_pending_tangency` entries carry their compiled distance-form residual;
  `resolve_tangencies` restores it before a second pass, and clears the
  previous pass's `tangency_junction_undecided` warning.
- `JUNCTION_MANIFOLD_TOL`'s two readings documented at the definition.
- Three new tests; 0142's `point_on_line` number corrected.

## Files

- `agentcad/toolkit/sketch.py` — `_configuration_scale` (extent),
  `_invalidate_junction_cache` (new), `_claim` / `_add` (invalidation),
  `resolve_tangencies` (restore + warning replacement), the two
  `_pending_tangency.append` sites (`"flat"`), `JUNCTION_MANIFOLD_TOL` comment.
- `tests/test_sketch_tangent_direction.py` —
  `test_the_junction_verdict_does_not_depend_on_where_the_sketch_sits`
  (24 cases), `test_the_flat_form_is_always_the_loud_one_wherever_the_sketch_
  sits` (4 cases), `test_the_object_api_may_keep_drawing_after_it_has_solved`.
- `docs/changelog/0142-sketcher-review-fixes.md` — the `3.6e-04` → `3.68e-07`
  correction.

## Verification

`.venv/bin/python -m pytest -q tests/test_sketch*.py
tests/test_prd009_acceptance.py` — 581 passed. `make test-fast` (`-n 2
--dist loadscope -m "not slow"`) — 1688 passed, 1 skipped. Full suite in two
chunks, because a single `make test` exceeds the 600 s tool cap: chunk A
`-n 4 --dist loadscope tests/ --ignore=tests/test_examples.py` — **1998
passed, 1 skipped** (317 s); chunk B `-n 2 tests/test_examples.py` — **20
passed** (924 s). **2018 passed and 1 skipped across the 2019 collected**
(`--collect-only` reports 2019); the skip is the same pre-existing
`test_analysis.py:166` the 1990-test baseline reports. Collection went
1990 → 2019 (+29): 24 translation-sweep cases, 4 identity-sweep cases, 1
object-API regression.

Every new test was run against the pre-change behaviour first — the old
`_configuration_scale` and a no-op `_invalidate_junction_cache` restored by a
plugin — and **8 of the 29 fail there**: the six translation-sweep cases where
the gate had opened wide enough to swallow the offset (`d = 1e-06` at shifts
1e2/1e4/1e6, `1e-04` at 1e4/1e6, `1e-02` at 1e6), the identity sweep at shift
1e6 (3 seeds kept the flat form and 0 of them carried the warning), and the
object-API regression with `IndexError` out of `least_squares`. The other 21
cases pass on both sides, which is the point: the fix moved the verdict only
where the verdict was wrong.

## Notes

- This is the **sixth** instance of one defect: a junction verdict that depends
  on something geometry does not. Seed distance (0143), evaluation budget
  (0143), the millimetre (0144), a stationary point of the residual (0144), a
  curve with no size (0144), and now the origin. The pattern each time is a
  tolerance written against a quantity that is not the one the question is
  about. The two sweeps — scale and translation — are now both guarded; the
  axis with no test is the axis a seventh instance will use.
- Still open, and deliberately not fixed here: `solve`'s `ok` is an absolute
  `max_residual < 1e-7`, so a sketch far from the origin is scored on a
  yardstick its own coordinates cannot meet (2.5e-05 worst residual at 1e6 mm
  on a genuinely converged solve). That is a `solve` question, wider than the
  junction pass, and worth its own pass.
