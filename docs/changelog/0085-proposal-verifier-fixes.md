# 0085 — evidence that cannot be swapped, and a hold that dies with its proposal

- **Commit:** pending
- **Date:** 2026-08-10
- **Author:** Nikita Fedorov

## Summary
A verification review of PRD-002 demonstrated three remaining defects: a
*discarded* packet build silently replaced the **frozen** packet's diff meshes
(the packet.json was thrown away, the geometry it pointed at was not); a hold
that outlived its closed proposal let the next proposal for the same branch pair
inherit that proposal's conflict resolutions, after a self-contradictory error
message that rewrote `held_by` on its way out; and the conflict modal told a
reviewer to complete a merge whose Complete button it had just disabled. Each
fix has a regression test written first.

## Changes

### D1 — diff assets are namespaced by the build that wrote them
- `PacketBuilder._measure` mints a **generation** per measuring pass
  (`uuid4().hex[:16]`) and publishes it as `packet["generation"]`. Diff meshes
  go to `diff/<generation>/<part>.<kind>.acm` and the URLs carry the segment.
  `_clear_diff_assets` (which wiped the shared directory on the way *in*, before
  the build was known to be publishable) is gone.
- `PacketBuilder.build` now publishes the packet **together with** its
  generation's assets: a build whose `record_packet` returns False (the merge
  overtook it) deletes the generation directories it made
  (`_drop_generations`), and a build that persists deletes every *other*
  generation under the slot (`_publish_generation`, which also collects the flat
  `<part>.<kind>.acm` files a store written before this change still has). Only
  a packet that persists owns a directory.
- **Contract change:** `GET /api/projects/{proj}/proposals/{pid}/diff/{gen}/{part}/{kind}.acm`
  — one new path segment. `PacketBuilder.diff_mesh_path(proj, pid, generation,
  part, kind)` gains the same argument and whitelists it against
  `^[0-9a-f]{16}$`; a generation that is not on disk (or not a generation at
  all) is a `NotFoundError` → 404, the same answer a collected asset gets. The
  UI reads its mesh URLs off the packet, so no frontend change was needed.

### D2 — a hold dies with its proposal, and never transfers
- `ProposalManager._release_staged_merge` (new): moving a proposal to `closed`
  aborts a staged merge that proposal **holds**, through PRD-001's public
  `merges.abort`, drops `staged_merge`, and appends the audit entry the
  reconciler already uses — `merge_discarded`, with `details.reason: "closed"`.
  Called from `update()` *before* the transition, so a refused abort cannot
  leave a closed proposal with a live hold.
- `ProposalManager._orchestrate` refuses a branch pair whose staged merge is
  held by a **different** holder before `MergeOrchestrator` is asked anything —
  which is what stops the mutation: passing our own `held_by` into `merge()`
  rewrote the staged state's holder to *us* on the way to being refused, so the
  second call resumed that merge and landed another proposal's resolutions.
- The message is now coherent and actionable: *"a staged merge of 'feat' into
  'master' is held by proposal:1; close or merge that proposal first, then merge
  proposal 2"* (was: *"belongs to proposal:2, not to proposal 2"*). It comes
  from one helper, `_held_elsewhere`, used by both the pre-check and the
  defensive `result["held"]` branch in `merge()`.
- `agentcad/core/merge.py` is **untouched**.

### D3 — the held conflict modal stops contradicting itself
- `frontend/js/merge.js`: with every conflict resolved on a **held** merge, the
  left pane says the merge belongs to `proposal:N` and completes there, instead
  of "Complete the merge to run the validation pass and land it" beside the
  disabled button. One string branch; `node --check` clean.

## Files
- `agentcad/core/packet.py` — generation id, per-generation diff paths/URLs,
  `_drop_generations`/`_publish_generation`, `diff_mesh_path` signature,
  `_part_section`/`_geom_diff` thread the generation, module docstring.
- `agentcad/core/proposals.py` — `_release_staged_merge`, the cross-holder
  pre-check in `_orchestrate`, `_held_elsewhere`, close path in `update()`.
- `agentcad/server/routes_proposals.py` — the `{gen}` segment on the diff asset
  route (and its docstring).
- `frontend/js/merge.js` — the resolved-and-held note.
- `tests/test_packet.py` — `test_a_discarded_build_leaves_the_frozen_packets_meshes_alone`,
  `test_a_regeneration_garbage_collects_the_previous_generation`, `CUBE_WIDER_HOLE`,
  and the three existing cases that named the flat asset paths.
- `tests/test_proposals.py` — `test_closing_a_proposal_aborts_the_merge_it_held`,
  `test_the_next_proposal_for_the_pair_starts_a_clean_merge`,
  `test_a_merge_held_by_another_proposal_is_refused_coherently`, plus the
  `_rehold`/`_held_by`/`_stage_held_conflict` helpers.
- `tests/test_proposals_api.py` — the asset-route test now asserts the
  generation-scoped URL, a well-formed generation that is not on disk (404) and
  three segments that are not generations at all (404, from the whitelist).
- `tests/test_prd002_acceptance.py` — AC3's asset path/URL.
- `docs/agent-api.md`, `docs/architecture.md`,
  `docs/superpowers/specs/2026-08-10-change-proposals-design.md` — the URL
  shape, the on-disk layout, and a third as-built fold-back section (+
  divergences 21 and 22).

## Notes
- **Renders are not namespaced.** `renders/<part>.<side>.<view>.png` has the
  same shape of exposure as the old diff paths (a discarded build's PNG can
  outlive it), but the render URL is a *tool* contract
  (`proposal_render`/`…/render/{side}/{part}`) that MCP and chat call by hand,
  so widening it is a larger change than this defect list. Left as a known
  follow-up rather than folded in silently.
- The generation is minted per `_measure`, not per `build`: a build that
  re-measures because a head moved makes two, and discards the first with the
  same code path that discards a losing build.
- `_publish_generation` deleting *everything* else under the slot is safe
  because it only runs after `record_packet` returned True, which cannot happen
  once the proposal is terminal — so it can never collect the assets of a frozen
  packet.
