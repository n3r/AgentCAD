# 0107 — 2026-08-11 — PRD-004 second-review fixes: per-run budgets, honest posts, a gate that cannot be deleted

- **Commit:** pending
- **Date:** 2026-08-11
- **Author:** Claude

## Summary

A second independent review of the geometry-CI feature (over the branch
`0106-geometry-ci-review-fixes` already fixed) found six more defects and one
test gap. Every one of them is the same shape: a **promise** — a budget, a
certified commit, a merge gate, a CI exit code — was being made out of state
that another actor could overwrite, delete or forge.

Its findings 1, 2 and 6 were re-verified against the current code and are
genuinely closed by 0106 (`--work-dir` refuses an overlap and never deletes what
it did not create; the specs stage runs under `SpecRunner.run(deadline=…)`,
determinism reads the deadline before each of its four calls, and a
one-second floor stops a call the budget cannot pay for; posting happens under
`ProposalManager._lock` with a write-then-audit rollback). Finding 2's *tail* —
"no final deadline check after the last selected item" — was still open and is
addressed below.

Each fix has a regression test written first; two of them (the shared-state race
and the whole Action output path) were run against the old behaviour to prove
they fail.

## Changes

### C3 (major) — a run's policy belongs to the run, not to the runner

`service.checks` is a **singleton**: one `CheckRunner` answers the CLI, the chat
agent, the MCP tool and the route. `run()` wrote `_deadline`, `_truncated` and
`_min_volume` onto it with no serialization, so two concurrent callers
overwrote each other's execution policy — session A's short budget was nulled by
session B's unbounded run, A then measured everything and still reported
`complete: true`.

- `CheckRunner.run` now builds a **per-run context** (`_run_context`) and
  measures through it. Nothing in a run assigns to `self`; the shared instance
  keeps its defaults for ever.
- The context *is* a `CheckRunner`, deliberately: the stage methods already take
  their policy from `self`, so binding them to a throwaway instance changed no
  signature, no caller and no test that drives a stage directly. A lock around
  whole runs was the alternative and is worse — a ten-minute CI run would block
  the UI's own check.

### C4 (major) — a dirty working tree cannot certify a commit

A working-tree report records the tree's committed head as `source.sha` **and**
`dirty: true` beside it, and both posting and the gate ignored the flag. So:
commit C has a failing drawing, an uncommitted local edit fixes it,
`agentcad check --proposal P` posts a *green* report whose `head` is C, the gate
passes and the merge lands the unfixed C.

- `post_to_proposal` refuses it (`_refuse_dirty`): a `ValidationError` naming the
  dirty tree, CLI exit 2, nothing written and nothing audited. Fail-closed at the
  post rather than at the gate, so the honest report never becomes a record.
- A `--ref` run is untouched: it measured the **commit** it materialized, and its
  `dirty` flag describes a working tree it deliberately did not measure.
- CI runners are always clean (`actions/checkout` materializes the commit), so
  the Action path never meets this.

### C5 (major) — a deleted record is not "nothing posted", and a record is validated

`checks.json` is an ordinary file: deleting it restored the **permissive**
`skipped` gate, so removing a red report unblocked the merge — while the
append-only audit log went on recording that a check had been posted. And a
hand-written `{"head": …, "status": "green"}` passed the gate, because a posted
record was never schema-checked.

- The gate asks the audit first (`_was_posted`): a proposal with a
  `checks_posted` line and no readable record is a `fail` (`reason: missing`)
  saying *re-run*, never `skipped`. A genuinely never-posted proposal still
  skips — that is the one permissive branch, and it is what "posting is how a
  proposal opts in" means. `_was_posted` fails **closed** on its own failure.
- New `validate_record()`, `validate_report`'s counterpart for the envelope:
  `CHECKS_SCHEMA`, the fields the verdict reads, `validate_report` over the
  embedded report, and a **cross-check** — `status`/`exit_code`/`complete`/`head`
  are copies of the report's own values, so a mismatch means one of the two was
  edited. `_checks_verdict` runs it first; an unvalidatable record is a `fail`
  (`reason: invalid_record`) with the problems in `details.error`.
- The unknown-`status` branch is left alone rather than folded into the
  validator, so it stays reachable and keeps its own wording.

### C7 (major) — the Action's verdict cannot come from a file the run did not write

The check step did not clear a pre-existing report, and `report_outputs.py`
wrote raw report strings into the single-line `$GITHUB_OUTPUT` protocol *after*
the real exit code. A stale or crafted `{"status": "red\nexit-code=0"}` therefore
forged a second output line that replaced the verdict, and the job finished
**green** having measured nothing.

- The check step `rm -f`s `$REPORT_JSON`/`$REPORT_MD` **before** running: a
  restored cache, a report committed to the repository or a previous matrix leg
  can never be read as this run's verdict.
- `report_outputs.py` validates: `status` must be one of `green|red|skip` (any
  other string is emitted as empty — unknown is not an attack), a red stage's
  name must match `^[a-z]+$`, and **any** value containing a newline or a
  carriage return is refused with `::error::` and a non-zero exit, having written
  nothing. Values that reach `$GITHUB_OUTPUT` are proven newline-free, so the
  single-line form stays.
- `exit-code` is written by the step that owns it, **last**, after the parser —
  and a parser refusal escalates a `0` to a `2`, because no verdict is not a
  pass.

### C8 (major) — CLI setup is inside the exit-code mapping

`cmd_check` created the work dir and built the service *before* the `try`, so an
unwritable `--work-dir` escaped as a traceback and process exit **1** — the code
reserved for "the model is wrong", which automation reads as red geometry.

- Setup moved inside the mapped region; the `finally` tolerates a partial
  construction (`if service is not None`) and a kernel that will not stop is a
  stderr note rather than a replacement verdict.
- `_build_service` stops the pool it just started if `AgentCADService` or
  `_register_examples` raises. Otherwise every worker (one process, ~0.5 GB) was
  left running with nobody holding a reference to it.

### C9 (major) — a limit must be a number

`argparse`'s `type=float` returns `nan`/`inf` happily and `json.loads` reads the
bare `NaN` literal, so both the CLI and the REST/MCP surfaces accepted them.
Every comparison with NaN is false: a NaN `--budget` switched off the deadline it
configures (and the report still said `complete: true`), and a NaN
`--min-volume` made `volume > min_volume` false for a real overlap — a green
report on an interfering assembly.

- `cli._finite_arg` is the argparse `type` for `--budget` and `--min-volume`:
  finite, non-negative, exit 2 with a message naming the flag and why.
- `checks._finite` refuses the same values inside `CheckRunner.run`, which is
  what covers the tool, the route and any embedder. The offending value travels
  as a **string** in `details`, because a NaN there would be the literal `NaN` in
  the JSON payload the error becomes.

### C10 (minor) — one rule for an empty stage list

`stages: []` is falsy, so `tuple(stages) if stages else STAGES` read an explicit
"nothing" as "everything" and launched the full multi-minute pipeline — while
the CLI rejects an empty `--stages` and `CheckRunner.run(stages=())` selects
none. The tool boundary now gives the CLI's answer (a `validation_error` naming
the four stages), and the runner's "an empty tuple selects none" contract is
stated in its docstring for the direct caller.

### Finding 2's tail — the overshoot after the last item

The deadline is read *before* each item, so an expiry **inside the last one** is
seen by nobody. It stays `complete: true`: everything selected was measured, and
`complete: false` means "something was not measured", not "this took longer than
you asked" — flipping it would turn a fully measured green run into exit 2 and a
gate `fail` for the one-in-flight-call overshoot the contract already documents.
`_note_overshoot` records it in `warnings[]` instead, so the report never
silently claims it stayed inside its budget.

## Files

- `agentcad/core/checks.py` — `_finite`; `CheckRunner._run_context`,
  `_note_overshoot`, `_refuse_dirty`, `_was_posted`; `run()` measures through the
  per-run context and validates `budget_s`/`min_volume`; `validate_record` +
  `_RECORD_TYPES`; `gate_provider`'s missing-record branch; `_checks_verdict`
  validates first.
- `agentcad/core/tools_run_checks.py` — `stages: []` is a `validation_error`;
  `STAGES if stages is None else tuple(stages)`; schema descriptions for
  `stages` and `budget`.
- `agentcad/cli.py` — `_finite_arg` and the two `type=` hooks; `cmd_check`'s
  setup inside the try/finally; `_build_service` stops a kernel it started when
  construction fails.
- `.github/actions/agentcad-check/action.yml` — the report paths are cleared
  before the run; the parser's refusal escalates the code; `exit-code` is written
  last.
- `.github/actions/agentcad-check/report_outputs.py` — closed enum, stage-name
  regex, newline/CR refusal with a non-zero exit, and the reasoning in the
  module docstring.
- `tests/test_checks_pipeline.py` — C3 (two interleaved runs, budget and
  `min_volume`, via the `_interleaved` helper), C9 at the runner, the
  last-item overshoot.
- `tests/test_checks_gate.py` — C4 (refusal + the `--ref` exemption + the CLI
  end-to-end: dirty ⇒ exit 2 and no slot, committed ⇒ posts and the gate
  passes), C5 (deleted record ⇒ fail, never-posted ⇒ skipped, four invalid
  records ⇒ fail).
- `tests/test_checks_cli.py` — C8 (unwritable `--work-dir`, a failure after the
  kernel starts), C9 at the parser; `_FakeKernel` records `stop()`.
- `tests/test_checks_api.py` — C10 (the boundary refuses, the runner still
  selects none), C9 at the tool.
- `tests/test_geometry_ci_action.py` — C7: the stale-report deletion and the
  hostile-report escalation as **executed** step bodies (`_stub_bin`), the
  parser's four refusal cases, the unknown-status case, and the
  `exit-code`-is-last ordering.
- `docs/geometry-ci.md`, `AGENTS.md`,
  `.github/actions/agentcad-check/README.md`,
  `docs/superpowers/specs/2026-08-11-geometry-ci-design.md` — the gate table's
  two new fail branches, the dirty rule, the finite-limit rule, the per-run
  budget, the empty-stage-list rule, the Action's three verdict rules, and an
  "As built — the third review (C3–C10)" section.

## Notes

- `_was_posted` reaches for `CheckStore._store().audit(...)`, which is
  `ProposalStore`'s own reader: it answers `[]` for a log that does not exist
  (the ordinary never-posted case) and skips a torn line, so the gate needs no
  error handling of its own beyond failing closed.
- The C3 fix deliberately reuses `CheckRunner` as the context object rather than
  introducing a `RunContext` dataclass: every stage method already reads
  `self._deadline`/`self._min_volume`, and threading a new parameter through
  eleven methods would have rewritten every test that drives a stage directly —
  a large diff whose only purpose is to say the same thing differently.
- The dirty-post refusal is a **contract change** on `post_to_proposal`
  (`run_checks {proposal}` and `POST /checks` now answer `validation_error` for a
  dirty working-tree report). It is the intended one: the alternative — posting
  it and recording it as non-certifying — leaves a record whose only purpose is
  to be disbelieved.
- Not taken here: `service.checks.last` is still mutated from `run()` on the
  shared runner. It is a bounded dict of finished reports, not policy, and the
  worst a race can do is evict one entry early.
