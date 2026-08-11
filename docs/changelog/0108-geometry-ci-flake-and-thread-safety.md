# 0108 — Geometry CI: an AC9 flake, a racy report cache, a work dir made before it was refused

- **Commit:** pending
- **Date:** 2026-08-11
- **Author:** Nikita Fedorov

## Summary
Four findings from the PRD-004 verification review, before the PR: a ~10%
flaky acceptance test (a clock the normalizer did not reach), a data race in
`CheckRunner._remember` that discards a *finished* report rather than evicting
one early, a `--work-dir` the CLI created inside the user's project on its way
to refusing it, and three sentences in `0106`/`0107` and the docs that
described behavior the code does not have. Nothing here changes what a check
measures.

## Changes

### F1 (blocking) — `test_the_cli_and_the_tool_report_the_same_thing` was a 10% flake

- `tests/test_checks_api.py::_normalize` zeroed the **top-level** clock, the
  host block and each stage's `duration_s` — and stopped there. A stage embeds
  the document the surface it drove produced: `stages[i].report` is PRD-003's
  spec report, and it carries its own second-resolution `generated`
  (`agentcad/core/specs.py`, embedded by `checks.py::_specs_stage`). The CLI
  subprocess and the in-process tool stamp it ~0.1–0.3 s apart, so the strings
  agreed *unless* the pair straddled a second boundary — roughly 1 run in 10,
  and always on exactly `.stages[2].report.generated`.
- `_normalize` now strips a **named clock-key set** —
  `{started, finished, generated, checked_at, duration_s}` — recursively, at
  every depth, via `_strip_clocks`, and still drops `host` whole. Future-proof
  by construction: the next embedded document is covered without anyone
  remembering to add its path. Everything else is compared exactly, so a
  genuine divergence in a *measurement* still fails the test.
- The test docstring now names what is normalized and why.
- `tests/test_prd004_acceptance.py`'s AC9 test carried a **copy** of the old
  `_normalize` with the same hole (CLI subprocess vs. in-process route, same
  embedded `generated`) — the same flake, one file over. It now imports the
  one normalizer from `tests/test_checks_api.py` (the `test_render_frame.py`
  → `test_render.py` precedent), so the rule cannot drift again.

### F2 (medium) — `CheckRunner._remember` was not thread-safe

- `_remember` did `pop` / assign / `while len(...) > LAST_REPORTS:
  pop(next(iter(...)))` on `self.last`, a dict on the **singleton**
  `service.checks` that the CLI, the chat agent, the `run_checks` tool and
  every request share. Unserialized, that raises `RuntimeError: dictionary
  changed size during iteration` and `KeyError` — 12 threads × 20 000 calls
  over 60 project names with `sys.setswitchinterval(1e-9)` produced ~3000
  exceptions.
- It matters because of **where** it runs: the last statement of `run()`,
  after every measurement. The exception does not evict an entry early — it
  escapes `run` and **discards a fully measured report** (CLI exit 2, tool
  error), i.e. minutes of kernel work traded for a cache write.
- Fixed with a `threading.Lock` (`self._last_lock`, new in `__init__`) held
  across the three statements. `pop(..., None)` alone would not have done it —
  the `RuntimeError` comes from the `next(iter(...))` walk, not the `pop`.
  The lock is never held across a measurement.

### F3 (low) — a refused `--work-dir` was created before it was refused

- `cli.cmd_check` resolved `--work-dir` **and `mkdir`'d it** before the runner
  ran, so `--work-dir <project>/sub` created `sub` inside the user's project
  and *then* exited 2 on the overlap refusal. Nothing was ever deleted (0106's
  containment fix is intact), but 0106's "a refused path leaves nothing
  behind" was false on the surface most people use.
- The CLI now only *resolves* the path (absolute before the kernel spawns, and
  granted to the sandbox — a grant is a path, not a directory).
  `CheckRunner._work_dir` creates it after `_refuse_overlap` accepts it, which
  is what its docstring already claimed.
- Consequence, pinned in the C8 test: an unwritable `--work-dir` now fails
  *inside* `run()` rather than before the service is built. Same contract —
  exit 2 with a named message, under the same try/except — but the kernel is
  up by then and the `finally` stops it.

### F4 (low) — the tool/route do not raise for a refused post; three docs said they did

- Behavior unchanged and correct: `tools_run_checks.run_checks` catches the
  `AppError` from `post_to_proposal` and returns the report it just measured
  with `posted: {id, ok: false, error}` plus a `NOT posted` line in
  `warnings`, at HTTP 200 with no top-level `error` key. Throwing away minutes
  of kernel work to report a delivery failure would help nobody.
- The risk is a consumer that reads only `status`/`exit_code`: a green,
  complete report whose `posted.ok` is `false` certified **nothing**, and the
  proposal's `checks` gate is still `skipped`. So the `run_checks` **tool
  description** now names `posted.ok`, calls a refused post a receipt, and says
  which two refusals land there (dirty tree, proposal gone terminal mid-run);
  the `proposal` argument description points at it too.
- `docs/geometry-ci.md` and `docs/agent-api.md` now describe the refusal
  per-surface (CLI: stderr + exit 2 · tool/route: a receipt at 200) instead of
  claiming `validation_error` everywhere.

### Also — a parser refusal in the Action is always a harness error

- `action.yml` escalated the exit code to `2` only when the check itself said
  `0`. A hostile or unparseable report **alongside exit 1** kept the 1, and the
  re-raise step printed `red — failed stages: unknown` (both outputs are empty
  after a refusal) — measured geometry nobody could read. A refusal is now `2`
  whatever the check said: an unreadable report is not a verdict.

### Corrections to earlier entries

Per `docs/changelog/README.md` ("don't rewrite past entries except to fix a
factual error"), three sentences are corrected in place, each marked as a
correction and pointing here:

- `0106` — "the directory is created only after it is accepted, so a refused
  path leaves nothing behind" was false for the CLI (F3).
- `0107` — "`run_checks {proposal}` and `POST /checks` now answer
  `validation_error` for a dirty working-tree report" was false; it is a
  receipt (F4).
- `0107` — "the worst a race can do is evict one entry early" was false; it
  discards a finished report (F2).
- `0107` — the parser-refusal escalation is widened from "a `0`" to "any code".

## Files
- `agentcad/core/checks.py` — `import threading`; `self._last_lock` in
  `__init__`; `_remember` serialized, with the cost of the race named.
- `agentcad/cli.py` — `cmd_check` no longer creates `--work-dir`; comment says
  who does and why the sandbox grant does not need the directory to exist.
- `agentcad/core/tools_run_checks.py` — `run_checks` description: `posted.ok`,
  the receipt shape, and that `status`/`exit_code` are about geometry only;
  `proposal` argument description points at `posted.ok`.
- `.github/actions/agentcad-check/action.yml` — a parser refusal sets `code=2`
  unconditionally.
- `.github/actions/agentcad-check/README.md` — the escalation, restated.
- `tests/test_checks_api.py` — `_CLOCK_KEYS` + `_strip_clocks`, a recursive
  `_normalize` and its docstring; `test_the_last_report_cache_survives_
  concurrent_runs` (8 threads × 3000 calls, tightened switch interval, zero
  exceptions, `len(last) <= LAST_REPORTS`); the description test now demands
  `posted.ok`.
- `tests/test_checks_cli.py` — `test_a_refused_work_dir_is_never_created`; the
  fake `_Runner` creates the work dir like the real one; the C8 test's
  "nothing was started" assertion follows the move.
- `tests/test_checks_gate.py` — `test_a_refused_post_is_a_receipt_on_the_
  report_not_an_error` (green report, `posted.ok: false`, warning, no slot, no
  audit line).
- `tests/test_geometry_ci_action.py` —
  `test_a_parser_refusal_is_a_harness_error_whatever_the_check_said`.
- `tests/test_prd004_acceptance.py` — the duplicated `_normalize` is gone; AC9
  imports the one in `tests/test_checks_api.py`.
- `AGENTS.md` — the dirty-post trap says which surface raises and which
  receipts.
- `docs/geometry-ci.md`, `docs/agent-api.md` — the dirty-post refusal per
  surface.
- `docs/changelog/0106-*.md`, `docs/changelog/0107-*.md` — the corrections
  above.

## Verification
- `uv run pytest -q tests/test_checks_api.py` **× 25 consecutive runs: 25/25
  green** (27 passed each, ~14.4 s per run).
- Forced-gap repro (a scratch copy with `time.sleep(1.2)` between the CLI run
  and the tool run): **3/3 failed before**, on exactly
  `.stages[2].report.generated` and nothing else; **3/3 passed after**. The
  same repro against `tests/test_prd004_acceptance.py`'s AC9 fails with the old
  copied normalizer and passes 3/3 with the shared one.
- The eight geometry-CI files (`test_checks*.py`, `test_geometry_ci_action.py`,
  `test_prd004_acceptance.py`): **269 passed** in 320 s.
- `make test-fast`: **894 passed, 1 skipped**.
- `make test`: **1183 passed, 1 skipped** in 23:00 (baseline 1179 + 1 skipped;
  the four new tests are F2, F3, F4 and the Action parser refusal).
- An accepted `--work-dir` end to end: `agentcad check --project <copy>
  --stages build --work-dir <tmp>/accepted` → exit 0, and the runner created
  `accepted/`.

## Notes
- **How F1 was proven, not argued.** See Verification: the flake was
  reproduced deterministically by forcing the second boundary the race needs,
  rather than by running the suite until it failed.
- **Why a key set and not a path.** Pinning `report["stages"][i]["report"]
  ["generated"]` would have fixed today's flake and left tomorrow's: any stage
  that starts embedding a document brings its own clock. Stripping by key name
  is a smaller rule that covers the class.
- The F2 test is bounded (8 × 3000, ~0.2 s) rather than the 12 × 20 000 that
  first reproduced it: the tightened switch interval, not the volume, is what
  makes the race certain, and a regression test may not cost seconds.
- Not taken: making `service.checks.last` an `OrderedDict`/`lru_cache`. The
  lock is three lines and keeps insertion-order eviction obvious; the dict is
  not hot (one write per completed check, which takes minutes).
