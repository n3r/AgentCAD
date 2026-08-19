# 0268 — PRD-024 final whole-branch review: the write grant, the prompt leak, and fifteen smaller fixes

- **Commit:** pending
- **Date:** 2026-08-19
- **Author:** Claude (agent)

## Summary

The final whole-branch review of PRD-024 (two independent reviewers, one fix
pass) found two Critical defects and fifteen smaller ones (17 in total). The Criticals: `bench
run`/`bench score` handed the confined worker a **write** grant on the task
tree and on the submission — the worker that executes candidate-authored Python
— and the external-submission walkthrough in `docs/bench.md` told evaluators to
`cat prompt.md`, which leaks the reviewer-only HTML comments (reference
parameters, threshold rationale) that the built-in runner strips. Both are
closed, along with a NaN that could have scored a degenerate shape `iou: 1.0`, a
build timeout and a build crash that were indistinguishable from a build
failure, harness meta in 29 task starters, a task id printed on two shipped drawings, and the missing
disclosures.

## Changes

### Critical — the write grant (A1)

- `agentcad/bench/cli.py` `_cmd_run` and `_cmd_score`: `extra_writable` is now
  **only the `--work-dir`**. It used to carry `tasks_base` (`_cmd_run`) and
  `submission` + `task.root` (`_cmd_score`). `extra_writable` flows to
  `_build_service` → `KernelClient(writable_dirs)` → the Landlock/seatbelt
  **write** rules of the worker that runs the candidate's own part scripts, so
  a candidate could have overwritten `reference/steps/<part>.step` before
  `_geometry` measured against it (a geometry 1.0 it wrote itself) and, on
  `bench run`, corrupted the maintainer's checked-in `benchmarks/` tree.
- Neither grant ever bought a read: reads are unrestricted in the `local`
  posture, and in `hosted` the read roots are a separate list that already
  includes `resource_root()` — the repo root, which holds `benchmarks/`
  (`kernel/sandbox_linux._read_roots`). The comments that claimed otherwise are
  replaced with the real reasoning.

### Critical — the prompt leak (B1)

- New subcommand **`agentcad bench prompt <task-id> [--tasks-dir DIR]
  [--json]`** (`agentcad/bench/cli.py`, `_cmd_prompt` + the parser block).
  It writes exactly `tasks.prompt_text(task)` to stdout — reviewer comments
  stripped, every asset inlined as text — exits 0/2, builds no service and
  starts no kernel. `--json` emits `{"task", "prompt", "assets"}`.
  `agentcad/cli.py` is untouched: the subcommand lives in `add_bench_parser`.
- `docs/bench.md` "Submitting from outside the repo" step 2 now uses it and
  says why `cat prompt.md` is wrong.

### Important

- **A2 — a build timeout and a build crash are named, and budget truncation is
  separated** (`agentcad/bench/scoring.py`). `_build_all` now takes the
  deadline and the notes list, and `_failed_build` classifies a not-`ok` build
  result from its error payload against `_NAMED_BUILD_FAILURES`:
  `timeout` → `state: "failed", reason: "build_timeout"` and
  `kernel_crash` → `state: "failed", reason: "build_crash"` — both measured
  zeros with the weights **not** renormalised — or `state: "error"` plus a note
  when a `--budget` has already expired, which is our truncation. Everything
  else stays `failed`/`build_failed`. `_geometry_part` returns a harness
  failure for a build whose state is `error`, so `built`, `valid`, `geometry`
  and `metrics` agree on one row.
  The crash lane is the one place rule 2's "a kernel that is gone" is decided
  the other way, and the orchestrator ruled it so: nothing else stops measuring
  when a build kills the worker — `core/specs.py` treats a mid-measurement
  `KernelError` as a row payload, so `SpecRunner.run` survives and returns real
  rows for every other part — so as an `error` a candidate with one
  reliably-crashing part and an otherwise-passing rubric banked a renormalised
  specs-heavy score (0.24 → 0.60 on an `mts`-weighted task). `_blames_harness`
  is unchanged and still answers `iou`/`check_interference` the other way,
  where a dead worker really does mean *that* measurement did not happen.
  Note for the record: a build **never** raises a `KernelError` —
  `service._build_with` catches it and answers `{"ok": False, "error": …}` —
  so the reviewer's premise that `_blames_harness` was classifying build
  timeouts was not the case; the payload type is the discriminator and that is
  where the fix went.
- **B2 — the two rendered drawings named the task.**
  `mfd_002_angle_bracket` and `mfd_003_head_flange` printed
  `bench_mfd_00N_…_reference / <part>` in the title block, handed to the model
  verbatim. Both assets are scrubbed to the bare part id, and
  `author.neutral_title(svg, project, part_id)` (new, called from
  `render_drawing` before `check_dims`) keeps future renders neutral. The
  product's own `tools_drawing` label is unchanged — it is right for a user
  whose project name is their own.
- **B5 — starter headers.** All 29 starter scripts under `mts_*`, `asm_*` and
  `opt_*` carried a six-to-nine-line header explaining the harness *and* the
  strategy ("the starter and the reference are the SAME script at different
  parameters…", "the rubric is injected from …"). `starter/` is copied verbatim
  into the agent's scratch project, so every byte of it is prompt. Each is
  trimmed to the one-line form the `fix_*` starters already used:
  `# Copied from examples/<...>.py into this project.` The full note stays on
  the reference side, which is where a maintainer reads it.
- **B3/B4/B6/B7 — `docs/bench.md` disclosures.** The swept-surface STEP-boolean
  product finding (why `fix_005` weights `geometry` 0.00) and the
  `_view_bounds` curved-silhouette finding, both under "What this does not
  guarantee"; the build-timeout non-guarantee; what a generated sheet actually
  dimensions (overall extents + a hole callout) versus the full dimension set
  the prompt carries in words; `bench run`'s `ANTHROPIC_API_KEY` prerequisite
  and what `--model` accepts and defaults to; and the optimisation-category
  caveat (on `opt_001`/`003`/`004` the binding limit is the declared `PARAMS`
  range, not an engineering constraint; `opt_002`/`opt_005` are
  constraint-bound).

### Minor

- **A3** `agentcad/kernel/handlers/bench.py`: a `_finite(value, stage)` guard on
  both side volumes and on the per-rotation intersection total. A NaN volume
  walked through both clamps — `union <= 0.0` is false and `min(1.0, nan)`
  answers **1.0** — so a shape whose volume OCCT could not compute scored a
  perfect IoU. It is now `WorkerError(ERROR_KERNEL, …, {"stage": …})`, i.e.
  `status: "error"` one level up.
- **A4** the three copies of `_finite` (`scoring`, `report`, `publish`) are one
  `_json.is_finite_number`, imported as `_finite` at each site so no call site
  or behaviour moved.
- **A5** `.github/workflows/bench.yml`'s `selftest` job sets
  `AGENTCAD_EXPECT_SANDBOX: active`, `ci.yml`'s ubuntu row verbatim: this job
  scores candidate-authored Python in a confined worker, so a degraded sandbox
  must be red rather than silently skipped.
- **A6** `bench score --out`'s help names `bench run --report DIR` as the
  layout `bench report` reads.
- **A7** `_cmd_publish` records why the coverage roster is the shipped set
  regardless of a row's `task_set`, and what a second task set would need.
- **B8** the contamination bullet stops implying a rotation policy exists (it
  is Phase 3) and adds the observation that the scratch project is named
  `bench_<task_id>`, so a run is not blind.
- **B9** tests: one-`fast`-per-category (and `fast ⊂ core`); every task's
  weights equal the §7.6 category default except the two argued overrides,
  with the defaults written out in the test rather than read from a bundle;
  and the `selftest` job may not carry `-m "not slow"`.
- **B10** the subscore table says what a zero `specs` denominator does
  (`no_rubric_attached` / `nothing_measured`, both `0.0` with `status: "ok"`).

## Files

- `agentcad/bench/cli.py` — A1 (both `extra` lists), B1 (`prompt` subcommand,
  parser, dispatch, module docstring table), A6, A7.
- `agentcad/bench/scoring.py` — A2 (`_build_all` signature + `_failed_build`,
  `_geometry_part`'s harness arm), A4.
- `agentcad/bench/_json.py` — A4 (`is_finite_number`).
- `agentcad/bench/report.py`, `agentcad/bench/publish.py` — A4.
- `agentcad/bench/author.py` — B2 (`neutral_title`, wired into
  `render_drawing`).
- `agentcad/kernel/handlers/bench.py` — A3.
- `.github/workflows/bench.yml` — A5.
- `benchmarks/tasks/model_from_drawing/mfd_00{2,3}_*/assets/drawing.svg` — B2.
- 29 × `benchmarks/tasks/{modify_to_spec,assemble_and_clear,optimize_under_constraints}/*/starter/parts/*.py`
  — B5 (header only; no geometry byte moved).
- `docs/bench.md` — B1, B3, B4, B6, B7, B8, B10, A2 and the A1 flag-table note.
- `docs/architecture.md`, `CLAUDE.md` — the surface is five subcommands, and
  the bench bullet records `bench prompt` and the write-grant rule.
- `tests/test_bench_cli.py` — the two write-grant regressions and five
  `bench prompt` tests.
- `tests/test_bench_scoring.py` — five build-classification tests.
- `tests/test_bench_kernel_iou.py` — two non-finite guard tests, driven against
  `register()`'s own toolbox with stubs (no build123d script reliably produces
  a NaN volume).
- `tests/test_bench_author.py` — `neutral_title`, a rendered sheet that names
  no project, and a shipped-asset leakage test.
- `tests/test_bench_tasks_fix_asm.py` — the starter-leak list widened and
  applied to **every** category, plus the one-line-header shape.
- `tests/test_bench_tasks.py` — the `fast` set and the category weight vectors.
- `tests/test_prd024_acceptance.py` — `not slow` and the sandbox env on the
  `selftest` job.

## Notes

**Evidence.**

- `uv run pytest -q tests/test_bench_*.py tests/test_prd024_acceptance.py`
  → `286 passed in 64.62s`.
- `uv run ruff check` on every touched module and test → `All checks passed!`.
- `uv run python -c "import yaml; yaml.safe_load(open('.github/workflows/bench.yml'))"`
  → parses.
- A3's guard is revert-proven: with the two `_finite` calls removed, both new
  handler tests fail (`DID NOT RAISE`).
- B5's headers change no geometry, re-proved by scoring three touched starters:
  `mts_001_thin_the_nozzle` **0.7582**, `opt_001_lightest_bracket` **0.8000**,
  `asm_003_bolted_joint` **0.2500** — all well under the 0.95 bar.
- `make test` — 4725 passed, 36 skipped in 10m20s (branch tip).

**The behaviour change worth flagging.** A build whose result is a harness
`error` — today only budget truncation, plus an exception class
`_blames_harness` did not anticipate — now excludes `geometry` instead of
scoring it 0.0, so all four build-derived subscores answer one row the same
way. Timeouts and crashes no longer reach that lane at all: they are the
candidate's measured zeros, which is what closes the renormalisation exploit
rather than leaving it to the note it used to carry.

**Deliberately not done.** The reviewer's suggested `Scorer(…,
build_timeout_s=…)` test hook was not added: the build ceiling is
`service._build_with`'s hard-coded 300 s and `core/service.py` is off-limits,
so the classification is unit-tested against a stubbed `_ensure_built` instead,
which is the seam that actually decides it. `core/tools_drawing.py`'s
`f"{project} / {part_id}"` label was not changed either — it is correct for a
product user, and only a bench asset has to be anonymous.
