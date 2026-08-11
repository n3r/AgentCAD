# Roadmap

The goal: **a world-class cloud (and open-source) CAD system where humans
and AI agents work as peers.** This document is the index of everything we
intend to build, one PRD per feature. The evidence behind it is
[market_research.md](market_research.md) (August 2026: the competitive
landscape plus deep dives on materials data, native CAD import,
marketplaces, and physics engines); the requirements live in
[prd/](prd/) — see [prd/README.md](prd/README.md) for the template and
conventions.

**Status model.** A PRD's folder is its status: `prd/pending/` → not
started · `prd/in-progress/` → in its design/spec/build cycle ·
`prd/completed/` → acceptance criteria verified and merged. The index below mirrors the
folders; both move in the same commit. When a feature is picked up it still
runs the house process (brainstorming → design spec → implementation plan
under `docs/superpowers/`); the PRD is that process's input.

## How the August 2026 founder ideas shaped this roadmap

Eight idea clusters were reviewed against the competitive analysis and the
existing plan. Every one landed — some as new PRDs, some strengthening
features already planned, all engineering-reviewed rather than copied:

| # | Founder idea | Engineering review | Landed in |
|---|---|---|---|
| 1a | Build tab | Workspaces are views over one model substrate, not separate documents; Build hosts the modeling surfaces | PRD-025 (+ 009/010/016/026) |
| 1b | Production tab with swappable modules (3DP vs machining…) | The "swappable module" is a **process profile**: an open DFM rule pack + cost model + output pipeline per process — exactly our pack architecture | PRD-025 + PRD-021 + PRD-022 |
| 1c | Test tab (loads, heat, flows…) | One evidence rail unifying specs, analysis, FEM, motion; flows stay burst-to-cloud | PRD-025 + PRD-003/019/030 |
| 1d | Library of reusable models | The personal/org layer of the package registry; mate-ready parts, versioned | PRD-011 (+ 025 surface) |
| 1e | Marketplace for people and agents | "Community McMaster-Carr": kernel-validated parametric code with engineering outputs; **code never executes on consumers' machines** (the safety inversion) | PRD-031 (+ 007 seed) |
| 2 | Vastly expanded materials | Reframed from "almost all possible" to *credible*: 300–1,000 generic materials with per-value provenance and basis labels (typical/minimum), temperature tables, process metadata — bulk-importing licensed DBs is legally excluded | PRD-028 |
| 3 | Powerful manual CAD with optional AI | The house inversion holds: every GUI operation emits script edits; full sketcher + feature vocabulary + direct-ops make manual work first-class | PRD-009 + PRD-010 + PRD-016 |
| 4 | Skills that teach agents to build | Versioned, loadable knowledge packs — project/org/core layers, marketplace-distributable, bench-measured | PRD-029 |
| 5 | Projects/parts/assembly UI at scale | Folders, tags, search, thumbnails, bulk ops, dashboard, 1k-row virtualization | PRD-027 |
| 6 | Kinetics/physics for moving parts | Promoted from a former non-goal into a staged plan: closed-chain kinematics → MuJoCo rigid-body dynamics (forces, motor sizing) → worst-case loads into FEM | PRD-030 |
| 7 | Import from all CAD systems | Honest three-tier plan: deep neutral formats built-in · ODA converter opt-in · cloud conversion connectors with consent — feature history doesn't survive translation *anywhere*, and we say so | PRD-032 (+ 017) |
| 8 | Revamp menus, panels, modals | Real dialog system, ⌘K command palette over the same registry agents use (human/agent parity as an invariant), panel layout manager, shortcuts | PRD-026 |

What the ideas didn't cover — and the analysis says is the wedge — stays
the spine of the plan: the **v4 collaborative core** (branches, CAD pull
requests, executable specs, geometry CI, cloud with local-first sync,
review threads). It is what makes a mixed human+agent team safe and is the
part incumbents structurally cannot copy.

## The thesis (unchanged)

1. **The unit of collaboration is the change.** Onshape merges
   last-writer-wins over unreviewable binary deltas; our model is code, so
   branches, semantic diffs, real conflicts, review, and CI are possible —
   GitHub-for-CAD, where half the committers are agents and the kernel
   referees every merge.
2. **The validation loop is the moat.** Benchmarks and incumbent alphas
   agree: generation is cheap, validated engineering is the bottleneck.
   v4+ extends kernel refereeing from "does it build" to "does it meet
   spec, pass review, and survive manufacturing rules."
3. **The window is open but closing.** Onshape Labs promises permissioned
   agents and a FeatureScript MCP; Autodesk ships MCP servers. Bolted-on.
   Ours is native — the ordering below ships it while that's still unique.

## PRD index

### v4 — the collaborative core

| PRD | Feature | Status | Origin | Depends on |
|---|---|---|---|---|
| [001](prd/completed/PRD-001-branching-version-control.md) | Branching version control — branches, immutable versions, semantic merge with real conflicts, kernel-validated merge gates | completed (PR #8, AC1–AC7 verified) | analysis | — |
| [002](prd/completed/PRD-002-change-proposals-geometric-diff.md) | Change proposals & geometric diff — CAD pull requests with review packets (diffs, metric deltas, renders, 3D add/remove volumes) | completed (PR #9, AC1–AC9 verified) | analysis | 001 |
| [003](prd/completed/PRD-003-design-specs-executable.md) | Executable design specs — machine-checkable intent (`check_wall`, `check_mass`, clearances, stack-ups) with requirement traceability | completed (PR #10, AC1–AC9 verified) | analysis | — |
| [004](prd/completed/PRD-004-geometry-ci.md) | Geometry CI — `agentcad check` + GitHub Action: rebuild, specs, interference, drawings on every ref/proposal | completed (PR #11, AC1–AC10 verified) | analysis | 001 · 003 |
| [005](prd/pending/PRD-005-multi-tenant-cloud.md) | Multi-tenant cloud — auth, orgs, roles, audit principals, local-first git sync, one-compose self-host | pending | analysis | — |
| [006](prd/pending/PRD-006-sandboxing-quotas.md) | Cross-platform sandboxing & quotas — Linux/Windows confinement, cgroup budgets, per-tenant metering | pending | analysis | — |
| [007](prd/pending/PRD-007-share-links-customizer.md) | Share links & customizer publishing — read-only viewer links; published parts with parameter sliders emitting B-rep artifacts | pending | analysis + idea 1e | 005 · 006 |
| [008](prd/in-progress/PRD-008-review-threads-presence.md) | Review threads & presence — comments anchored to faces/params/lines/diffs; per-part claims; per-user undo | implemented (AC1–AC9 verified, branch `prd-008-review-threads`) | analysis | 005 (soft) |

### v5 — daily-driver depth & the ecosystem

| PRD | Feature | Status | Origin | Depends on |
|---|---|---|---|---|
| [009](prd/pending/PRD-009-sketcher-v2.md) | Sketcher v2 — arcs/splines/ellipses/slots/conics, full constraints, drag-to-solve, DOF diagnostics | pending | analysis + residual | — |
| [010](prd/pending/PRD-010-feature-toolkit-ii.md) | Feature toolkit II — patterns, ISO/ANSI hole wizard with flowing metadata, ribs/draft, sheet-metal v2 (relief, partial flanges) | pending | analysis + residual | — |
| [011](prd/pending/PRD-011-parts-library-registry.md) | Parts library & package registry — "pip for parts": versioned, kernel-validated packages; org/personal libraries; McMaster ingestion | pending | analysis + idea 1d | 003 |
| [012](prd/pending/PRD-012-configurations.md) | Configurations — named variants with per-config metrics/BOM/drawings; matrix builds | pending | analysis | — |
| [013](prd/pending/PRD-013-assembly-v2.md) | Assembly v2 — sub-assemblies, instance patterns, simplified reps for 1k+ instances, richer joints, exploded views, URDF export | pending | analysis + idea 6 | — |
| [014](prd/pending/PRD-014-drawings-v2.md) | Drawings v2 — ASME/ISO sheets, title/revision blocks, assembly drawings with BOM+balloons, sections, PDF, deterministic regen | pending | analysis | 010 · 012 · 015 (soft) |
| [015](prd/pending/PRD-015-bom-release-management.md) | BOM & release management — structured BOMs, Rev approval on proposals, immutable release bundles | pending | analysis | 001 · 002 · 003 |
| [016](prd/pending/PRD-016-direct-modeling-ux.md) | Direct modeling & measurement UX — measure/sections/overlays, direct ops emitting code, selection-aware chat | pending | analysis + idea 3 | 026 (soft) |
| [017](prd/pending/PRD-017-interop-pack.md) | Interop pack (neutral) — STEP AP242 PMI export, 3MF metadata, glTF, structured assembly-STEP import, USD flag | pending | analysis | — |
| [025](prd/pending/PRD-025-workspaces-ia.md) | Workspaces — Build · Test · Produce · Library · Market over one model; process profiles as the "swappable modules" | pending | idea 1 | 026 |
| [026](prd/pending/PRD-026-workbench-shell.md) | Workbench shell revamp — dialog system (no native prompts), ⌘K palette over the registry, menus, resizable panels, shortcuts | pending | idea 8 | — |
| [027](prd/pending/PRD-027-project-navigation-scale.md) | Navigation at scale — folders, tags, search, thumbnails, bulk ops, project dashboard, virtualized trees | pending | idea 5 | 026 (soft) |
| [028](prd/pending/PRD-028-materials-database.md) | Materials database — 300–1,000 cited generic materials, basis labels, temperature tables, process metadata, community cards | pending | idea 2 | — |
| [029](prd/pending/PRD-029-agent-skills.md) | Agent skills & knowledge packs — loadable, versioned craft (core/org/project layers), bench-measured | pending | idea 4 | — |

### v6 — generative engineering, manufacturing & community

| PRD | Feature | Status | Origin | Depends on |
|---|---|---|---|---|
| [018](prd/pending/PRD-018-task-to-part-generation.md) | Task-to-part generation — kernel-grounded iterate-until-spec-green loop; multimodal; candidates; proposals out | pending | analysis | 003 · 002 (soft) |
| [019](prd/pending/PRD-019-design-studies-optimization.md) | Design studies & optimization — sweeps/DOE/optimizers over PARAMS with spec/FEM objectives; Pareto reports | pending | analysis | 003 · 012 · 020 (soft) |
| [020](prd/pending/PRD-020-jobs-fleet-orchestration.md) | Jobs & fleet orchestration — persisted queue, quotas, roles; agents coordinate only through branches/proposals | pending | analysis | 001 · 005 · 006 |
| [021](prd/pending/PRD-021-dfm-rule-packs-costing.md) | DFM rule packs & costing — open per-process rules run by the kernel with located violations; cost models; `check_dfm` as spec/CI gate | pending | analysis + idea 1b | 003 (soft) |
| [022](prd/pending/PRD-022-manufacturing-connectors.md) | Manufacturing connectors — instant quotes, slicer pipeline to sliced 3MF, scan-assist, sim burst | pending | analysis + idea 1b | 021 (soft) · 005 |
| [023](prd/pending/PRD-023-auto-documentation.md) | Auto-documentation — assembly instructions from mate semantics, READMEs, release notes; human-approved | pending | analysis | 013 · 015 |
| [024](prd/pending/PRD-024-agentcad-bench.md) | AgentCAD-Bench — public, kernel-scored agentic-CAD evals; our release gate | pending | analysis | 003 |
| [030](prd/pending/PRD-030-motion-dynamics.md) | Motion & dynamics — closed-chain kinematics; MuJoCo rigid-body dynamics (reactions, motor sizing); loads→FEM handoff | pending | idea 6 | 013 |
| [031](prd/pending/PRD-031-marketplace.md) | Marketplace & community hub — validated parametric components/projects/skills; server-side execution only; provenance & disclosure | pending | idea 1e | 005 · 006 · 007 · 011 |
| [032](prd/pending/PRD-032-universal-cad-import.md) | Universal CAD import — neutral-deep + ODA opt-in + consent-gated cloud conversion; fidelity reports; re-import diffs | pending | idea 7 | 017 |

Sequencing inside phases follows each PRD's dependency header; the phase
order is the strategy: collaboration core first (the wedge), daily-driver
depth second (adoption), moats third (compounding). PRD-026/027 are early
v5 (they unblock most UI work); PRD-030's kinematics tier and PRD-032's
Tier-1 formats can ride late v5.

## Shipped before the PRD system (v0.1 → v3)

The delivered base, summarized — details in `docs/changelog/0001–0062`:
script-as-model parts on the OCCT kernel with structured errors and the
Error Doctor; projects and assemblies with rigid/revolute/cylindrical mates,
driven-DOF motion sweeps, and interference checks; STEP/BREP/STL import;
2D drawings with detected dimensions and hole callouts; PMI/GD&T with
tolerance stack-ups; sheet metal with flat patterns; class-A surfacing
helpers with curvature verification; linear-static/modal/thermal FEM; a
30-material library; typed parameters; per-solid semantics; a GUI
constraint sketcher and face push/pull that emit script edits; server-side
renders for agent vision; git-backed undo/history; turn locks and
concurrent multi-agent sessions; mesh LOD streaming; macOS-sandboxed
execution; a fast macOS PR gate, focused Linux/Windows portability jobs, and
scheduled exhaustive macOS coverage; single-binary packaging; a 42-tool agent
surface (45 with `[fem]`) over MCP, chat, and REST.

## Deliberate non-goals

Evidence-backed exclusions (each traceable to
[market_research.md](market_research.md)):

- **Our own geometry kernel, CAD language, or B-rep foundation model** —
  the kernel graveyard, the DSL tax, and data-poor training runs are
  documented mistakes; OCCT + Python + integrated generation models win.
- **In-house CAM/toolpathing** — the handoff is STEP AP242 + PMI +
  standards drawings + DFM-checked quotes (PRD-021/022).
- **In-house high-fidelity solvers** (contact/nonlinear FEM, CFD,
  flexible-body/transient dynamics, fatigue) — built-in tiers stay fast
  sanity checks; fidelity bursts to cloud solvers (PRD-022, PRD-030).
- **Bulk-importing licensed materials databases** — legally excluded;
  curation with per-value citations instead (PRD-028).
- **Bundling proprietary CAD translators** (HOOPS/Parasolid/JT Open) —
  license-incompatible; consent-gated cloud connectors instead (PRD-032).
- **Points/rewards marketplace economies and client-side execution of
  marketplace code** — the farming and supply-chain-worm lessons (PRD-031).
- **Same-file CRDT co-editing as the collaboration foundation** — per-part
  concurrency + proposals deliver the value first (PRD-001/002/008).
- **Interactive Class-A sculpting UX** — surfacing toolkit + curvature
  analysis stand; sculpting is a product of its own.
- **Enterprise PLM ceremony, vault PDM, per-seat or metered-API pricing,
  public-documents free tiers, iframe app stores, VR concepting,
  mesh-generation text-to-3D** — competitor liabilities, not obligations.

*(The former "full kinematic/closed-chain solver" non-goal is retired —
promoted into PRD-030's staged plan at founder request.)*

## Working the roadmap

Pick the lowest-numbered unblocked PRD in the active phase unless priorities
say otherwise. Per feature: move the PRD to `in-progress/`, run
brainstorming → design spec → implementation plan (`docs/superpowers/`),
build in vertical slices behind the extension points, verify the PRD's
acceptance criteria, move to `completed/`, and update this index — same
commit, with its changelog entry.
