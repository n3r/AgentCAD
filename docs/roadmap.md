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

## Sequencing decision — the marketplace chain (16 Aug 2026)

**Decision: the marketplace moves from "someday in v6" onto the critical
path, and a minimal hosted slice of PRD-005 is lifted out of the deferred
deployment work to unblock it.**

The reasoning, recorded because it reorders three phases:

*The software is the copyable part.* A competent team with coding agents can
reproduce this feature set. What compounds is a catalog of kernel-validated,
mate-ready parametric parts plus a format others adopt — and an audience.
That is the durable asset; thesis 2's validation loop is a *feature* until it
is attached to a network, at which point it becomes the network's quality bar.

*But the asset and the storefront are separable, and PRD-031 fuses them.*
The asset needs none of 031's four hard dependencies: PRD-011's MVP already
supports **git-hosted indexes — a repo is an index**, so a validated catalog
can start compounding with no cloud, no auth and no sandbox. The storefront
(identity, listings, moderation, economy) is where all four blockers live and
is the commodity half.

*There is a technical fork inside that split.* PRD-031's safety inversion —
marketplace code never executes on a consumer's machine — is precisely what
forces cloud + sandbox. A git-hosted registry has the opposite model: code
runs locally, like pip. Normal for a developer tool, unacceptable for a
consumer marketplace. Two distribution models, and only the first is
reachable today.

*Audience comes from share links before it comes from a market.* PRD-031's
own cold-start mitigation is the PRD-007 customizer loop. A part that becomes
a URL someone else opens is the growth surface; an empty shelf is not.

*A gap between format and open publishing is healthy, not a delay.* Changing
a published format breaks every pinned consumer. The seeded catalog is the
format's proving ground.

**Resulting order** (supersedes the phase ordering below where they conflict):

| # | Step | Why it is here |
|---|---|---|
| 1 | **011, registry-first** — **DONE (PR #15)** | Shipped: the content-addressed package format, the content-verified cache and lockfile, local **and git-hosted** indexes (this repository serves its own catalog via `subdir: "catalog"`), the nine-stage publish gate, `agentcad package validate` / `agentcad publish`, seven tools, the Library dialog, and a **nine-package COTS catalog**. The preset ↔ configuration schema is frozen and **PRD-012 FR1 is amended to it**. Licensing settled (Apache-2.0, repo + catalog). Step 2 inherits: the publish gate is explicitly **not** a security boundary — 006 is still the backstop. |
| 2 | **005-lite → [PRD-005a](prd/completed/PRD-005a-hosted-core.md)** — **DONE (PR #17)** | Deploy + identity + public read only. Orgs, roles, audit principals and local-first sync stay deferred as genuine deployment work. Carved out on 17 Aug 2026 as its own PRD (031a/031b's letter-suffix precedent) so folder-as-status stays truthful for both halves. Its design settles the question the sequencing forced: **without PRD-006 an account is arbitrary code execution on the host**, so registration is closed, roles are not a boundary between members, and the anonymous surface is nine pre-generated, provably kernel-free entries. |
| 3 | **007** — **DONE (PR #20)** | Share links & customizer — the growth loop, and 031's own hard dependency. Needs hosting, not full multi-tenancy. Inherits from 005a, all four now shipped and testable: the `PUBLIC_PATHS`/`PUBLIC_PREFIXES` allowlist with the reachable-set **equality** test (007's own AC9, delivered early — grow `EXPECTED_PUBLIC` and the `NOT_YET_BUILT` subtrahend together, and note that `is_public` is `startswith`, so `/s/` must carry its slash); the route-pack `PREFIX` seam that lets `/s/<token>` mount at the root without another `app.py` edit; `presence.TokenBucket` as the rate-limit primitive (007 is its second consumer and should promote it to its own module); and the recorded verdict that **bounded params on a member-authored script is a different threat from arbitrary upload** and is shippable before 006. `routes_public.py` is the worked example of a kernel-free anonymous pack. |
| 4 | **[031a](prd/completed/PRD-031a-marketplace-catalog.md)** — **DONE (PR #21)** | Public read-only catalog we seed, with add-to-library. Needs 011 + 005a + 007. The browse payload is already pre-generated: 005a serves `catalog/index.json`'s metadata and shipped previews anonymously, filtered to indexes whose `scope` is `public`; add-to-library is the existing authenticated `add_package`/`use_part` path. **Shipped:** anonymous search/listing/script/params (kernel-free) + the one listing customizer (PRD-007 containment reused, shared `customizer_guard`) + a kernel-free mesh read + `market_install` + the Market UI. AC1–AC8 machine-checked; AC9 graded as evidence (no browser). |
| 5 | **006** | Sandboxing becomes blocking only when third-party code runs on our servers — which is 031b, not 031a. |
| 6 | **031b** | Open publishing, verified tiers, moderation, economy. |

Demoted behind that chain: **013, 014, 015, 017** (daily-driver depth —
they buy credibility but do not compound). **026/027** stay early-v5 movable:
if we are inviting an audience, the shell is the shop window.

Success metric for the catalog is **usefulness, not contributors**. We will
not out-community GrabCAD's 7M engineers; we can plausibly out-*availability*
McMaster and TraceParts on mate-ready parametric parts, because agents author
and the kernel referees — and that bet is winnable without a contributor base.

**Blocker resolved (16 Aug 2026):** the repo had no LICENSE file and no
license field (surfaced when a GPL-3.0 solver was declined in PRD-009).
Founder decision: **Apache-2.0** for both the repository and the seed catalog
packages — `LICENSE` + `pyproject.toml` fields landed with the PRD-011 design
commit. The 031a licensing precondition is closed.

## PRD index

### v4 — the collaborative core

| PRD | Feature | Status | Origin | Depends on |
|---|---|---|---|---|
| [001](prd/completed/PRD-001-branching-version-control.md) | Branching version control — branches, immutable versions, semantic merge with real conflicts, kernel-validated merge gates | completed (PR #8, AC1–AC7 verified) | analysis | — |
| [002](prd/completed/PRD-002-change-proposals-geometric-diff.md) | Change proposals & geometric diff — CAD pull requests with review packets (diffs, metric deltas, renders, 3D add/remove volumes) | completed (PR #9, AC1–AC9 verified) | analysis | 001 |
| [003](prd/completed/PRD-003-design-specs-executable.md) | Executable design specs — machine-checkable intent (`check_wall`, `check_mass`, clearances, stack-ups) with requirement traceability | completed (PR #10, AC1–AC9 verified) | analysis | — |
| [004](prd/completed/PRD-004-geometry-ci.md) | Geometry CI — `agentcad check` + GitHub Action: rebuild, specs, interference, drawings on every ref/proposal | completed (PR #11, AC1–AC10 verified) | analysis | 001 · 003 |
| [005](prd/pending/PRD-005-multi-tenant-cloud.md) | Multi-tenant cloud — **the remainder after the 005a carve-out**: orgs, workspaces, per-project roles, audit principals, OIDC/passkeys, per-tenant fair scheduling, local-first git sync, signed desktop builds | pending — deferred; the carve-out is recorded FR-by-FR in the PRD's header | analysis | 005a · 006 |
| [005a](prd/completed/PRD-005a-hosted-core.md) | Hosted core ("005-lite") — one deployable instance (Dockerfile + compose), invite-only identity (sessions for browsers, bearer tokens for agents), and an enumerated, kernel-free public-read surface | completed (PR #17); AC1–AC11 verified except AC3's browser half, graded as evidence (no Chrome extension was available in any session — see PRD and changelog 0197) ([design](superpowers/specs/2026-08-17-hosted-core-design.md) · [plan](superpowers/plans/2026-08-17-hosted-core.md)) | founder decision (16 Aug 2026) | 011 · 008 |
| [006](prd/pending/PRD-006-sandboxing-quotas.md) | Cross-platform sandboxing & quotas — Linux/Windows confinement, cgroup budgets, per-tenant metering | pending — **step 5**, blocking only for 031b (third-party code on our servers). Until it lands, an account on a 005a instance is arbitrary code execution on the host (005a design, Decision 1) — hence closed registration | analysis | — |
| [007](prd/completed/PRD-007-share-links-customizer.md) | Share links & customizer publishing — read-only viewer links; published parts with parameter sliders emitting B-rep artifacts | completed (PR #20); AC1–AC9 verified, the two browser ACs graded as evidence | analysis + idea 1e | 005a (006 not required for our own content — see 005a's design, Decision 2) |
| [008](prd/completed/PRD-008-review-threads-presence.md) | Review threads & presence — comments anchored to faces/params/lines/diffs; per-part claims; per-user undo | completed (PR #12, AC1–AC9 verified) | analysis | 005 (soft) |

### v5 — daily-driver depth & the ecosystem

| PRD | Feature | Status | Origin | Depends on |
|---|---|---|---|---|
| [009](prd/completed/PRD-009-sketcher-v2.md) | Sketcher v2 — arcs/splines/ellipses/slots/conics, full constraints, drag-to-solve, DOF diagnostics | completed (PR #13, AC1–AC7 verified) | analysis + residual | — |
| [010](prd/completed/PRD-010-feature-toolkit-ii.md) | Feature toolkit II — patterns, ISO/ANSI hole wizard with flowing metadata, ribs/draft, sheet-metal v2 (relief, partial flanges) | completed (PR #14, AC1–AC8 + AC7b verified) | analysis + residual | — |
| [011](prd/completed/PRD-011-parts-library-registry.md) | Parts library & package registry — "pip for parts": versioned, kernel-validated packages; git-hosted indexes; a seeded COTS catalog; McMaster ingestion | completed (PR #15, AC1–AC9 verified) | analysis + idea 1d | 003 |
| [012](prd/completed/PRD-012-configurations.md) | Configurations — named parameter sets with per-config metrics/BOM/drawings; matrix builds | completed (PR #18, AC1–AC9 verified) ([design](superpowers/specs/2026-08-17-configurations-design.md) · [plan](superpowers/plans/2026-08-17-configurations.md)) | analysis | — |
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
| [031](prd/pending/PRD-031-marketplace.md) | Marketplace & community hub — validated parametric components/projects/skills; server-side execution only; provenance & disclosure | pending — **split: 031a seeded read-only catalog (step 4) · 031b open publishing (step 6)** | idea 1e | 031a: 011 · 005a · 007 — 031b: + 006 |
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
scheduled exhaustive macOS coverage; single-binary packaging; a then-42-tool
agent surface (45 with `[fem]`) over MCP, chat, and REST — 85/88 today, and
this line is a v3 snapshot, not a current count.

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
