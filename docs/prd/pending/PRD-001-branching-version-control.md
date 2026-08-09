# PRD-001 — Branching version control for projects

- **Status:** pending
- **Phase:** v4 — collaborative core
- **Created:** 2026-08-09
- **Origin:** competitive analysis (Aug 2026)
- **Depends on:** — (first feature of v4)
- **Related:** PRD-002 (proposals build on branches), PRD-004 (CI runs on refs), PRD-015 (releases pin tags)

## Problem & motivation

Project history today is a linear git snapshot log used only for undo
(`project_history`/`project_restore`, `UndoCursor`). There is no way to try a
risky change in isolation, keep two design directions alive, name a state
("the version we sent to the machine shop"), or combine work from two people
or two agents. Every collaborative workflow the v4 wedge depends on —
proposals, review, CI, releases — needs branches and named immutable
versions underneath.

The competitive evidence (market_research.md, "Onshape & cloud-native CAD"):
versioning is Onshape's backbone, and also its ceiling — merging is per-tab
with three fixed strategies, conflicting edits resolve as "the from workspace
wins," and nothing can be cherry-picked or reviewed, because the deltas are
binary database operations. Model-as-code removes that ceiling: our parts are
Python files and our manifest is JSON, both of which merge, diff, and
conflict *meaningfully*. Nobody in the market can offer a real merge; we get
it nearly for free.

## Users & jobs

- **Design engineer (human):** explore an alternative ("what if the flange
  is welded, not bolted") without destabilizing the mainline; name and
  return to known-good states.
- **Reviewing engineer (human):** see exactly what a branch changes before
  it lands (consumed via PRD-002).
- **Design agent:** get a cheap isolated workspace per task — branch, work,
  propose — so parallel agents never fight over the mainline or each other.
- **CI / release tooling (agent or automation):** address any state by ref
  (branch, tag) for checks and bundles.

## Goals

- G1. A project can hold multiple named branches with independent histories,
  cheap to create and switch.
- G2. Any state can be tagged as an immutable named version, restorable and
  referenceable forever.
- G3. Branches merge with *surfaced* conflicts: script conflicts as standard
  git conflict markers, manifest conflicts key-wise (per part, per instance,
  per material entry) — never line-wise JSON garbage, never silent
  last-writer-wins.
- G4. A merge is only complete when the kernel revalidates the result — a
  merge that breaks builds or introduces interference is blocked (or lands
  flagged, per caller's choice).
- G5. Everything is equally usable from the UI, the tools, and raw git (the
  project remains a plain git repo a power user can clone).

## Non-goals

- Proposal/review workflow — PRD-002 (this PRD is the substrate).
- Multi-project / registry versioning — PRD-011.
- Rewriting history (rebase/squash): out of v1; history is append-only.
- Server-hosted remotes and sync — PRD-005 (this PRD is local-first).

## Experience

**Human path.** The toolbar gains a branch switcher next to the project
switcher: current branch name, a menu of branches with last-change times, a
"New branch…" action, and a "Versions…" list of tags. Switching branches
swaps the working state (parts, params, assembly) with the same liveness as
any other change — viewport, tree, and inspector refresh from WebSocket
events. A "Merge into <target>…" action shows a pre-merge summary (parts
changed on each side, conflicts predicted) and either completes — with a
post-merge validation report — or drops the user into conflict view: per
conflicted part, both versions side by side (script text + built geometry
when buildable), pick-left / pick-right / edit-by-hand. Undo (Cmd+Z) keeps
working per-branch exactly as today.

**Agent path.** `branch_create {project, name, from?}` → work normally (all
existing mutating tools operate on the current branch of that agent's
session) → `merge_branch {project, source, target, strategy?}`. On conflict
the tool returns a structured payload naming each conflicted file/key with
both sides' content; the agent resolves by writing the file (scripts) or
calling `resolve_merge {choices}` (manifest keys), then re-runs the merge.
Tags: `version_tag {project, name, message?}`; `list_versions`. History
tools grow a `ref` argument.

**Handoff.** A human can pick up an agent's branch from the switcher at any
moment (and vice versa); the branch is the shared unit of work.

## Functional requirements

**Branching**
- FR1. `branch_create/list/switch/delete` as tools + REST + UI; names match
  `[a-z0-9][a-z0-9-_/]{0,63}`; deleting the current or default branch is a
  `validation_error`.
- FR2. Per-(project, client) current-branch state: each client identity
  (browser, `chat:<session>`, MCP agent id) has its own checked-out branch;
  the store resolves reads/writes against the caller's branch. Two agents on
  two branches of one project work concurrently without turn-lock conflicts
  (the per-project turn lock applies per-branch).
- FR3. Branch switch is O(working set), leaves `.cache/` valid (cache keys
  are content-addressed already — switching must reuse hits across
  branches), and never loses uncommitted state (every mutation is already a
  snapshot; switching requires a clean snapshot boundary).

**Versions (tags)**
- FR4. `version_tag` creates an immutable named version of the current
  state; tags are listable with author/date/message; `project_restore` and
  every `ref`-accepting tool/route accept a tag name.
- FR5. Tags cannot be deleted or moved once referenced by a release
  (PRD-015 forward-compatibility: the tag store records referrers).

**Merge**
- FR6. `merge_branch` performs: fast-forward when possible; otherwise a
  three-way merge — part scripts via git textual merge; `project.json` via a
  structure-aware driver that merges at the granularity of
  parts.<id>, assembly.instances.<id>, materials.<id>, pmi sections, and
  scalar project fields.
- FR7. Conflicts return as `{"error": {"type": "merge_conflict", "details":
  {"conflicts": [{path|key, ours, theirs, base}]}}` — never partially
  applied; the merge is staged until resolved or aborted (`merge_abort`).
- FR8. Non-conflicting concurrent edits merge cleanly: A edits part X, B
  edits part Y ⇒ both land; A edits script, B edits the same part's params
  ⇒ both land (different keys).
- FR9. Completed merges run a validation pass: rebuild changed parts,
  re-resolve mates, `check_interference`. Failures block the merge by
  default; `allow_invalid: true` lands it with the failure recorded in the
  merge commit message and returned to the caller.
- FR10. A merge is one history entry on the target branch recording both
  parents (real git merge commit) with attribution of the merging identity.

**Compatibility & integrity**
- FR11. The existing linear-history surface (`project_history`,
  `project_restore`, `undo`/`redo`, UndoCursor semantics) is unchanged
  per-branch; all 292+ existing tests keep passing.
- FR12. The project remains a standard git repository (`.history/` layout
  preserved); external `git log/diff/clone` sees true branches/tags. Derived
  data (`.cache/`, `exports/`) stays untracked.
- FR13. Byte-determinism guarantee is preserved: identical script+params on
  any branch hit the same mesh cache entry.

## Agent surface

New tools: `branch_create {project, name, from?}` · `branch_list {project}`
· `branch_switch {project, name}` · `branch_delete {project, name}` ·
`version_tag {project, name, message?}` · `list_versions {project}` ·
`merge_branch {project, source, target?, allow_invalid?}` ·
`resolve_merge {project, choices}` · `merge_abort {project}`.
Changed: `project_history`/`project_restore` gain optional `ref`.
New events: `branch_changed {project, client, branch}`,
`merge_completed {project, source, target, validation}`.
New error type: `merge_conflict` (details as FR7).

## Technical approach

Extends the existing per-project git engine (`agentcad/core/history.py` and
the `.history/` git-dir): branches/tags are native git refs; the snapshot-on-
`project_changed` hook commits to the client's current branch. New pieces:

- **Manifest merge driver** — pure function `merge_manifests(base, ours,
  theirs) -> (merged, conflicts)` in `agentcad/core/` with exhaustive unit
  tests; wired as the merge path for `project.json` (not a git
  `.gitattributes` driver — we orchestrate the three-way merge ourselves via
  `git merge-tree` or index-level plumbing, keeping the store in control).
- **Branch-aware store** — `ProjectStore` gains a ref-resolution layer keyed
  by the client-identity ContextVar (the same one turn-locking stamps);
  working-tree materialization per branch (either one worktree per branch
  under `.history/worktrees/` or check-out-on-switch; decide in design —
  worktrees favor fast switching and concurrent branch reads).
- **Tool pack** `tools_versioning.py` + route pack `routes_versioning.py`
  (extension-point contract; cores untouched).
- **UI** — toolbar branch switcher + versions dialog + conflict view
  (script conflict editor reuses CodeMirror; geometry side-by-side reuses
  the viewport in a modal split). Depends on PRD-026's dialog primitives if
  it lands first; otherwise ships with minimal styled modals.
- **Validation pass** reuses the service rebuild orchestration + existing
  interference handler; runs on the merged state before the merge commit is
  finalized (staged in a temp worktree).

Kernel untouched. Storage change: none to the manifest schema; `.history/`
gains refs — old projects upgrade transparently (their history becomes the
default branch).

## MVP & phasing

- **MVP:** branches + tags + switch (FR1–FR5, FR11–FR13), merge with the
  manifest driver and textual script merge, conflicts surfaced as structured
  errors, resolution via file write + `resolve_merge`, validation pass
  blocking. Toolbar switcher + a minimal conflict list UI (side-by-side
  geometry view can lag).
- **Phase 2:** the full conflict view (dual-viewport compare), pre-merge
  summary prediction, per-branch turn-lock refinement, merge of PMI/solid-
  materials edge cases.
- **Phase 3 (with PRD-002):** proposals consume merges; merge gating moves
  into the proposal flow.

## Acceptance criteria

- AC1. Two branches editing *different* parts merge with zero conflicts;
  the merged project builds green and the merge commit has two parents
  (verified by test using the rocketry example on a copy).
- AC2. Two branches editing the *same lines* of one script produce
  `merge_conflict` naming that part with ours/theirs/base; an agent
  resolves it via tools only (scripted test), and the merge completes with
  a validation report.
- AC3. Param-vs-script concurrent edits on one part merge cleanly (FR8
  second case, test).
- AC4. A merge that would introduce assembly interference is blocked by
  default and lands with `allow_invalid: true`, with the pair named in both
  cases (test).
- AC5. Tagging then mutating then restoring the tag round-trips
  byte-identically (manifest + scripts), and the tag survives branch
  deletion (test).
- AC6. Browser session: create branch, edit, switch back and forth, merge
  clean, see the result live — zero console errors, verified per the
  definition of done.
- AC7. Full suite green; `project_history`/undo behavior unchanged on the
  default branch (existing tests untouched).

## Risks & open questions

- **Working-tree strategy** (worktree-per-branch vs checkout-on-switch):
  worktrees cost disk and complexity but make concurrent branch access
  clean; decide in the design spec with a benchmark on a 30-part project.
- **Mid-rebuild switches:** switching while a rebuild is in flight must
  either drain or cancel; the kernel pool's affinity keys must incorporate
  branch to avoid cross-branch shape-LRU pollution.
- **Merge of `pmi`/`solid_materials`/`connectors`-dependent state** can be
  key-wise mergeable but semantically wrong (e.g., both sides renamed
  solids); validation pass is the backstop — document that semantic review
  belongs to PRD-002.
- **History bloat** from per-mutation snapshots × branches: measure; if
  needed add `git gc` housekeeping to the store (out of MVP).

## Competitive references

Onshape: workspaces/versions as the backbone; per-tab three-strategy merge,
last-writer-wins on conflict, no cherry-pick (market_research.md, "Onshape").
Git-based ECAD/MCAD attempts exist but none merge CAD *semantically* because
their models aren't text. We differ by: real three-way merges with real
conflicts, kernel-validated merge gates, and a project that is a plain git
repo any engineer can clone.
