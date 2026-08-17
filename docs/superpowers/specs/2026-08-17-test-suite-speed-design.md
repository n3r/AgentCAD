# Test-suite wall-clock speedup — design

- **Date:** 2026-08-17
- **Status:** approved (autonomous session; measurements cited inline)
- **Problem:** `make test` (the complete gate) takes **1609.9 s (26:49)** —
  3229 passed, 7 skipped — at the current `-n 2 --dist loadscope` on a
  10-core/32 GB M-series machine. The suite tripled since the last speedup
  (changelog 0062: ~314 tests) and the two-worker cap no longer fits the
  hardware it runs on.

## Measurements (this machine, 2026-08-17 baseline run)

- Wall 1609.9 s; CPU 2267 s user + 1195 s sys ≈ **3462 s total work**. The
  two workers are near-perfectly packed (2 × 1610 ≈ 3220): the current config
  wastes almost nothing — it is simply starved for workers.
- Sum of junit per-test times: **2576 s**; the rest (~890 s CPU) is
  per-worker overhead (imports, kernel startups, collection).
- Largest `loadscope` scheduling units (module, or class within module):
  - `tests/test_examples.py` — **1079.9 s / 20 tests (42 % of test time)**;
    inside it the engine example alone: sweep 714 s, defaults 137 s, STEP
    export 80 s, interference 69 s ≈ **1000 s**.
  - Next: `test_checks_pipeline` 200 s, `test_proposals` 160 s,
    `TestPacketBuilder` (test_packet.py) 94 s, `test_prd001_acceptance` 70 s,
    `test_packages_gate` 59 s. Everything except examples is ≤ 200 s.
- Worker memory, sampled live: ~500 MB per heavy process (xdist worker or
  kernel subprocess) ≈ ~1 GB per worker pair. Eight pairs ≈ 8 GB — comfortable
  in 32 GB. The 0062-era memory caution does not bind on this hardware.

## Why the obvious knob is not enough (Amdahl)

`loadscope` pins each module (class, when tests sit in one) to a single
worker. With `test_examples.py` at 1080 s, **no worker count beats ~1080 s**
(1.5×). Splitting per example still leaves engine at ~1000 s (1.6×). The
engine example itself must be split before workers help.

## Approaches considered

- **A. Raise workers only** (`-n 8`): ceiling 1080 s → ~1.5×. Rejected as the
  sole lever.
- **B. A + split examples per example**: engine still one 1000 s unit → ~1.6×.
  Insufficient.
- **C. A + class-granular split of the examples suite, chunking the engine
  sweep (chosen)**: `loadscope` schedules classes independently, so
  restructure `tests/test_examples.py` into classes: one per small example,
  one engine "core" class (defaults + interference + STEP ≈ 286 s — the new
  tallest unit), and K=4 engine sweep-chunk classes (~714/4 ≈ 180 s each,
  parts assigned `sorted(part_ids)[i::4]`). Predicted wall at 8 workers:
  the naive bound `max(286 s, 2576/8 + per-worker overhead)` says 430–500 s,
  but a scheduler-aware simulation (xdist's count-descending queue reorder,
  refill watermark, contention at ~1.3 CPU-s per busy worker-second on 10
  cores) corrects this to **~450–545 s (7.5–9 min), ~3–3.6×**, with 430 s as
  a zero-contention floor. The ≤ 600 s success bar is set off the corrected
  number.
- **D. Reduce total work in the second tier** (template-copy fixtures for
  `test_proposals`/`test_packet`, consolidating `checks`/`packages` CLI
  spawns): deferred. The cold caches and real CLI subprocesses there are
  load-bearing test semantics (determinism and CLI contracts — see AGENTS.md
  traps); ceiling gain at 8 workers is only ~40–60 s of wall. Revisit with
  durations data after C lands.

## Design (approach C, as revised by the adversarial review)

1. **`tests/test_examples.py` restructure** (same file, same coverage):
   - Non-collected base classes hold the tests: `_BuildAndAssemblyTests`
     (per-part defaults-valid, interference, STEP export) and `_SweepTest`
     (per-part extremes sweep), over `_ExampleBase` with a **class-scoped**
     `service` + `example` fixture pair that copytrees the example (still
     ignoring `.cache`/`exports`) into a fresh `make_test_service` — class-
     scoped because two engine classes can share a worker, and one service
     cannot open two projects with the same name.
   - **Per-part parametrization is load-bearing, not cosmetic**: xdist's
     loadscope scheduler refills a worker whenever its pending-**test** count
     drops to ≤ 2, and xdist 3.8's `--loadscope-reorder` (default **on**)
     sorts the queue by test count descending. A 1-test sweep class both
     sorts to the queue tail and invites a second engine unit onto the same
     worker (simulated on the baseline data: 2–3 chunks serialize on one
     worker in ~86 % of runs → 620–830 s walls). With ~8–9 tests per chunk
     class the watermark protects the unit and it sorts earlier. Part ids are
     read from the committed `project.json` at collection time via
     `pytest_generate_tests`, so every worker collects identical ids.
   - `TestConstruction/TestFasteners/TestPrototyping/TestRocketry`:
     subclasses of `(_SweepTest, _BuildAndAssemblyTests)` — **base order is
     load-bearing**: pytest collects inherited tests in reverse MRO, and this
     order keeps defaults/interference/STEP running before the sweep, as the
     old module did. Marks unchanged (`integration, slow, timeout(900)`).
   - `TestEngineCore` (`exhaustive`): engine per-part defaults (33 tests —
     which also sorts it early under the count-descending reorder) +
     interference + STEP.
   - `TestEngineSweep0..3` (`exhaustive`): **generated** from
     `ENGINE_SWEEP_CHUNKS = 4` via `type(...)`, each sweeping
     `sorted(part_ids)[i::4]`, so a rebalance is a one-constant change that
     cannot silently drop a chunk. Sweep semantics per part are unchanged
     (set min/max/choices, assert ok + volume), **plus** the baseline-restore
     `set_params` is now asserted — the old ordering (interference after
     sweep) implicitly validated post-sweep state; the assert restores and
     strengthens that property. Each chunk pays one cold defaults-equivalent
     build per part it owns (bounded by the old defaults pass, ≈ +137 s CPU
     total).
   - **Coverage honesty**: `test_every_example_is_covered` derives the
     covered set **from the classes themselves** (both families — build and
     sweep), pins the sweep tiling
     `{(chunk, of)} == {(i, ENGINE_SWEEP_CHUNKS)}`, and asserts every example
     has ≥ 1 part (per-part parametrization would otherwise turn an empty
     manifest into a silent skip). Adding an example, deleting a class, or
     botching a rebalance all go red.
2. **Worker count and queue order**: `Makefile` gets
   `PYTEST_PARALLEL ?= -n auto --maxprocesses=8 --dist loadscope
   --no-loadscope-reorder` — `-n auto` resolves to **physical** cores (psutil
   is installed), so this machine gets 8 (capped) and small machines scale
   down; `?=` lets any machine override
   (`make test PYTEST_PARALLEL="-n 2 --dist loadscope"`).
   `--no-loadscope-reorder` is measured, not theoretical: with the default
   count-descending queue sort, the few-test engine classes start mid-queue
   and the first full run landed at 631 s with a ~410 s sweep chunk
   (round-robin had clustered stud_set 177 s + intake_manifold 139 s) ending
   exactly at the wall; collection order starts `test_examples.py` early
   instead, and `ENGINE_SWEEP_CHUNKS` went 4 → 6 to cap the worst chunk near
   its heaviest single part (~180 s).
   CI (`.github/workflows/ci.yml`) keeps `-n 2` — its runners were not
   measured here, and a worker bump stays follow-up. **Superseded in one
   respect by PR evidence**: the macOS PR job missed the FR6 drag budget
   twice in a row (16.36 / 20.18 ms vs 16 ms) after the split changed which
   scopes co-schedule with `test_sketch_bench` at `-n 2` under CI's default
   count-descending reorder — while main's nightly had already missed the
   same gate at 18.65 / 22.20 ms that week. Fix (the ladder's rung (b),
   applied to CI only): both CI pytest invocations `--ignore` the bench
   module and run it as a serial tail (3 s solo), so the FR gate measures
   product latency, not co-scheduled OCCT builds. Local `make test` keeps
   the bench in the parallel bulk — it passed 4/4 at 8 workers on this
   machine. Honest accounting stands: the nightly exhaustive job at `-n 2`
   pays the split's duplicated cold builds (~137 s CPU, ~1–2 min) without
   gaining balance — and in exchange its chronic engine-sweep 900 s timeout
   (two hits the same week) disappears, since no chunk approaches 900 s.
3. **Contention headroom**: `tests/test_packages_gate.py` (55 s test under
   the global 120 s pytest-timeout) gets a conventional module-level
   `timeout(600)` override — margin against 8-worker contention, same
   pattern as `test_checks_pipeline.py`/`test_prd001_acceptance.py`, not a
   loosened budget.
4. **Docs**: AGENTS.md testing section ("Two xdist workers…"), AGENTS.md
   quick-start comments (lines ~28/30, "two-worker suite"), and README's
   "two-worker suite" line updated; the edit gate is
   `grep -rniE "two.worker|-n 2" AGENTS.md README.md` returning zero stale
   hits.

## Risks and their handling

- **Wall-clock-budget tests under contention**: `test_sketch_bench.py`
  asserts p50 warm ≤ 16 ms / cold ≤ 250 ms; `test_packet.py` and
  `test_prd002_acceptance.py` assert "packet generates warm under 10 s"
  (measured 5.5 s at `-n 2`). Eight busy cores inflate these. **Gate:** run
  the full suite twice at the new config; the flake watch covers the budget
  asserts **and any pytest-timeout kill of a previously-passing test** (the
  nearest cliff was `test_packages_gate`'s 55 s test under the 120 s global
  timeout — pre-fixed with a module `timeout(600)`). On a flake, fall back
  per the ladder: `--maxprocesses=6`, then a two-phase `make test` (parallel
  bulk + tiny serial phase for timing-budget modules — noting the serial
  tail's own wall cost). Do not loosen the budgets themselves — they are FR
  gates. **Post-merge-attempt addendum**: the bench's drag-frame test missed
  on CI even serial and idle (16.17 ms p50 vs 16.0 on a budget that measures
  10.9 ms p50 locally and 2.9–3.2 ms on the reference M1 Max), so it now
  follows the FR6 measurement convention `test_prd009_acceptance.py` AC2
  already documents: the hard 16 ms bar reads the **fastest** frame (the one
  statistic scheduler noise can only worsen; a real regression has no fast
  frames), with the established `FR6_LOADED_SLACK = 4.0` ceiling on the p50
  so tail-only regressions stay visible. The budget constant is unchanged;
  correctness asserts (flips, residuals) are untouched.
- **Contention, not fixed overhead, is the real added cost of workers**: the
  baseline's ~890 s CPU beyond junit-summed test time is mostly concurrent
  kernel/sys work that *scales with busy workers* (~1.3 CPU-s per busy
  worker-second → ~10.4 demanded cores at 8 busy workers on 10). Per-worker
  fixed cost (kernel spawn + OCCT import + collection) is ~20–40 s. Both are
  priced into the 450–545 s prediction.
- **Chunk imbalance**: engine part costs are uneven (thread geometry —
  round-robin by sorted id may cluster the 13 threaded `*_set` parts). With
  per-part parametrization the durations report now shows cost *per part*,
  so imbalance is diagnosable; if a sweep chunk exceeds the ~286 s core
  class, rebalance by bumping `ENGINE_SWEEP_CHUNKS` (classes are generated —
  a one-constant change that the coverage test's tiling assert keeps honest).
- **Semantics**: no test is deleted, no marker tier changes, `make test`
  remains the complete gate including `exhaustive`; `test-fast`/`test-pr`/
  `test-sequential` keep their meanings and inherit the parallelism bump.
  Known deltas, all deliberate: per-part test ids replace whole-example
  loops; interference/STEP run before the sweep in combined classes (they
  used to run after); the sweep's baseline-restore is now asserted.

## Adversarial review record (2026-08-17)

A 22-agent review (5 lenses × find, then per-finding refutation) confirmed
14 findings, refuted 3, and filed 9 notes. Every confirmed finding is folded
into the design above; the load-bearing ones: the loadscope refill-watermark/
queue-reorder blocker (fixed by per-part parametrization), the literal-set
coverage test (fixed by deriving from classes + tiling assert), reverse-MRO
collection order (fixed by base order), the unasserted sweep restore (now
asserted), the `test_packages_gate` timeout cliff (module override), stale
"two-worker" text beyond the one AGENTS.md bullet (grep gate), and the
corrected performance model and nightly-CI accounting above.

## Success criteria

- `make test` green (cite counts) at the new config, **twice**, with no
  budget-test flakes and no new pytest-timeout kills.
- Full-suite wall ≤ ~600 s on this machine (predicted ~450–545 s), from
  1609.9 s — and `make test-pr` / `test-fast` also improve.
- Changelog entry with before/after numbers; AGENTS.md/README updated.
