# 0062 — Cut full-suite runtime with cancellable WebSockets and parallel tests

- **Commit:** pending
- **Date:** 2026-08-09
- **Author:** Codex (with Nikita Fedorov)

## Summary

The test path now avoids synchronous git history work outside history-focused
coverage, releases disconnected WebSockets immediately, reuses prepared CAD
projects without sharing mutable state, and runs the full suite with two xdist
workers. Against the 300-test pre-engine baseline, the full gate fell from
319.05 seconds to 73–76 seconds while remaining green with 5 expected FEM
skips; the sequential fallback fell to 131.74 seconds. After rebasing onto the
new exhaustive engine example, the full gate passed 314 tests with 5 skips in
1302.54 seconds, while the fast tier passed 279 with 5 skips in 21.81 seconds.

## Changes

- **WebSocket lifecycle**: the server waits for bus events and client
  disconnect concurrently, preserves the 20-second keepalive, and uses a queue
  sentinel to release the blocking event waiter immediately on teardown. The
  server regression test now requires disconnect cleanup in under two seconds.
- **History isolation**: ordinary tests construct services through
  `make_test_service`, which disables the synchronous git snapshot hook.
  `test_history.py` and the real MCP server retain production history behavior.
- **Fixture reuse**: analysis, motion, and tolerance-stack tests build a
  module-scoped project template and copy it per test, preserving mutation
  isolation while reusing scripts and geometry caches. Missing FEM extras now
  skip before any FEM project geometry is prepared.
- **Test tiers**: broad example, MCP, pool, sandbox, and timeout/restart
  coverage is marked `slow`/`integration`. `make test-fast` excludes slow
  coverage; `make test` remains the complete gate; `make test-sequential`
  remains available for process debugging.
- **Parallel execution**: pytest-xdist is locked as a development dependency.
  Local full tests and all three CI platforms use two workers with
  `--dist loadscope`, keeping module fixtures together and capping OCCT process
  growth. Before the engine-example merge the fast target completed at 269
  passed and 5 skipped in 16–18 seconds; on the rebased branch it completed at
  279 passed and 5 skipped in 21.81 seconds.

## Files

- `agentcad/server/app.py`, `tests/test_server.py` — cancellation-aware
  WebSocket delivery and teardown regression coverage
- `tests/conftest.py`, `tests/test_*.py` — history-free test services,
  copy-on-write heavy fixtures, FEM skip ordering, and test markers
- `pyproject.toml`, `uv.lock` — marker declarations and pytest-xdist dependency
- `Makefile`, `.github/workflows/ci.yml` — fast/full/sequential targets and
  two-worker full-suite execution
- `README.md`, `AGENTS.md` — contributor commands and test architecture

## Notes

The timing comparison includes a cold first OCCT import in the old baseline;
absolute results vary with filesystem and kernel cache warmth. Two workers are
deliberate: higher automatic fan-out can multiply OCCT memory use and worker
startup cost on developer laptops and CI runners. The engine example added on
main after the original measurement now dominates the complete gate with its
real-thread parameter sweep, interference check, and STEP export; it is marked
slow/integration and remains part of `make test`.
