# 0170 — 2026-08-16 — PRD-011 slice 4: the publish gate, part A — the cell, the ephemeral service, the data stages

- **Commit:** pending
- **Date:** 2026-08-16
- **Author:** Claude (Opus 5)

## Summary

`agentcad/core/packages/gate.py` opens with the part that can damage a user:
containment. A gate run materialises into
`<work-dir>/agentcad-package-<pid>-<rand>/`, drives a **second, ephemeral
`AgentCADService`** rooted there over the same warm kernel with all three seams
nulled, and deletes only the cell it made. On that scaffold it implements the
three data stages — `format`, `contract`, `presets` — while the other six are
`skip`/`not_implemented` rows, so the seam is real and not aspirational. The
report is PRD-004's document with three additions (`package`, `note`, the
verdict), and the test that says so runs `checks.validate_report` over a real
gate report and asserts the only problems are the stage-name vocabulary.

## Changes

- `agentcad/core/packages/gate.py` (new)
  - `GATE_STAGES` (nine, closed), `IMPLEMENTED_STAGES` (three, this slice),
    `PUBLISH_SKIP_EXEMPT` (the closed set of five world-facts),
    `STAGE_SKIP_EXEMPT`, `SECURITY_NOTE`, `GATE_PROJECT`, `MIN_README_CHARS`,
    `NUMERIC_REQUIRED`.
  - `_ephemeral_service(cell, kernel)` — `AgentCADService(cell, …)` →
    `bus.on_publish = None` → `create_project("pkg_gate")` → `build_registry`
    → `store.branch_resolver = None` → `store.write_guard = None`, in that
    order because `build_registry` installs the last two. The docstring names
    each seam and the failure it prevents, and records that **unlike PRD-004's
    runner this one really writes** (`create_part`/`set_params`, dozens per
    run), so the write guard is live here and nulling it is load-bearing —
    the future `checks._ephemeral_service` predicted.
  - `PackageGate(service)` — **stateless**: `run()` builds a `_Run` that holds
    the deadline, the accumulators and the ephemeral service, so one gate can
    serve two callers without sharing a verdict. Nothing is read off the
    service at construction (`pac` loads before `p`, `s` and `v`).
  - `PackageGate.run(path, *, stages, strict, jobs, work_dir, budget_s)` —
    validates the stage subset, the budget (via `checks._finite`, the one
    place that refuses a NaN limit) and `jobs`; refuses an overlapping
    `--work-dir`; `mkdtemp`s the cell; measures; tears down in a `finally`;
    shapes the report.
  - `_refuse_overlap` — `checks._refuse_overlap` plus one path: the **package
    source directory**. A cell inside the package would change the very
    content id the gate is attesting to, and the teardown would delete part of
    the package.
  - Stage `format`: `validate_package_manifest(doc, root=…)`, the inventory
    and the published ceilings, `docs/README.md` present and non-trivial,
    `previews/*.png` present, one row per declared part file that exists.
  - Stage `contract`: one `kernel.request("inspect", …)` per part, then the
    **package standard** — every `number`/`int` parameter declares `min`,
    `max`, `unit` and `description`, or the row fails naming the parameter,
    the missing keys and why ("the gate's claim … is vacuous without it").
  - Stage `presets`: `validate_presets`, then per configuration both
    `validate_configuration(entry, inspected_spec)` **and** an application
    through `service.set_params` on a scratch part, with the overrides cleared
    between configurations.
  - `verdict(stages)` — pure, fail-closed: `{publishable, exempt_skips,
    blockers}`.
  - `validate_gate_report(report)` — `checks.validate_report` minus exactly
    the unknown-stage-name problems for names this module declares, plus the
    gate's own stage-name rule and its three keys.
  - `_host(kernel)` — `checks._host`'s fields plus **`build123d`**, read from
    the `ping` handler: a package's real compatibility surface is the pinned
    kernel stack, and only the kernel knows it (design Decision 2). Slice 8's
    index entry records it.
- `tests/test_packages_gate.py` (new, `slow`) — 65 tests.
- `tests/test_packages_ocp_free.py` — a probe for `gate`.

## Files

- `agentcad/core/packages/gate.py` — new
- `tests/test_packages_gate.py` — new
- `tests/test_packages_ocp_free.py` — one probe added

## Divergences from the plan, and why

- **The gate report is not accepted by `checks.validate_report`, and cannot
  be.** That function checks `stage.name in checks.ALL_STAGES` (`build`,
  `assembly`, `specs`, `drawings`, `determinism`); seven of the nine gate
  stages are not in it, and `checks.py` may not be edited. So `gate.py` ships
  **`validate_gate_report`**, which runs PRD-004's validator and subtracts
  **exactly** the unknown-stage-name problems it raises for names
  `GATE_STAGES` declares — by exact message, not by pattern, so a reworded
  PRD-004 problem turns
  `test_the_report_is_a_prd004_report_apart_from_its_stage_names` red instead
  of silently dropping something. That test is also the proof of the claim:
  the *only* complaints `checks.validate_report` has about a real gate report
  are the stage names.
- **A `not_implemented` (or `not_selected`, or `budget_exceeded`) stage blocks
  `publishable`.** The plan derives the verdict from rows alone. A stage that
  produced no rows because nobody measured it would then be invisible to the
  verdict, and `validate --stages format` would answer "publishable" — the
  "validated as a badge" failure the design spec exists to prevent. So a
  stage-level skip blocks unless its reason is in `STAGE_SKIP_EXEMPT`
  (`no_presets_declared` — the package legitimately has nothing there).
  **Consequence for this slice: a green package is `publishable: false` with
  the six unmeasured stages named in `blockers`.** Slice 5 implements them and
  it flips; slice 8 is the first caller that reads the field.
- **The `presets` stage validates twice.** The design spec says validation is
  the *application* through `set_params` and not a private normaliser. That is
  necessary but not sufficient: `set_params` stores a numeric value **raw**
  and the worker clamps it at build (`service.set_params`' own docstring), so
  a preset of `size: 400` on a parameter whose max is 40 applies quietly. The
  gate therefore also runs slice 1's `validate_configuration` against the
  **inspected** spec — which is exactly the `params_spec` argument slice 1
  shipped for this — and a test
  (`test_a_preset_above_the_declared_max_fails_even_though_a_build_clamps`)
  pins it.
- **A part contributes one contract row, not two.** The plan asks for "PARAMS
  present" and "`build` present" rows. `handle_inspect` answers both in one
  call and raises on the first failure, so a second row could only be invented
  here. The single row carries the kernel's own message verbatim ("script must
  define a PARAMS dict") and its `error` payload.
- **Overrides are cleared between configurations.** `set_params` merges, so
  validating preset B after preset A would validate B *on top of* A's values.
  Not in the plan; `test_one_preset_does_not_leak_its_parameters_into_the_next`
  is the test that would catch its absence.

## Measurement

`MIN_README_CHARS = 200` is the only new threshold. The smallest README this
repository ships is `examples/prototyping/README.md` at **3 061 bytes** (the
others: 3 259, 4 466, 5 220, 6 864), so the floor sits ~15x below real content
and refuses only a `# name` stub.

## Verification

Targeted:

```
.venv/bin/python -m pytest -q tests/test_packages_gate.py tests/test_packages_ocp_free.py
106 passed in 6.98s
```

(`tests/test_packages_gate.py` holds this slice's 65 tests and slice 5's; the
two were verified as one run, on the 0167–0169 precedent.)

OCP-free, in a fresh interpreter with `OCP`/build123d blocked at
`sys.meta_path` (`tests/test_packages_ocp_free.py`, probe
`agentcad.core.packages.gate`) — passing above.

Full suite, with PRD-011 slices 4–6 in the tree:

```
.venv/bin/python -m pytest -q -n 2 --dist loadscope -rs
2885 passed, 1 skipped in 25:06
```

The baseline after slices 1–3 was **2763 passed, 1 skipped** (changelogs
0167–0169); slices 4–6 add **122** tests (97 gate + 24 CLI + 1 OCP-free
probe). `make test` is that command (`test-full`). The single skip is
pre-existing and explained — `tests/test_analysis.py:166: agentcad[fem]
installed; the 501 fallback is unreachable`. The number is cited in all three
of this sequence's entries because the three slices were built and verified as
one run; nothing between them changes the count.

## Notes

- **What the containment tests attack:** the projects root is hashed with
  `content.content_id` before and after a run and compared; a caller's
  `--work-dir` keeps a file it already held; a run with no `--work-dir` leaves
  its temp root empty; a `--work-dir` that **is**, **holds** or **sits inside**
  either the projects root or the package directory is refused with both paths
  named (six parametrised cases) — including one that reaches the projects
  root through a **symlink**, because both sides are resolved before they are
  compared; a refused path is never created; the ephemeral service ends with
  all three seams `None`, is rooted in the cell, shares the caller's kernel,
  and knows exactly one project.
- **A part file that escapes the package is refused lexically** — the row
  names `../evil.py` and the gate never opens it, let alone inspects it.
- The gate holds no run state: `vars(PackageGate(service)) == {"_service":
  service}` is a test, and two runs over two copies of one tree produce
  row-for-row identical reports.
- A stage that raises becomes **one `error` row plus a `report.errors[]`
  entry**, and the stages after it still run — PRD-004's rule.
- `jobs` is accepted and validated in this slice and used in slice 5; a
  non-positive or non-integer value is a `validation_error` here, before a
  kernel starts.
