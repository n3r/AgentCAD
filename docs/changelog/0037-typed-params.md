# 0037 — Typed PARAMS: bool, enum, string, and int parameters

- **Commit:** pending
- **Date:** 2026-08-09
- **Author:** Claude (with Nikita Fedorov)

## Summary

PARAMS entries gain an optional `type` field — `number` (default), `int`,
`bool`, `enum` (with `choices`), `string` (with `max_len`) — so parts can
expose feature toggles, named configurations, and text parameters, not just
numbers. Validation is enforced in both layers (kernel worker and service),
the inspector renders type-appropriate controls, and the manifest can never be
poisoned by a wrong-typed value (roadmap "Non-numeric parameters").

## Changes

- **Kernel contract** (`worker.py`): `_validate_params_spec` branches per
  type — enum requires non-empty `choices` with the default a member; bool
  defaults must be real bools; string defaults are length-checked against
  `max_len` (default 200); `min`/`max` are legal only on number/int, `choices`
  only on enum, `max_len` only on string (explicit `None` means absent — the
  worker's own `handle_inspect` output round-trips as a valid PARAMS spec).
  `_resolve_params` type-checks overrides: numeric out-of-range values still
  clamp with a warning; int coerces integral floats (3.0 → 3) and rejects
  fractions; enum overrides canonicalize to the *matched declared choice*
  (3.0 resolves to declared int 3 — a raw float can never reach `build(p)` or
  split the shape-cache key), with bools rejected first since `True == 1`.
  `handle_inspect` emits `type` always, plus `choices`/`max_len` when present.
- **Service** (`service.py`): `set_params` pre-checks values are JSON scalars,
  validates against the inspected spec before anything is written, and
  normalizes (number→float, int→int, enum→declared choice, bool/str as-is), so
  a bad value never touches the manifest. `ProjectStore` stores params as
  native JSON scalars (the old unconditional `float()` coercions are gone).
- **Inspector UI** (`inspector.js`): checkbox for bool, `<select>` for enum
  (choices kept typed via an index map; a stale non-member override renders a
  disabled "(not in choices)" placeholder instead of silently showing the
  first choice), text input for string, step=1 for int (fractional values are
  never PATCHed; blur rounds). Pending edits are flushed synchronously when
  switching parts so a blur-commit is never dropped; checkbox/select sync is
  no longer blocked by lingering focus (the activeElement guard now applies
  only to text/number/range).
- **Docs/templates**: part-authoring contract, agent-api `set_params` row,
  CHEATSHEET §1, and the chat SYSTEM_PROMPT sentence updated; example tests
  branch per type (bool sweeps True/False, enum sweeps every choice).

## Files

- `agentcad/kernel/worker.py` — typed spec validation/resolution/inspect
- `agentcad/core/service.py` — typed `set_params` validation + normalization
- `agentcad/core/project.py` — JSON-scalar params in store (no float coercion)
- `agentcad/core/model.py` — `PartRecord.params`/`ParamSpec` typing
- `agentcad/core/tools.py` — set_params tool description
- `agentcad/core/templates.py` — CHEATSHEET typed-params section
- `agentcad/agent/chat.py` — SYSTEM_PROMPT wording
- `frontend/js/inspector.js`, `frontend/css/app.css` — typed controls
- `tests/conftest.py` — shared `TYPED_SCRIPT` / `NUMERIC_ENUM_SCRIPT`
- `tests/test_kernel.py`, `tests/test_service.py`, `tests/test_examples.py`
- `docs/part-authoring.md`, `docs/agent-api.md`

## Notes

Adversarial review (3 reviewers + per-finding verification) confirmed six
defects in the initial implementation — enum canonicalization in both layers,
inspect-output round-trip with explicit `None`s, and four inspector
interaction bugs — all fixed here with regression tests. Cache keys already
JSON-serialize params, so no key-format change; int params stored as JSON
ints do produce a distinct key from a float of equal value, which only
affects newly-typed params. Numeric-only scripts behave byte-identically.
