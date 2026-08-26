# 0362 — Bench `generate_from_prompt` category + loop-vs-one-shot delta (PRD-018 S5)

- **Commit:** pending
- **Date:** 2026-08-25
- **Author:** Nikita Fedorov

## Summary
PRD-018 Slice 5 (AC8): a new bench category `generate_from_prompt` whose
candidate is produced by the multi-turn generation loop
(`agent/generate.run_generation`) instead of bench's single-turn runner, scored
by the existing `scoring.Scorer` against a frozen rubric, and compared against a
one-shot baseline. The loop-minus-one-shot delta is written per task and
surfaced in `bench report`.

## Changes
- **`agentcad/bench/tasks.py`** — split the category tuple into `V1_CATEGORIES`
  (the five PRD-024 `bench-v1` categories) plus the new
  `GENERATION_CATEGORY = "generate_from_prompt"`; `CATEGORIES` is their sum, so
  the loader/report treat the new category like any other while the v1 self-test
  can still name "the original set". No change to the bundle format — a
  generation task is the same prompt + frozen rubric SPECS + reference STEP +
  metric windows.
- **`agentcad/bench/generation.py`** (new) — the loop-as-candidate machinery:
  `run_loop_submission` drives `run_generation` over a task's prompt and returns
  the best candidate; `write_loop_submission` "accepts" it as a plain project at
  the task's target part id (a candidate that produced nothing writes a manifest
  naming the part with no file — a measured zero, never an error);
  `generation_delta` is the pure `loop − one-shot` per subscore + total (a delta
  is `null` unless BOTH sides measured the subscore — an excluded side is never
  subtracted); `run_one_generation_task` runs both halves, scores both with the
  shared `Scorer`, and writes `score.json` (the loop's, canonical),
  `oneshot_score.json`, `generation.json`, `run.json`, `transcript.json` and
  both submissions. Test seams `LOOP_CLIENT_FACTORY` / `ONESHOT_CLIENT_FACTORY`
  (the `runner.CLIENT_FACTORY` precedent); the one-shot seam falls back to the
  runner's; `require_generation_agents` refuses before the kernel spawns.
- **`agentcad/bench/cli.py`** — `_cmd_run` routes a `generate_from_prompt` task
  through `generation.run_one_generation_task`, gating its factories up front
  only when the selection actually contains one.
- **`agentcad/bench/report.py`** — `aggregate` reads a `generation.json` sidecar
  beside a score and adds an additive, guarded `generation` block (top-level and
  on the task row); `render_markdown` renders a "Generation vs one-shot (AC8)"
  table when present. A run with no generation task writes no `generation.json`,
  so every other report is byte-for-byte unchanged (AC3).
- **`benchmarks/tasks/generate_from_prompt/gfp_001_shim_bracket/`** (new) — one
  authored bundle: a 60×24×4 mm rounded shim with a central Ø8 bore, a rubric
  (`check_valid` / `check_wall` / `check_bbox`), a reference STEP built via
  `bench.author step`, and metric windows on bbox/volume/n_solids (mass dropped
  so the score is material-independent). Reference scores exactly 1.0 (AC1) and
  the STEP matches its script (D9 drift check).
- **`tests/test_bench_generation.py`** (new) — offline, fake-client: the six
  subscores on the bundle, byte-stable score for a fixed fake, the
  deleted-part → `specs` zero with `status: "ok"`, the loop-beats-one-shot delta
  computed + reported through `bench report`, the delta's honesty on an excluded
  subscore, the full `main()` path, and the live gate (refused without a key).
- **`tests/test_bench_tasks.py`** — the three v1 count-guards
  (five-per-category / one-per-category-in-fast / category default weights) now
  scope to `V1_CATEGORIES` and assert the generation category separately.

## Files
- `agentcad/bench/tasks.py` — `V1_CATEGORIES`, `GENERATION_CATEGORY`, `CATEGORIES`
- `agentcad/bench/generation.py` — new module (loop candidate + AC8 delta)
- `agentcad/bench/cli.py` — `_cmd_run` generation routing
- `agentcad/bench/report.py` — additive `generation` block + markdown table
- `benchmarks/tasks/generate_from_prompt/gfp_001_shim_bracket/**` — the bundle
- `tests/test_bench_generation.py` — new offline tests
- `tests/test_bench_tasks.py` — v1 guards scoped to `V1_CATEGORIES`

## Notes
- Fail-honest: this module never scores — it produces two submissions and hands
  them to the existing `Scorer`, whose rule-2 semantics (`error` = harness,
  candidate-caused = measured zero) are inherited unchanged. The rubric is still
  injected into a copy and re-binds SPECS, so a candidate's own SPECS inflate
  nothing.
- Offline determinism: both factories are seams; `score.json` and
  `generation.json` are byte-stable (the scorer rounds/strips, the delta is a
  pure function of two such scores). No fan-out, no `--jobs`; `score.json`
  carries no timestamp/host/path. The live half rides the existing bench key
  gate and is skipped without `ANTHROPIC_API_KEY`.
- Follow-up (Slice 7 owns docs): a `docs/bench.md` section on the generation
  category and the AC8 delta is not written here.

## Notes
`make test` — 7220 passed, 51 skipped (13:15); non-passing were the count-guards (this wave cites the count) and the documented prd028 FEM + supervisor/test_server load timeouts (pass in isolation).
