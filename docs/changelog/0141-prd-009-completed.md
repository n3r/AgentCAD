# 0141 — PRD-009 slice 14: acceptance tests AC1–AC7, the docs sweep, close-out

- **Commit:** pending
- **Date:** 2026-08-12
- **Author:** Nikita Fedorov

## Summary

The last slice of [the sketcher-v2 plan](../superpowers/plans/2026-08-12-sketcher-v2.md).
It adds `tests/test_prd009_acceptance.py` — **one named test per acceptance
criterion**, walked through the tools, the routes and a real kernel rather
than through the unit seams — sweeps the prose surfaces that the eleven
implementation slices left behind, gives `AGENTS.md` the fourteen hard-won
sketcher gotchas, and records in the PRD what was actually built, including
the twelve places it diverges from what the PRD asked for.

**`make test` → 1769 passed, 1 skipped** (1770 collected), against the
**1441 passed, 1 skipped** baseline this PRD started from.

It also found a bug, which is the point of writing acceptance tests over the
real stack: **`solve_ms` has been a large negative number for every sketch
containing an arc since slice 5** — see below.

## The acceptance suite

`tests/test_prd009_acceptance.py`, 35 tests (2 `slow`):

| AC | Test | What it actually asserts |
|---|---|---|
| AC1 | `test_ac1_a_slotted_cam_emits_code_that_rebuilds_to_the_solved_metrics` | the slotted cam solved through the **tool**, emitted with a round-trip block, rebuilt through the **real kernel**, compared against the area from Green's theorem over the *solved* coordinates and the bbox from the arcs' true extremes — `rel=1e-6` |
| AC2 | `test_ac2_a_hundred_step_drag_over_the_route_never_flips_branch` | 100 warm-started drag frames over `POST /api/sketch/solve`, the browser's frame protocol minus the browser: **0 flips**, `warm_started` on every frame, cached diagnostics, solver p50 inside 16 ms |
| AC2 | `test_ac2_browser_half_evidence_is_recorded` | the slice-10 session is on the record (flips, prediction, console, screenshots) |
| AC3 | `test_ac3_a_redundant_constraint_names_the_constraint_that_was_added` | the redundant `parallel` is named as **#6** — the one just added — with `dof 0` and `ok: true`; and the contradictory variant raises with the same set in `details.diagnostics` |
| AC4 | `test_ac4_an_under_constrained_sketch_reports_dof_and_free_entities` | over the route: `dof 2 == n_params − rank`, `under_constrained`, `free_entities` naming the free corners |
| AC4 | `test_ac4_the_dof_chip_is_wired_into_the_shipped_frontend` | the chip, its click-to-highlight, its "not necessarily the unique culprit" wording and its `cached` staleness marker are still in `sketcher.js` |
| AC5 | `test_ac5_a_sketch_on_the_enclosures_top_face_rebuilds_green` | the enclosure's largest planar face, anchored to a projected edge (`point_on_line` + 20 mm along it), `dof 0`, emitted **through the tool** with the basis and the caveat, round-tripped through `parse_blocks`, and rebuilt as a real 12 × 8 × 2 mm pad on the real part — from a **copy** of `examples/` |
| AC6 | `test_ac6_the_v1_corpus_is_identical_through_the_tool_surface` (22 cases) | every v1 case, through the **tool** rather than the library, still returns the coordinates captured from the shipped solver to `abs=1e-9`, and FR3's nine frozen keys are still a subset |
| AC6 | `test_ac6_solve_ms_is_a_duration_even_when_the_sketch_has_arcs` | `solve_ms` is bounded on **both** sides, with and without arcs — the regression for the bug below |
| AC6 | `test_ac6_the_full_suite_count_is_cited` | this entry cites a `make test` count — and so must whatever entry lands next |
| AC7 | `test_ac7_browser_half_evidence_is_recorded` | all five browser sessions (0135, 0136, 0138, 0139, 0140) are on the record, each with a clean console |
| AC7 | `test_ac7_the_sketcher_surfaces_the_changelogs_claim_exist` | the drag protocol, the block loader, `openOnFace`, the server emission call — and that `buildSnippet`/`findChains`/`fmtNum` have **not** come back, because there must be exactly one emitter |
| AC7 | `test_ac7_the_round_trip_survives_the_whole_stack` | solve → persist → parse → re-solve → re-emit over the route, byte for byte, plus a hand edit detected as `diverged` with the spec intact |
| — | `test_the_solver_and_the_emitter_run_in_the_server_process` | a fresh interpreter solves and emits with neither `OCP` nor `build123d` in `sys.modules` |

Two of them borrow their geometry oracles from `tests/test_sketch_emit.py`
and `tests/test_sketch_on_face.py` rather than re-deriving Green's theorem
here — a second implementation of an oracle is a second thing to keep true.

**The browser halves are evidence checks plus a structural gate**, the PRD-001
AC6 / PRD-002 AC1 / PRD-008 AC1 pattern: the evidence check fails if the
record is deleted, the structural gate fails if the feature is deleted, and
neither claims to prove that anything renders. Those were driven for real, in
five sessions, and the changelogs are the record.

## The bug the acceptance suite found

`solve_ms` — one of FR3's **nine frozen result keys** — has been wrong for
every sketch containing an arc since slice 5:

```
cam lobe (2 arcs)   solve_ms  -183277193.73     # before
cam lobe (2 arcs)   solve_ms         4.70 ms    # after
rectangle (no arcs) solve_ms         0.40 ms    # unchanged
```

`Sketch.solve` takes `t0 = time.perf_counter()` at the top and
`t1 = time.perf_counter()` after the diagnostics — and the **arc output loop
twenty lines later reused the name `t1`** for the arc's start angle in
radians. `solve_ms` was therefore `(an angle − t0) × 1000`: about −51 hours on
this machine, and negative on every machine.

Nothing caught it for six slices, for three reasons worth keeping:

- the **v1 corpus asserts coordinates**, and FR3 freezes the keys' *meanings*
  in prose, not in an assertion;
- the **benchmark times the call itself** with its own `perf_counter`, so
  every FR6 number ever quoted is unaffected — and so are slice 10's browser
  measurements, whose `srv` column came from `solve_ms` on **arc-free**
  sketches (a staircase and a 4-line profile);
- a **one-sided budget assertion cannot see it**: `solve_ms <= 16` passes
  happily on −183 000 000.

The fix is a rename (`th1`/`th2` for the angles) with the reason in a comment,
and the regression is
`test_ac6_solve_ms_is_a_duration_even_when_the_sketch_has_arcs`, which bounds
the value on **both** sides with and without arcs. AC2's route assertion is
now two-sided for the same reason.

## Docs

- **`docs/part-authoring.md`** — ellipses (the eccentric-anomaly
  parametrization, the handles, what is out of scope), the junction rule for
  tangency, a **Sketching on a face** section (the basis, fixed +
  construction references, the `kind: "other"` gap, the renumbering caveat),
  the **entity → build123d mapping table** (FR11), and **round-trip
  persistence** (the block, `parse_blocks`, the three statuses).
- **`docs/user-guide.md`** — the "Sketching & push/pull" section rewritten
  around what a user now sees: the new tools, the DOF chip and what clicking
  it does, drag-to-solve and why a fully-constrained sketch does not move,
  sketch-on-face, the reopenable block, and the divergence banner's two
  choices.
- **`agentcad/core/templates.py`** (the `part_template` cheat-sheet) — the
  new entity table, virtual handles, all 21 constraint types, the diagnostics
  block, what `initial` is *for*, and `emit`/`persist`/`plane`.
- **`docs/architecture.md`** — two new rows: `toolkit/sketch.py` (the residual
  IR, the analytic Jacobian, why it is one of two OCP-free toolkit modules)
  and `core/sketch_emit.py` (the single emitter and the round-trip block);
  plus the `sketchplane` handler pack and the sketch-blocks route.
- **`docs/agent-api.md`** — `persist` on the `solve_sketch` row, `ellipses` in
  the entity shape, and a round-trip paragraph naming `parse_blocks` and
  `POST /api/sketch/blocks`.
- **`README.md`** — the tool count corrected to **71 (74 with `[fem]`)**,
  which slice 12's `sketch_plane` had made stale, and the toolkit bullet now
  says what the solver actually covers.
- **`AGENTS.md`** — a new **"Sketcher gotchas (PRD-009)"** section, fourteen
  items, each traceable to a measurement in 0127–0141. The ones that cost the
  most to learn: **a residual that is second-order flat at a shared endpoint
  makes the Jacobian rank-deficient *at* the solution and reports phantom
  DOF** (found twice — slots in 0132, tangency in 0137 — and fixed both times
  by choosing the residual *form*, never a tolerance); `EllipticalCenterArc`
  takes `arc_size`, because `end_angle` raises `UnboundLocalError` in the
  pinned build123d; **for a face, the basis is stable and the ordinal is
  not**; emission is 9 decimals with shared literals behind a closure gate,
  and the bug only reproduces on non-round coordinates; and diagnostics are
  kept off the drag path **by structure** — the drag rows live outside
  `self.residuals` and `solve` slices them off before anything is reported —
  not by a flag anyone can forget to set.

## The PRD

`docs/prd/in-progress/PRD-009-sketcher-v2.md` gains **Status: implemented**, a
**Verification** table (AC → proving test → the number measured), and an **As
built — divergences from this document** section with twelve entries. The
ones a reviewer should read first:

1. **The browser missed the 16 ms budget and the GUI now predicts locally**
   (0136). Keep-alive works in Chrome (0 new connections in 600 requests) and
   the network leg is 4.2 ms; two display-frame boundaries are what push
   end-to-end to 18.0 ms p95 (32.3 ms at the FR6 size). The plan's named
   fallback was selected with the number in hand.
2. **Slice 2's ≥ 20× rollback bar was not met end to end — 16–17× — and the
   cause is measured** (0128): the Jacobian itself improved 104×; the rest is
   scipy's trust-region machinery. A bespoke LM loop hits the design's 0.78 ms
   and was **not** adopted, with the reasoning recorded.
3. **Ellipse-to-ellipse tangency and on-ellipse point constraints are
   refused**, not quietly approximated (0138).
4. **The construction toggle arrived with sketch-on-face, not slice 9**
   (0135/0139) — a browser-only flag would have been a lie the server-side
   emitter never sees.
5. **`sketch_plane` is a new tool where the design said none** (0139), with
   the reasoning and the corrected count.
6. **Round-trip "re-solve from the spec" appends a new block rather than
   rewriting the diverged one** (0140).

The file stays in `docs/prd/in-progress/`; moving it and flipping
`docs/roadmap.md` is the close-out commit's job, not this slice's.

## Files

- `tests/test_prd009_acceptance.py` — **new**, 35 tests
- `agentcad/toolkit/sketch.py` — the `solve_ms` fix (a one-line rename)
- `docs/part-authoring.md`, `docs/user-guide.md`, `docs/architecture.md`,
  `docs/agent-api.md`, `README.md`, `AGENTS.md`
- `agentcad/core/templates.py` — the cheat-sheet's sketch section
- `docs/prd/in-progress/PRD-009-sketcher-v2.md` — status, verification, as-built
- `docs/changelog/0141-prd-009-completed.md` — this entry

## Verification

```
uv run pytest -q tests/test_prd009_acceptance.py       35 passed
uv run pytest -q tests/test_sketch_*.py tests/test_sketch.py
                                                     297 passed
```

**`make test`**, run in chunks (this sandbox caps a foreground command at
600 s; `test_parts_build_at_param_extremes[engine]` alone is ~890 s and
**must run alone** — run alongside another chunk it times out at 900 s, which
it did here once before being re-run by itself):

| chunk | result | time |
|---|---|---|
| `-m "not slow"` (`-n 2 --dist loadscope`) | 1441 passed, 1 skipped | 258.13 s |
| `-m slow` checks_pipeline/checks_ref/checks_cli/checks_api | 93 passed | 207.97 s |
| `-m slow` specs/specs_gate/specs_api/packet | 120 passed | 93.69 s |
| `-m slow` anchors_kernel/pool/sandbox/proposals*/prd00{1,2,3,4,8,**9**} | 71 passed | 129.77 s |
| `-m slow` mcp/kernel/geometry_ci/comments_proposals/sketch_{bench,diagnostics,arcs,on_face,emit,roundtrip} | 24 passed | 22.94 s |
| `-m slow` examples `-k "not engine"` (`-n 4`) | 16 passed | 93.64 s |
| `-m slow` examples `-k "engine and not param_extremes"` | 3 passed | 294.11 s |
| `-m slow` examples `-k "param_extremes and engine"` (alone, serial) | 1 passed | 836.71 s |
| **total** | **1769 passed, 1 skipped** | |

`93 + 120 + 71 + 24 + 16 + 3 + 1 = 328`, and `pytest --collect-only -m slow`
collects **328**; `-m "not slow"` collects **1442** = 1441 passed + 1 skipped.
Total collected **1770**. The four non-engine `param_extremes` cases appear in
two chunks and are counted once.

**Honest note on the chunks.** The `solve_ms` fix landed after the first pass,
so `-m "not slow"` (which contains every sketch module and both new files) and
the two `slow` chunks that touch the sketch stack were **re-run after it**;
the checks, specs and examples chunks were not, because nothing in them
imports `agentcad.toolkit.sketch` (`grep -rl "toolkit.sketch" agentcad/
examples/` returns `core/sketch_emit.py`, `core/templates.py`,
`core/tools_sketch.py` and `toolkit/__init__.py`, all of which are in the
re-run chunks).

The `-m "not slow"` chunk above is the re-run *after* this entry existed: the
first pass reported **1 failed** —
`test_ac6_the_full_suite_count_is_cited`, which requires the close-out entry
to cite a `make test` count — which is the gate working exactly as designed
(PRD-008's `test_ac9` does the same for the newest entry).

Against the **1707 passed, 1 skipped** state slices 1–12 left, this is
**+62**: 27 in `tests/test_sketch_roundtrip.py` (slice 13, changelog 0140) and
35 in `tests/test_prd009_acceptance.py` (this slice, of which 2 are `slow`).
Against the **1441 passed, 1 skipped** baseline the PRD started from, +328,
and no test was deleted; two were rewritten, both because they encoded
behaviour the branch fixed (0137's Notes and 0128's).

## Notes

- **The acceptance file imports helpers from three other test modules**
  (`test_sketch_emit`, `test_sketch_on_face`, `test_sketch_v1_corpus`). That
  is a deliberate coupling: the corpus's captured coordinates and the two
  geometry oracles are the things being asserted *against*, and copying them
  here would create a second copy that can drift into agreement with a bug.
- **AC6 is asserted twice on purpose.** The corpus module asserts the library
  call; the acceptance file asserts the same 22 cases through the registered
  tool, which is the layer where "over-constrained is not an error" could have
  broken v1 compatibility without the corpus noticing.
- **What the acceptance suite does not prove:** that anything renders, that a
  pointer drag feels smooth, or that a console is clean. Those are browser
  claims, they were driven for real five times, and the changelogs — with
  screenshots — are the record. The structural gates under them are the
  strongest thing a Python test can say about a browser feature, and they say
  it plainly rather than implying more.
- `docs/roadmap.md` is untouched here, and the PRD is still in
  `in-progress/` — the move is the close-out commit.
