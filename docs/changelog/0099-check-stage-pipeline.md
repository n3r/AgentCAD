# 0099 — 2026-08-11 — PRD-004 slice 2: the four-stage check pipeline

- **Commit:** pending
- **Date:** 2026-08-11
- **Author:** Claude

## Summary

Second slice of PRD-004 (geometry CI): `CheckRunner` — the sequencer that
fills slice 1's report shape by driving four surfaces that already exist over
a live service and a working tree. It measures nothing new: every row comes
from `service._ensure_built`, `service._resolved_instances` +
`service.check_interference`, `SpecRunner.run`, or the registered
`generate_drawing` / `flat_pattern` tools, and a failure's `error` is that
surface's payload verbatim. Still no CLI, no `--ref`, no tool and no route
(slices 3–5); still zero kernel imports.

## Changes

- `agentcad/core/checks.py` grows the sequencer half (the pure half is
  unchanged):
  - `CheckRunner(service, registry=None)`. Nothing is captured in `__init__` —
    `service.specs` is read inside `_stage_specs`, because the pack that will
    build this runner (`tools_run_checks.py`, slice 5) loads at `r`, before
    `tools_specs` at `s`.
  - `run(proj, *, ref=None, stages=STAGES, strict=False, budget_s=None,
    min_volume=0.001, verify_determinism=False, sha=None, ref_label=None,
    work_dir=None) -> dict`. `ref` and `verify_determinism` raise
    `NotImplementedError` (slice 3's declared seam, with a test, rather than
    silently measuring the working tree and calling it a ref). An unknown
    stage name is a `ValidationError` naming the four valid ones; an unknown
    project is the store's `NotFoundError`. Both are exit 2 material — the
    only exceptions that leave `run`.
  - **Stage 1 `build`** — one row per manifest part through `_ensure_built`.
    `ok: false` → `fail` carrying the `KernelError.to_payload()` verbatim
    (AC4: the same `details.line` and Error-Doctor `details.hint`
    `update_part_script` returns); a script part whose `metrics.is_valid` is
    false → `fail`; otherwise `pass` with
    `details = {cache_key, volume_mm3, mass_g, n_solids, is_valid, cached}`.
    `cached` is observed *before* the call (the mesh for the part's current
    cache key already on disk), because `_ensure_built` returns the same shape
    either way. Kernel warnings are forwarded to `report.warnings` prefixed
    with the part id.
  - **Stage 2 `assembly`** — `< 2` instances → the whole stage
    `skip`/`no_instances`. `_resolved_instances(timeout_s=remaining)`: an
    unresolvable mate is one `fail` row of kind `mate` carrying the payload (a
    `KernelError` there is an `error` row instead — "we do not know"), never a
    traceback. Then `check_interference(min_volume, timeout_s=remaining)` —
    the **service method**, not the tool, whose schema has no `timeout_s`.
    Each pair is a `fail` row of kind `pair`, subject `"a ↔ b"`, details
    `{a, b, volume_mm3}` (AC2). Each id in `skipped_mesh` is a
    `skip`/`mesh_only` row whose hint says an empty pair list is not proof it
    clears. Instance rows are built *after* the interference call on purpose,
    so a mesh instance has exactly one row rather than a pass and a skip
    fighting over the same id.
  - **Stage 3 `specs`** — `service.specs.run(proj)`, its report embedded whole
    as `stage["report"]` and one item per row preserving `status`, `message`,
    `requirement`, `reason`, `hint` and `error`, with `measured`, `limit`,
    `unit`, `scope`, `part` (and `location`) in `details` (AC3). `declared ==
    0` → `skip`/`not_declared`; no `service.specs` → `skip`/`specs_unavailable`;
    any exception → one `error` row plus a `report.errors[]` entry.
  - **Stage 4 `drawings`** — `generate_drawing` per script part (SVG only: DXF
    is not byte-stable), then `flat_pattern` **only** where
    `declares_flat_pattern(script)` says the script defines one — a part that
    does not is absent, not green. A reference part is `skip`/`not_script`; a
    tool returning `{"error": …}` is a `fail` row with that payload. Without a
    registry the stage is `skip`/`drawings_unavailable`.
  - **The budget** is a deadline on `time.monotonic`, read **before each
    item**: an exhausted budget degrades the rows a stage never reached to
    `skip`/`budget_exceeded`, marks every later stage the same way, and sets
    `report.complete = false` → exit 2 with the completed portion reported
    (FR5). The docstring states the honest overshoot: `_ensure_built` (300 s)
    and the drawing tools (120 s) take no `timeout_s`, so the worst case is
    one in-flight kernel call.
  - **`source`** (working-tree mode): `{kind: "worktree", ref: null, sha: <the
    tree's `.history` head, best effort>, label: --ref-label, host_sha: --sha,
    dirty: <`git status --porcelain` through `history._run`>}`. Every git touch is
    best-effort and never raises; a project with no history is an ordinary
    project. **`host`**: platform, `sys.version`, agentcad version,
    `fem`, `sandbox` (the kernel's own `sandboxed`), `pool_size`, kernel class.
  - A stage that raises something nobody predicted becomes one `error` row plus
    a `report.errors[]` entry with `fatal: true`, and the run continues.
  - New module-private helpers: `_payload` (any exception as the structured
    payload the tools already return), `_elapsed`, `_number`, `_point`, and the
    skip hints `_BUDGET_HINT` / `_MESH_HINT` / `_NOT_SCRIPT_HINT`.
  - New imports: `platform`, `sys`, `..kernel.client.KernelError`,
    `.model.AppError`/`ValidationError`, and `core.specs` as a module (so a
    test flipping `_fem_available` is reflected in `host.fem`). None of them
    pull `OCP` or build123d.
- New `tests/test_checks_pipeline.py` (30 test functions, 32 collected —
  the clean-example test is parametrized over three examples — `integration` +
  `slow`, `timeout(900)`):
  the report shape of a real run, host provenance, clean copies of
  `construction`/`prototyping`/`rocketry` green at exit 0, AC4 (a broken script
  with its line and hint), cached/uncached metrics rows, reference parts, AC2
  (an overlapping pair), per-instance rows, `no_instances`, an unresolvable
  mate, the mesh skip and `--strict` flipping only the verdict, AC3 (a
  tightened limit with measured vs limit), the embedded spec report and
  requirement traceability, `not_declared`, `specs_unavailable`, AC8 (a FEM
  check skipping without the extra, then red under `--strict`), drawing rows,
  the `flat_pattern` presence rule, `not_script`, a failing drawing,
  `drawings_unavailable`, stage selection, the two harness errors, the budget
  (whole-run and mid-stage), and the slice-3 seams.

## Files

- `agentcad/core/checks.py` — grew `CheckRunner` and its helpers (~430 lines);
  the slice-1 pure layer is untouched
- `tests/test_checks_pipeline.py` — new; 30 pipeline tests (32 collected)
- `docs/changelog/0099-check-stage-pipeline.md` — this entry

## Notes

- **Deviation (design Decision 3): an imported part's `is_valid` is reported,
  never enforced.** The design says `ok: true` + `metrics.is_valid is False` →
  `fail`. That holds for script parts. For a **reference** part it does not:
  OCCT calls the shipped `examples/rocketry` STEP import invalid
  (`Compound.is_valid` over 180 solids), which is exactly why
  `tests/test_examples.py` exempts reference parts from that assertion and
  `import_cad_file` merely reports the flag. Failing on it would redden a clean
  bundled example — and the dogfood workflow (slice 7) — over geometry nobody
  in this repo authored. So the row passes, `details.is_valid` carries the
  fact, and a `report.warnings[]` entry names the part and the solid count in
  both renderings.
- Two other named skip reasons the runner adds to slice 1's set:
  `specs_unavailable` (no `service.specs` — the load-order degradation) and
  `drawings_unavailable` (no registry was passed; the plan forbids rebuilding
  one inside the runner).
- The top-level `requirements` map names rows by **this report's** item ids
  (`specs:<part>:<check>`), while `stages[specs].report.requirements` keeps the
  spec report's own ids. Same requirements, same verdicts, two id spaces — the
  check report's ids are the ones that resolve inside it.
- `_resolved_instances` and `check_interference` each resolve the assembly, so
  a mate-bearing project pays the mate pass twice. That is the design's
  sequence (the first call is what turns an unresolvable mate into a named row
  instead of an exception out of the interference call) and both calls are
  under the same deadline.
- Verification: `uv run pytest -q tests/test_checks_pipeline.py
  tests/test_checks.py -p no:randomly` → **82 passed** in 200.31 s;
  `make test-fast` → **777 passed, 1 skipped** in 189.66 s (unchanged from
  0098 — every test here is `slow`); `make test` → **996 passed, 1 skipped**
  in 1367.29 s (0:22:47), against 0098's 964-passed baseline — exactly the 32
  tests this slice adds, and no existing test file was edited.
