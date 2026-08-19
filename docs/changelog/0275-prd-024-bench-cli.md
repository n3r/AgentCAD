# 0275 — PRD-024: `agentcad bench` — the CLI, `score`, `report`, `publish`, and the two `cli.py` edits

- **Commit:** pending
- **Date:** 2026-08-19
- **Author:** Claude (PRD-024 Task 4, with Task 6's `_cmd_report` and Task 7's `_cmd_publish` folded in)

## Summary

`agentcad/bench/cli.py` — the whole `agentcad bench` surface in one OCP-free
module: `add_bench_parser` (all four sub-subcommands, so `--help` is honest from
this slice on), `cmd_bench`, `bench_service`, a working `bench score` over the
Task-3 `Scorer`, and a working `bench report` over the already-landed
`agentcad/bench/report.py`, and a working `bench publish` over Task 7's
`agentcad/bench/publish.py`. `agentcad/cli.py` takes **exactly two** edits, both
specified in the design (§9.1): `_build_service` gains the keyword-only
`examples: bool = True`, and `main()` gains the lazy parser registration plus one
dispatch arm and the `bench` metavar entry.

## Changes

- **`bench score SUBMISSION --task ID`** measures one submission against one task
  and exits `0` when a score was produced, `2` for any harness failure. There is
  deliberately no exit `1`: a low score is a *measurement*, and turning it into a
  failing exit would make the runner and the release gate the same thing (design
  §9.3). Every subscore excluded — no `weights_effective` — is `2`, because that
  is "we could not produce a verdict", not a zero.
- `_cmd_score` is `cmd_check`'s skeleton, byte-for-byte in its shape: setup is
  *inside* the exit-code mapping (a traceback out of a CLI is process exit 1, the
  code reserved for "the model is wrong"), the kernel is stopped and the work root
  released in a `finally` tolerant of a partial construction, and writing
  `score.json` and printing the table are under the *same* mapping as the
  measurement.
- **`--work-dir` is accepted, refused and created before the kernel spawns**
  (`cli._accept_work_dir`, review I1), with the guard bound to the submission, the
  task directory, the shipped `benchmarks/` tree and the projects root
  (`scoring.refuse_scoring_overlap`). A refused path leaves nothing behind — a
  promise with a test on it.
- The projects root is a throwaway `mkdtemp` that this process removes in the same
  `finally`. The scorer never opens the submission through it (it copies into a
  cell and opens a muzzled ephemeral service there), but `_build_service` needs
  *a* root, and pointing one at the user's tree would put every project of theirs
  into the confined worker's writable set for a run that has no business reading
  any of them.
- The submission and the task directory are added to `extra_writable` — known
  here, the one moment the seatbelt/Landlock profile can still be widened, since
  the worker's read of a reference STEP is governed by the same rule set as its
  writes.
- **`bench report RESULTS`** aggregates, optionally gates against `--baseline`
  with `--epsilon`, writes `--json-out` (canonically, via `bench._json.write_json`)
  and `--md` (atomically, via `ProjectStore._atomic_write`), and returns
  `report.report_exit_code` — `0` met or ungated, `1` a regression, `2` an
  incomparable baseline or an unreadable results directory. **No service and no
  kernel**: the command is pure, so a CI job can run it on a machine that has
  never built a solid. A test asserts it never constructs one.
- **`bench publish LEADERBOARD [-o PATH] [--title TEXT]`** validates every row
  against the five disclosure rules and renders the static page. `0` the page was
  written · **`1` a row was rejected for incomplete disclosure and NOTHING was
  written** · `2` harness (an absent board, an unwritable output). The exit-1 lane
  is the one place in this module where `1` is not about a model: a leaderboard is
  a claim about other people's work, so a row that does not disclose everything
  the rules require refuses the **whole** board rather than being dropped from it.
  The handler calls `load_rows` and then `publish` rather than `publish` alone —
  `load_rows` never raises for a bad row, so the exit-1 lane is separated from the
  harness lane before anything can be written, and every problem of every row is
  printed at once. The roster it measures a row against is `load_tasks()`'s ids:
  rule 5 (`publish._coverage_problems`) is what stops a row buying a place by
  running the easy half. `-o` defaults to `docs/bench/index.html` (design §12).
  **No service and no kernel** — a test asserts it.
- **`bench run` is registered but refuses**, printing `not implemented in this
  slice` and exiting 2 (Task 5 wires it). It is in the parser from this slice on
  because `agentcad bench --help` is a promise about the surface, and a stub that
  exited 0 would read to CI as "the suite ran".
- **Output conventions are `check`'s, not new ones**: `--quiet`/`--json` are the
  same mutually exclusive pair; `--quiet` prints nothing anywhere and the exit
  code is the whole answer; `--json` **replaces** the stderr table with the
  canonical document on stdout, so `bench score --json | jq` sees exactly the
  bytes `score.json` holds. The plain path prints an aligned table to stderr and
  one verdict line to stdout. The table's `contrib` column is computed from
  `weights_effective`, never from the declared weight — a column that does not sum
  to the total is a column that lies.
- `--budget` and `--epsilon` go through `cli._finite_arg`: a NaN deadline is never
  in the past so it bounds nothing, and a non-finite tolerance does not widen the
  gate, it deletes it.
- Identity is `locks.set_client_id("bench")` — `cmd_check`'s `"ci"` one lane over,
  so a bench run never collides with a human's per-client checkout.
- **`_build_service(..., examples: bool = True)`** (edit 1 of 2). The default keeps
  `check`, `serve`, `export` and `package` byte-identical; `bench_service` passes
  `examples=False` so a task derived from a bundled example is not solvable by
  opening that example (design §8.2). **The catalog stays registered** —
  `assemble_and_clear` tasks legitimately reach for fasteners, and benching
  without a shipped product surface would measure something other than the
  product. It is a parameter rather than `AGENTCAD_EXAMPLES=0` because that
  variable is process-global and a bench run inside a pytest worker would clobber
  a neighbour.
- **`main()`** (edit 2 of 2): the metavar becomes
  `{serve,open,mcp,new,export,check,bench,package,publish,admin}`,
  `from .bench.cli import add_bench_parser` + `add_bench_parser(sub)` sit after the
  `check` subparser block, and the dispatch chain gains
  `elif args.command == "bench": raise SystemExit(cmd_bench(args))`. Both imports
  are inside `main()`, so importing `agentcad.cli` (the server does) pulls in
  nothing of the bench.
- `agentcad/bench/author.py`'s one `_build_service(scratch)` now passes
  `examples=False` — the authoring helper builds a reference in a throwaway root
  and has no more use for the examples than the scorer does.

## Files

- `agentcad/bench/cli.py` — **new**. `add_bench_parser`, `cmd_bench`,
  `bench_service`, `_cmd_score`, `_cmd_report`, `_cmd_publish`, the `run`
  refusal, `DEFAULT_PAGE`, `_print_problems` and the two printers.
- `agentcad/cli.py` — the two edits: `_build_service`'s `examples` parameter (plus
  one docstring paragraph) and `main()`'s metavar / parser registration /
  dispatch arm.
- `agentcad/bench/author.py` — `main()` builds its scratch service with
  `examples=False`.
- `tests/test_bench_cli.py` — **new**, 26 tests.
- `tests/test_packages_cli.py` — `test_help_lists_package_beside_the_other_commands`
  now expects `bench` in the metavar (the test is per-command *and* pins the
  literal string, so a new command has to update it — by design).
- `docs/changelog/0275-prd-024-bench-cli.md` — this entry.

## Verification

```
$ uv run pytest -q tests/test_bench_cli.py
..........................                                               [100%]
26 passed in 26.31s

$ uv run pytest -q tests/test_bench_cli.py tests/test_bench_publish.py \
      tests/test_bench_report.py
85 passed, 1 deselected in 18.24s
      # the deselected row loads the shipped benchmarks/tasks tree, which is
      # mid-authoring; nothing in this change touches it

$ uv run ruff check agentcad/bench/cli.py agentcad/cli.py tests/test_bench_cli.py
All checks passed!
```

Smoke, the real CLI (AC1 through the process boundary):

```
$ uv run agentcad bench score \
    benchmarks/tasks/model_from_drawing/mfd_001_spacer_plate/reference/project \
    --task model_from_drawing/mfd_001_spacer_plate --out <out>
model_from_drawing/mfd_001_spacer_plate — model_from_drawing · task set bench-v1 v1 · harness 1 · agentcad 0.1.0
  subscore      status             value  weight   contrib
  built         ok                1.0000    0.15    0.1500
  geometry      ok                1.0000    0.50    0.5000
  interference  not_applicable    0.0000    0.00         —
  metrics       ok                1.0000    0.15    0.1500
  specs         ok                1.0000    0.10    0.1000
  valid         ok                1.0000    0.10    0.1000
  wrote <out>/score.json
bench score: model_from_drawing/mfd_001_spacer_plate — 1.0000 over 5 subscore(s)
exit=0

$ uv run agentcad bench report <results> --baseline benchmarks/baseline.json --epsilon 0.02
task set bench-v1 · harness 1 · agent builtin · model claude-sonnet-5 · 1 task(s)
  category                          score      n  missing
  model_from_drawing               1.0000      1        0
bench report: 1.0000 over 1 task(s) — baseline unrecorded (the baseline records
no total yet; the gate is a no-op until a run records one)
exit=0

$ uv run agentcad bench publish <board> -o <page> --title AgentCAD-Bench
bench publish: 2 row(s) over 1 category(ies) → <page>
exit=0

$ uv run agentcad bench publish <board-with-one-undisclosed-row> -o <page>
agentcad bench publish: the leaderboard was not written; 3 disclosure problems:
  - kcl: config is missing; the full-disclosure rule is fail-closed and has no override
  - kcl: submission must be a non-empty string
  - kcl: submission is empty; a leaderboard row must name the artefact that reproduces it
exit=1
                                     # and <page> does not exist

$ uv run agentcad bench publish <absent>          →  exit 2, "is not a leaderboard directory"
$ uv run agentcad bench run --report /dev/null    →  exit 2, "not implemented in this slice"
$ uv run agentcad bench                           →  exit 2, "pick a subcommand: run, score, report, publish"
```

`make test` — 4702 passed, 36 skipped (measured at branch tip 1ae80d1, all slices landed)

## Notes

- **Plan defect corrected (1):** the brief's `test_bench_help_lists_the_four_subcommands`
  wraps `_run(["bench", "--help"])` in `pytest.raises(SystemExit)`, but `_run`
  already absorbs the `SystemExit` and returns its code — the outer `raises` can
  never fire. The test now asserts `_run([...]) == 0`.
- **Plan defect corrected (2):** the brief's `_cmd_score` skeleton creates the
  throwaway projects root with `mkdtemp` and never removes it, leaking a directory
  per invocation. It is removed in the same `finally` that stops the kernel — a
  directory this process created, so the "never delete a directory it did not
  create" contract is untouched.
- **Plan defect corrected (3):** the brief's skeleton returns
  `2 if not score.get("weights_effective") else 0` *after* a `finally` that may
  itself print, but imports `locks`/`AppError` in `cmd_bench` where neither is
  used. Those imports are dropped; the return is unchanged in meaning.
- `bench report` takes no `--tasks-dir`, matching design §9.2 exactly. The roster
  it measures against comes from `bench.json`'s per-task index when `bench run`
  wrote one, and otherwise from the ids on disk; a half-run suite is still caught,
  because `compare_baseline` folds every baseline task id the report does not
  carry in as a `0.0` and reports it as a `coverage` regression.
- The `--json` path *replaces* the table rather than printing both (which is what
  `_print_check` does). It is the brief's wording and the better contract: the
  bytes on stdout are exactly the bytes on disk, with nothing to strip.
- `agentcad/bench/cli.py` imports neither OCP nor build123d, and every bench
  module it needs is imported inside a handler, so `add_bench_parser` costs one
  `argparse` call at parse time and nothing else.
- **Fix round 1** (review of `da081d5`): the §4.8 "every subscore excluded" exit-2
  lane now has a test — both other exit-2 tests short-circuit before a service
  exists, so the lane was reachable only in production. It stubs `bench_service`
  and `scoring.Scorer` and asserts the code is `2`, that `score.json` is still
  written (evidence beats silence — the exclusion moves the exit code and nothing
  else) and that the kernel is still stopped.
- **Fix round 1, minors:** `test_bench_report_needs_no_kernel` patches
  `agentcad.cli._build_service` rather than the `bench_service` wrapper, so it
  still fails if a handler reaches past the wrapper; the two real-kernel
  `_build_service` tests carry `@pytest.mark.timeout(300)`; an autouse fixture
  restores `locks.current_client_id()`, because `set_client_id` writes a
  **ContextVar** and these tests drive `main()` in-process — without it every test
  after the first `bench score` would run as `"bench"`; `_print_problems` lost its
  dead `command` parameter; and `_cmd_report`'s `AppError` handler prints the
  `details["problems"]` list the way `_cmd_score`'s does.
