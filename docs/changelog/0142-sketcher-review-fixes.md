# 0142 — PRD-009 review fixes: the tangency degeneracy as a class, row-scale-free rank, and five leaks

- **Commit:** pending
- **Date:** 2026-08-13
- **Author:** Nikita Fedorov

## Summary

An independent review of the PRD-009 sketcher returned CHANGES-REQUIRED with
fifteen reproduced findings. The load-bearing one is the **third** instance of
the second-order-flat tangency degeneracy (changelogs 0132/0137 are the first
two): a junction pinned by `point_on_circle` was invisible to a detector built
on a hardcoded list of entity handles. This entry fixes the **class** — the
detector now reads the constraint graph — and fixes the rank analysis, which
had a second way to lie, plus a code-injection path through the emitted plane
header, a construction slot that emitted real geometry, and four browser bugs.

## P1 — the tangency degeneracy, as a class

**The property.** Tangency's distance forms (`dist(centre, line) - r`,
`d(c1,c2) - (r1 ± r2)`) sit at an **extremum of the manifold the other
constraints cut out**: if the sketch already holds a point `P` on both curves
then `dist(centre, line) <= |centre - P| = r` there, with equality exactly at
tangency. A residual at an extremum has its gradient in the span of the rows
that pin it, so the row adds no rank, reports itself `redundant` while removing
a real degree of freedom, and its value is the **square** of the geometric
error rather than the error.

**Why it came back a third time.** Slices 6 and 10 fixed the two ways a
junction can be *structural* or *coincident*, and both keyed off `_handles_of`
— which returns `()` for a circle (a circle has no endpoints) and only
`.start`/`.end` for an arc. A junction held by `point_on_circle` was therefore
invisible.

**The fix.** `note_incidence` records every "this point lies on this curve"
relation, driven by the module-level table `ON_CURVE_ARGS`; `parse_sketch`
registers them in a pre-pass, exactly as it already did for coincidences, so a
spec stays a set rather than a program. `_junction_handles` looks the junction
up in the union-find **and** the incidence graph together, and `_tangent_at`
builds each curve's tangent reference there — including the new
`_RadialTangent` (`rot90(unit(p - centre))`), which is what a circle needs
since it has no handles. `NOT_ON_CURVE` lists the rest of the vocabulary
explicitly, and `test_every_constraint_type_is_classified_as_on_curve_or_not`
fails when a constraint type is added without that decision. **That test is the
class-level fix**; the residual change is the instance.

Measured at the solution, before → after (the "before" column is this same
solver with the incidence graph removed from `_on_curve_handles`, which is
exactly what the handle list saw):

| configuration | rank | dof | status | blame |
|---|---|---|---|---|
| line + circle, junction by `point_on_circle` | 1/2 → **2/2** | 3 → **2** | `over_constrained` → `under_constrained` | `[tangent]` → **none** |
| line + arc, junction by `point_on_circle` | 1/2 → **2/2** | 5 → **4** | same | `[tangent]` → **none** |
| circle + circle sharing that point | 2/3 → **3/3** | 2 → **1** | same | `[tangent]` → **none** |
| junction by `point_on_line` | 2/3 → **3/3** | 4 → **3** | same | `[tangent]` → **none** |
| junction by `midpoint` | 3/4 → **4/4** | 3 → **2** | same | `[tangent]` → **none** |
| junction by a `coincident` relay onto it | 3/4 → **4/4** | 3 → **2** | same | `[tangent]` → **none** |

Compiled kind: `tangent_line_circle` / `tangent_circles` → `tangent_dir` in all
six. Smallest singular value of the row-scaled Jacobian: `0.0` → **4.28e-01 to
7.69e-01**.

**`max_residual` was lying.** On `point_on_circle(p,C) + tangent(L,C) +
distance(p,q,20)` from an off-tangent seed the solve reported
`max_residual 8.11e-11, ok: true` while the true tangency error
`|(p-c)·u|/|u|` was **4.97e-05 mm** — a ratio of **6.1e+05**, because the
distance residual is that error squared. With the direction form the residual
is the *sine* of the error, so the ratio is the radius: measured **10.0**
(`max_residual` 1.92e-09, true error 1.92e-08 mm).
`test_the_reported_max_residual_measures_the_geometric_error` asserts the
ratio, not the smallness.

**The audit of every remaining residual.** Done two ways. By hand: the only
residuals in the vocabulary whose value is a *distance at an extremum* are the
two tangency distance forms; everything else is linear in its arguments
(`fixed`, `coincident`, `distance_x/y`, `horizontal`, `vertical`, `radius`,
`equal_radius`, `midpoint`), a unit-vector product (`parallel`,
`perpendicular`, `tangent_point_perp`, `tangent_dir`, `symmetric`,
`equal_length`) or an angle (`angle`) — all first-order where they hold.
`distance` and `point_on_circle` have a vanishing-gradient case only at a
degenerate coincidence (`p == q`, `p == centre`), which is a zero-length
entity, not a pinned junction. `_tangent_ellipse`'s auxiliary-anomaly form is
already a pair of direction residuals, so it was never in this class. And
executably: `test_no_constraint_that_removes_a_dof_is_ever_blamed` runs a
four-sketch corpus covering every constraint type, asserts nothing is blamed,
**and proves the corpus is not vacuous** by dropping each constraint in turn
and checking `dof` rises. Two secondary findings, reported not fixed: every
residual that normalizes a direction has an *unbounded* gradient as its segment
degenerates (that is P3, contained by row scaling), and a junction at an
ellipse's `.major`/`.minor` axis handle is not detected — it is not degenerate
there (the auxiliary-anomaly form is first-order), so it is a gap in coverage,
not a bug.

## P3 — one badly-scaled row destroyed the rank analysis

The rank threshold was relative to the largest singular value of the whole
Jacobian while greedy blame is relative to each row's own norm, so the two
halves of the diagnostics block measured different things. A line whose points
are 1e-9 mm apart (one GUI double-click on the same spot) writes 1.4e+09 into
the matrix through `_accum_dir`'s `1/n`, lifting the threshold to ~0.28:

```
before   rank 3 of 7   dof 7 (true 3)   status over_constrained
         redundant []  conflicting []   free_entities ['b','c','d','z','z2']
after    rank 7 of 7   dof 3            status under_constrained
         redundant []  conflicting []   free_entities ['z','z2']
```

`Sketch.row_scaled` normalizes rows before the SVD and before the greedy pass
(row scaling changes neither the row space nor the null space, so it cannot
change which rows are independent), and **where the greedy pass runs to
completion its own count is taken as the rank** — greedy forward selection in
declaration order is a rank-revealing factorization, so this makes `status`,
`dof` and the blame sets agree *by construction*.
`test_the_status_and_the_blame_set_can_never_disagree` asserts that over the
whole diagnostics corpus. `chipState()` now branches on `status` rather than on
`redundant.length`, so a sketch that ever does report `over_constrained` with
an empty blame set says so instead of rendering the DOF number it just
contradicted.

One consequence worth naming: a `perpendicular` or `point_on_line` on that same
1e-9 mm line now reports `did_not_converge` (max residual 4.6e-07 and 3.6e-04)
rather than `over_constrained` with `conflicting: [that constraint]`. The rows
really are independent; the sketch really does not solve. Blaming a constraint
for a degenerate *entity* was the wrong answer.

## The rest

- **P2** — a `construction`-marked slot emitted `SlotCenterToCenter(...)` as
  real geometry: `face_slots` filtered only on `_slot_is_standalone`. Fixed at
  both exits (the face form and, via `_members`, the compiled `<slot>.arc_a` /
  `<slot>.side_1` curve form, which inherit their owner's flag). The contract is
  now asserted per entity kind in the form the next kind cannot miss: **a
  construction entity emits exactly what its absence emits**, byte for byte.
- **P4** — the sketcher carried its whole state across a part/project switch:
  nothing called `resetModel()`, so `model`, `plane`, `blockName`, `readOnly`,
  `selection` and the banner all survived and Insert appended part A's
  `sketch_profile()` into part B's script — and if A was a sketch-on-face,
  every solve still shipped A's face basis. `init` now tracks the owning
  `"<project>::<part>"` and resets on a change.
- **P5** — `plane["part"]` and `plane["face_index"]` were interpolated raw into
  generated Python; a crafted `part` put `import os` on **line 2** of the
  script, reachable from any agent/MCP/HTTP caller and defeating
  `assert "import" not in code`. Both are now validated (an int ordinal; an
  identifier-ish expression matching `_PART_REF_RE`), and the basis vectors
  raise `EmitError` instead of a bare `ValueError` — which the tool layer
  already renders as `validation_error`.
- **P6** — `initial` could never warm-start a sketch containing a 3-point arc:
  the compiled `<name>.center` point made `seed()` report `initial_incomplete`
  and cold start every frame. Compiled points are excluded from the coverage
  requirement (they may still be seeded by name).
- **P7** — three unhandled exception paths that returned HTTP 500 instead of
  the `validation_error` contract: a non-numeric plane vector, `fn(**kw)` on a
  constraint with the wrong keywords (now bound through a cached
  `inspect.Signature` *before* the call, so a genuine `TypeError` inside a
  constraint is not mislabelled), and `{"script": 123}` on `/api/sketch/blocks`
  (the one route that bypasses the registry's type check).
- **P8** — a missed `pointerup` left `press` armed, so the next button-less
  mouse movement became a drag that POSTed a solve frame per animation frame.
  `onEntityPointerDown` now captures the pointer like `startDrag` and the
  circle tool already do, and a `pointermove` with `e.buttons === 0` disarms.
- **P9** — the read-only latch was bypassable through the constraint chips,
  the one mutating entry point that did not check `readOnly`.
- **P10** — `refreshBlocks` had no request-generation guard: a slow
  `/api/sketch/blocks` landing after the user started drawing called
  `resetModel()` and discarded in-progress geometry with no undo. It now
  re-checks its own entry condition on arrival and carries a generation
  counter; `insertSnippet` gets the same guard on `solveSeq`.
- **P11** — a stale face ordinal was silently accepted on reopen. `sketch_plane`
  now returns `face_id` (`area_mm2`, `normal`, `origin`) and accepts it back as
  `expect`, answering `face_check: ok | moved | unchecked` **with both
  measurements**; the sketcher records it in the plane and re-checks on reopen.
  Surfaced, never repaired — which face the user meant is not guessable. The
  threshold (`FACE_MOVED_AREA_REL = 0.25`) sits between an ordinary resize and
  the measured renumber (`corner_r: 6.0`: 5989 mm² → 51 mm², a 99% drop).
- **P12/P14** — three docs claims that said the opposite of the code:
  `part-authoring.md` said "two sketches in one script never shadow each other"
  where `AGENTS.md` and `tools_sketch.py` say the second silently wins;
  `AGENTS.md` claimed both OCP-free modules are asserted with `OCP` blocked at
  `sys.meta_path` (only `toolkit/sketch.py` is — `sketch_emit` gets a
  post-import `sys.modules` check); and it claimed there is "no endpoint-anchored
  elliptical constructor" when `EllipticalStartArc` exists (the accurate claim
  is that none anchors *both* ends).
- **P13** — `parse_blocks` matched markers inside string literals (a docstring
  quoting the marker produced a phantom `diverged` block and shifted
  `next_name`); it now reads `tokenize`'s COMMENT tokens, falling back to the
  old line scan when the script does not tokenize. A blank line between the
  marker and the spec no longer downgrades a block to `unverified` — the
  scanner was stricter than the hash it protects.
- **P15** — `test_prd009_acceptance.py`'s wall-clock `solve_ms[50] <= 16.0` is
  flaky on a loaded machine. The FR6 budget is now asserted on the **fastest**
  frame (the measurement scheduler noise can only make worse) with a 4× ceiling
  on the median, so a genuine regression still fails and a busy machine does
  not.

## Files

- `agentcad/toolkit/sketch.py` — `ON_CURVE_ARGS` / `NOT_ON_CURVE` /
  `constraint_types()`; `_RadialTangent`; `note_incidence`,
  `_on_curve_handles`, `_junction_handles`, `_tangent_at`,
  `_junction_tangents` replacing `_joined_handle`/`_joined_handles`;
  `Sketch.row_scaled` and the rank/greedy reconciliation in `analyze`;
  `_Point.internal` and the `seed` exclusion; `_dispatch` hoisted to module
  level with `_signatures()` kwarg binding; module docstring rewritten for all
  three contracts.
- `agentcad/core/sketch_emit.py` — construction slots at both exits; `_members`
  sub-entity inheritance; `_plane_header` / `_plane_expr` validation and
  `_PART_REF_RE`; `_comment_lines` and the blank-line tolerance in
  `parse_blocks`.
- `agentcad/core/tools_sketch.py` — `face_id`, `face_check`, `FACE_ID_KEYS`,
  `FACE_MOVED_AREA_REL`, `FACE_NORMAL_TOL`; `sketch_plane`'s `expect` argument
  and its tool description.
- `agentcad/server/routes_sketch.py` — `/api/sketch/blocks` types its one
  argument.
- `frontend/js/sketcher.js` — `sketchOwner` reset on part/project change;
  `blocksSeq` / `faceSeq` generation guards; pointer capture and the
  `buttons === 0` disarm; `readOnly` on the constraint chips; `chipState`
  branching on `status`; `face_id` in the plane and `checkReopenedFace`.
- `tests/test_sketch_tangent_direction.py` — six pinned-junction
  configurations, the classification gate, the `max_residual` consistency
  test, two new derivative builders for `_RadialTangent`.
- `tests/test_sketch_diagnostics.py` — the degenerate-row regression, the
  row-scale invariance property, the status/blame consistency property, and
  the four-sketch audit corpus.
- `tests/test_sketch_emit.py` — the per-kind construction contract (7 kinds,
  both directions).
- `tests/test_sketch_on_face.py` — six crafted-plane payloads, the route's
  `validation_error`, and the four `face_check` tests.
- `tests/test_sketch_roundtrip.py` — the two error-contract tests and the two
  `parse_blocks` scanner tests.
- `tests/test_sketch_initial.py` — the 3-point-arc warm start, and the
  narrowing (a *user* point left out is still a cold start).
- `tests/test_prd009_acceptance.py` — `FR6_LOADED_SLACK`.
- `AGENTS.md`, `docs/agent-api.md`, `docs/part-authoring.md`,
  `docs/user-guide.md` — the corrected claims and the new contracts (the
  part-scoped sketch, the read-only latch reaching the chips, and the
  reopened-face check).

## Verification

`uv run pytest -q tests/test_sketch*.py tests/test_prd009_acceptance.py` —
405 passed with `test_tools.py`/`test_server.py` alongside.

**`make test` in two chunks** — a single run exceeds this sandbox's 600 s
foreground cap (`test_parts_build_at_param_extremes[engine]` alone is ~890 s),
so the same `-n 2 --dist loadscope` command is split by file:

```
uv run pytest -q -n 2 --dist loadscope tests/ --ignore=tests/test_examples.py
    1805 passed, 1 skipped in 646.72s (0:10:46)
uv run pytest -q -n 2 --dist loadscope tests/test_examples.py
    20 passed in 1076.77s (0:17:56)
```

**1825 passed, 1 skipped** over the two chunks.

**1826 tests collected** in total, against the branch's 1770 before this work
(1769 passed + 1 skipped): **+56 tests**, every one of them a regression for a
finding above.

Solver cost, re-measured (`tests/test_sketch_bench.py`, p50 of the drag-frame
harness) against changelog 0134's numbers — the row scaling is an O(mn) pass
in front of an SVD that is already there, and the kwarg binding is cached:

```
                 0134      now
cam lobe         0.45 ms   0.49 ms
staircase 50     6.72 ms   6.90 ms
arc ring + slot  10.12 ms  11.15 ms   (FR6 budget 16 ms)
```

**Real browser** (headless Chrome for Testing 1228 via Playwright in a scratch
venv, SwiftShader WebGL, scratch server on port 8731 with a scratch projects
dir; the user's 8630 was never touched and the server was stopped afterwards):

```
P4   square on part "Bracket"        4 points · chip "6 DOF" · Insert enabled
     click part "Shim"               0 points · chip "fully constrained" ·
                                     Insert disabled · banner cleared
     draw on Shim, add H             POST /api/sketch/solve carries 4 points
                                     and NO `plane` key

P8   press a point, swallow the      solve POSTs during a 14-step button-less
     pointerup, move the mouse       mouse move: 0

P10  /api/sketch/blocks delayed      3 points drawn while it was in flight
     6 s in-page, draw meanwhile     3 points after it landed · not sk-locked

P9   diverged block reopened         sk-locked · chips ["H l1 ×", "V l2 ×"],
                                     both disabled
     click every chip                chips unchanged · 0 solve POSTs

P3   square + H bottom + H top       chip "4 DOF"
     + a redundant Par               chip "over-constrained (1)" · class warn

CONSOLE ERRORS: NONE
```

Screenshots: `v-a-square-on-bracket.png`, `v-b-after-part-switch.png`,
`v-c-buttonless-move.png`, `v-d-slow-blocks.png`,
`v-e-readonly-diverged.png`, `v-f-over-constrained-chip.png`.

`node --check frontend/js/sketcher.js` — clean.

## Notes

The reason this entry spends most of its length on P1 is that the *residual*
was already understood twice; what was missing was a place where "a junction is
pinned" is defined once. It is `ON_CURVE_ARGS` now, and the test that keeps it
honest is not about tangency at all — it is that every constraint type the spec
front-end accepts is classified. A fourth instance now requires someone to add
a constraint type *and* add it to `NOT_ON_CURVE` while it does put a point on a
curve.

Follow-up not taken: a junction at an ellipse's `.major`/`.minor` handle is not
detected as pinned. It is not a degeneracy there — the auxiliary-anomaly form
is first-order — so the cost is one unnecessary parameter, not a wrong answer.
