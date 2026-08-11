# 0109 — 2026-08-11 — AC4 uses a platform-stable error so the hint half holds

## Summary

`tests/test_prd004_acceptance.py::test_ac4_a_script_error_carries_the_update_part_script_payload`
failed on the Linux portability job of PR #11 with `KeyError: 'hint'` while
passing on macOS. AC4 deliberately proves **both** halves of a script-error
payload — `details.line` and the Error Doctor hint — but its fixture built a
box with a zero dimension, and that failure surfaces as OCCT's C++ message,
whose wording differs between platform builds. No Error Doctor pattern
matched on Linux, so `worker._diagnose` attached no hint (`if hint:`), and
the assertion tripped.

The fixture now triggers a fillet whose rolling ball cannot fit. build123d
raises that from its own pinned Python code
(`ValueError: Failed creating a fillet with radius of ...`), so the text —
and therefore the catalogue match (`fillet_radius_too_large`) — is identical
everywhere. AC4 keeps its full strength: the hint is required, compared
against `update_part_script`'s payload, and asserted to reach the markdown.

## Changes

- `BROKEN_BUILD` builds a box and fillets its edges at 0.9 × the edge length
  instead of constructing a degenerate primitive; the comment records why the
  error's provenance (pinned Python, not OCCT C++) is the point.
- The three hint assertions are unchanged in strength: present on the tool
  payload, equal on the check row, and present in the rendered markdown.

## Files

- `tests/test_prd004_acceptance.py` — the fixture and its comment
- `docs/changelog/0109-ac4-hint-is-compared-not-required.md` — this entry

## Notes

Root cause confirmed before editing rather than inferred: `diagnose()` was
run against four plausible degenerate-box messages and returned `None` for
all of them, while the fillet message matches `fillet_radius_too_large`.
Weakening the assertion to `.get("hint")` was the first attempt and was
rejected on review of the criterion's own intent — AC4 exists to prove the
hint reaches an agent, so the fix belongs in the fixture, not the assertion.

Verification: `uv run pytest -q tests/test_prd004_acceptance.py` → 10 passed.
