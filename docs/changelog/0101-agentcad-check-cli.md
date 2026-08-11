# 0101 — 2026-08-11 — PRD-004 slice 4: the `agentcad check` CLI

- **Commit:** pending
- **Date:** 2026-08-11
- **Author:** Claude

## Summary

Fourth slice of PRD-004 (geometry CI): the pipeline slices 1–3 built becomes a
command. `agentcad check` certifies a project **headlessly** — one service, one
warm kernel, the tool registry, and no FastAPI app, port, chat engine or API
key — writes the JSON report and the markdown summary, prints a stage table and
a verdict, and answers with the exit code that is the feature's actual API
(FR1, AC5): `0` green · `1` red, the model is wrong · `2` harness, we could not
produce a verdict. Exactly the two additive edits to `agentcad/cli.py` the plan
sanctions, plus one bug the first real run surfaced (below).

## Changes

- `agentcad/cli.py`:
  - `_build_service(projects_dir, extra_writable=None)` — the one sanctioned
    signature change. `extra_writable` is appended to `_writable_roots(...)`
    **before** the kernel starts, because the seatbelt profile is fixed when
    the workers spawn: a `--work-dir` (or a project) outside
    `tempfile.gettempdir()` cannot be granted afterwards. The default leaves
    every existing caller's writable roots byte-identical, which is pinned by a
    test rather than asserted in a comment.
  - `cmd_check(args) -> int` — resolve `--stages`, resolve `--work-dir` to an
    **absolute** path (a relative one handed to `git worktree add` would
    materialize the throwaway tree inside the user's project), build the
    service with the sandbox widened, `locks.set_client_id("ci")`,
    `build_registry`, then `service.checks.run(...)` when the slice-5 pack has
    installed it and a plain `CheckRunner(service, registry)` until then. The
    kernel is stopped in a `finally`. Every exception out of `run` is exit 2 by
    family: an `AppError` (`NotFoundError` → unknown project, `ValidationError`
    → `--ref` without git, `ConflictError` → a name collision) prints its
    message, anything else prints its type and message. A red *report* is never
    an exception — it is exit 1 with the rows that say why.
  - Output helpers: `_check_stages` (comma-separated, validated against
    `STAGES` **before** the kernel starts, so a typo costs a millisecond and
    names the four valid stages; `determinism` stays unselectable — it has its
    own flag), `_write_check_outputs` (atomic `--report`/`--md` writes through
    `ProjectStore._atomic_write`; an unwritable path is exit 2),
    `_check_lines` / `_check_named` / `_check_verdict` / `_print_check` (the
    stage table, the named failures, the warnings and the harness errors on
    **stderr**; the one-line verdict on stdout; `--json` puts the report alone
    on stdout so `agentcad check --json | jq` works; `--quiet` prints nothing).
    Exit codes are identical in all three modes.
  - `_is_path(project)` — `cmd_export`'s idiom (`"/" in project or
    project.startswith(".")`), now named once and used twice in `cmd_check`.
  - The subparser with the design spec's full flag set — `--project`
    (default `.`) `--projects-dir --ref --stages --report --md --strict
    --verify-determinism --budget --min-volume --work-dir
    (--proposal | --auto-proposal) --sha --ref-label (--quiet | --json)` — the
    `main()` branch (`raise SystemExit(cmd_check(args))`), and the subparser
    `metavar` grown to `{serve,open,mcp,new,export,check}` with the hidden
    `worker` subcommand still hidden.
  - `--proposal` / `--auto-proposal` are accepted and **ignored with a warning**
    until slice 6, so the CLI surface does not change shape mid-plan.
- New `tests/test_checks_cli.py` (22 tests; 11 drive the real console script in
  a subprocess — 10 of them `slow` — and 11 exercise the CLI's own plumbing
  in-process with the service stubbed out): AC5's three exit codes, the written
  report validating under `validate_report` and the markdown naming the failing
  item, `--json`'s stdout being JSON and nothing else, `--quiet` printing
  nothing, `--stages bogus` refusing before anything expensive, a blown
  `--budget` exiting 2 with `complete: false` on disk, a project outside the
  usual writable roots (TMPDIR redirected) still building, every flag reaching
  `CheckRunner.run` through `main()`, the exit-code mapping over four fake
  reports, a harness exception being exit 2 rather than a traceback, an
  unwritable report path being exit 2, and `_build_service`'s default writable
  roots being unchanged.

## Files

- `agentcad/cli.py` — `_build_service` gains `extra_writable`; `cmd_check` plus
  its helpers, its subparser and its `main()` branch (~250 lines added)
- `tests/test_checks_cli.py` — new; 22 tests
- `docs/changelog/0101-agentcad-check-cli.md` — this entry

## Notes

- **The bug the first real run found, and the fix.** `agentcad check --project
  <path>` on macOS failed *every* build with
  `PermissionError: Operation not permitted` writing the project's `.cache/`:
  `_writable_roots` grants the projects dir, `~/.agentcad`, the system temp dir
  and the bundled examples — and a project opened **by path** is none of those.
  That is the CI shape (`--project .` on a checkout), so `cmd_check` now adds
  the resolved project path to `extra_writable` alongside `--work-dir`. It is
  the same "the profile is fixed at spawn" constraint the parameter exists for,
  and it is why the parameter takes a list. Verified before/after on a copy of
  `examples/prototyping` outside `$TMPDIR`: 4 failed rows → `green`, exit 0.
  The regression test redirects `TMPDIR` for the child process so the project
  is genuinely outside the always-allowed temp root; on Linux, where
  `sandbox.supported()` is false, it passes trivially and harmlessly.
- **The stage table is printed after the run, not live.** The design spec asks
  for a table "as each stage finishes"; `CheckRunner` has no progress callback
  and slice 4's file list is `cli.py` plus its test, so adding one would be a
  `checks.py` edit this slice is not sanctioned to make. The table is rendered
  from the finished report instead. A progress hook is a clean follow-up
  (slice 5's runner already publishes `check_finished`; a per-stage event would
  serve the UI and the CLI at once).
- **`--quiet` still names a harness failure on stderr.** "Prints nothing but
  the exit code" is about the *report*: an exit 2 with no diagnosis is
  unactionable, and the flag exists to keep a CI log clean, not to hide why the
  run could not happen.
- **Examples are copied *and renamed* in the tests.** The CLI registers every
  bundled example at startup, so opening an unrenamed copy by path would hit
  `ConflictError: a different project named 'prototyping' is already
  registered`. The copies drop `.cache`/`exports` the way
  `tests/test_examples.py` does; nothing here touches `examples/`.
- **Subprocess cost.** The two example-driven fixtures are module-scoped and
  every later run against the same copy reuses the warmed `.cache/`, so the
  file's eleven subprocess tests pay seven kernel starts and two real build
  passes: 22 passed in 23.8 s.
- The tests drive the real console script (`<venv>/bin/agentcad`) and fall back
  to `python -c "from agentcad.cli import main; main()"` — `agentcad.cli` has
  no `__main__` guard and slice 4 is not allowed to add one, so
  `python -m agentcad.cli` is deliberately not the entry point under test.
- Verification: `uv run pytest -q tests/test_checks_cli.py` → **22 passed** in
  23.79 s; `make test-fast` → **789 passed, 1 skipped** in 190.66 s (0100's
  777 + the 12 non-`slow` tests here); `make test` → **1036 passed, 1 skipped**
  in 1358.60 s (0:22:38), against 0100's 1014-passed baseline — exactly the 22
  tests this slice adds, with no pre-existing test file edited. And the real
  command, on a renamed copy of `examples/prototyping` outside the projects
  dir:

  ```
  $ agentcad check --project …/proto --projects-dir …/projects \
        --md …/r.md --report …/r.json
  manual_proto — worktree
    stage      status  pass  fail  skip  error  total     time
    build      green      2     0     0      0      2    2.9 s
    assembly   green      2     0     0      0      2    0.4 s
    specs      skip       0     0     0      0      0    0.0 s  (not_declared)
    drawings   green      2     0     0      0      2    3.5 s
  wrote …/r.json
  wrote …/r.md
  check: green — manual_proto · 6 passed, 0 failed, 0 skipped, 0 errors of 6 in 6.9 s (exit 0)
  exit=0
  ```
