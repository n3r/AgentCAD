# 0358 — PRD-018 slice 1: the generation loop + budget/termination

- **Commit:** pending
- **Date:** 2026-08-25
- **Author:** Claude (Opus subagent) / Nikita Fedorov

## Summary
`agent/generate.py` — the budgeted iterate-until-green generation loop
(FR3–FR5): a standalone `GenerationLoop`/`run_generation`, not a
ChatEngine subclass, reusing chat's seams by import.

## Changes
- Reuses `client_factory`, `_block_to_dict`, `_render_tool_result`, and
  the `_call_tool` tenancy-capture pattern verbatim; a RESTRICTED tool
  list (`ALLOWED_TOOLS`, asserted; a forbidden tool_use — delete_part,
  generate_part, proposals — refused at dispatch, no recursion).
- **Mechanical look-and-measure in CODE (FR3):** after any turn where the
  model wrote a script, the loop itself dispatches render_view →
  get_metrics → run_specs on the scratch part and injects the results
  (render as a real image block) into the same user turn — a model that
  omits them cannot skip them (test-proven with a fake model that never
  emits them).
- **Budget (FR4):** `_BudgetedGenClient` (bench's BudgetedClient twin)
  refuses the next `messages.create` past wall-clock/max_iterations/
  max_tokens, raising `_BudgetStop` the loop catches → `budget_exhausted`
  (a result, never an exception); outer `asyncio.wait_for` backstop.
  best-so-far = `(kernel_valid, spec_pass_count, -spec_fails,
  -metric_distance)`.
- **Three terminal states:** spec_green (kernel-valid AND run_specs
  green), budget_exhausted (best-so-far, spec_green:false, failing checks
  named), abandoned (FR5: ≥3 consecutive build crashes or the wall-clock
  backstop; structured error preserved; sibling candidates are independent
  asyncio tasks and continue).
- **Half-write integrity (FR3/AC3):** each candidate iterates on scratch
  part `gen_<genid>_<n>` (NOT the design's `__gen_` — a leading underscore
  fails `validate_id`; exposed as `SCRATCH_PREFIX`/`scratch_id()`, the
  contract S4 builds its listing guard + cleanup off);
  `cleanup_scratch(service, project, gen_id)` deletes them; no live
  user-facing orphan (accept/rename is S4).
- Identity `gen:<id>:<n>`; tenancy captured before each run_in_executor;
  events `generation_progress`/`generation_done` + tagged
  `chat_tool_call`/`chat_tool_result` for the transcript.

## Files
- `agent/generate.py`, `tests/test_generation_loop.py` (10 tests) — new

## Notes
The `run_generation(service, registry, *, project, prompt, images?,
intent?, budget?, candidates?, client_factory?, gen_id?, bus?)` interface
is documented for S4. `make test` — 7192 passed, 51 skipped (14:29); the non-passing were the count-guards reading the pre-commit newest changelog (this commit adds the count), one PRD-017 AC7 set-equality assertion updated to a subset check (S3's intake extensions are a legitimate addition; the guard still refuses an unsupported ext), and the documented supervisor/navigation load flakes + prd028 FEM timeout (34/34 pass in isolation).
