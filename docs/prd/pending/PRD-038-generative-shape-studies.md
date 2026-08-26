# PRD-038 — Generative shape studies

- **Status:** pending
- **Phase:** v6 — generative engineering
- **Created:** 2026-08-25
- **Origin:** founder direction from the Aug-2026 UX study ("use
  generative algorithms to generate the best and most optimal shape for
  parts") + competitive analysis
- **Depends on:** PRD-019 (studies/optimizers — hard) · PRD-003 (specs
  as constraints — completed) · the `[fem]` tier (objective evaluation)
  — soft: PRD-010 (toolkit generators — completed), PRD-018 (candidate
  UX precedent), PRD-022 (cloud burst), PRD-035 (study container)
- **Related:** PRD-034 (the result is a feature), PRD-030

## Problem & motivation

"Make this part lighter but keep it strong" is the canonical generative
ask, and the UX study validated its product shape: a dialog (objective ·
keep-constraints · load case · material) returning candidate geometries
with mass and safety factor, one click to apply, undoable. What
incumbents sell under "generative design" is a cloud topology-optimizer
black box that returns un-editable mesh blobs — the opposite of this
product's thesis, where every result must be a kernel-validated,
parametric, *reviewable* artifact. We can deliver the ask honestly by
inverting the approach: generate within **parametric shape families**
(spoke/rib/window/lattice lightening patterns, shell-and-rib substitution
— the PRD-010 toolkit vocabulary), drive them with PRD-019's optimizers
against PRD-003 specs and FEM objectives, and return candidates that are
ordinary script features with provenance. Free-form topology optimization
stays a later, cloud-burst tier — additive, not foundational.

## Users & jobs

- **Engineer:** state the objective and the invariants; compare
  candidates on real numbers; apply one and keep editing — it is a
  feature, not a fait accompli.
- **Non-expert maker:** "make it lighter/stronger/cheaper" without
  knowing which ribs to draw.
- **Agent:** run studies as tools mid-conversation ("2 kN must hold,
  minimize mass") and defend the choice with the study's evidence.
- **Reviewer:** a generative change arrives as a proposal whose packet
  shows the study, the candidates, and why this one (PRD-002).

## Goals

- G1. A generative study object: objective (minimize mass / maximize
  stiffness-per-mass / minimize cost via PRD-021 when present),
  keep-constraints (faces/features that must survive — bore, bolt
  pattern, mounting planes), a load case (a PRD-035 study or inline),
  material, and a candidate budget.
- G2. Candidates are parametric: each is a shape-family instantiation
  with its own parameters, built and validated by the kernel, scored by
  the declared objective + spec set; failures are reported, not hidden.
- G3. Apply = a script feature (PRD-034) carrying study provenance
  (study id, family, winning parameters), one undo step; the study
  persists for re-runs after geometry drift (PRD-035's staleness rules).
- G4. The candidate comparison is honest: real per-candidate metrics
  (mass, σ_max, SF, cost basis), evaluation tier labeled (analytic
  screen vs FEM-verified), no renders standing in for numbers.
- G5. Extensible families: shape families are toolkit-pack citizens
  (PRD-010's extension point), so new strategies (gyroid infill panels,
  bridge trusses) arrive as packs — including from the marketplace
  eventually (PRD-031 disclosure rules apply).

## Non-goals

- In-house free-form topology optimization (SIMP/level-set) or
  high-fidelity solver loops — the roadmap's solver non-goal stands;
  a cloud-burst topology tier may arrive via PRD-022 later, returning
  *reference* geometry that a family fit then re-parameterizes.
- Mesh-blob outputs of any kind — everything applied is B-rep from
  script, or it does not apply.
- Multi-part/assembly-level generative layout — single-part scope here.

## Experience

Design mode, Generate ▸ Generative study: the dialog collects objective,
keeps (picked like PRD-035 fixtures), load case, material, budget. Run
streams progress ("family spoke: 6 candidates · screening · FEM top 3");
results land as a candidate board — geometry thumbnail, family, mass,
SF, Δ vs baseline, evaluation tier chip. Apply inserts `Generative1`
into the tree/timeline (PRD-034), rebuilds, updates checks; the study
stays in the part's study list, going stale honestly when the part
changes. An agent runs the same loop via tools and typically finishes by
opening a proposal whose packet embeds the board (PRD-002). With
PRD-019's Pareto reporting, multi-objective studies show the front
rather than a single winner.

## Functional requirements

- FR1. Study definition persisted (a `generative` study type beside
  PRD-035's — one container, shared staleness/hashing).
- FR2. Family registry: a shape family declares applicability (what
  geometry it can host), its parameter space, and its keep-constraint
  handling; ships with at least spokes, radial ribs, circular windows,
  and perimeter-shell lightening; families are toolkit-pack extensions.
- FR3. The candidate loop is PRD-019 machinery: sampling/optimization
  over family parameter spaces, two-stage evaluation (analytic screen:
  mass + section heuristics; FEM verification for the shortlist via the
  load case), spec set enforced (a candidate failing any declared spec
  is marked failed, shown, and unapplicable).
- FR4. Keep-constraints are structural: a candidate that modifies a
  kept face/feature is rejected by the kernel-measured check, not by
  family convention.
- FR5. Apply emits curated-style script (the family's generator call
  with the winning parameters) as one feature + one undo step, with
  provenance recorded in the feature and the study.
- FR6. Every candidate row carries: metrics, evaluation tier, build
  time, and — when cost models exist (PRD-021) — cost with basis.
- FR7. Budget honesty: candidate counts and FEM verifications respect
  a declared budget; exhaustion reports "screened N, verified M,
  stopped by budget" (the PRD-004 `--budget` posture).
- FR8. Tools: `run_generative_study {project, part_id, definition}` ·
  `get_generative_study {id}` · `apply_generative_candidate {id,
  candidate}` — post-state returns; events `study_changed`.
- FR9. Studies run on the kernel pool with `affinity=part_id`, serial
  per part (the PRD-012 build-path precedent: no fan-out).

## Agent surface

The three tools; structured errors (`family_inapplicable {reason}`,
`keeps_unsatisfiable`, `budget_exhausted {screened, verified}`,
`solver_unavailable`). Study results are fully structured so an agent
can argue trade-offs without screenshots.

## Technical approach

Service: `generative` study type in `core/studies.py`'s container +
`tools_generative.py`; families as toolkit modules (kernel-side
generators, PRD-010's pack seam) with a service-side registry mirroring
the gate-provider pattern; evaluation reuses PRD-019 executors and
`[fem]` handlers; scripts emitted through PRD-034's curated-style
writer. Frontend: the dialog + candidate board in Design mode; board
thumbnails from existing server renders.

## MVP & phasing

- **MVP (after 019):** the four built-in families on rotationally/
  prismatically symmetric parts; analytic screen + FEM shortlist;
  candidate board; apply-as-feature; the three tools.
- **Phase 2:** cost objectives (021), Pareto boards (019), proposal
  packet embedding (002), family packs from third parties.
- **Phase 3 (022):** cloud topology-reference tier with family
  re-parameterization; assembly-aware keeps (joints as constraints).

## Acceptance criteria

- AC1. On the flange-class example: a minimize-mass study with kept
  bore + bolt pattern returns ≥ 3 valid candidates with distinct
  families, real mass/SF numbers, and at least one FEM-verified row;
  applying the winner drops mass, keeps checks green, and lands one
  undoable feature whose script re-parses (PRD-034 round-trip).
- AC2. A keep-violating family instantiation is rejected by the
  kernel-measured check and shown as failed (negative test).
- AC3. Changing the objective or material changes the candidate
  ranking (no fixed outcomes — parity test on two configurations).
- AC4. Budget exhaustion mid-study reports honest partial results and
  exit-2 semantics under CI.
- AC5. `apply_generative_candidate` from MCP equals the UI apply
  (script diff parity).
- AC6. Full suite green; without `[fem]`, studies degrade to
  analytic-only with the tier labeled (both directions tested).

## Risks & open questions

- **Family generality:** four families cover plates/flanges/brackets,
  not everything; the registry + applicability contract keeps honesty
  ("no applicable family" is a valid answer). Measure applicability
  across the catalog.
- **Objective gaming:** analytic screens can mis-rank; FEM-verify the
  shortlist always, and label tiers loudly.
- **Where 018 ends and 038 begins:** 018 generates *parts from tasks*;
  038 optimizes *shapes of existing parts*. Shared candidate-board UX
  should be one component — design review aligns them.

## Competitive references

Fusion's generative design and nTopology prove demand and price the
compute; both output artifacts hostile to downstream editing
(market_research.md, physics/generative deep dive). Our inversion —
candidates as parametric, kernel-validated, reviewable script features
with honest evaluation tiers — trades peak topological freedom for
editability, reviewability, and CI-gradability, which is the product's
whole bet.
