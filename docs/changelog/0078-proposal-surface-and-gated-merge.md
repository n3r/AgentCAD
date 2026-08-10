# 0078 — Proposal tool pack, route pack, events and the gated merge

- **Commit:** pending
- **Date:** 2026-08-10
- **Author:** Nikita Fedorov

## Summary
Slice 2 of PRD-002: the change-proposal *surface* — six tools, six routes, the
`proposal_changed` event, the branch-delete guard and `proposal_merge`, the
gate in front of PRD-001's merge. With this, a proposal is a working "CAD PR
without evidence": open it, review it, and merge it through a policy gate that
records every override. The review packet (the evidence) is slices 3–4.
`merge.py`, `branches.py`, `history.py`, `worker.py`, `tools.py`, `app.py` and
`service.py` are untouched; everything arrives as a tool pack, a route pack and
two methods on `ProposalManager` (AC5, AC6, AC8, AC9).

## Changes
- **New tool pack `agentcad/core/tools_proposals.py`** — `register()` installs
  `service.proposals` (a `ProposalManager`) and `service.gate_providers` (the
  empty list PRD-003/PRD-004 append to), then registers six tools:
  - `proposal_create {project*, source*, title*, target?, description?, draft?}`
    → `{proposal, gates, packet: null}`
  - `proposal_list {project*, state?}` → `{proposals, counts}`
  - `proposal_get {project*, id*}` → `{proposal, gates, audit, packet}`
  - `proposal_update {project*, id*, title?, description?, state?}` → `{proposal, gates}`
  - `proposal_review {project*, id*, verdict*, summary?}` → `{proposal, gates}`
  - `proposal_merge {project*, id*, allow_invalid?}` → the PRD-001 merge
    payload + `{proposal, gates}`, or `merge_conflict`, or a blocked
    `validation_error`.

  The pack **self-disables without git** (`history.available()`), so no tool,
  no `service.proposals` and no `gate_providers` — the FEM/versioning
  precedent. Descriptions restate the PRD-001 convention (**old = target =
  ours, new = source = theirs**), name `resolve_merge` as the conflict
  follow-up, and state that `allow_invalid` reaches the kernel validation gate
  **only** — never the approvals policy.
- **`ProposalManager.merge(proj, id, allow_invalid=False)`** (in
  `agentcad/core/proposals.py`), per the design spec's Decision 7:
  1. `draft` is a `conflict_error` ("open it first"), `merged`/`closed` too;
  2. gates are evaluated and the **first `fail` refuses before anything is
     merged**, with all of them in `details.gates` and `details.failing`;
  3. `service.merges.merge(...)` runs **unchanged**, and its three outcomes are
     forwarded:
     - **success** → `merge = {commit, parents, ts, allow_invalid,
       fast_forward, validation}` recorded on the proposal, state → `merged`,
       any `packet.json` marked `frozen` (FR12), audit `merged` (plus an
       `override` entry when `allow_invalid` landed a failed validation),
       `proposal_changed {reason: "merged"}` published, and the merge payload
       returned with `{proposal, gates}` added;
     - **`merge_conflict`** (returned, never raised) → passed through
       **verbatim** with only `details.proposal` added; the proposal's state
       does not change and the audit records `merge_attempted {outcome:
       "conflict"}`. The staged merge lives in PRD-001's `merge.json`, so
       `resolve_merge` then a second `proposal_merge` completes it;
     - **blocked `validation_error`** (`details.validation` present) →
       re-raised with `details.proposal` added; audit `merge_attempted
       {outcome: "blocked"}`; a retry with `allow_invalid: true` lands it.
  No locking is added: `MergeOrchestrator._holding_target` already holds the
  target's turn across validation and finalization.
- **The branch-delete guard (FR2)** — `ProposalManager.ensure_branch_guard()`
  wraps the bound `BranchManager.delete`, refusing with a `conflict_error`
  (`details = {proposal, branch, role, state}`) when an **active** proposal
  names the branch as its `source` **or** its `target`. One hook covers the
  tool, the REST route and the UI, and `branches.py` stays untouched — the
  `install_write_guard` precedent. It is installed **lazily and idempotently**
  (a `_proposal_guard` attribute on the wrapper) from every `ProposalManager`
  entry point and from `routes_proposals.build_router`, because
  `tools_proposals` is imported *before* `tools_versioning` and
  `service.branches` does not exist at `register()` time. With no proposals on
  disk the guard returns before touching the store, so PRD-001's delete
  behavior is unchanged (and `tests/test_branches.py` passes unedited).
- **New route pack `agentcad/server/routes_proposals.py`** — registry
  passthroughs with `routes_versioning`'s `_RAISE`/`_BODY_ERRORS`/`_result`/
  `_body_keys`/`_json` helpers reused verbatim:
  `GET|POST /api/projects/{proj}/proposals`,
  `GET|PATCH …/proposals/{pid}`, `POST …/{pid}/review`, `POST …/{pid}/merge`.
  Body keys are whitelisted per route (never `**body`) and `null` reads as
  "omitted". `merge_conflict` is the single error type answered at HTTP **200**
  with an `{"error": …}` body; `notfound`/`validation`/`conflict` map to
  404/422/409 and **every other type, `invalid_arguments` included, is a 422**.
  The router is empty when the tool pack registered nothing (no git).
- **Event:** `proposal_changed {project, id, state, reason}` with
  `reason ∈ created | updated | review | merged` now covers the merge too, and
  reaches a second client live over the WebSocket. It is not `project_changed`,
  so it never triggers a git snapshot (asserted).

## Files
- `agentcad/core/tools_proposals.py` — new (~210 lines): the pack, the two
  seams, six tools.
- `agentcad/server/routes_proposals.py` — new (~115 lines): six routes.
- `agentcad/core/proposals.py` — added `merge()`, `ensure_branch_guard()`,
  `_check_branch_free()`, `_merges()`, `_freeze_packet()`; `_branches()` and
  the four other public entry points now install the guard; module docstring
  updated (the module no longer "never merges" — it gates and delegates).
- `tests/test_proposals_api.py` — new: 14 tests in the three
  `test_versioning_api.py` sections (registration incl. the description
  contract, the no-git degradation and the load-order/laziness proof · routes
  incl. body whitelisting, the 404/409/422 mapping and the 200 `merge_conflict`
  · events incl. the WebSocket and the no-snapshot assertion).
- `tests/test_proposals.py` — extended (created on this branch by slice 1) with
  section 7 (branch-delete guard, 2 tests) and section 8 (`proposal_merge`, 7
  tests: draft/terminal refusal, AC6 policy incl. "`allow_invalid` does not
  bypass approvals", the `changes_requested` state gate, packet freezing, the
  two-parent happy path, AC5 blocked-then-overridden validation, and the
  verbatim conflict pass-through resolved with `resolve_merge`), plus the
  `parts` fixture and the `_on`/`_script`/`_propose_and_approve` helpers.

## Notes
- **Load order is the trap.** `tools._load_tool_packs` walks
  `pkgutil.iter_modules` alphabetically, so `tools_proposals` sorts *before*
  `tools_versioning`: `service.branches` / `service.merges` are absent at
  `register()` time. Verified empirically by
  `test_the_pack_is_imported_before_the_versioning_pack` (asserts the module
  order) and `test_the_manager_takes_service_branches_lazily` (a manager built
  against a branch-less service constructs fine and fails a `create` with a
  message naming git). Never rename the pack to "fix" this — take the seam
  lazily.
- **`allow_invalid` overrides the kernel gate only.** A test asserts explicitly
  that it does *not* satisfy the approvals policy: `allow_invalid` is the
  caller's statement about the kernel's verdict on geometry, and letting it
  waive a human approval would make one field mean two unrelated things. v1 has
  no policy override at all (set `approvals_required: 0` in `policy.json`).
- **The conflict payload is passed through verbatim** — `set(result) ==
  {"error"}` is asserted — because the UI's existing conflict modal and
  `resolve_merge` key off PRD-001's exact shape. The only addition is
  `details.proposal`.
- A merge whose source never diverged returns PRD-001's
  `{"already_up_to_date": true}`; the proposal is still resolved by it and
  moves to `merged`. That is also what a second `proposal_merge` sees after
  `resolve_merge` has landed the staged merge — which is exactly how the
  conflict → resolve → merge loop completes.
- The guard is also installed from `build_router`, not only on first
  proposal-manager use: after a server restart a client might delete a branch
  before any proposal call has happened, and route packs are mounted after all
  tool packs, which is the earliest point at which `service.branches` exists.
  (Small deviation from the plan's "on first proposal-manager use", in the
  safe direction.)
- Deferred to slice 4 as planned: `proposal_packet` / `proposal_render` and the
  packet/render/diff routes. `proposal_get` already returns the packet summary
  (`null` until then), and `merge()` already freezes a packet found on disk, so
  slice 4 only has to write one.
- Verification: `uv run pytest tests/test_proposals.py tests/test_proposals_api.py -q`
  → 58 passed; `uv run pytest tests/test_branches.py tests/test_versioning_api.py
  tests/test_server.py tests/test_tools.py -q` → 82 passed;
  `make test-fast` → 529 passed, 1 skipped; `make test` → **568 passed,
  1 skipped** (the 545/1 baseline of `0077` plus this slice's 23 cases, with
  every pre-existing test unedited).
