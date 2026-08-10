# 0091 — `evaluate_specs` and the fail-closed `specs` proposal gate

- **Commit:** pending
- **Date:** 2026-08-11
- **Author:** Claude

## Summary
PRD-003 Slice 5: design specs become **enforceable**. `SpecRunner` grows
`evaluate_specs(project, ref)` — a gate-shaped verdict for any branch — and
`gate_provider()`, one callable appended to PRD-002's `service.gate_providers`
that replaces its `specs` placeholder. The gate is **fail-closed**: a declared
check that failed, errored, or was never evaluated is red, a red gate blocks
`proposal_merge`, and `allow_invalid` does **not** waive it. `proposals.py`,
`merge.py` and `packet.py` are not edited — the provider seam is the whole of
the enforcement surface (FR11, AC7).

## Changes

### `evaluate_specs` — `agentcad/core/specs.py`
- **`SpecRunner.evaluate_specs(proj, ref=None)`** returns
  `{available, status: green|red|skip|pending, ref, head, checked_at, summary,
  failures, skips, errors, reason}`. A named ref is resolved and pinned by the
  existing `_pinned` helper (`history.resolve_branch` → `branches.pinned(proj,
  branches.tree_of(proj, ref))`), so a tag named like a branch is still a
  `validation_error` (PRD-001 X1) and a `ref` without git is still a
  `validation_error` naming git.
- **The head is read before *and* after** the evaluation. A head that moved
  makes the result `pending` — `available: false`, `reason: "head_moved"`,
  plus `moved_to` — rather than a verdict wearing a commit it did not measure
  (the review packet's re-read rule, one layer up). `pending` is reserved for
  exactly this condition: it is the only one a retry resolves.
- **`available`** means "a verdict for `head` was produced": true for
  green/red/skip, false only for `pending`.

### The wall-clock budget (`GATE_BUDGET_S = 30.0`)
- `_report` gained an optional `deadline` argument, threaded **only** from
  `evaluate_specs`. `run_specs` passes `None` and stays unbounded by design:
  an engineer who asked for a full report asked for its cost.
- On exhaustion each not-yet-reached declaring part (and the project scope, if
  a `specs.py` exists) yields one synthetic `error` record of kind
  `"unevaluated"` via `_unevaluated` / `_unevaluated_part`, which makes the
  report **red**. One record per scope rather than one per declaration: naming
  them individually would cost the very kernel round trip the budget just ran
  out of. A part that declares nothing is still absent — "silent" is not
  "unmeasured".
- `evaluate_specs` reports that as `reason: "budget_exceeded"` and the gate
  summary names `run_specs` as the exact fix.

### The gate provider (design Decision 7)
- **`SpecRunner.gate_provider()`** returns a closure literally named `specs`,
  for two reasons: `ProposalManager.gates` replaces a built-in gate of the same
  name (so there is one `specs` gate, never six gates), and its own
  except-branch names a gate after the provider function, so even a bug in here
  cannot produce a differently named gate.
- It **catches everything itself** and always returns a dict. PRD-002's
  fallback degrades a raising provider to `pending`, which is precisely the
  not-fail-closed outcome this gate exists to prevent; an internal failure is
  therefore `fail` with `details.reason = "evaluation_failed"`.
- State mapping:

  | state | when |
  |---|---|
  | `skipped` | the source ref declares no specs at all |
  | `pass` | everything declared was evaluated and nothing failed or errored (skips are allowed and are **named** in the summary) |
  | `fail` | anything failed or errored, **or** could not be evaluated — a kernel error, a source branch that will not build, an exhausted budget |
  | `pending` | the source head moved mid-evaluation |

- `details` carries `{status, summary, failures, skips, errors, ref,
  source_head, specs_py_changed, reason}`. The gate's own `summary` is a
  sentence; `details.summary` is the counts dict.
- **Every red summary names its exit**, because the gate is a hard block: a
  measurement failure names the failing check ids and states that
  `allow_invalid` does not waive this gate; an *unmeasured* one names
  `run_specs`. That wording lives in one place, `_gate_wording`.
- **The gate measures the proposal's SOURCE branch, not a merge preview.** A
  preview needs a staged merge tree that does not exist before the merge, and
  "will the merged result be green" at project scale is PRD-004's question. The
  provider records the head it measured in `details.source_head`; PRD-002
  already evaluates gates inside `_holding_source` and audits a
  `gate_head_mismatch`, so the verdict cannot be pinned to a head that moved
  under it.

### `details.specs_py_changed`
- One `history._run(canonical, "diff", "--name-only", target_head,
  source_head, "--", "specs.py")` — through `ProjectHistory._run`, never a raw
  `subprocess`, so the hermetic git environment applies. It closes a review
  hole nothing else covers: `packet.py` builds diff rows only for
  `parts/*.py` and `merge._validate` only revalidates *changed parts*, so a
  proposal that **weakens a spec** would otherwise produce no row, no
  validation and no signal. A full `specs` section in the packet is a
  `packet.py` change and remains out of scope (a named PRD-002-Phase-2 /
  PRD-008 gap).

### Cost control
- **The shared canonical `.cache/`** already makes every part the source branch
  did not change a disk read on both sides.
- **A per-runner memo**, bounded LRU (`_GATE_MEMO_LIMIT = 32`), keyed by
  `(project, ref, head)` — and reused, under a commit-pair key, for the
  advisory `specs_py_changed` flag so a warm read pays one git call rather than
  four. Nothing is memoized without a head (no git, no key) or for a
  `pending`/budget-exhausted result (neither is a verdict worth keeping).
- **`GATE_BUDGET_S`** bounds the worst case, fail-closed.

### Registration — `agentcad/core/tools_specs.py`
- **`install_specs_gate(service)`**: appends the provider when
  `service.gate_providers` exists (it is absent when git is, because
  `tools_proposals` self-disables — not an error, there is simply nothing to
  gate). Idempotent **by name**: `build_registry` may run twice over one
  service, and two providers named `specs` would evaluate the gate twice and
  then have one silently overwrite the other.
- The `run_specs` description now states the gate rule inline — red blocks
  `proposal_merge`, an unevaluated check blocks too, and `allow_invalid` does
  not waive it — because the tool description is an agent's only documentation
  at call time, and this is the decision most likely to be "fixed" wrongly
  later.

## Measurements

`proposal_get` (`ProposalManager.get`) on a copy of `examples/rocketry`
(4 parts; `nozzle` declaring `check_valid` + `check_wall` + `check_mass`, a
root `specs.py` declaring `check_interference_free`), source branch `feat` with
`wall` raised, macOS / M-series, one warm kernel worker:

| read | wall clock |
|---|---|
| no gate provider at all (PRD-002 baseline) | **60 ms** |
| cold — memo cleared, `*.specs.json` sidecars deleted | **373 ms** |
| warm — same head, memo hit | **130 ms** |
| after a head move (memo invalidated, sidecars still valid) | **308 ms** |
| ice cold — whole `.cache/` wiped, so the nozzle rebuilds | **441 ms** |
| warm after that | **125 ms** |

So the gate costs roughly **+310 ms cold and +65 ms warm** on a real
four-part project, worst case +380 ms when nothing has ever been built. That is
well inside an interactive proposal read, so **the escape hatch is not needed**:
persisting the report beside `packet.json` at packet-build time stays a
separate, unimplemented slice (it touches `packet.py`). The warm figure is
mostly git subprocesses, not kernel work — the memo means zero kernel calls,
asserted by a test.

## Verification

- `uv run pytest -q tests/test_specs_gate.py` → **18 passed**.
- `uv run pytest -q tests/test_proposals.py tests/test_proposals_api.py
  tests/test_specs.py tests/test_specs_api.py tests/test_specs_gate.py` →
  **174 passed**.
- `make test-fast` → **717 passed, 1 skipped**.
- `make test` → **871 passed, 1 skipped** (853 + 1 before this slice; the 18
  new tests are exactly `tests/test_specs_gate.py`).

## Files
- `agentcad/core/specs.py` — `evaluate_specs`, `gate_provider`,
  `_specs_py_changed`, `_head_of`, the `_memo_get`/`_memo_put` LRU, the
  `deadline` parameter on `_report` plus `_out_of_budget` / `_unevaluated` /
  `_unevaluated_part`, and the module-level `_named` / `_gate_wording` wording
  helpers. `GATE_BUDGET_S` now has a consumer; `_GATE_MEMO_LIMIT` and
  `_UNEVALUATED` are new.
- `agentcad/core/tools_specs.py` — `install_specs_gate`, called from
  `register`; the gate paragraph added to the `run_specs` description.
- `tests/test_specs_gate.py` — new, 18 tests in four sections
  (`evaluate_specs`, the gate provider, the gated merge, cost).
- `tests/test_proposals.py` — **one line**, the edit the plan sanctioned:
  `test_specs_and_checks_are_skipped_with_no_providers` sets
  `service.gate_providers = []` before its own
  `assert getattr(service, "gate_providers", None) in (None, [])`, which is
  what the test's name says it is testing.
- `tests/test_proposals_api.py` — **one line**, an unplanned but forced edit:
  `test_the_pack_installs_the_service_seams` asserted
  `service.gate_providers == []` right after `build_registry`, which the
  design's own `register()` snippet makes false. It now asserts the seam holds
  exactly one provider, named `specs` — a stronger statement of the same
  invariant. See Notes.

## Notes

- **Why fail-closed on *unevaluated*.** A declared-but-unmeasured spec is not
  evidence of green; treating it as green would let a proposal merge by simply
  never running. This is the deliberate divergence from PRD-002's default (a
  provider outage degrades to `pending`) and the one its as-built note reserved
  for this PRD. The cost of being wrong the other way is a merge that violates
  stated intent, which is the entire problem PRD-003 exists to solve. Revisit
  only on a user report, and then as a `policy.json` field (`specs_required`),
  never as a silent default change.
- **`allow_invalid` is asserted explicitly not to waive the gate**, in its own
  test, because that is the decision most likely to be "fixed" wrongly later.
  It reaches PRD-001's kernel gate and nothing else.
- **A gate-blocked merge writes no audit entry.** `ProposalManager.merge`
  raises its `ConflictError` before any `append_audit`, and `proposals.py` is
  off-limits to this PRD, so the plan's "the audit log carries the blocked
  attempt" is only true for merges blocked *inside* PRD-001 (`merge_attempted`
  / `blocked`). The tests assert what is true: nothing merged, no staged merge
  left behind, and the merge recorded once it lands.
- **Memo key deviation.** The design named `(project, source_head,
  declaration_hash)`. The implementation uses `(project, ref, head)`: for a
  branch the head *is* the declaration hash — PRD-001 snapshots every write, so
  an edited `SPECS` moves it — and computing a separate hash would require
  materializing the ref's tree first, which is exactly the work the memo exists
  to skip.
- **AC7's tag half is not implementable as the plan words it.** The plan asks
  for `evaluate_specs(proj, ref=<tag>)` to be green, but the design's own X1
  rule (and Slice 3's shipped, tested behaviour) makes a tag a
  `validation_error` — a tag must never answer for a branch. Slice 5 therefore
  tests green-for-a-good-branch / red-for-a-branch-with-a-broken-budget; Slice
  7's `test_ac7_*` should be worded the same way.
- **Known gap, unchanged:** a proposal that weakens a spec still has no packet
  row. `details.specs_py_changed` is the flag, not a fix.
