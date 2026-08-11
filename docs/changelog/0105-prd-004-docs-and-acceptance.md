# 0105 — 2026-08-11 — PRD-004 slice 8: documentation, acceptance tests, close-out

- **Commit:** pending
- **Date:** 2026-08-11
- **Author:** Claude

## Summary

Final slice of PRD-004 (geometry CI). Slices 1–7 built the report, the
pipeline, the ref containment, the command, the tool and its routes, the
proposal gate and the GitHub Action; this slice writes the feature down and
proves it against its own contract. `docs/geometry-ci.md` is the end-to-end
reference `action.yml` and the action's README have been linking to since
slice 7, `tests/test_prd004_acceptance.py` walks AC1–AC10 one named test at a
time over the real stack, and the PRD, the roadmap and the four contributor
docs catch up with what shipped.

**AC1 is the one criterion a test suite cannot satisfy** — it asks for a green
**live run** of `.github/workflows/geometry-ci.yml` in this repository's own
Actions. Its test asserts the *shape* (the workflow matrixes the bundled
examples and drives them through the same composite action a user consumes)
plus this record; the run URL and conclusion are cited below and in the pull
request, following the PRD-001 AC6 / PRD-003 AC8 evidence precedent.

## Changes

- **`docs/geometry-ci.md` (new)** — the feature's reference: what a check
  certifies and what it deliberately does not; the CLI contract with every
  flag; the three exit codes and the table that produces them; a tour of the
  `schema: 1` report generated against a real run and checked with
  `validate_report`; the markdown rendering; the four stages and what each may
  claim; working-tree mode vs `--ref` and the byte-identity guarantee (with its
  stated price, a cold cache); the honest `--budget` overshoot; the determinism
  guard and why DXF is excluded; the tool, the routes and the `check_finished`
  event; proposal posting and the gate's verdict table; consuming the Action,
  runner requirements, the caching layers and the trust model (absorbed from
  `.github/actions/agentcad-check/README.md`, which now links here for the
  narrative and keeps the input/output tables).
- **`docs/agent-api.md`** — a `### Geometry CI` section for `run_checks`, its
  routes, `check_finished`, the exit-code contract and the gate semantics; the
  header tool count is now **65 tools (68 with the `[fem]` extra)**, counted
  from a live `build_registry`; `proposal_get`'s gate list no longer says the
  `checks` gate is a placeholder, and `proposal_changed`'s `reason` vocabulary
  gains `checks`.
- **`docs/architecture.md`** — a `## Geometry CI` section after "Design specs"
  (the sequencer, the four stages, the ephemeral-service diagram for `--ref`,
  the determinism guard, the surfaces and the gate), plus `core/checks.py`,
  `core/tools_run_checks.py` and `server/routes_checks.py` rows in the
  component table.
- **`AGENTS.md`** — a **"CI gotchas (PRD-004)"** section (the muzzled ephemeral
  service, the `tools_run_checks.py` load-order rule, `items` never `checks`,
  report-honest vs `--strict`, `worktree add --detach <sha>`, `resolve_branch`
  before `resolve_tag`, DXF's instability, the deliberate cold cache, the
  budget's in-flight overshoot, and the reference-part `is_valid` deviation);
  the stale "42/45 agent tools" line is now the real count; the CLI line lists
  `check`; `docs/geometry-ci.md` joins "Where to read more".
- **`CLAUDE.md`** — the condensed traps gain the three that bite hardest, and
  the docs list gains `docs/geometry-ci.md`.
- **`README.md`** — geometry CI is a headline capability, with a short **CI**
  section carrying the workflow snippet; the tool count and the `docs/` index
  line are corrected.
- **`docs/user-guide.md`** — the proposals modal's Checks tab now describes the
  real CI gate (posting is how a proposal opts in; nothing posted is skipped;
  red, stale, incomplete or unreadable is a fail that blocks the merge) instead
  of "the CI slot".
- **`docs/roadmap.md`** — the PRD-004 row pointed at `prd/pending/` while the
  file has lived in `prd/in-progress/` since the feature was picked up. Fixed,
  and the status column now records the implemented state.
- **`docs/prd/in-progress/PRD-004-geometry-ci.md`** — status → **implemented**,
  plus a `### Verification (slice 8)` table mapping every AC to its proving
  test and an `### As built — divergences from this document` section recording
  the seven deviations slices 1–7 made deliberately. The file **stays in
  `in-progress/`**: it moves to `completed/` when the live AC1 run is green and
  the branch merges.
- **`tests/test_prd004_acceptance.py` (new, 10 tests)** — one named test per
  acceptance criterion, over the real service and its full registry (not
  `make_test_service`), driving the surfaces a user and an agent actually
  touch:

  | AC | Proving test |
  |----|---|
  | AC1 | `test_ac1_the_dogfood_workflow_certifies_the_bundled_examples` — the workflow parses, matrixes `construction`/`prototyping`/`rocketry` (each a real project on disk), uses `./.github/actions/agentcad-check` with `agentcad: .`, and is `pull_request` and never `pull_request_target`; the action is composite and runs `agentcad check`; the **live run** is cited in this entry |
  | AC2 | `test_ac2_interference_in_construction_is_red_in_both_renderings` — two construction instances parked on top of each other: the assembly stage is red, the `pair` row names both ids with a positive `volume_mm3`, exit 1, and both ids appear in `render_markdown` |
  | AC3 | `test_ac3_a_broken_spec_names_the_check_with_measured_and_limit` — a PRD-003 `check_wall` fixture green at `wall=2.5`, red at `wall=0.8` with `measured < limit.min_mm`, the requirement red, and the geometry still built |
  | AC4 | `test_ac4_a_script_error_carries_the_update_part_script_payload` — the row's `error.type`, `details.line` and Error-Doctor `details.hint` are asserted **equal to** the payload `update_part_script` returned for the same edit |
  | AC5 | `test_ac5_the_three_exit_codes_and_a_report_that_validates` — the real console script: 0 on a clean copy (with `validate_report` clean), 1 on a copy with a broken script, 2 on an unknown project |
  | AC6 | `test_ac6_verify_determinism_passes_on_a_bundled_example` — the `determinism` stage green on `prototyping`, every pass row naming what it `compared`, and the DXF row a `skip`/`not_byte_stable` |
  | AC7 | `test_ac7_checking_a_tag_leaves_the_project_byte_identical` — a warmed project fingerprinted file by file before and after `--ref v1`, plus head and `git status` unchanged, and the ref run's cold cache asserted rather than hidden |
  | AC8 | `test_ac8_without_the_fem_extra_a_check_skips_and_strict_flips_it` — skip/`fem_extra_missing` at exit 0, `--strict` at exit 1 with the row **still** a skip |
  | AC9 | `test_ac9_the_mcp_passthrough_and_the_cli_report_the_same_thing` — `POST /api/tools/run_checks` (what the MCP server proxies) against the real CLI over one project, equal after normalizing the clock, the host block and every duration |
  | AC10 | `test_ac10_the_full_suite_count_is_cited` — the evidence check over this entry |

## Files

- `docs/geometry-ci.md` — new; the feature reference
- `tests/test_prd004_acceptance.py` — new; AC1–AC10
- `docs/prd/in-progress/PRD-004-geometry-ci.md` — status, verification table,
  as-built divergences
- `docs/agent-api.md`, `docs/architecture.md`, `docs/user-guide.md`,
  `docs/roadmap.md`, `AGENTS.md`, `CLAUDE.md`, `README.md`,
  `.github/actions/agentcad-check/README.md` — updated surfaces
- `docs/changelog/0105-prd-004-docs-and-acceptance.md` — this entry

## Notes

- **AC1 — the live run.** `.github/workflows/geometry-ci.yml`, `examples`
  matrix (`construction`, `prototyping`, `rocketry`, `fasteners`) on
  `ubuntu-latest`.
  - Run: https://github.com/n3r/AgentCAD/actions/runs/31492128698 —
    conclusion **success**, on PR #11 (`576acff`). All four jobs green:
    `check (construction)`, `check (prototyping)`, `check (rocketry)`,
    `check (fasteners)`; `check (engine)` skipped, as designed (nightly).
    AC1 is met: the Action certified the bundled examples on a real runner.
  - It could not be produced locally: this machine is not a GitHub runner, and
    `act` is not installed. Everything the runner executes *is* exercised
    locally — `tests/test_geometry_ci_action.py` runs the action's shell bodies
    verbatim against copies of `examples/construction` — but a green live run
    is the criterion, and the criterion is not met by a simulation. Per the
    plan's landing note: if that run is red for an environment reason, the fix
    is the runner (system libraries, pool size, disk) or the matrix, never a
    loosened check.
- **AC10 — the suite.** `make test` → **1117 passed, 1 skipped** in 1388.72 s
  (0:23:08), against slice 7's 1107-passed baseline — exactly the 10 tests this
  slice adds. `uv run pytest -q tests/test_prd004_acceptance.py -p no:randomly`
  → **10 passed** in 26.73 s, and with the action suite alongside
  (`tests/test_geometry_ci_action.py`) → **32 passed** in 24.52 s. **No
  pre-existing test file was edited by this slice** — `git status --porcelain`
  shows only the two new test/doc files, the new changelog and doc, and nine
  edited docs.
- **Two ACs are evidence checks, and deliberately so.** AC1 (a live CI run) and
  AC10 (the suite count) are statements about runs that happen outside the
  suite. Following PRD-001 AC6 and PRD-003 AC8, each has a named test that
  fails if the record is removed, rather than a test that pretends to re-drive
  the thing itself.
- **The eight as-built divergences are documented, not silently absorbed** —
  they are now in the PRD's own "As built" section and, where a future
  contributor would trip over them, in `AGENTS.md`: a reference part's
  `is_valid` is reported rather than enforced (OCCT calls the shipped rocketry
  STEP import invalid); there is no separate `fem-smoke` stage (PRD-003's specs
  tier already owns it); the pack is `tools_run_checks.py`, never
  `tools_checks.py`; the `checks` gate never answers `pending`; the determinism
  stage compares SVG only; a ref check runs on a cold cache; the stage table
  prints after the run rather than live; and the Action takes `--sha` as
  provenance and never `--ref`.
- **`docs/user-guide.md` needed prose, not screenshots.** The gate renders
  through PRD-002's existing Checks tab with no frontend change in this
  feature, so there is no new UI surface to verify in a browser — the tab's
  chip is fed by a new provider, and what changed is what the chip *says*.
- The action's README keeps its input/output tables (they version with
  `action.yml`) and now links to `docs/geometry-ci.md` for the caching,
  runner-requirement and trust-model narrative, which lives in one place.
