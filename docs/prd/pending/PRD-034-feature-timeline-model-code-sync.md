# PRD-034 — Feature timeline & model ⇄ code sync

- **Status:** pending
- **Phase:** v5 — daily-driver depth (the Design mode's core surface)
- **Created:** 2026-08-25
- **Origin:** founder direction from the Aug-2026 UX study (round 4, "the
  familiar workstation", validated in adversarial review) + founder idea #3
- **Depends on:** PRD-026 (shell — completed) · PRD-009/010 (feature
  vocabulary — completed) — soft: PRD-016 (direct ops), PRD-025 (mode frame)
- **Related:** PRD-003, PRD-008 (anchors), PRD-016, PRD-025

## Problem & motivation

The model *is* a build123d script — that is the product's thesis — but the
UI shows only two projections of it: a parameter list and raw code. Every
CAD veteran's mental model is missing: the **feature tree** (SolidWorks
FeatureManager, Fusion Browser, FreeCAD tree) and the **history timeline**
(Fusion). The UX study tested this directly: the winning mockup's Design
surface was tree + timeline + properties + a script drawer, and reviewers
called the *bidirectional* selection sync (click a feature → its geometry
and its script lines light; click geometry → its feature) the load-bearing
familiarity move. Without it, "old and new ways at the same time" fails for
the old way: a SolidWorks user cannot find "the chamfer" in forty lines of
Python. Agents gain equally — a structured feature list with source spans
is a far better anchor for review comments (PRD-008), selection-aware chat
(PRD-016), and edit targeting than raw line numbers.

## Users & jobs

- **CAD veteran:** navigate and edit the part through the tree/timeline
  they already know; never read Python unless they want to.
- **Code-first user:** keep writing the script; watch the tree follow.
- **Agent:** `get_feature_tree` returns features with param bindings and
  source spans — target edits at features, not text offsets; explain a
  model feature-by-feature.
- **Reviewer:** anchor a thread to "Chamfer1", not to a line that moves.

## Goals

- G1. Every part shows a feature tree and an ordered history timeline
  derived from its script — honestly: code that cannot be structured is
  shown as an opaque script node, never guessed at.
- G2. Selection is bidirectional and three-way: feature ⇄ script span ⇄
  geometry (faces/edges), in both the viewport and the code view.
- G3. Feature edits are script edits (the house inversion): a dialog on
  Extrude1 rewrites its literal in the script and rebuilds — modeless,
  next to the geometry, previewing live (the review explicitly rejected
  modal-over-scrim editing).
- G4. Creating features from the toolbar (primitives, holes, patterns,
  chamfers — the PRD-009/010 vocabulary) appends well-formed script in
  the curated style, so GUI-built parts remain clean code.

## Non-goals

- Parsing arbitrary Python into a full feature history — tiering below is
  the honest contract; no "best effort" trees that lie.
- A second model representation persisted beside the script — the script
  stays the single source of truth; the tree is a projection, recomputed.
- Free-orbit viewport work, measurement UX, sectioning — PRD-016.
- Assembly-level history (mates/instances have their own surfaces).

## Experience

The Design mode's left rail is the Browser: project → parts → bodies →
features, with visibility eyes and per-feature value chips (`Extrude1 ·
8 mm`). Under the viewport, the timeline strip shows the same features in
build order. Clicking either selects the feature: its faces highlight, the
script drawer (a FreeCAD-console-style collapsible panel) scrolls to and
highlights its span, and the properties panel shows its parameters.
Clicking geometry in the viewport selects the owning feature. Double-click
(or Edit) opens a modeless dialog docked by the properties panel — slider
moves preview geometry live; OK commits the script edit and rebuilds;
Cancel restores. Toolbar Create ▸ Cylinder asks d/h and appends the
feature. A part whose script defeats structuring shows one "script" node
with an honest explanation and still supports param-level editing.

**Agent path.** `get_feature_tree {project, part_id}` → features with ids,
kinds, params, source spans, face ids. `edit_feature {project, part_id,
feature_id, params}` rewrites the bound literals (same contract as
`set_params`: validation, post-state return). Chat selection context
(PRD-016) carries the selected feature id.

## Functional requirements

- FR1. **Tiered structuring, honest at every tier.** T1: scripts in the
  curated style (BuildPart blocks, toolkit calls, primitives, locations
  contexts, fillet/chamfer/pattern operations) parse via AST into an
  ordered feature list with param bindings and source spans. T2: authors
  may mark arbitrary regions with a structured comment annotation
  (`# @feature: name`) to name them in the tree. T3: anything else is one
  opaque script node. The tier is visible in the UI and in tool output.
- FR2. **Geometry provenance.** The worker records, per build, a mapping
  from features to the faces/edges they created or last modified
  (build123d label/provenance mechanisms; fallback: topological diffing
  between feature steps). Mapping rides the existing mesh/build payloads;
  absence (T3) degrades to whole-body highlight.
- FR3. Bidirectional selection: tree/timeline → viewport highlight +
  script span highlight; viewport pick → feature; script cursor inside a
  span → feature. All three stay in sync through rebuilds.
- FR4. Feature edit dialogs are modeless, previewing on input; commit is
  a script edit through the existing write path (undo/history, turn
  locks, `project_changed` — one undo step per commit, not per preview).
- FR5. Feature creation: at minimum Box, Cylinder, Extrude-from-sketch,
  Hole, circular/linear Pattern, Fillet, Chamfer append curated-style
  script; each lands in tree + timeline + selection.
- FR6. Suppress/unsuppress a feature (comment-out transform of its span,
  T1 only) with honest downstream invalidation.
- FR7. Reordering is **refused with an explanation** when the AST says
  dependencies forbid it; allowed reorders rewrite the script. (Fusion
  parity without pretending code has no data flow.)
- FR8. Tool surface: `get_feature_tree`, `edit_feature`,
  `add_feature {kind, params}`, `suppress_feature`; events:
  `feature_selected` (UI-follow), existing `rebuild_*` unchanged.
- FR9. Review threads (PRD-008) can anchor to a feature id; the anchor
  resolves through renames/re-parses by span+kind matching, orphaning
  honestly like face anchors do.
- FR10. The tree/timeline never blocks on the kernel: parsing is
  server-side and pure; provenance arrives with build results.

## Agent surface

`get_feature_tree {project, part_id}` → `{tier, features: [{id, kind,
label, params: {name → {value, span}}, span, faces}]}` ·
`edit_feature {project, part_id, feature_id, params}` → post-state ·
`add_feature {project, part_id, kind, params}` · `suppress_feature
{project, part_id, feature_id, suppressed}`. Errors are structured
(`feature_not_editable` carries the tier and reason). Registered
unconditionally (no optional dependency).

## Technical approach

- **Service:** a pure `core/features.py` (AST over the script text; no
  OCP import — the kernel boundary holds) producing the tree; a
  `tools_features.py` pack; script rewrites go through the same guarded
  write path as `set_params`/sketcher commits.
- **Kernel:** provenance capture in the worker build path (label pass
  over build123d objects; per-feature topological snapshot diff as
  fallback), returned as `feature_faces` beside the mesh.
- **Frontend:** Browser tree extends the existing sidebar; timeline is a
  new strip component; the script drawer reuses the CodeMirror editor
  read-only with span highlights; dialogs ride the PRD-026 dialog stack
  in a modeless variant.

## MVP & phasing

- **MVP:** T1 parsing for the curated style + examples; tree + timeline +
  three-way selection; modeless edit for literal-bound params; Cylinder/
  Box/Chamfer creation; `get_feature_tree`/`edit_feature`.
- **Phase 2:** provenance-based face mapping (replacing whole-body
  highlight), T2 annotations, suppress, the full creation vocabulary,
  thread anchoring.
- **Phase 3:** reorder-with-refusals, sketch-feature drill-in (opens the
  PRD-009 sketcher), agent-visible tier diagnostics in the Error Doctor.

## Acceptance criteria

- AC1. Browser session on the examples: tree + timeline render for a
  toolkit-style part; clicking a pattern feature highlights its holes,
  its script span, and its properties — and clicking a hole face selects
  the pattern (three-way, verified live).
- AC2. Editing an extrude depth via the dialog previews live, commits
  one undo step, rebuilds, and the script diff is exactly the literal.
- AC3. A deliberately unstructurable script shows the opaque node with
  the honest explanation; `get_feature_tree` reports `tier: "opaque"`.
- AC4. `edit_feature` from MCP produces the same script edit and
  post-state as the dialog (parity test).
- AC5. GUI-created Cylinder → the script diff is curated-style code that
  reparses to the same tree (round-trip test).
- AC6. Full suite green; parsing adds no kernel calls (asserted).

## Risks & open questions

- **AST fragility:** build123d idioms vary; mitigation is the tier
  contract plus a corpus test over every example and catalog part —
  measured coverage, not hope.
- **Provenance cost:** per-feature topology snapshots could slow builds;
  mitigation: capture only when the part is open in a browser session
  (client-driven flag), never in checks/CI/bench paths.
- **Two selection systems** (faces from PRD-008 anchors, features here)
  must share one highlight pipeline or they will fight — design review
  decides the owner.

## Competitive references

SolidWorks FeatureManager and Fusion's timeline are the muscle memory
being honored (UX study, familiarity map). FreeCAD proves tree+console
coexistence. Nobody else offers the inverse direction — a *code-first*
model whose tree is a projection — because nobody else's model is code;
that asymmetry (script stays authoritative, tree never lies) is the
differentiator, and the tier contract is what keeps it honest.
