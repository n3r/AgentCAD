# 0087 — Design-spec constructors: the declaration vocabulary

- **Commit:** pending
- **Date:** 2026-08-10
- **Author:** Nikita Fedorov

## Summary
Slice 1 of PRD-003 (design specs as executable tests): `agentcad/toolkit/specs.py`,
the ten FR3 constructors that a part script's `SPECS` list (and a project's root
`specs.py`) is built from. Pure data, stdlib only, **zero kernel imports** — so a
`check_fem_static(...)` declares cleanly on a machine with no `[fem]` extra (FR4).
Inert on its own: nothing imports the module until a part script does, so landing
it changes no behavior. The kernel pack that evaluates these declarations is
slice 2; the runner is slice 3.

## Changes
- **New module `agentcad/toolkit/specs.py`** — the interfaces slices 2–7 consume:
  - constants `SPEC_FORMAT = 1`,
    `PART_KINDS = ("valid", "mass", "volume", "bbox", "wall", "that", "fem_static")`,
    `PROJECT_KINDS = ("interference_free", "clearance", "stackup")`, plus
    `AXES = ("x", "y", "z")` and `SIDES = ("min", "max")`;
  - part-scope constructors `check_valid`, `check_mass`, `check_volume`,
    `check_bbox`, `check_wall`, `check_that`, `check_fem_static`;
  - project-scope constructors `check_interference_free`, `check_clearance`,
    `check_stackup`;
  - private validators `_number` / `_positive` / `_non_negative` / `_bounds` /
    `_vec3` / `_identifier` / `_name` / `_requirement` / `_face`, and the
    `_declaration` record builder.
- **One record shape** for every constructor —
  `{"spec": 1, "kind", "scope", "name", "limit", "requirement", "options"}`.
  `limit` is a dict, never a scalar (a two-sided check has two bounds and the key
  says which is which); `options` carries what/how to measure, not the threshold.
  `check_that` additionally carries its callable under `"fn"` — the only
  non-JSON value in the vocabulary.
- **Eager validation is the FR1 mechanism.** `SPECS` is built while the module
  executes, so a bad argument raises *there* and surfaces as a `script_error`
  with `details.line`, exactly like a malformed `PARAMS`. No new error type:
  every validator raises `ValueError` naming the offending argument
  (`check_wall(min_mm="thick")`, `check_mass()` with neither bound,
  `check_mass(min_g=5, max_g=1)`, `check_bbox([1, 2])`,
  `check_that("nope", name="x")`, `check_clearance("a", "a", 1)`,
  `check_stackup(..., axis="w", ...)`). `bool` is rejected as a number even though
  it is an `int` subclass.
- **`check_bbox` normalizes** a scalar or an `[x, y, z]` to
  `{"within_mm": [x, y, z]}`; all numbers are normalized to `float`, `grid` to
  `int`.
- **Default names** are stable and derived: `valid`, `mass_min` / `mass_max` /
  `mass_range` (same for `volume`), `bbox_within`, `wall_min`, `fem_static`,
  `no_interference`, `clearance_<a>_<b>`,
  `stackup_<from>_<to>_<axis>`; `check_that`'s `name` is required. A name may not
  contain `:` — report ids are `"<part_id>:<name>"` / `"project:<name>"` and are
  joined on.
- **`requirement`** is validated as a non-empty string and otherwise stored
  verbatim: an id (`SYS-042`) or a URL, opaque, never parsed or resolved.
- **Two helpers for the evaluators** (a small addition beyond the plan's listed
  names, so the format knowledge has one owner): `is_declaration(value)` — the
  `spec` marker + known kind/scope test slice 2 needs for its structural
  rejection — and `json_safe(declaration)` — a copy with `fn` dropped and
  `"predicate": true` added, which is exactly the declaration shape that crosses
  the JSON-RPC boundary.
- **`agentcad/toolkit/__init__.py`** re-exports `specs` lazily like the other
  submodules (one `__all__` entry, one `__getattr__` branch, one docstring line).
- **New `tests/test_specs_toolkit.py`** — 83 pure-Python tests (no kernel
  fixture, no store, no git): the shared record shape per constructor, scope and
  kind coverage, default names, `requirement` pass-through for an id and a URL,
  limit/option contents, every eager-validation case above, `json.dumps`
  round-trips for every declaration except `check_that`, the `json_safe` /
  `is_declaration` helpers, the lazy package re-export, and an
  `integration`/`portability`-marked subprocess import with a `sys.meta_path`
  finder that raises for `OCP` and `build123d` — the honest form of "this module
  needs no geometry kernel".
- **Stale status metadata fixed** in the same change (the PRD README's rule that
  location, roadmap row and `Status:` move together): the roadmap's PRD-003 row
  now links `prd/in-progress/…` and reads `in progress`, and the PRD's own
  `Status:` line matches. The file had already been moved to
  `docs/prd/in-progress/`.

## Files
- `agentcad/toolkit/specs.py` — new; the ten constructors, validators, and the
  `is_declaration` / `json_safe` boundary helpers
- `agentcad/toolkit/__init__.py` — `specs` added to `__all__`, the lazy
  `__getattr__` branch and the module docstring's import list
- `tests/test_specs_toolkit.py` — new; 83 tests
- `docs/roadmap.md` — PRD-003 row: link and status
- `docs/prd/in-progress/PRD-003-design-specs-executable.md` — `Status:` line
- `docs/superpowers/specs/2026-08-10-executable-design-specs-design.md`,
  `docs/superpowers/plans/2026-08-10-executable-design-specs.md` — design spec
  and implementation plan for the feature

## Notes
- **Two limit-key choices slice 3 must honor:** `check_stackup(..., within=…)`
  stores `{"within_mm": …}` (unit-suffixed like every other limit key, while the
  argument keeps the plan's `within` spelling), and `check_bbox`'s limit is
  always the three-vector `{"within_mm": [x, y, z]}` even when a scalar was
  given.
- **`check_fem_static` requires at least one of `max_vm_mpa` / `max_disp_mm`.**
  FR3 marks both optional individually, but a check with no limit can neither
  pass nor fail; this mirrors `check_mass()`/`check_volume()` rejecting a
  declaration with neither bound.
- The module carries no `advisory` flag and no project-scope `check_that` —
  both are deliberate v1 exclusions from the design spec.
- `make test`: **749 passed, 1 skipped** (baseline 666 passed / 1 skipped, plus
  the 83 new tests — exactly the delta, so no pre-existing test moved).
  `make test-fast`: 665 passed, 1 skipped. No existing test file was edited.
