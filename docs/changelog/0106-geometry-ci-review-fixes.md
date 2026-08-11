# 0106 — 2026-08-11 — PRD-004 review fixes: work-dir containment, a budget that bounds every stage, post/lifecycle race

- **Commit:** pending
- **Date:** 2026-08-11
- **Author:** Claude

## Summary

An independent review of the finished PRD-004 geometry-CI feature returned
CHANGES-REQUIRED with ten reproduced findings. This fixes all ten, each with a
regression test written first.

The critical one is a **data-loss bug in `--work-dir`**: the throwaway worktree
was `<work-dir>/<project-name>`, and anything already there was `shutil.rmtree`d
*before* `git worktree add` ran. From the projects root that path **is** the
live project directory, so `agentcad check --ref … --work-dir .` deleted the
user's project — uncommitted files included — and the `finally` deleted it a
second time. The reviewer's reproduction now raises a `ValidationError` naming
both paths and leaves every byte in place.

The other major one is that `--budget` did not bound the two most expensive
things a run does: the specs stage called `SpecRunner.run` with no deadline, and
one determinism row made four unpreemptable kernel calls behind a single budget
check. The "worst-case overshoot is one in-flight kernel call" sentence in the
docs, `--help` and `AGENTS.md` was false when it was written; it is true now.

## Changes

### W1 (critical) — `--work-dir` can no longer reach the project

- `CheckRunner._work_dir` resolves the caller's `--work-dir` **once**, at the
  top of `run()`, and `_refuse_overlap` rejects any path that equals, contains
  or is contained by the project directory **or** the projects root — a
  `ValidationError` naming both paths (CLI exit 2). The directory is created
  only after it is accepted, so a refused path leaves nothing behind.
- A run materializes into a **unique subdirectory it creates itself**:
  `tempfile.mkdtemp(prefix=f"agentcad-check-{os.getpid()}-", dir=work_dir)`,
  with the worktree at `<cell>/<project>/`. The ephemeral service and the
  determinism copies are rooted in that cell, and the teardown deletes exactly
  it — plus the whole temp root only when the runner created it. A caller's
  `--work-dir` and everything already in it are untouched, which is what
  `docs/geometry-ci.md` promised.
- `_materialized` no longer deletes anything: it re-checks the overlap (last
  gate before `worktree add`) and **refuses** a destination that already
  exists, rather than removing a directory this run did not create.

### W2 (major) — the budget bounds every stage

- `SpecRunner.run` grows a `deadline` passthrough into `_report` (the deadline
  mechanism PRD-003 already threads through every tier; `run_specs` still passes
  `None` and stays unbounded by design). `CheckRunner._stage_specs` passes the
  pipeline's remaining deadline, and marks the run `complete: false` when the
  deadline expired inside the spec run.
- `_determinism_item` reads the deadline before **each** of its four kernel
  calls (two `_ensure_built`, two `generate_drawing`) instead of once per row,
  emitting the same `skip`/`budget_exceeded` row every other truncated item
  gets. `_compare_svg` warns rather than silently comparing nothing.
- New `_MIN_CALL_S = 1.0` floor and `CheckRunner._cannot_afford()`: below one
  second no kernel call is issued at all, because a call that cannot finish can
  only overshoot the budget and then be reported as a *timeout* — a red row for
  something the budget did. The floor is applied in **every** loop that starts
  a kernel call (build, drawings, assembly, determinism), not only in the
  stages the review named, so the rule the docs now state is the rule the code
  follows everywhere.

### W3 (minor) — a budget that expires inside `assembly` is a skip, not a red

- `_stage_assembly` checks the floor before the mate pass and before the
  interference call, and `_budget_broke()` classifies a kernel `timeout` on a
  call that was handed *less than its own ceiling* (`_MATES_CEILING_S` /
  `_INTERFERENCE_CEILING_S`) as the budget running out: `skip`/`budget_exceeded`
  and `complete: false` (exit 2), never an `error` row and exit 1. A timeout
  under no deadline, or under one larger than the ceiling, still reads as it
  always did.

### W4 (minor) — the third live seam on the ephemeral service

- `_ephemeral_service` now nulls `service.store.write_guard` beside
  `bus.on_publish` and `store.branch_resolver`. `build_registry` installs a
  guard whose first act is `branches.ensure_checkout(proj)`, which materializes
  a branch tree in the repository the linked worktree belongs to — the user's.
  It was inert only because a check happens not to write today.

### W5 (minor) — the CLI's post-run block is inside the exit-code mapping

- `cmd_check` wraps `_write_check_outputs` / `_post_check` / `_print_check` in
  the same `AppError → 2` / `Exception → 2` mapping as the run itself. A
  proposals index that cannot be read or rebuilt used to escape as a traceback
  and exit **1** — the code reserved for "the model is wrong". The report is
  still written before anything can fail.

### W6 (minor) — posting no longer races the proposal lifecycle

- `post_to_proposal` performs the reconcile, the terminal-state check, the
  `checks.json` write and the audit append **under `ProposalManager._lock`** —
  `record_packet`'s mechanism, for `record_packet`'s reason: a check measures
  for minutes, and a merge landing between "this proposal is open" and "here is
  the evidence" wrote post-decision evidence onto a terminal proposal. A post
  that loses the race is discarded with a `ConflictError`.
- The audit append is what makes the write final: if it fails, the slot is
  rolled back to what it held (nothing, or the previous post), so a gate can
  never read evidence with no audit line behind it. The branch lookup stays
  outside the lock — it is a git call, and no proposal read should wait on it.

### W7 (minor) — the Action reports setup failures as harness, not red

- The final step reads `steps.check.outcome` and treats an **empty**
  `exit-code` (the check step never ran) as a harness error: `::error::agentcad
  check did not run …`, exit 2. `${EXIT_CODE:-1}` used to turn a failed
  `uv pip install` into "red — failed stages: unknown".

### W8 (minor) — `--strict --verify-determinism` is no longer red for ever

- Rows carry `strict_exempt` (`make_item` validates it is only ever set on a
  skip; `validate_report` checks the same), `finalize_report` never counts an
  exempt row in `strict_failures`, and the DXF determinism row — a skip *by
  construction*, which no project can fix — is the one row that sets it. The
  row stays visible with its reason, its hint and its place in the counts.

### W9 (minor) — the Action's check step is executed by a test

- `tests/test_geometry_ci_action.py` now runs the check step's body **verbatim**
  with a real `agentcad` on `$BIN`, over a project whose path contains a space,
  and asserts the argv construction, the `%q` quoting and the `set +e` /
  `code=$?` capture, plus every `$GITHUB_OUTPUT` line it writes.

### W10 + nits

- `docs/architecture.md` — the stale "64 tools" in the diagram is now **65**,
  matching `docs/agent-api.md` (68 with the `[fem]` extra).
- **N1/N2 (action):** a requirement starting with `-` is refused (it is a
  positional argument to `uv pip install`, and `--index-url=…` on a fork's PR
  is the whole attack), and any newline in a `$GITHUB_OUTPUT` value is refused
  (it forges a second output line).
- **N3 (tests):** the vacuous `all()` over a possibly-empty item list in the
  blown-budget test now requires a reason *or* rows; the version tautology
  compares against `importlib.metadata.version("agentcad")`; the AC3 acceptance
  test pins the declared limit literally (`{"min_mm": 2.0}`); `_run_body` takes
  an explicit `cwd` so no step body silently reads this repository.

## Files

- `agentcad/core/checks.py` — `_within`, `_MIN_CALL_S`, `_MATES_CEILING_S`,
  `_INTERFERENCE_CEILING_S`; `make_item(strict_exempt=…)` + validation;
  `finalize_report` exempt filter; `validate_report` field check;
  `_ephemeral_service` write-guard muzzle; `CheckRunner._cannot_afford`,
  `_budget_broke`, `_work_dir`, `_refuse_overlap`; reworked `_run_ref`
  (unique cell), `_materialized` (refuse, never delete), `_stage_assembly`,
  `_stage_specs`, `_determinism_item`, `_compare_svg`, `post_to_proposal`.
- `agentcad/core/specs.py` — `SpecRunner.run(deadline=…)` passthrough and the
  two docstrings that claimed `run` was unconditionally unbounded.
- `agentcad/cli.py` — post-run block inside the exit-code mapping; `--budget`,
  `--work-dir` and `--strict` help text.
- `.github/actions/agentcad-check/action.yml` — requirement prefix guard,
  newline guard, `OUTCOME` env and the harness-error branch.
- `.github/actions/agentcad-check/README.md` — `strict` exemption; what an
  empty `exit-code` means.
- `tests/test_checks.py` — `strict_exempt` at the pure level (counted in the
  summary, never in `strict_failures`, refused on a non-skip row both at
  construction and by `validate_report`).
- `tests/test_checks_ref.py` — W1 (the reviewer's repro, both overlap
  directions, the unique-cell semantics), W4 (all three seams), W8.
- `tests/test_checks_pipeline.py` — W2 (specs deadline, determinism per-call),
  W3 (assembly boundary + timeout classification), the floor in build and
  drawings, N3.
- `tests/test_checks_cli.py` — W5 (post-run failure → exit 2).
- `tests/test_checks_gate.py` — W6 (merge mid-post, write/audit atomicity), W5
  over a real store.
- `tests/test_geometry_ci_action.py` — W7, W9, N1, N2, N3.
- `tests/test_prd004_acceptance.py` — N3.
- `docs/geometry-ci.md`, `AGENTS.md`, `CLAUDE.md`, `docs/architecture.md`,
  `docs/prd/in-progress/PRD-004-geometry-ci.md`,
  `docs/superpowers/specs/2026-08-11-geometry-ci-design.md` — the work-dir
  contract, the budget's now-true bound, the third seam, `strict_exempt`, the
  corrected claim about what the action tests execute, and an
  "As built — the second review (W1–W10)" section in the design spec.

## Notes

- `_budget_broke` classifies on the remaining budget **before** the call, not
  after: a call handed its full ceiling that times out anyway is still a real
  error, which is why a large `--budget` does not turn genuine kernel timeouts
  into skips.
- A stage holding only `budget_exceeded` skips still reads `green` — skips never
  redden anything (PRD-003's `report_status`) — and `complete: false` is what
  makes the report exit 2. That is unchanged, and the tests assert the rows and
  the exit code rather than the stage's status word.
- `post_to_proposal` reaches for `ProposalManager._lock` by name. It is the only
  lock that serializes against `merge`; a second one of our own would serialize
  nothing. It is an `RLock`, so the re-entrant `reconcile` inside it is safe.
- Follow-up not taken here: `_ensure_built` and the drawing tools still take no
  `timeout_s`, so one in-flight kernel call can still overshoot a budget.
  Threading one through `service._rebuild` is a core-service change and was out
  of scope for a review pass.
