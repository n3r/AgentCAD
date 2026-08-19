# 0262 — PRD-024: `bench report` aggregation, the baseline gate, and `benchmarks/baseline.json`

- **Commit:** pending
- **Date:** 2026-08-19
- **Author:** Claude (Task 6 of the PRD-024 plan)

## Summary
Adds `agentcad/bench/report.py` — the pure, OCP-free aggregator over a results
directory of `score.json` / `run.json` documents — plus the shipped
`benchmarks/baseline.json` with `total: null`, and `tests/test_bench_report.py`.
This is FR9's report half and the whole of FR11's release gate (design
Decisions 10 and 11). Nothing here starts a service, touches the kernel, opens
a socket or reads a clock.

## Changes
- **`agentcad/bench/report.py`** (new): `REPORT_SCHEMA = 1`,
  `BASELINE_SCHEMA = 1`, `HEADER_SCHEMA = 1`, `SCORE_SCHEMA = 1`,
  `MAX_RENDERED_TASKS = 25`, and four functions:
  - `aggregate(results_dir, *, tasks_root=None, expected=None)` walks
    `<results>/tasks/<category>/<id>/score.json`, reads `over_budget` (and only
    that) from the sibling `run.json`, and returns the document of design §10.
    **A category total is the unweighted mean of its tasks' totals; the overall
    total is the unweighted mean of the category totals** — at 5-per-category
    the two coincide, and saying it this way means a v2 that adds tasks to one
    category cannot silently reweight the headline number.
  - **A missing task is a row with `total: 0.0`, `missing: true`,** counted in
    its category's `missing`. Without that rule a release could be gated green
    by not running the hard half.
  - `compare_baseline(report, baseline, epsilon, *, path=None)` →
    `{"path", "epsilon", "status", "regressions", "task_deltas"}` with
    `status ∈ {ok, regressed, incomparable, unrecorded}`. **It gates on `total`
    and on each category only.** A category the baseline names and the run
    never produced measures `0.0` — the missing-task rule one level up.
  - **Per-task deltas are computed, sorted worst-first, rendered — and never
    gated.** A single task under a stochastic agent is noise; gating on noise
    makes the release gate a coin flip. This is the one place the design
    deliberately measures more than it enforces.
  - `report_exit_code(report)` → `2` incomparable · `1` regressed · else `0`.
    `2` keeps the geometry-CI meaning: *we could not produce a verdict*. A
    baseline from another `(task_set, harness)` is exactly that — not a pass,
    and not a failure of the model.
  - `render_markdown(report)` in `checks.render_markdown`'s idiom
    (`checks.py:602-671`): a facts line, the category table, the baseline
    section with every regression named (baseline / measured / delta), then the
    worst task rows capped at `MAX_RENDERED_TASKS` with `_capped`/`_more`'s
    `+N more` line (`checks.py:594-600`), then the warnings.
- **`benchmarks/baseline.json`** (new): the v1 baseline, `total: null`,
  `recorded`/`source` null, with the `note` field saying so. A null baseline is
  `unrecorded` and exits **0** with the reason printed.
- **`tests/test_bench_report.py`** (new, 17 tests): the mean-of-category-means
  rule, the missing-task rule via an explicit `expected` list and via a
  `tasks_root` (a `shutil.copytree` of `benchmarks/tasks` into `tmp_path` —
  `benchmarks/` is read, never written), harness-mismatched score rows included
  with a warning, an unreadable `score.json` raising, byte-identity across two
  aggregations, the regression / inside-epsilon / missing-category / per-task
  cases, the non-finite epsilon refusal, the shipped baseline's exit 0, and the
  three markdown assertions (table, cap, regression names).

## Files
- `agentcad/bench/report.py` — new module (aggregate, gate, exit code, markdown)
- `benchmarks/baseline.json` — new, unrecorded v1 baseline
- `tests/test_bench_report.py` — new test module
- `docs/changelog/0262-prd-024-bench-report.md` — this entry

## Notes
- **Ordering inside `compare_baseline` is load-bearing.** Foreign *schema* →
  `incomparable` (we cannot read what it claims). Then `total is None` →
  `unrecorded`, which deliberately **outranks** a `(task_set, harness)`
  mismatch: with no number recorded there is nothing to be incomparable about,
  and the shipped `baseline.json` must keep exiting 0 across a harness bump
  rather than turning CI red before a single number exists. Only once a number
  is recorded does a `(task_set, harness)` mismatch become exit 2.
- **`expected` defaults to the tasks root only when one is given.** The plan's
  interface line said it defaults to `[t.id for t in load_tasks(root=tasks_root)]`
  unconditionally, but its own test calls `aggregate(results)` with no root and
  asserts `n == 3` — with the shipped root that would be 4 rows and a bogus
  missing. With neither `expected` nor `tasks_root`, the ids found on disk are
  the denominator: a hand-assembled directory (one `bench score` output, a
  downloaded submission) has no roster to be measured against, and inventing
  one out of `benchmarks/tasks` would turn "score this submission" into "score
  this submission against 25 tasks it never claimed". The `bench run` path
  passes the root it ran, so the honest denominator is kept exactly where it is
  known.
- **Comparability is checked against the pair the header actually declares.**
  Design §6's trio is `(task_set, task_version, harness)`, but `task_version`
  is a property of a *task* and `bench.json` carries no single value for it, so
  `aggregate` compares `task_set` and `harness` and prints the row's
  `task_version` inside the warning sentence. The row is included either way —
  scores from different harness versions are never *silently* averaged.
- **The directory name is the task's identity**, not `score.json`'s `task` /
  `category` fields: it is the key `expected` is stated in, and it is the one
  thing a mislabelled score cannot lie about. A disagreement is a warning.
- **`run.json` may be absent** (a standalone `bench score` writes no run) —
  `over_budget` is then `false`, no warning. A *present* but unparseable one
  earns a warning, not the run's verdict: it holds provenance, not measurement.
  An unreadable or foreign-schema `score.json` or `bench.json`, by contrast,
  raises `ValidationError` — that is `bench report`'s half of exit 2.
- **A non-finite `--epsilon` is refused** by `compare_baseline` itself:
  `nan < -nan` is `False`, so a NaN tolerance does not widen the gate, it
  deletes it silently and green. The CLI screens it with `_finite_arg`; the
  gate refuses it too.
- The report body carries **no timestamp, host, path or duration** (the
  packages-provenance rule), every float is rounded to 6 places by
  `_json.round_floats` before it leaves `aggregate`, and both writes go through
  `_json.write_json` → `ProjectStore._atomic_write`. Two aggregations of the
  same directory are byte-identical (tested).
- **`agentcad/bench/cli.py` was NOT touched** — the plan's Task 6 Step 5 asked
  for `_cmd_report`, but `cli.py` is Task 5's file and was being written
  concurrently; wiring `_cmd_report` to these four functions belongs to that
  task's diff. The command path was smoked directly instead (see the report).
- Tests: `uv run pytest -q tests/test_bench_report.py` → **17 passed in 0.29s**;
  `uv run pytest -q tests/test_bench_report.py tests/test_bench_tasks.py -x` →
  **36 passed in 0.26s**. `make test` — <orchestrator fills>.
