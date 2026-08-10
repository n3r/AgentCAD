# 0083 — PRD-002 review fold-back: evidence that cannot be fabricated

- **Commit:** pending
- **Date:** 2026-08-10
- **Author:** Nikita Fedorov

## Summary
An independent review of the shipped change-proposals feature returned
CHANGES-REQUIRED with five reproduced majors and five minors, every one of them
a way the *evidence* could lie: a review packet generated after the merge that
described post-merge heads and attributed other people's commits to the
proposal; two interleavings in which a slow packet build un-merged a proposal
or un-froze its packet; a `comment` that silently retracted the commenter's own
approval; and a conflicted merge that landed through `resolve_merge` with the
proposal left at `approved` and the override it really ran under recorded as
`false`. All are fixed in the proposal layer — `merge.py` is untouched — each
behind a regression test written first against the reviewer's repro scripts.

## Changes

### R1 — a terminal proposal is never measured again (`packet.py`, `proposals.py`)
- `PacketBuilder.packet` refuses to build for a proposal in a TERMINAL state:
  a frozen packet is served as it stands, a non-frozen one is served without
  regeneration, and a terminal proposal with **no** packet is a
  `conflict_error`. `regenerate` on any of them stays a 409.
- `ProposalManager._freeze_packet` now takes the proposal (not just its id) and
  freezes the **absence** of a packet as a durable record —
  `{frozen: true, stale: false, generated: null, generated_by: null, ok: false,
  parts: [], note: "…"}` (`_absent_packet`) — so the "no packet was generated
  before the merge" answer survives a restart and can never be replaced by a
  fabricated one. `proposal_get`'s packet summary reads
  `{generated: null, stale: false, ok: false, frozen: true}`.
- Since a terminal proposal is never built for, nothing re-`_checkpoint`s the
  branch worktrees on its behalf.

### R2/R3 — one writer order for `proposal.json` and `packet.json`
- New `ProposalManager.record_packet(proj, pid, packet)`: writes `packet.json`,
  appends the `packet_generated` audit entry, updates the proposal's packet
  summary and publishes `proposal_changed`, **all under the manager's
  `RLock`**, re-reading the proposal's state first. A build overtaken by the
  merge is discarded — nothing is written — and `False` is returned.
- `PacketBuilder._persist` is now a thin delegation to it and returns `bool`;
  `build()` serves the frozen packet when its own result was discarded.
- Previously `_persist` did its own load→mutate→save outside any lock: one
  interleaving reverted `state: merged` / `merge` back to `approved` / `null`,
  the other wrote `frozen: false` over the packet the merge had just frozen.

### R4 — a `comment` counts for nothing (`proposals.py`)
- `_approvals_gate` keeps the latest **counted** verdict per actor
  (`_COUNTED_VERDICTS = ("approve", "request_changes")`). A comment after an
  approval used to displace it, leaving a proposal in state `approved` that
  could not merge behind a gate reporting "0 recorded". Comments are still
  recorded on the proposal and in the audit log.

### R5 — a conflicted merge cannot escape the proposal record (`proposals.py`)
- A conflicted `proposal_merge` records `staged_merge = {merge_id, source,
  target, source_head, target_head, allow_invalid, ts}` on the proposal (and
  the `merge_attempted` audit entry now carries `merge_id`).
- New reconciler — `reconcile()` / `_reconcile()` / `_landed_commit()` /
  `_finish_landed()` — runs on every read path (`get`, `list`, `merge`, and
  `PacketBuilder.packet`). If the recorded merge is still staged, nothing
  happens. If it **landed**, the proposal transitions to `merged` with the real
  commit (found on the target as the commit whose parents are exactly the two
  recorded heads — what `MergeOrchestrator._finalize` commits), its real
  parents, the `allow_invalid` the *staged* merge carried, and the verdict read
  back out of the commit message (`validation.recovered: true`,
  `merge.reconciled: true`), plus the `override` audit entry when the override
  applied. If it is gone without landing (`merge_abort`), the record is dropped
  and audited `merge_discarded`.
- `proposal_merge` on a proposal it reconciles in that same call returns the
  merge that landed (`already_landed: true`) instead of merging an ancestor —
  which is what used to write `allow_invalid: false` and `parents: []` over a
  merge that really ran with the override.
- Rejected alternative: subscribing to `merge_completed`. `EventBus.subscribe`
  hands out a `Queue` only a consumer thread can drain, and `on_publish` is a
  single slot the service already holds.

### R6 — the branch-delete guard is one critical section (`proposals.py`)
- The guard wrapper holds the manager's `RLock` across `_check_branch_free`
  **and** the delete, and `create()` moved its branch-existence check inside the
  same lock, so create-vs-delete serializes instead of racing.

### R7–R10, N1–N3 (minors)
- **R7** `PacketBuilder` keeps a per-`(project, id)` build slot
  (`{lock, builds}`): concurrent builds of one proposal are serialized and the
  waiting caller returns the build it waited for instead of clobbering its
  renders and diff meshes (and their shared fixed-name `.tmp` paths).
- **R8** A side whose checkout could not be *read* is `{"ok": false, "error":
  …}` with `geom_diff {"available": false, "reason": "the <side> side is
  unreadable"}` — never `present: false`, which made the geometric diff report
  the whole part as added or removed (the loudest number in the packet).
- **R9** `proposal_render` writes every render it draws to the `path` it
  reports (previously true only for the packet's own `iso` pair).
- **R10** A frozen packet serves **only** the renders stored with it; any other
  view is a `conflict_error`, like regenerating it.
- **N1** `packet.py` passes the service's `MESH_TOLERANCE` to `geom_diff`
  explicitly (same 0.1 default) so an overlay is faceted like the shapes it is
  drawn over.
- **N2** `kernel/handlers/diff.py` guards the two per-side `shape_volume`
  calls, so a failure there carries `details.stage` like the booleans do.
- **N3** `routes_proposals._json` reads the body rather than trusting
  `content-length`: a chunked request's body used to read as "no arguments".

## Files
- `agentcad/core/proposals.py` — counted verdicts; `staged_merge` + the
  reconciler; `record_packet`; `_freeze_packet(proj, proposal)` and
  `_absent_packet`; `_recovered_validation`; locked branch guard and creation.
- `agentcad/core/packet.py` — terminal refusal, build slots, `_persist` via the
  manager, frozen/persisting `render()`, unreadable sides, `geom_diff`
  tolerance.
- `agentcad/core/tools_proposals.py` — `proposal_review`/`proposal_packet`/
  `proposal_render`/`proposal_merge` descriptions restated for the new
  contracts.
- `agentcad/server/routes_proposals.py` — `_json` reads the body.
- `agentcad/kernel/handlers/diff.py` — guarded per-side volumes.
- `frontend/js/proposals.js` — `packetHeader()` renders the frozen "no packet"
  record as its note instead of `generated null by null`.
- `tests/test_proposals.py` — `test_a_comment_never_retracts_an_approval`,
  `test_a_comment_does_not_lift_a_request_for_changes`,
  `test_the_guard_and_proposal_creation_serialize`,
  `test_a_merge_with_no_packet_freezes_the_absence`,
  `test_a_conflicted_merge_finished_by_resolve_merge_is_reconciled`,
  `test_an_aborted_staged_merge_leaves_the_proposal_where_it_was`.
- `tests/test_packet.py` —
  `test_a_merged_proposal_serves_the_frozen_absence_of_a_packet`,
  `test_a_terminal_proposal_is_never_measured_again`,
  `test_a_build_that_loses_the_race_to_a_merge_is_discarded`,
  `test_a_merge_cannot_interleave_a_packets_read_modify_write`,
  `test_two_concurrent_builds_produce_one_packet_with_whole_assets`,
  `test_a_side_that_cannot_be_read_is_not_reported_as_an_absent_part`,
  `test_an_on_demand_render_is_written_to_the_path_it_reports`,
  `test_a_frozen_packet_only_serves_the_renders_taken_with_it`.
- `tests/test_proposals_api.py` —
  `test_a_body_without_a_content_length_is_still_read`.
- `docs/agent-api.md` — the `proposal_packet`/`proposal_render`/
  `proposal_review`/`proposal_merge` rows and the conflict-recovery trap.
- `AGENTS.md` — four new proposal gotchas (terminal packets, the single write
  order, counted verdicts, the staged-merge reconciler).
- `docs/user-guide.md` — the "Conflicts" paragraph now describes the
  reconciliation instead of "merge the proposal again so it is marked merged".
- `docs/superpowers/specs/2026-08-10-change-proposals-design.md` — new
  "As built — the review fold-back" section and divergences 13–15.

## Notes
- The three race tests force their interleavings with monkeypatched hook points
  (`PacketBuilder._persist`, `PacketBuilder._renders`, `ProposalStore.save`)
  rather than sleeping and hoping; the "merge waits" assertion is a 1.5 s
  liveness window, which can only fail in the direction of the bug.
- `merge.py` and PRD-001's merge path are untouched — the reconciler reads git
  and the proposal record only.
- A proposal that reconciles to `merged` on a *read* (`proposal_get` polling in
  the UI) makes a subsequent `proposal_merge` the ordinary "already merged"
  `conflict_error`; only the call that performs the reconciliation returns the
  `already_landed` payload. Both are honest; the second is what keeps the
  documented recovery flow working.
- Follow-up not taken: `packet.json` still has no schema version, so the frozen
  "absent packet" record is recognised by `generated: null`.
