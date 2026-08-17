# 0195 — 2026-08-17 — test_sketch_drag joins the CI serial bench tail

## Summary

`tests/test_sketch_drag.py::test_the_cached_block_is_measurably_cheaper` is a
wall-clock comparison (cached vs uncached drag block) that misses its margin
under parallel co-load. It cost PR #15 a full macOS CI rerun (8.55 ms vs a
7.08 ms bound while co-loaded), and the PRD-005a slice agent re-confirmed the
mechanism: 17 passed standalone, flaky under `-n` co-scheduling. PR #16 built
the serial bench tail for exactly this class and moved `test_sketch_bench.py`
into it; `test_sketch_drag.py` carries the same kind of assertion and was
left behind.

## Changes

- `.github/workflows/ci.yml`: both the PR job and the nightly ignore
  `tests/test_sketch_drag.py` in the parallel phase and run it in the serial
  tail alongside `test_sketch_bench.py`.

## Files

- `.github/workflows/ci.yml`
- `docs/changelog/0195-sketch-drag-serial-tail.md` — this entry

## Notes

CI-only; no product or test code changed. The last full-suite measurement is
0194's: `make test` — 3640 passed, 1 skipped in 660.31 s.
