# 0310 — check_clearance gains an optional two-sided bound (`max_mm`)

- **Commit:** pending
- **Date:** 2026-08-23
- **Author:** Claude (Opus 5)

## Summary
`check_clearance(a, b, min_mm)` was a floor, and a floor is satisfied by moving
the two instances further apart: parking everything 500 mm apart scored full
marks on every clearance a project could declare. The constructor now takes an
optional `max_mm`, so a declaration can say "these two are within X mm of each
other" and a check can grade **placement**, not only non-interference. The
field is additive: a two-argument call emits byte-for-byte the declaration it
always did.

## Changes
- `check_clearance(a, b, min_mm, *, max_mm=None, name=None, requirement=None)`.
  `max_mm` is validated by `_positive` (so a bool, a string, `0`, a negative,
  a NaN or an infinity raise at construction, naming `max_mm`) and must be
  **strictly greater** than `min_mm` — the refusal names both numbers, because
  either one may be the typo. Equal bounds would be a window no real
  measurement can land in: a limit that cannot *pass*, the mirror of the
  non-finite limit that cannot fail.
- The bound is stored in the declaration's `limit` as `{"min_mm": …,
  "max_mm": …}` — `limit` is opaque data to every reader downstream
  (`_record` copies it wholesale into the row, and the row's `unit` is already
  `mm`), so both bounds surface in reports with no rendering change.
- `SpecRunner._eval_clearance` passes iff `min_mm <= distance` **and**
  (`max_mm is None` or `distance <= max_mm`), with the same `_slack` tolerance
  the rest of the module uses. Messages name the bound that broke:
  - over the maximum → `fail`, "… is 10 mm, above the 5 mm maximum — the parts
    are not seated";
  - under the minimum → `fail`, the unchanged "… below the 1 mm minimum"
    (declaring a maximum does not change how a floor violation reads);
  - inside both → `pass`, "… is 10 mm, within [1, 20] mm", mirroring the
    kernel's `_bounded` two-sided phrasing;
  - one-sided → `pass`, the unchanged "… at or above the 1 mm minimum".
  `skipped_mesh` (`skip`/`mesh_only`), the unknown-instance error row and the
  `KernelError` paths are untouched.
- A declaration with **no `min_mm`** (only reachable by hand-editing a
  `specs.py`, since the constructor takes it positionally) is now one named
  `error` row — *"clearance declares no min_mm; declare it with
  check_clearance"*, carrying the offending `limit` in `details` — returned
  **before** the mate pass, so it costs no kernel call. It used to report the
  same defect two ways: a clean-looking `fail` when the distance exceeded
  `max_mm`, and an `error` worded as a raw `TypeError` from `_fmt(None)`
  (caught by the evaluator dispatch) otherwise.
- Docs: the constructor is listed as `check_clearance(a, b, min_mm,
  max_mm=None)` in `docs/part-authoring.md`'s project-scope table and in
  `templates.CHEATSHEET`'s project-scope vocabulary line (the part-authoring
  cheat sheet the tools hand an author), plus a short gotcha explaining why
  `max_mm` is what turns the check into a placement check.

## Files
- `agentcad/toolkit/specs.py` — the constructor: the new keyword, its
  validation, and the docstring saying what the second bound is for.
- `agentcad/core/specs.py` — `_eval_clearance`: reads `limit` once, evaluates
  the upper bound server-side, and the three message branches.
- `agentcad/core/templates.py` — the project-scope vocabulary line in
  `CHEATSHEET`, the part-authoring cheat sheet (not a `specs.py` starter
  template — there is none).
- `docs/part-authoring.md` — the project-scope constructor table row and a new
  gotcha bullet.
- `tests/test_specs.py` — six new tests (see below).

## Notes
**Kernel-side vs server-side, and why server-side won.** `handle_clearance`
already returns `distance_mm` in every non-mesh result, so the upper bound is
one comparison on a number that has *already* crossed the process boundary.
Evaluating it in `_eval_clearance` therefore needs no new request field, no
change to `agentcad/kernel/handlers/specs.py`, and — the load-bearing part —
no version agreement between a running worker and a server that has learned
about `max_mm`: a worker that has never heard of the field still answers
correctly, because it is never asked. The minimum deliberately keeps deferring
to the worker's `ok`, so a one-sided declaration takes byte-for-byte the code
path it always took; the split is documented in a comment at the top of the
function rather than left for a reader to infer. The cost is that the two
bounds are judged in two processes — acceptable because they use the same
`_slack` formula (`max(1e-9, abs(limit) * 1e-9)`, defined identically in both
modules) and because only one of them can ever be the failing bound.

**Nothing about the wire, the gate or the merge changed.** `min_mm` is still
the only key in the `clearance` request; `run_specs`, the `specs`/`checks`
gates and the manifest merge treat a declaration's `limit` as data, so an old
declaration is unaffected and a new one carries an extra key that no reader
enumerates.

**Not touched, by design:** `benchmarks/**` (the `assemble_and_clear` rubrics
that will use this are a separate task), `docs/bench.md`'s disclosed-limitation
bullet (same task), and `agentcad/bench/**`.

## Verification
```
$ uv run pytest -q tests/test_specs.py tests/test_checks.py \
      tests/test_bench_scoring.py tests/test_specs_toolkit.py \
      tests/test_specs_kernel.py tests/test_specs_gate.py
319 passed, 2 skipped in 87.63s (0:01:27)

# after review round 1 (the min-less guard + the boundary row)
$ uv run pytest -q tests/test_specs.py
76 passed, 2 skipped in 30.34s

$ uv run ruff check agentcad/toolkit/specs.py agentcad/core/specs.py \
      agentcad/core/templates.py tests/test_specs.py
All checks passed!
```
New tests in `tests/test_specs.py`:
`test_the_clearance_maximum_is_additive_in_the_declaration` (a two-arg call is
the exact dict it was, and the two-sided one differs only in `limit`),
`test_a_clearance_maximum_at_or_below_the_minimum_names_both`,
`test_a_bad_clearance_maximum_raises_at_construction` (0, negative, string,
bool, inf), `test_a_clearance_declaring_only_a_maximum_is_one_named_error`
(a stub `SpecRunner`, no kernel — the guard returns before it needs one), and
the kernel-backed `test_run_grades_the_clearance_upper_bound` — one `specs.py`
with five rows driven end to end through the declaration pass and five
`clearance` kernel calls: a one-sided row whose message is pinned verbatim, a
satisfied maximum, a violated maximum, a gap of **exactly** `max_mm` (the
bound is inclusive at evaluation time, not only at construction), and a
two-sided row whose *floor* is broken.

`make test — 5426 passed, 50 skipped (branch tip; the slow AC1 set separately: 41 passed — all 25 references 1.0 under the hardened rubrics)`
