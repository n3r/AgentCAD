# 0185 — Full test gate 3× faster: per-part loadscope classes + 8 xdist workers

- **Commit:** pending
- **Date:** 2026-08-17
- **Author:** Claude (with Nikita Fedorov)

## Summary

`make test` (the complete gate, exhaustive included) drops from **1609.9 s
(26:49)** to **522.0 s (8:42)** on a 10-core/32 GB machine — same tests, same
tiers, same budgets. The suite had tripled since changelog 0062 fixed two
xdist workers, and `tests/test_examples.py` had become a single 1080 s
`loadscope` scheduling unit (42 % of all measured test time) that no worker
count could beat.

## Changes

- **`tests/test_examples.py` restructured** from one parametrized module into
  `loadscope`-schedulable classes: one per small example, `TestEngineCore`
  (engine defaults + interference + STEP), and `TestEngineSweep0..5` part-
  chunk classes **generated** from `ENGINE_SWEEP_CHUNKS = 6` (a rebalance is
  a one-constant change that cannot silently drop a chunk — the coverage
  test pins the tiling `{(chunk, of)}`). Coverage per part is unchanged:
  defaults-valid, min/max/choices sweep, interference, STEP.
- **Per-part parametrization is load-bearing, not cosmetic** (adversarial
  review finding, confirmed against xdist 3.8 source): loadscope refills a
  worker whenever its pending-*test* count drops to ≤ 2, so a 1-test sweep
  class lets a second ~200 s engine unit pile onto the same worker
  (simulated: 620–830 s walls in ~86 % of runs). With 5–9 tests per class
  the watermark protects the unit; part ids come from the committed
  `project.json` at collection time so all workers collect identical ids.
- **New guards the old module lacked**: `test_every_example_is_covered`
  derives coverage from the classes themselves (a new example, a deleted
  class, or a botched rebalance goes red; the old discovery loop just went
  silently green), asserts every example has ≥ 1 part (per-part parametrize
  would otherwise turn an empty manifest into silent skips), and the sweep's
  baseline-restore `set_params` is now asserted (the old ordering validated
  post-sweep state implicitly via interference-after-sweep; the new classes
  run interference before the sweep, so the assert carries that property).
- **`Makefile`**: `PYTEST_PARALLEL ?= -n auto --maxprocesses=8
  --dist loadscope --no-loadscope-reorder`. `-n auto` = physical cores
  (psutil installed), capped at 8 ≈ 8–10 GB of worker+kernel pairs; `?=`
  keeps it overridable per machine. `--no-loadscope-reorder` is measured,
  not theoretical: the default count-descending queue sort started the
  few-test engine classes mid-queue and run 1 landed at 631 s with a ~410 s
  sweep chunk ending exactly at the wall; collection order + K=6 landed
  522 s.
- **`tests/test_packages_gate.py`** gains a module `timeout(600)` — its
  heaviest setup measured 55 s at `-n 2`, sitting under the global 120 s
  pytest-timeout with no contention margin at 8 workers. Headroom in the
  existing convention (`test_checks_pipeline` et al.), not a loosened budget.
- **Docs**: AGENTS.md testing bullet + quick-start comments and README's
  "two-worker suite" line now describe auto-scaled workers and the
  `PYTEST_PARALLEL` override (grep-gated: no stale "two-worker"/"-n 2" text).
- **CI untouched** (`.github/workflows/ci.yml` still `-n 2`): its runners
  were not measured here. Honest accounting: the nightly exhaustive job pays
  the split's duplicated cold builds (~137 s CPU) without gaining balance at
  2 workers; a CI worker bump is the follow-up that more than wins it back,
  validated by CI itself.

## Measurements (this machine, 2026-08-17)

| run | config | result |
| --- | --- | --- |
| baseline | `-n 2 --dist loadscope` | 3229 passed, 7 skipped, **1609.90 s**; CPU 3462 s |
| run 1 | 8 workers, K=4, default reorder | 3310 passed, 7 skipped, **630.97 s** (tail: ~410 s clustered chunk) |
| run 2 | 8 workers, K=6, `--no-loadscope-reorder` | 3310 passed, 7 skipped, **522.00 s**; CPU 3266 s |
| run 3 | same (confirmation) | 3310 passed, 7 skipped, **519.25 s** |

Test count 3229 → 3310 is arithmetic, not new coverage: −20 old example
tests, +101 (the same checks as per-part ids, +1 coverage guard, and the
engine sweep as 33 per-part tests). `make test-fast`: 2665 passed in 111.9 s
(was ~305 s). Flake watch across all runs: `test_sketch_bench` FR6 budgets,
both packet "warm < 10 s" gates, and a grep for pytest-timeout kills — zero
hits. Contention-sensitive wall-clock budgets held at 8 workers.

## Files

- `tests/test_examples.py` — class-per-example + generated engine sweep
  chunks, per-part parametrization, class-scoped `service`/`example`
  (`@classmethod` fixtures — pytest 9 deprecates instance-method class-scoped
  fixtures), coverage/tiling guard
- `tests/test_packages_gate.py` — module `timeout(600)` contention headroom
- `Makefile` — `PYTEST_PARALLEL` default: 8-capped auto workers, no reorder
- `AGENTS.md`, `README.md` — testing docs match the new scheduling
- `docs/superpowers/specs/2026-08-17-test-suite-speed-design.md`,
  `docs/superpowers/plans/2026-08-17-test-suite-speed-plan.md` — design,
  plan, adversarial-review record, execution notes

## Notes

- Reviewed by a 22-agent adversarial workflow (5 lenses; 14 confirmed
  findings folded in, 3 refuted). The load-bearing ones: the refill-watermark
  /queue-reorder blocker, the literal-set coverage test, reverse-MRO
  collection order (base order `(_SweepTest, _BuildAndAssemblyTests)` keeps
  defaults before sweep), the unasserted restore, and the packages_gate
  timeout cliff.
- Known residual: `stud_set` (190 s) and `intake_manifold` (170 s) are
  congruent mod 6 in sorted order, so they still share Sweep3 (~360 s). With
  the early start it no longer sets the wall (~20–40 s of headroom left on
  the table); if engine grows, re-check chunk sums in `--durations` before
  reaching for K=8.
- The kernel remains one subprocess per xdist worker (session fixture);
  8 workers ≈ 8 kernel spawns ≈ +100–150 s CPU total, absorbed by the win.
- Follow-ups, deliberately not in this commit: CI worker bump (validate on
  CI runners), second-tier work reduction (`test_proposals`/`test_packet`
  template fixtures, checks/packages CLI spawn consolidation — the cold
  caches and real CLI spawns there are load-bearing test semantics).
