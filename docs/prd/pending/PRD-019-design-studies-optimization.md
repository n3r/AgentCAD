# PRD-019 — Design studies and optimization

- **Status:** pending
- **Phase:** v6 — generative engineering & the manufacturing bridge
- **Created:** 2026-08-09
- **Origin:** competitive analysis (Aug 2026)
- **Depends on:** PRD-003 (hard — objectives/constraints bind to specs) ·
  PRD-012 (hard — reuses the config-matrix build machinery) · PRD-020 (soft —
  background execution) · PRD-006 (soft — compute quotas)
- **Related:** PRD-002 (winning candidate lands as a proposal), PRD-004 (CI
  can pin a study result), PRD-018 (generate-then-optimize composes)

## Problem & motivation

Sizing questions — "how thin can this wall go before the first mode drops
under 120 Hz?", "which of these two params actually drives mass?" — are
answered today by hand: an agent (or human) tweaks a param, rebuilds, reads
metrics, repeats, serially, inside a chat transcript. The pieces of a real
answer already exist (typed PARAMS with bounds, deterministic rebuilds, a
parallel kernel pool, metrics after every mutation, FEM tiers), but nothing
composes them into a study.

The incumbent answer to "optimization" is generative design: expensive
black-box topology optimization producing organic meshes — the wrong tool
for our targets, who mostly need *explainable sizing* over parameters they
already named. The competitive evidence (market_research.md, "The workflow
ring"): nTop sells headless automation (nTop Automate) as an enterprise SKU
on an effectively $10k+/seat product — engineers pay for computable design
iteration; SimScale markets agentic setup→run→evaluate; and physics-AI
surrogate companies (PhysicsX $300M, Neural Concept $100M) are built on
exactly the mass-generated labeled geometry a script-native CAD produces as
a side effect. Meanwhile ("Where AgentCAD wins" #2, #4) the kernel already
referees every candidate, headless license-free execution makes a 200-build
sweep economically trivial where per-seat incumbents make it impossible,
and the deterministic cache makes repeated and refined sweeps nearly free —
a structural advantage no incumbent regeneration pipeline can claim.

## Users & jobs

- **Design engineer (human):** ask a trade-off question in one call —
  "minimize mass subject to first mode > 120 Hz and wall ≥ 2 mm" — and get a
  Pareto front instead of an afternoon of manual param bisection.
- **Analyst (human):** run a DOE to find which parameters matter before
  committing to a design direction.
- **Optimization agent:** set up studies from natural language, watch
  convergence, narrate trade-offs, and feed findings back into the spec
  layer ("margin exists — tighten the mass budget").
- **Reviewing human:** judge the winning candidate as an ordinary proposal
  (PRD-002) with the study report as its evidence.

## Goals

- G1. Parameter sweeps, DOE sampling, and scipy-driven optimization over
  typed PARAMS run as first-class jobs — one call in, a study artifact out.
- G2. Objectives and constraints bind to real evaluators: metrics
  (`get_metrics`), analysis kinds (`analyze_part`), specs (PRD-003), and FEM
  (`fem_static`/`fem_modal`/`fem_thermal` when the `[fem]` extra is present).
- G3. Multi-objective studies produce Pareto fronts; every study produces a
  report artifact (tables + renders + charts) reproducible from its spec.
- G4. The winning candidate is promotable to a proposal in one action — the
  study's conclusion enters the project through review, not by side effect.
- G5. Sweeps are cheap: candidate evaluation parallelizes across the kernel
  pool and memoizes on the deterministic content-hash cache, so re-running
  or refining a study only builds new points.
- G6. Studies never mutate working state: candidates evaluate ephemerally
  (param overrides at build time), and only promotion writes anything.

## Non-goals

- Topology/free-form optimization — the incumbents' black box; out per the
  roadmap non-goals (interop later if demanded).
- Surrogate model training — the data flywheel is real but a later product;
  studies emit archivable candidate records to keep the option open.
- CFD/nonlinear/high-fidelity sim in the loop — burst to cloud solvers via
  PRD-022; built-in FEM stays the fast sanity tier.
- A full statistics workbench (RSM, kriging, sensitivity indices beyond
  basic effects) — later phases; v1 is sweep, LHS/DOE, and scipy optimize.

## Experience

**Human path.** A Studies panel: pick a part, choose parameters and ranges
(pre-filled from PARAMS bounds), state objectives and constraints (pickers
over metrics, spec names, FEM quantities), choose a sampler, run. Progress
streams into the job tray (PRD-020). Results view: a sortable candidate
table, a Pareto scatter for two-plus objectives (click a point → its render,
metrics, spec chips), and the study report. "Promote winner" applies that
candidate's params on a branch and opens a proposal (PRD-002) whose packet
embeds the study report.

**Agent path.** `run_study {project, part_id, params, objectives,
constraints?, sampler?, budget?}` — e.g. params `{wall: {min: 2, max: 5},
expansion_ratio: {min: 3, max: 8}}`, objectives `[{metric: "mass_g", goal:
"min"}, {fem_modal: {mode: 1}, goal: "max"}]`, constraints `[{spec:
"nozzle_wall_min"}]`. The tool (or background job) yields the candidate
table, Pareto set, and report path; `promote_candidate` turns row *k* into a
proposal. The agent narrates trade-offs from the table — exactly the
structured data an LLM reasons over well.

**Handoff.** The study report is shared evidence: humans read the same
tables and scatter the agent cites; the promoted proposal carries the
provenance.

## Functional requirements

**Study definition**
- FR1. A study spec is data: part (or config family from PRD-012), parameter
  domains (continuous ranges, int steps, enum choices — honoring each
  param's `type` and bounds from `params_spec`), objectives (min/max over
  metric paths, analysis results, spec margins, FEM outputs), constraints
  (spec references or inline `check_*` predicates), sampler + budget.
  Malformed specs are `validation_error`s naming the field.
- FR2. Samplers v1: full-factorial grid, latin-hypercube (n samples), and
  scipy optimizers — `differential_evolution` (global), SLSQP/COBYLA/
  Nelder-Mead (local, warm-startable from a given point). Enum/int
  dimensions are handled by enumeration × continuous optimization; the
  chosen strategy is recorded in the report.
- FR3. Referencing a FEM objective without the `[fem]` extra is a
  `validation_error` with the install hint — consistent with the
  tools-only-when-runnable philosophy.

**Evaluation**
- FR4. Candidates evaluate ephemerally: param overrides applied at build
  time (the PRD-012 config-matrix path), never written to the manifest; a
  candidate build failure (fillet blowup, invalid geometry) marks that point
  infeasible with its structured error attached — it never aborts the study.
- FR5. Evaluation parallelizes across the kernel pool with per-candidate
  affinity keys (the service's current `affinity=part_id` routing would
  serialize a single-part sweep onto one worker — the study runner keys
  affinity by candidate to spread the load).
- FR6. Candidate results memoize by the deterministic cache identity
  (content hash × params × material): repeated points, re-runs, and refined
  studies skip already-evaluated candidates.
- FR7. Two-stage evaluation when FEM objectives are present: cheap
  metric/spec screening first, FEM only on survivors (configurable off).

**Results**
- FR8. Every candidate record: params, feasibility, objective values,
  constraint margins, per-spec pass/fail, and (for Pareto members / top-k)
  a `render_view` thumbnail.
- FR9. Multi-objective studies compute the non-dominated (Pareto) set;
  single-objective studies rank. An all-infeasible study returns `ok` with
  an empty feasible set and the binding constraints named — not an error.
- FR10. The study report is an artifact (`exports/studies/<id>/report.json`
  + `report.md`): spec echo, environment (versions, pack sizes), candidate
  table, Pareto set, charts, renders. Identical study spec + code version ⇒
  identical candidate table (determinism test).
- FR11. `promote_candidate` applies the winning params via a branch +
  proposal (PRD-001/002); single-user fallback applies via `set_params`
  (normal history snapshot, undoable). The proposal description links the
  study report.
- FR12. Studies run foreground (small budgets) or as PRD-020 background
  jobs: cancellable (clean stop, partial results kept and marked partial),
  resumable (already-evaluated candidates skipped via FR6), quota-metered
  (PRD-006).

## Agent surface

New tools: `run_study {project, part_id?, config_set?, params, objectives,
constraints?, sampler?, budget?, background?}` · `get_study {project,
study_id}` · `list_studies {project}` · `promote_candidate {project,
study_id, candidate}` · `cancel_study {project, study_id}` (thin alias over
PRD-020's `cancel_job`).
New events: `study_progress {project, study_id, done, total, feasible,
best}` · `study_completed {project, study_id, report}`.
Errors: `validation_error` (malformed spec, FEM-without-extra); infeasible
outcomes and candidate build failures are results, not errors.

## Technical approach

- **Study runner** — `agentcad/core/studies.py`: sampler drivers, the
  evaluation queue over the kernel pool (`service` rebuild orchestration
  with ephemeral param overrides — the PRD-012 seam), memo store, Pareto
  computation, report writer. scipy is already in the dependency tree (the
  sketch solver's `least_squares` rides it), so optimizers add no install
  weight.
- **Tool pack** `agentcad/core/tools_studies.py` + **route pack**
  `agentcad/server/routes_studies.py`; cores untouched.
- **Evaluators** — objectives/constraints resolve through existing paths:
  metrics from the rebuild result, `analyze_part` kinds, PRD-003's
  `run_specs` evaluation, and the FEM handler pack (`kernel/handlers/fem.py`)
  behind the extra. No new kernel handlers.
- **Persistence** — study specs + candidate records under the project
  (`.studies/` beside `.cache/`, untracked derived data except the report,
  which is an export artifact); memo keys are the existing content-hash
  identity so cross-study reuse is free.
- **Background execution** — a `study` job kind registered with PRD-020
  (progress, cancel, resume, quotas); foreground mode runs the same runner
  inline with events.
- **Frontend** — Studies panel + results view; the Pareto scatter is a small
  dependency-free SVG/canvas module (no bundler, consistent with the
  frontend's vendored-modules discipline).

Kernel untouched; the manifest untouched (studies never write it — only
`promote_candidate` does, through the normal set-params/proposal path).

## MVP & phasing

- **MVP:** grid sweep + LHS + one scipy optimizer over a single part;
  objectives/constraints on metrics + specs; ephemeral parallel evaluation
  with memoization; report artifact (JSON + markdown); foreground with
  progress events; `promote_candidate` via direct `set_params`.
- **Phase 2:** FEM objectives with two-stage screening; background jobs +
  cancel/resume (PRD-020); Pareto scatter UI; promotion as branch +
  proposal (PRD-001/002); config-family studies (PRD-012).
- **Phase 3:** assembly-level studies (objectives over `check_interference`
  margins, stack-ups), DE/global tuning, candidate-archive export for the
  surrogate flywheel, spec-margin feedback suggestions.

## Acceptance criteria

- AC1. The nozzle study — mass vs. first-mode frequency across `wall` and
  `expansion_ratio` — produces a Pareto front and a winning proposal, driven
  entirely through tools with no chat transcript (integration test on a
  rocketry copy; FEM legs `importorskip`, suite green without the extra).
- AC2. Re-running the identical 20-point sweep evaluates zero new kernel
  builds (memo hit count asserted) and finishes an order of magnitude
  faster (timed).
- AC3. A 12-candidate sweep on a 3-worker pool distributes across all
  workers (pool instrumentation), and a mid-study cancel stops cleanly with
  partial results marked partial and the part's stored params untouched.
- AC4. A study whose constraints exclude the whole domain returns `ok`,
  `feasible: []`, with the binding constraint named — no exception (test).
- AC5. A candidate whose geometry fails to build is recorded infeasible with
  its structured error, and the study completes (test with a
  fillet-radius domain that includes a known-failing region).
- AC6. Identical study spec run twice ⇒ identical candidate tables
  (determinism test); the report records versions and the study spec echo.
- AC7. Browser session: define the nozzle study, run, watch progress,
  explore the Pareto scatter, open a candidate's render, promote — zero
  console errors.
- AC8. "Which sizes violate the mass budget?" over a PRD-012 config family
  answers in one `run_study` call with per-config spec results (integration
  once PRD-012 lands).

## Risks & open questions

- **Noisy/discontinuous response surfaces** (fillets fail in sub-regions,
  metrics jump at topology changes) break local optimizers. Mitigation:
  infeasible-point handling (FR4), DE as the default global strategy,
  multi-start; grid/LHS + Pareto documented as the honest default.
- **FEM cost in the loop:** minutes per candidate swamps a sweep.
  Mitigation: two-stage screening (FR7), pool parallelism, background jobs,
  FEM legs opt-in.
- **Pool contention with interactive work:** a 200-build study can starve a
  human's rebuild. Mitigation: PRD-020's fair scheduler with reserved
  interactive headroom; foreground studies capped small.
- **Mixed-integer domains** (int/enum params) have no gradient. Mitigation:
  enumeration × continuous strategy (FR2); combinatorial budget math shown
  in the report.
- **Over-trusting single-case FEM:** a study can optimize to a load case
  that isn't the critical one. Mitigation: reports restate the evaluated
  cases and carry the materials caveat ("typical figures, not design
  allowables") verbatim.
- **Open question:** where multi-part studies draw the mutation boundary
  (per-part overrides vs. assembly-level params) — decide with PRD-012/013.

## Competitive references

Fusion generative design: cloud-credit topology optimization, organic mesh
output, black box (market_research.md, "The desktop incumbents"). nTop:
implicit-modeling optimization at $10k+/seat with headless Automate as an
enterprise SKU ("The workflow ring"). Onshape: in-assembly linear-static
sim, no optimization ("Cloud-native CAD: Onshape"). SimScale: agentic
sim-in-the-cloud, disconnected from the authoring loop. None offer
explainable sizing over the designer's own named parameters, bound to
executable specs, with results as reviewable proposals — that combination
(white-box objectives, spec constraints, deterministic-cache economics,
proposal-native output) is ours alone.
