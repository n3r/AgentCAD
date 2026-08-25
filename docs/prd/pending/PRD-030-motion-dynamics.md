# PRD-030 — Motion & dynamics: kinematics, forces, and mechanism testing

- **Status:** pending
- **Phase:** v6 — moats (kinematics tier can start late-v5)
- **Created:** 2026-08-09
- **Origin:** founder idea #6 (Aug 2026), engineering-reviewed; grounded by dedicated research (market_research.md, "Physics & motion"). Promotes the former "full kinematic solver" non-goal into a staged plan.
- **Depends on:** PRD-013 (richer joints + URDF — hard) · PRD-003 (dynamic results as spec checks — soft) · PRD-020 (long runs as jobs — soft)
- **Related:** PRD-019 (studies over mechanism params), PRD-022 (sim burst for what we don't build), PRD-025 (Test mode hosts it)

## Problem & motivation

Today AgentCAD answers "does it fit and clear?" — mates pose parts,
`sweep_motion` drives one DOF and boolean-checks interference per sample.
It cannot answer the next questions every mechanism raises: does the
four-bar actually reach both positions? What torque must the motor supply?
What force lands on the hinge pin — and does the bracket survive it?
Founder idea #6 ("kinetics or physics to test moving parts") is exactly
this gap.

The research (market_research.md, "Physics & motion") makes the build
decision clear. SolidWorks Motion (embedded ADAMS) defines the canonical
workflow — motors, springs, contact, torque/power plots, and joint
reactions exported as FEM loads — while Fusion's motion is kinematic-only
and Onshape's answer was to bridge into NVIDIA Isaac Sim (Mar 2026),
validating the CAD-mates→sim path our URDF export anticipates. Among
engines, **MuJoCo** is the fit: Apache-2.0, a pip wheel that drops into our
sandboxed worker model, monthly releases, closed chains via MJCF equality
constraints (which URDF cannot express), soft convex contact, and inverse
dynamics that directly answers motor sizing. Drake's hydroelastic contact
is the credible second tier; Bullet is fading; PhysX/Rapier mismatch.

## Users & jobs

- **Mechanism designer:** verify range of motion with closed chains,
  size motors ("peak 0.42 N·m at 35°"), find peak pin loads, and hand
  those loads to FEM — without leaving the project.
- **Robotics user:** the same mates that exported URDF now simulate;
  results match what Isaac/MuJoCo downstream will see.
- **Agent:** run "prove this mechanism works under load" as a tool chain:
  kinematics → dynamics → worst-case frames → `fem_static` — and gate it
  in specs ("hinge reaction ≤ 500 N through the cycle").
- **Reviewer:** a proposal touching a mechanism carries its motion
  evidence (clear/blocked, torque curve, peak loads).

## Goals

- G1. **Kinematics v2 (pose solving):** simultaneous multi-joint
  assemblies — closed chains (four-bars, sliders-cranks, parallel
  linkages) solved for consistent poses across a driven sweep; reachable/
  locked/singular diagnostics. Exact B-rep interference checking stays the
  clearance referee (existing `&`-based path) at solved poses.
- G2. **Dynamics v1 (rigid-body):** gravity, springs/dampers, motors
  (position/velocity/torque-driven), joint friction and limits, convex
  contact — returning joint reaction forces/torques, required actuator
  torque over the cycle (inverse dynamics), contact-force timelines, and
  motion trajectories for viewport playback.
- G3. **Loads→FEM handoff:** extract worst-case frames (peak reactions +
  inertial state) into linear-static FEM load cases (with inertia relief)
  — the Motion→Simulation workflow, one tool call.
- G4. Results as evidence: motion runs are persisted artifacts (setup +
  curves + verdicts) surfaced in the Test mode (PRD-025), citable in
  specs (PRD-003) and proposals (PRD-002).
- G5. Honest fidelity labeling: sweep (exact B-rep, quasi-static) vs
  dynamics (convex proxies, rigid bodies) — every result names its tier
  and its geometry approximation.

## Non-goals

- Flexible-body dynamics, transient/explicit FEA (impact/crash), fatigue,
  nonlinear materials — burst to external solvers (PRD-022) or defer;
  the research is unambiguous that rigid-body + static-FEM handoff covers
  our users' questions.
- RL-scale GPU simulation farms (MJX/Newton) — not our product; URDF/MJCF
  export keeps the door open.
- Cams/gears as *force-transmitting contact pairs* at MVP (gear coupling
  as a kinematic ratio lands with PRD-013; tooth-contact forces are a
  later fidelity step).
- Building or forking a physics engine.

## Experience

**Human path.** In the Test workspace, "Motion" creates a study on the
assembly: pick driven joints (from mates) and a drive profile (sweep,
constant speed, torque), add gravity (default on), optional springs/
dampers at joints, friction defaults per material pair. Run → the viewport
plays the trajectory (reusing the existing frames-playback machinery from
`sweep_motion`); a results rail shows: clearance verdict (from B-rep
checks at sampled poses), torque-vs-angle curve per motor, reaction curves
per joint, contact events. "Send peak loads to FEM" creates the load case
and opens the FEM setup prefilled.

**Agent path.**
`solve_poses {project, drives: [{joint, range}], samples}` →
per-sample joint states + diagnostics (closed-chain aware).
`simulate_motion {project, drives|motors, duration|cycle, gravity?,
springs?, friction?}` → run id + summary {clear, peak_torques,
peak_reactions, contact_events}; `get_motion_run {id}` → full curves.
`extract_load_case {run, criterion: "peak_reaction", joint?}` →
FEM-ready case; chain into `fem_static`. All long runs submit as jobs
(PRD-020) with progress events.

## Functional requirements

**Kinematics v2**
- FR1. Closed-chain pose solving over the mate graph (PRD-013 joint set):
  driven DOFs swept, dependent DOFs solved; per-sample convergence status;
  locked/singular states reported with the offending constraint named
  (structured error details, agent-actionable).
- FR2. `sweep_motion` compatibility: existing single-DOF behavior and
  payload unchanged; multi-joint sweeps extend the same result shape
  (frames map per instance) so viewport playback needs no rework.
- FR3. Exact-geometry interference at solved poses stays available
  (per-sample boolean checks, mesh-kind skips preserved).

**Dynamics v1**
- FR4. Model translation: mates/joints → MJCF (hinge/slide/ball; equality
  constraints for loops; limits from connector ranges); mass/inertia from
  existing per-part metrics; collision geometry via convex decomposition
  (CoACD-class) cached per part content hash — never raw tessellation
  (research: proxy quality is the correctness knob).
- FR5. Actuation & elements: position/velocity/torque motors on driven
  joints; joint springs/dampers; per-pair friction defaults from material
  metadata (PRD-028) with overrides.
- FR6. Outputs per run: joint reactions (force/torque, full timeline),
  required actuator torque via inverse dynamics for prescribed motion,
  contact events (pair, time, peak force), trajectories at fixed sample
  rate; run artifact persisted under the project (input spec + results,
  content-hashed).
- FR7. Determinism & bounds: fixed-seed, fixed-timestep runs are
  reproducible; wall-clock/step budgets enforced by the worker (existing
  timeout discipline); divergence (NaN/explosion) returns a structured
  error naming the first bad frame and likely cause (contact stiffness,
  timestep) via the Error Doctor pattern.
- FR8. Fidelity labels: every result payload carries `{tier: "sweep" |
  "dynamics", geometry: "brep" | "convex-proxy", engine, version}`.

**Handoff & evidence**
- FR9. `extract_load_case` produces a `fem_static`-compatible case (fixed
  faces from mate anchors, loads at joint locations, inertia relief flag)
  and records provenance (run id, frame, criterion).
- FR10. Spec checks: `check_motion_clear {drives…}`,
  `check_peak_reaction {joint, max_n}`, `check_actuator_torque {motor,
  max_nm}` join the PRD-003 vocabulary; CI-runnable (PRD-004) within
  budgets.
- FR11. Test-workspace UI: study setup, playback, curves (SVG plots),
  verdict chips; runs listed with staleness against geometry hash.

## Agent surface

New tools: `solve_poses` · `simulate_motion` · `get_motion_run` ·
`extract_load_case` · spec checks per FR10. Extended: `sweep_motion`
(multi-joint). Events: `motion_run_started/finished {run, summary}`.
Dynamics tools register only when the `[motion]` extra (mujoco +
decomposition dep) is installed — the FEM-pack capability rule.

## Technical approach

- **Placement:** dynamics runs inside the kernel-worker sandbox (mujoco is
  a pip wheel; no network, CPU-bound, killable — fits the existing
  worker/timeout/respawn model). A dedicated handler pack
  (`kernel/handlers/motion.py`) owns MJCF translation + run loop; convex
  decomposition as a cached kernel step next to LOD tiers.
- **Kinematics v2** extends `_mates_resolver` (loop closure via the same
  joint math or via MuJoCo's constraint solver in kinematic mode — decide
  in design with a four-bar benchmark; MuJoCo-for-poses avoids a second
  solver).
- **Packs:** `tools_motion.py`, `routes_motion.py`, Test-rail frontend
  module; optional-extra gating mirrors `[fem]` exactly.
- **Artifacts:** runs under `<project>/.motion/` (derived, cache-keyed,
  untracked like `.cache/`), summaries in results payloads; load-case
  provenance in the manifest (additive).
- **Second tier later:** Drake hydroelastic behind the same tool surface
  when contact-fidelity demand materializes (engine field already in
  payloads); external burst (PRD-022) for everything in Non-goals.

## MVP & phasing

- **MVP (kinematics v2):** closed-chain pose solving + multi-joint sweeps
  + diagnostics + interference at poses (no new extra needed if solved in
  resolver; else ships with the extra).
- **Phase 2 (dynamics v1):** `[motion]` extra, MJCF translation, gravity/
  motors/springs/friction/contact, reactions + inverse-dynamics torque,
  run artifacts, Test-rail UI + playback, `extract_load_case` → FEM.
- **Phase 3:** spec checks + CI budgets; studies integration (PRD-019:
  optimize link lengths against torque); Drake tier evaluation.

## Acceptance criteria

- AC1. A four-bar linkage example solves a full crank revolution with
  consistent poses (loop closure error < 1e-6 m) and reports the toggle
  positions; the same model with an unreachable geometry returns a
  structured diagnostic naming the joint (tests).
- AC2. Slider-crank under gravity + velocity-driven crank: required
  crank torque curve matches the analytic solution within 5% (validation
  test — the FEM-pack precedent of validating against theory).
- AC3. A latch mechanism reports a contact event with peak force; the
  extracted worst-case load case runs `fem_static` and the pin bracket
  stress appears (integration test).
- AC4. `check_actuator_torque` gates a proposal red when a mass increase
  pushes the motor past its limit (spec + CI test with PRD-003/004).
- AC5. Same seed + same inputs ⇒ identical run artifact hash (determinism
  test); a deliberately unstable setup fails with the divergence
  diagnostic, not a hang (budget test).
- AC6. Without the `[motion]` extra: dynamics tools absent from the
  registry, kinematics v2 still works, suite green (capability test).
- AC7. Viewport playback of a dynamics run on the engine example's
  crank works in the browser at interactive rate (verified session).

## Risks & open questions

- **Contact realism vs expectation:** convex proxies mislead on
  conforming contacts (gear teeth, cam profiles) — mitigations: fidelity
  labels (FR8), per-pair proxy-quality overrides, and explicit "not for
  tooth forces yet" docs; Drake tier is the upgrade path.
- **Kinematic solver choice** (extend resolver math vs MuJoCo-in-kinematic
  -mode) — benchmark both on four-bar + parallel-gripper fixtures in the
  design spec; criteria: robustness at singularities, dependency weight.
- **Friction defaults** from materials are rough; label as estimates,
  require overrides for spec-gating checks.
- **Run artifact size** (timelines × samples): cap sample rates, store
  compressed, summarize by default.
- **Two sources of truth for mass** (metrics vs MJCF) — single-source
  from the metrics pipeline, tested equality.

## Competitive references

SolidWorks Motion defines the workflow (ADAMS solver, reactions→FEM);
Fusion motion is kinematic-only; Onshape bridges to Isaac Sim rather than
shipping dynamics (market_research.md, "Physics & motion"). We differ:
open engine (MuJoCo) inside the same sandboxed, deterministic,
structured-error kernel discipline as everything else; results as
persisted, spec-gateable evidence; and the agent runs the whole ladder —
sweep → dynamics → FEM — as one conversation.
