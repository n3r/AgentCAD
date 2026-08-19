# PRD-024 — AgentCAD-Bench: public agentic-CAD evals

- **Status:** in-progress
- **Phase:** v6 — moats
- **Created:** 2026-08-09
- **Origin:** competitive analysis (Aug 2026)
- **Depends on:** PRD-003 (hard — specs are the scoring vocabulary)
- **Related:** PRD-018 (bench is its regression suite), PRD-004 (same
  headless harness; CI gating pattern)

## Problem & motivation

Nobody publishes agentic-CAD numbers. Zoo — the nearest architectural
rival, with a shipping Plan→Act→Observe agent — publishes zero evals;
the incumbents launch AI features with demo videos; academic benchmarks
(Text2CAD-Bench, MUSE, the BenchCAD lineage) exist, but no product
reports against them (market_research.md, "AI-native CAD: Zoo, AdamCAD,
Backflip, and the research frontier"). The gap matrix scores public
agentic-CAD evals "none — nobody publishes — build-differentiated (first
mover wins narrative)." Buyers cannot distinguish demos from capability,
and neither can we: without a benchmark, our own agent's regressions are
invisible until a user hits them, and PRD-018's generation loop has no
objective target.

The benchmark is only possible for us because scoring can be mechanical:
the kernel referees success — build validity, spec pass, geometric
match, interference — the same way it referees every product mutation.
A subjective (LLM-judged) benchmark would be just another marketing
artifact; a kernel-scored one sets the narrative and the quality bar
simultaneously, and open-source distribution makes it the reference
everyone else must report against. Classic first-mover leverage.

## Users & jobs

- **AgentCAD maintainers:** catch capability regressions before release;
  put an honest agent-capability number next to the test count.
- **External agent builders** (Claude via MCP, KCL-based systems,
  academic groups): a standard task set and a mechanical scorer to
  report against.
- **Evaluators, press, buyers:** reproducible comparisons instead of
  demo videos.
- **The generation loop (PRD-018):** its regression suite and objective
  function at release granularity.
- **The built-in chat agent:** the primary system under test.

## Goals

- G1. An open task suite — 25–40 tasks at v1 — across five categories:
  model-from-drawing, modify-to-spec, fix-the-broken-part,
  assemble-and-clear, optimize-under-constraints.
- G2. Scoring is 100% mechanical and kernel-refereed: geometry match,
  spec pass (PRD-003 vocabulary), interference, metric windows — no LLM
  judging anywhere.
- G3. `agentcad bench` runs the built-in agent and scores any submission
  deterministically; anyone can reproduce any published number.
- G4. External agents run the same tasks over MCP against the same tool
  surface; their submissions score identically.
- G5. A public leaderboard with full-disclosure rows; our own agent's
  score gates releases.

## Non-goals

- LLM-as-judge or human-panel scoring — subjectivity would forfeit the
  credibility that is the point.
- A training corpus or data flywheel — tasks are eval; contamination is
  a risk we manage, not a feature.
- Benchmarking GUI-driving (computer-use) agents — the unit under test
  is tool-surface competence.
- General CAD-understanding benchmarks (retrieval, segmentation) — the
  academic lanes exist; ours is the agentic loop.

## Experience

**Maintainer path.** `agentcad bench run --tasks core --agent builtin
--report out/` runs each task in a fresh scratch project under the
budgeted built-in chat loop; `out/` holds per-task `score.json`,
transcript, and the final project. `agentcad bench report out/` prints
the category table and writes markdown/HTML. CI runs the fast subset on
every release candidate; the release ships only when the score clears
the recorded baseline.

**External path.** An evaluator registers the MCP server (`claude mcp
add agentcad …`), hands their agent a task's prompt and assets, lets it
work against a scratch project, then submits the resulting project
directory: `agentcad bench score path/to/submission --task
modify_to_spec/007`. Scoring is offline, deterministic, and
agent-agnostic — the submission format is just the final project. A
leaderboard row is scores + versions + config + a link to the
reproducible submission.

**The benched agent's path.** Nothing special — the ordinary 73-tool
surface (42 with `[fem]`) on a scratch project, a task prompt, maybe an
attached drawing image. That is the design: the benchmark measures the
product surface as it ships.

## Functional requirements

**Tasks**
- FR1. Task bundle format under `benchmarks/tasks/<category>/<id>/`:
  `task.json` (schema-versioned: prompt, category, budgets `{wall_s,
  turns}`, scoring weights, frame declaration), optional `assets/`
  (drawing SVG/PNG/PDF, datasheet), optional `starter/` (the broken part
  for fix tasks, the parts for assembly tasks), and `reference/`
  (ground-truth STEP, expected metric windows, and a SPECS file in
  PRD-003 vocabulary).
- FR2. v1 ships 25–40 tasks, at least five per category, mixing authored
  tasks with tasks derived from the bundled examples. Every task's
  reference solution is validated in CI — the reference must score 1.0
  (self-test).
- FR3. Core tasks require neither `[fem]` nor network; an optional `fem/`
  category sits behind the extra with the standard skip semantics.

**Scoring**
- FR4. Per-task `score.json`: weighted subscores in [0, 1] — `built`
  (rebuilds clean), `valid` (per-solid validity), `specs` (PRD-003
  `run_specs` pass fraction against the task's SPECS), `geometry` (IoU
  against the reference), `interference` (assembly tasks: clean pairs),
  `metrics` (mass/bbox windows) — plus total, budget usage, and an
  over-budget flag. Weights come from `task.json`; the schema is
  versioned and published.
- FR5. Geometry IoU: a kernel scorer computes intersection and union
  volumes between candidate and reference B-rep — operator booleans
  (`&`), volumes via the solids-sum rule (`worker._shape_volume`) —
  after the task-declared frame alignment (default: as-built world
  coordinates; tasks may declare CoM/bbox normalization). References
  load through the existing STEP reference path (boolean-capable);
  mesh-only candidates score 0 on geometry with reason `skipped_mesh`.
- FR6. Scoring the same submission twice yields byte-identical
  `score.json`; results embed `{task_set, harness, agentcad}` versions
  and, for runs, `{model, agent_config}`.
- FR7. A boolean failure on a pathological candidate degrades honestly:
  the geometry subscore reports `{status: "error"}` and is excluded from
  the weighted total — never a harness crash, never a silent zero.

**Harness**
- FR8. `agentcad bench run {--tasks glob, --agent builtin, --report
  dir}`: a fresh scratch project per task (never the user's projects
  dir), the headless in-process service (the PRD-004 pattern), budget
  enforcement (wall clock + turn cap) — on overrun, score whatever is on
  disk and flag over-budget; transcripts persisted.
- FR9. `agentcad bench score <dir> --task <id>` scores any submission (a
  project directory) offline; `agentcad bench report <dir>` aggregates
  to a category table plus markdown/HTML.
- FR10. External-agent runs are a first-class documented path: scratch
  server + MCP registration + task handoff + submission layout; a
  worked walkthrough with Claude Code over MCP ships in docs and is
  exercised in CI (marked slow).
- FR11. CI gating: `bench report` compares against a checked-in
  `benchmarks/baseline.json`; regression beyond a declared epsilon
  exits non-zero — the release gate, and the standing regression suite
  for PRD-018's generation loop.

**Leaderboard**
- FR12. `agentcad bench publish <results-dir>` generates the static
  leaderboard page (rows: agent/model, versions, per-category + total,
  date, link to submission and transcript), published from the repo. A
  row without full disclosure — versions, config, reproducible
  submission — is not accepted.
- FR13. Launch bar (the roadmap's done-when): published results for the
  built-in agent plus at least two external setups (e.g., Claude over
  MCP and a KCL-based baseline), each reproducible from the published
  harness.

## Agent surface

Deliberately none. Benched agents use the ordinary tool surface —
adding bench-only tools would contaminate the measurement. The harness
is CLI-only (`agentcad bench run|score|report|publish`); the IoU scorer
is kernel-internal, never registered as a model-facing tool; no new
events or error types on the product surface.

## Technical approach

- **Package** `agentcad/bench/` — task loader, runner, scorer, report
  and leaderboard generators — reusing the headless service exactly as
  `agentcad check` does (the `cli.py` `_build_service` path); scratch
  projects under a temp root (the examples-run-on-a-copy discipline).
- **Kernel**: an `iou` scorer joins the analysis handler pack
  (`agentcad/kernel/handlers/analysis.py` or a sibling
  `handlers/bench.py`): two placed shapes in, `{intersection_mm3,
  union_mm3, iou}` out, with `safe_bool`-style guards and the
  solids-sum volume rule; reference geometry loads via
  `kernel/refload.py` (STEP → B-rep).
- **Built-in runner** drives `agentcad/agent/chat.py`'s tool-use loop
  with per-task budget wrappers; transcripts are the existing chat
  history payloads.
- **CLI**: `cmd_bench` joins `agentcad/cli.py`; a CI workflow runs the
  fast subset plus all reference self-tests; the release checklist
  consumes FR11's exit code.
- **Tasks live in-repo** under `benchmarks/`, references as STEP + SPECS
  files — PRD-003's constructors are one language for intent, gates,
  and evals.
- Product surface untouched: no manifest, tool, or event changes.

## MVP & phasing

- **MVP:** task format + loader + scorer (built/valid/specs/geometry/
  metrics) + `bench score` + `bench run --agent builtin` + 25 tasks
  (5 × 5) + reference self-tests green in CI + JSON/markdown reports.
- **Phase 2:** the external-agent walkthrough, submission validation,
  `bench publish`, and launch results (built-in + two external rows).
- **Phase 3:** baseline gating in release CI (FR11), the `fem/`
  category, a task-set v2 rotation policy, and PRD-018 regression
  wiring (generation-loop scores tracked per release).

## Acceptance criteria

- AC1. Every shipped task's reference solution scores 1.0 total via
  `bench score` — the self-test suite, green in CI (CI run).
- AC2. A deliberately flawed solution — one missing hole, one violated
  spec — scores below 1.0 with exactly the failing subscores named:
  the geometry drop ≈ hole/union ratio within tolerance, specs naming
  the failed check (tests).
- AC3. `bench score` run twice on one submission produces byte-identical
  `score.json` (determinism test).
- AC4. IoU scorer: reference vs itself = 1.0; disjoint parts = 0.0; a
  mesh-only candidate scores geometry 0 with `skipped_mesh` (kernel
  tests).
- AC5. `bench run --agent builtin` on the fast subset completes within
  budgets in CI, persists transcripts and scores, and the baseline
  comparison sets the exit code (CI run; requires the
  `ANTHROPIC_API_KEY` secret, marked accordingly).
- AC6. The documented external walkthrough — Claude Code over the
  agentcad MCP server on one task — produces a submission that
  `bench score` accepts and scores (slow test, with checked-in example
  artifacts).
- AC7. `bench publish` renders the leaderboard from a results directory
  with built-in + at least two external rows, each linking its
  reproducible submission (build check; the roadmap's published-results
  bar at launch).
- AC8. An over-budget run scores the on-disk state and flags
  `over_budget` rather than erroring (test).
- AC9. The core bench suite is green without `[fem]`; the full repo
  suite is green, count cited.

## Risks & open questions

- **Contamination** — public tasks leak into training data and scores
  inflate. Mitigations: versioned task sets (every score labeled
  bench-v1, …), published transcripts (shortcuts are inspectable), and
  a rotation policy adding fresh tasks per release. Public-and-
  reproducible beats secret-and-unverifiable for our purpose; say so.
- **Frame-alignment ambiguity** — a correct part in a different pose
  scores badly. Tasks must declare the frame; authoring guidelines
  require prompts that pin datums/orientation; normalization modes
  cover the rest. Task review is part of PR review.
- **Boolean fragility on adversarial geometry** — FR7's honest
  degradation; a voxel-IoU fallback scorer is an open question,
  deferred until real failures appear.
- **Cost and nondeterminism of running agents** — runs are budgeted and
  subset-ed in CI; the public claim (scoring) is fully deterministic;
  model versions are pinned in every row.
- **Leaderboard governance** — apples-to-oranges configs. The
  full-disclosure rule (FR12) is the bar; disputed rows are settled by
  rerunning the published submission.
- **Maintenance drag** — product-surface changes can break tasks; the
  reference self-tests (AC1) make breakage loud in CI, and tasks pin
  the agentcad version they were authored against.

## Competitive references

No product publishes agentic-CAD evals: Zoo ships zero; the incumbents'
AI ladders (Onshape Labs, Fusion's agentic Assistant, Creo Automate)
launch with promises and demos, not numbers; academic benchmarks —
Text2CAD-Bench, MUSE, the BenchCAD lineage — exist, but no product
reports against them (market_research.md, "AI-native CAD: Zoo, AdamCAD,
Backflip, and the research frontier"; "Gap matrix"). We differ:
kernel-scored mechanical ground truth — the same referee that validates
every product mutation — an open, reproducible harness runnable against
anyone's agent over MCP, and the score wired into our own release gate.
The benchmark is a product commitment, not a marketing artifact.
