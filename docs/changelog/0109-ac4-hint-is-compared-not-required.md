# 0109 — 2026-08-11 — AC4 compares the Error Doctor hint instead of requiring it

## Summary

`tests/test_prd004_acceptance.py::test_ac4_a_script_error_carries_the_update_part_script_payload`
failed on the Linux portability job of PR #11 with `KeyError: 'hint'` while
passing on macOS. The test asserted that a script error always carries
`details.hint`, but the Error Doctor attaches one only when a pattern matches
the OCCT message (`worker._diagnose`: `if hint:`), and OCCT words this
failure differently across platform builds. The assertion was
over-specified; the criterion's actual claim — that the check row and
`update_part_script` hand back the *same* payload — is unchanged and now
covers the hint by comparison.

## Changes

- Dropped `assert payload["details"]["hint"]`; `details.line` (always set for
  a script error) is still required.
- The row-vs-payload comparison uses `.get("hint")` on both sides, so the two
  surfaces must agree whether or not the doctor matched.
- Docstring states that the hint is optional and why.

## Files

- `tests/test_prd004_acceptance.py` — the two assertions and the docstring
- `docs/changelog/0109-ac4-hint-is-compared-not-required.md` — this entry

## Notes

Root cause confirmed before editing: `error_doctor.diagnose` returns `None`
for every plausible `Box(0, 0, 0)` message, and `worker._diagnose` only sets
`details.hint` when the diagnosis is truthy — so an absent hint is designed
behavior, not a regression. Verification: `uv run pytest -q
tests/test_prd004_acceptance.py` → 10 passed.
