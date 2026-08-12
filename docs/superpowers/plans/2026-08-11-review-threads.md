# Anchored review threads and presence — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to work through this plan slice by slice.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ship
[PRD-008](../../prd/in-progress/PRD-008-review-threads-presence.md) — comments
that anchor to a face, a param, a script line range, an assembly instance or a
proposal diff hunk and either survive an edit or say honestly that they did
not; threads with replies, resolve/reopen, mentions and notifications; agents
as first-class participants through five tools; live presence; per-part soft
claims for humans alongside the existing agent turn lock; and author-aware
undo — per
[the design spec](../specs/2026-08-11-review-threads-design.md).

**Architecture (one paragraph):** `agentcad/core/comments.py` holds a
`CommentStore` (files only, modelled line for line on `ProposalStore`: atomic
writes, an append-only `audit.jsonl`, a rebuildable `index.json`, a persisted
`next_id` high-water mark) and a `CommentManager` (lifecycle, mentions,
events), living at `.history/agentcad/comments/` — canonical, inside GIT_DIR,
branch-free, structurally beyond `project_restore`'s reach.
`agentcad/core/anchors.py` validates an anchor on create and **resolves it at
read time** into one of four states (`ok`/`moved`/`orphaned`/`unverified`),
deriving face signatures in the server process from the `.acm` mesh plus the
`<key>.faces.u32` sidecar — **no kernel call and no rebuild** — and remapping
script line ranges with an exact snippet search backed by a `difflib` line map
over the blob at the anchor's stored head. `agentcad/core/presence.py` is an
in-memory, TTL'd registry fed by an HTTP heartbeat (not a client→server
WebSocket: `/ws` is in `app.py`, carries no client identity, and its host guard
is HTTP middleware). `core/locks.py` gains a `ClaimRegistry` plus two
contextvars, so the part dimension reaches `ProjectStore.write_guard` **without
changing that seam's signature**. Surfaces are packs: `tools_comments.py`,
`routes_comments.py`, `routes_presence.py`, `frontend/js/comments.js`,
`frontend/js/presence.js`.

**Tech stack:** Python 3.12 stdlib (`json`, `difflib`, `re`, `threading`,
`contextvars`, `hashlib`) + NumPy (already a transitive dependency, used only
to read `.acm`/`.faces.u32`) / FastAPI route packs / pytest with the
session-scoped `kernel` fixture / CodeMirror 5.65.21 gutters and Three.js
0.185.1, both already vendored. **No new runtime dependency. No new vendored
frontend library** — in particular no `CSS2DRenderer`; pins are HTML overlays
projected with `camera.project()`.

---

## Global constraints (encode these in every slice)

- **Only `agentcad/kernel/` may import `OCP`/build123d.** This plan adds
  **zero** kernel files and **zero** kernel handlers. `core/anchors.py` reads
  `.acm` through `agentcad.kernel.acm` (which states in its own docstring that
  it has no OCP dependency, and which `core/packet.py`, `core/service.py` and
  `core/tools_vision.py` already import). Assert it: import
  `agentcad.core.anchors` in a fresh interpreter and assert
  `"OCP" not in sys.modules` (the PRD-003/PRD-004 pattern).
- **Do not edit `worker.py`, `tools.py`, `app.py` or `service.py`.** New
  capability arrives as tool packs (`core/tools_comments.py`) and route packs
  (`server/routes_comments.py`, `server/routes_presence.py`), discovered by the
  existing `tools._load_tool_packs` / `app._mount_route_packs` scans.
- **Do not edit `proposals.py`, `packet.py`, `merge.py`, `branches.py`,
  `manifest_merge.py`, `specs.py` or `checks.py`.** PRD-001 through PRD-004 are
  finished and reviewed; this feature *consumes* them. In particular the
  packet's `hunks` and the diff rows' `data-part`/`data-hunk`/`data-line` are
  used exactly as they are.
- **Exactly three additive changes to existing non-test Python files** in the
  whole plan:
  1. `core/locks.py` — `ClaimRegistry`, `write_part_var`/`override_var` and
     their context managers, `current_write_part()`. Purely additive;
     `TurnLock` is not modified.
  2. `core/project.py` — a `with locks.write_scope(part_id):` wrapper around
     `write_script` and `update_part_entry`. **`write_guard`'s signature does
     not change**, so `service.py:107`'s lambda,
     `tools_versioning.install_write_guard` and every guard installed in a test
     keep working byte-identically.
  3. `core/history.py` — `snapshot()` appends a `Client:` trailer, `log()`
     parses it into `author`, and a new `revert()` method (slice 10 only).

  Plus two additive frontend edits to existing files (`api.js`'s default
  header, `inspector.js`'s tab map) and the new-module wiring in `main.js`.
  **Any other diff to an existing non-test file is a design bug — stop and
  re-read the design spec.**
- **The module is `comments`, never `threads`.** `agentcad/toolkit/threads.py`
  is ISO screw threads and `tests/test_threads.py` is its test module. The word
  "thread" survives only in payloads and tool names (`resolve_thread`,
  `comment_changed {thread}`), which FR7 freezes.
- **Tool-pack load order.** Packs load alphabetically:
  `tools_comments` (`c`) loads **before** `tools_proposals` (`p`),
  `tools_run_checks` (`r`), `tools_specs` (`s`) and `tools_versioning` (`v`).
  Therefore `service.proposals`, `service.branches` and `service.merges` are
  read **inside methods, never captured in `register()` or `__init__`**, and
  the pack must assign nothing that a later pack overwrites (it adds **no gate
  provider** — `tools_proposals` sets `service.gate_providers = []`
  unconditionally, and threads do not gate merges anyway).
- **`bus.on_publish` is a single slot already claimed by
  `service._snapshot_on_event`.** Never assign it. New events go through
  `service.bus.publish` only, and none of them is `project_changed` — a comment
  is not a model change and must not trigger a history snapshot.
- Storage paths come from `store.canonical_path_of` — **never `path_of`**.
  Atomic writes (`ProjectStore._atomic_write`) for `thread.json`, `index.json`,
  `next_id` and the `.facesig.json` sidecar; `audit.jsonl` and
  `notifications.jsonl` are **appended**, never rewritten.
- Every git call goes through `history._run` / `_run_bytes` (hermetic env,
  10 s timeout) — **never a raw `subprocess`**.
- Structured errors only: `NotFoundError` / `ValidationError` / `ConflictError`
  → 404/422/409. **No new error type.** An orphaned or unverified anchor is
  *payload*, never an exception.
- Route packs **whitelist request-body keys**; never `**body`.
- **All 1183 tests must keep passing** (baseline: `1183 passed, 1 skipped`,
  `make test`, from changelog 0111). Two suites are explicit regression gates
  and must pass **unmodified**: `tests/test_locks.py` (AC6) and the existing
  undo/redo coverage in `tests/test_history.py` (Decision 16).
- **Examples tests run on a copy** — never mutate `examples/` in place.
- FastAPI `TestClient` must pass `base_url="http://127.0.0.1"`; WebSocket tests
  need `create_app(..., extra_allowed_hosts={"testserver"})`.
- Mark broad/process-heavy coverage `slow`; mark OS-sensitive coverage
  `portability` (the git `revert` tests in slice 10 qualify). Do not mark pure
  domain logic.
- **Never `uv sync` / `uv pip install` into the shared venv** from a parallel
  agent — use a scratch venv. **Subagents must not run `git`.**
- **Every UI slice is verified in a real browser** (`run` skill → screenshot →
  zero console errors), not only by tests.
- **Every slice lands with a `docs/changelog/NNNN-<slug>.md` entry staged with
  the change**, written from the real diff. The next number at the time of
  writing is **0112**; recompute it (highest existing + 1) at each slice —
  other work may land in between.

---

## Slice map

| # | Slice | Lands | Depends on |
|---|---|---|---|
| 1 | `core/comments.py` — store + lifecycle | threads, replies, resolve/reopen, audit, part/param/instance anchors, AC8 | — |
| 2 | `core/anchors.py` — face + script_range resolution | AC2, AC3, the four-state resolution | 1 |
| 3 | tool pack + route pack + `comment_changed` | the whole agent surface, AC9 | 1, 2 |
| 4 | `proposal_hunk` anchors | the PRD's open question, answered | 3 |
| 5 | mentions + notifications | AC4 | 3 |
| 6 | presence: registry, heartbeat, browser identity | FR9, R6 | 3 |
| 7 | claims at the `write_guard` seam | AC5, AC6 | 6 |
| 8 | UI: Threads panel, face pins, gutter, param badges | the human path | 3, 4 |
| 9 | UI: presence avatars, tree chips, notifications drawer, conflict/override dialog | FR10, the claim UX | 5, 6, 7, 8 |
| 10 | snapshot authorship + author-aware undo + `revert` | FR13, FR14, AC7 | — (independent) |
| 11 | docs, acceptance tests, PRD close-out | AC1, the full-suite citation | all |

Slices 1–3 are the spine; 4–7 are independent of each other; 8–9 are the UI;
10 is independent of everything else and may be worked in parallel by a second
agent (it touches only `history.py`, `tools_undo.py`, `tools_history.py`).

---

## Slice 1 — `core/comments.py`: the store and the lifecycle

**Why first:** it is the contract every other slice is written against, it
needs no kernel and no git, and AC8 falls out of the storage decision alone.

### Files
- `agentcad/core/comments.py` (new)
- `tests/test_comments.py` (new)

### The shapes (copy from the design spec, Decisions 2, 3, 9)

```python
STATES = ("open", "resolved")
ANCHOR_KINDS = ("part", "face", "param", "script_range", "instance",
                "proposal_hunk")
ACTIONS = ("created", "replied", "resolved", "reopened",
           "comment_edited", "comment_deleted", "mentioned")
MAX_ATTACHMENTS = 8
MAX_BODY_BYTES = 16 * 1024
MAX_SNIPPET_LINES, MAX_SNIPPET_BYTES = 40, 4096
```

Directory layout, byte-for-byte the `ProposalStore` idiom:

```
.history/agentcad/comments/
  next_id · index.json · notifications.jsonl · <id>/thread.json · <id>/audit.jsonl
```

### Tasks

- [ ] **Task 1 — module docstring stating the four rules.** Where the state
  lives and why (canonical, GIT_DIR, branch-free, `project_restore`-proof);
  which writes are atomic and which are appends; that ids come from a persisted
  high-water mark; and that `actor_kind` is imported from `proposals.py`, not
  re-implemented. Cite PRD-002 as the template.
- [ ] **Task 2 — `CommentStore`.** `dir_of` (via `canonical_path_of`),
  `_thread_dir`, `_valid_id` (whitelist `^[1-9][0-9]{0,17}$` before it touches
  the filesystem — ids arrive from REST path segments), `load`, `save`, `list`,
  `allocate_id`, `_high_water`/`_write_high_water`, `_read_index`/`_write_index`
  (a missing or corrupt index is rebuilt, never an error), `append_audit`,
  `audit`. One `threading.RLock`. **Copy `ProposalStore`'s structure
  deliberately; do not invent a second idiom.**
- [ ] **Task 3 — `CommentManager.create(proj, anchor, body, attachments)`.**
  Validates the anchor (Task 5), the body (non-empty, ≤ `MAX_BODY_BYTES`,
  markdown subset is *not* parsed or sanitized server-side — it is rendered as
  text with links/code by the client) and the attachments (Task 6); allocates
  an id; writes `thread.json`; appends `created`; returns the post-state
  thread. Records `branch` from `service.branches.current(proj)` when the
  versioning pack is present, else `""` — **read lazily inside the method**.
- [ ] **Task 4 — replies and lifecycle.** `reply(proj, tid, body, attachments)`
  (appends a comment with a per-thread sequential id, bumps `updated`, audits
  `replied`); `resolve` / `reopen` (idempotent, record actor + `actor_kind` +
  ts, audit); `edit_comment` / `delete_comment` (author-only; the root comment
  cannot be deleted; an edit audits `previous_sha256`, a delete leaves
  `deleted: true, body: null`). All under the manager's `RLock`, all returning
  the post-state thread.
- [ ] **Task 5 — cheap anchor validation** (`part`, `param`, `instance` here;
  `face` and `script_range` land in slice 2, `proposal_hunk` in slice 4).
  Unknown part / unknown param / unknown instance is a `validation_error`
  carrying the known set. A `face` anchor in this slice raises
  `NotImplementedError` only inside the module's private table — never
  reachable from a public API before slice 2 (register the kind but return a
  `validation_error` "not yet supported" so no partial surface leaks).
- [ ] **Task 6 — attachment validation (FR8, AC9).** Normalize to an
  `exports/`-relative POSIX path; reject `..`, reject anything not under
  `store.exports_dir(proj)`, `resolve()` **both** sides before comparing
  (macOS `/var` → `/private/var`), require existence at creation, cap at
  `MAX_ATTACHMENTS`. Anything else is a `validation_error`. Read-time missing
  files render as `{path, available: false}`, never an error.
- [ ] **Task 7 — `list(proj, **filters)` and `get(proj, tid)`.** Filters:
  `state`, `kind`, `part_id`, `branch`. Returns `{threads, counts}`. No
  resolution yet — slice 2 adds it behind `resolve_anchors`.

### Tests (`tests/test_comments.py`)
- store round-trip; id monotonicity after a hand-deleted directory (the
  `next_id` file is the only thing that remembers);
- index rebuilt from directories when deleted / when corrupt;
- `audit.jsonl` is append-only: assert byte-prefix stability across three
  mutations;
- root comment cannot be deleted; a non-author edit is refused; a delete leaves
  a tombstone and an audit line;
- attachment validation: inside `exports/` passes; `../../etc/passwd`,
  an absolute path outside, and a symlink pointing outside are all
  `validation_error` (**AC9**);
- **AC8:** create a thread, `project_restore` to an earlier snapshot, assert the
  thread is still listable and unchanged (use the real-history service fixture,
  not `make_test_service`'s snapshot-disabled one);
- a thread is invisible to git: after creation, `git status --porcelain` in the
  project is clean.

### Verification
```
uv run pytest tests/test_comments.py -q
uv run pytest -q -x tests/test_proposals.py tests/test_history.py
```
Cite both counts. Changelog: `NNNN-prd008-comment-store.md`.

---

## Slice 2 — `core/anchors.py`: validation and read-time resolution

**Why here:** it is the honesty core of the feature and it is testable without
any server, tool or UI.

### Files
- `agentcad/core/anchors.py` (new)
- `tests/test_anchors.py` (new), `tests/test_anchors_kernel.py` (new, `slow`)

### Tasks

- [ ] **Task 1 — the resolution vocabulary.**
  `RESOLUTION = ("ok", "moved", "orphaned", "unverified")` with a module
  docstring stating what each means and, explicitly, that `unverified` means
  *we did not look* and must never be rendered as "fine". A `resolve(...)`
  result always carries `status`, and carries `reason` + `hint` whenever the
  status is not `ok` (enforce it in the constructor with a `ValueError`, the
  PRD-003 `make_item` precedent).
- [ ] **Task 2 — `face_table(acm_bytes, face_ids) -> list[dict]`.** Pure NumPy
  over `acm.parse(...)["positions"]`/`["indices"]` and the `u32` sidecar. Per
  face ordinal: `area` (Σ triangle areas), `centroid` (area-weighted), `normal`
  (normalized area-weighted sum of triangle normals), and `bbox_uvw` (the
  centroid mapped into the whole shape's bbox as three fractions in `[0,1]`;
  a degenerate axis maps to `0.5`). Faces with no triangles still consume their
  ordinal (`mesh.py` guarantees this) and come back with `area: 0.0` and
  `present: False`.
- [ ] **Task 3 — `signature_table(service, proj, part)`.** Compute the cache
  key **without building**: `service._cache_key_for(proj, record)`. If
  `<key>.acm` or `<key>.faces.u32` is missing → return `None` (the caller
  answers `unverified` / `part_not_built`). Otherwise memoize in-process by key
  and persist `<key>.facesig.json` beside the mesh with
  `ProjectStore._atomic_write`. **Never call `service.mesh_info` or
  `_ensure_built` here** — both build.
- [ ] **Task 4 — face validation.** `n_faces = max(sidecar) + 1`; assert in a
  comment and in a test that this is *not* `metrics.n_faces` (deduped by
  build123d's `faces()`). Reject `kind == "reference"` parts with a hint. Store
  `{centroid, normal, area_mm2, bbox_uvw, n_faces, mesh_key}`.
- [ ] **Task 5 — the face matcher.** Constants `NORMAL_DOT = 0.985`,
  `AREA_REL = 0.25`, `UVW_DIST = 0.15`, `AMBIGUITY_MARGIN = 0.05`,
  `STICKY_MARGIN = 0.02`, each with a comment naming the measurement that set
  it (Task 9). Fast path: `mesh_key` unchanged → `ok`. Otherwise filter to
  candidates, score, require a unique winner beyond the ambiguity margin, tie-
  break to the stored ordinal within the sticky margin. No candidate or
  ambiguous → `orphaned` with the distinguishing `reason`. **Never return a
  best guess.**
- [ ] **Task 6 — script_range tier 1.** Exact snippet comparison at the stored
  range → `ok`; exact snippet search elsewhere, disambiguated by the stored
  `before`/`after` context → `moved`. Pure string work, no git. **This is where
  AC3 is won.**
- [ ] **Task 7 — script_range tier 2.** `difflib.SequenceMatcher` opcodes
  between the blob at `anchor.head` (`history._run_bytes("cat-file", "blob",
  f"{head}:parts/{part}.py")`) and the current text; map the range; a range
  wholly inside a deleted/replaced block is `orphaned`; below
  `LINE_CONFIDENCE_MIN = 0.6` is `orphaned`. No git / unreachable head →
  `unverified` with the reason, **never `orphaned`**.
- [ ] **Task 8 — `resolve(service, proj, anchor)` dispatch**, including the
  manifest-only kinds and the cross-branch rule (Decision 7): a target absent
  on the reader's branch while the anchor names another branch is
  `unverified` / `other_branch`, not `orphaned`. Every result records
  `against: {branch, head}`.
- [ ] **Task 9 — the tolerance spike (risk R1).** A `slow` test (or a scratch
  script whose numbers are pasted into the constants' comments) that, for each
  bundled example, builds at default params and at a small perturbation, then
  reports per part: faces whose ordinal held, faces the matcher moved, faces it
  orphaned. **Set the five constants from this run and cite it.** If AC2 cannot
  be met on a real example, stop and report — do not loosen tolerances.

### Tests
- `tests/test_anchors.py` (no kernel): the four-state constructor invariant;
  tier-1 remap across an inserted line (**AC3**), across an unchanged file,
  across a duplicated snippet (context disambiguation), across a deleted range;
  tier-2 mapping with a fabricated old/new pair; `unverified` when git is
  absent; `face_table` on a hand-built synthetic ACM buffer (a unit cube:
  6 faces, 12 triangles) — areas, normals and `bbox_uvw` asserted exactly.
- `tests/test_anchors_kernel.py` (`slow`, session `kernel` fixture): build a
  bundled example, anchor a face, tweak a non-topological param, assert
  `ok`/`moved` with the *right* face (verified independently by normal +
  centroid, the `test_facemod.py` idiom); then change a param that removes the
  face and assert `orphaned` (**AC2**); assert `max(sidecar)+1` and
  `metrics.n_faces` on a multi-solid part and document the divergence (**R2**);
  assert `face_table` agrees with `face_info` on a planar face within tolerance
  (**R3**).
- import-purity test: `"OCP" not in sys.modules` after importing
  `agentcad.core.anchors` in a fresh interpreter.
- **R8:** resolving anchors on unbuilt parts issues zero kernel calls (spy on
  the pool) and returns `unverified`.

### Verification
```
uv run pytest tests/test_anchors.py -q
uv run pytest tests/test_anchors_kernel.py -q          # slow, needs the kernel
```
Cite the spike's numbers in the changelog. Changelog:
`NNNN-prd008-anchor-resolution.md`.

---

## Slice 3 — the tool pack, the route pack and `comment_changed`

### Files
- `agentcad/core/tools_comments.py` (new)
- `agentcad/server/routes_comments.py` (new)
- `tests/test_comments_api.py` (new)
- `docs/agent-api.md` (tool count 65→70 / 68→73, a new "### Review threads"
  section)

### Tasks

- [ ] **Task 1 — the pack.** `register(registry, service)` installs
  `service.comments = CommentManager(service)` and registers the four thread
  tools. **Reads `service.proposals` / `service.branches` only inside methods**
  (load order: `c` before `p` and `v`). Adds **no** gate provider. Registers
  unconditionally — comments need no git (only tier-2 remap and
  `proposal_hunk` do, and they degrade by saying so).
- [ ] **Task 2 — `list_comments`.** Args `{project, part_id?, state?, kind?,
  branch?, anchor_status?, resolve_anchors?=true}`; returns
  `{threads, counts: {open, resolved, orphaned}}`. The description must state:
  what the four resolution statuses mean; that a face anchor may come back
  `orphaned` and that this is the contract, not a bug; that `actor_kind` is
  bookkeeping, not authentication; that listing never rebuilds a part, so an
  unbuilt part's face anchors are `unverified`.
- [ ] **Task 3 — `add_comment`.** Exactly one of `anchor` / `thread`; both or
  neither is a `validation_error` naming the rule. Returns the post-state
  thread. Description covers the six anchor shapes with a worked example each,
  and the `exports/` attachment rule.
- [ ] **Task 4 — `resolve_thread` / `reopen_thread`.** Idempotent; post-state.
- [ ] **Task 5 — `comment_changed` events**, published from the manager on
  every mutation: `{type, project, thread, state, action, part}`. Never
  `project_changed`.
- [ ] **Task 6 — the route pack.** The eight endpoints from Decision 18.
  Whitelist body keys. Map errors through the existing types only. `GET` with
  `resolve_anchors=false` for the cheapest list.
- [ ] **Task 7 — docs.** `docs/agent-api.md`: header count, a "Review threads"
  section following the existing per-feature section style, and the four
  statuses in the conventions list.

### Tests (`tests/test_comments_api.py`)
- REST round-trip for all eight endpoints with
  `TestClient(base_url="http://127.0.0.1")`;
- unknown project / thread → 404; both `anchor` and `thread` → 422;
  attachment outside `exports/` → 422 (**AC9** at the API layer);
- `comment_changed` arrives on `/ws`
  (`create_app(..., extra_allowed_hosts={"testserver"})`);
- a mutation does **not** create a history commit (assert `history.head` is
  unchanged) — a comment is not a model change;
- MCP surface: the five tools appear in `GET /api/tools` and round-trip through
  `POST /api/tools/{name}` (extend `tests/test_mcp.py`'s existing count
  assertion, which will need the new number).

### Verification
```
uv run pytest tests/test_comments_api.py tests/test_tools.py tests/test_mcp.py -q
uv run agentcad serve  &  curl -s localhost:8630/api/tools | jq '.tools | length'
```
Changelog: `NNNN-prd008-comment-tools-routes.md`.

---

## Slice 4 — `proposal_hunk` anchors

### Files
- `agentcad/core/anchors.py` (extend), `agentcad/core/comments.py` (validation
  table), `tests/test_comments_proposals.py` (new)

### Tasks
- [ ] **Task 1 — validation** against the *persisted* `packet.json` only:
  proposal exists, `file` is a key of the packet's script diffs, `0 <= hunk <
  len(hunks)`. Store `{proposal, file, hunk, hunk_header, generation, head}`.
  **Never call `service.packets.packet(...)`** — it can build geometry and move
  proposal state. A missing packet is a `validation_error` telling the caller
  to open the packet first; a truncated diff (`unified is None`) is a
  `validation_error` naming the truncation.
- [ ] **Task 2 — resolution** per the design spec's table: same generation →
  `ok`; new generation with a unique byte-identical `header` → `moved`; header
  absent or non-unique → `orphaned`/`hunk_regenerated`; frozen packet →
  `unverified`/`packet_frozen`; packet or file gone → `orphaned`.
- [ ] **Task 3 — `list_comments {kind: "proposal_hunk", proposal: id}`**
  filter so the proposals UI can fetch exactly its own threads in one call.

### Tests
- create a thread on a hunk, regenerate the packet with an unrelated change,
  assert `moved` to the same header; regenerate with that hunk rewritten,
  assert `orphaned`; merge the proposal (freezing the packet) and assert
  `unverified` / `packet_frozen`;
- the resolution path issues no kernel call and does not change the proposal's
  `packet.generated` timestamp.

### Verification
```
uv run pytest tests/test_comments_proposals.py tests/test_packet.py tests/test_proposals.py -q
```
Changelog: `NNNN-prd008-hunk-anchors.md`.

---

## Slice 5 — mentions and notifications (AC4)

### Files
- `agentcad/core/comments.py` (extend), `agentcad/core/tools_comments.py`
  (+`list_notifications`), `agentcad/server/routes_comments.py` (+2 endpoints),
  `tests/test_comments_notifications.py` (new)

### Tasks
- [ ] **Task 1 — the mention regex and the plausibility filter.**
  `(?<![\w@])@([A-Za-z0-9_.:-]{1,64})`, then keep only ids that are `browser`,
  `browser:*`, `chat`, `chat:<session>` (validated with
  `agent.chat.SESSION_ID_RE` — import it, do not re-write it) or currently
  present. Everything else stays plain text and delivers nothing. Self-mentions
  do not notify.
- [ ] **Task 2 — `notifications.jsonl`.** Append-only, two line kinds
  (`mention`, `read`); unread = mention seqs for an identity minus every seq
  named by a later `read` line for that identity. No per-identity file (an
  identity is an unvalidated header — a filename derived from it is a
  traversal surface).
- [ ] **Task 3 — the `notification` event** `{to, project, thread, comment,
  from, ts}`, published per mention. Document in the tool description **and**
  in `docs/agent-api.md` that the bus is a broadcast and clients filter on
  `to`; per-principal delivery is PRD-005.
- [ ] **Task 4 — `list_notifications {project?, unread?}`** returning
  `{notifications, unread}` for the **calling identity only**; and
  `POST /api/projects/{p}/notifications/read {ids?}` (omit `ids` = mark all).
  Marking read is deliberately a route, not a tool.

### Tests
- **AC4:** a browser client posts a comment containing `@chat:main`; a WS test
  client (`extra_allowed_hosts={"testserver"}`) receives the `notification`
  event, and `list_notifications` under `X-Agent-Id: chat:main` returns one
  unread record; after `POST …/read` it returns zero;
- `@nobody` and `@todo` create no notification and remain in the body;
- a self-mention creates none;
- the log is append-only across a read + a new mention.

### Verification
```
uv run pytest tests/test_comments_notifications.py -q
```
Changelog: `NNNN-prd008-mentions-notifications.md`.

---

## Slice 6 — presence: registry, heartbeat, per-browser identity

### Files
- `agentcad/core/presence.py` (new), `agentcad/server/routes_presence.py` (new)
- `frontend/js/api.js` (the default `X-Agent-Id` header), `frontend/js/presence.js`
  (new), `frontend/js/main.js` (wire the heartbeat + the `presence_changed` case)
- `tests/test_presence.py` (new)

### Tasks
- [ ] **Task 1 — `PresenceRegistry`.** In-memory dict keyed
  `(lock_key, client_id)` → `{kind, label, focus, since, seen}`; TTL
  `PRESENCE_TTL_S = 45`; expiry computed lazily on read; **no background
  thread**. `touch()`, `leave()`, `roster()`. Never persisted.
- [ ] **Task 2 — the route pack.** `POST /api/projects/{proj}/presence`
  `{part_id?, surface?, label?, claim?, leave?}` → `{you, clients, claims,
  ttl_s}`; `GET` the same without registering. `surface` ∈ `viewport | editor |
  inspector | proposals`, anything else is a `validation_error`. Per-identity
  token bucket (1/s, burst 5) → over-rate returns the roster with
  `throttled: true`, **never an error**. Publish `presence_changed` only when
  the roster actually differs.
- [ ] **Task 3 — the browser identity (risk R6).** `api.js` generates
  `browser:<8 hex>` once, keeps it in `localStorage["agentcad.client_id"]`, and
  sends it as `X-Agent-Id` on every request (including the hand-rolled
  `fetch` helpers — `getMesh`, `getMeshFaces`, `getDiffMesh`, `uploadImport`
  — which do not go through `request()`). Verify the first-run path: a fresh
  client id has no `checkouts.json` row and must land on the default branch
  cleanly.
- [ ] **Task 4 — `presence.js`.** Heartbeat every 15 s (and immediately on
  project/part/branch/tab-focus change), `{leave: true}` via
  `navigator.sendBeacon` on `pagehide`, state into `state.presence`, and an
  avatar strip inserted before `#conn-dot` with `renderLockIndicator`'s exact
  lazy-create pattern (no `index.html`/CSS churn). Colors from `tree.js`'s
  exported `INSTANCE_PALETTE`; label, not raw nonce, in the tooltip.
- [ ] **Task 5 — `main.js`**: one new `case "presence_changed":` in
  `handleEvent`, guarded by `ev.project !== state.projectName` like every other
  case, plus `presence.init()`.

### Tests (`tests/test_presence.py`)
- roster join/leave/expiry with a frozen clock; TTL expiry is lazy;
- `presence_changed` fires on join and on focus change but **not** on a
  no-op heartbeat;
- the rate limiter returns `throttled: true`, HTTP 200;
- a bad `surface` is 422;
- presence is empty after a fresh registry (never persisted).

### Verification
```
uv run pytest tests/test_presence.py -q
```
Then the `run` skill: two browsers (one normal, one incognito) on the same
project → both avatars appear in both toolbars within 15 s, disappear within
45 s of closing one. Screenshot, zero console errors.
Changelog: `NNNN-prd008-presence.md`.

---

## Slice 7 — claims at the `write_guard` seam (AC5, AC6)

### Files
- `agentcad/core/locks.py` (additive), `agentcad/core/project.py` (two
  `with locks.write_scope(...)` wrappers), `agentcad/core/tools_comments.py`
  or a small `install_claim_guard` in `core/presence.py`,
  `agentcad/server/routes_presence.py` (+ the override route, + the lazy
  install), `tests/test_claims.py` (new)

### Tasks
- [ ] **Task 1 — `ClaimRegistry` in `locks.py`.** `TurnLock`'s exact shape: a
  `threading.Lock`, a dict keyed `(lock_key, part)`, wall-clock TTL
  (`CLAIM_TTL_S = 90`), raise-never-block. `acquire`, `release`, `get`, `all`,
  `check(key, part, client_id, *, override=False)`. **`TurnLock` itself is not
  modified.**
- [ ] **Task 2 — `write_scope` / `claim_override` / `current_write_part`.**
  Two `ContextVar`s and two context managers, with tokens reset in `finally`.
- [ ] **Task 3 — `project.py`**: wrap `write_script` and `update_part_entry`
  in `with locks.write_scope(part_id):`. Nothing else in that file changes;
  `write_guard`'s signature does not change.
- [ ] **Task 4 — `install_claim_guard(service)`.** Wrap the existing guard,
  calling the previous one **first** (so `ensure_checkout` + the turn check keep
  their order), idempotent by function attribute (`_claims_installed`).
  Precedence exactly as the design spec's table: turn-held-by-other → the
  existing `ConflictError` unchanged; caller holds the turn → no claim check;
  part claimed by a different client **and both are `human`** → `ConflictError`
  with `{claim: {...}, overridable: true}`; otherwise proceed and refresh the
  caller's claim.
- [ ] **Task 5 — lazy installation.** `ensure_claim_guard()` at the top of
  every claims entry point **and** unconditionally from
  `routes_presence.build_router` (route packs mount after every tool pack, and
  `tools_versioning` (`v`) replaces `write_guard` after `tools_comments` (`c`)
  loads — this is the whole reason for the lazy pattern; `ProposalManager`'s
  branch-delete guard is the precedent).
- [ ] **Task 6 — the override.** `POST /api/projects/{proj}/claims/override
  {part}` arms a single-use, 30 s override for `(key, part, caller)` and
  publishes `claim_changed` with `overridden_by`. Library and tool callers use
  `with locks.claim_override():` instead. Both paths are consulted by
  `ClaimRegistry.check`.
- [ ] **Task 7 — claim acquisition.** A claim is taken by (a) a heartbeat with
  `claim: true` (the UI sends it when the editor buffer is dirty or a param
  control is being dragged — *viewing* never claims) and (b) any successful
  part-scoped write. Released on `leave`, on TTL, or on an override.

### Tests (`tests/test_claims.py`)
- **AC5:** `browser:a` claims part X; `browser:b`'s `PUT …/parts/X` → 409 with
  `details.claim.holder == "browser:a"` and `overridable: true`; after arming
  the override the retry lands and `claim_changed` fires with `overridden_by`;
  `browser:b`'s write to part **Y** is untouched throughout;
- an **agent** (`X-Agent-Id: bot`) writing to a human-claimed part proceeds —
  claims are human-vs-human only;
- a client holding the project turn is never claim-checked (FR12);
- whole-manifest writes (`add_part`, assembly) are not claim-checked;
- the guard survives a full `build_registry(service)` (**R7**) and
  `checks.py`'s ephemeral service still ends with `write_guard is None`
  (**R7**).
- **AC6 regression gate:** `tests/test_locks.py` passes **unmodified**.

### Verification
```
uv run pytest tests/test_claims.py -q
uv run pytest tests/test_locks.py tests/test_checks_ref.py tests/test_branches.py -q
git diff --stat tests/test_locks.py     # must be empty
```
Changelog: `NNNN-prd008-part-claims.md`.

---

## Slice 8 — UI: Threads panel, face pins, editor gutter, param badges

### Files
- `frontend/js/comments.js` (new), `frontend/index.html` (one tab + one pane),
  `frontend/js/inspector.js` (the `panes` map + the badge hook),
  `frontend/js/editor.js` (`gutters` option + marker API),
  `frontend/js/viewport.js` (a world→screen helper for pins),
  `frontend/js/main.js` (the `comment_changed` case, the face-card button),
  `frontend/css/*` (pins, badges, panel)

### Tasks
- [ ] **Task 1 — the Threads pane.** A 4th `.tab[data-tab="threads"]` +
  `#pane-threads`; add the entry to `inspector.js`'s `panes` map (it is
  snapshotted at init, so an unregistered pane silently never shows). List
  open/resolved with an anchor breadcrumb and a status chip
  (`ok`/`moved`/`orphaned`/`unverified` — four distinct visual states, with the
  reason in the tooltip), a composer, replies, resolve/reopen.
- [ ] **Task 2 — click-to-focus.** Face → select the part, `highlightFace` the
  *resolved* ordinal (never the stored one), fit the camera; script_range →
  switch to the Code tab and scroll to the resolved range; param → scroll the
  Params pane to `div.param[data-param]`; instance → select it in the tree.
  An `orphaned` or `unverified` anchor is **not** focusable — the row says why.
- [ ] **Task 3 — face pins.** An absolutely-positioned `#pins` overlay, sibling
  of the canvas inside `#viewport` (the `#facecard`/`#hud` pattern — **no
  `CSS2DRenderer` is vendored**). Positions come from `camera.project()` inside
  the existing render loop, only for open face threads on the current part,
  hidden when the anchor is not `ok`/`moved`. A "Comment" button on the
  existing `#facecard` opens the composer with the face anchor pre-filled.
- [ ] **Task 4 — the editor gutter.** Pass
  `gutters: ["CodeMirror-linenumbers", "agentcad-comments"]` and
  `setGutterMarker` on the first line of each open `script_range` thread; click
  opens the thread. Clear and re-apply on `setPart` and after every save.
- [ ] **Task 5 — param badges.** A count badge in `.param-head`, re-applied
  after `buildParamControls` **and** after `syncParamValues` (rows are rebuilt
  only when the part id or `params_spec` changes — a badge must survive both
  paths).
- [ ] **Task 6 — live updates.** `case "comment_changed":` in `handleEvent`,
  project-guarded like every other case; the panel, pins, gutter and badges all
  re-render from `state.comments`, never imperatively poked.

### Verification
`run` skill, real browser: create a face comment; tweak a param and watch the
pin follow (or the row flip to `orphaned` with a reason); create a line comment
and insert a line above it; check the gutter marker moves. Screenshots of each,
**zero console errors**. Then `make test` for the JS-adjacent server tests.
Changelog: `NNNN-prd008-threads-ui.md`.

---

## Slice 9 — UI: avatars, tree chips, notifications drawer, override dialog

### Files
- `frontend/js/presence.js` (extend), `frontend/js/tree.js` (a chip +
  `"presence"` in `onKeys`), `frontend/js/comments.js` (the drawer),
  `frontend/js/proposals.js` (diff-row affordance), `frontend/js/main.js`
  (the conflict dialog), `frontend/css/*`

### Tasks
- [ ] **Task 1 — tree chips.** A per-part dot for a present client or a claim,
  rendered **from state** (`renderParts` clears and rebuilds `#parts-list` on
  every relevant change); add `"presence"` to that module's `onKeys`.
- [ ] **Task 2 — the "editing" chip** naming the claim holder's *label*, in the
  tree row and above the editor.
- [ ] **Task 3 — the conflict/override dialog.** On a 409 whose details carry
  `overridable: true`, show "<label> is editing <part>" with an explicit
  Override button that calls the arming route and retries once. A plain turn-lock
  409 keeps today's message and offers **no** override.
- [ ] **Task 4 — diff-row comments.** A hover affordance on `div.diff-line`
  rows with `data-hunk >= 0` (rows before the first `@@` carry `-1`), opening
  the composer with a `proposal_hunk` anchor from `data-part`/`data-hunk`.
  **Re-applied after every `renderDetail()`** — the pane is rebuilt on each tab
  click and on every `proposal_changed`. Existing threads render as a count
  chip on the hunk header row.
- [ ] **Task 5 — the notifications drawer.** A toolbar button with an unread
  count badge (the `#proposals-btn`/`#proposals-count` pattern), a list, and
  click-to-open-thread; marks read through the route.

### Verification
`run` skill with two browsers: A opens the editor on part X (claim chip appears
in B's tree), B edits X → conflict dialog naming A → Override → the write lands
and A sees `claim_changed`. Comment on a proposal hunk from the Files tab and
see it survive a tab switch. Screenshots, zero console errors.
Changelog: `NNNN-prd008-presence-ui.md`.

---

## Slice 10 — snapshot authorship, author-aware undo, `revert` (FR13/FR14, AC7)

**Independent of slices 1–9** — a second agent may take it in parallel.

### Files
- `agentcad/core/history.py`, `agentcad/core/tools_undo.py`,
  `agentcad/core/tools_history.py`, `agentcad/server/routes_undo.py`,
  `tests/test_undo_authors.py` (new, `portability`)

### Tasks
- [ ] **Task 1 — the `git revert` spike (risk R4). Do this first.** Prove, in a
  throwaway project, that `revert --no-commit <sha>` + commit works through
  `history._run` with GIT_DIR at `.history` and the project as work tree **and**
  in a linked worktree at `.history/trees/<b>/`; that `revert --abort` restores
  cleanly after a conflict; and that the resulting commit does not confuse
  `parent_of` or `UndoCursor`'s `"restore "`-prefix guard. **If it does not
  work, stop and report** — the fallback is Tasks 2–4 only, with AC7's first
  half re-scoped in the PRD with evidence, not a half-working revert.
- [ ] **Task 2 — the `Client:` trailer (FR13, risk R5).** `snapshot()` appends
  `Client: <locks.current_client_id()>` when absent; `log()` parses it into
  `author` (absent → `null`, never `"unknown"`). Git's author/committer stay the
  fixed local identity — an unvalidated header must not look like a
  cryptographic claim. **Grep the suite for exact-message assertions first**
  (`test_history.py`, `test_branches.py`, `test_versioning_api.py`, the
  proposals reconciler's scans).
- [ ] **Task 3 — stack authorship.** `UndoCursor.on_snapshot` records `author`
  from `locks.current_client_id()` (it runs synchronously in the mutating
  call's context). `status()` reports `mine` counts alongside the totals.
- [ ] **Task 4 — `scope`.** `undo`/`redo` take `scope: "any" | "mine"`,
  **default `"any"`, byte-identical to today**. `"mine"` pops the caller's most
  recent entry, skipping others'. When that entry is the branch head, the
  existing restore path runs unchanged.
- [ ] **Task 5 — `ProjectHistory.revert(path, commit)`.** For a `"mine"` undo
  of a non-head commit. Conflict → `revert --abort`, the entry stays on the
  stack, and a `ConflictError` with `{commit, reason: "overlapping_changes",
  paths, blocked_by}`. **Never a partial apply.**
- [ ] **Task 6 — the tool/route surface.** `undo`/`redo` gain `scope`;
  `project_history` rows gain `author`. Descriptions state that `"mine"` is
  best-effort over a shared linear history and that an overlapping change is a
  refusal, not a merge.

### Tests (`tests/test_undo_authors.py`, `portability`)
- **AC7:** A edits part X, B edits part Y, A `undo {scope: "mine"}` → only X
  reverts and B's edit stands; then B also edits X and A's `"mine"` undo of the
  X commit returns the structured conflict with `blocked_by` naming B's commit;
- `scope: "any"` (the default) behaves exactly as today — the existing undo
  coverage in `tests/test_history.py` passes **unmodified**;
- the `Client:` trailer round-trips through `log()`; a pre-trailer commit reads
  back `author: null`;
- redo after a revert reverts the revert.

### Verification
```
uv run pytest tests/test_undo_authors.py tests/test_history.py tests/test_branches.py -q
git diff --stat tests/test_history.py    # existing undo cases unmodified
```
Changelog: `NNNN-prd008-author-aware-undo.md`.

---

## Slice 11 — docs, acceptance tests, PRD close-out

### Files
- `tests/test_prd008_acceptance.py` (new)
- `AGENTS.md` (a "Review-thread gotchas (PRD-008)" section — copy the design
  spec's list verbatim), `CLAUDE.md` (one condensed line if warranted)
- `docs/architecture.md`, `docs/agent-api.md`, `docs/user-guide.md`
- `docs/prd/in-progress/PRD-008-…` → `docs/prd/completed/`, `docs/roadmap.md`
  (status + the moved link; note the index currently points at
  `prd/pending/PRD-008-…` and must be corrected)

### Tasks
- [ ] **Task 1 — AC1 end to end** as a scripted test: a browser identity opens
  a face thread; an agent identity `list_comments`, sees the face anchor with
  its signature, edits the script, `render_view`s, replies with the render as
  an attachment, `resolve_thread`s; a second browser identity's WS client
  observes `comment_changed` for each step. The two-browser half is the manual
  `run`-skill verification with a screenshot and a zero-console-errors claim.
- [ ] **Task 2 — one test per remaining AC**, each named for its AC and each
  citing the design decision it exercises (AC2–AC9 already have homes in slices
  1–7; this module re-asserts them at the seam an outside reader would test).
- [ ] **Task 3 — `docs/user-guide.md`:** the Threads panel, pins, the gutter,
  avatars, the claim chip and the Override button, the notifications drawer —
  and one honest paragraph that identity is self-asserted, presence is
  ephemeral, and notifications are visible to anyone on the machine until
  PRD-005.
- [ ] **Task 4 — `docs/architecture.md`:** the `.history/agentcad/comments/`
  layout, the read-time resolution model, the presence/claims precedence table.
- [ ] **Task 5 — `AGENTS.md` gotchas.** The design spec's list, verbatim.
- [ ] **Task 6 — close-out.** Move the PRD, update the roadmap row (status and
  path), and write the final changelog citing the full-suite count.

### Verification
```
make test          # cite the count; expect >= 1183 passed, 1 skipped
uv run pytest tests/test_prd008_acceptance.py -q
```
Plus the `run` skill for the two-browser AC1 half, with screenshots.
Changelog: `NNNN-prd008-completed.md`.

---

## Rollback / landing notes

- Slices 1–5 are additive and inert if unused: no existing code path calls
  them, so they can land behind nothing and be reverted by deleting files plus
  one `docs/agent-api.md` hunk.
- **Slice 6's browser-identity change is the first behavioral one.** It changes
  every browser request's `X-Agent-Id`, which changes the per-client branch
  checkout row and the string shown in the lock indicator. If it needs backing
  out, it is a two-line revert in `api.js`; nothing server-side depends on the
  nonce being present.
- **Slice 7 is the riskiest**: it inserts a wrapper into the one seam every
  persistent write passes through. Its own kill switch is
  `service.claims = None` (the guard wrapper then calls only the previous
  guard). Land it with `tests/test_locks.py` green and unmodified, or do not
  land it.
- Slice 10 touches `history.py`, which every feature depends on. Its two halves
  are separable: the `Client:` trailer can land alone, and `scope`/`revert` can
  be reverted without it.
- If the slice-2 spike (R1) or the slice-10 spike (R4) fails, the honest
  outcome is a smaller documented claim in the PRD, not a looser tolerance or a
  partial revert. Say so in the changelog with the numbers that forced it.
