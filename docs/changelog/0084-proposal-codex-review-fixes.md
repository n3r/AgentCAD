# 0084 — the gate you could walk past: the held merge, and seven more

- **Commit:** pending
- **Date:** 2026-08-10
- **Author:** Nikita Fedorov

## Summary
A second independent review of the change-proposals feature found that 0083 had
fixed the *bookkeeping* of a conflicted proposal merge without fixing the
**gate bypass underneath it**: `resolve_merge` still landed the branch the
moment the last conflict was resolved, so a proposal set to
`changes_requested` — or one whose gate had gone red — merged anyway, and the
reconciler's job was to write down what had already happened. A staged merge is
now HELD by the proposal that staged it (`held_by`, the one seam this PRD adds
to `merge.py`), and only `proposal_merge` lands it, after re-evaluating the
gates. Seven more findings follow the same rule as 0083's: every one of them a
way the evidence, or the decision it supported, could be less true than it
looked. Each fix has a regression test written first.

## Changes

### C1 — a merge a proposal staged is not finalizable by anything else
(`merge.py`, `proposals.py`, `merge.js`)
- `MergeOrchestrator.merge` takes an optional **`held_by`**; the staged state
  carries it and `resolve` inherits it across re-stagings (`resolve` rebuilds
  the state dict, and a hold a resolution could drop is no hold). At zero
  outstanding a merge that was *already staged* and is held returns
  `{merged: false, held: true, held_by, merge_id, source, target,
  outstanding: 0, resolved, hint}` and lands nothing. `merge_abort` still
  discards it, and a merge nobody holds is byte-for-byte what it was.
- **`finalize_held(project, allow_invalid=None)`** is the only way a held merge
  lands. It is `_merge` with `honor_hold=False` — the same validation pass, the
  same `_verify_clean`/`commit-tree`/CAS/`_finalize`, nothing duplicated — plus
  the `heads_moved` and `outstanding` refusals `resolve` already makes.
- `ProposalManager.merge` re-evaluates the gates **fresh** and then either
  starts a merge (`held_by="proposal:<id>"`) or, when the orchestrator already
  holds one for this pair at zero outstanding, calls `finalize_held` with the
  override the staged merge ran under (a stronger `allow_invalid` on this call
  wins). A red gate refuses with the staged merge untouched and the target
  where it was.
- `merge_status`/`_summary` and the `merge_conflict` payload carry
  `held`/`held_by`, and the conflict modal reads them: the footer says "held by
  proposal:N" and **Complete merge** becomes a disabled **Complete in the
  proposal**. Resolving the last conflict there toasts where to finish it.
- The reconciler stays as a safety net for merges staged *before* the hold
  existed. It had to learn one thing: `resolve_merge` re-stages under a **new
  merge id** every time it records a resolution, so `held_by` — not the id — is
  the stable identity of "this proposal's merge"; the id alone read a resolved
  conflict as a discarded merge.

### C2 — a packet frozen behind the commits that merged says so (`proposals.py`)
- Freezing sets `stale: false` (a pinned packet cannot be stale against today's
  heads) and that swallowed the one staleness that matters. `proposal.merge`
  now records **`heads`** — the two commits the merge really consumed, a
  fast-forward's `previous`/`commit` included — and `_freeze_packet` compares
  the packet's pinned heads with them, writing **`stale_at_merge`**. Surfaced
  in `proposal_get`'s packet summary and as a UI chip beside "frozen".

### C3 — packet evidence is pinned to heads it re-reads (`packet.py`)
- The heads were read up front; metrics, renders and booleans came off the live
  worktrees afterwards, so a mid-build commit produced numbers from one
  revision under a label naming another. `build()` is now a loop over a new
  `_measure()`: both heads are re-read when measuring finishes, a moved head
  discards the build and takes it again, and a head that moves a second time
  persists the packet marked `stale` with a warning.
- Each side's `_checkpoint` runs under that branch's turn (`_holding`,
  `_holding_target`'s pattern) — best effort: a turn someone else holds is not
  a reason to refuse a review packet, and the head re-read is the guarantee.

### C4 — gate evaluation and the merge hold the source turn (`proposals.py`)
- Gates ran, then `MergeOrchestrator` resolved the source ref: another client
  could commit in between and land content no gate had seen.
  `_holding_source` holds the SOURCE branch's turn across both. The lock order
  is fixed — proposal takes the source, orchestrator takes the target — and
  cannot deadlock, because `TurnLock.acquire` raises instead of blocking.
- The head the gates saw is recorded as `merge.gates_source_head`; a merge that
  consumed a different one is audited `gate_head_mismatch` (prevention is the
  lock; this is the record if it ever fails).

### C5 — `params_diff` covers the scripts' PARAMS declarations (`packet.py`)
- It compared manifest *overrides* only, so changing a `default`, `min`, `max`
  or `type` in the script produced an empty structured diff beside a full
  script diff (FR4). New `params_spec(script)` reads the declaration with
  `ast.literal_eval` — never `exec`; the kernel is the only thing that runs a
  script, and a declaration it cannot read literally yields `{}` rather than a
  guess. `params_delta` takes both sides' specs and appends rows carrying
  `"source": "spec"` with a `"spec.<field>"` field name. Override rows are
  unchanged, so every existing consumer still reads.

### C6 — a failed git read is an error, not empty evidence (`packet.py`)
- A `cat-file` that returned non-zero became `{}` — which the delta reads as
  "this side removed every part" — and both `git diff` callers ignored their
  return codes, while the packet still said `ok: true`. `_manifest_at`,
  `_changed_paths` and `_script_diffs` check the return code and record a
  `_git_error` entry: `{part: null, stage, fatal: true, command, ref, error}`.
  `ok` is now `not any(fatal)`. A failed manifest read also adds a warning
  saying the part rows below are not what the proposal changes. FR8's per-part
  degradation is untouched and still keeps `ok: true`.

### C7 — assets belong to a generation (`packet.py`, `routes_proposals.py`)
- Regeneration clears the **whole** diff directory before writing, not only the
  parts it processes: a part a later packet no longer contains kept serving the
  previous generation's mesh from its predictable URL.
- The asset route maps a read failure to 404 (`NotFoundError`) instead of 500 —
  a regeneration can unlink the file between `is_file()` and `read_bytes()`.

### C8 — the id high-water mark is not a cache (`proposals.py`)
- "Ids are never reused" rested on `index.json`, which is explicitly
  rebuildable: delete the newest proposal's directory, lose the index, and the
  highest id came round again. `proposals/next_id` is a one-line file written
  **before** the directory it names exists (a crash between the two costs an
  id, it never reuses one), and every rebuild takes `max(scan, high-water)`.

## Files
- `agentcad/core/merge.py` — the ONLY seam: module docstring (a `held_by`
  paragraph), `merge(..., held_by=None)` → `_merge`, `finalize_held`,
  `_merge(..., held_by, honor_hold)` + `was_staged` + the `held_by` state key
  and the hold check, `_held_payload`, `held_by` in `_conflict_payload`,
  `held`/`held_by` in `_summary`. No other behavior moved.
- `agentcad/core/proposals.py` — `_orchestrate` (hold-aware merge/resume),
  `_holding_source`, `_hold_key`, `gates_source_head`/`heads`/
  `gate_head_mismatch`, `_stale_at_merge` + `stale_at_merge` in the freeze,
  the absent packet and the packet summary, the reconciler's hold-aware
  "still staged" test, the `next_id` high-water mark.
- `agentcad/core/packet.py` — `build`/`_measure` split with the head re-read,
  `_heads_now`, `_holding`, `_clear_diff_assets`, `params_spec`/`_spec_rows`,
  `_script_at`, `_git_error` + return-code checks in `_manifest_at`/
  `_changed_paths`/`_script_diffs`, `ok` from `fatal` errors.
- `agentcad/server/routes_proposals.py` — the diff asset route reads inside a
  `try`/`except OSError` → 404.
- `agentcad/core/tools_proposals.py` — `proposal_merge` and `proposal_packet`
  descriptions restated (the hold, `stale_at_merge`, spec rows, what `ok:
  false` means now).
- `agentcad/core/tools_versioning.py` — `resolve_merge`'s description: a held
  merge answers `{held: true, outstanding: 0}` and lands nothing.
- `frontend/js/merge.js` — `updateProgress` (held footer + button),
  `handleResult` (a `held` reply).
- `frontend/js/proposals.js` — the "stale at merge" chip.
- `tests/test_proposals.py` — `test_a_merge_a_proposal_staged_is_not_landed_by_
  resolve_merge`, `test_a_held_merge_refuses_when_a_gate_turned_red_while_it_
  waited`, `test_a_held_merge_is_landed_by_proposal_merge_with_its_own_
  override`, `test_a_source_commit_between_the_gates_and_the_merge_is_refused`,
  `test_a_source_turn_held_by_someone_else_is_a_clean_conflict_error`,
  `test_an_id_is_never_reused_even_when_the_index_is_lost`; the existing
  reconciler test now stages a **pre-hold** merge (`_unhold`).
- `tests/test_packet.py` — `test_params_delta_reports_script_declaration_
  changes`, `test_params_delta_still_reports_overrides_beside_the_
  declaration`, `test_params_spec_reads_a_declaration_without_executing_the_
  script`, `test_a_script_only_params_change_reaches_the_structured_diff`,
  `test_a_source_commit_mid_build_forces_a_rebuild`, `test_a_source_that_keeps_
  moving_is_marked_stale_not_mislabelled`, `test_a_failed_git_read_is_a_named_
  error_not_empty_evidence`, `test_a_manifest_that_cannot_be_read_is_not_a_
  deleted_project`, `test_a_regeneration_owns_the_whole_diff_directory`,
  `test_a_packet_frozen_behind_the_commits_that_merged_says_so`,
  `test_a_packet_that_described_what_merged_is_not_stale_at_merge`.
- `tests/test_proposals_api.py` —
  `test_an_asset_unlinked_between_the_check_and_the_read_is_a_404`; the packet
  summary assertions gained `stale_at_merge`.
- `docs/agent-api.md` — the held-merge behavior on `resolve_merge`,
  `merge_status`'s `held`/`held_by`, `stale_at_merge`, spec rows in
  `params_diff`, what `ok: false` means.
- `docs/user-guide.md` — the conflict paragraph (both the merge modal's and the
  proposals one): resolving no longer lands a proposal's merge.
- `AGENTS.md` — four new gotchas (the hold, the source turn, the packet head
  re-read, failed git reads).
- `docs/superpowers/specs/2026-08-10-change-proposals-design.md` — an "As built
  — the second review fold-back" section, the two open questions decided, and
  divergences 16–20.

## Notes
- The two review "open questions" are now explicit decisions in the spec: a
  gate provider that errors degrades to `pending` (PRD-003/PRD-004 own their
  own blocking semantics when they land), and a stale approval still counts in
  v1 (re-requesting review is PRD-008's).
- PRD-001's merge suite is untouched and green — the hold is inert for any
  caller that does not pass `held_by`, and `_merge`'s early returns
  (`already_up_to_date`, fast-forward) are reached before it.
- The C3 tests force their interleavings with a monkeypatched `_renders` hook
  (commit once → rebuilt; commit every time → persisted `stale`), so both
  branches of the re-read are covered deterministically.
