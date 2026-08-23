# PRD-027 — Project, part & assembly navigation at scale

- **Status:** completed (PR #34, merged 2026-08-23 as `bf3b41a`) — MVP + Phase 2 shipped; Phase 3 (sub-assembly nesting, pattern member rows, 1k-instance certification) deferred. Design: `docs/superpowers/specs/2026-08-23-project-navigation-scale-design.md`; plan: `docs/superpowers/plans/2026-08-23-project-navigation-scale.md`; changelogs 0331–0339.
- **Phase:** v5 — daily-driver depth
- **Created:** 2026-08-09
- **Origin:** founder idea #5 (Aug 2026), engineering-reviewed
- **Depends on:** PRD-026 (shell primitives — soft but strongly preferred)
- **Related:** PRD-013 (sub-assemblies appear in the tree), PRD-025 (Library/workspaces), PRD-012 (config identity in rows)

## Problem & motivation

The sidebar is a flat list of parts and a flat list of instances; the
project switcher is a dropdown; there is no search, grouping, tagging, or
visual identification. This already strains on the bundled engine example
(32 parts, 63 instances) and collapses at the scale the roadmap targets
(PRD-013: 1k+ instances; PRD-011: libraries of packages). Founder idea #5
names it: "with lots of parts it would become hard to manage and navigate."

Incumbents treat navigation as core IP — SolidWorks' feature/assembly trees
with folders and states, Fusion's browser, Onshape's instance lists — and
their users still complain at scale (market_research.md, "Onshape"
weaknesses: large-assembly slowdowns). We can do better with less: our
parts are files with metrics, thumbnails are a solved problem
(render_view), and search over code+metadata is trivial server-side.

## Users & jobs

- **Engineer on a 50-part project:** find the part ("the one with the
  M5 bosses"), see at a glance what's broken, act on groups (export these
  six, set material on all brackets).
- **Newcomer to a project:** orient visually — thumbnails and structure
  tell the story before reading any script.
- **Agent:** query the project semantically (`search_parts`) instead of
  listing everything; address stable groups ("the fastener set") in
  conversation.
- **Reviewer (PRD-002):** navigate a proposal's touched parts quickly.

## Goals

- G1. Hierarchical organization: folders/groups for parts and assembly
  instances (plus sub-assembly nesting when PRD-013 lands), drag-to-
  organize, collapse state persisted.
- G2. Instant search and filtering: by name/label, material, build state
  (error/stale/ok), tag, kind (script/reference/package), free-text over
  script content — results in < 100 ms on 1k parts.
- G3. Visual identity: per-part thumbnails (server-rendered, cached by
  content hash) in tree rows (small) and a project dashboard grid (large).
- G4. Tags as first-class metadata usable by humans, filters, and agents.
- G5. Bulk operations on multi-selection: set material, tag, export,
  delete (with dependency checks), move to folder.
- G6. A project home/dashboard: recent projects with thumbnails and stats
  (parts, mass, last change, failing checks), replacing the bare dropdown
  as the entry experience.
- G7. Scale mechanics: virtualized tree rendering, incremental updates from
  existing WS events — smooth at 1k+ rows.

## Non-goals

- A feature-history tree (we have no feature tree by design — the script
  is the history; PRD-016 covers script-navigation aids).
- Cross-project global search (Library/Market search is PRD-011/031).
- Saved smart-filter views (later phase).
- Access control on folders (permissions are project-level, PRD-005).

## Experience

The sidebar becomes a real tree: folders ("chassis", "fasteners",
"imported"), rows with a 24 px thumbnail, name, and state dot; type-to-
filter box pinned at top (`/` focuses it); right-click (or ⋯) context menu
with rename/tag/move/export/delete; multi-select with Shift/Cmd; a bulk
action bar appears on multi-selection. Assembly section mirrors the same
tree mechanics with instance patterns (PRD-013) shown as one collapsible
row. Every row is keyboard-reachable (already true) and drag-reorderable.

Filters compose: `state:error tag:printed material:al6061 boss` narrows
live; the same query language powers the agent tool. Clearing search
restores the tree state.

The project switcher becomes a dashboard page (first run and ⌘K "open
project"): card grid of projects with hero thumbnail (assembly render),
part count, total mass, last-modified, failing-check badge (with PRD-003);
plus New/Open-by-path actions in proper dialogs (PRD-026).

**Agent path.** `search_parts {project, query, filters?}` returns matching
parts with metadata (the same engine the UI uses); `set_part_meta {project,
part_id, tags?, folder?}` organizes; bulk verbs accept part lists. Agents
use tags as stable handles ("everything tagged `printed` gets the FDM
profile" — composes with PRD-025's Produce and PRD-021 checks).

## Functional requirements

- FR1. Manifest gains additive, schema-tolerant metadata: per-part
  `folder` (path-like, `a/b`) and `tags: [str]`; per-instance `folder`;
  unknown keys keep round-tripping (store already tolerant).
- FR2. Tree renders folders + rows with thumbnail, label, state dot;
  drag-move updates the manifest atomically; collapse state persists per
  project (localStorage).
- FR3. Search: server-side index over id/label/material/tags/state +
  script text (simple inverted index or on-demand grep — decided in
  design; must hit G2's latency on 1k parts); UI filter box + query
  syntax `field:value` free-composed with text.
- FR4. Thumbnails: server renders a small iso view per part on successful
  rebuild (reusing render_view's rasterizer), cached by the part's content
  hash next to the mesh cache; served via `GET .../parts/{id}/thumb.png`;
  never blocks the rebuild path (generated post-`rebuild_finished`,
  budget-capped).
- FR5. Bulk operations: multi-select → set material / add-remove tags /
  move folder / export selection / delete; each is one service call per
  part under the hood but presented as one undo step (grouped history
  label; PRD-001-compatible).
- FR6. Dashboard: project cards with assembly thumbnail, stats, and
  badges; renders in < 500 ms for 20 projects (thumbnails cached).
- FR7. Virtualized rendering keeps tree interaction at 60 fps with 1k+
  rows (measured with the PRD-013 synthetic assembly).
- FR8. All existing WS events keep panes live (a rebuild flips the row
  state dot; a tag change from an agent appears immediately).

## Agent surface

New tools: `search_parts {project, query, filters?}` ·
`set_part_meta {project, part_id, folder?, tags?}` ·
`bulk_part_op {project, part_ids, op, args}` (op ∈ material/tag/untag/
folder/export/delete; per-part results array, partial success allowed with
per-item errors). Changed: `get_project` includes `folder`/`tags`.
New event: `parts_meta_changed {project, part_ids}`.

## Technical approach

- **Store/model:** additive fields on part/instance records
  (`core/project.py`, `core/model.py` dataclasses); validation (folder
  path charset, tag charset/length, counts).
- **Search:** `core/search.py` building a lightweight in-memory index per
  open project, invalidated by the existing EventBus (`project_changed`);
  script-text search reads the files it already owns. Tool + route pack
  (`tools_navigation.py`, `routes_navigation.py`).
- **Thumbnails:** post-build hook on the service (same place LOD tiers are
  requested) calling the existing software rasterizer at ~192², written
  atomically into `.cache/` keyed by content hash; route serves with
  long-lived cache headers.
- **Frontend:** tree rewrite in `frontend/js/tree.js` with a small
  virtual-list utility; dashboard page module; bulk bar; all on existing
  state/actions wiring and PRD-026 primitives (context menus, dialogs).
- No kernel changes.

## MVP & phasing

- **MVP:** folders + tags (manifest + tools), tree with thumbnails +
  type-to-filter (client-side filter at MVP scale), bulk material/tag/
  export, dashboard v1.
- **Phase 2:** server search index + query syntax, virtualization, bulk
  delete/move with dependency handling, grouped-undo polish.
- **Phase 3 (with 013):** sub-assembly nesting, instance-pattern rows,
  1k-instance performance target certified.

## Acceptance criteria

- AC1. Engine example: create folders (block/pistons/fasteners), drag
  parts in, reload — structure persists; agent `search_parts
  {query: "tag:fastener"}` returns exactly the tagged set (test).
- AC2. Type "err" with one broken part filters to it in <100 ms
  (browser-verified); fixing it live-updates the dot without refresh.
- AC3. Thumbnails appear for every built part on the dashboard and tree;
  a script change refreshes the thumb after rebuild (hash-keyed, test).
- AC4. Bulk: select 6 fasteners → set material steel_a36 → one undo
  restores all six (test through service + UndoCursor).
- AC5. Synthetic 1k-part tree scrolls at interactive rate with
  virtualization (measured, PRD-013's fixture).
- AC6. Full suite green; browser session per definition of done.

## Risks & open questions

- **Folder semantics vs files:** folders are manifest metadata, not
  directories — scripts stay flat in `parts/` (portability, git-diff
  stability). Confirm no user expectation mismatch in design review.
- **Script-text search cost** on huge projects: on-demand grep may beat a
  maintained index at our scale; benchmark both in design.
- **Thumbnail queue starvation** under mass rebuilds (studies, CI):
  thumbnails are lowest-priority pool work, skippable under load
  (freshness catches up post-run).
- **Bulk-op partial failure UX:** per-item error surfacing needs a real
  results dialog (PRD-026), not a toast blizzard.

## Competitive references

SolidWorks/Fusion/Onshape trees set expectations (folders, states,
search); their large-assembly complaints set the bar to beat
(market_research.md, "Onshape", "Desktop incumbents"). We differ:
thumbnails and search come from the same validated substrate agents use
(render cache, one registry), tags are shared human/agent handles, and
navigation state stays out of the geometry files — so organizing a project
never touches its history.
