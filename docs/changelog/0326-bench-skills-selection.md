# 0326 — PRD-029 slice 5: `bench run --skills` and the with/without comparison

- **Commit:** pending
- **Date:** 2026-08-23
- **Author:** Nikita Fedorov (orchestrated with Claude)

## Summary
FR8/AC5: a bench run selects which skills the built-in agent can see —
`all` (default), `none`, or a named list — and records the selection as
provenance in `run.json`/`bench.json`, so two runs of one task differ only
in the skill and `bench report --baseline` prints the delta.

## Changes
- `bench/cli.py`: `--skills all|none|<name>[,<name>…]` on `run`
  (`_skills_arg` validates at parse time against the shipped index — an
  unknown name is a usage error, exit 2, naming the unknown and every
  selectable name; a capability-hidden name is reported as "exists but is
  not loadable here"); `_install_skills(task_service, selection)` after
  `_derive_task_service`: `all` → the task service's own library; `only` →
  `SkillLibrary(store, only=frozenset(names))` on both the service (so the
  tools refuse) and the engine; `none` → an empty `only` on the service and
  `skills=None` to the engine (the system prompt is byte-identical to
  `SYSTEM_PROMPT`, asserted). The selection lands in the `bench.json`
  header.
- `bench/runner.py`: `SKILLS_ALL`, `skills_block(selection)`,
  `run_task(..., skills=None)` → `ChatEngine(..., skills=skills,
  budget=SkillBudget.from_config())`, `run_json(..., skills=None)` →
  `"skills": {"mode": "all"|"none"|"only", "names": [sorted]}`.
  `RUN_SCHEMA` unchanged (additive); `score.json` untouched and asserted
  byte-identical across modes.
- `docs/bench.md`: the flag in the `run` usage and table, the `run.json`
  layout line, a "Measuring a skill" section (run twice, convert the
  without-run's report to a baseline document, `bench report B/ --baseline
  A/baseline.json`).
- `tests/test_bench_skills.py` (5): the scripted task run with `none` and
  `snap-fits` — the `load_skill` result carries content in one and a
  refusal in the other, `run.json` modes, `score.json` equality, the report
  delta line, `--skills nope` exit 2.

## Files
- `agentcad/bench/cli.py`, `agentcad/bench/runner.py`, `docs/bench.md`
- `tests/test_bench_skills.py` — new

## Notes
Two departures from the spec's letter: the refusal for a skill outside the
`only` set is `skill_not_found` (`notfound_error`, with a hint naming
`bench --skills`), not `skill_unavailable` — Slice 1's library already
implements the restriction that way and giving one condition two names
helps nobody; and `bench report --baseline` takes a baseline *document*
(numbers), not a results directory, so the doc shows the one-filter
conversion rather than a form the command does not have. Slice tests:
`uv run pytest -q tests/test_bench_skills.py tests/test_bench_cli.py
tests/test_bench_runner.py tests/test_bench_report.py
tests/test_prd024_acceptance.py tests/test_skills_tools.py
tests/test_skills_chat.py` — 166 passed. The full `make test` for the
branch is cited in the slice-7 entry that follows this one (same tree).
