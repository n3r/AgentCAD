# PRD-004 — Geometry CI

- **Status:** pending
- **Phase:** v4 — collaborative core
- **Created:** 2026-08-09
- **Origin:** competitive analysis (Aug 2026)
- **Depends on:** PRD-001 (hard — checks run on refs) · PRD-003 (hard —
  specs are the richest stage) · PRD-006 (soft — Linux sandbox for
  untrusted repos)
- **Related:** PRD-002 (statuses post to proposals), PRD-012 (config
  matrix joins the stages), PRD-024 (bench rides the same headless
  harness)

## Problem & motivation

Nothing today re-validates a whole project at change scale. A part is
checked when it rebuilds; everything else — mates still resolving,
assemblies still interference-free, specs still green, drawings still
generating — is checked only if someone remembers to. Branches (PRD-001)
and proposals (PRD-002) make this acute: a merge decision needs a
project-wide verdict, and an agent needs a machine-readable one.

CI for CAD exists nowhere — the gap matrix scores it "none — nobody —
build-differentiated (unclaimed)" (market_research.md, "Gap matrix").
Incumbents cannot get there: their models don't regenerate
deterministically, their automation is 1990s COM/VBA driving a GUI ("The
desktop incumbents"), and Onshape meters its API per company — the 85
requests/day change caused a forum uproar — a structural mismatch with
CI-shaped workloads ("Cloud-native CAD: Onshape"). AgentCAD's determinism
guarantee — same script + params ⇒ identical geometry, cache key
`sha256(content, params, density, tolerance)` — makes CI both trivial and
trustworthy: a red check means the change is wrong, not that regeneration
was flaky. It also makes GitHub a first-class distribution channel: an
AgentCAD project is a repo, and repos expect CI on push.

## Users & jobs

- **Design engineer (human):** push a branch, get a verdict — did my
  change break a build, a mate, a spec, a drawing — without opening the
  app.
- **Reviewing engineer (human):** trust the green check on a proposal
  instead of re-running validations by hand.
- **Design agent:** `run_checks` is the feedback loop at change scale — a
  red stage carries the same structured error the agent already knows how
  to fix (script line + hint, interference pair, failing check with
  measured vs limit).
- **Open-source project maintainer:** a published GitHub Action gives any
  repo-hosted AgentCAD project real CI with one workflow file.
- **Release tooling (PRD-015):** "released implies green" needs a runner
  that can certify a ref.

## Goals

- G1. One command certifies a ref: `agentcad check` rebuilds every part,
  re-resolves the assembly, runs interference, specs, and drawing
  regeneration — headless, no server, no API key.
- G2. Two reports from one run: machine-readable JSON (versioned schema,
  structured errors) and human-readable markdown (renders as a GitHub job
  summary).
- G3. Determinism is enforced, not assumed: the runner can prove that two
  builds of the same ref produce identical mesh bytes.
- G4. Statuses land where decisions happen: a check posts its verdict to
  the proposal (PRD-002) it certifies.
- G5. The repo dogfoods it: the bundled examples run under geometry CI in
  this repository's own GitHub Actions.

## Non-goals

- Review workflow and merge gating UX — PRD-002 (this PRD produces the
  status it displays).
- Spec semantics — PRD-003 (this PRD invokes `evaluate_specs`).
- Benchmark scoring — PRD-024 (same harness pattern, different verdicts).
- Fleet/queue orchestration — PRD-020 (a check is one bounded run here).
- Cross-OS byte-identity — the determinism guarantee is per-platform;
  cross-OS comparisons stay metric-tolerance-based (the existing three-OS
  matrix encodes this).

## Experience

**Human path.** Locally: `agentcad check` before opening a proposal —
a stage table scrolls by, `report.md` names each failure with its hint,
exit code answers scripts. On GitHub: push a branch, the action runs the
same command, the job summary shows the stage table, the commit gets a
status, the proposal's Checks tab (PRD-002) shows the posted verdict.

```
agentcad check [--project PATH|NAME] [--ref REF]
               [--stages build,assembly,specs,drawings]
               [--report report.json] [--md report.md]
               [--strict] [--verify-determinism] [--budget SECONDS]
```

**Agent path.** `run_checks {project, ref?}` returns the full report as
data. A red build stage carries `details.line` and the Error Doctor hint;
a red assembly stage names the interfering pair; a red specs stage names
the check with measured vs limit — each one a structured task the agent
picks up, fixes on its branch, and re-runs, closing the loop without a
human or a shell.

**Handoff.** CI red on an agent's proposal is the agent's to fix; CI green
is the reviewer's floor. The report is identical in both hands.

## Functional requirements

**Runner**
- FR1. `agentcad check` builds the service headless and in-process (the
  `agentcad/cli.py` `_build_service` path — no HTTP, no port, no API key)
  and runs the stage pipeline against a project at `--ref` (PRD-001
  branch/tag; default: working tree). Exit codes: 0 green, 1 red, 2
  harness failure.
- FR2. Stages, each independently reportable: **build** (every script
  part rebuilds; validity per solid), **assembly** (mates re-resolve;
  `check_interference` clean), **specs** (PRD-003 `evaluate_specs`),
  **drawings** (`generate_drawing` for every drawable part and
  `flat_pattern` where the script defines it — must succeed;
  byte-stability is a phase-2 assertion), **fem-smoke** (only when
  `[fem]` is installed and specs request it).
- FR3. Checking a ref never mutates the project: the ref materializes
  into a temp worktree with its own cache dir; the working tree and
  `.cache/` are byte-untouched (asserted by test, not hoped).
- FR4. Skips are first-class: FEM-dependent checks without the extra and
  mesh-only reference parts (`skipped_mesh`) report as skip, distinct
  from pass; `--strict` turns skips red.
- FR5. Bounded execution: per-part kernel timeout (the pool's existing
  per-request timeout), a total `--budget` wall clock, parallelism via
  `AGENTCAD_KERNEL_POOL_SIZE`; a blown budget exits 2 with the completed
  portion reported.
- FR6. `--verify-determinism` builds every part twice and asserts
  identical mesh cache keys and bytes — the standing regression guard for
  the core guarantee.

**Reports**
- FR7. `report.json` — versioned schema (`"schema": 1`): per stage, per
  part/instance/check, results embed the same structured errors the tools
  return (`type`/`message`/`details`/`hint`); machine consumers get
  exactly what agents get.
- FR8. `report.md` — human summary: a status table, then each failure
  with its hint; valid as a GitHub Actions job summary and a PR comment
  body.
- FR9. Proposal integration: `--proposal <id>` (or auto-match by source
  branch) posts `{status, report}` to the proposal's CI slot via
  PRD-002's store; the Checks tab renders it.

**GitHub Action & dogfood**
- FR10. A reusable action (in-repo `.github/actions/agentcad-check`
  first, marketplace later): setup uv → cached install of the pinned
  agentcad → `agentcad check --ref $GITHUB_SHA` → upload report artifacts
  → set the commit status and job summary. Runner requirements documented
  (OCCT wheels ≈ 2 GB installed, cached between runs).
- FR11. This repository dogfoods it: the three bundled examples run under
  geometry CI on every push, alongside `make test`.
- FR12. `run_checks {project, ref?, stages?, strict?}` returns the same
  report over the registry (MCP/chat/REST) and publishes
  `check_finished {project, ref, status}` on the WebSocket channel.

## Agent surface

New tool: `run_checks {project, ref?, stages?, strict?}` — the full
report as post-state, structured errors embedded.
New event: `check_finished {project, ref, status}`.
No new error types: a red check is data in the report; harness failures
surface as the existing error families. The CLI is the primary surface;
the tool exists so agents close the loop without a shell.

## Technical approach

- **Core module** `agentcad/core/checks.py`: the stage pipeline over
  `AgentCADService` — rebuild orchestration, `mates.resolve`,
  interference, `evaluate_specs`, and drawing regeneration are existing
  service/tool paths; this module sequences them and shapes the report.
  No new kernel handlers.
- **CLI**: `cmd_check` joins `serve/mcp/worker/new/export` in
  `agentcad/cli.py`, reusing `_build_service`; ref materialization via
  PRD-001's worktree plumbing over `core/history.py`.
- **Tool pack** `agentcad/core/tools_checks.py` + **route pack**
  `agentcad/server/routes_checks.py` (`POST /api/projects/{p}/checks`,
  `GET` last report); proposal posting goes through `core/proposals.py`
  (PRD-002).
- **Action**: `action.yml` plus a `geometry-ci.yml` workflow for the
  bundled examples; `report.md` doubles as `$GITHUB_STEP_SUMMARY`.
- **Sandboxing**: macOS workers are already seatbelt-confined; Linux
  confinement arrives with PRD-006. Until then, Linux CI runs with the
  same trust model as `pytest` on the same repo — the scripts under test
  are the repo's own — stated in docs rather than papered over.
- Report schema documented in `docs/` and covered by schema-validation
  tests.

## MVP & phasing

- **MVP:** `agentcad check` with build/assembly/specs stages on working
  tree and `--ref`; JSON + MD reports and exit codes; the `run_checks`
  tool; this repo's workflow running the three examples green.
- **Phase 2:** drawings stage with the byte-stability assertion,
  `--verify-determinism`, `--strict`, the published reusable action with
  commit statuses.
- **Phase 3:** proposal status posting (PRD-002's Checks tab),
  config-matrix builds (PRD-012), and bench-score gating riding the same
  harness (PRD-024).

## Acceptance criteria

- AC1. Geometry CI runs green on all three bundled examples in this
  repository's own GitHub Actions (live CI run — the roadmap's
  done-when).
- AC2. Introducing an interference into the construction example turns
  the assembly stage red with the offending pair named in both
  `report.json` and `report.md`, exit code 1 (test on a copy).
- AC3. Breaking a spec turns the specs stage red naming the check with
  measured vs limit (test over a PRD-003 fixture).
- AC4. A script error in one part fails the build stage carrying
  `details.line` and the Error Doctor hint — the same payload
  `update_part_script` would return (test).
- AC5. `report.json` validates against the published schema; exit codes
  0/1/2 are each covered (tests).
- AC6. `--verify-determinism` passes on the examples: two builds,
  identical cache keys and mesh bytes (test).
- AC7. `agentcad check --ref <tag>` leaves the working tree and `.cache/`
  byte-identical (test asserting no diff).
- AC8. Without `[fem]`, fem-linked checks report skip and the exit stays
  0; `--strict` flips it to 1 (tests; suite green without the extra).
- AC9. `run_checks` over MCP returns a report identical to the CLI's
  (test).
- AC10. Full suite green, count cited.

## Risks & open questions

- **Runtime on large projects** — warm kernel ≈ 3 s plus N builds.
  Mitigations: content-hash cache hits for unchanged parts, pool
  parallelism, `--budget`. Open: should the action carry `.cache/`
  between runs (`actions/cache`)? Ship without, measure, then decide.
- **Runner footprint** — the ~2 GB OCCT install; uv cache in the action;
  document minimum runner sizes.
- **Drawing byte-stability** — any timestamp or random id in SVG/DXF
  output breaks it; enforce no-timestamp exporters before promoting the
  assertion out of phase 2.
- **Untrusted-fork CI** — running a stranger's scripts is arbitrary code
  execution on the runner; the action documents `pull_request` vs
  `pull_request_target` hygiene, and PRD-006's Linux confinement is the
  real answer.
- **Status races** — a check certifies specific head SHAs; PRD-002's
  merge re-checks gates against current heads, so a stale green cannot
  merge a newer red.

## Competitive references

Nobody ships CI for CAD (market_research.md, "Gap matrix" — unclaimed).
Onshape cannot regenerate deterministically and meters its API per
company — the 85-requests/day episode ("Cloud-native CAD: Onshape").
Incumbent automation is COM/VBA macros driving a GUI ("The desktop
incumbents"); their new AI features generate, but nothing re-validates a
whole design on every change. We differ: determinism by construction,
red checks that are structured tasks an agent can autonomously fix, and a
GitHub Action that makes open-source CAD projects first-class citizens of
the software-CI world.
