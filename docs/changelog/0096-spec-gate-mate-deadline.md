# 0096 — the spec gate's deadline reaches the mate pass (and a directory named specs.py is red)

- **Commit:** pending
- **Date:** 2026-08-11
- **Author:** Nikita Fedorov

## Summary
The PRD-003 verification review passed the branch MERGE-READY with one liveness
follow-up: the gate budget threaded in changelog 0094 covered every kernel call
made through `SpecRunner._kernel`, but the assembly tier reaches the kernel one
more way — `service._resolved_instances` → `mates.resolve` → `resolve_mates`,
issued at a flat 120 s. On a large mated assembly with project specs that is
`proposal_get` blocking for up to two minutes per call while `proposal_merge`
holds the source turn lock: exactly the S2 failure mode, one seam over. The
deadline now threads through that pass too. Also closes a one-line hole from the
same review: a *directory* named `specs.py` read as "this project declares no
project specs".

## Changes
- **NEW-1 — `resolve_mates` runs under the gate deadline.** `mates.resolve`
  takes an optional `timeout_s` (mirroring the `check_interference` passthrough
  added in 0094) and clamps it to the new module constant
  `mates.RESOLVE_TIMEOUT_S = 120.0`, which is also the default when it is
  omitted — so nothing changes for a caller that passes nothing.
  `AgentCADService._resolved_instances` forwards it, and `check_interference`
  passes its own `timeout_s` down to the mate pass it performs internally.
- **Every gate-context call site now asks for what the budget has left.**
  `SpecRunner._kernel`'s clamp-or-refuse logic is extracted to
  `SpecRunner._budgeted(normal, deadline, method)` (unchanged behavior:
  `max(_MIN_KERNEL_TIMEOUT_S, min(ceiling, remaining))`, and a
  `KernelError("budget_exceeded")` raised *before* the request is issued when
  nothing is left). The new `SpecRunner._mate_timeout(deadline)` applies it to
  `RESOLVE_TIMEOUT_S`, and returns `None` without a deadline. It is used by
  `_project_key` (which now takes `deadline`, passed by `_project_block`), by
  `_eval_clearance`, and by `_eval_stackup`; `_eval_interference` needed no
  change, since the bound it already computes now travels into the mate pass
  through `check_interference`. Both evaluators catch the refusal and return
  the same `_error_row` shape every other stopped measurement produces, so the
  `budget_exceeded` reason survives into the verdict. `run_specs` carries no
  deadline and keeps the flat 120 s ceiling.
- **A fourth path found while fixing the first three: `stackup`.**
  `_eval_stackup` said "no kernel call at all (the mate chain is manifest
  arithmetic)", but `compute_stackup` reads the *nominal* off
  `service._resolved_instances`, so on a mated assembly it too issued a flat
  120 s `resolve_mates`. `compute_stackup` takes the same optional `timeout_s`
  and the comment now says what it really does.
- **NEW-3 — a directory at `specs.py` is unreadable, not absent.**
  `SpecRunner.project_script` discovers the file with `exists()` rather than
  `is_file()`, so a `specs.py/` falls into the `read_text` path and its
  `IsADirectoryError` surfaces through the existing X5 error path (a red
  `project:specs` declaration row, `exists: true`, and a `read_error`
  `declaration_error`) instead of returning `None` and leaving the gate green.
- **The now-inaccurate "every kernel call under the gate" sentences
  corrected.** The `GATE_BUDGET_S` docstring in `core/specs.py` and the S2
  bullet in changelog 0094 both claimed a coverage `_kernel` alone did not
  have; they now name what `_kernel` covers and where the mate pass fits, and
  `docs/agent-api.md` lists the mate pass's 120 s among the ceilings the
  deadline replaces.

## Files
- `agentcad/core/mates.py` — `RESOLVE_TIMEOUT_S` constant; `resolve(...,
  timeout_s=None)` clamped to it; module docstring note.
- `agentcad/core/service.py` — `_resolved_instances(proj, timeout_s=None)`
  passthrough; `check_interference` forwards its `timeout_s` to it.
- `agentcad/core/specs.py` — `_budgeted` extracted from `_kernel`; new
  `_mate_timeout`; `_project_key(..., deadline=None)` and its caller;
  `_eval_clearance` and `_eval_stackup` bounded and degrading;
  `project_script` uses `exists()`; `GATE_BUDGET_S` docstring.
- `agentcad/core/tools_stackup.py` — `compute_stackup(..., timeout_s=None)`
  forwarded to `_resolved_instances`.
- `tests/test_specs_gate.py` — `MATE_BLOCK` / `MATED_CLEARANCE_SPECS` scripts
  and the `mated_demo` fixture (a mate-driven assembly whose only specs are
  project-scope);
  `test_the_mate_pass_under_the_gate_is_bounded_by_the_remaining_budget`,
  `test_every_project_evaluator_bounds_the_mate_pass` and
  `test_run_specs_resolves_mates_at_the_flat_ceiling`.
- `tests/test_specs.py` — `test_a_directory_named_specs_py_is_red_not_absent`.
- `docs/changelog/0094-spec-review-fixes.md` — S2 wording + a pointer here.
- `docs/agent-api.md` — the budget bullet names the mate pass and its ceiling.

## Notes
- Demonstrated failing first: with a stubbed 5 s `resolve_mates` and
  `GATE_BUDGET_S = 2.0`, the gate issued `timeout_s=120.0` (`assert 120.0 <=
  2.0`) and the call outlasted the budget; the directory test's report came
  back `green` with no `project:specs` row at all; with the sidecar key and
  `clearance` fixed, `_eval_stackup` still issued `120.0` under a 5 s deadline
  (`assert 120.0 <= 5.0` on `[4.99…, 120.0]`).
- The clamp lives in `mates.resolve` rather than in each caller so no caller can
  ask for more than the flat ceiling; the floor and the raise-before-issue stay
  in `SpecRunner`, where the budget is owned.
- `_mate_timeout` imports `RESOLVE_TIMEOUT_S` lazily and returns `None` on
  `ImportError`, keeping the same optional-module seam
  `_resolved_instances` has: no mates module means no request to bound.
- When the budget refuses the mate pass inside `_project_key`, the existing
  `except Exception` there degrades to an unkeyed (uncached) evaluation, and
  the checks that follow report `budget_exceeded` rows — red, never a silent
  green.
