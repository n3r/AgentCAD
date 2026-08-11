# 0104 — 2026-08-11 — PRD-004 slice 7: the geometry CI action and the dogfood workflow

- **Commit:** pending
- **Date:** 2026-08-11
- **Author:** Claude

## Summary

Seventh slice of PRD-004 (geometry CI): `agentcad check` becomes something a
repository *runs* (FR10, FR11). A composite GitHub Action,
`.github/actions/agentcad-check`, installs agentcad on a runner, checks a
project in the checked-out tree, appends the markdown report to
`$GITHUB_STEP_SUMMARY`, uploads `report.json`/`report.md` as an artifact and
fails the job with the check's own exit code — and
`.github/workflows/geometry-ci.yml` dogfoods it over the bundled examples.

**The action checks the WORKING TREE, and `$GITHUB_SHA` is provenance, never a
ref** (design Decision 9). `actions/checkout` has already materialized the ref
into the working directory, and a runner has no AgentCAD `.history/` repo —
that git directory is per-project and is the first thing users are told to
`.gitignore` — so `--ref "$GITHUB_SHA"` would exit 2 on every run. The SHA and
the ref name go in as `--sha` / `--ref-label`, which populate
`source.host_sha` / `source.label` and the markdown header.

No Python in `agentcad/` changed: the whole slice is CI plumbing plus one
helper script the action calls and one test module.

## Changes

- `.github/actions/agentcad-check/action.yml` — a **composite** action (not
  Docker: a Docker action would bake the ~2 GB OCCT layer and defeat
  `setup-uv`'s cache). Steps, in order:
  1. **Resolve inputs** (`id: plan`) — validates the mutually exclusive
     `proposal`/`auto-proposal` pair *before* the kernel spawns, resolves the
     report paths (default `$RUNNER_TEMP/agentcad-check/`), splices the `[fem]`
     extra into the requirement before any version specifier
     (`agentcad==1.2` → `agentcad[fem]==1.2`), and derives a per-OS/per-project
     artifact name so two matrix jobs cannot collide on `upload-artifact@v4`.
  2. `astral-sh/setup-uv@v5` with `enable-cache: true`,
     `cache-dependency-glob: uv.lock` — L1, the only caching layer in v1.
  3. The **six OCCT system libraries** on Linux, verbatim from
     `.github/workflows/ci.yml` (`libgl1 libglu1-mesa libxrender1 libxcursor1
     libxft2 libxinerama1`); a test compares the two lists so they cannot drift.
  4. **Install agentcad** — `uv sync --locked --no-dev` when the requirement is
     the checked-out repository and a `uv.lock` is present (the dogfood path:
     the pinned graph `ci.yml` already proves), otherwise `uv venv` +
     `uv pip install <requirement>`. Ends with `agentcad --help` as a smoke test
     of the console script.
  5. **Run agentcad check** (`id: check`) — builds the argv as a bash array
     (every value quoted; inputs arrive through `env:`, never interpolated into
     a script body, because a project name is attacker-controlled on a fork's
     PR), then **saves the exit code instead of failing**.
  6. **Write the job summary** / **Upload the report** / **Re-raise the exit
     code**, all `if: always()`, in that order — so a red or budget-truncated
     check still leaves its evidence and only then fails the job.
- `.github/actions/agentcad-check/report_outputs.py` — turns `report.json` into
  the `status` and `failed-stages` step outputs. A file rather than an inline
  heredoc: a heredoc body must start at column 0 and a YAML block scalar cannot
  contain an unindented line. It never fails — a missing or unparseable report
  yields empty outputs, and the check's saved exit code stays the answer.
- `.github/actions/agentcad-check/README.md` — the input/output tables, the
  working-tree-vs-`--ref` explanation, runner requirements (~2 GB installed
  OCCT + uv cache → budget 8 GB disk; ~0.5 GB RAM per kernel worker →
  `pool-size: 1` on a standard runner), the four caching layers and which are on
  in v1 (L1 only), the `.gitignore` a repo-hosted project needs
  (`.history/`, `.cache/`, `exports/`), and the trust model.
- `.github/workflows/geometry-ci.yml` — `ubuntu-latest` only. `examples`
  matrixes `construction`, `prototyping`, `rocketry` and `fasteners` on
  push/PR; `engine` (33 parts, 65 instances) runs on the nightly `schedule` or
  an explicit `workflow_dispatch`, mirroring how `ci.yml` defers its exhaustive
  suite. `permissions: contents: read`, a `geometry-ci-${{ github.ref }}`
  concurrency group, `pull_request` and **never** `pull_request_target`, and no
  secrets — a fork's part scripts are arbitrary Python and Linux has no
  seatbelt.
- `tests/test_geometry_ci_action.py` (new, 22 tests) — the drift guards:
  every long flag in the check step exists on `agentcad check --help`;
  `--ref` is *not* among them while `--sha`/`--ref-label` are; the `stages`
  default equals `core.checks.STAGES`; the OCCT package lists match `ci.yml`;
  the summary/upload/re-raise steps are ordered and `always()`; the workflow
  uses the local action with `agentcad: .` and every matrixed example exists on
  disk. The shell bodies are **executed** (`bash -n`, plus real runs of the
  plan, summary and re-raise steps with a fake `$GITHUB_OUTPUT`), so a quoting
  bug fails here rather than on a runner.

## Files

- `.github/actions/agentcad-check/action.yml` — new, the composite action
- `.github/actions/agentcad-check/report_outputs.py` — new, report → step outputs
- `.github/actions/agentcad-check/README.md` — new, the action's documentation
- `.github/workflows/geometry-ci.yml` — new, the dogfood workflow
- `tests/test_geometry_ci_action.py` — new, YAML/shell/CLI-drift tests

`.github/workflows/ci.yml` is deliberately untouched.

## Notes

- **Verified locally** (no runner available; `act` and `actionlint` are not
  installed here): all three `.github/**/*.yml` files parse under
  `yaml.safe_load`; `bash -n` passes on all five embedded scripts; and the
  action's `plan` → `check` → summary → re-raise steps were executed verbatim
  against copies of `examples/construction` — a clean copy gave
  `exit-code=0, status=green, failed-stages=`, and a copy with a broken part
  script gave `exit-code=1, status=red, failed-stages=build,assembly,drawings`
  with `report.json`/`report.md` written and the summary appended in both cases.
- **AC1 is not satisfied by this commit.** It requires a green live workflow
  run; the coordinating session pushes the branch and cites the run URL.
- `github-token` is accepted and documented as **reserved for phase 2** (a
  commit status); setting it emits a `::warning::` and nothing else.
- `--proposal`/`--auto-proposal` are forwarded unconditionally, per slice 6: on
  a plain runner checkout there are no proposals (no `.history/`) and the CLI
  degrades to a stderr warning while still returning the check's own exit code.
- Deviations from the design spec's input table, all additive: `proposal`,
  `auto-proposal`, `report-json`, `report-md` and `artifact-name` are new
  inputs; `min-volume`, `work-dir` and `ref` deliberately have none (`ref` is
  Decision 9).
