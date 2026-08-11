# `agentcad-check` — the geometry CI action

A **composite** GitHub Action that runs `agentcad check` against a project in
the checked-out repository: it rebuilds every part, re-resolves the assembly,
runs interference, evaluates the design specs and regenerates the drawings,
then writes a JSON report, appends a markdown summary to the job summary,
uploads both as an artifact and exits `0` / `1` / `2`.

Composite rather than Docker: a Docker action would have to bake the ~2 GB
OCCT layer into an image and would defeat `setup-uv`'s cache.

This file is the action's own surface — its inputs and outputs, which version
with `action.yml`. The command it runs, the report schema, the stage semantics,
the exit codes and the proposal gate are documented once, in
[`docs/geometry-ci.md`](../../../docs/geometry-ci.md).

```yaml
jobs:
  geometry:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: n3r/AgentCAD/.github/actions/agentcad-check@main
        with:
          project: .
```

This repository dogfoods it in
[`.github/workflows/geometry-ci.yml`](../../workflows/geometry-ci.yml), where
`agentcad: .` installs the checked-out source instead of the published wheel.

## It checks the working tree; the SHA is provenance

`actions/checkout` has already materialized `$GITHUB_SHA` into the working
directory, and a runner has **no AgentCAD `.history/` repo** to resolve a ref
against — that git directory is per-project, is not committed to the host repo,
and is the thing users are told to `.gitignore`. So the action checks the tree
it was given and passes the host SHA as *provenance*:

```
agentcad check --project . --sha "$GITHUB_SHA" --ref-label "$GITHUB_REF_NAME" …
```

`--ref` stays the local and server mechanism, where `.history` exists and
branches, tags and proposals are real. Passing `--ref "$GITHUB_SHA"` on a
runner would exit `2` on every run.

## Inputs

| input | default | |
|---|---|---|
| `project` | `.` | project directory (a path) or project name (with `projects-dir`) |
| `projects-dir` | `` | projects root, for a repository holding several projects |
| `stages` | `build,assembly,specs,drawings` | comma-separated subset |
| `strict` | `false` | count skipped rows as failures (rows keep their status; only the verdict moves). A row marked `strict_exempt` — an unconditional skip, today only the DXF determinism row — is never counted |
| `budget` | `` | wall-clock seconds; empty is unbounded |
| `verify-determinism` | `false` | build every part a second time on a cold cache and compare bytes |
| `proposal` | `` | post the report to this proposal id |
| `auto-proposal` | `false` | post to the one active proposal whose source is the branch checked |
| `report-json` | `$RUNNER_TEMP/agentcad-check/report.json` | |
| `report-md` | `$RUNNER_TEMP/agentcad-check/report.md` | |
| `pool-size` | `1` | → `AGENTCAD_KERNEL_POOL_SIZE` |
| `agentcad` | `agentcad` | pip requirement; `.` installs the checked-out repository |
| `python-version` | `3.12` | |
| `fem` | `false` | install the `[fem]` extra |
| `upload-artifacts` | `true` | |
| `artifact-name` | `agentcad-check-<os>-<project>` | must be unique across matrix jobs (`upload-artifact@v4`) |
| `github-token` | `` | **reserved for phase 2** (a commit status); ignored in v1, with a warning |

`proposal` and `auto-proposal` are mutually exclusive. Both need a project with
AgentCAD history (`.history/`) — on a plain runner checkout there is none, and
the CLI degrades to a warning on stderr and still returns the check's own exit
code.

## Outputs

| output | |
|---|---|
| `status` | `green` / `red` / `skip` (empty when no report was produced) |
| `exit-code` | `0` green · `1` red, the model is wrong · `2` harness, no verdict |
| `report-json` / `report-md` | resolved paths |
| `failed-stages` | comma-separated names of the red stages |

The check step never fails the job itself: it saves the exit code, the summary
and the artifact land under `if: always()`, and a final step re-raises the code.
A red check is therefore always accompanied by its evidence.

If a **setup** step fails, the check never runs and `exit-code` is empty: the
final step then fails the job with a harness error (exit `2`) that says so —
never as `red`, which would blame your geometry for a failed install.

## Runner requirements

- **Disk:** the OCCT wheels are ~2 GB installed, plus the uv cache — budget
  **8 GB free**.
- **Memory:** ~0.5 GB per kernel worker on top of the runner baseline, so
  `pool-size: 1` on the standard 2-core / 7 GB runner; raise it only on a
  larger one.
- **OS:** `ubuntu-latest` and macOS runners are supported. On Linux the action
  installs the six system libraries OCCT needs even headless (`libgl1
  libglu1-mesa libxrender1 libxcursor1 libxft2 libxinerama1`) — the same list
  `.github/workflows/ci.yml` uses. Windows runners are untested in v1.

## Caching

| layer | mechanism | v1 |
|---|---|---|
| L1 — the ~2 GB OCCT wheels | `astral-sh/setup-uv` `enable-cache`, keyed on `uv.lock` | **yes** — the layer that matters |
| L2 — the resolved `.venv` | `actions/cache` on `.venv` | no — a warm uv cache makes the install cheap; measure first |
| L3 — AgentCAD's geometry cache | `actions/cache` on the project's `.cache/` | no — the PRD's open question; add it yourself if a cold rebuild dominates your runs |
| L4 — apt packages | — | no; fast, and apt cache restore is fragile |

L1 is the only caching layer the action turns on. To try L3 yourself, cache
`<project>/.cache/` keyed on the part scripts — a cache hit skips the rebuild
of parts whose `cache_key` is unchanged.

## What a repo-hosted project should `.gitignore`

```gitignore
.history/
.cache/
exports/
```

`.history/` is AgentCAD's own per-project git directory, `.cache/` holds derived
geometry, and `exports/` holds generated STEP/STL/drawings. None belongs in the
host repository; all three are rebuilt by the check.

## Trust model

The workflow that calls this action must use `pull_request`, **never**
`pull_request_target`, and must not hand it secrets. A part script is arbitrary
Python that the check executes, and on Linux there is no seatbelt (macOS has
one; see PRD-006 for the real answer). A fork's pull request therefore runs
untrusted code on the runner with the permissions of the workflow — keep them
read-only.
