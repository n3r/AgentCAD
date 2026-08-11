# PRD-004 — Geometry CI

- **Status:** completed — merged to main in PR #11 (AC1–AC10 verified)
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

### Verification (slice 8)

Every criterion above has a named test in `tests/test_prd004_acceptance.py`,
which walks it end to end through the surfaces a user and an agent actually
touch — the `run_checks` tool, the HTTP passthrough MCP proxies, the real
console script, git and the bundled examples on a renamed copy — rather than
through the unit seams (`tests/test_checks.py`, `test_checks_pipeline.py`,
`test_checks_ref.py`, `test_checks_cli.py`, `test_checks_api.py`,
`test_checks_gate.py`, `test_geometry_ci_action.py` — 193 tests between them):

| AC | Proving test |
|----|---|
| AC1 | `test_ac1_the_dogfood_workflow_certifies_the_bundled_examples` — the workflow parses, matrixes `construction`/`prototyping`/`rocketry` (each a real project on disk), drives them through `./.github/actions/agentcad-check` with `agentcad: .`, and is `pull_request` and never `pull_request_target`; the action is composite and runs `agentcad check`. **The live run is the criterion** and is cited in `docs/changelog/0105-prd-004-docs-and-acceptance.md` and in the pull request |
| AC2 | `test_ac2_interference_in_construction_is_red_in_both_renderings` — two construction instances parked on top of each other: the assembly stage red, a `pair` row naming both ids with a positive `volume_mm3`, exit 1, and both ids in `render_markdown` |
| AC3 | `test_ac3_a_broken_spec_names_the_check_with_measured_and_limit` — a PRD-003 `check_wall` fixture green at `wall=2.5`, red at `wall=0.8` with `measured < limit.min_mm`, the requirement red, the geometry still built |
| AC4 | `test_ac4_a_script_error_carries_the_update_part_script_payload` — the row's `error.type`, `details.line` and Error-Doctor `details.hint` asserted **equal to** the payload `update_part_script` returned for the same edit |
| AC5 | `test_ac5_the_three_exit_codes_and_a_report_that_validates` — the real console script: 0 on a clean copy (with `validate_report` clean), 1 on a copy with a broken script, 2 on an unknown project |
| AC6 | `test_ac6_verify_determinism_passes_on_a_bundled_example` — the `determinism` stage green, every pass row naming what it `compared`, the DXF row a `skip`/`not_byte_stable` |
| AC7 | `test_ac7_checking_a_tag_leaves_the_project_byte_identical` — a warmed project fingerprinted file by file before and after `--ref v1`, head and `git status` unchanged, and the ref run's cold cache asserted rather than hidden |
| AC8 | `test_ac8_without_the_fem_extra_a_check_skips_and_strict_flips_it` — `skip`/`fem_extra_missing` at exit 0, `--strict` at exit 1 with the row **still** a skip |
| AC9 | `test_ac9_the_mcp_passthrough_and_the_cli_report_the_same_thing` — `POST /api/tools/run_checks` (what the MCP server proxies) against the real CLI over one project, equal after normalizing the clock, the host block and every duration |
| AC10 | `test_ac10_the_full_suite_count_is_cited` — the evidence check over the slice-8 changelog, where `make test`'s count is recorded |

**AC1 is the one criterion the suite cannot satisfy.** It asks for a green
**live** workflow run in this repository's own Actions, which no local test can
produce. Following the PRD-001 AC6 / PRD-003 AC8 precedent, its test asserts
the *shape* (the workflow exists, matrixes the bundled examples and drives them
through the same composite action a user consumes) plus the *record*, and the
run URL and conclusion are cited in the changelog and the pull request.
Most of what the runner executes is exercised locally by
`tests/test_geometry_ci_action.py`, which runs the action's shell bodies
verbatim: the input plumbing (paths, the `[fem]` splice, the artifact name, the
refusals), the summary step, the re-raise step, and the **check step's own
body** — argv construction, quoting and the `set +e` capture — against a real
`agentcad` over a throwaway project. The install step is *not* executed (it
provisions a Python and installs a package), and neither is the workflow
itself, so a simulation is still not the criterion.

### As built — divergences from this document

1. **A reference part's `is_valid` is reported, never enforced.** FR2 says
   "validity per solid"; that holds for **script** parts. OCCT calls the
   shipped `examples/rocketry` STEP import invalid across its 180 solids, which
   is exactly why `tests/test_examples.py` exempts reference parts from the
   same assertion and `import_cad_file` merely reports the flag. Failing on it
   would redden a clean bundled example — and the dogfood workflow — over
   geometry nobody in this repo authored. The row passes, `details.is_valid`
   carries the fact, and a warning names the part and the solid count in both
   renderings.
2. **There is no separate `fem-smoke` stage.** FR2 listed one; PRD-003 already
   evaluates `check_fem_static` inside the specs tier, honestly skipping it as
   `fem_extra_missing` without the extra. A fifth stage would have been a
   second way to say the same thing, with a second cache and a second skip
   vocabulary. AC8 is satisfied by the specs stage.
3. **The tool pack is `agentcad/core/tools_run_checks.py`, not
   `tools_checks.py`** (which the Technical approach names). Packs load
   alphabetically and `tools_proposals` assigns `service.gate_providers = []`
   unconditionally, so a pack at `c` would have had its gate silently
   discarded. The route pack keeps its planned name.
4. **The `checks` gate never answers `pending`.** `ProposalManager.merge()`
   blocks a gate whose state is `fail` and *nothing else*, so `pending` is
   merge-permissive and a green posted against an older commit would have stood
   while the source moved on. A stale report is a `fail` naming both SHAs and
   saying re-run — PRD-003's X8 finding, closed the same way. The
   complementary asymmetry: **posting is how a proposal opts in**, so a
   proposal nobody checked is `skipped` and blocks nothing — "nobody checked"
   meaning no record *and* no `checks_posted` line in the append-only audit, so
   deleting a posted report is a `fail` rather than a way back to permissive.
   A report that measured a **dirty working tree** is refused at the post: its
   `head` is the committed sha, and uncommitted edits mean that is not what was
   measured.
5. **The drawings stage regenerates SVG only, and byte-stability lives in
   `--verify-determinism`.** DXF is excluded by name because `ezdxf` stamps
   `$TDCREATE` and fresh GUIDs into every document; it is one
   `skip`/`not_byte_stable` row with a hint naming the prerequisite. That row
   is the report's one `strict_exempt` skip: it is unconditional, so `--strict`
   does not count it (it would otherwise be red for ever and say nothing).
6. **A ref check runs on a cold cache.** FR3's "byte-untouched" guarantee is
   absolute, so the throwaway worktree carries no `.cache/` and every part is a
   real kernel build. The price is stated in the docs and asserted in the
   tests rather than hidden; a `ProjectStore.cache_dir` override seam is the
   recorded phase-2 follow-up, after measurement.
7. **The GitHub Action checks the working tree and takes `$GITHUB_SHA` as
   provenance** (`--sha` / `--ref-label`), not as `--ref` as FR10 words it.
   `actions/checkout` has already materialized the ref, and a runner has no
   AgentCAD `.history/` repo — that directory is per-project and is the first
   thing users are told to `.gitignore` — so `--ref "$GITHUB_SHA"` would exit 2
   on every run.
8. **The CLI prints its stage table after the run, not live.** `CheckRunner`
   has no progress callback, and adding one was out of the CLI slice's file
   list. A per-stage event would serve the UI and the CLI at once and is the
   clean follow-up.

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
