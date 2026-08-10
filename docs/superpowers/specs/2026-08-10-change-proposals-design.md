# Change proposals & geometric diff — design

**Date:** 2026-08-10 · **Status:** approved for implementation ·
**PRD:** [PRD-002](../../prd/in-progress/PRD-002-change-proposals-geometric-diff.md)
**Builds on:** [PRD-001](../../prd/completed/PRD-001-branching-version-control.md)
(merged in PR #8) and its
[design spec](2026-08-09-branching-version-control-design.md).
**Scope:** the PRD's **MVP** section only (FR1–FR8, FR10–FR15, plus the
head-pinning half of FR9 — see Decision 4). Phase 2/3 items are listed under
"Out of scope" and must not creep in.

## Problem

A change lands the instant a tool call returns. `update_part_script` writes,
rebuilds, publishes `project_changed`, and the snapshot hook commits — done.
PRD-001 added branches, so work can now happen *somewhere else*, but there is
still no object between "an agent edited a branch" and "that edit is on
master": `merge_branch` is a single call with a kernel validation gate and no
human in it. The unit of review is a git commit; the way to review it is to
read the diff by hand or watch the agent live.

That is the gap the competitive analysis scores highest
(`market_research.md`, "The workflow ring" and the gap matrix): Onshape has
branches and versions but merges per-tab with three fixed strategies and "the
from workspace wins", because binary microversion deltas cannot be reviewed;
CoLab raised $72M bolting pinned feedback and an AI reviewer onto files
*outside* the CAD. Our parts are Python and our manifest is JSON, and PRD-001
already gives us real three-way merges — so the review object is not just
possible, it is nearly free. It is also the trust mechanism the whole v4 thesis
rests on: **agents propose, humans decide, the kernel referees.**

What a proposal must add on top of PRD-001, concretely:

1. a durable, attributed object with a lifecycle (draft → open → approved /
   changes_requested → merged / closed) that survives restarts and is never
   rewound by `project_restore`;
2. a **review packet** — script and PARAMS diffs, metric deltas, assembly
   deltas, matched before/after renders, and kernel-computed added/removed
   volumes — cheap enough to regenerate on view;
3. a **gate** in front of `merge_branch`: approvals policy now, spec (PRD-003)
   and CI (PRD-004) statuses when they land, with every override recorded.

## Goals

- G1. Every change is reviewable before it lands: source/target, intent, and an
  auto-generated packet.
- G2. The packet is complete and cheap — under 10 s warm on the rocketry
  example (AC2). Cost scales with the change, not the project.
- G3. The lifecycle is governed and attributed, human vs agent, queryable
  forever, append-only.
- G4. Merge happens only through the gate; a red gate blocks and an override is
  recorded, never silent.
- G5. One packet, two consumers: pixels and prose for humans, structured JSON
  plus real image content for agents.

## Non-goals / out of scope for this spec

- Branch/merge mechanics — PRD-001. **This spec adds nothing to conflict
  semantics**; `proposal_merge` calls `MergeOrchestrator.merge` and passes its
  payloads through verbatim.
- Threaded discussion and pinned comments — PRD-008. This spec *defines the
  anchors* those threads will target (Decision 4, `hunks`) and stores no
  threads.
- Check execution — PRD-004; spec evaluation — PRD-003. This spec ships the
  **gate-provider seam** and nothing that fills it.
- Authentication / multi-tenant principals — PRD-005. Identity here is the
  existing `client_id_var` string; `actor_kind` is derived bookkeeping, not
  authentication.
- Cross-project / registry proposals — PRD-011. Revisions — PRD-015.
- Packet LRU/GC, archived packets surviving branch deletion, spec/CI gate
  rendering, branch protection and richer policy — PRD Phase 2.
- Reviewer-agent playbooks — docs, PRD Phase 3.

---

## Architecture at a glance

```
             tools_proposals.py                 routes_proposals.py
             (proposal_create/list/get/          (/api/projects/{p}/proposals…,
              update/packet/review/merge/         …/packet, …/review, …/merge,
              render)                             …/render/…, …/diff/…)
                        │                                   │
                        └─────────────┬─────────────────────┘
                                      ▼
                        service.proposals ──► ProposalManager      (core/proposals.py)
                        (seam installed by     • state machine + policy
                         the pack)             • append-only audit
                                               • gate evaluation
                                               • ProposalStore (.history/agentcad/proposals/)
                                      │
                    ┌─────────────────┼──────────────────┬────────────────────┐
                    ▼                 ▼                  ▼                    ▼
            PacketBuilder      MergeOrchestrator    BranchManager        AgentCADService
         (core/packet.py)       (core/merge.py)    (core/branches.py)     (core/service.py)
          • git diff refs        UNCHANGED          • tree_of(branch)      • _ensure_built
          • manifest deltas                         • pinned(tree)         • get_metrics
          • metrics per side                                               • get_assembly
          • matched renders ──► core/render.py (gains an explicit frame)   • ensure_mesh
          • geom diff ────────► kernel "geom_diff" (handlers/diff.py)
```

Two new core modules (`proposals.py`, `packet.py`), one new kernel handler pack
(`kernel/handlers/diff.py`), one tool pack, one route pack, one frontend
module. Edits to existing files are confined to **two additive signatures**:
`core/render.py`'s `render_acm(..., frame=None)` (Decision 5) and
`frontend/js/viewport.js`'s overlay pair (Decision 9). **`worker.py`,
`tools.py`, `app.py`, `service.py`, `merge.py`, `branches.py`,
`manifest_merge.py` and `history.py` are not touched at all.** The one seam
that needs rewiring — refusing `branch_delete` while an open proposal names the
branch — is installed by the tool pack the way `tools_versioning.install_write_guard`
installs the store guard (Decision 7).

---

## Decision 1 — proposal state lives in the PRD-001 sidecar, not in the project

**Chosen:** `<project>/.history/agentcad/proposals/`, next to `config.json`,
`checkouts.json`, `tags.json` and `merge.json`.

```
<project>/.history/agentcad/proposals/
  index.json                  {"next_id": 4, "proposals": [ <summary>, … ]}
  policy.json                 {"approvals_required": 1, "self_approve": false}   (optional)
  3/
    proposal.json             the object (FR1)
    audit.jsonl               append-only, one JSON object per line (FR14)
    packet.json               the generated packet (Decision 4)
    renders/<part>.<side>.<view>.png
    diff/<part>.added.acm  ·  diff/<part>.removed.acm
```

**Why not the PRD's `<project>/.proposals/`.** Three reasons, in order of
weight:

1. **A proposal is not per-branch state.** `<project>/.proposals/` sits in the
   *default branch's working tree*. Every other branch has its own working tree
   at `.history/trees/<branch>/` (PRD-001 Decision 1), so each would grow its
   own `.proposals/` and a proposal created on `feat` would be invisible from
   `master`. A proposal is *about* a branch pair and must be visible from every
   branch — exactly the argument that made `.cache/` canonical and shared
   (PRD-001 Decision 5). `.history/agentcad/` is canonical by construction.
2. **FR3 becomes structural instead of a rule.** `.history/` is inside GIT_DIR,
   so proposal state is not in any tree, cannot be committed, and
   `project_restore` — which is `git checkout <commit> -- .` in the working
   tree — cannot possibly rewind it. The PRD's plan (add `.proposals/` to
   `.history/info/exclude`) works, but relies on `_refresh_excludes` running
   for every existing project and on nobody ever passing `--force`.
3. **No new exclude line, no migration.** `_refresh_excludes`
   (`history.py:206`) appends managed lines and is guarded by
   `if not git_dir.is_dir(): return`, so only the main tree writes it; adding a
   fifth managed line is a change to a file every project already has. Reusing
   `.history/agentcad/` needs zero migration.

**PRD divergence to fold back:** `<project>/.proposals/` →
`<project>/.history/agentcad/proposals/`; the `info/exclude` addition is
dropped as unnecessary.

**Durability and concurrency.** `ProposalStore` holds one `threading.RLock`
(the `MergeOrchestrator` precedent) and:

- writes `proposal.json`, `packet.json`, `index.json` and `policy.json` with
  `ProjectStore._atomic_write` (temp + `os.replace`) — `*.tmp` is already a
  managed exclude line;
- **appends** to `audit.jsonl` with `open(path, "a", encoding="utf-8")` +
  `flush()`. This is the one file that is deliberately *not* atomically
  replaced: FR14 makes it append-only, and a read-modify-atomic-write cycle
  both loses the append-only property and can truncate the log on a crash. The
  RLock serializes appends within the process; across processes, an `O_APPEND`
  write of a single short line is atomic on the platforms we support.
- rebuilds `index.json` from the per-proposal directories when it is missing or
  unparseable, so the index is a cache, never the source of truth.

**Identity.** `id` is a decimal string allocated from `index.json`'s `next_id`
under the lock and **never reused** (`next_id` only increments, even if a
proposal directory is removed by hand). The UI renders it as `#3`. Keeping it a
string means PRD-005 can switch to server-issued ids without a schema change.

**Path resolution.** Always `store.canonical_path_of(proj) / ".history" /
"agentcad" / "proposals"` — never `path_of`, which follows the caller's branch.

## Decision 2 — the object and its lifecycle

`proposal.json` (FR1, plus the fields the packet and gates need):

```json
{
  "id": "3",
  "project": "rocketry",
  "source": "nozzle-thinner",
  "target": "master",
  "title": "Thin the nozzle wall to 1.6 mm",
  "description": "Mass budget; wall still above the 1.2 mm minimum.",
  "author": "chat:main",
  "author_kind": "agent",
  "state": "open",
  "created": "2026-08-10T09:04:11",
  "updated": "2026-08-10T09:12:04",
  "reviews": [{"actor": "browser", "actor_kind": "human",
               "verdict": "approve", "summary": "…",
               "ts": "…", "source_head": "<sha>"}],
  "merge": null,
  "packet": {"generated": "…", "source_head": "<sha>",
             "target_head": "<sha>", "ok": true}
}
```

`merge` becomes `{"commit": "<sha>", "parents": [...], "ts": "…",
"allow_invalid": false, "validation": <PRD-001 report>}` at merge time.

**States and transitions.** The full table; anything not in it is a
`validation_error` naming the current state and the allowed set.

| from | to | trigger |
|---|---|---|
| `draft` | `open` | `proposal_update {state: "open"}` |
| `draft` | `closed` | `proposal_update {state: "closed"}` |
| `open` | `approved` | `proposal_review {verdict: "approve"}` |
| `open` | `changes_requested` | `proposal_review {verdict: "request_changes"}` |
| `open` | `closed` · `merged` | update · `proposal_merge` |
| `approved` | `changes_requested` · `open` · `closed` · `merged` | review · update · update · merge |
| `changes_requested` | `open` | `proposal_update {state: "open"}` (author re-requests review) |
| `changes_requested` | `approved` · `closed` · `merged` | review · update · merge |
| `closed` | `open` | `proposal_update {state: "open"}` (reopen, FR1) |
| `merged` | — | terminal |

`proposal_review {verdict: "comment"}` records a review and an audit entry
without changing state — the honest MVP shape for "leave a verdict a human can
audit" when the verdict is neither approve nor block, and the row PRD-008 will
hang a thread off.

`proposal_merge` from `draft` is a `conflict_error` ("a draft proposal cannot
be merged; open it first"), not a `validation_error`: the request is
well-formed and refused by workflow state.

**One open proposal per (source, target)** (FR2). Enforced in
`ProposalManager.create` under the lock by scanning the index for a proposal in
a non-terminal state (`draft`, `open`, `approved`, `changes_requested`) with
the same pair ⇒ `ConflictError` with `details.existing_id`. `merged` and
`closed` proposals do not block a new one.

**Target default.** `target` defaults to `branches.default_branch(proj)`, **not
to the caller's current branch**. A proposal is a durable object read by other
clients; making its target depend on who happened to create it would be a
lottery. (`merge_branch` defaults to the caller's branch because it acts
immediately, for that caller.) `source` is required and must not equal
`target`. Both are validated through `history.resolve_branch` — a tag must
never answer for a branch (PRD-001 X1).

**Branch deletion** (FR2). `branch_delete` of a branch named as `source` or
`target` by a non-terminal proposal is a `conflict_error` naming the proposal
id. Implemented by the tool pack wrapping `service.branches.delete` at
registration time — the `install_write_guard` precedent — so `branches.py` is
untouched and the guard applies to the tool, the REST route and the UI at once.
A `merged`/`closed` proposal does not block deletion; its packet keeps whatever
files were already written (Phase 2 makes archived packets fully independent of
the branch).

## Decision 3 — attribution and the audit log

**Actor** is `locks.current_client_id()` — the same ContextVar turn locks and
branch checkouts key on, populated from the `X-Agent-Id` header (`browser` when
absent, `app.py:130`), the MCP proxy (`AGENTCAD_AGENT_ID` or `mcp`), and the
chat executor (`chat` / `chat:<session>`, re-set per tool call because executor
threads do not inherit contextvars).

**`actor_kind`** is one pure function, `proposals.actor_kind(identity)`:

```python
def actor_kind(identity: str) -> str:
    """human iff the action came from the browser UI; everything else is an
    agent. The chat dock is a human ASKING an agent — the action is the
    agent's, and the audit trail must say so. PRD-005 replaces this with the
    authenticated principal's class, with no schema change."""
    return "human" if identity == "browser" or identity.startswith("browser:") else "agent"
```

This is a deliberate, documented judgement call, not a heuristic to extend: the
browser is the only surface a human drives directly. It is stated in the tool
descriptions and in `docs/agent-api.md` so nobody reads `actor_kind` as
authentication.

**Audit entry** (`audit.jsonl`, one per line, FR13/FR14):

```json
{"seq": 4, "ts": "2026-08-10T09:12:04", "actor": "browser",
 "actor_kind": "human", "action": "reviewed",
 "details": {"verdict": "approve", "source_head": "<sha>"}}
```

Actions: `created`, `updated`, `state_changed`, `reviewed`,
`packet_generated`, `merge_attempted`, `merged`, `override`, `closed`,
`reopened`. `seq` is assigned under the lock from the current line count.
No tool writes, edits or deletes audit entries — `proposal_get` returns them
and there is no update path (FR14).

## Decision 4 — the review packet

### Where the two sides come from

**Not temp worktrees.** PRD-001 already materializes a live working tree per
branch (`branches.tree_of(proj, branch)` — the project directory for the
default branch, `.history/trees/<dirname>/` otherwise), and every mutation
snapshots, so a branch tree is normally clean and at its head. The packet
builder therefore:

1. resolves `source_head = history.resolve_branch(canonical, source)` and
   `target_head` likewise, and records both in the packet;
2. gets `source_tree` / `target_tree` from `tree_of`;
3. runs ordinary service calls under `with branches.pinned(proj, tree):` — the
   exact mechanism the merge validation pass uses (`merge.py:529`), which means
   the mesh cache, kernel pool, mates resolver and error shapes are reused
   verbatim;
4. refuses with a `conflict_error` when either tree is dirty at generation time
   (`git status --porcelain` non-empty after a checkpoint snapshot) — a packet
   generated against uncommitted bytes would pin head SHAs that do not describe
   what was measured.

**PRD divergence to fold back:** "materialize source and target states in temp
worktrees" → use the branch worktrees PRD-001 already maintains. Temp
worktrees become necessary only for packets pinned to a head that is no longer
the branch head (archived packets, Phase 2).

Because `.cache/` is canonical, content-addressed and shared across branches
(PRD-001 Decision 5), every part whose `(script, params, density, tolerance)`
tuple is unchanged is a disk-cache hit with **zero kernel work** on both sides.
That is the whole of G2.

### What is in it

The packet is one JSON document, `packet.json`, plus PNG and ACM files beside
it. Shape (abridged; every key below is normative):

```json
{
  "proposal": "3", "ok": true, "stale": false, "frozen": false,
  "generated": "2026-08-10T09:12:04Z", "generated_by": "chat:main",
  "elapsed_ms": 4210,
  "source": "nozzle-thinner", "target": "master",
  "source_head": "<sha>", "target_head": "<sha>", "base": "<sha>",
  "summary": {"parts_changed": 1, "parts_added": 0, "parts_removed": 0,
              "instances_changed": 0, "mass_delta_g": -12.4},
  "parts": [{
    "part": "nozzle", "change": "modified",
    "changed_by": ["script", "params"],        // also "manifest" (label, …)
    "script_diff": {"path": "parts/nozzle.py", "unified": "@@ …",
                    "added_lines": 4, "removed_lines": 2, "truncated": false,
                    "hunks": [{"index": 0, "header": "@@ -12,6 +12,8 @@",
                               "old_start": 12, "new_start": 12}]},
    "params_diff": {"added": [], "removed": [],
                    "changed": [{"name": "wall", "field": "value",
                                 "old": 2.0, "new": 1.6}]},
    "build": {"old": {"ok": true}, "new": {"ok": true}},
    "metrics": {"volume_mm3": {"old": 41230.5, "new": 40418.1,
                               "delta": -812.4, "pct": -1.97},
                "mass_g": {…}, "area_mm2": {…},
                "center_of_mass": {"old": [x,y,z], "new": [x,y,z],
                                   "delta": [dx,dy,dz]},
                "bbox": {"old": {"min": […], "max": […]}, "new": {…},
                         "size_delta_mm": [dx, dy, dz]}},
    "geom_diff": {"available": true, "unchanged": false,
                  "added_mm3": 0.0, "removed_mm3": 812.4,
                  "added_mesh": null,
                  "removed_mesh": "/api/projects/rocketry/proposals/3/diff/nozzle/removed.acm",
                  "skipped": null},
    "renders": {"view": "iso", "width": 640, "height": 480,
                "frame": {"min": […], "max": […]},
                "old": "/api/projects/rocketry/proposals/3/render/old/nozzle",
                "new": "/api/projects/rocketry/proposals/3/render/new/nozzle"}
  }],
  "assembly": {"changed": false, "instances_added": [], "instances_removed": [],
               "instances_moved": [], "mates_changed": [],
               "total_mass_g": {"old": …, "new": …, "delta": …, "pct": …},
               "renders": null},
  "manifest": {"scalars_changed": [], "materials_changed": []},
  "binary": [],
  "warnings": [], "errors": []
}
```

**Script diffs (FR4).** `git diff --no-color --unified=3 <target_head>
<source_head> -- parts/` through `history._run`, split per path. Diffs are text
by construction (`parts/*.py`); anything under `imports/` is detected with
merge's own heuristic (a NUL in the first 8000 bytes or not UTF-8 decodable)
and reported in `binary` as `{path, sides: {old|new: {bytes, sha256} | null}}` —
the same "size + digest, never the bytes" contract `merge._binary_conflict`
uses. A diff body over `_MAX_DIFF_BYTES = 256 KB` (the merge cap, reused) is
omitted with `"truncated": true`.

`hunks` exists **only** so PRD-008 has a stable anchor: `{proposal_id, part,
path, hunk_index, old_start, new_start}` identifies a hunk without depending on
line numbers surviving a regeneration. Nothing in MVP reads it besides the UI's
`id` attributes.

**As built (slice 4/6 fold-back).** Three details of the block above are worth
stating exactly, because the UI and the AC tests depend on them:
`params_diff` rows are `{name, field, old, new}` where **`field` is `"value"`**
for the ordinary case — the manifest stores parameter *overrides*, so there is
one row per changed override; a dict-valued (full spec) parameter is compared
field by field over `default/min/max/type/unit/choices/description` and yields
one row per changed field. `changed_by` has a **third** value, `"manifest"`
(label, material, kind, … — anything in the entry that is neither the script
nor the params). `assembly.renders` is **`null` by design**: assembly renders
are the expensive kind, so `proposal_render` draws one on demand. And every
timestamp (`generated`, the proposal's `created`/`updated`, every audit `ts`)
is **zone-aware UTC with the `Z` designator** — a naive stamp is read as local
time by `Date.parse`, which made "written a second ago" display as hours old.

**PARAMS diffs (FR4).** From the two manifests, not from git. Manifests are
read with the *strict* loader semantics `merge._manifest_at` established: a
`project.json` that exists but does not parse is a `validation_error` naming
the ref and the file — reading it as `{}` would report the entire project as
deleted. Compared per parameter and per parameter field (`default`, `min`,
`max`, `type`, `unit`, `choices`, `description`) with the same normalizing
comparison `manifest_merge._norm` uses (type-qualified JSON), so `6` and `6.0`
are different values.

**Changed-part detection.** A pure function
`changed_parts(old_manifest, new_manifest, changed_scripts) -> list[dict]`
in `packet.py`, mirroring `merge._changed_parts`'s rule: the union of (a) part
ids whose `parts/<id>.py` bytes differ per `git diff --name-only`, and (b) part
ids whose manifest entry dict differs; classified `added` / `removed` /
`modified`. `merge._changed_parts` is deliberately **not** refactored to share
this — it works against a staged *tree oid*, not a ref, and the merge path must
not move in this PRD.

**Metric deltas (FR4).** For each changed part present on a side,
`service.get_metrics(proj, part_id)` under `pinned(side_tree)`. Deltas are
`{old, new, delta, pct}` for the scalars; `pct` is `null` when `old == 0`.
`center_of_mass` gets a per-axis `delta`; `bbox` reports both boxes plus
`size_delta_mm` (per-axis size change) rather than a meaningless six-number
delta. A part that exists on one side only reports the present side and `null`
for the other.

**Assembly deltas (FR5).** `service.get_assembly(proj)` under `pinned` on each
side, so mate-driven instances are compared at their **resolved** transforms,
not their authored ones (comparing authored positions would report "no change"
for a mate whose anchor moved). Instances added/removed by id; `instances_moved`
carries old/new `position` and `rotation_deg`; `mates_changed` carries the
old/new `mate` objects from the manifests; `total_mass_g` as a delta block.

**Matched renders (FR6).** Decision 5.

**Geometric diff (FR7).** Decision 6.

**Honest degradation (FR8).** Every per-part section is independently
fallible and never aborts the packet:

- an unbuildable side sets `build.<side> = {"ok": false, "error": <structured
  error>}` and leaves `metrics`, `renders` and `geom_diff` `null` for that part;
- a boolean failure sets `geom_diff = {"available": false, "error": {…},
  "reason": "boolean failed"}` with metrics still present;
- a render failure sets that side's render to `null` and appends to
  `warnings`;
- anything unexpected is caught per part, appended to `errors` as
  `{"part": …, "stage": "metrics"|"render"|"geom_diff", "error": {…}}`, and the
  packet still returns `ok: true` for the parts that worked. `ok` is `false`
  only when the packet could not be produced at all (unreadable manifest,
  unknown ref).

### Caching and staleness — the half of FR9 that MVP keeps

FR9 is a Phase-2 item, but head-pinning is *cheaper than not doing it* here:
the packet already has to write PNG and ACM files somewhere and already records
both heads, and FR12 requires freezing the packet at merge. So MVP:

- persists `packet.json` and its assets under the proposal directory;
- serves the persisted packet when `source_head` and `target_head` still match
  the branches' current heads;
- marks it `"stale": true` and **regenerates on view** when either head moved
  (the PRD's "regenerates on next view"), or when `regenerate: true` is passed;
- generates lazily — a `draft` proposal has no packet until first view (the
  PRD's open question, answered: lazily).

Deferred to Phase 2 and explicitly not built: LRU/GC of packet assets, keeping
a packet alive after its source branch is deleted, and a packet pinned to a
head that is no longer a branch head.

**Budget (AC2).** For the rocketry nozzle change: one `git diff`, two manifest
reads, two `get_metrics` (both cache hits except the changed part on the source
side), one `geom_diff` kernel call, two renders at 640×480 (the software
rasterizer is pure Python — resolution is the dominant cost knob, which is why
the packet renders smaller than `render_view`'s 800×600 default), and two
`get_assembly` calls. Everything except the single changed build and the
boolean is disk-cache work.

## Decision 5 — matched renders need an explicit frame

`render_acm` auto-fits: it projects every vertex into camera space and fits the
combined 2-D extents with a 5 % margin (`render.py:128-136`). Two renders of
different geometry therefore get different scales and centers — the before/after
pair is *not* superimposable, which is exactly what FR6 forbids.

**Chosen:** one additive keyword argument.

```python
def render_acm(meshes, view="iso", width=800, height=600,
               frame: dict | None = None) -> bytes:
    """... ``frame`` is a world-space bounding box {"min": [x,y,z],
    "max": [x,y,z]}; its eight corners are projected through the camera basis
    and their 2-D extents replace the auto-fit, so two renders of different
    geometry share one camera. Omit it for today's per-mesh auto-fit."""
```

Implementation: build the 8 corners, project with the same `right/up` basis
`_camera_basis(view)` already returns, and use their min/max in place of `mins`
/`maxs`. Nothing else in the function changes; `frame=None` is byte-identical
to today (asserted by a test).

The packet computes the frame as the union of both sides' world bboxes — from
`metrics.bbox` for a part, from `get_assembly().bbox` for the assembly —
inflated by 2 % so a silhouette never touches the frame edge. Both sides are
rendered with the same `frame`, `view` (`iso`), and size, so the pair is
literally superimposable.

**Renders are files, not payload.** The MCP and chat transports lift **one**
top-level `png_base64` per tool result into image content
(`mcp_server.py:110`, `chat.py:110`); a packet with N before/after pairs cannot
ride that path. So `proposal_packet` returns render **URLs**, and one additive
tool — `proposal_render {project, id, side, part?, view?}` — returns a single
image with `png_base64`, giving agents real image content one render at a time
(G5). The REST twin returns `image/png` directly, exactly like
`routes_vision.py`.

**PRD divergence to fold back:** one additive tool, `proposal_render` (the same
kind of divergence PRD-001 recorded for `merge_status`).

## Decision 6 — the geometric diff is a new kernel handler pack

**Chosen:** a new pack `agentcad/kernel/handlers/diff.py` contributing one
method, `geom_diff` — *not* a new `kind` on `analyze`. `analyze` takes one
script; a diff takes two shapes and writes two meshes, so overloading it would
mean an item-shaped payload on a part-shaped handler. A sibling pack is what
the extension-point contract is for, and it keeps `analysis.py` (a large OCP
module) untouched.

```
method: "geom_diff"
params: {
  "old":  {"script": …, "params": {…}} | {"source": "<path>"} | null,
  "new":  {"script": …, "params": {…}} | {"source": "<path>"} | null,
  "added_path":   "<abs .acm path>" | null,   # tessellate new-old here
  "removed_path": "<abs .acm path>" | null,   # tessellate old-new here
  "tolerance": 0.1
}
result: {
  "added_mm3": 0.0, "removed_mm3": 812.4,
  "old_volume_mm3": …, "new_volume_mm3": …,
  "added_triangles": 0, "removed_triangles": 4820,
  "skipped_mesh": ["old"]            # present only when non-empty
}
```

Rules, each one a gotcha the repo has already paid for:

- **Volumes come from `toolbox["shape_volume"]`, never `.volume`.** A boolean
  result is routinely a nested `Compound`, and `Compound.volume` undercounts
  (it reports only the first child subtree) — `worker._shape_volume` sums
  `shape.solids()`.
- **Difference, not intersection.** `new - old` and `old - new` via the `-`
  operator. (The `&`-vs-`Shape.intersect()` trap applies to intersection, which
  this handler does not use; the difference operator returns a
  `Part`/`Compound`, and an empty result is an empty `Compound` whose
  `solids()` is empty ⇒ volume `0.0`.)
- **Mesh-kind references are excluded.** Items are resolved the same way
  `handle_interference` resolves them; a `mesh` kind (imported STL — one welded
  face, no surface) is never fed to a boolean, because STL booleans segfault
  OCCT. The side is named in `skipped_mesh` and both volumes come back `0.0`
  with the diff marked unavailable upstream.
- **Failures are structured, not fatal.** Each boolean runs in its own
  `try/except Exception` and raises `WorkerError(ERROR_KERNEL, "geometric diff
  unavailable: <reason>", {"stage": "added"|"removed"})`; the packet catches
  `KernelError` and records `geom_diff.available = false` with metrics intact.
  The per-request timeout (300 s, the build timeout) is the outer backstop.
- **Diff meshes are tessellated with `toolbox["tessellate"]`** (which takes
  `shape.wrapped`) and written with `toolbox["atomic_write"]`. A zero-volume
  side writes no file and reports `null`, so the UI knows there is nothing to
  overlay.
- The pack registers the handler unconditionally; `_load_handler_packs` refuses
  a name that collides with a builtin, and `geom_diff` does not.

**The content-hash short circuit (FR7, AC4) is in the service, not the
kernel.** Before any kernel call, the packet builder compares
`service._cache_key_for(proj, record)` computed under `pinned(old_tree)` and
under `pinned(new_tree)`. Equal ⇒ `geom_diff = {"available": true,
"unchanged": true, "added_mm3": 0.0, "removed_mm3": 0.0}` with **zero kernel
work** — which is precisely what AC4's move-an-instance-only test asserts. The
cache key already folds script content, params, density and tolerance
(`service._cache_key`), so it is the right identity.

Kernel calls pass `affinity=part_id`, matching `_rebuild`, so the diff lands on
the worker whose 16-entry shape LRU is most likely to hold both sides already.

## Decision 7 — `proposal_merge` = gates, then PRD-001, unchanged

This is what PRD-001's "Phase 3: merge gating moves into the proposal flow"
means concretely. `merge_branch` keeps working exactly as it does today; the
proposal adds a gate *in front of it* and records what came back.

**Gate objects.** `proposal_get` and `proposal_merge` both return
`gates: [{name, state, summary, details?}]` with `state ∈ pass | fail | pending
| skipped`:

| gate | MVP behavior |
|---|---|
| `state` | `fail` when the proposal's latest verdict is `changes_requested`; `pass` otherwise. `draft`/`closed`/`merged` are refused before gates are evaluated. |
| `approvals` | Decision 8. `fail` names the policy in `details`. |
| `validation` | `pending` before the merge; filled from the merge result afterwards. **Not pre-evaluated** — it *is* the PRD-001 validation pass, and running it twice would double the kernel cost for no new information. |
| `specs` (PRD-003) | `skipped`, `summary: "spec evaluation not installed"`. |
| `checks` (PRD-004) | `skipped`, `summary: "no checks posted"`. |

**The seam PRD-003 and PRD-004 plug into** is one list on the service,
installed empty by the proposals pack:

```python
service.gate_providers: list[Callable[[str, dict], dict | None]] = []
```

A provider takes `(project, proposal)` and returns a gate object or `None`. The
spec pack and the CI pack append their own from their own `register()`, so
neither PRD needs to touch `proposals.py`. Provider exceptions are caught and
degrade to `{"state": "pending", "summary": "<provider> errored"}` — a broken
optional pack must never block a merge or crash a read.

**The merge, step by step.**

1. Load the proposal; `draft` / `closed` / `merged` ⇒ `conflict_error`.
2. Evaluate gates. Any `fail` ⇒ `conflict_error` naming the first failing gate,
   `details.gates` carrying all of them — **before** anything is merged.
   `allow_invalid: true` does **not** bypass this (Decision 8).
3. `result = service.merges.merge(proj, source, target, allow_invalid=…)`.
   Three outcomes, all of them PRD-001's:
   - **success** ⇒ record `merge` on the proposal, freeze the packet
     (`packet.json` gets `"frozen": true` and is never regenerated, FR12), state
     → `merged`, audit `merged` and — when `allow_invalid` and
     `validation.ok is false` — an additional `override` entry carrying the
     report. Publish `proposal_changed`. `merge_completed` and `project_changed`
     are published by the orchestrator; the proposal adds nothing there.
   - **`{"error": {"type": "merge_conflict"}}`** (returned, not raised) ⇒
     **passed through verbatim**, with `details.proposal = <id>` added. The
     proposal's state does not change; the audit records `merge_attempted`
     with `outcome: "conflict"`. The agent resolves it with `resolve_merge` /
     the UI's existing conflict modal and calls `proposal_merge` again — the
     PRD's exact wording, and it costs us nothing because the staged merge
     lives in `.history/agentcad/merge.json`, independent of proposals.
   - **`validation_error` with `details.validation`** (the kernel gate blocked)
     ⇒ propagated unchanged; audit `merge_attempted` with `outcome: "blocked"`;
     the `validation` gate in the returned payload flips to `fail` with the
     report. Retrying with `allow_invalid: true` re-runs step 3 and lands it
     (AC5) — the staged merge is still there, so nothing is redone.
4. Everything about locking, cleanliness, the two-parent commit, the ref CAS
   and the tree sync is PRD-001's and is not re-implemented, re-checked or
   wrapped. In particular the target's turn lock is taken and held by
   `_holding_target` across validation and finalization; `proposal_merge` adds
   no lock of its own beyond the proposal-file RLock.

**The override is recorded in three places** (FR10): the audit log, the
proposal's `merge.allow_invalid`, and — because `MergeOrchestrator._commit_message`
already writes `Validation: FAILED (allow_invalid) — …` — the merge commit
message. AC5 asserts all three.

## Decision 8 — merge policy v1 is two fields, enforced in the service

`policy.json` (absent ⇒ defaults):

```json
{"approvals_required": 1, "self_approve": false}
```

The approvals gate counts **distinct actors whose latest review verdict is
`approve`**, excluding the proposal's `author` when `self_approve` is false
(FR11). Latest-wins means an approver who later requests changes stops
counting. The gate fails with

```json
{"name": "approvals", "state": "fail",
 "summary": "1 approval required, 0 recorded (self-approval does not count)",
 "details": {"approvals_required": 1, "approvals": 0,
             "self_approve": false, "author": "chat:main"}}
```

and `proposal_merge` turns that into a `conflict_error` naming the policy
(AC6). **`allow_invalid` does not override it**: `allow_invalid` is the
caller's statement about the *kernel's* verdict on geometry (that is what it
means in `merge_branch`), and letting it also override a human approval
requirement would make the one field mean two unrelated things. v1 has no
policy override at all; a project that wants unreviewed merges sets
`approvals_required: 0` or uses `merge_branch` directly, and both are visible
in the audit trail.

**Policy is read at call time, not cached**, and there is deliberately **no
policy tool or route in MVP** — org rules, code owners and branch protection
wait for PRD-005's roles (the PRD's own "policy scope creep" risk). The file is
the seam.

**Stale approvals.** Each review records the `source_head` it was made against.
When that differs from the current source head, `proposal_get` marks the review
`"stale": true` and the approvals gate's summary says so — but the approval
still counts in v1. Dismissing stale approvals is a third policy field and
therefore Phase 2; making it silent would be worse than making it visible.

## Decision 9 — UI (MVP)

The frontend recon from PRD-001 still holds and constrains this: `setupMenus()`
snapshots `.menu-wrap` elements **once at boot**, so new menus must be static
markup in `index.html`; modals follow `#drawing-modal`'s
`.modal-overlay > .modal > .modal-head + body` structure with three handlers
(close button, backdrop click, Escape); `#toasts` is `z-index: 90` above
`.modal-overlay`'s 80; `setupKeys()` has a `modalOpen()` guard.

- **Toolbar entry.** A `#proposals-btn` next to the branch switcher with an
  open-count badge (`#proposals-count`, hidden at zero), refreshed on
  `proposal_changed` and on project load.
- **`#proposals-modal.modal-overlay.hidden`** — a wide modal (`.modal.wide`,
  `width: min(1100px, 100%)`) with a master/detail split: a left list (state
  chip, `#id`, title, author with a human/agent badge, relative age, gate dot)
  and a right detail pane with a header (title, source → target, state, actions)
  and — **as built** — *five* tabs: Overview, Files, Geometry, Checks and
  **Audit**. Audit is the fifth because FR14's append-only log has no other
  surface; metrics and renders stay inside Overview, as specified.
- **Overview** — description, the metric-delta table (part, volume, mass, CoM,
  bbox: old → new, Δ, %), and the before/after render pair per changed part as
  two `<img>` elements at the same size (they are frame-matched, so a CSS
  cross-fade on hover is free and is the cheapest possible "superimposable"
  proof).
- **Files** — per changed part, the unified diff rendered as **plain DOM**:
  one `<div class="diff-line">` per line with `.diff-add` / `.diff-del` /
  `.diff-hunk` / `.diff-ctx`, inside an `overflow-x: auto` block, plus the
  PARAMS diff table. **Not** a CodeMirror merge view: `frontend/vendor/` holds
  only `codemirror.js`, `codemirror.css`, `python.js` and the three.js files —
  there is no merge addon, and adding one means vendoring new files *and*
  editing `scripts/vendor_frontend.sh` (the frontend is offline-only, no CDN)
  for a read-only view. A DOM line node per diff line is also exactly the anchor
  PRD-008 needs (`data-hunk` / `data-line` attributes ship now, unused).
  **PRD divergence to fold back.**
- **Geometry** — the target build in the viewport plus the diff meshes as
  translucent color-coded overlays (added green, removed red, both from theme
  tokens). `viewport.js` gains one additive pair,
  `showDiffOverlay(partId, buffer, key, kind)` / `clearDiffOverlay()`, modelled
  **exactly on `highlightFace`/`clearFaceHighlight`** (`viewport.js:371-420`):
  a separate mesh parented to the **scene root, not `contentGroup`**, with its
  own dispose path, cleared by `clearContent()` and on part switch. That is the
  established non-destructive-overlay precedent, and it keeps `scene` and
  `contentGroup` module-private (there is deliberately no generic
  "add a THREE.Group" export). Buffers arrive as raw ACM1 `ArrayBuffer`s through
  the existing `parseACM` path, like `showPart`/`showAssembly`.
- **Checks** — the gate list with state chips and drill-in (the validation
  report renders with the same block `merge.js` already has for
  `merge_completed`; **as built**, reusing it meant exporting `reportBlock` —
  a third additive change to an existing file, additive and with no existing
  caller touched).
- **Audit** (as built) — the append-only log as a table: seq, ts, actor with
  its human/agent kind, action, details.
- **Actions** — Approve · Request changes · Comment · Merge · Close/Reopen ·
  Edit… · Regenerate packet. Merge is disabled
  while a gate is red, with the reason in its `title`; the disabled state is a
  hint, not the enforcement (FR11 says the service enforces, and a test asserts
  the service refuses even when the UI would have allowed it). An explicit
  "Merge anyway (validation failed)" appears only after a blocked validation and
  sends `allow_invalid: true`.
- **Events.** `handleEvent()`'s single `switch (ev.type)` gains one case,
  guarded by `if (ev.project !== state.projectName) return;`, delegating to
  `proposals.handleEvent(ev)`: refresh the count badge, the list, and the open
  detail pane when the id matches. `merge_completed` already refreshes the
  project.
- **Plumbing.** `frontend/js/api.js` gains a
  `// ---- proposals ----` section of one-line arrows over the module-private
  `enc()`, with the routes' dual error contract commented (a `merge_conflict`
  arrives at HTTP 200 in `res.error`, everything else throws `ApiError`) — the
  same note `api.js:97-99` already carries for merge. `state.js` gains
  `proposals` (list payload or null) and `proposal` (open detail or null) with
  one-line comments, mutated only through `setState`. `proposals.js` follows
  `versions.js`/`merge.js`: module-scope handles, `export function init(actions)`
  wired from `main.js`, `open()`/`isOpen()`, content built with
  `document.createElement` + `textContent` (never `innerHTML` for data), and a
  module `busy` flag guarding double-submit on Approve/Merge.
- **CSS** — new `prop-*`, `diff-*`, `gate-*` classes using **only** existing
  tokens (`--panel`, `--hairline`, `--dim`, `--accent*`, `--err*`, `--ok*`,
  `--scrim`, `--shadow-modal`) so light mode keeps working; a `.modal.wide`
  variant.

---

## Surfaces

### Tools (`agentcad/core/tools_proposals.py`)

**Registration and load order.** `tools._load_tool_packs` walks
`pkgutil.iter_modules` over `agentcad.core`, so `tools_proposals` is imported
**before** `tools_versioning` — `service.branches` does not exist yet at
`register()` time. Therefore:

```python
def register(registry, service) -> None:
    if not service.history.available():
        return          # no git -> no branches -> no proposals (FEM precedent)
    service.proposals = ProposalManager(service)   # takes service.branches lazily
    service.gate_providers = []
```

`ProposalManager` reaches `service.branches` / `service.merges` **inside each
handler**, never in `__init__`, and raises a `ValidationError` naming git if
they are still absent when a handler runs. The `branch_delete` wrapper
(Decision 2) is installed the same way — lazily, on first use — because
`service.branches` is not there to wrap at registration time. Route packs are
mounted after all tool packs, so `routes_proposals.build_router` can probe
`registry.get("proposal_list") is None` and return an empty router, exactly as
`routes_versioning` does.

| Tool | Schema | Returns |
|---|---|---|
| `proposal_create` | `{project*, source*, target?, title*, description?, draft?}` | the proposal + `{gates, packet: null}` |
| `proposal_list` | `{project*, state?}` | `{proposals: [summary…], counts: {open, draft, …}}` |
| `proposal_get` | `{project*, id*}` | `{proposal, gates, audit, packet: {generated, stale, ok} \| null}` |
| `proposal_update` | `{project*, id*, title?, description?, state?}` | the updated proposal + gates |
| `proposal_packet` | `{project*, id*, regenerate?}` | the packet (Decision 4) |
| `proposal_review` | `{project*, id*, verdict*, summary?}` | the updated proposal + gates |
| `proposal_merge` | `{project*, id*, allow_invalid?}` | the PRD-001 merge result + `{proposal, gates}`, or `merge_conflict`, or a blocked `validation_error` |
| `proposal_render` | `{project*, id*, side*, part?, view?}` | `{path, width, height, view, side, part, png_base64}` (additive — the image-content path) |

`verdict ∈ approve | request_changes | comment`; `side ∈ old | new`
(`old` = target = *ours*, `new` = source = *theirs*, stated in every
description so the PRD-001 convention never inverts); `view ∈ iso | front | top
| right`.

### Routes (`agentcad/server/routes_proposals.py`)

```
GET    /api/projects/{proj}/proposals                      ?state=
POST   /api/projects/{proj}/proposals        {source, target, title, description, draft}
GET    /api/projects/{proj}/proposals/{id}
PATCH  /api/projects/{proj}/proposals/{id}   {title, description, state}
GET    /api/projects/{proj}/proposals/{id}/packet          ?regenerate=1
POST   /api/projects/{proj}/proposals/{id}/review  {verdict, summary}
POST   /api/projects/{proj}/proposals/{id}/merge   {allow_invalid}
GET    /api/projects/{proj}/proposals/{id}/render/{side}/{part}   ?view=iso   -> image/png
GET    /api/projects/{proj}/proposals/{id}/render/{side}          ?view=iso   -> image/png (assembly)
GET    /api/projects/{proj}/proposals/{id}/diff/{part}/{kind}.acm -> application/octet-stream
```

All are `registry.call(...)` passthroughs with **explicitly whitelisted body
keys** (never `**body`), reusing `routes_versioning`'s `_result` /`_body_keys`
/`_json` shape verbatim: `merge_conflict` is the single error type returned as
an `{"error": …}` body at HTTP **200**; everything else — including
`invalid_arguments` and unmapped types — raises into 404/422/409/422. The two
binary routes decode `png_base64` / read the ACM file and return raw bytes with
`Cache-Control: no-store`, exactly like `routes_vision.py` and the mesh route.

### Events

```json
{"type": "proposal_changed", "project": "rocketry", "id": "3",
 "state": "approved", "reason": "review"}
```

`reason ∈ created | updated | review | packet | merged | closed`. The PRD
specifies `{project, id, state}`; `reason` is additive so the UI knows whether
to refetch the packet (the same kind of addition `branch_changed`'s `client`
is). Not `project_changed`, so it never triggers `_snapshot_on_event`.
`proposal_merge` additionally causes PRD-001's `project_changed` +
`merge_completed`, unchanged.

### Error shapes

```json
{"error": {"type": "conflict_error",
  "message": "an open proposal already exists for 'nozzle-thinner' -> 'master'",
  "details": {"existing_id": "3", "source": "nozzle-thinner", "target": "master"}}}

{"error": {"type": "validation_error",
  "message": "cannot move proposal 3 from 'merged' to 'open'",
  "details": {"id": "3", "from": "merged", "to": "open", "allowed": []}}}

{"error": {"type": "conflict_error",
  "message": "proposal 3 cannot merge: 1 approval required, 0 recorded",
  "details": {"id": "3", "failing": "approvals",
              "gates": [{"name": "approvals", "state": "fail", …}, …]}}}

{"error": {"type": "validation_error",
  "message": "merge validation failed: nozzle build error",
  "details": {"validation": { … PRD-001 report … }, "merge_id": "9f31c0",
              "proposal": "3"}}}

{"error": {"type": "merge_conflict", "message": "… 2 conflicts",
  "details": { … PRD-001 details verbatim … , "proposal": "3"}}}
```

- Unknown proposal / unknown branch / unknown part → `notfound_error` (404).
- Bad state transition, bad verdict, unknown side, `source == target`,
  unparseable `project.json` on a ref → `validation_error` (422).
- Duplicate open proposal, red policy gate, merging a draft, deleting a branch
  with an open proposal, a dirty branch tree at packet time → `conflict_error`
  (409).
- `merge_conflict` and the blocked-validation `validation_error` are PRD-001's
  payloads, forwarded with `details.proposal` added and nothing else changed.
- Packet-internal failures are **never** errors: they are `build.<side>.error`,
  `geom_diff.available: false`, or entries in `warnings` / `errors` (FR8).

---

## Data flow — the AC1 walk

1. Agent (identity `chat:main`): `branch_create {project: "rocketry", name:
   "nozzle-thinner"}` → `branch_switch` → `set_params {part_id: "nozzle",
   values: {wall: 1.6}}`. The store resolves through `branch_resolver` to
   `.history/trees/nozzle-thinner/`; `project_changed` snapshots on that branch.
2. `proposal_create {project: "rocketry", source: "nozzle-thinner", title: …,
   description: …}` → target defaults to `master`; id `1` allocated under the
   lock; `proposal.json` + `audit.jsonl` written under
   `.history/agentcad/proposals/1/`; audit entry `created` with `actor:
   "chat:main"`, `actor_kind: "agent"`; `proposal_changed {state: "open"}`
   published.
3. Human opens the browser (identity `browser`), sees the badge, opens the
   proposal. The UI calls `GET …/proposals/1/packet`, which finds no packet and
   generates: `git diff master nozzle-thinner -- parts/` → `parts/nozzle.py`;
   manifests at both heads → `parts.nozzle.params.wall` 2.0 → 1.6; changed part
   `nozzle`.
4. Under `pinned(target_tree)`: `get_metrics("rocketry", "nozzle")` → disk-cache
   hit. Under `pinned(source_tree)`: same call → the one real kernel build
   (already warm from step 1, so also a cache hit). Cache keys differ ⇒ no
   short-circuit ⇒ one `geom_diff` kernel call with both `(script, params)`
   pairs → `removed_mm3 = 812.4`, `added_mm3 = 0.0`, one `removed.acm` written.
5. Frame = union of both bboxes; two 640×480 `iso` renders written under
   `renders/`. `packet.json` persisted with both head SHAs. Audit
   `packet_generated`.
6. Human clicks Approve → `proposal_review {verdict: "approve"}` → state
   `approved`, audit `reviewed` with `actor_kind: "human"`,
   `proposal_changed` published; a second browser tab sees it live (AC8).
7. Human clicks Merge → `proposal_merge` → gates: `state` pass, `approvals`
   pass (1 approval, author is `chat:main` so self-approval is not in play),
   `validation` pending, `specs`/`checks` skipped → `merges.merge("rocketry",
   "nozzle-thinner", "master")` → three-way (or fast-forward) merge, kernel
   validation pass, two-parent commit, ref CAS, tree sync, `merge_completed`.
8. Proposal state → `merged` with the commit id; packet frozen; audit `merged`.
   `proposal_get` shows every action attributed: `created`/`packet_generated`
   as `agent`, `reviewed`/`merged` as `human` (AC1).

## Testing strategy

**Shared harness** (copy verbatim from `tests/test_versioning_api.py` and
`tests/test_prd001_acceptance.py`):

```python
_GIT = [pytest.mark.integration, pytest.mark.portability,
        pytest.mark.skipif(shutil.which("git") is None,
                           reason="git not found on PATH")]
pytestmark = _GIT

@pytest.fixture(autouse=True)
def _reset_context():
    cid = locks.client_id_var.set("local")
    pin = pinned_tree_var.set(None)
    yield
    locks.client_id_var.reset(cid); pinned_tree_var.reset(pin)
```

plus the `stack` / `demo` / `client` fixture triple (`AgentCADService(...)`
directly — **not** `make_test_service`, which sets `bus.on_publish = None` and
kills the snapshot hook; `build_registry(service)`;
`create_app(..., extra_allowed_hosts={"testserver"})` and
`TestClient(app, base_url="http://127.0.0.1")`), the
`assert getattr(service, "proposals", None) is not None` seam check, the
`_on(service, proj, client, branch)` multi-client helper, and the
`assert "error" not in result, result` assertion idiom.

- **`tests/test_proposals.py`** — the object, lifecycle, policy and audit.
  Mostly kernel-free (a hand-built project from `BOX_SCRIPT` and its
  `.replace(...)` variants), real service with real history. Covers: every
  legal transition and a representative
  illegal one; duplicate-open refusal; `branch_delete` refusal; audit
  append-only and ordering; `actor_kind` for `browser` / `chat:main` / `mcp` /
  `local` via `locks.set_client_id`; index rebuilt from directories when
  deleted; `project_restore` leaves proposals untouched (AC9); policy defaults
  and self-approval (AC6).
- **`tests/test_packet.py`** — packet generation, `slow` for the example-driven
  cases. Script/PARAMS/assembly deltas from hand-built manifests (pure);
  metric deltas, matched frames, geometric diff and the unbuildable-side
  degradation (AC7) against the real kernel; the content-hash short-circuit
  asserted by counting kernel `build`/`geom_diff` calls with the
  monkeypatch-a-counting-`kernel.request` pattern `tests/test_history.py`
  already uses (AC4); the hole-volume case within 1 % (AC3); the timed
  rocketry case (AC2) on a **copy**
  (`shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".cache",
  "exports"))`, `tests/test_examples.py:44`).
- **`tests/test_render.py`** (extended) — `frame=None` is byte-identical to
  today; two different shapes rendered with the same `frame` produce the same
  projected scale/center (assert by rendering a known point-sized marker, or by
  unit-testing the extracted framing helper directly).
- **`tests/test_proposals_api.py`** — the three sections
  `test_versioning_api.py` uses (`# --- 1. registration`, `# --- 2. routes`,
  `# --- 3. events`): every tool registered with `input_schema["type"] ==
  "object"`, `project` in properties *and* required, a non-empty description;
  a **description-contract** test asserting the descriptions name `old`/`new`
  as target/source and name the follow-up tool (`proposal_packet`,
  `resolve_merge`) — the twin of
  `test_tool_descriptions_state_the_ours_theirs_convention`; argument
  validation returning `invalid_arguments`; the **self-disable** test
  (monkeypatch `ProjectHistory.available -> False`; no `proposal_*` tools and
  no `service.proposals`); each route returning the tool payload verbatim, with
  `merge_conflict` at HTTP 200 and `invalid_arguments`/unknown types at 422;
  the whitelisting test that posts `{"evil": …, "project": "other", "title":
  None}` and still gets 200; the render/diff routes returning real bytes with
  the right media type; and `proposal_changed` drained off a
  `service.bus.subscribe()` queue (AC8).
- **`tests/test_prd002_acceptance.py`** — one named test per criterion
  (`test_ac1_…` … `test_ac9_…`), mirroring `tests/test_prd001_acceptance.py`,
  over the real service, registry and kernel. AC1's browser half and the
  Geometry-tab half of AC3 are the browser session (below); the test asserts
  the evidence is on the record, as `test_prd001_acceptance.py` does for AC6.
- **Browser verification** (AC1, AC3, definition of done): create a branch and
  a proposal, review and merge it in the browser with zero terminal use; drill
  the hole case and see the red overlay in the Geometry tab. Zero console
  errors, screenshots recorded in the changelog.
- **Markers.** Everything that shells git carries `integration` + `portability`
  + the git skipif; example-driven and kernel-heavy cases add `slow`. Nothing
  here is `exhaustive`.

## Risks and open questions

| Risk | Mitigation / what the implementer must verify empirically |
|---|---|
| **Boolean diff robustness and cost.** OCCT booleans fail readily on real parts, and `new - old` on a filleted/shelled body is exactly the hard case. | Per-boolean guard + the honest `available: false` fallback + the 300 s request timeout. **Measure on `examples/surfacing` and `examples/engine`, not just rocketry**, and record the numbers in the changelog. A mesh-space diff is the cheaper fallback if the B-rep one proves unreliable — but only after measurement, and it is not in MVP. |
| **AC2's 10 s budget.** The renderer is pure-Python rasterization and the packet does two renders per changed part. | 640×480 and `iso` only in MVP. If the warm rocketry packet misses 10 s, the first lever is render size, the second is rendering the assembly only when it changed — **not** dropping the geometric diff. Time it and cite the number. |
| **`geom_diff` on multi-solid parts.** `pairwise_interference` documents that build123d 0.9's `&` misbehaves on multi-solid `Compound` operands. `-` is a different operator, but the family is suspect. | Verify `-` on a two-solid part in the spike before building on it; if it misbehaves, decompose to solids and difference per-solid, summing volumes (the shape `pairwise_interference` already uses). |
| **Tool-pack load order.** `tools_proposals` sorts before `tools_versioning`, so `service.branches` does not exist at `register()` time. | Decided: take `service.branches`/`service.merges` lazily inside handlers, and install the `branch_delete` wrapper lazily too. Assert it with a test that builds the registry and calls a proposal tool — never rename the pack, never touch `tools.py`. |
| **`EventBus.on_publish` is a single slot** already claimed by the service. | Proposals only `publish`; nothing here may assign `on_publish`. If a future staleness hook needs it, it must chain, not overwrite. |
| **Packet storage growth** — PNGs and diff meshes per proposal. | MVP keeps one packet per proposal (overwritten on regeneration), which bounds it at O(open proposals × changed parts). LRU/GC is Phase 2. Measure the on-disk size of the rocketry packet and record it. |
| **Stale packets and stale approvals.** A review is a judgement about a specific source head. | Both are pinned and surfaced (`packet.stale`, `review.stale`); the merge re-checks gates against current heads; approvals still count in v1, visibly. Dismissal is Phase 2. |
| **Identity is a header until PRD-005.** `X-Agent-Id` is unvalidated, so `actor_kind` is bookkeeping, not authentication. | Documented in `docs/agent-api.md` and in the tool descriptions. Same exposure turn locks already have. |
| **Two browser tabs share the `browser` identity**, so "two humans approving" is not expressible locally. | Accepted for a local single-user tool; `approvals_required: 1` with `self_approve: false` is still meaningful when the author is an agent — which is the v4 case that matters. PRD-005 revisits it. |
| **A dirty branch tree at packet time.** | Checkpoint-then-refuse (`conflict_error`), mirroring `BranchManager._checkpoint`; never generate a packet whose pinned heads do not describe the measured bytes. |
| **Reference (STL) parts.** `build_reference` reports `center_of_mass` as the **bbox center**, not a true CoM. | The packet must not present a CoM delta for a mesh-kind part as if it were a mass property; mark it `null` with a warning, and skip the geometric diff (`skipped: "mesh"`). |

## PRD divergences to fold back

1. **Proposal state lives at `<project>/.history/agentcad/proposals/`**, not
   `<project>/.proposals/` — proposals must be branch-independent, and
   `.history/` is already outside every tree, so FR3 holds structurally and no
   `info/exclude` change is needed.
2. **The packet is generated from the branches' existing worktrees**
   (`branches.tree_of` + `branches.pinned`), not from temp worktrees. Temp
   worktrees are only needed for packets pinned to a non-head commit, which is
   Phase 2.
3. **Two additive tools** beyond the PRD's list: `proposal_render` (the
   MCP/chat image-content path — the transports lift exactly one top-level
   `png_base64` per result, so a multi-render packet cannot carry images
   itself). *(`proposal_packet` returns render URLs instead of images.)*
4. **Head-pinning and on-view regeneration (the cheap half of FR9) are in
   MVP**, because the packet must persist its PNG/ACM assets and be frozen at
   merge anyway. LRU/GC and branch-deletion survival stay Phase 2.
5. **Diffs render as plain DOM, not a CodeMirror merge view** — the merge addon
   is not vendored, and a DOM line node is the anchor PRD-008 needs.
6. **`proposal_changed` carries `reason`** in addition to `{project, id,
   state}`.
7. **A geometric diff is a new handler pack** `kernel/handlers/diff.py`, not a
   new kind on `analysis.py` — the PRD offered either.
8. **`allow_invalid` overrides only the kernel validation gate**, never the
   approvals policy; v1 has no policy override.
9. **Five tabs, not four** — Audit is a fifth tab because FR14's append-only
   log has no other surface (the PRD's Experience section names four).
10. **`params_diff` rows carry `"field": "value"`** for the ordinary case: the
    manifest stores parameter *overrides*, not specs. A dict-valued parameter
    still yields one row per changed spec field. `changed_by` has a third
    value, `"manifest"`.
11. **`assembly.renders` is `null` by design** — assembly renders are the
    expensive kind and are drawn on demand by `proposal_render`.
12. **All timestamps are zone-aware UTC** (`…Z`): the proposal's
    `created`/`updated`, every audit `ts`, and the packet's `generated`.
