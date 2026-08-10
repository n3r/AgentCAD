# PRD-002 — Change proposals with geometric diff (CAD pull requests)

- **Status:** implemented — MVP shipped in six slices (changelog entries
  `0077`–`0082`); every acceptance criterion has a named test in
  `tests/test_prd002_acceptance.py`. Design spec and implementation plan:
  `docs/superpowers/specs|plans/2026-08-10-change-proposals*.md`
- **Phase:** v4 — collaborative core
- **Created:** 2026-08-09
- **Origin:** competitive analysis (Aug 2026)
- **Depends on:** PRD-001 (hard — a proposal is a branch pair) · PRD-003
  (soft — spec gates)
- **Related:** PRD-004 (CI posts check status), PRD-008 (threads anchor to
  packet hunks), PRD-015 (revisions ride proposals), PRD-018 (generated
  parts arrive as proposals)

## Problem & motivation

A change lands the instant a tool call returns. There is no unit of change
bigger than one history snapshot, no way to package "here is what I did and
why," and no decision point between an agent's work and the mainline. v3's
turn locks serialize writes but offer no "propose, then decide" step — so
trusting an agent today means watching it live, and reviewing a colleague
means reading raw history.

This is the single largest open space the competitive analysis found.
Onshape merges per-tab with three fixed strategies and "the from workspace
wins" on conflict, because binary microversion deltas cannot be reviewed by
a human (market_research.md, "Cloud-native CAD: Onshape"). CoLab raised a
$72M Series C bolting pinned feedback and an AI reviewer onto files *outside*
the CAD — review today is screenshots in slides ("The workflow ring"). The
gap matrix scores change review "nobody in-CAD — build-differentiated (CAD
pull requests)," and "the model is reviewable" is our first structural
advantage ("Where AgentCAD wins"): parts are Python, the manifest is JSON —
diffs a human and a frontier model both read natively. The proposal is also
the trust mechanism the whole v4 thesis rests on: agents propose, humans
decide, the kernel referees.

## Users & jobs

- **Design engineer (human author):** package a branch as a reviewable
  change with an argument — title, description, evidence — instead of a
  "come look at my screen" call.
- **Reviewing engineer (human):** judge a change in minutes from one packet:
  what code changed, what the geometry did, what the numbers did — approve
  or push back with authority.
- **Design agent (author):** the mandated end state of every agent task —
  branch, work, propose, with a self-written summary and machine-gathered
  evidence (renders, metric deltas).
- **Reviewer agent:** consume the same packet as structured data plus
  images; leave a verdict a human can audit.
- **Release automation (PRD-015):** drive revision approvals over this
  state machine instead of a parallel workflow.

## Goals

- G1. Every change is reviewable before it lands: a proposal captures
  source/target branches, intent (title/description), and an auto-generated
  review packet.
- G2. The packet is complete and cheap: per-part script and PARAMS diffs,
  metric deltas, assembly deltas, matched before/after renders, and a 3D
  geometric diff — under 10 s warm for the rocketry example.
- G3. The lifecycle is governed: draft → open → approved/changes-requested
  → merged/closed, with per-action attribution distinguishing human from
  agent actors, queryable forever.
- G4. Merge happens only through the gate: PRD-001's kernel validation,
  plus spec (PRD-003) and CI (PRD-004) statuses as they land; a red gate
  blocks, and an override is recorded, never silent.
- G5. One packet, two consumers: pixels and prose for humans in the UI;
  structured JSON plus image content for agents over MCP and chat.

## Non-goals

- Branch/merge mechanics — PRD-001 (this PRD consumes `merge_branch` and
  adds nothing to conflict semantics).
- Threaded discussion — PRD-008 (this PRD defines the hunk anchors it
  targets).
- Check execution — PRD-004 (this PRD displays posted statuses).
- An auto-review product — a reviewer agent is just another client holding
  these tools, not a feature we build.
- Cross-project / registry proposals — PRD-011.

## Experience

**Human path.** The toolbar gains a Proposals entry with an open-count
badge: a list (state chips, author with a human/agent badge, age, gate
summary) and a detail view with five tabs (Audit joined the four below — FR14's
append-only log has no other surface). *Overview* — description, a
metric-delta table (volume/mass/CoM/bbox per changed part, old → new, Δ and
%), and before/after render pairs. *Files* — script diffs in a CodeMirror
merge view plus a PARAMS diff table. *Geometry* — the viewport overlaying
the target build with translucent green (added) and red (removed) diff
solids per changed part. *Checks* — merge validation, spec, and CI statuses
with drill-in. Actions: Approve, Request changes, Merge (disabled while a
gate is red, with an explicit recorded override), Close.

**Agent path.** `branch_create` → edits → `proposal_create {project,
source, title, description}` returns the proposal with packet status.
`proposal_packet` returns the full structured packet — diffs as text,
deltas as numbers, renders as real image content (the `render_view`
mechanics). A reviewer agent calls `proposal_review {verdict, summary}`.
Merge is `proposal_merge` — PRD-001's `merge_branch` plus gates; a
`merge_conflict` surfaces exactly as PRD-001 defines, is resolved with the
same tools, then `proposal_merge` runs again.

**Handoff.** New commits on the source branch mark the packet stale; it
regenerates on next view. Review feedback arrives as PRD-008 threads
anchored to packet hunks; the agent addresses each with evidence and
re-requests review. The proposal is the one page where a human supervises
an agent without reading a transcript.

## Functional requirements

**Object & lifecycle**
- FR1. A proposal is `{id, project, source, target, title, description,
  author, state, created, updated}` with states `draft → open → approved |
  changes_requested → merged | closed` (reopen: closed → open); invalid
  transitions are a `validation_error`.
- FR2. One open proposal per (source, target) pair — a duplicate
  `proposal_create` is a `conflict_error` naming the existing id. Deleting
  a branch with an open proposal is a `conflict_error`; merged/closed
  proposals keep an archived packet after branch deletion.
- FR3. Proposals are workflow metadata, not model state: stored under the
  project but excluded from history snapshots — `project_restore` never
  rewinds proposal state.

**Review packet**
- FR4. Per changed part: unified script diff; PARAMS diff (params
  added/removed, and per-param default/min/max/type/choices changes as
  old → new); metric deltas for volume, mass, CoM, and bbox as `{old, new,
  delta, pct}`; build status on both sides.
- FR5. Assembly deltas: instances added/removed, moved instances with
  old/new transforms, mate changes, total-mass delta.
- FR6. Matched renders: for each changed part (and the assembly when it
  changed), before/after PNGs from the same view with identical camera
  framing computed from the union of both bboxes — visually superimposable
  pairs.
- FR7. Geometric diff per changed part: `added_mm3` (new − old) and
  `removed_mm3` (old − new) computed by kernel booleans, plus tessellated
  diff solids for the viewport overlay. Identical content hashes
  short-circuit to "unchanged" with zero kernel work; mesh-only reference
  parts report `skipped_mesh` (the `check_interference` rule).
- FR8. The packet degrades honestly: an unbuildable side yields the
  structured build error in place of renders/metrics/geometric diff for
  that part — never a generation crash.
- FR9. Packets are cached keyed by (source head, target head); a branch
  update marks the packet stale; regeneration is on demand
  (`regenerate: true`) or on view.

**Gating & merge**
- FR10. `proposal_merge` = the PRD-001 merge plus gates: merge validation
  (PRD-001 FR9) always; spec evaluation via PRD-003's `evaluate_specs`
  seam when the project declares specs; CI status via PRD-004 when posted.
  Any red gate blocks with the failing gate named; `allow_invalid: true`
  merges anyway and records the override in the audit log and the merge
  commit message.
- FR11. Merge policy v1 (per-project settings): `approvals_required`
  (default 1) and `self_approve` (default false — the author's own approval
  does not count). Policy is enforced in the service, not the UI.
- FR12. Merging sets state `merged` with the merge commit id; the packet is
  frozen and archived at merge.

**Attribution & audit**
- FR13. Every action (create/update/review/merge/close/override) is stamped
  `{actor, actor_kind: human | agent, ts, action, details}` — actor is the
  client identity from the existing `client_id_var` plumbing (`browser`,
  `chat:<session>`, MCP agent ids), upgraded to authenticated principals by
  PRD-005 without a schema change; `actor_kind` derives from the identity
  class.
- FR14. The audit trail is append-only, returned by `proposal_get`, and not
  editable by any tool.
- FR15. `proposal_changed {project, id, state}` publishes on the WebSocket
  channel for every state/packet transition; the UI updates live.

## Agent surface

New tools (as built, eight — `proposal_render` was added; see "As built"):
`proposal_create {project, source, target?, title, description?,
draft?}` · `proposal_list {project, state?}` · `proposal_get {project, id}`
(object + gates + audit) · `proposal_update {project, id, title?,
description?, state?}` (draft↔open, close/reopen) · `proposal_packet
{project, id, regenerate?}` (structured packet; renders arrive as image
content over MCP/chat) · `proposal_review {project, id, verdict, summary?}`
· `proposal_merge {project, id, allow_invalid?}`.
New event: `proposal_changed {project, id, state}`.
Errors: `conflict_error` (duplicate proposal, policy violation, red gate),
`merge_conflict` (pass-through from PRD-001), `validation_error` (bad
transition). House contract throughout: structured errors, post-state
returns.

## Technical approach

- **Core module** `agentcad/core/proposals.py`: proposal store (JSON docs
  plus an append-only audit JSONL under
  `<project>/.history/agentcad/proposals/` — inside GIT_DIR, so FR3 holds
  structurally and no `info/exclude` change is needed — atomic writes), the
  state machine, policy checks, and gates. Packet orchestration is its own
  module, `agentcad/core/packet.py`.
- **Packet generation** in the service over PRD-001's ref layer: read both
  sides from the **branch worktrees PRD-001 already maintains**
  (`branches.tree_of` + `branches.pinned`; temp worktrees would only be needed
  for a packet pinned to a non-head commit, which is Phase 2), rebuild changed
  parts through the normal
  kernel-pool path — unchanged parts are content-hash cache hits, so packet
  cost scales with the change, not the project. Script diffs via `git diff`
  between refs; PARAMS and assembly deltas from the two manifests.
- **Geometric diff kind** in the analysis handler pack
  (`agentcad/kernel/handlers/analysis.py` grows a `geom_diff` handler, or a
  sibling `handlers/diff.py`): two built shapes in; `new - old` and
  `old - new` via operator booleans; volumes via the solids-sum rule
  (`worker._shape_volume` — the nested-Compound trap); diff solids
  tessellated to ACM for the overlay. Wrapped in `safe_bool`-style guards —
  on boolean failure the packet reports "geometric diff unavailable" with
  metrics still present.
- **Matched renders**: `agentcad/core/render.py` gains an explicit framing
  bbox (today it auto-frames per mesh); the packet renders both sides with
  the union bbox so poses match. No kernel involvement — renders come from
  cached ACM meshes.
- **Tool pack** `agentcad/core/tools_proposals.py` + **route pack**
  `agentcad/server/routes_proposals.py` (list/detail/actions + packet
  download); cores untouched per the extension-point contract.
- **Frontend**: proposals list + detail (`frontend/js/proposals.js`),
  script diffs as **plain DOM** line nodes (no CodeMirror merge addon is
  vendored, and a line node is exactly the anchor PRD-008 needs), geometry
  overlay in `viewport.js` (diff meshes as translucent color-coded groups over
  the target build).
- No manifest schema change; one new kernel handler kind; identities stay
  opaque strings so PRD-005 slots in principals cleanly.

## MVP & phasing

- **MVP:** proposal object + lifecycle + audit (FR1–FR3, FR13–FR15); packet
  with script/PARAMS diffs, metric deltas, assembly deltas, matched
  renders, and the geometric diff volumes + overlay (FR4–FR8); merge with
  the PRD-001 validation gate and v1 policy (FR10–FR12); list/detail UI
  with all four tabs; the `proposal_*` tools.
- **Phase 2:** packet caching/staleness (FR9), the spec gate (with
  PRD-003), CI status display (with PRD-004), archived packets surviving
  branch GC, richer policy (branch protection).
- **Phase 3:** PRD-008 threads anchored to packet hunks; PRD-015 revision
  workflows riding the state machine; reviewer-agent playbooks (docs, not
  code).

## Acceptance criteria

- AC1. The roadmap round trip: an agent creates a branch, edits the
  rocketry example, and opens a proposal via tools; a human reviews and
  merges it in the browser with zero terminal use; `proposal_get` shows
  every action attributed with the correct `actor_kind` (scripted test +
  browser session).
- AC2. The packet for a nozzle wall-thickness change generates warm in
  < 10 s and contains the script diff, PARAMS diff, metric deltas,
  before/after renders with identical camera framing, and geometric diff
  volumes (timed test on a rocketry-example copy).
- AC3. Drilling a new hole reports `removed_mm3` equal to the hole volume
  within 1%, and the red overlay renders in the Geometry tab (test +
  browser check).
- AC4. A move-an-instance-only change produces assembly deltas and zero
  per-part kernel diff work — the content-hash short-circuit is asserted
  (test).
- AC5. A proposal whose merge validation fails (introduced interference)
  blocks `proposal_merge`; `allow_invalid: true` merges with the override
  recorded in the audit log and merge commit message (test).
- AC6. Under default policy, self-approval does not satisfy
  `approvals_required`, and merging without approval is a `conflict_error`
  naming the policy (test).
- AC7. An unbuildable source side yields a packet embedding the structured
  script error for that part with the rest of the packet intact (test).
- AC8. A second browser sees `proposal_changed` transitions live (WS test
  with `extra_allowed_hosts={"testserver"}`).
- AC9. `project_restore` to a pre-proposal snapshot leaves the proposal
  untouched (test); full suite green.

### Verification (slice 6)

Every criterion above has a named test in `tests/test_prd002_acceptance.py`,
which walks it end to end through the real stack — tools, HTTP routes, git and
the kernel — rather than through the unit seams:

| AC | Proving test |
|----|---|
| AC1 | `test_ac1_roundtrip_agent_proposes_human_merges` (rocketry, on a copy: the agent half through tools, the human half through the routes; audit `actor_kind`s asserted) + `test_ac1_browser_half_evidence_is_recorded` |
| AC2 | `test_ac2_packet_generates_warm_under_10s` (timed on a rocketry copy; measured **0.97 s** warm in slice 4) |
| AC3 | `test_ac3_drilled_hole_reports_removed_volume` (within 1 %, parseable ACM1 solid, served by the asset route) + `test_ac3_browser_overlay_evidence_is_recorded` |
| AC4 | `test_ac4_instance_move_does_no_per_part_kernel_work` |
| AC5 | `test_ac5_failed_validation_blocks_then_overrides` |
| AC6 | `test_ac6_self_approval_does_not_satisfy_policy` |
| AC7 | `test_ac7_unbuildable_side_degrades_honestly` |
| AC8 | `test_ac8_second_client_sees_proposal_changed_live` |
| AC9 | `test_ac9_project_restore_does_not_rewind_proposals` + the full-suite run cited in `docs/changelog/0082-proposals-docs-and-acceptance.md` |

The **browser halves of AC1 and AC3** ("merges it in the browser with zero
terminal use", "the red overlay renders in the Geometry tab") were driven for
real in a headless-Chrome session in slice 5 — screenshots and a clean console
are recorded in `docs/changelog/0081-proposals-ui.md`, and the two evidence
tests above fail if that record is removed. Re-driving a browser from the test
suite is deliberately not done (the PRD-001 AC6 precedent).

### As built — divergences from this document

1. Proposal state lives at `<project>/.history/agentcad/proposals/`, not
   `<project>/.proposals/`: inside GIT_DIR, so FR3 holds structurally.
2. The packet reads the **branch worktrees PRD-001 already maintains**, not
   temp worktrees (needed only for a non-head pin — Phase 2).
3. **Eight tools, not seven:** `proposal_render` is additive, because MCP and
   chat lift exactly one `png_base64` per result, so a multi-render packet
   cannot carry its own images — `proposal_packet` returns render URLs.
4. Head-pinning and on-view regeneration (the cheap half of FR9) are in MVP;
   LRU/GC and surviving branch deletion stay Phase 2.
5. Script diffs render as plain DOM line nodes, not a CodeMirror merge view.
6. `proposal_changed` carries a `reason` alongside `{project, id, state}`.
7. The geometric diff is a new handler pack, `kernel/handlers/diff.py`.
8. `allow_invalid` overrides the kernel validation gate only, never the
   approvals policy.
9. **Five tabs, not four** — Audit is the fifth (FR14's log had no surface).
10. `params_diff` rows are `{name, field, old, new}` with `field: "value"` for
    an ordinary override (the manifest stores overrides, not specs); a
    dict-valued parameter yields one row per changed spec field. `changed_by`
    has a third value, `"manifest"`.
11. `assembly.renders` is `null` by design — assembly renders are drawn on
    demand by `proposal_render`.
12. All timestamps are zone-aware UTC (`…Z`).

## Risks & open questions

- **Boolean diff robustness and cost** — OCCT booleans fail readily on
  complex parts; guards, the honest "diff unavailable" fallback, and the
  per-request kernel timeout keep the packet alive. Measure on the
  surfacing example; a mesh-space diff is the cheaper fallback if needed.
- **Packet storage growth** (renders + diff meshes per head pair) — LRU/GC
  the cache; archive only merged-state packets.
- **Identity is a header until PRD-005** — attribution is honest
  bookkeeping, not authentication; documented, and fixed by 005 with no
  schema change.
- **Policy scope creep** — v1 is two fields; org rules, code owners, and
  branch protection wait for PRD-005's roles.
- **Stale-packet races** (review vs. new commits) — the packet pins head
  SHAs and displays staleness prominently; merge re-checks gates against
  current heads.
- Open: do draft proposals generate packets eagerly or lazily? MVP:
  lazily, on first view.

## Competitive references

Onshape has branches but not review: per-tab merge, three fixed strategies,
"the from workspace wins," no cherry-pick — binary deltas can't be reviewed
(market_research.md, "Cloud-native CAD: Onshape"). CoLab: $72M for pinned
feedback + AutoReview, bolted on outside the CAD ("The workflow ring").
Zoo hides its DSL from engineers — the opposite of review-centric
("AI-native CAD"). GitHub supplies the interaction model we borrow — PRs,
review states, merge gates — applied to a model that is code. We differ:
the review unit lives inside the CAD, the packet carries kernel-computed
geometric evidence, gates are kernel-refereed, and either side of the
review can be an agent.
