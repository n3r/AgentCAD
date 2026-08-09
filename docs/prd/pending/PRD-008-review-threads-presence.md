# PRD-008 — Anchored review threads and presence

- **Status:** pending
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

## Risks & open questions

- **Re-anchoring is heuristic** — signatures can mismatch after large
  topology changes. The contract is honesty: orphan, never mis-pin;
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
