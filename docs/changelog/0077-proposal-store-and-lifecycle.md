# 0077 — Proposal store, state machine, audit log, policy and gate seam

- **Commit:** pending
- **Date:** 2026-08-10
- **Author:** Nikita Fedorov

## Summary
Slice 1 of PRD-002 (change proposals & geometric diff): the proposal *core* —
a durable, attributed object with a governed lifecycle, an append-only audit
log, a merge policy and the gate list that `proposal_merge` will consult.
Pure core, no surface: nothing imports `agentcad/core/proposals.py` yet, so
landing it changes no behavior. The tool pack, the route pack and the gated
merge are slice 2; the review packet is slice 4.

## Changes
- **New module `agentcad/core/proposals.py`** with the interfaces slices 2–5
  consume:
  - constants `STATES` / `TERMINAL` / `ACTIVE` / `VERDICTS` / `DEFAULT_POLICY`
    (`{"approvals_required": 1, "self_approve": False}`);
  - `actor_kind(identity)` — `"human"` iff the identity is `browser` (or
    `browser:*`), everything else (`chat`, `chat:main`, `mcp`, `local`, an
    agent id) is `"agent"`. Bookkeeping, not authentication, until PRD-005;
  - `ProposalStore` — files only, no policy, no git, no events:
    `dir_of` / `load` / `save` / `list` / `allocate_id` / `append_audit` /
    `audit` / `policy` / `packet_path` / `asset_dir`;
  - `ProposalManager` — `create` / `list` / `get` / `update` / `review` /
    `gates` / `transition`, each returning post-state
    (`{proposal, gates}`, plus `audit` + `packet` from `get`).
- **Storage layout** under `<project>/.history/agentcad/proposals/`, beside
  PRD-001's `config.json`/`checkouts.json`/`tags.json`/`merge.json`:
  `index.json`, optional `policy.json`, and `<id>/proposal.json` +
  `audit.jsonl` (+ `packet.json`, `renders/`, `diff/` for slice 4). Every path
  comes from `store.canonical_path_of`, never `path_of`, so a proposal is
  visible from every branch and belongs to none. Because `.history/` is inside
  GIT_DIR, `project_restore` structurally cannot rewind proposal state (FR3).
- **Writes:** `proposal.json` and `index.json` go through
  `ProjectStore._atomic_write`; `audit.jsonl` is *appended*
  (`open(path, "a")` + `flush()`) and never rewritten — FR14 makes it
  append-only, and a read-modify-replace cycle would both lose that property
  and risk truncating the log. There is deliberately no method that edits or
  removes an entry (a test asserts the audit API is exactly
  `{append_audit, audit}`).
- **Identity:** ids are decimal strings from `1`, allocated under the store's
  RLock from `index.json`'s `next_id`, which only ever increments — a proposal
  directory removed by hand does not hand its id to the next proposal.
  `index.json` is a cache: `ProposalStore.list` reads the per-proposal
  directories and rewrites the index whenever it is missing, unparseable or
  disagrees.
- **State machine:** the design spec's table
  (`draft→open|closed`, `open→approved|changes_requested|closed|merged`,
  `approved→changes_requested|open|closed|merged`,
  `changes_requested→open|approved|closed|merged`, `closed→open`, `merged`
  terminal). Anything else is a `ValidationError` whose `details` carry
  `{id, from, to, allowed}`. `proposal_update` may only drive `open`/`closed`
  (`_UPDATABLE`), so an approval cannot be faked by writing a state and a
  merge cannot be faked at all. A `comment` verdict is a legal same-state
  move while the proposal is active, and refused once it is terminal.
- **Attribution (FR13):** every action records `locks.current_client_id()` and
  its derived `actor_kind`; audit actions are `created`, `updated`,
  `state_changed`, `closed`, `reopened`, `reviewed` (slice 2 adds
  `merge_attempted`, `merged`, `override`; slice 4 adds `packet_generated`),
  each with a monotonic `seq` assigned from the file's line count. A corrupt
  line is skipped on read, not raised.
- **Creation rules:** `target` defaults to `branches.default_branch(proj)` —
  **not** the caller's branch, because a proposal is a durable object other
  clients read; `source == target` is a `ValidationError`; both branches
  resolve through `history.resolve_branch` (never `resolve_ref`, so a tag
  named like a branch cannot answer for it); a second proposal for the same
  `(source, target)` while one is in any `ACTIVE` state is a `ConflictError`
  with `details.existing_id` (FR2).
- **Policy and gates (FR11):** `gates()` returns
  `[state, approvals, validation, specs, checks] + providers` with
  `state ∈ pass|fail|pending|skipped`. Approvals count distinct actors whose
  *latest* verdict is `approve`, excluding the author unless
  `self_approve`; `validation` is `pending` until a merge fills it (it *is*
  PRD-001's validation pass — pre-evaluating would double the kernel cost);
  `specs`/`checks` are `skipped`. The PRD-003/PRD-004 seam is
  `service.gate_providers`, a list of `(project, proposal) -> gate | None`
  callables read at call time; a provider gate replaces a built-in of the same
  name, and a provider that raises degrades to a `pending` gate named after
  the callable instead of propagating.
- **Staleness:** each review records the `source_head` it was made against;
  `get()` returns reviews annotated `stale: true` when the source branch has
  moved and the approvals gate says so in its summary — but the approval still
  counts in v1 (dismissal is a third policy field, Phase 2).
- **Event:** `create`/`update`/`review` publish
  `proposal_changed {project, id, state, reason}` on the bus. It is not
  `project_changed`, so it never triggers the snapshot hook.
- **Status metadata corrected:** PRD-002 had already moved to
  `docs/prd/in-progress/` while the roadmap row still said `pending` and
  linked `prd/pending/…`. Row, link and the PRD's own `Status:` line now
  agree, per `docs/prd/README.md`'s "location = status" rule.

## Files
- `agentcad/core/proposals.py` — new (716 lines): `actor_kind`,
  `ProposalStore`, `ProposalManager`, the transition table and the gate
  builders.
- `tests/test_proposals.py` — new: 32 test functions / 35 cases in six sections (store and
  identity · state machine · creation rules · attribution and audit ·
  durability · policy and gates). Carries `integration` + `portability` +
  the git skipif; no test here builds geometry except the `project_restore`
  case, which needs a real part to rewind.
- `docs/roadmap.md` — PRD-002 row: `pending` → `in progress`, link retargeted
  to `prd/in-progress/`.
- `docs/prd/in-progress/PRD-002-change-proposals-geometric-diff.md` —
  `Status:` line matches the folder, naming the design spec and plan.
- `docs/superpowers/specs/2026-08-10-change-proposals-design.md`,
  `docs/superpowers/plans/2026-08-10-change-proposals.md` — the approved
  design spec and six-slice implementation plan this work follows (written in
  the design cycle, first tracked here).

## Notes
- **Nothing imports this module yet.** Slice 1 is inert by construction:
  `tools_proposals.register` (slice 2) is what installs `service.proposals`
  and `service.gate_providers`.
- `ProposalManager` reads `service.branches` **inside** methods, never in
  `__init__`, and raises a `ValidationError` naming git when it is absent —
  `tools._load_tool_packs` walks `pkgutil.iter_modules`, so `tools_proposals`
  will be imported *before* `tools_versioning` and `service.branches` will not
  exist at `register()` time.
- `branches._ensure_history` is called from `create` (the `MergeOrchestrator`
  precedent) so a project that was never mutated still gets its repo and first
  snapshot before a proposal names a branch on it.
- `transition()` mutates in memory and appends the audit entry; the caller
  saves. Every caller in this module saves immediately afterwards, and every
  transition is validated *before* any mutation, so a refused update leaves
  neither a half-applied edit nor a stray audit line.
- The `state` gate keys off the proposal's **state**, not off the latest
  verdict, so `changes_requested → open` (the author re-requesting review)
  clears the gate — otherwise reopening would be inert.
- Two forward-looking pieces are already here because the core is their only
  sensible home: the `proposal_changed` publish and `get()`'s `packet`
  summary (which reads `packet.json` when slice 4 writes one, and returns
  `None` until then).
- Verification: `uv run pytest tests/test_proposals.py -q` → 35 passed;
  `make test-fast` → 509 passed, 1 skipped; `make test` → **545 passed,
  1 skipped** in 18:22 (the 510/1 baseline of `0076-prd-001-completed.md`
  plus this file's 35 cases, with every pre-existing test unedited —
  `git diff --name-status -- tests/` shows only the new file).
