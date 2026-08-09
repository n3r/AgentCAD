# PRD-003 — Design specs as executable tests

- **Status:** pending
- **Phase:** v4 — collaborative core
- **Created:** 2026-08-09
- **Origin:** competitive analysis (Aug 2026)
- **Depends on:** — (standalone; the v4 gates and v6 loops consume it)
- **Related:** PRD-002 (spec gates on proposals), PRD-004 (CI stage),
  PRD-015 ("released implies green"), PRD-019 (specs as objective
  functions), PRD-021 (DFM checks join the vocabulary), PRD-024 (scoring
  vocabulary)

## Problem & motivation

Design intent has nowhere to live as code. "Keep it under 120 g," "walls
never below 2.5 mm," "0.5 mm clearance to the chamber" are stated in chat,
tracked in spreadsheets, and silently violated three edits later — the
kernel validates that geometry *builds*, not that it *meets spec*. For
agents this is the missing half of the loop: the structured-error contract
tells an agent when it broke the model, but nothing tells it when it broke
the requirement.

The competitive evidence says this niche is ownerless: Valispace — the one
product that owned requirements↔engineering-budget traceability — was
absorbed into Altium (market_research.md, "The workflow ring"), and the gap
matrix scores executable design specs "implicit in tests — Valispace
(orphaned) — build-differentiated (design-tests, unclaimed)." No CAD ships
machine-checkable intent. Meanwhile the research consensus ("AI-native
CAD") is that validation, not generation, is the bottleneck — and a spec
layer is precisely what turns the kernel from a syntax referee into an
intent referee: TDD for hardware, where a human writes the spec and an
agent iterates geometry until green.

## Users & jobs

- **Design engineer (human):** pin budgets and constraints to the geometry
  they govern — versioned, diffed, merged like everything else — and see
  at a glance which are green.
- **Systems engineer (human):** trace a requirement id ("SYS-042") to the
  checks and parts that implement it, with live status.
- **Design agent:** receive intent as code, iterate until `run_specs` is
  green, and cite the report as evidence of done.
- **Reviewing engineer / proposal gate (PRD-002):** reject changes that
  violate declared intent without re-deriving it.
- **Optimization & bench harnesses (PRD-019, PRD-024):** use checks as
  objective functions and mechanical scoring.

## Goals

- G1. Intent is code: assertions over built geometry and assemblies live in
  part scripts (`SPECS`) and a project `specs.py`, with the same declared,
  validated contract as PARAMS.
- G2. Rebuild-time signal without friction: a failing spec is a warning
  during editing; it gates only at proposal/CI/release boundaries.
- G3. Requirement traceability: every check can carry a requirement
  id/URL, and the project answers "which geometry implements SYS-042 and
  is it green."
- G4. Graceful capability degradation: FEM-dependent checks skip honestly
  when the `[fem]` extra is absent — skips are data, never hidden.
- G5. One vocabulary, many consumers: the same check records drive the
  inspector chips, proposal gates (PRD-002), CI (PRD-004), release
  criteria (PRD-015), optimization objectives (PRD-019), and bench scoring
  (PRD-024).

## Non-goals

- Requirements management — we store requirement strings/URLs, not a
  requirements database or import pipeline.
- Check execution at change scale and reporting — PRD-004.
- DFM rules — PRD-021 (it registers new check kinds into this report
  shape).
- The optimization loop — PRD-019 (it binds objectives to these records).
- New tolerance modeling — PMI exists (`set_part_pmi`);
  `check_stackup` consumes the existing `tolerance_stackup` machinery.

## Experience

**Human path.** An engineer (or their agent) adds three lines to the nozzle
script:

```python
from agentcad.toolkit.specs import check_wall, check_mass, check_that

SPECS = [
    check_wall(min_mm=2.5, requirement="ENG-014"),
    check_mass(max_g=120, requirement="SYS-042"),
    check_that(lambda part, metrics: metrics["bbox"]["size"][2] <= 80.0,
               name="fits_fairing"),
]
```

and a project-level `specs.py` for assembly intent:

```python
from agentcad.toolkit.specs import check_clearance, check_interference_free

SPECS = [
    check_interference_free(),
    check_clearance("nozzle1", "chamber1", min_mm=0.5,
                    requirement="INT-003"),
]
```

The inspector shows per-part spec chips (green/red/grey-skip) that update
live on rebuild; a project Specs panel lists every check grouped by
requirement with measured-vs-limit; clicking a failing wall check reveals
the thin point's location. Nothing blocks editing — red is information
until a proposal tries to merge.

**Agent path.** The human states a budget in chat; the agent writes the
spec *first* (TDD), then iterates geometry: `set_params` → rebuild result
carries `specs: {failed: 1, checks: [...]}` → adjust → green. `run_specs
{project}` gives the full report on demand; `list_specs` reads declared
intent without building. In PRD-002's flow the agent cites the green
report as merge evidence.

**Handoff.** Specs written by either side bind both: an agent cannot
"forget" a budget stated last week, and a human sees an agent's
self-imposed constraints in plain code in the diff.

## Functional requirements

**Declaration**
- FR1. Part scripts may define `SPECS` as a list of records produced by
  `agentcad.toolkit.specs` constructors; a malformed `SPECS` fails the
  rebuild as a script error with `details.line`, exactly like a malformed
  `PARAMS`.
- FR2. A project may hold `specs.py` at its root defining `SPECS` over
  assembly instance ids; it is tracked, versioned, diffed, and merged like
  a part script, and executes under the same worker sandbox.
- FR3. v1 vocabulary: `check_valid()`, `check_wall(min_mm)`,
  `check_mass(min_g?, max_g?)`, `check_volume(min_mm3?, max_mm3?)`,
  `check_bbox(within_mm)`, `check_clearance(a, b, min_mm)`,
  `check_interference_free(min_volume_mm3?)`,
  `check_stackup(from_instance, to_instance, axis, within)`,
  `check_fem_static(fixed_face, load_face, load_N, max_vm_mpa?,
  max_disp_mm?)`, and `check_that(fn, name)` for arbitrary predicates over
  the built part + metrics. Every constructor accepts `requirement: str`
  (id or URL).
- FR4. Constructors are pure data with no kernel/OCP dependency —
  declaration is data, evaluation is the kernel's job; a `check_fem_*`
  spec declares cleanly on a machine without `[fem]`.

**Evaluation**
- FR5. Part-level specs evaluate on every rebuild; the rebuild payload
  gains `specs: {passed, failed, skipped, checks}`. A failing spec never
  fails the rebuild — geometry lands, the failure is signal (the warnings
  tier). Parts without `SPECS` incur zero added work.
- FR6. Each check result is `{name, kind, status: pass|fail|skip|error,
  measured, limit, requirement?, location?, message}`; `location` is a
  world point when the underlying analysis yields one (the wall check's
  thinnest point does today).
- FR7. `run_specs {project, part_id?}` evaluates everything (or one part):
  part specs plus project specs, returning the full report with
  per-requirement grouping (`requirements: {<id>: {checks, status}}`).
- FR8. Skip semantics: `check_fem_*` without the `[fem]` extra returns
  `{status: "skip", reason: "fem_extra_missing", hint}` — the `run_specs`
  tool itself always registers (the never-show-unrunnable-tools rule
  governs tools; skips are data a CI `--strict` mode can escalate). Checks
  needing booleans or walls on mesh-only reference parts skip with
  `reason: "mesh_only"`.
- FR9. A spec that itself crashes (bad predicate, unknown instance id)
  reports `status: "error"` with traceback details — distinct from a
  failing check and from a build error.
- FR10. Results are deterministic and cacheable under the content-hash
  discipline: unchanged script + params + spec inputs reuse the stored
  result (PRD-004's speed depends on this).

**Gating & traceability**
- FR11. A service seam `evaluate_specs(project, ref?) -> {status:
  green|red|skip, failures, skips}` returns gate-shaped status for any ref
  — the shape PRD-002 renders in its Checks tab and PRD-004 posts.
- FR12. Requirement strings flow end to end: declared → evaluated →
  grouped in `run_specs` → visible in the Specs panel. A requirement with
  zero checks does not exist to us (no requirements database — non-goal).
- FR13. UI: per-part spec chips in the inspector, live on rebuild events;
  a project Specs panel grouped by requirement showing measured vs limit.

## Agent surface

New tools: `run_specs {project, part_id?}` · `list_specs {project,
part_id?}` (declared specs with requirements, no evaluation).
Changed: rebuild-returning tools (`update_part_script`, `set_params`,
`set_solid_materials`) gain the `specs` summary in their post-state.
No new error type: spec failures are data in reports; malformed
declarations are the existing script-error family; gate consumption
belongs to PRD-002/PRD-004.

## Technical approach

- **Toolkit module** `agentcad/toolkit/specs.py` — pure-data constructors
  importable from part scripts (re-exported from `toolkit/__init__.py`),
  zero kernel imports.
- **Worker handler pack** `agentcad/kernel/handlers/specs.py` — evaluates
  part-level checks against the built shape via the toolbox (`metrics`
  plus the wall/section machinery the analysis pack already has);
  `check_that` predicates run inside the sandboxed worker with the built
  part and metrics dict.
- **Service orchestration** — project-level checks reuse existing paths:
  interference via the `check_interference` machinery, stack-ups via
  `tolerance_stackup`, FEM via the fem handler pack when present. One
  genuinely new geometry op: a `clearance` handler (minimum distance
  between two placed shapes via BRepExtrema) joins the analysis pack.
- **Tool pack** `agentcad/core/tools_specs.py` + **route pack**
  `agentcad/server/routes_specs.py`; `evaluate_specs` lands as a service
  seam (like `mates.resolve`), not a fork of service internals.
- **Frontend**: chips + Specs panel in `inspector.js`; viewport location
  markers deferred.
- **Storage**: none — part specs live in scripts and `specs.py` is a
  tracked file, so PRD-001 versions, diffs, and merges intent for free.
  The `CHEATSHEET` in `templates.py` gains a SPECS section
  (authoring-facing surface change).

## MVP & phasing

- **MVP:** constructors (valid, wall, mass, volume, bbox, clearance,
  interference_free, that) + part `SPECS` + project `specs.py` evaluation
  + `run_specs`/`list_specs` + rebuild summaries + inspector chips; the
  rocketry example ships real specs.
- **Phase 2:** `check_stackup` + `check_fem_static` with skip semantics,
  the requirement-grouped panel, result caching (FR10), thin-point
  markers in the viewport.
- **Phase 3:** the `evaluate_specs` gate consumed by PRD-002 (red gate on
  proposals) and PRD-004 (CI stage); PRD-019 binds objectives to the same
  records; PRD-021 registers DFM packs as new check kinds in this report
  shape.

## Acceptance criteria

- AC1. The rocketry example ships specs — chamber mass budget, nozzle wall
  minimum, flange bolt-circle clearance — green as shipped; thinning the
  nozzle wall via `set_params` turns `run_specs` red naming `check_wall`
  with measured vs limit and the thin point's location (test on a copy).
- AC2. A rebuild with a failing spec still lands geometry and returns
  `ok: true` with the failure in `specs` (test).
- AC3. `check_fem_static` reports skip + hint without `[fem]` and
  evaluates with it (paired tests, `importorskip` pattern; suite green
  without the extra).
- AC4. Project `specs.py`: the clearance check reports the measured
  minimum distance for a too-close pair, and `check_interference_free`
  names the offending pair (tests).
- AC5. A `check_that` predicate that raises reports `status: "error"` with
  a traceback, not a crashed rebuild (test).
- AC6. Requirement strings group correctly in the `run_specs` report, and
  `list_specs` returns declarations without triggering any build (tests).
- AC7. `evaluate_specs(project, ref)` returns green for a tagged good
  state and red for a branch with a broken budget (test over PRD-001
  refs).
- AC8. Spec chips render and live-update in a real browser session on
  rebuild, zero console errors.
- AC9. Full suite green (count cited); rebuild latency for spec-less parts
  is unchanged — spec evaluation provably skipped when no `SPECS` exist
  (test).

## Risks & open questions

- **Predicates are arbitrary code** — the trust model is unchanged (part
  scripts already execute in the sandboxed worker); document that specs
  run confined and never in the server process.
- **Instance-id coupling** — project specs name assembly instances; a
  rename breaks the spec, surfaced as `status: "error"` naming the
  missing id — honest, not silent. A rename-refactor tool could later
  rewrite both sides.
- **Severity is contextual, not declared** — rebuild warns, boundaries
  gate. Open question: does a per-spec `advisory: true` flag (reported,
  never gating) earn its place? Defer until a real user asks.
- **Clearance cost** — BRepExtrema on complex pairs can be slow; the
  per-request kernel timeout applies; measure and document limits.
- **Vocabulary sprawl** — every analysis wants a `check_`. Rule: a
  constructor lands only when its measurement already exists as a
  handler; PRD-021 is the sanctioned expansion path.

## Competitive references

Valispace owned requirements↔engineering-budget traceability and was
absorbed into Altium — the niche is ownerless (market_research.md, "The
workflow ring"). Onshape and SolidWorks ship no executable design intent;
budgets live in spreadsheets that silently diverge from the model.
Simulation tools check physics, not intent. The gap matrix calls
design-tests "unclaimed — build-differentiated." We differ: specs are code
in the same repo as the geometry, evaluated by the same kernel that
referees every change, and they double as the agent's termination
condition (PRD-018/019), the merge gate (PRD-002/004), and the benchmark
scoring vocabulary (PRD-024) — TDD for hardware, not a requirements
database.
