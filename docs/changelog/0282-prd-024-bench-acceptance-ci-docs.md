# 0282 — PRD-024: the acceptance suite, the bench CI workflow, the example submission and the docs

- **Commit:** pending
- **Date:** 2026-08-19
- **Author:** Claude (Task 11 of the AgentCAD-Bench plan)

## Summary

Closes PRD-024's evidence gap: one acceptance file that grades AC1/AC6/AC9 and
the STEP-drift rule, a **separate** `bench.yml` workflow whose secret-holding
job can never be reached by a pull request, a checked-in external submission
that the FR10 walkthrough is written against, and `docs/bench.md` plus
cross-references from every surrounding document. No product code changed —
this slice adds a test file, a workflow, a data directory and documentation.

## Changes

- **`tests/test_prd024_acceptance.py` (new, 60 tests — 41 slow, 19 fast).**
  - **AC1** — parametrised over `load_tasks()` at collection time (25 params,
    `slow`): every shipped `reference/project` scores exactly 1.0, with a
    non-empty `weights_effective` so a vacuous "everything excluded" 1.0 cannot
    pass, and every subscore status in `{ok, not_applicable}`.
  - **The STEP-drift check (design D9)** — 15 params (`asm_*`/`opt_*` ship
    `reference.steps: {}` and are not parametrised): the reference project is
    staged **under the test's own projects root** (never opened in place —
    `benchmarks/` is read-only and is not a writable root for the confined
    worker), re-exported, and compared to the checked-in datum through the
    kernel: volume within 1e-6 relative and every bbox bound within 1e-3 mm via
    `build_reference`, plus `iou >= 0.9999` wherever the datum is actually
    scored (the one exception is argued under Notes).
  - **AC6** — `benchmarks/examples/submission-mfd-001` is scored through
    `main()` (the real `agentcad bench score` process path, not `Scorer`
    directly, because what FR10 promises an outside team is the *command*):
    exit 0, `schema: 1`, `built: ok`, total `0.9959 ± 0.01` and strictly below
    1.0. A second, kernel-free test proves the example is not a copy of the
    reference and ships no `specs.py`.
  - **AC9** — `agentcad/bench/**` is parsed with `ast` (not grepped: the
    package states its own "OCP-free by contract" rule in prose, and a
    substring search flags the sentence as a violation of itself) and may
    import neither `build123d`/`OCP` nor `gmsh`/`skfem`/`meshio`; `iou` and any
    `bench*` name are absent from `build_registry(service).list()`; there is no
    `core/tools_bench.py` and no `server/routes_bench.py`.
  - **The workflow's shape** — `bench.yml` parses, has exactly
    `{selftest, guard, builtin}`, `permissions: contents: read`, no
    `pull_request_target`; `builtin.if` is pinned as a **whole string** to the
    three ANDed conditions (`has_key`, `event_name != 'pull_request'`,
    `ref == refs/heads/main`) and the job takes the secret only through `env`; `selftest`'s whole job body contains no `secrets.` and runs both
    bench test globs; `guard` emits `::notice` and never runs the agent;
    `builtin` gates on `--baseline` and uploads `always()`; the OCCT apt
    package list equals `ci.yml`'s. And `ci.yml` itself mentions no bench
    needle and still carries exactly two `expect_sandbox: active`, the byte
    assertion `tests/test_prd006_acceptance.py` makes on it.
  - **The docs** — `docs/bench.md` exists and the five surrounding documents
    cross-reference it.

- **`.github/workflows/bench.yml` (new).** Three jobs. `selftest`
  (`ubuntu-latest`, 45 min, **no secret**) installs the OCCT libraries with
  `ci.yml`'s apt hardening verbatim and runs
  `pytest -q -n 2 --dist loadscope tests/test_bench_*.py
  tests/test_prd024_acceptance.py` — the PR-blocking half, AC1 included.
  `guard` answers "is `ANTHROPIC_API_KEY` configured?" into a job output and
  emits a **visible `::notice::`** when it is not; it exists because the
  `secrets` context is unreadable from a job-level `if:`, and a silently
  skipped benchmark is indistinguishable from one that scored zero. The guard
  takes the secret through `env:` rather than interpolating `${{ secrets.… }}`
  into the shell body — that interpolation is textual substitution, so a value
  carrying a quote or a backtick would be *executed* instead of tested; only
  its emptiness is read, and it is never printed. `builtin`
  (`macos-latest`, 60 min) runs `bench run --set fast --agent builtin` then
  `bench report --baseline … --epsilon 0.05`, whose exit code is AC5's gate,
  and uploads `bench-out` `always()`. It is a **separate file** because
  `ci.yml` is asserted byte-wise (D20), and its `if:` ANDs three conditions
  (see Fix round 1): a fork PR must never reach a job that holds a secret *and*
  runs agent-authored Python (`geometry-ci.yml`'s rule), and the paid run stays
  on `main` even though `on.push` covers `roadmap` for `selftest`'s sake.

- **`benchmarks/examples/submission-mfd-001/` (new).** A `project.json` and one
  `parts/spacer_plate.py` — what an external agent hands in, nothing else.
  Deliberately imperfect in two realistic ways: R4 corner rounds instead of the
  drawing's R5, and Ø6.6 holes (the ISO 273 medium clearance for M6) instead of
  the Ø6.0 THRU it dimensions. Scores **0.9959**: `built`/`valid`/`specs`/
  `metrics` all 1.0 and the whole deviation on `geometry` (0.991873 — a 189 mm³
  symmetric difference against a 23 239 mm³ union). Its `README.md` names the
  task and the command that reproduces the table.

- **`docs/bench.md` (new).** Mirrors `docs/geometry-ci.md`'s shape: two rules to
  read the feature by (the zero rule; script-is-the-solution /
  STEP-is-the-datum), what is and is not measured (no LLM judging anywhere),
  the four commands with their flag table and the exit-code matrix, the task
  bundle and `task.json`, the rubric's re-binding of `SPECS` and the
  rubric-ownership rule, metric windows, every subscore's computation, the IoU
  handler's four constraints, the total and `weights_effective`, `score.json`
  and the six determinism rules, `bench run`'s budgets/transcripts/results
  layout, `bench report` and the baseline gate, `bench publish`'s five
  disclosure rules **including D24's row-relative narrowing of rule 4**, the
  external-agent MCP walkthrough worked through the checked-in submission, the
  eleven-point authoring checklist and the `author.py` commands, the honest
  non-guarantees (the monkeypatch note, per-task deltas not gated, frame
  ambiguity, the contamination stance), the CI split, the Phase 2/3 seams and a
  code map.

- **Surrounding docs.** `docs/agent-api.md` gains a Conventions bullet stating
  that the bench adds no tool/route/event and *why* `iou` is kernel-internal.
  `docs/architecture.md` gains an `agentcad/bench/` component row, `bench` in
  the handler-pack list, and an "AgentCAD-Bench" section on the muzzled-copy
  scorer and `_build_service(examples=False)`. `docs/geometry-ci.md` gains the
  sibling-command cross-reference. `AGENTS.md` gains a fourteen-bullet "Bench
  gotchas (PRD-024)" section and a `docs/bench.md` line under "Where to read
  more". `CLAUDE.md` gains one condensed trap bullet and the doc pointer.

- **`.dockerignore`** now excludes `benchmarks/` and `out/` (D22): the task
  tree is resource data, not runtime payload, and a bench run's results and
  transcripts must not land in the image. `.gitignore` excludes `out/` for the
  neighbouring reason — a results directory is evidence to publish
  deliberately, never a tree to commit by accident.

## Fix round 1 (review of 1ae80d1)

- **The doc said the opposite of the code about reviewer comments.**
  `docs/bench.md` claimed the weight-override rationale in `prompt.md` is
  "visible to the agent — that is accepted". It is not: `tasks.prompt_text`
  runs `strip_reviewer_comments`, which removes every `<!-- … -->` block (and
  the blank run it leaves) before the prompt reaches the model, on purpose —
  telling an agent which subscore is unweighted tells it how it is marked.
  The paragraph is inverted and now says the trap out loud (**rationale
  written as prose would reach the model**, and assets are attached verbatim
  because an SVG's comments are part of the drawing), and authoring checklist
  item 4 carries the same clause.

- **The paid `builtin` job could fire on a push to `roadmap`.** `on.push`
  covers `[main, roadmap]` so that `selftest` runs on both, but the job's `if:`
  excluded only `pull_request` — wider than design §13's "schedule, dispatch
  and main". Added `&& github.ref == 'refs/heads/main'`. A scheduled run always
  carries the default branch so the ref test costs nothing there; a manual
  `workflow_dispatch` must now be launched from main, which is deliberate. The
  acceptance test **pins the whole condition string** rather than probing for
  substrings — `A || B` contains both needles and would have passed a
  membership test while running the paid job on either half. Related:
  `cancel-in-progress` is now `${{ github.event_name == 'pull_request' }}`,
  because cancelling an in-flight paid run burns the spend and leaves no
  result.

- **The `assemble_and_clear` limitation is now disclosed in the doc.** A new
  bullet under "What this does not guarantee": the rubric a v1 assembly task
  can express is one-sided (`check_clearance(min_mm=…)` is a floor) and metric
  windows are keyed to a **part**, so nothing in the rubric sees placement — a
  candidate that creates every instance and parks them far apart satisfies the
  `specs` and `interference` channels in full. `check_stackup` cannot close it
  (it measures tolerance accumulation along a *mate* chain; these instances are
  placed). Closing it needs a max-clearance/placement check in
  `toolkit/specs.py` and a v2 task set.

- **Minors.** AC1 now asserts `total == 1.0` exactly (the document is rounded
  to six decimals before it is written, so a tolerance would hide a rubric a
  hair off from the thing it grades — all 25 return the literal float).
  Checklist item 8 states the datum rule (**required whenever `geometry`
  weight > 0**, which the loader enforces; optional below it — `fix_005` ships
  one at weight 0.00, the `asm_*`/`opt_*` bundles declare `steps: {}` because
  generating them cost 5.2 MB nothing would open). `out/` added to
  `.gitignore` (the doc tells users to `--report out/`). The guard's notice now
  distinguishes a **fork pull request** — where GitHub withholds every secret
  by design — from a genuinely unconfigured key, so a contributor is not sent
  looking for a setting that is not theirs to set. `docs/bench.md`'s publish
  rule 1 no longer lists `notes` as required (it is not in
  `REQUIRED_ROW_KEYS`) and now names the type of each key. The test map gains
  `tests/test_bench_tasks_fix_asm.py`. The example's README shows the CLI's
  `wrote …/score.json` line, and both it and `docs/bench.md` now state the
  geometry arithmetic correctly: the two errors pull opposite ways (~143 mm³ of
  material the reference keeps and the candidate removes, ~46 mm³ the reference
  removes and the candidate keeps), and it is that **189 mm³ symmetric
  difference** over a 23 239 mm³ union that costs the 0.0081 of IoU.

- **A full-suite count guard, in the house style.**
  `test_the_close_out_changelog_cites_a_full_suite_count` mirrors
  `tests/test_prd006_acceptance.py`'s `test_ac8_the_full_suite_count_is_cited`:
  it finds this entry by **slug** (the repo renumbers changelogs on a merge
  collision) and requires 4–6 digits immediately before the word `passed`.
  **It is red until the placeholder below is filled in**, which is the point.

## Files

- `tests/test_prd024_acceptance.py` — new (AC1, the drift check, AC6, AC9, the
  workflow assertions, the doc cross-references)
- `.github/workflows/bench.yml` — new (`selftest` / `guard` / `builtin`)
- `benchmarks/examples/submission-mfd-001/{project.json,parts/spacer_plate.py,README.md}`
  — new
- `docs/bench.md` — new
- `docs/agent-api.md`, `docs/architecture.md`, `docs/geometry-ci.md`,
  `AGENTS.md`, `CLAUDE.md` — cross-references and the gotcha/trap entries
- `.dockerignore` — `benchmarks/`, `out/`
- `.gitignore` — `out/`

## Notes

**Test evidence.**

```
$ uv run pytest -q tests/test_prd024_acceptance.py
59 passed in 69.05s

$ uv run pytest -q -n 2 --dist loadscope tests/test_bench_*.py tests/test_prd024_acceptance.py
264 passed in 70.21s
```

(The second command is exactly what `bench.yml`'s `selftest` job runs; the
45-minute budget is an order of magnitude of headroom over the measured 70 s.)

Full suite: `make test — 4703 passed, 36 skipped` (measured on the branch
after this fix round; the count guard
`test_the_close_out_changelog_cites_a_full_suite_count` is the same evidence
check PRD-004/006/008/011/012 close out with, and it stays red on a
placeholder by design).

**The one deliberate narrowing of the drift check.** The IoU half runs only
where the datum is actually scored (a non-zero `geometry` weight); the volume
and bbox comparison runs for every datum. `fix_005_invalid_shell` is the
exception, and it is the product finding this PRD already recorded rather than
a datum that drifted: two STEP imports of its swept pipe surface have identical
volume to fifteen digits (21711.685196909326 mm³ on both sides) and intersect
at **0.0 mm³**. That is precisely why the task weights `geometry` 0.00, argued
in its own `prompt.md`. Asserting an IoU there would assert a defect the bench
fenced, not the drift the test exists for — and the volume/bbox comparison
still catches a datum that moved.

**Measured, not asserted.** Two numbers in `docs/bench.md` are real runs, not
estimates: the example submission's 0.9959, and — as the counter-example for
why every prompt states its datum in words — the same script with the plate's
corner at the origin instead of its centre, a dimensionally perfect part in the
wrong pose, which measures `geometry` **0.1369** and totals **0.5684** with
every other subscore still 1.0.

**Not touched.** `docs/roadmap.md` (the PRD-024 row already reads correctly;
the status flip and the PRD move are the close-out's), `ci.yml`, and every
`agentcad/bench/*.py` module.
