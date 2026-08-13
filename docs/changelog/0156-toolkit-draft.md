# 0156 — 2026-08-13 — PRD-010 slice 10: `features.draft`, measured monotone on real parts

- **Commit:** pending
- **Date:** 2026-08-13
- **Author:** Claude (PRD-010 slice 10)

## Summary

The rest of FR8: `features.draft(part, faces, angle_deg, neutral_plane)` over
build123d's `draft`, binary-searching **down** on failure exactly as
`safe_fillet` does, plus an Error Doctor entry for the failure OCCT describes
with an empty string. Design Decision 7 promoted draft out of the PRD's Phase 3
on the strength of monotone failure measured on **four synthetic shapes**;
spike S6 widened that to **four bundled example parts through the kernel
worker** before the search was allowed to rely on it. It holds — and the sweep
turned up a second fact the design did not have, which changed the
implementation: **the dominant draft failure does not raise at all**.

## Spike S6 — the monotonicity sweep, through the kernel worker

`scratchpad/spike_draft_sweep.py`, 27 angles from 0.25° to 60°, on the design's
four synthetics and on four bundled parts at the params their `project.json`
ships. Faces are "every face whose normal is perpendicular to the pull
direction" (the selector a caller writes), neutral plane at the part's bbox
minimum Z. `ok` means the call returned a **valid** solid of positive volume.

| part | faces | 0.25 | 0.5 | 0.75 | 1 | 1.5 | 2 | 2.5 | 3 | 3.5 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 12 | 15 | 17.5 | 20 | 25 | 30 | 35 | 40 | 45 | 50 | 60 |
|---|---:|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| synthetic: box 40×30×20 | 4 | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok | X | X | X | X |
| synthetic: box + R4 vertical fillets | 8 | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok | X | X | X | X | X | X | X | X | X | X | X |
| synthetic: box + boss | 5 | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok | X | X | X | X | X | X | X | X | X |
| synthetic: shelled box t=2 | 8 | ok | ok | ok | ok | ok | ok | ok | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X |
| `prototyping/enclosure_base` | 56 | ok | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X |
| `rocketry/nozzle` | 2 | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X |
| `construction/angle_bracket` | 7 | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X |
| `construction/gusset_plate` | 18 | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok | ok | X | X | X | X | X | X | X | X |

| part | largest ok | smallest fail | monotone | islands | per-attempt cost |
|---|---:|---:|---|---|---:|
| box 40×30×20 | 35 | 40 | **yes** | none | 3.06–5.53 ms |
| box + R4 fillets | 10 | 12 | **yes** | none | 7.53–10.76 ms |
| box + boss | 15 | 17.5 | **yes** | none | 3.34–5.69 ms |
| shelled box t=2 | 2.5 | 3 | **yes** | none | 3.12–8.43 ms |
| `enclosure_base` | **0.25** | 0.5 | **yes** | none | 15.20–82.48 ms |
| `nozzle` | **none** | 0.25 | **yes** | none | 0.05–0.34 ms |
| `angle_bracket` | **none** | 0.25 | **yes** | none | 0.29–0.63 ms |
| `gusset_plate` | 17.5 | 20 | **yes** | none | 18.11–21.63 ms |

**Finding 1 — monotone on all eight, no islands.** The binary search is sound.
Design Decision 8 stands; nothing to amend.

**Finding 2 — the real ceilings are lower than the design's synthetics, and two
of the four real parts refuse every angle.** `enclosure_base` (the canonical
shelled enclosure) caps at **0.25°**, not the 2° the synthetic shelled box
suggested. `nozzle` (revolved cone + cylinder) and `angle_bracket` (a filleted
L) fail at 0.25° and everywhere above. So "the fallback is the feature" is
right, and it needs a rung below it: when *nothing* works, the helper returns
the part **unchanged** and says so, rather than an undrafted part that looks
drafted.

**Finding 3 — the failure is usually silent, and this changed the code.**
`scratchpad/spike_draft_kind.py` classified every failing angle:

| part | angle | how it failed | `is_valid` | volume it handed back |
|---|---:|---|---|---:|
| box + boss | 17.5 – 35 | **returned a shape** | `False` | 16444.35 … 13163.18 mm³ |
| box + boss | 40 | raised `OCP.OCP.Standard.Standard_Failure` | — | — |
| `enclosure_base` | 0.5 / 1 / 2 | **returned a shape** | `False` | 35980.72 / 32421.18 / 26825.69 mm³ |
| `enclosure_base` | 3 | raised `Standard_Failure` | — | — |
| `gusset_plate` | 20 / 25 | **returned a shape** | `False` | 293438.22 / 285265.73 mm³ |
| `angle_bracket` | 0.25 / 1 | raised `build123d…DraftAngleError` | — | — |

Only the extreme angles raise. Everywhere else `draft()` succeeds, returns one
solid with a plausible positive volume, and `is_valid` is `False` — the
misplaced-cut class of failure, in a new place. Every attempt in the helper is
therefore validated, and a test asserts the raw build123d call really does
behave this way so the validation cannot be mistaken for belt-and-braces.

**Finding 4 — the two failure signatures, for the Error Doctor.** Confirmed
through the worker: `OCP.OCP.Standard.Standard_Failure` with `str(exc) == ""`
(length **0**), raised from `build123d/topology/three_d.py:1836` inside
`operations_part.py:97`'s `draft`; and `build123d.topology.three_d.
DraftAngleError: Draft operation failed. Use \`err.face\` and
\`err.problematic_shape\` for more information.` (88 chars) on `nozzle` and
`angle_bracket`. **The doctor entry was possible**: with an empty message the
only text there is is the traceback, and the traceback contains the operation,
so the regex is `(?s)^(?=.*(?:Standard_Failure|DraftAngleError))(?=.*[Dd]raft)`
— the two-lookahead form the module already uses for `spline_degenerate_points`
and `primitive_nonpositive_dimension`, and it is placed with the other specific
`Standard_Failure` entries, ahead of the generic ones. Both signatures are
triggered for real in the tests, per `error_doctor.py`'s standing rule.

## What shipped

- `features.draft(part, faces, angle_deg, neutral_plane, *, min_angle=0.25,
  rel_tol=0.02) -> (part, achieved_deg, warning|None)`.
  - `faces` is a list of `Face` objects **or a selector callable**
    `f(part) -> faces` — never indices, which renumber on any topology change
    (design Decision 3). An index list raises a `ValueError` that says so and
    shows a `filter_by` example.
  - `attempt()` treats a raise, an invalid result and an empty result alike, so
    finding 3 cannot leak an invalid solid into a rebuild.
  - On failure it binary-searches `[min_angle, angle_deg]` to `rel_tol`, and
    the warning names the requested angle, the face count, the applied angle to
    three decimals, and **what OCCT said** — which for the empty-message case is
    "raised Standard_Failure with no message — OCCT reports nothing about why a
    draft fails". Measured end-to-end in the worker on a plate + rib + boss:
    `requested 5.000 deg on 10 face(s) failed …; applied the largest angle that
    produced a valid solid, 4.703 deg`.
  - When even `min_angle` fails the part is returned **unchanged** with
    `achieved = 0.0` and a warning naming the failing angle, the face count and
    the reason — the `nozzle`/`angle_bracket` case, and the honest-degradation
    requirement of this slice.
  - `@holes.carries_records`, because build123d's `draft` returns a new object
    with none of the original's attributes.
- `DRAFT_CEILINGS` — the measured table as a module constant next to the code
  that reports it, so the docstring's numbers and the sweep cannot drift.
- `error_doctor.py`: `draft_angle_too_large`, quoting the measured ceilings and
  pointing at `features.draft`, and warning that draft can *return* an invalid
  solid without raising.

## Files

- `agentcad/toolkit/features.py` — `draft`, `DRAFT_CEILINGS`, `_check_faces`,
  `_check_angle`
- `agentcad/kernel/error_doctor.py` — the `draft_angle_too_large` entry
- `tests/test_features.py` — 13 more tests (draft + both doctor signatures)
- `docs/part-authoring.md` — the ceilings table and the silent-failure warning
- `docs/changelog/0156-toolkit-draft.md`

## Verification

- `.venv/bin/python -m pytest -q tests/test_features.py tests/test_toolkit.py`
  — **48 passed in 5.69s** (37 in `test_features.py`: 24 for slice 9, 13 for
  slice 10).
- `make test` (the full suite, run in the two chunks this machine uses so the
  examples build does not time out), against the slice-1 baseline of
  **2214 passed, 1 skipped**:
  - chunk A — `.venv/bin/python -m pytest -q -n 4 --dist loadscope tests/
    --ignore=tests/test_examples.py` → **2231 passed, 1 skipped in 341.49s** (re-run on the final tree)
  - chunk B — `.venv/bin/python -m pytest -q -n 2 tests/test_examples.py` →
    **20 passed in 918.89s**
  - total **2251 passed, 1 skipped** = the 2214 baseline + the 37 new tests in
    `tests/test_features.py`. No new skips.
- `make test-fast` — **1921 passed, 1 skipped in 310.69s**.
- The sweep above is `scratchpad/spike_draft_sweep.py` /
  `spike_draft_kind.py`, both through `KernelClient`.

## Notes

- The ceilings are *this geometry's*, not a standard's. `DRAFT_CEILINGS`
  carries the part name with each number for exactly that reason.
- Draft before shelling or filleting. The two parts that refuse every angle are
  a revolved solid and a filleted L — neither is exotic, and neither becomes
  draftable by lowering the angle, which is why the "unchanged" rung exists.
- Not done here (slice 14 owns them): the CHEATSHEET section and the
  `AGENTS.md` gotcha lines. The two facts that belong there are "draft fails
  **monotonically** and caps at 0.25° on `enclosure_base`, with an empty OCCT
  message" and "draft can RETURN an invalid solid without raising".
