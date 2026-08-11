# 0098 — 2026-08-11 — PRD-004 slice 1: the geometry-CI report shape

- **Commit:** pending
- **Date:** 2026-08-11
- **Author:** Claude

## Summary

First slice of PRD-004 (geometry CI): the pure reporting layer that every
later slice is written against. `agentcad/core/checks.py` now defines the
`schema: 1` report — rows, stages, the verdict, the markdown rendering and a
hand-rolled validator — with no kernel, no service, no I/O and no new
dependency. Nothing imports it yet; it is a contract plus its tests.

## Changes

- New `agentcad/core/checks.py`:
  - `REPORT_SCHEMA = 1`, `STAGES = ("build", "assembly", "specs", "drawings")`
    plus the `determinism` pseudo-stage, and the closed enums the validator
    checks (`ITEM_STATUSES`, `STAGE_STATUSES`, `GATE_STATES`, `ITEM_KINDS`,
    `SOURCE_KINDS`).
  - `summarize`, `report_status`, `group_requirements` and `assign_ids` are
    **imported from `core/specs.py`**, not re-implemented — one product, one
    status vocabulary. A test asserts they are literally the same objects.
  - `make_item(stage, kind, subject, status, message, …)` builds one row with
    the id `"<stage>:<subject>"`, de-duplicated through `assign_ids` when the
    caller passes its `seen`/`warnings` accumulators. A `skip` without both a
    `reason` and a `hint` raises `ValueError` (PRD-003's rule, enforced at
    construction); so does an unknown status or kind. `error` carries the tool
    payload verbatim.
  - `make_stage(name, items, …)` summarizes and statuses itself; a `reason`
    means the stage was explicitly skipped (`not_selected`, `no_instances`,
    `not_declared`, `budget_exceeded`, …) and reports `skip`. The specs stage
    may embed its `SpecRunner.run` document whole as `stage["report"]`.
  - `finalize_report(project, stages, …)` flattens every stage's rows into the
    top-level `summary`/`status`, derives `requirements` from the specs stage's
    rows through `group_requirements`, and stamps `exit_code`.
  - `exit_code(report)`: `complete: false` → 2 regardless of status; any
    `fail`/`error` row → 1; `--strict` meeting any `skip` → 1 (via
    `strict_failures`); else 0. `--strict` never rewrites a row — it records
    the ids it counted and moves only the derived `status`/`exit_code`.
  - `render_markdown(report)` (FR8): header (project, source/ref/sha/dirty,
    duration, agentcad version, platform, `fem: yes/no`, strict, exit code),
    the stage table, `## Failures` (message, `error.details.line`, hint),
    `## Skipped` grouped by reason, then warnings and harness errors.
    Rendered failures and skips are capped at `MAX_RENDERED_FAILURES = 50`
    with a `_+N more — see report.json_` line, because
    `$GITHUB_STEP_SUMMARY` is capped at 1 MiB.
  - `validate_report(report) -> list[str]` (AC5): hand-rolled key/type/enum
    checks — required keys, `schema == 1`, every stage name in `STAGES` plus
    `determinism`, unique item ids, every `skip` carrying a reason and a hint,
    `strict_failures` naming rows that exist, the summary's five integer
    counts, the gate `state` vocabulary where one appears. No `jsonschema`
    dependency, by design.
  - `declares_flat_pattern(script)`: `ast.parse` for a module-level
    `flat_pattern` def, falling back on `SyntaxError` to a line-anchored
    `^def flat_pattern(` scan (PRD-003's fail-closed precedent), memoized by
    `sha256(script)` in a bounded dict. It never executes the script.
- New `tests/test_checks.py` — 50 tests over the pure layer: the vocabulary
  identity, item/stage/report construction, every `exit_code` branch, the
  validator's negative cases, the markdown (header, table, failures with line
  and hint, grouped skips, the 50-item cap, the incomplete and strict notes),
  the `flat_pattern` presence scan (nested defs, unparseable scripts,
  memoization, never executing), and an `integration`/`portability` probe that
  imports `agentcad.core.checks` with `OCP`/`build123d` blocked at
  `sys.meta_path`.

## Files

- `agentcad/core/checks.py` — new; the pure report layer
- `tests/test_checks.py` — new; 50 unit tests
- `docs/changelog/0098-check-report-shape.md` — this entry

## Notes

- Naming: per-stage rows are **`items`**, never `checks` — `checks` already
  means the built-in gate name, `report["checks"]` in a spec report, and the
  proposals UI tab. `status` is the four-value row status; `state` is the
  gate's, and the validator rejects one used as the other.
- Report-honest by policy (design Decision 6): a `skip` row stays a `skip`
  with its reason and hint even under `--strict`, which is the opposite of
  PRD-003's unconditionally fail-closed `specs` gate — different audiences,
  different questions, one set of measurements.
- A spec `error` exits **1, not 2**: it is a fact about the model, and a caller
  must be able to tell "read the report and fix the design" from "fix the
  environment".
- Slice 2 consumes `make_item` / `make_stage` / `finalize_report` /
  `exit_code` / `declares_flat_pattern`; `render_markdown` and
  `validate_report` are what the CLI (slice 4) and the docs (slice 8) use.
- Verification: `uv run pytest -q tests/test_checks.py` → 50 passed;
  `uv run python -c "import agentcad.core.checks, sys; assert 'OCP' not in
  sys.modules"` → clean; `make test-fast` → 777 passed, 1 skipped (198 s);
  `make test` → **964 passed, 1 skipped** in 1259.84 s, against the 914-passed
  baseline of 0097 — exactly the 50 tests this slice adds, and no existing
  test file was touched.
