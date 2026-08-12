# PRD-008 — Anchored review threads and presence

- **Status:** implemented — AC1–AC9 verified (branch
  `prd-008-review-threads`; see Verification below)
- **Phase:** v4 — collaborative core
- **Created:** 2026-08-09
- **Origin:** competitive analysis (Aug 2026)
- **Depends on:** PRD-005 (soft — real principals and cross-user
  notifications at team scale; the single-human + agents loop works
  locally without it) · PRD-002 (soft — proposal-hunk anchors)
- **Related:** PRD-001 (branch context), PRD-026 (shell surfaces the
  panels)

## Problem & motivation

Feedback has no place to land on the model. "Make this wall thicker"
lives in chat prose that scrolls away, points at nothing, and can't be
marked done; two clients coordinate through a coarse per-project turn
lock that makes a human and an agent — or two humans — take turns on the
*whole project* even when they touch different parts. There is no way to
see who else is in a project or what they're looking at.

Review-on-the-model is table stakes: Onshape ships feature-anchored
comments, follow mode, and per-user undo on its microversion substrate
(market_research.md, "Cloud-native CAD: Onshape"); CoLab built a $72M
business on pinned CAD feedback and a 47k-engineer waitlist for its AI
reviewer — while review elsewhere remains screenshots in slides ("The
workflow ring"). The gap matrix scores anchored comments **build** and
co-presence "per-part concurrency + presence first; same-file CRDT
later." The agent-native angle is the differentiator: a comment on a
face is not prose — it is a structured work item an agent can list,
address with kernel-computed evidence, and resolve. This is where human
intent enters the loop.

## Users & jobs

- **Reviewing engineer (human):** point at the thing — a face, a param,
  a script line — say what's wrong, and see it addressed with evidence.
- **Design engineer (human):** know who is in the project and what
  they're touching; stop overwriting and being overwritten.
- **Design agent:** read open threads as a work queue; reply with
  renders and metric deltas; resolve — review feedback becomes
  structured tasks, not lost chat.
- **Proposal reviewer (PRD-002):** comment on the exact diff hunk the
  concern is about.

## Goals

- G1. Comments anchor to model entities — part, face, param, script line
  range, assembly instance, proposal diff hunk — and survive edits or
  fail honestly (orphaned, never silently lost).
- G2. Threads have a lifecycle: replies, resolve/reopen, mentions,
  notifications.
- G3. Agents are first-class participants: threads are tool-visible,
  replies carry evidence, resolution is a tool call.
- G4. Presence is live: who is here, looking at what.
- G5. Humans get per-part soft claims instead of the coarse project
  turn; agents keep explicit turns; per-user undo works in shared
  sessions.

## Non-goals

- Same-file CRDT co-editing — a deliberate later step
  (market_research.md, "What we deliberately will not build"); per-part
  concurrency plus review delivers the value first.
- Review verdicts and merge gating — PRD-002 (threads inform, verdicts
  decide).
- Email/push delivery — PRD-005's channels; this PRD ends at in-app +
  WebSocket.
- Replacing chat — the agent dock stays; threads are anchored,
  persistent, resolvable — a different animal.

## Experience

**Human path.** A comment affordance on a face in the viewport, a param
row in the inspector, a line gutter in the editor, an instance in the
tree. Commenting drops a pin (a billboard at the anchor's world point;
a gutter icon for line ranges; a badge on the param row). A Threads
panel in the inspector lists open/resolved threads with anchor
breadcrumbs; clicking one focuses the anchor — the camera flies to the
face, the editor scrolls to the range. `@chat:main` or `@nikita` in a
body raises a notification in the drawer. Presence: avatars in the
toolbar, a colored outline plus "Nikita is editing" chip on claimed
parts. Editing a part someone else has claimed produces a non-blocking
warning naming them, with an explicit Override.

**Agent path.** `list_comments {project, state: "open"}` returns the
work queue — each thread with its full anchor payload (face anchors
carry the face index plus geometric signature; script anchors the line
range plus snippet). The agent fixes the script, replies with evidence —
`add_comment {thread, body, attachments: [<render path>]}` after a
`render_view` — and calls `resolve_thread`. Mentioning an agent identity
notifies its session: "@chat:main please look at this" makes the thread
that agent's next task.

**Handoff.** The roadmap loop: a human pins "this boss needs a fillet"
on a face; the agent lists it, edits, replies with a before/after
render, resolves; the human watches it happen live and reopens if
unsatisfied.

## Functional requirements

**Anchors**
- FR1. Anchor kinds: `part {part_id}` · `face {part_id, face_index}` ·
  `param {part_id, param}` · `script_range {part_id, start, end}` ·
  `instance {instance_id}` · `proposal_hunk {proposal, file, hunk}`
  (lands with PRD-002). Anchors validate on create — face_index within
  the part's face count (the `<key>.faces.u32` sidecar is the
  authority), param in `params_spec`, instance in the assembly — else
  `validation_error`.
- FR2. Face anchors store a geometric signature at creation (`{center,
  normal, area_mm2}` — the `face_info` payload) beside the ordinal;
  after a rebuild the anchor re-matches by signature within tolerance,
  since ordinals can renumber. Script-range anchors store a context
  snippet hash and re-map across edits (GitHub-style line tracking).
- FR3. An anchor whose target disappears — face cut away, param
  removed, instance or part deleted — becomes `orphaned`: the thread
  stays readable and listable, flagged, with its last-known anchor
  payload. Never silently dropped.

**Threads**
- FR4. A thread is a root comment plus ordered replies with state
  `open | resolved` (resolve/reopen recorded with actor + ts). A comment
  is `{id, author, ts, body, attachments?}` — body is a markdown subset
  (text, code, links); authors may edit/delete their own comments with a
  tombstone kept in the audit trail.
- FR5. Threads are workflow metadata: stored per project outside history
  snapshots (the PRD-002 pattern) — `project_restore` never deletes
  discussion; PRD-005's push/pull syncs them.
- FR6. Mentions: `@<identity>` in a body creates a notification
  `{to, thread, comment, ts, read}` — listable per identity and pushed
  as a `notification` WebSocket event. Identities are today's client ids
  (`browser`, `chat:<session>`, MCP agent ids); PRD-005 upgrades them to
  principals transparently.

**Agent tools**
- FR7. `list_comments {project, part_id?, state?, kind?}` returns
  threads with full anchor payloads and resolve state. `add_comment
  {project, anchor?, thread?, body, attachments?}` opens a thread
  (anchor given) or replies (thread given) — exactly one of the two.
  `resolve_thread` / `reopen_thread {project, thread}`. All mutating
  calls return the post-state thread.
- FR8. Attachments must resolve inside the project's `exports/` tree
  (`render_view` and export outputs qualify); anything else is a
  `validation_error` — no path disclosure through comments.

**Presence**
- FR9. Connected clients may report focus over the existing WebSocket
  channel (`{project, part_id?, surface: viewport|editor|inspector}`);
  the server keeps an in-memory registry, expires entries on disconnect
  or staleness, and fans out `presence_changed {project, clients:
  [{id, kind, focus}]}`. Presence is ephemeral — never persisted.
- FR10. The UI renders presence: toolbar avatars, per-part indicators in
  the tree, and an "editing" chip naming the claim holder.

**Claims & per-user undo**
- FR11. Per-part soft claims for humans: a browser client editing a part
  (editor focus, param drag, push/pull) acquires a claim `{project,
  part, holder, ttl}` (auto-refreshed). A write to a claimed part by a
  *different human* returns `conflict_error` with `{claim: {holder,
  expires_at}, overridable: true}`; retrying with `override: true`
  proceeds, and the `claim_changed` event names the override. Claims are
  enforced at the single `ProjectStore.write_guard` seam — the same
  choke point turn locks use.
- FR12. Agent turns are unchanged and senior: a held project turn
  (`acquire_turn`) hard-blocks other clients' writes exactly as today —
  the existing turn-lock test suite must pass unmodified. Claims never
  apply to a client holding the turn.
- FR13. History snapshots gain authorship: each snapshot commit records
  the mutating client identity (from `client_id_var`), so history
  answers "who did this."
- FR14. Per-user undo: undo reverts the calling client's most recent
  commit. When it is the branch head — restore, as today. When others
  committed after it — apply the inverse patch of that commit (git
  revert). When the revert overlaps later changes — return a structured
  `conflict_error` naming the overlap; never a partial or silent
  clobber.

## Agent surface

New tools: `list_comments {project, part_id?, state?, kind?}` ·
`add_comment {project, anchor?, thread?, body, attachments?}` ·
`resolve_thread {project, thread}` · `reopen_thread {project, thread}` ·
`list_notifications {project?}`.
New events: `comment_changed {project, thread, state}` ·
`presence_changed {project, clients}` · `claim_changed {project, part,
holder}` · `notification {to, thread}`.
Changed: writes to a human-claimed part may return `conflict_error` with
`overridable: true` details (new details shape, existing error type).
Deliberately absent: claim and presence tools for agents — agents
coordinate through turns and branches (PRD-001), not claims.

## Technical approach

- **Core** `agentcad/core/threads.py` (thread store — JSON docs plus
  audit under `<project>/.threads/`, atomic writes, excluded from
  snapshots via the history excludes; anchor validation and
  re-anchoring) and `agentcad/core/presence.py` (in-memory registry
  keyed by WebSocket connection).
- **Claims** extend `agentcad/core/locks.py`: `TurnLock` generalizes to
  scoped locks over (project, part?) with one precedence rule — project
  turn beats part claim. Enforcement stays at `ProjectStore.write_guard`
  (wired in `service.py`), which gains the part dimension
  (`write_guard(proj, part?)`) while project-only calls keep guarding
  manifest-wide writes. One seam, both mechanisms.
- **Face anchors** ride existing plumbing: the viewport already picks
  faces via the triangle→face `<key>.faces.u32` sidecar
  (`kernel/worker.py` writes it, `server/app.py` serves `mesh/faces`);
  signatures come from the `face_info` handler payload. Re-matching is
  nearest-signature within tolerance, else orphan.
- **WebSocket**: the `/ws` channel (server→client today) accepts
  client→server presence messages — schema-validated, rate-limited, and
  behind the existing Host-allowlist/origin guard.
- **Undo**: `core/history.py` grows commit authorship (author = client
  id on `snapshot`) and `revert(project, commit)` via `git revert`
  plumbing with conflict detection; the undo path in `tools_history.py`
  becomes author-aware.
- **Tool pack** `agentcad/core/tools_threads.py` + **route pack**
  `agentcad/server/routes_threads.py`. **Frontend**: pins + comment box
  in `viewport.js`, Threads panel + param badges in `inspector.js`,
  gutter markers in `editor.js`, avatars in `main.js`/`tree.js`, a
  notifications drawer.

## MVP & phasing

- **MVP:** anchors (part/face/param/script_range/instance) + threads +
  resolve + the agent tools + `comment_changed` events + Threads panel,
  face pins, and editor gutter; presence registry + avatars + focus
  chips.
- **Phase 2:** mentions + notifications drawer; per-part claims at the
  write_guard with the override UX; signature re-anchoring hardening;
  snapshot authorship.
- **Phase 3:** per-user undo (revert path); proposal-hunk anchors (with
  PRD-002); notification delivery channels (with PRD-005).

## Acceptance criteria

- AC1. The roadmap loop end to end: a human comments on a face in
  browser A ("this boss needs a fillet"); an agent `list_comments` →
  sees the face anchor, edits the script, replies with a before/after
  render attached, `resolve_thread`; browser B sees the pin, reply, and
  resolution live with zero console errors (scripted agent test plus a
  two-browser session).
- AC2. A face anchor survives a rebuild that keeps the face (param
  tweak) via signature re-match, and flags `orphaned` when the face is
  cut away — thread still listable (tests).
- AC3. A script_range anchor tracks its lines across an edit inserted
  above it (test).
- AC4. `@chat:main` in a comment delivers a `notification` event to that
  session and a listable unread record (WS test with
  `extra_allowed_hosts={"testserver"}`).
- AC5. Claims: browser A edits part X; browser B's write to X returns
  `conflict_error` naming A with `overridable: true`; B's retry with
  `override: true` lands and `claim_changed` fires; B's write to part Y
  proceeds untouched (tests at the write_guard seam).
- AC6. Turn precedence: with an agent holding the project turn, writes
  by both browsers fail exactly per the existing turn-lock tests — the
  current lock suite passes unmodified (regression gate).
- AC7. Per-user undo: A edits part X, B edits part Y, A undoes — only X
  reverts and B's edit stands; after B also edits X, A's undo of the X
  commit returns the structured conflict (tests).
- AC8. Threads survive `project_restore` to an earlier snapshot (test).
- AC9. Attachments outside `exports/` are rejected as
  `validation_error` (test); full suite green, count cited.

### Verification (slice 11)

Every criterion above has a named test in `tests/test_prd008_acceptance.py`,
which walks it through the surfaces a human and an agent actually touch — the
five tools, the REST routes, the WebSocket, real git history and a real kernel
build — rather than through the unit seams (`tests/test_comments.py`,
`test_anchors.py`, `test_anchors_kernel.py`, `test_comments_api.py`,
`test_comments_proposals.py`, `test_comments_notifications.py`,
`test_presence.py`, `test_claims.py`, `test_undo_authors.py`).

| AC | Proving test |
|----|---|
| AC1 | `test_ac1_the_review_loop_end_to_end` — a browser identity opens a face thread; an agent identity lists it, sees the server-stamped signature, edits the script, `render_view`s, replies with the render attached and resolves; a WS client observes the resolution live and the bus carries `created`/`replied`/`resolved` in order. The **two-browser half** was driven for real in slices 8–9 (headless Chrome, two `localStorage` identities, screenshots, zero page errors) and is asserted as a record by `test_ac1_browser_half_evidence_is_recorded` — the PRD-001 AC6 / PRD-002 AC1 precedent |
| AC2 | `test_ac2_a_face_anchor_survives_or_says_it_did_not` — at the narrowed wording below: a bounds-stable tweak keeps the anchor on the *same face* (verified geometrically, not by trusting the resolver), cutting the face away answers `orphaned` with a reason, a hint and **no** face index, and the thread stays listable, filterable and resolvable either way |
| AC3 | `test_ac3_a_script_range_anchor_tracks_an_insert_above_it` — two lines inserted above the range: `moved`, `reason: snippet_found_verbatim`, the new address in the payload, the stored anchor untouched |
| AC4 | `test_ac4_a_mention_delivers_an_event_and_an_unread_record` — `@chat:main` publishes `notification` on `/ws` and leaves exactly one unread record for that identity, cleared by the read route; `@nobody` stays plain text; nobody else's inbox is involved |
| AC5 | `test_ac5_a_claim_conflicts_overrides_and_leaves_other_parts_alone` — the 409 with `claim.holder` and `overridable: true`, the armed override landing the retry, part Y untouched throughout, and an agent never claim-blocked |
| AC6 | `test_ac6_the_turn_lock_still_decides_first` — with an agent holding the turn both browsers fail with the pre-existing message and **no** claim details, and the turn holder is never claim-checked; `test_ac6_the_lock_suite_is_unmodified_evidence` is the record that `tests/test_locks.py` passed unmodified (a claim about a diff, not about a run) |
| AC7 | `test_ac7_per_user_undo_and_its_structured_conflict` — A edits X, B edits Y, A's `undo {scope: "mine"}` reverts only X; then B edits X on top of A and A's undo is a `conflict_error` with `{commit, reason: "overlapping_changes", paths, blocked_by}`, nothing landed, the entry still A's |
| AC8 | `test_ac8_threads_survive_project_restore` — thread and audit byte-identical across a restore to the first snapshot |
| AC9 | `test_ac9_an_attachment_outside_exports_is_refused` — traversal, absolute, symlink-out, wrong-tree and missing paths all `validation_error` at the tool and 422 at the route, with a real export still accepted; `test_ac9_the_full_suite_count_is_cited` is the evidence check over the close-out changelog where `make test`'s count is recorded |

### As built — divergences from this document

1. **AC2's wording is narrowed to what the system provably does**, and this is
   the divergence that matters most. The criterion above says a face anchor
   "survives a rebuild that keeps the face (param tweak) via signature
   re-match". The slice-2 spike measured it — 11 bundled parts × up to 3
   parameters × +1%/+10%/+30%, 3 206 face pairs with ground truth established
   independently of the matcher (`docs/changelog/0113-prd008-anchor-resolution.md`):

   Those numbers were **re-measured after code review**
   (`docs/changelog/0123-prd008-review-fixes.md`) with a stricter ground-truth
   oracle — a Lowe ratio test on each mutual-nearest-neighbour hop, so a face
   the oracle cannot pair unambiguously leaves the sample instead of being
   guessed at — and the second run is the one to quote:

   | claim | measured (re-run, 2 693 known-truth faces) |
   |---|---|
   | face ordinals are stable | **no** — 87–93% hold; one part renumbered 20 of 44 faces for a 1% tweak |
   | a surviving face re-matches | **53.9%** (1 451 of 2 693), not always |
   | a destroyed face orphans | the safe direction, and the usual one |
   | it never mis-pins | **not true: 2 of 2 693**, both on a body of revolution |
   | it speaks for a DELETED feature | **no** — nothing here deletes anything; see the second table below |

   The review reproduced a third class the first run never exercised: a face
   *cut away* whose only surviving candidate sits at the same normal and
   normalized position re-pinned onto it at 0.87 "confidence", because
   `AMBIGUITY_MARGIN` — the constant the whole guarantee rested on — cannot
   fire when there is exactly one candidate. `LONE_AREA_REL`, an absolute area
   bar a lone candidate must clear on its own, was the first answer to it and
   **did not close the class** (`docs/changelog/0125-prd008-verifier-fixes.md`):
   verification widened the same boss to r=20 on a 40 mm plate, where the plate
   top left behind is 0.24 away in area share — inside the 0.30 bar — and the
   thread moved onto it at confidence 0.93. The table above cannot see any of
   this, because that sweep only ever perturbs a *number*.

   So the deletion class was measured on its own terms: 67 deletions (a
   synthetic plate+feature family swept over the adversarial sizes, plus 13
   real deletions in the bundled examples), ground truth from a geometric
   oracle that uses none of the matcher's features — every triangle centroid of
   a before-face tested against the after-mesh — so "destroyed" means the only
   correct answer is `orphaned`.

   | claim | measured (327 destroyed faces) |
   |---|---|
   | a destroyed face orphans | **91.7%** with the area bar alone (27 mis-pins) |
   | …with the adjacency gate | **98.8%** (4 mis-pins) |
   | surviving faces still resolve | 72.8%, and the gate cost none of them |
   | a cut-away face never re-pins | **not true: 4 of 327** — a square pad on a square plate |

   What closed the 23 is not another tolerance: it is the one feature a
   replacement face does *not* reproduce, the number of faces it touches
   (`_same_neighborhood`, read off the same mesh sidecars, no kernel call). It
   costs nothing on the parameter class — all 1 144 correct lone matches there
   keep their neighbor count. The residual 4 and the residual 2 are reported
   rather than tuned away, and every surface says both numbers.

   Slices 8–9 found two further ceilings in the browser
   (`docs/changelog/0119-prd008-threads-ui.md`): a parameter change that moves
   a face's position *relative to the shape's bounds* orphans it even though
   the face still exists (`bbox_uvw` is what makes a pure scale survivable, and
   is exactly what a bounds change moves), and a **closed curved face** — a
   cylinder's side — orphans on any edit, because its area-weighted normal
   nearly cancels and the `NORMAL_DOT` gate then admits no candidate.

   **The honest criterion, and the one the acceptance test asserts:** *a face
   anchor survives a parameter tweak where the face's position within the
   shape's bounds is stable, or reports `orphaned` with a reason and no
   address — and points at the wrong face only rarely (2 in 2 693 across a
   parameter change, 4 in 327 when the face was destroyed outright); the thread
   stays listable either way.* Orphaned is a correct outcome, and
   for a repeated feature (104 near-identical thread faces on
   `fasteners/tapped_plate`) it is the *only* correct outcome. Loosening a
   tolerance to raise the hit rate buys mis-pins: at `AMBIGUITY_MARGIN 0.15`
   the rate rises ~1.5 points and one mis-pin appears. That trade was refused,
   and the reverse trade was taken — `LONE_AREA_REL` costs 1.1 points of hit
   rate to close the lone-candidate hole.
2. **The module is `comments`, not `threads`** — `core/comments.py`,
   `core/anchors.py`, `core/tools_comments.py`, `server/routes_comments.py`.
   `agentcad/toolkit/threads.py` is ISO screw threads and `tests/test_threads.py`
   is its test module. The word survives in the payloads and tool names the
   agent surface froze (`resolve_thread`, `comment_changed {thread}`).
3. **Storage is `<project>/.history/agentcad/comments/`, not `<project>/.threads/`.**
   Inside GIT_DIR, so threads are canonical, ride no branch, are never merged
   and are structurally beyond `project_restore`'s reach — the PRD-002 sidecar
   pattern, and what makes AC8 true by construction rather than by an exclude
   rule that a future `git add -A` could out-vote.
4. **Face signatures are derived in the server from the `.acm` mesh plus the
   `<key>.faces.u32` sidecar — not from the `face_info` handler.** Resolution
   therefore issues **zero** kernel calls and never rebuilds: an unbuilt part's
   anchors come back `unverified`/`part_not_built` instead of triggering a
   300-second build behind a list call. The signature also gained `area_frac`
   (a face's share of the tessellated area) because an absolute area is not
   scale-invariant, and the planned `STICKY_MARGIN` tie-break was **deleted**:
   at an ambiguity margin of 0.20 it is unreachable, and dead code that looks
   like a safety net is worse than none.
5. **Presence is an HTTP heartbeat, not client→server WebSocket traffic.**
   `/ws` lives in `server/app.py` — a core this feature may not edit — carries
   no client identity (`set_client_id` is HTTP middleware) and is guarded by
   HTTP middleware a route pack cannot reproduce. The substance of FR9 (report
   focus, see others) is met over `POST /api/projects/{p}/presence`, whose
   *response* is the whole roster, with `presence_changed` as an optimization.
   The new-attack-surface risk this document raised is answered by not creating
   the surface.
6. **`ProjectStore.write_guard`'s signature did not change.** The part a write
   names travels on a `contextvars` variable (`locks.write_scope`), so
   `service.py`'s lambda, `tools_versioning.install_write_guard` and every
   guard any test installs keep working byte-identically — which is what let
   `tests/test_locks.py` be the AC6 regression gate rather than a casualty.
   Only `write_script` and `update_part_entry` are claim-covered; whole-manifest
   writes stay turn-locked only, and there is a test asserting exactly that.
7. **Per-user undo did not re-key the undo stacks.** `scope` defaults to
   `"any"` and is byte-identical to the behavior that predates authorship,
   because a human pressing Cmd+Z to take back the agent's edit is the product's
   flagship loop; per-client stacks would have left that browser's stack empty.
   `"mine"` skips (never discards) other clients' entries. Authorship is a
   `Client:` commit **trailer**, not a git author field: the client id is an
   unvalidated header and must not be dressed up as a cryptographic claim.
8. **Proposal-hunk anchors landed in the MVP, not in phase 3**, and they answer
   this document's open question: a regenerated packet re-maps by the hunk's
   byte-identical **header** (`moved`, never `ok`, even at the same index), a
   rewritten or now-ambiguous header is `orphaned`, and a packet frozen by a
   merge is `unverified`/`packet_frozen` — the thread is the record of a review
   of exactly that diff.
9. **Notifications are one append-only log per project with a `to` field**, not
   a file per identity: an identity is an unvalidated header, and a filename
   derived from one is a traversal surface. Delivery is the existing broadcast
   with client-side filtering, which is honest on a single-node,
   unauthenticated, 127.0.0.1-only server and is what PRD-005 changes.
10. **The agent surface is five tools and no claim tools** (registry 65 → 70;
    73 with the `[fem]` extra). Agents coordinate through `acquire_turn` and
    branches; claims are human-vs-human, because an agent blocked by a human's
    open editor would 409 on the first write of the loop this PRD exists for.
11. **A lone survivor is not evidence, in the script matcher either.** A
    second review round (`docs/changelog/0124-prd008-codex-fixes.md`) found
    the same shape as divergence 1 in `find_snippet`: the stored context was
    consulted only to break a tie between two or more copies, so deleting the
    anchored one of two identical lines re-pinned the thread onto the
    unrelated survivor and reported `moved` at **confidence 1.0**. A lone hit
    must be contradicted by **neither** side of the stored context. 0124 asked
    only for one *agreeing* side — a real edit routinely rewrites the other —
    and `0125` showed that is too weak, because duplicated blocks end the same
    way far more often than they begin the same way, so the surviving twin
    coincides on one side routinely. What makes the strict rule affordable is
    that a refused hit goes to tier 2's diff, which is exactly what answers a
    block that stayed put with one side rewritten. The uncovered shape is
    stated rather than hidden: an anchor that stored **no** context has
    nothing to corroborate. Related, same round: the cross-branch rule
    (Decision 7) now runs before *every* absence verdict, not only a missing
    part, so a parameter/range/face missing from a part that exists on both
    branches is `unverified`/`other_branch` instead of a false "it was
    removed".
12. **Presence is bounded, and the bounds are part of the design.** Identity
    is a self-asserted header on an unauthenticated server, so `MAX_ID_CHARS`
    (refused, never truncated — truncation would merge two identities into one
    roster/claim/mention key), `MAX_CLIENTS` (**process-wide**, and a full
    roster refuses a *new* row rather than evicting an incumbent, because a
    rotating flood is by construction the newest rows) and `MAX_BUCKETS` on the
    rate limiter, which bounds the limiter's memory — a rotating identity is
    granted its first beat exactly like a real newcomer, and what stops the
    flood is that a table with nothing refilled in it has no room to mint
    another (`0125`). The `presence_changed` broadcast *is* the roster, so
    bounding one bounds both. The identity check itself lives in
    `locks.check_client_id`, not on the presence router: a part write reaches
    the claim registry from the write guard with the same header and never
    passes a presence route (`0125`).
13. **Three deliberate UI gaps**, named rather than left to be discovered: the
    assembly-`instance` anchor has no create affordance in the browser (it is
    creatable over tools/REST and focuses correctly); comment attachments have
    no file picker (an agent attaches a render from `exports/`, and the panel
    renders the chip); and `scope: "mine"` undo has **no toolbar gesture** —
    Cmd+Z stays the shared stack on purpose.

## Risks & open questions

- **Re-anchoring is heuristic** — signatures can mismatch after large
  topology changes. The contract is honesty: orphan rather than guess, and
  publish both mis-pin rates (2 in 2 693 across a parameter change, 4 in 327
  across a deletion) rather than claim there are none;
  tolerances tuned on the bundled examples. PRD-002's hunk anchors
  sidestep the problem by anchoring to immutable diffs.
- **Two coordination mechanisms** (turns + claims) risk confusion — one
  precedence rule, surfaced identically in UI chips and error details;
  agents deliberately get no claim tools.
- **Revert conflicts** make per-user undo feel partial — the scope is
  explicit (clean revert or structured refusal), and the
  branch-per-task workflow (PRD-001) keeps shared-mainline undo rare.
- **Client→server WS traffic is new attack surface** — schema-validated
  messages, per-connection rate limits, the same origin guard as HTTP.
- **`.threads/` sync** — PRD-005's push/pull must carry threads and
  notifications; agree the layout before 005 freezes its sync manifest.
- Open: when a proposal packet regenerates, do hunk-anchored threads
  pin to the old hunk or re-map? Resolved in PRD-002 phase-3 design.

## Competitive references

Onshape ships feature-anchored comments, follow mode, and per-user undo
on microversions — the collaboration benchmark (market_research.md,
"Cloud-native CAD: Onshape"). CoLab built a $72M business on pinned CAD
feedback and is bolting an AI reviewer on top — outside the CAD, on
uploaded files ("The workflow ring"). We differ: anchors extend to
params, script lines, and diff hunks because the model is code; threads
are structured work items agents actually consume and resolve with
kernel-computed evidence; and concurrency is per-part claims plus
explicit agent turns — same-file CRDT stays a deliberate non-goal ("What
we deliberately will not build").
