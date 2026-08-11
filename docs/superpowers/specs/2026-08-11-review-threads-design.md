# Anchored review threads and presence — design

- **PRD:** [PRD-008](../../prd/in-progress/PRD-008-review-threads-presence.md)
- **Date:** 2026-08-11
- **Depends on (all completed):** PRD-001 (branches, `.history` git layer,
  `UndoCursor`) · PRD-002 (the `.history/agentcad/` workflow-metadata pattern,
  `actor_kind`, the packet's `hunks`, the diff rows' `data-part/hunk/line`) ·
  PRD-003 (the wrapper-not-a-core-edit seam idiom) · PRD-004 (the load-order
  and ephemeral-service traps)
- **Soft dependency, deliberately not waited on:** PRD-005. Identity here is
  **today's client-identity model** — `locks.current_client_id()`, i.e.
  `browser`/`browser:<nonce>`, `chat`/`chat:<session>`, an MCP agent's
  `X-Agent-Id`, `ci`, `local` — and `actor_kind` is `human` **iff** the
  identity is the browser. That is bookkeeping, not authentication, and every
  surface here says so.
- **Plan:** [2026-08-11-review-threads.md](../plans/2026-08-11-review-threads.md)

---

## Problem

Feedback on a model has nowhere to land. "This boss needs a fillet" lives in
chat prose that scrolls away, points at nothing, and cannot be marked done. Two
clients coordinate through one project-wide turn lock, so a human and an agent
take turns on the *whole project* even when they touch different parts. And
nothing tells you who else is in the project or what they are looking at.

Everything this feature needs to *point at* already exists and is reviewed. The
viewport already resolves a click to a B-rep face ordinal through the
`<key>.faces.u32` sidecar. The inspector already renders one `div.param` per
parameter with `data-param` on it. The editor is CodeMirror 5 with a gutter API
sitting unused. The proposals Files tab already emits one `div.diff-line` per
unified-diff line carrying `data-part`, `data-hunk` and `data-line` — placed
there in PRD-002 slice 5 with a comment naming this PRD. `.history/agentcad/`
is already the home of workflow metadata that rides no branch and that
`project_restore` structurally cannot rewind.

So the design work is **not** plumbing. It is three honesty problems:

1. **What does an anchor mean after the thing under it moves?** A line edit, a
   parameter tweak that renumbers face ordinals, a packet regeneration. The
   contract is *orphan, never mis-pin* — a comment pointing at the wrong face
   is worse than a comment pointing at nothing.
2. **What is honestly implementable as "presence" on a single node with no
   authentication?** Not "who is logged in" — that is PRD-005. Only "which
   clients have talked to this server recently, and about what".
3. **How do two coordination mechanisms coexist without becoming a maze?** One
   precedence rule, stated once, surfaced identically everywhere, with agents
   deliberately excluded from the second one.

---

## Architecture at a glance

```
 browser A ──HTTP──┐                    ┌── comment_changed  ─┐
 browser B ──HTTP──┤                    │   presence_changed  │
 chat:main ────────┼─► FastAPI ─► service ── claim_changed ───┼─► EventBus ─► /ws
 MCP agent ────────┘        │              notification    ───┘   (fan-out,
                            │                                       unchanged)
                            ▼
        ┌─────────────────────────────────────────────────────────┐
        │ core/comments.py    CommentStore  (files)                │
        │                     CommentManager(lifecycle, mentions)  │
        │ core/anchors.py     validate() · resolve()  (pure + I/O  │
        │                     on .cache/ and git; NO kernel calls) │
        │ core/presence.py    PresenceRegistry (in-memory, TTL)    │
        │ core/locks.py       + ClaimRegistry (in-memory, TTL)     │
        └─────────────────────────────────────────────────────────┘
                            │
                            ▼
   .history/agentcad/comments/           (canonical · GIT_DIR · branch-free)
     next_id · index.json · notifications.jsonl
     <id>/thread.json · <id>/audit.jsonl
```

Every surface is an extension-point pack. **No edit to `worker.py`,
`tools.py`, `app.py`, `service.py`, `proposals.py`, `packet.py`, `merge.py`,
`branches.py`, `checks.py` or `specs.py`.** Three files outside the packs are
touched, each for a reason the PRD names explicitly:

| File | Change | Why |
|---|---|---|
| `core/project.py` | a `with locks.write_scope(part_id):` around the two part-scoped write paths | FR11's "enforcement stays at the `write_guard` seam", without changing the guard's signature (below) |
| `core/locks.py` | `ClaimRegistry`, `write_scope`, `claim_override` | FR11: "Claims extend `agentcad/core/locks.py`" |
| `core/history.py` | `snapshot` appends a `Client:` trailer; new `revert()` | FR13 / FR14, named in the PRD's technical approach |

New files:

| File | Role |
|---|---|
| `agentcad/core/comments.py` | `CommentStore` (files only) + `CommentManager` (lifecycle, mentions, notifications) |
| `agentcad/core/anchors.py` | anchor validation and **read-time** resolution; face signatures; line remapping |
| `agentcad/core/presence.py` | `PresenceRegistry` — in-memory, TTL, never persisted |
| `agentcad/core/tools_comments.py` | tool pack: the five PRD tools + `service.comments` |
| `agentcad/server/routes_comments.py` | REST for threads and notifications |
| `agentcad/server/routes_presence.py` | presence heartbeat + roster; installs the claim guard |
| `frontend/js/comments.js` | Threads panel, composer, pins, gutter markers, badges |
| `frontend/js/presence.js` | heartbeat loop, avatar strip, tree chips |

---

## Decision 1 — the module is `comments`, not `threads`

The PRD's technical approach says `agentcad/core/threads.py` and
`<project>/.threads/`. **Both are rejected**, because "thread" is already taken
twice in this repository:

- `agentcad/toolkit/threads.py` — ISO screw threads (`external_thread`,
  `threaded_rod`, bd_warehouse), a *part-authoring* symbol re-exported from
  `toolkit/__init__.py`.
- `tests/test_threads.py` — that toolkit's test module.
- and `threading` is imported in nine core modules.

AGENTS.md already carries a "three live name collisions" gotcha from PRD-003;
this is the same class of bug, caught before it lands. So:

- module `agentcad/core/comments.py`, packs `tools_comments.py` /
  `routes_comments.py`, tests `tests/test_comments*.py`,
  frontend `frontend/js/comments.js`;
- storage under `.history/agentcad/comments/`;
- **the domain word "thread" survives in the data and in the agent surface**
  (`thread.state`, `resolve_thread`, `comment_changed {thread}`) because that
  is the PRD's contract and the tool names are frozen by FR7. A *thread* is a
  concept in the payload; it is never a Python module or a file path here.

---

## Decision 2 — where threads live: `.history/agentcad/comments/`

FR5 says "stored per project outside history snapshots (the PRD-002
pattern)". The PRD-002 pattern *is* `.history/agentcad/`, so we use it
literally rather than the PRD's alternative suggestion of a `<project>/.threads/`
directory plus a new `_EXCLUDE_LINES` entry. Four reasons, in order of weight:

1. **`project_restore` structurally cannot touch it.** Restore is
   `git checkout <commit> -- .` in a working tree; `.history/` is GIT_DIR. A
   working-tree `.threads/` would depend on an exclude line staying correct
   forever — one `git add -A` before the exclude is refreshed and discussion
   becomes model state. (AC8 is then true by construction, not by vigilance.)
2. **Branch-independence.** `.history/agentcad/` is the common dir shared by
   every linked worktree at `.history/trees/<branch>/`. A thread on a face must
   be visible from every branch and belong to none — the same argument PRD-002
   makes for proposals. A working-tree `.threads/` would fork per branch and
   silently disappear on a branch switch.
3. **Merges never see it.** A thread cannot conflict, and
   `manifest_merge`/`resolve_merge` need to know nothing about it.
4. **It is the reviewed, tested idiom.** `ProposalStore` is the template, down
   to the id allocator.

Layout (deliberately isomorphic to `proposals/`):

```
.history/agentcad/comments/
  next_id                 "8\n"          the id high-water mark (NOT a cache)
  index.json              {"next_id": 8, "threads": [summary, …]}   (a CACHE)
  notifications.jsonl     append-only: deliveries and read marks
  7/thread.json           the thread document (atomic write)
  7/audit.jsonl           append-only: every action with actor + actor_kind
```

**Durability rules — copied from `ProposalStore`, not reinvented:**

- `thread.json` / `index.json` / `next_id` are `ProjectStore._atomic_write`.
- `audit.jsonl` and `notifications.jsonl` are **appended, never rewritten**.
  A read-modify-replace cycle loses append-only-ness and can truncate the log
  on a crash. There is deliberately no method that edits or removes a line.
- `index.json` is rebuilt from the per-thread directories whenever it is
  missing or unparseable — it is never the source of truth.
- ids are decimal strings from `next_id`, which only increments; the
  high-water mark lives in its own one-line file so a hand-deleted directory
  cannot hand its id to the next thread.
- one `threading.RLock` in the store serializes id allocation, index refreshes
  and appends; one `RLock` in the manager wraps every read-modify-write of a
  thread document.
- paths always come from `store.canonical_path_of` — **never `path_of`**,
  which follows the caller's branch.

Without git on PATH, `.history/agentcad/comments/` is just a directory: the
comments pack **still registers and works**. Only tier-2 line remapping
(Decision 6) and `proposal_hunk` anchors (Decision 8) degrade, and they degrade
by saying so.

### The documents

```jsonc
// thread.json
{
  "id": "7",
  "project": "demo",
  "state": "open",                 // open | resolved
  "anchor": { … },                 // Decision 3 — immutable after creation
  "branch": "feat/nozzle",         // the branch it was authored on (context)
  "author": "browser:7f3a", "author_kind": "human",
  "created": "2026-08-11T09:12:04Z", "updated": "2026-08-11T09:19:31Z",
  "resolved": {"actor": "chat:main", "actor_kind": "agent",
               "ts": "2026-08-11T09:19:31Z"} | null,
  "comments": [
    {"id": "1", "author": "browser:7f3a", "author_kind": "human",
     "ts": "…Z", "body": "this boss needs a fillet",
     "attachments": [], "mentions": [], "edited": null, "deleted": false}
  ]
}
```

```jsonc
// notifications.jsonl — one line per event, two kinds
{"seq": 12, "kind": "mention", "to": "chat:main", "project": "demo",
 "thread": "7", "comment": "2", "from": "browser:7f3a", "ts": "…Z"}
{"seq": 13, "kind": "read", "to": "chat:main", "ids": [12], "ts": "…Z"}
```

`read` is a *line*, not a mutation, so the log stays append-only; unread =
`mention` seqs for `to` minus every seq named by a later `read` line for the
same `to`. Identities are arbitrary strings from an unvalidated header, so a
per-identity **file** would be a path-traversal surface: one shared log with a
`to` field has no such problem and needs no sanitization.

`comments[0]` is the root. **The root comment cannot be deleted** — a thread is
retired by resolving it, and `delete` on `comments[0]` is a `validation_error`.
Editing or deleting any other comment is allowed **only for its own author**
(`locks.current_client_id() == comment.author`, an honesty check, not
authorization) and leaves a tombstone: `deleted: true`, `body: null`, and an
audit line naming the actor and the comment id. An edit records
`details.previous_sha256` rather than the previous text — proof that the text
changed, without a "delete" that retains what it deleted.

---

## Decision 3 — the anchor: six kinds, one envelope, immutable

An anchor is written once, at creation, and **never updated**. Everything about
"where is it now" is computed at read time (Decision 4). This is the single
most important structural choice in the design: a stored anchor is *evidence of
what the author pointed at*, and evidence that rewrites itself is not evidence.

```jsonc
{
  "kind": "face",              // part | face | param | script_range
                               // | instance | proposal_hunk
  "part": "nozzle",            // part | face | param | script_range
  "face_index": 12,            // face
  "param": "wall",             // param
  "start": 40, "end": 47,      // script_range — 1-based, inclusive
  "instance": "nozzle_1",      // instance
  "proposal": "3", "file": "parts/nozzle.py", "hunk": 1,   // proposal_hunk

  // provenance, every kind
  "branch": "feat/nozzle",
  "head": "9f1c…",             // branch head at creation ("" without git)

  // kind-specific evidence, captured at creation
  "signature": {"centroid": [x,y,z], "normal": [x,y,z], "area_mm2": 41.9,
                "bbox_uvw": [0.5, 0.5, 1.0], "n_faces": 26,
                "mesh_key": "3a9c…"},                        // face
  "snippet": "…", "snippet_sha256": "…",                     // script_range
  "before": "…", "after": "…",                               // script_range ctx
  "hunk_header": "@@ -12,6 +12,8 @@", "generation": "b1c2…"  // proposal_hunk
}
```

**Validation on create (FR1) — a bad anchor is a `validation_error`, never a
stored orphan:**

| kind | validated against | rejected when |
|---|---|---|
| `part` | the manifest's `parts` | unknown part |
| `face` | **`max(<key>.faces.u32) + 1`** (see the trap below) | unknown part · `kind != "script"` · index out of range · the part has never been built (`build the part first`, with a hint) |
| `param` | the part's `params_spec` | unknown part · script does not load · unknown param |
| `script_range` | the current script's line count | `start < 1` · `end < start` · `end > lines` |
| `instance` | `manifest.assembly.instances` | unknown instance |
| `proposal_hunk` | `service.proposals` + the packet's `parts[].script_diff.hunks` | unknown proposal · unknown file · index out of range · packet absent/truncated |

> **Trap — `n_faces` has two meanings.** `metrics["n_faces"]` is
> `len(shape.faces())`, and build123d's `faces()` **deduplicates by hash**;
> `faces_in_mesh_order` (a raw `TopExp_Explorer` walk) does not. For a compound
> or shared-face shape the metric is *smaller* than the sidecar's face count.
> FR1 already names the right authority: the `<key>.faces.u32` sidecar. Validate
> against `max(sidecar) + 1`, and never against `metrics.n_faces` or
> `face_info.n_faces`.

Reference (imported STL/STEP) parts get **no** face anchors: they have no
sidecar, `handlers/reference.py` reports a hardcoded `n_faces: 1`, and an
imported STL is one welded mesh face with no surface. A `face` anchor on a
`kind == "reference"` part is a `validation_error` naming that; `part` and
`instance` anchors on them are fine.

---

## Decision 4 — resolution is computed at read time and has four states

An anchor's *current* status is never stored. Every read computes it:

```jsonc
"resolution": {
  "status": "ok" | "moved" | "orphaned" | "unverified",
  "face_index": 14,            // the CURRENT ordinal, when moved
  "start": 44, "end": 51,      // the CURRENT range, when moved
  "confidence": 0.97,          // matcher score, face and script_range
  "reason": "part_not_built",  // always present unless status == "ok"
  "hint": "…",                 // present for unverified and orphaned
  "against": {"branch": "main", "head": "1a2b…"}   // what it resolved against
}
```

- **`ok`** — the anchor still points at what it pointed at, by identity
  (ordinal + signature agree, or the snippet is byte-identical at the stored
  range).
- **`moved`** — re-matched with a different address; the payload carries the
  new address and the score. The stored anchor is unchanged.
- **`orphaned`** (FR3) — the target is gone, or no candidate cleared the
  tolerance. The thread stays readable, listable, resolvable, and carries its
  **last-known anchor payload**. Never silently dropped, never re-pointed.
- **`unverified`** — *we do not know*, and that is a fourth fact, not a
  synonym for "fine". Reasons: `part_not_built` (no mesh in `.cache/`, and a
  list call may not force a 300 s build), `no_git` (tier-2 remap unavailable),
  `packet_frozen`, `other_branch` (the anchor's branch is not the reader's and
  the target does not exist there).

PRD-003's four-value spec vocabulary made exactly this distinction between
`skip` and `error`; the same discipline applies here. Collapsing `unverified`
into `ok` would let a UI draw a pin at a stale ordinal.

**Resolution never calls the kernel and never forces a build.** A comment list
must stay a cheap read: `list_comments` on a 40-part project must not rebuild
40 parts. Cost budget per call: manifest read (cached), plus at most one
`.acm` + `.faces.u32` read per distinct part (memoized by cache key), plus at
most one `git cat-file blob` per distinct `(head, part)`.

`list_comments {resolve_anchors: false}` returns anchors with no `resolution`
block at all for the cheapest possible listing; the default is `true`.

---

## Decision 5 — face anchors: signatures derived from the mesh, in-process

FR2 says a face anchor stores "a geometric signature at creation
(`{center, normal, area_mm2}` — the `face_info` payload)". `face_info` is a
real handler (`kernel/handlers/facemod.py`) — and it is the wrong source:

- it **rebuilds the shape from scratch on every call** (`tools_facemod`,
  `timeout_s=300`), so verifying N anchors costs N builds;
- it returns one face per call, so a *re-match* — which must compare against
  **every** face — costs `n_faces` builds;
- its `normal_at()` samples at u=v=0.5, a local property of a curved face, and
  its `center()` is a centre of mass that need not lie on the surface.

Instead, **signatures are derived in the server process from files the build
already wrote**: `<key>.acm` (positions + triangle indices) and
`<key>.faces.u32` (one face ordinal per triangle). `agentcad/kernel/acm.py`
states in its own docstring that it has no OCP dependency, and `core/packet.py`,
`core/service.py` and `core/tools_vision.py` already import it from the server
process. NumPy is already a transitive dependency.

```python
# core/anchors.py — pure numpy over two files; no kernel, no OCP, no rebuild
def face_table(acm_bytes, face_ids) -> list[dict]:
    """One row per face ordinal: area-weighted centroid, area-weighted unit
    normal, tessellated area, and the centroid in normalized bbox coordinates."""
```

Per face: `area` = Σ triangle areas; `centroid` = area-weighted mean of
triangle centroids; `normal` = normalized area-weighted sum of triangle
normals; `bbox_uvw` = the centroid mapped into the **shape's** bounding box as
three fractions in `[0, 1]`.

Three properties make this the better signature, not merely the cheaper one:

1. **Consistency beats accuracy.** A tessellated area is not the B-rep area,
   but it is computed identically at creation and at match time, at the same
   `MESH_TOLERANCE`. For matching, a consistent estimator wins.
2. **`bbox_uvw` is scale-invariant.** AC2 requires a face anchor to survive "a
   param tweak". A parameter that scales the part moves every absolute centroid
   and every absolute area — and moves *no* `bbox_uvw`. This is the single
   change that makes AC2 winnable with tight tolerances.
3. **Zero marginal cost.** The inputs are already on disk after any build, and
   the table is memoized in-process keyed by the mesh cache key, plus persisted
   as `<key>.facesig.json` beside the mesh (atomic write, content-addressed like
   everything else in `.cache/`). PRD-004's determinism stage compares an
   explicit tuple `(".acm", ".faces.u32")`, so a third sidecar is invisible to
   it.

Absolute `centroid`/`normal`/`area_mm2` are stored **as well**, because they
are what a human or an agent reads in the payload ("the 41.9 mm² face at
z = 60"), and `face_info` remains the tool an agent calls to inspect one face.

### The matcher

Given the stored `signature` and the current `face_table`:

```
candidates = [f for f in table if
                 dot(f.normal, sig.normal)        >= NORMAL_DOT      (0.985, ~10°)
             and |f.area - sig.area| / max(...)   <= AREA_REL        (0.25)
             and dist(f.bbox_uvw, sig.bbox_uvw)   <= UVW_DIST        (0.15)]

score(f) = 0.5 * dot(normal)
         + 0.3 * (1 - |Δarea| / max(area))
         + 0.2 * (1 - dist(bbox_uvw) / UVW_DIST)

best, runner_up = two highest scores
if not candidates                      -> orphaned  (reason: "no_candidate")
if best.score - runner_up.score < 0.05 -> orphaned  (reason: "ambiguous")
if best.index == anchor.face_index     -> ok
else                                   -> moved (face_index = best.index)
```

- **Tie-break to the stored ordinal**: when the stored ordinal is itself a
  candidate and within `0.02` of the best score, keep it. Stability over
  cleverness.
- **`ambiguous` is an orphan, not a guess.** Six identical faces of a cube are
  genuinely indistinguishable by this signature once the part rotates; the PRD's
  contract is "orphan, never mis-pin".
- **The constants are placeholders until measured.** They must be tuned against
  the bundled examples (Decision 20, risk R1) and are module-level named
  constants with a comment citing the measurement that set them.
- **Fast path:** when the current mesh cache key equals `signature.mesh_key`,
  the geometry is byte-identical — return `ok` without building a table.

---

## Decision 6 — script_range anchors: two tiers, both stdlib

**Tier 1 — snippet search (no git, always available).** The anchor stores the
exact `snippet` (the lines `[start, end]`, capped at 40 lines / 4 KiB), its
sha256, and three lines of context each side (`before`, `after`).

```
if lines[start-1:end] == snippet                -> ok
matches = every offset where the snippet occurs exactly
  1 match                                       -> moved
 >1 matches -> disambiguate by before/after context
  1 survives                                    -> moved
```

AC3 ("tracks its lines across an edit inserted above it") is won entirely in
tier 1, exactly, with no heuristics.

**Tier 2 — a real line map (needs git).** When tier 1 finds zero or several
candidates, read the script as it was at the anchor's stored `head`
(`git cat-file blob <head>:parts/<id>.py`, through `history._run_bytes` — never
raw `subprocess`) and build a line map from
`difflib.SequenceMatcher(None, old_lines, new_lines).get_opcodes()`:

- `equal` blocks map old→new by offset;
- a range wholly inside an `equal` block → `moved`, `confidence: 1.0`;
- a range that survives partially (its first and last lines both map) →
  `moved` over the mapped span, `confidence` = mapped fraction;
- a range wholly inside a `delete`/`replace` block → `orphaned`
  (`reason: "lines_removed"`).

Below `LINE_CONFIDENCE_MIN = 0.6` the result is `orphaned`, never a low-quality
"moved". With no git, or an unreachable head (gc, deleted branch), tier 2 is
skipped and the answer is `unverified` (`reason: "no_git"` /
`"head_unreachable"`) — not `orphaned`: we did not look, so we must not claim.

`param`, `part` and `instance` anchors need no tier: existence in the manifest
is `ok`, absence is `orphaned`. They cost one manifest read.

---

## Decision 7 — cross-branch reads

Threads are branch-free storage, but their targets are not. Resolution always
runs against **the reader's current branch** (`branches.current`), and the
result records `against: {branch, head}`.

- Target exists on the reader's branch → resolve normally (`ok`/`moved`/
  `orphaned`).
- Target does not exist there **and** the anchor names a different branch →
  `unverified`, `reason: "other_branch"`, with a hint naming the anchor's
  branch. Claiming `orphaned` would tell a user their comment's face was cut
  away when they merely switched branches.
- `list_comments {branch: "<name>"}` filters to threads authored on one branch;
  the default is every thread, because a review comment on `main` must be
  visible while you work on `feat/x`.

---

## Decision 8 — `proposal_hunk` anchors

PRD-002 left two things here on purpose: `packet._script_diffs` emits
`hunks: [{index, header, old_start, new_start}]` "so PRD-008 can anchor a
thread to a hunk without depending on line numbers surviving a regeneration",
and `proposals.js:diffBlock` stamps `data-part` / `data-hunk` / `data-line` on
every row. Both are used as-is; neither changes.

The anchor stores `{proposal, file, hunk, hunk_header, generation, head}` —
`generation` being the packet's generation id (PRD-002 0085 made diff assets
generation-namespaced). Resolution against the *current* packet:

| condition | status |
|---|---|
| same `generation` | `ok` |
| new generation, a hunk in the same file whose `header` matches byte-for-byte, uniquely | `moved` (`hunk` = its index) |
| new generation, header absent or non-unique | `orphaned` (`reason: "hunk_regenerated"`) |
| packet frozen by a merge | `unverified` (`reason: "packet_frozen"`) — the diff it describes is history now, and the thread is a record of a review of exactly that |
| packet missing / file no longer in the diff | `orphaned` |

This answers the PRD's open question ("when a packet regenerates, do
hunk-anchored threads pin to the old hunk or re-map?"): **re-map by header
identity, orphan otherwise, and never regenerate a packet in order to resolve a
comment.** Resolution reads the persisted `packet.json` only — it must never
call `service.packets.packet(...)`, which can build geometry and can move a
proposal's state.

Note also: `data-line` in the DOM is the **0-based index into the unified-diff
text**, not a source line number, and rows before the first `@@` carry
`data-hunk="-1"`. The UI must anchor to `data-hunk`, and treat `-1` as "no
hunk" (the composer is not offered there).

---

## Decision 9 — lifecycle, attribution and the audit

- States are exactly `open` and `resolved` (FR4). `resolve_thread` on a
  resolved thread is idempotent and records nothing; `reopen_thread` moves it
  back and records actor + ts. There is no `closed`, no `wontfix`, no
  assignment — those are PRD-005/PRD-031 shapes.
- **Resolution is not authorization.** Anyone may resolve or reopen any thread,
  because there is no authentication to base a rule on. The audit says who did.
- Every mutation appends one line to `<id>/audit.jsonl` with the
  `ProposalStore.append_audit` shape: `{seq, ts, actor, actor_kind, action,
  details}`. Actions: `created`, `replied`, `resolved`, `reopened`,
  `comment_edited`, `comment_deleted`, `mentioned`.
- `actor_kind` is imported from `core/proposals.py` — **not re-implemented**.
  `human` iff the identity is `browser` or `browser:*`; the chat dock is a
  human asking an *agent*, so those actions are the agent's.
- A thread carries `author_kind` at the thread level and per comment, so the UI
  can render a human/agent distinction without re-deriving it.

---

## Decision 10 — client identity, and how two browsers become two identities

Today **every browser tab is the identity `browser`** — the middleware sets
`set_client_id(request.headers.get("x-agent-id") or "browser")` and the
frontend sends no header. AC1 and AC5 are written about "browser A" and
"browser B", and claims are defined as conflicting between "different humans".
With one identity for all browsers, neither is testable and claims are inert.

**`frontend/js/api.js` gains one default header:**
`X-Agent-Id: browser:<8 hex>`, generated once and kept in **`localStorage`**
under `agentcad.client_id`.

- `localStorage`, not `sessionStorage`: identity is per browser profile, so two
  tabs of one profile stay one client — which is what keeps today's per-client
  branch checkout (`checkouts.json` is keyed by client id) behaving as it does
  now. Two *browsers* (or a normal + an incognito window) are two identities,
  which is exactly the AC1/AC5 setup.
- `actor_kind("browser:7f3a")` is already `"human"` — PRD-002 anticipated this
  prefix and `tests/test_proposals.py` asserts it.
- Every server-side test that uses `TestClient` without the header keeps the
  identity `browser`, so nothing existing changes.
- Identity is still **not authentication**: the header is unvalidated and
  self-asserted. Every tool description and the user guide say so.

**Display labels are presence data, not identity.** A client may send a
`label` with its heartbeat (default: `"Browser"` / the agent id); it is shown
in chips and avatars and is never written into a thread, an audit line, or a
lock. Only the identity string is persisted.

---

## Decision 11 — mentions and notifications

`@<identity>` in a body (regex `(?<![\w@])@([A-Za-z0-9_.:-]{1,64})`) creates a
notification per FR6. Rules:

- The mention must name a **plausible identity**: `browser`, `browser:*`,
  `chat`, `chat:<session>` (validated with `chat.SESSION_ID_RE`), or a
  currently-or-recently-present client id from the presence registry. Anything
  else is *not* a mention — it stays plain text in the body and is not
  delivered. Silently minting notifications for `@todo` is noise.
- Mentions are recorded on the comment (`mentions: [ids]`), appended to
  `notifications.jsonl`, and published as `notification {to, project, thread,
  comment, ts}`.
- **The bus is a broadcast.** Every `/ws` client receives every `notification`
  event and filters on `to` client-side. That is honest for a single-user,
  single-node, 127.0.0.1-only server with no authentication, and it is stated
  in the docs; PRD-005 is where per-principal delivery becomes real. No secret
  is disclosed that a `GET` on the same box would not disclose.
- Self-mentions do not notify.
- `list_notifications {project?, unread?}` returns `{notifications: [...],
  unread: n}` for **the calling identity only**.
- Marking read is a **route, not a tool** (`POST /api/projects/{p}/notifications/read`):
  agents do not need a read cursor, and the PRD's tool list is exactly five.

---

## Decision 12 — events

| event | payload | published by |
|---|---|---|
| `comment_changed` | `{project, thread, state, action, part}` | `CommentManager` on every mutation |
| `notification` | `{to, project, thread, comment, from, ts}` | `CommentManager` on a mention |
| `presence_changed` | `{project, clients: [{id, kind, label, focus, since}], claims: {...}}` | `PresenceRegistry` on a roster change |
| `claim_changed` | `{project, part, holder, holder_kind, expires_at, overridden_by?}` | `ClaimRegistry` via the presence pack |

All four go through `service.bus.publish` and reach the browser over the
existing `/ws` fan-out. **`bus.on_publish` is not touched** — it is a single
slot already claimed by `service._snapshot_on_event`, and PRD-003 learned that
lesson. None of these events is `project_changed`, so none of them triggers a
history snapshot: a comment is not a model change.

`presence_changed` is published only when the fanned-out roster actually
differs (join, leave, focus change, claim change) — a 15-second heartbeat from
five clients must not be 20 events a minute.

---

## Decision 13 — presence: an HTTP heartbeat, not a client→server WebSocket

FR9 says clients "report focus over the existing WebSocket channel". **We
diverge on the transport and meet the requirement's substance.** Reasons, in
order:

1. **`/ws` lives in `server/app.py`, a core the extension-point contract
   forbids editing to add a feature.** Its receive path
   (`_wait_for_websocket_disconnect`) exists to detect disconnects; teaching it
   a message schema means editing the file every feature is told not to edit.
2. **The WebSocket has no identity.** `set_client_id` is called only by the
   HTTP middleware; `websocket_endpoint` never calls it. Presence keyed on a
   connection with no client id would require inventing identity plumbing on a
   second path — and then reconciling it with the HTTP one.
3. **The origin/Host guard is HTTP middleware.** Over HTTP, presence inherits
   the reviewed guard, the identity plumbing, the error mapping and the
   rate-limiting story for free. The PRD's own risk list calls client→server WS
   traffic "new attack surface"; the cheapest way to not have new attack
   surface is to not open a new inbound channel.
4. A route pack *can* declare a WebSocket route on its `APIRouter`, but it
   cannot see `create_app`'s `allowed_hosts` (which carries
   `extra_allowed_hosts={"testserver"}`), so it could not reproduce the guard
   correctly under test.

So:

```
POST   /api/projects/{proj}/presence   {part_id?, surface?, label?, claim?, leave?}
   ->  {you, clients: [...], claims: {...}, ttl_s}
GET    /api/projects/{proj}/presence   ->  same payload, no registration
```

- The UI heartbeats every **15 s** (`PRESENCE_HEARTBEAT_S`); an entry expires
  after **45 s** (`PRESENCE_TTL_S`). On `pagehide` the UI sends
  `{leave: true}` via `navigator.sendBeacon` (a POST, so DELETE is not used).
- **No reaper thread.** Expiry is computed lazily on every read; because the
  heartbeat *response* carries the full roster, every client converges within
  one heartbeat even if it misses every event. `presence_changed` is an
  optimization, not the mechanism. A background thread in the server process
  would be a new lifecycle to own for no gain.
- **Presence is never persisted** (FR9). The registry is a dict in
  `AgentCADService`'s process; a restart empties it, which is correct.
- Rate limit: a per-identity token bucket (**1 heartbeat/second, burst 5**);
  over-rate calls return the roster with `throttled: true` rather than an
  error — a heartbeat must never surface as a red toast.
- Focus payload: `{project, part_id?, surface: "viewport"|"editor"|
  "inspector"|"proposals"}` (FR9's set plus `proposals`, where reviewers
  actually are). Anything else is a `validation_error`.

---

## Decision 14 — claims: one precedence rule, human-vs-human only

`core/locks.py` gains a `ClaimRegistry` built exactly like `TurnLock`
(a `threading.Lock`, a dict, wall-clock TTL, raise-never-block):

```python
class ClaimRegistry:
    def acquire(self, key: str, part: str, holder: str, ttl_s: float = 90.0) -> dict
    def release(self, key: str, part: str, holder: str) -> dict
    def get(self, key: str, part: str) -> dict | None
    def all(self, key: str) -> dict[str, dict]
    def check(self, key: str, part: str | None, client_id: str, *,
              override: bool = False) -> None
```

`key` is `store.lock_key(proj)` — the same branch-aware key turn locks and undo
stacks use, never the bare project name (PRD-001's rule).

### The precedence table — the one rule, stated once

Evaluated in this order on every persistent write:

1. **Project turn held by another client** → `ConflictError` (unchanged code
   path, unchanged message, unchanged details). *AC6 is the regression gate:
   `tests/test_locks.py` must pass unmodified.*
2. **The caller holds the project turn** → no claim check at all (FR12:
   "Claims never apply to a client holding the turn").
3. **The write names a part, that part is claimed by a different client, and
   both the holder and the caller are `human`** → `ConflictError` with
   `details = {claim: {holder, holder_kind, expires_at, part}, overridable:
   true}`. A retry under `override: true` proceeds, steals the claim, and
   publishes `claim_changed` with `overridden_by`.
4. **Otherwise** → proceed, and refresh the caller's claim on that part.

Rule 3's human-vs-human restriction is the PRD's own wording ("a write to a
claimed part by a *different human*") and it is load-bearing: if an agent's
write were blocked by a human's open editor, the flagship loop — human pins a
comment on a face, agent fixes it and replies — would 409 on the agent's very
first write. **Agents are governed by turns; claims stop two humans clobbering
each other.** Agents get no claim tools, exactly as the PRD's agent surface
says.

### How the part reaches the guard, without changing its signature

`ProjectStore.write_guard` is `Callable[[str], None]`. Widening it to
`(proj, part=None)` would require editing `service.py` (the default lambda),
`tools_versioning.install_write_guard`, and any test that installs a guard.
Instead, `core/locks.py` gains a context manager and `core/project.py` wraps
its two part-scoped write paths:

```python
# core/locks.py
write_part_var: ContextVar[str | None] = ContextVar("agentcad_write_part", default=None)
override_var:   ContextVar[bool]        = ContextVar("agentcad_claim_override", default=False)

@contextmanager
def write_scope(part: str | None): ...      # sets/resets write_part_var
@contextmanager
def claim_override(on: bool = True): ...    # sets/resets override_var
def current_write_part() -> str | None: ...
```

```python
# core/project.py — the only change here
def write_script(self, proj, part_id, text):
    with locks.write_scope(part_id):
        if self.write_guard is not None:
            self.write_guard(proj)
        ...
def update_part_entry(self, proj, part_id, **fields):   # the params path
    with locks.write_scope(part_id):
        ...  # existing body, whose save_manifest calls the guard
```

Every existing guard ignores the contextvar and behaves byte-identically.
Coverage is then honest and bounded:

- **Claim-covered:** `write_script` (the editor, `edit_script`, push/pull) and
  `update_part_entry` (params, material, label, solid materials) — i.e. FR11's
  "editor focus, param drag, push/pull".
- **Not claim-covered (turn lock only, by design):** whole-manifest structural
  writes — `add_part`, `remove_part`, assembly edits, project materials — plus
  `project_restore` and undo/redo, which are project-wide by nature. A claim is
  a *part* claim; pretending it guards project-wide operations would be a lie
  told by a green test.

The claim guard is installed by **wrapping** the existing guard
(`install_claim_guard`, idempotent by function attribute — PRD-003's
`install_rebuild_specs` precedent), calling the previous guard **first** so
`ensure_checkout` and the turn check keep their order. Because tool packs load
alphabetically and `tools_versioning` (`v`) *replaces* `write_guard` after
`tools_comments` (`c`) would have wrapped it, installation is **lazy**:
`ensure_claim_guard()` runs at the top of every claims entry point, and
unconditionally from `routes_presence.build_router` (route packs are mounted
after every tool pack, so the server always has it before the first request).
This is exactly how `ProposalManager` installs its `branch_delete` guard.

### Where `override: true` comes from

The browser's two part-write routes — `PUT /api/projects/{proj}/parts/{part_id}`
(script) and `PATCH /api/projects/{proj}/parts/{part_id}/params` — live in
`app.py:203,214`, a core we may not edit, so they cannot grow an `override` body
key or read a new header. The override therefore arrives **out of band, as a
one-shot arming call** from the claims route pack:

```
POST /api/projects/{proj}/claims/override  {part}
   -> {armed_until, claim}     # arms override for THIS identity + part, 30 s
```

The UI's conflict dialog ("Nikita is editing nozzle — Override?") calls it and
retries the write. `ClaimRegistry.check` consults the armed set (keyed
`(key, part, client_id)`, TTL 30 s, single-use) in addition to
`override_var` — the contextvar is what tools and library callers use
(`with locks.claim_override(): ...`), the route is what the browser uses. One
mechanism, two entry points, no core edit, and the override is auditable
because arming publishes `claim_changed`.

---

## Decision 15 — snapshot authorship (FR13)

`ProjectHistory.snapshot(path, message)` today commits as a fixed repo-local
identity (`AgentCAD <agentcad@local>`) and records nothing about the caller.
`MergeOrchestrator` already sets the precedent for the fix: its commit message
carries a `Merged-by: <client_id>` trailer.

- `snapshot()` appends a `Client: <locks.current_client_id()>` trailer to the
  message when one is not already present. Message *body* is untouched, so
  every existing prefix match (`"restore "` in `UndoCursor._step`, the
  reconciler's scans, `_VALIDATION_RE`) still holds — but the implementer must
  grep for exact-message assertions before landing this (risk R5).
- `log()` parses the trailer into `author` on each row (absent → `null`,
  meaning "committed before authorship existed", never `"unknown"`).
- Git's `author`/`committer` fields stay the fixed identity: rewriting them
  would make an unauthenticated header look like a cryptographic claim about
  who wrote a commit.

---

## Decision 16 — per-user undo: honest scope

`UndoCursor` today is two in-memory stacks keyed by `store.lock_key(proj)` —
i.e. per branch, shared by every client. Two things follow.

**What we will not do: re-key the stacks by client id.** It looks like the
obvious reading of "per-user undo" and it would break the product's flagship
loop. Today a human watches the agent edit and presses Cmd+Z to take it back.
With per-client stacks the browser's stack would be empty and Cmd+Z would do
nothing (or, worse, fall through to the post-restart git-log path and undo
something else). A regression in the one interaction this product is *about* is
not a feature.

**What we will do:**

1. Stack entries gain `author` (`locks.current_client_id()`, read inside
   `on_snapshot`, which runs synchronously in the mutating call's context).
2. `undo`/`redo` gain `scope`: `"any"` (**default — byte-identical to today's
   behavior, so the whole existing undo suite passes unmodified**) or
   `"mine"` (pop the caller's most recent entry, skipping others').
3. `scope: "mine"` where the entry **is** the branch head → today's restore
   path, unchanged.
4. `scope: "mine"` where others committed after it → `ProjectHistory.revert`:
   `git revert --no-commit <sha>` through `history._run`, then a commit
   `revert <sha8> (undo by <client>)`. A conflict → `git revert --abort`
   (and a `git reset --hard HEAD` belt-and-braces), the entry stays on the
   stack, and the caller gets `ConflictError` with
   `{commit, reason: "overlapping_changes", paths: [...], blocked_by: [shas]}`.
   Never a partial apply, never a silent clobber (FR14).
5. `UndoCursor.status` reports `mine` counts alongside the totals so the UI can
   label the button.

`revert` is the one genuinely new git verb in this design, running against a
GIT_DIR at `.history` with the project as work tree (and a linked worktree for
non-default branches). **The plan's first task in that slice is a spike that
proves it** (risk R4). If the spike fails, the honest fallback is not "ship a
half revert": it is to land steps 1–3 (which make AC7's *second* half — the
structured conflict — true and correct) and re-scope AC7's first half in the
PRD with the evidence. The plan says so explicitly rather than discovering it
at the end.

---

## Decision 17 — the agent surface

Exactly the five tools FR7/the PRD's agent surface name. Registry count moves
**65 → 70** (68 → 73 with `[fem]`); `docs/agent-api.md`'s header must be
updated with it.

```
list_comments   {project, part_id?, state?, kind?, branch?, anchor_status?,
                 resolve_anchors?=true}
  -> {threads: [{...thread, anchor: {...}, resolution: {...}}],
      counts: {open, resolved, orphaned}}

add_comment     {project, anchor?, thread?, body, attachments?}
  -> {thread}            # exactly one of anchor|thread; both or neither is a
                         # validation_error naming the rule

resolve_thread  {project, thread} -> {thread}
reopen_thread   {project, thread} -> {thread}
list_notifications {project?, unread?} -> {notifications: [...], unread: n}
```

Every mutating call returns the **post-state thread** (the repo's convention:
mutating operations return post-state, never bare OK). Tool descriptions must
state, in the description text: that a face anchor may come back `orphaned` and
what that means; that `actor_kind` is bookkeeping, not authentication; that
attachments must live under `exports/`; that agents have no claim tools and
coordinate through `acquire_turn` and branches.

**Attachments (FR8).** Each entry is stored as an `exports/`-relative POSIX
path. Validation: reject absolute paths that do not `resolve()` under
`store.exports_dir(proj).resolve()` (resolve **both** sides — macOS hands back
`/private/var` for `/var`); reject any relative path containing `..` or not
starting with `exports/`; require the file to exist at creation; cap at 8 per
comment. Anything else is a `validation_error` — "no path disclosure through
comments". At read time a missing file is reported as
`{path, available: false}` rather than an error, because `exports/` is
branch-scoped and a render made on another branch legitimately is not here.

---

## Decision 18 — REST surface

`routes_comments.py`:

```
GET    /api/projects/{proj}/comments?part_id=&state=&kind=&branch=
POST   /api/projects/{proj}/comments                 {anchor|thread, body, attachments}
POST   /api/projects/{proj}/comments/{id}/resolve
POST   /api/projects/{proj}/comments/{id}/reopen
PATCH  /api/projects/{proj}/comments/{id}/comments/{cid}   {body}
DELETE /api/projects/{proj}/comments/{id}/comments/{cid}
GET    /api/projects/{proj}/notifications?unread=
POST   /api/projects/{proj}/notifications/read       {ids?}   # omit ids = all
```

`routes_presence.py`:

```
POST   /api/projects/{proj}/presence   {part_id?, surface?, label?, claim?, leave?}
GET    /api/projects/{proj}/presence
POST   /api/projects/{proj}/claims/override   {part}
```

Both packs **whitelist body keys** before forwarding to a tool — never
`**body` (the registry rejects unknown and `null`-typed args). Errors are the
three existing types only (`NotFoundError`/`ValidationError`/`ConflictError` →
404/422/409). **No new error type**: an orphaned anchor is payload, never an
exception.

---

## Decision 19 — UI surfaces and their MVP scope

Every surface follows an existing pattern; none needs new vendored code.

| Surface | Pattern it copies | MVP |
|---|---|---|
| **Threads panel** | a 4th inspector tab: `.tab[data-tab="threads"]` + `#pane-threads`, added to `inspector.js`'s `panes` map | list open/resolved with anchor breadcrumb + status chip, composer, reply, resolve/reopen, click-to-focus |
| **Face pins** | an absolutely-positioned HTML overlay sibling of the canvas, like `#facecard`/`#hud` — **no `CSS2DRenderer` is vendored**; project the anchor's world point with `camera.project()` inside the existing render loop | a small numbered badge per open face thread on the current part; click opens the thread |
| **Comment on a face** | a button in the existing `#facecard` (`renderFaceCard`) | opens the composer pre-filled with the face anchor |
| **Editor gutter** | CodeMirror 5's `gutters` option + `setGutterMarker` — available in the vendored bundle, currently unused | a marker on the first line of each open `script_range` thread; click opens the thread |
| **Param badge** | `span.ref-badge` in `inspector.js` / `span.row-badge` in `tree.js` | a count badge in `.param-head`; **re-applied after every `buildParamControls`**, which rebuilds rows whenever `params_spec` changes |
| **Diff-hunk comments** | the existing `div.diff-line[data-part][data-hunk]` rows | a hover affordance on hunk rows; **re-applied after every `renderDetail()`**, which rebuilds the pane on each tab click |
| **Avatars** | `renderLockIndicator()`: lazily create a span and `insertBefore(#conn-dot)` — no `index.html`/CSS churn | initials chips per present client, colored from `tree.js`'s `INSTANCE_PALETTE`, tooltip = label + focus |
| **Tree chips** | `span.row-badge` / `span.row-dot` in `tree.js` | a dot per part with a present client or a claim; `"presence"` added to that module's `onKeys` |
| **Notifications drawer** | `#proposals-btn` + `#proposals-count` badge | toolbar button with unread count, list, click-to-open-thread |

Rendering discipline that the implementer must respect, because the existing
code makes it a trap:

- `tree.js` clears `#parts-list` and rebuilds on every relevant state change,
  so indicators must be **rendered from state**, never poked in imperatively.
- `proposals.js` rebuilds `#prop-pane` on every tab click and on every
  `proposal_changed`, so diff-row affordances must be re-applied after each
  render.
- `inspector.js` full-rebuilds param controls only when the part id or the
  `params_spec` JSON changed; badges must be re-applied on both paths.
- `viewport.js`'s overlay rule: overlay objects are parented to the **scene
  root**, never `contentGroup`, so they never enter the pick set; `clearContent`
  already clears highlights and diff overlays. Pins are HTML, so they follow the
  panel rule instead — hidden with the part switch, redrawn from state.

---

## Decision 20 — what is out of scope, explicitly

- **Multi-tenant identity, principals, permissions, cross-user delivery
  channels — PRD-005.** Identity here is a self-asserted header. Nothing in
  this design may be described as authentication or authorization, and the
  broadcast `notification` event is only defensible under that assumption.
- **Marketplace / social / public review — PRD-031.**
- **Same-file CRDT co-editing** — a deliberate non-goal
  (`market_research.md`, "What we deliberately will not build").
- **Review verdicts and merge gating — PRD-002.** Threads inform; verdicts
  decide. This feature adds **no gate provider** and touches
  `service.gate_providers` not at all. An orphaned or open thread never blocks
  a merge.
- **Follow mode / camera sharing** — not in the PRD's MVP; presence stops at
  "who is here and what are they looking at".
- **Persisted presence, presence history, idle detection beyond TTL.**

---

## Risks the implementer must verify empirically

**R1 — face-ordinal stability is unproven, and the tolerances are guesses.**
Evidence gathered for this design: a face index is a bare ordinal from
`TopExp_Explorer` (`toolkit/facemod.py:27-42`); *nothing* in the codebase
persists a face ordinal across a rebuild except the `push_face(build(p), i, d)`
block written into a script, whose own tool description warns that "face
indices are re-derived from the NEW geometry after each edit"; every test that
needs a face **searches for it by normal and centre** rather than hardcoding an
ordinal (`tests/test_facemod.py:131-144, 207-231`). PRD-004's determinism stage
byte-compares `.faces.u32`, but the second build shares the same kernel process
(affinity routing sends it to the same worker), so it proves reproducibility
within one OCCT process — not stability across a *parameter change*, which is
the case AC2 is about. **Required before the tolerances are frozen:** a spike
that, for each bundled example, builds at the default params and at a small
perturbation, and reports how many faces keep their ordinal, how many the
matcher moves, and how many it orphans. Set `NORMAL_DOT`, `AREA_REL`,
`UVW_DIST` and the ambiguity margin from those numbers, and cite the run in a
comment. If the matcher cannot clear AC2 on a real example, the honest outcome
is a documented smaller claim — not looser tolerances that mis-pin.

**R2 — two meanings of `n_faces`.** `metrics.n_faces` (deduped) can be smaller
than the sidecar's count. Validating a `face_index` against the metric would
reject legitimate anchors on compounds. Assert both numbers in a test on a
multi-solid example so the divergence is documented, not folklore.

**R3 — signature derivation from `.acm` must agree with the kernel.** Verify
that the face table's per-face centroid/normal/area computed from the mesh are
consistent with `face_info` for planar faces (normal within ~1e-3, centroid
within tessellation tolerance) and understand where they diverge for curved
ones (they will — `face_info` samples at u=v=0.5 and reports the B-rep area).
The matcher only needs internal consistency, but the *payload* shows these
numbers to humans, so the divergence must be measured and documented rather
than discovered in a bug report.

**R4 — `git revert` inside this git layout.** GIT_DIR is `.history`, the work
tree is the project, and non-default branches are linked worktrees at
`.history/trees/<b>/`. Verify, as the first task of the undo slice, that
`revert --no-commit` + commit works through `history._run` in both layouts,
that `revert --abort` restores cleanly after a conflict, and that the resulting
commit is snapshot-compatible (it must not confuse `UndoCursor`'s
`"restore "`-prefix guard or `parent_of`).

**R5 — the `Client:` trailer changes commit messages.** Grep the suite for
exact-message assertions (`test_history.py`, `test_branches.py`,
`test_versioning_api.py`, the proposals reconciler's scans) before landing
Decision 15.

**R6 — the browser identity change is behavioral.** `checkouts.json` is keyed
by client id, so changing `browser` → `browser:<nonce>` gives an existing user's
browser a *fresh* per-client checkout the first time. Verify the first-run path
(new client id, no checkout row) lands on the default branch cleanly and that
the branch menu still shows the right state; verify the lock indicator renders
a label, not a raw nonce.

**R7 — the claim guard's install order.** `tools_versioning` replaces
`write_guard` after `tools_comments` loads. Verify with a test that the wrapper
survives a full `build_registry(service)` (i.e. that the lazy install is
actually reached) **and** that `checks.py`'s `store.write_guard = None` for the
ephemeral service still results in no guard at all — a claim check reaching a
CI run's throwaway worktree would be a PRD-004-class bug.

**R8 — anchor resolution cost.** `list_comments` on a project with many threads
must not become a rebuild. Add a test that asserts zero kernel calls on a list
of threads whose parts are unbuilt (they must come back `unverified`).

---

## Gotchas this feature adds to AGENTS.md

To be appended as a "Review-thread gotchas (PRD-008)" section in the docs slice:

- **Threads live at `.history/agentcad/comments/`, are canonical and
  branch-free, and are never model state.** `project_restore` cannot rewind
  them, every branch sees the same list, and no merge ever touches them.
- **An anchor is immutable; its status is computed on every read.** `ok`,
  `moved`, `orphaned` and `unverified` are four different facts. `unverified`
  means *we did not look* (unbuilt part, no git, frozen packet) and must never
  be rendered as "fine".
- **Orphan, never mis-pin.** An ambiguous face match is an orphan. A
  low-confidence line remap is an orphan. Loosening a tolerance to make a pin
  appear is the one change this feature must never take.
- **`metrics.n_faces` is deduped; the `<key>.faces.u32` sidecar is the
  authority for face-index validation.**
- **Face signatures are derived in the server from `.acm` + `.faces.u32` — no
  kernel call, no rebuild.** `bbox_uvw` is what makes a scaling parameter
  change survivable; absolute centroids do not.
- **Claims are human-vs-human only, and never apply to a client holding the
  turn.** The precedence order is turn → own-turn bypass → claim → proceed.
  Agents get no claim tools by design.
- **The part dimension reaches `write_guard` through
  `locks.write_scope`, not through the guard's signature** — every existing
  guard keeps working byte-identically, and only `write_script` /
  `update_part_entry` are claim-covered. Whole-manifest writes are turn-locked
  only, on purpose.
- **The claim guard is installed lazily and from `routes_presence`**, because
  `tools_versioning` (`v`) replaces `write_guard` after `tools_comments` (`c`)
  loads. Same trap, same fix as `ProposalManager`'s branch-delete guard.
- **Presence is an HTTP heartbeat, not a client→server WebSocket.** `/ws` is in
  `app.py` (a core), carries no client identity, and its host guard is HTTP
  middleware. The heartbeat *response* is the mechanism; `presence_changed` is
  an optimization.
- **`notification` events are broadcast to every `/ws` client and filtered
  client-side.** That is honest on a single-node, unauthenticated,
  127.0.0.1-only server, and it is what PRD-005 fixes.
- **Per-user undo did not re-key the undo stacks.** `scope: "any"` is the
  default and is byte-identical to today, because a human pressing Cmd+Z to
  take back the agent's edit is the product's flagship loop.
- **`comments`, never `threads`** — `agentcad/toolkit/threads.py` is ISO screw
  threads and `tests/test_threads.py` is its test module.

---

## Acceptance criteria → design

| AC | Where it is won |
|---|---|
| AC1 roadmap loop, two browsers, zero console errors | Decisions 10 (distinct browser identities), 12 (events), 17 (tools), 19 (pins, panel) — plan slices 3, 6, 8, 11 |
| AC2 face anchor survives a param tweak; orphans when cut away | Decision 5 (`bbox_uvw`, ambiguity margin, tie-break) — slice 2, tolerances set by risk R1's spike |
| AC3 script_range tracks an insert above | Decision 6 tier 1 (exact snippet search) — slice 2 |
| AC4 `@chat:main` delivers a `notification` + unread record | Decision 11 — slice 5, WS test with `extra_allowed_hosts={"testserver"}` |
| AC5 claims: conflict, override, untouched other part | Decision 14 — slice 7 |
| AC6 turn precedence; existing lock suite unmodified | Decision 14 rules 1–2 — slice 7 (`tests/test_locks.py` is the gate) |
| AC7 per-user undo; structured conflict on overlap | Decision 16 — slice 10, gated by risk R4's spike |
| AC8 threads survive `project_restore` | Decision 2 (GIT_DIR placement) — true by construction; asserted in slice 1 |
| AC9 attachments outside `exports/` rejected; suite green | Decision 17 — slice 3 |
