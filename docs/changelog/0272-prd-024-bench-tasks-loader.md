# 0272 — PRD-024 slice 1: the bench task format, its loader, the authoring helper, and the first task

- **Commit:** pending
- **Date:** 2026-08-19
- **Author:** Claude (Task 1 of the PRD-024 plan)

## Summary
First code slice of AgentCAD-Bench: a new OCP-free package `agentcad/bench/`
holding deterministic JSON I/O, the schema-versioned task format and its pure
validator, plus a developer-only authoring helper — and one complete seed task
(`model_from_drawing/mfd_001_spacer_plate`) under a new top-level `benchmarks/`
data tree. No model-facing surface is added: no tool, no route, no event, no
manifest key, and no edit to `worker.py` / `tools.py` / `app.py` / `service.py`.

## Changes
- **`agentcad/bench/__init__.py`** — `HARNESS_VERSION = 1`, the scorer's own
  version. Two scores are comparable only when `(task_set, task_version,
  harness)` agree, so this is bumped whenever a subscore's computation changes,
  independently of `TASK_SCHEMA`.
- **`agentcad/bench/_json.py`** — one module owns byte-identity (FR6/AC3):
  `round_floats` (recursive, six places, bools left alone because
  `isinstance(True, int)`), `canonical_json` (`sort_keys`, `indent=2`,
  `allow_nan=False`, trailing newline), `write_json` (through
  `ProjectStore._atomic_write`, the random-staging-name writer) and `read_json`,
  which **refuses by size before parsing** and catches `RecursionError`
  alongside `ValueError` — the `core/packages/_json.py` trap restated here
  because `RecursionError` is not a `ValueError` and a bench reader consumes
  documents authored elsewhere.
- **`agentcad/bench/tasks.py`** — the format. Constants (`TASK_SCHEMA`,
  `METRICS_SCHEMA`, `MAX_ROTATIONS = 8`, `CATEGORIES`, `SUBSCORES`,
  `ALIGN_MODES`, `METRIC_KEYS`, `ASSET_SUFFIXES`, `STEP_SUFFIXES`), the frozen
  dataclasses `Frame` / `Budgets` / `MetricWindow` / `Task`, and:
  - `task_problems(raw, base)` — **pure, never raises**, returns human
    sentences in the style of `manifest_merge.config_problems`, running the ten
    checks of design §2 cheapest-first. That shape is what lets the CI
    self-test ask "does every shipped task validate?" without constructing
    anything, and what lets an author see every defect in one run.
  - `load_task` / `load_tasks` (sorted by id, filtered by `fnmatch` over the
    task id and by `sets` membership), `load_windows`, `prompt_text` (the
    prompt plus every asset inlined as fenced text), `tasks_root()` via
    `_resources.resource_root()` — the `examples/` and `catalog/` treatment, so
    no `pyproject.toml` change is needed and `benchmarks/` stays out of the
    wheel.
  - Two rules worth naming: `budgets.turns` is capped at
    **`MAX_TOOL_CALLS_PER_TURN`, imported from `agentcad.agent.chat`**, so
    raising the product's per-turn ceiling automatically raises what a task may
    declare and no continuation logic ever has to exist; and a declared path is
    accepted only after being **resolved and re-checked with `checks._within`**,
    so a `..`-escape is refused by where it lands rather than by how it is
    spelled.
  - `check_fem_static` is refused **by name** in a rubric block: the core suite
    stays green without the `[fem]` extra (FR3). So is `SPECS +=`
    (`SPECS_AUGMENTED_RE`, line-anchored): `specs.declares_specs` accepts `+=`
    because it is a legitimate binding for a *part script*, but a rubric block
    is appended to the **candidate's** script, so `+=` extends whatever the
    candidate declared instead of replacing it — which is exactly the hole the
    "re-bind, never append" rule exists to close, and the loader's own message
    already claimed to enforce it.
  - A `metrics` weight above zero is refused **by window count**, not only by
    the presence of `reference.metrics` (design §2 rule 8). A document that
    parses with `"windows": []` is the same defect as no document at all — a
    scored subscore with nothing to measure — and it is the shape an author
    reaches by deleting a window rather than by forgetting a file.
    `_metrics_problems` returns `(problems, window_count)` so the caller, which
    is the only place that knows the weight, decides whether zero is a defect.
  - A reference datum must be a **STEP** (`.step`/`.stp`), never a mesh — the
    IoU side has to be boolean-capable and a mesh side segfaults OCCT, so the
    loader refuses it before anything spawns.
- **`agentcad/bench/author.py`** — a developer helper, run as
  `uv run python -m agentcad.bench.author {step,metrics} <task-dir>`, and
  deliberately **not** an `agentcad` subcommand (it writes into the repository;
  every `agentcad` subcommand that writes writes into a user's project). It
  **copies** `reference/project` into a throwaway projects root before building:
  opening it in place would scatter `.cache/` and `exports/` through
  `benchmarks/`, and the confined worker cannot write there anyway — which is
  how the in-place first version announced itself (`RuntimeError: Failed to
  write STEP file`). `seed_metrics` emits a **starting point** (±1% on mass and
  volume, ±0.05 mm per bbox extent, `n_solids` pinned) the author then
  hand-edits and argues in the PR, never a generated rubric nobody read.
- **`benchmarks/tasks/model_from_drawing/mfd_001_spacer_plate/`** — the seed
  bundle: `task.json`, `prompt.md` (which names the material — aluminium 6061 —
  so the `mass_g` window is fair to an agent that would otherwise have to guess),
  a hand-authored three-view `assets/drawing.svg` (plain `<svg>`/`<rect>`/`<line>`/`<circle>`/`<text>`, no external
  font, no script), `reference/project/` (manifest + build123d script),
  `reference/steps/spacer_plate.step` (54 439 B, `ISO-10303-21;`),
  `reference/metrics.json` (four hand-edited windows) and
  `specs/parts/spacer_plate.py`.
- **`tests/test_bench_tasks.py`** — 19 tests over the loader: the seed resolves
  fully, every shipped task has zero problems, each of seven mutations is named
  by its own sentence, the rubric must bind `SPECS`, may not augment it with
  `+=` and may not use `check_fem_static`, a weighted `metrics` subscore needs
  at least one window, `load_task` carries the problem list, `load_tasks`
  filters (by *membership*, so mfd_002..005 landing later does not break it),
  `prompt_text` inlines the SVG, `canonical_json` is stable and refuses NaN,
  `round_floats` leaves bools alone, and `read_json` refuses by size before
  parsing and catches `RecursionError` (pinned to the recursion path: the
  fixture is 200 kB, far under the size ceiling). Nothing here starts a kernel;
  every mutation runs on a `tmp_path` copy, so `benchmarks/` stays a read-only
  input.

## Files
- `agentcad/bench/__init__.py` — new; `HARNESS_VERSION`.
- `agentcad/bench/_json.py` — new; canonical/deterministic JSON in and out.
- `agentcad/bench/tasks.py` — new; the format, the validator, the loader.
- `agentcad/bench/author.py` — new; the authoring helper.
- `benchmarks/tasks/model_from_drawing/mfd_001_spacer_plate/**` — new; the seed
  task bundle (8 files).
- `tests/test_bench_tasks.py` — new; 19 tests.
- `docs/changelog/0272-prd-024-bench-tasks-loader.md` — this entry.

## Review fixes folded in (round 1, on top of 19388a7)
- **`assets/drawing.svg`: the right view drew a 20 mm hole pitch, not 30 mm.**
  The view is 50 mm wide centred on x = 320 px, so the hole centres belong at
  290 and 350 (±15 mm at 2 px/mm); they were at 300 and 340. The four hidden
  lines move 294/306/334/346 → 284/296/344/356, and both edge-on hole groups
  now carry a comment stating the pitch they encode so the next reader can
  check the arithmetic without deriving it. Every other primitive was
  re-derived from the file and agrees with `prompt.md` and the reference
  script: top view 80 × 50 with rx = 5 mm and four Ø6 holes at (±30, ±15),
  front view 80 × 6, right view 50 × 6, and the 80 / 50 / 60 / 30 / 6
  dimension lines span exactly those distances.
- **The `metrics`-weight rule and the `SPECS +=` refusal**, both described in
  the Changes section above.
- The three minors: the material sentence in `prompt.md`, `load_tasks`'
  membership assertion, and the `_json` asserts.

## Notes
- **Why the rubric is separate from the reference.** The reference part script
  carries **no** `SPECS` of its own; `specs/parts/spacer_plate.py` is injected
  into every candidate, the reference included. If the reference declared its
  own checks it could pass against something no other submission is measured
  against — and because the block is *appended* to the candidate's script, the
  block must **re-bind** `SPECS` rather than `+=` it (the last module-level
  binding wins, so an agent cannot inflate the `specs` subscore with
  trivially-true checks of its own). Every constructor is imported under a
  `_bench_` alias, because the candidate's module namespace is in scope at that
  point.
- **Why `turns` is capped at `MAX_TOOL_CALLS_PER_TURN`.** `ChatEngine` breaks a
  turn at 30 tool calls (`chat.py:50`). A task that declared more would be
  stopped by the *product's* limit rather than by its own budget, which makes
  the number meaningless and invites continuation logic. Importing the constant
  rather than copying it means the ceiling moves in one place — and if 30 turns
  out to be too tight, that is a **product** finding raised in `chat.py`, and
  the bench then measures the change.
- **The metric windows are hand-edited, and the numbers are argued.** Measured
  reference: volume 23 192.65 mm³, mass 62.62 g (al6061, which the prompt now
  names outright — it is also the product's `DEFAULT_MATERIAL`, so an agent that
  chooses nothing lands on it anyway, but a scored mass window should not depend
  on that coincidence).
  `material` is a volume **ceiling** of 23 250 mm³, chosen deliberately below
  the 23 321 mm³ a plate with **square** corners would measure, so a missing R5
  is caught by the window rather than only by IoU. `height` is 5.95–6.05,
  `mass` 61.4–63.9 g (±2%), `solids` pinned at 1.
- **The seed's rubric was verified against the reference**, out of band: with
  the block appended to the reference script, `SpecRunner.run` reports
  `spacer_plate:valid`, `:ligament` and `:envelope` all passing. That derisks
  AC1 ("scoring the reference returns 1.0") before the scorer exists.
- The design spec's illustrative `metrics.json` in §1.3 carries numbers
  (mass 118–132 g, volume ≤ 22 100 mm³) that do **not** contain this reference
  — they were written before the material was fixed. The shipped windows use
  the measured values; the four window *names* are the spec's.
- `author.py` calls `cli._build_service(scratch)` with no `examples` keyword;
  Task 4 adds `examples: bool = True` to that signature and updates this one
  call site.
- **Follow-ups (later tasks, not gaps here):** the kernel `iou` handler,
  the scorer, the `bench` CLI, and the test that proves `agentcad/bench/**`
  imports neither `OCP` nor `build123d` (Task 11). `.dockerignore` gains
  `benchmarks/` in a later slice.

## Verification
```
$ uv run pytest -q tests/test_bench_tasks.py
...................                                                      [100%]
19 passed in 1.12s

$ uv run pytest -q tests/test_bench_tasks.py tests/test_checks.py tests/test_specs.py
134 passed, 2 skipped in 34.50s

$ uv run ruff check agentcad/bench tests/test_bench_tasks.py
All checks passed!
```
(`ruff format` is not this project's style — it would reformat
`core/checks.py` and `core/specs.py` too, and the repo ships no ruff config.)

`make test` — 4702 passed, 36 skipped (measured at branch tip 1ae80d1, all slices landed)
