# 0186 — CI runs test_sketch_bench as a serial tail after the parallel suite

- **Commit:** pending
- **Date:** 2026-08-17
- **Author:** Claude (with Nikita Fedorov)

## Summary

PR #16's macOS `pr` job missed the FR6 drag-frame budget twice in a row
(`test_sketch_bench.py::test_the_drag_frame_clears_the_fr6_budget[arc ring +
slot]`: 16.36 ms then 20.18 ms p50 vs the 16.0 ms budget). Both CI pytest
invocations now `--ignore` the bench module in the parallel run and execute
it serially afterwards (3.1 s solo), so the FR gate measures solver latency
instead of whatever OCCT build a shared 3-core runner co-scheduled beside it.
The budget itself is untouched.

## Changes

- `.github/workflows/ci.yml` — the `pr` step and the nightly `exhaustive`
  step each become two invocations: the existing `-n 2 --dist loadscope`
  run with `--ignore=tests/test_sketch_bench.py`, then
  `uv run pytest -q tests/test_sketch_bench.py` (serial; the module is
  `slow`-marked, so the `portability` jobs never ran it and are unchanged).

## Files

- `.github/workflows/ci.yml` — serial bench tail in `pr` and `exhaustive`
- `docs/superpowers/specs/2026-08-17-test-suite-speed-design.md` — the
  "CI untouched" decision superseded, with the measurements

## Notes

- Evidence this is contention, not a solver regression: the same test/param
  missed on **main's** nightly exhaustive job at 18.65 ms (Aug 14) and
  22.20 ms (Aug 16) before the 0185 split existed; locally the bench passed
  4/4 inside the 8-worker parallel bulk on an M-series machine at well under
  budget. The split (0185) did shift which loadscope scopes co-schedule with
  the bench at `-n 2` under xdist's default count-descending reorder, which
  is why the PR tier — previously green on main's pushes — started missing:
  hence a structural fix on CI rather than job reruns.
- Local `make test` deliberately keeps the bench inside the parallel bulk:
  4/4 green at 8 workers; if a developer machine ever flakes it, the spec's
  ladder (maxprocesses=6, then a local serial tail) applies.
- This also de-flakes the nightly exhaustive job, which additionally stops
  hitting the old engine-sweep 900 s timeout because 0185's chunks are each
  minutes, not tens of minutes.
