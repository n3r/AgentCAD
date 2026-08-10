# 0094 — PRD-003 review fixes: caller-branch verdicts, an enforced gate budget, cached failures

- **Commit:** pending
- **Date:** 2026-08-11
- **Author:** Nikita Fedorov

## Summary
An independent review of the executable-specs feature returned
CHANGES-REQUIRED with reproduced findings. This fixes all of them, each with a
failing regression test written first: a `ref=None` verdict that measured one
branch and was filed under another's head, a gate budget that was advisory
inside a part, a failing `spec_eval` re-paid on every read, a predicate that
could edit the metrics another check was measured against, and four smaller
correctness/UI holes.

## Changes
- **S1 — `evaluate_specs(ref=None)` is the CALLER's branch, throughout.** The
  report runs unpinned (the store's branch resolver puts it on the caller's
  tree) but the head came from `history.head(canonical)` — the *default*
  branch's — and the memo key was `(project, None, canonical_head)`, shared by
  every client on every branch, so a green `master` received `feat`'s memoized
  red verdict. New `SpecRunner._verdict_branch` resolves `branches.current` for
  `ref=None`; the head, the memo key `(project, branch, head)` and the
  moved-head re-read all use that branch. A branch the layer cannot name is
  evaluated and returned but not memoized.
- **S2 — `GATE_BUDGET_S` is now a deadline.** New `SpecRunner._kernel` issues
  every kernel call made under the gate with `min(its own ceiling, remaining)`
  instead of 300 s (`spec_eval`, `spec_declare`, `clearance`) or 600 s
  (`fem_static`, `interference`), and refuses to issue one with nothing left
  (`KernelError("budget_exceeded")`). The deadline is also re-checked *between
  checks* inside `_part_block` (before each FEM) and `_project_block` (before
  each assembly check) via the new `_budget_row`. `budget_exceeded` verdicts
  are now **memoized** — red with a stable reason for that head — and
  `run_specs`, which is unbounded by design, drops them for the project
  afterwards (`_forget_budget_verdicts`), so "run run_specs on that branch" is
  a true instruction rather than a hope.
- **S3 — the `.specs.json` sidecar caches the FAILURE too.** A failed
  `spec_eval` writes `{version, cache_key, error}` under the same content key
  and a later read re-raises that `KernelError` (the `_residue` path is
  unchanged); `spec_declare` failures are memoized in `_declaration_cache` the
  same way (`_DECLARE_ERROR` marker). Failures measured under a deadline are
  never cached, and `run_specs` passes `refresh=True` (ignores a cached
  failure) after dropping the cached declaration failures.
- **S4 — a predicate can no longer change another check's verdict.**
  `kernel/handlers/specs.py` hands each check a `copy.deepcopy` of the metrics
  and evaluates every `that` check **after** the built-in kinds; records are
  still emitted in declared order.
- **S5 — stale spec chips are cleared on a failed rebuild** (`inspector.js`
  `applyRebuildResult`, and one line in `main.js`'s `rebuild_failed`): a failed
  build carries no `specs` key, so the last good build's green chips used to
  sit beside a red build banner.
- **S6 — `specs_py_changed` is measured from the merge base**
  (`git diff --name-only <target>...<source>`). Two dots also reported what the
  *target* changed since the branch point, so a target that gained a `specs.py`
  flagged every open proposal as editing the spec.
- **S7 — a `mesh_only` clearance skip is a gate FAILURE** (new
  `SpecRunner._gate_row`), while a `run_specs` report keeps the named skip. The
  distance genuinely was not measured, and a gate that passes it means swapping
  a STEP reference for an STL silently satisfies a declared clearance. The
  failure carries `details.reason: "mesh_only"`, the hint, and
  `details.skipped_in_report`.
- **S8 — `write_project_specs` maps a failed delete to a `validation_error`**
  naming the path; only `FileNotFoundError` stays silent.
- **S9/S10/S11 (nits)** — `renderSpecs` no longer appends an empty 12 px chip
  strip for `SPECS = []`; `_part_block`/`_unevaluated_part` return a *copy* of
  the shared warnings list; `_memo_put` guards its eviction `next()` with a
  default.
- `service.check_interference` gained an optional `timeout_s` (default
  unchanged at 600 s) so a caller under a deadline can bound it.
- Tool description and docs updated for the three contract changes (S2 memo
  semantics, S3 failure caching, S7 gate clearance-skip semantics).

## Files
- `agentcad/core/specs.py` — `_kernel`, `_verdict_branch`, `_gate_row`,
  `_budget_row`, `_forget_declaration_failures`, `_forget_budget_verdicts`,
  `_remember_declaration`; deadline/refresh threading through `_shape_tier`,
  `_declare`, `_residue`, `_part_block`, `_project_block`, `_eval_fem`,
  `_eval_clearance`, `_eval_interference`, `_eval_stackup`, `_report`, `run`;
  merge-base `specs_py_changed`; `OSError` on delete; memo eviction guard.
- `agentcad/kernel/handlers/specs.py` — per-check metrics `deepcopy`,
  `that`-checks-last evaluation order, docstring rule.
- `agentcad/core/service.py` — `check_interference(..., timeout_s=None)`.
- `agentcad/core/tools_specs.py` — `run_specs` description: cached failures and
  the two gate divergences.
- `frontend/js/inspector.js` — clear `state.part.specs` on a failed rebuild;
  no empty chip strip.
- `frontend/js/main.js` — one line: clear specs on `rebuild_failed`.
- `tests/test_specs_gate.py` — 5 new tests (caller branch, mesh-only gate
  failure, merge-base flag, bounded kernel timeout, memoized budget verdict);
  `_cold` now clears the assembly sidecar too.
- `tests/test_specs_api.py` — failing `spec_eval` cached and re-read for free.
- `tests/test_specs_kernel.py` — mutating-predicate isolation.
- `tests/test_specs.py` — undeletable `specs.py` is a `validation_error`.
- `docs/agent-api.md`, `AGENTS.md`, `docs/part-authoring.md`,
  `docs/superpowers/specs/2026-08-10-executable-design-specs-design.md` —
  as-built notes for S1–S4, S6, S7.

## Notes
- **Why cache a failure at all.** The same script and params produce the same
  `contract_error`, and the browser re-reads a part on every
  `rebuild_finished`, so caching only the successes made a hung predicate cost
  300 s and a worker respawn *per read*. The trade-off taken deliberately: a
  genuinely transient worker crash keeps a part red until its script/params
  change or `run_specs` runs. That is the fail-closed direction, and it is
  named in the tool description.
- **Why memoize a `budget_exceeded` verdict.** It is red with a stable reason
  for that head; the memo can only keep a red, never make one green, and
  re-paying an exhausted 30 s budget on every `proposal_get` (while
  `proposal_merge` holds the source turn lock) is the cost the memo exists to
  prevent.
- **Why the mesh-only clearance is red in the gate but a skip in a report.** A
  report is read by an engineer, who is better served by the reason and hint; a
  gate decides a merge, and "declared but never measured" is exactly the hole
  it exists to close.
- Evaluation order inside `spec_eval` is now explicitly not a contract: `that`
  predicates run last. The built shape is still shared (it cannot be copied), so
  a predicate that mutates the B-rep remains out of scope.
