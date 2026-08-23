# 0309 — Correct the recorded narrative on bug 3 (prose only, no code)

- **Commit:** pending
- **Date:** 2026-08-23
- **Author:** Claude (Sonnet 5) with Nikita Fedorov

## Summary

Changelog `0280` (and everything that quoted it — `benchmarks/tasks/
fix_the_broken_part/fix_005_invalid_shell/prompt.md`, `docs/bench.md`,
`AGENTS.md`, `tests/test_prd024_acceptance.py`) recorded a false trigger for
bug 3: *"the STEP round trip is the cause; script-vs-script and STEP-vs-STEP
both intersect cleanly, only script-vs-STEP fails."* Changelog `0282:214-223`
already had the correct measurement (STEP ⊗ STEP is degenerate too), but the
wrong framing kept propagating into every doc that cited `0280` instead. The
measurements in `0308` settle it: serialization is irrelevant. The real
trigger is a pair of G1-tangent-jointed swept solids being **genuinely
distinct operands** — same-object and same-source-file booleans take a
shortcut inside OCCT and answer correctly regardless of format, which is why
"STEP-vs-STEP" looked clean; two *independent* STEP re-imports of the
identical shape are just as degenerate as script-vs-STEP. This commit is a
prose-only correction: no code changed, `docs/changelog/0280` and `0282` are
left untouched as the historical record, and `0308`'s code/tests are
unaffected.

## The corrected framing (now stated everywhere the wrong one was)

Tangent-jointed swept solids are unreliable `BRepAlgoAPI_Common` operands in
OCCT 7.9, independent of serialization. Operand *sameness* is what hides the
bug (a shortcut inside OCCT), not STEP-vs-script. A resulting positive volume
from a tangent-jointed pair is order-dependent and not provably trustworthy
either — the residual blind spot `0308`'s detector cannot close. `fix_005`'s
`geometry` weight stays 0.00 for the correct reason: the boolean is degenerate
for **every** candidate, the reference included, so a geometry weight there
would score everyone zero on shape — not because the STEP round trip
specifically fails. The product-side consequence — `worker.pairwise_
interference` could report a "clean" assembly on a swept-part collision — and
the fix — `agentcad/kernel/handlers/_bop.py`'s octant-subdivided crop recheck,
fail-closed `degenerate: true` on the worker path, `KernelError` on the bench
IoU path — are both `0308`, cited rather than re-explained.

## Files

- `AGENTS.md` (~:2785–2807) — rewrote the swept-solid half of the "Two
  product findings the bench fenced rather than fixed" bullet: now
  **detected** (changelog `0308`), root cause reframed to tangent-junction
  operand sameness, not STEP serialization. The bullet and the
  `docs/bench.md` cross-reference (needle asserted by
  `tests/test_prd024_acceptance.py::test_the_bench_is_cross_referenced_from_
  the_surrounding_docs`) are unchanged in shape.
- `docs/bench.md` (~:715–733) — same reframe on the "Two product findings
  bound what `geometry` can measure at all" swept-solid sub-bullet; the
  drawing sub-bullet (T1's territory) is untouched. States explicitly that
  `fix_005` keeps `geometry` 0.00 because the boolean is degenerate for every
  candidate, the reference included — not a STEP-specific defect.
- `benchmarks/tasks/fix_the_broken_part/fix_005_invalid_shell/prompt.md` —
  rewrote the reasoning inside the existing HTML review comment only (design
  §7.6 weight-override argument). The comment still starts the file
  (`<!--`), still contains "Weight override", and the prompt body after it
  — "The project holds one part, `coolant_elbow`…", the "24 mm centre-line
  bend radius" spec, the datum — is byte-unchanged.
  `tests/test_bench_tasks.py::test_prompt_text_strips_the_reviewer_html_
  comment` (:236–244) still holds: the file starts `<!--`, the comment
  carries "Weight override", and the stripped prompt text is untouched from
  "The project holds one part" onward. `task.json` is byte-unchanged.
- `tests/test_prd024_acceptance.py` (~:144–157) — docstring-only wording fix
  on `test_the_checked_in_step_datum_still_matches_its_script`: the STEP
  round-trip framing in the "why `fix_005` is the parametrize exception"
  paragraph is replaced with the tangent-junction/operand-sameness framing,
  citing `0308`. The skip logic (`scored = …`, the `DATUM_TASK_IDS`
  parametrization, the assertions) is untouched.

## Notes

- Sweep run after editing: `grep -rn "round trip" AGENTS.md docs/bench.md
  benchmarks/ --include="*.md"` and `grep -rn "21711" AGENTS.md docs/bench.md
  benchmarks/` — every remaining hit is inside the corrected wording itself
  (stating the round trip is *not* the trigger, or restating the measured
  21711.685 mm³ figure, which is still accurate) or is unrelated prior art
  (`sketch_plane`/URDF "round trip" language elsewhere in `AGENTS.md`, not
  about bug 3). No stragglers outside `docs/changelog/`.
- `docs/changelog/0280` and `0282` are historical entries and are
  deliberately left as-is per the changelog convention ("don't rewrite past
  entries except to fix a factual error" — `0282` was already factually
  correct; `0280`'s wrong framing is the thing this entry corrects going
  forward, not retroactively).
- No code, `task.json`, or `docs/changelog/0280|0282` touched.

## Verification

```
make test — 5411 passed, 50 skipped (branch tip; the slow AC1 set separately: 41 passed — all 25 references still 1.0)
```
