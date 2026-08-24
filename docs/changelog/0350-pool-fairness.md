# 0350 — PRD-005 slice 7: per-tenant fair kernel scheduling

- **Commit:** pending
- **Date:** 2026-08-24
- **Author:** Claude (Opus subagent) / Nikita Fedorov

## Summary
FR11: `KernelPool.request` gates entry per tenant — a bounded in-flight
cap (`max(1, size-1)`), a FIFO waiter queue (depth 32, 300 s wait
ceiling), and a round-robin drain across waiting tenants — with affinity
keys namespaced `org/ws:` so cross-tenant part-id collisions never share
a shape-LRU slot name. Local mode is byte-for-byte the historical line.

## Changes
- Tenant read via a `tenant_provider` module attr (lazy cached import of
  `tenancy.current_tenant` — the `kernel/sandbox.py` lazy-core-import
  discipline; the one eager import is `RateLimitedError`, stdlib-only,
  service-side half of the kernel package).
- `KernelBusyError(RateLimitedError)` → wire type **`kernelbusy_error`**
  (the house one-word derivation — not `kernel_busy_error`), HTTP 429
  through the existing isinstance walk with zero core edits; details
  carry tenant/in_flight/queued/limits/`retry_after_s`. Chosen over a
  503 on model.py's own documented split (a 429 clears when a slot
  frees) and the `share_build._inflight_slot` precedent.
- The gate decides **entry only** — each worker's single-in-flight lock
  still serializes; the drain increments in-flight before waking a
  waiter so a slot cannot be stolen in between; `None` affinity stays
  `None` (round-robin traffic must not collapse onto one hot key).
- Honest limits, documented in code/tests: namespacing is cache hygiene,
  not isolation (buckets still collide on small pools — statistical +
  deterministic tests); a nested same-thread `pool.request` would
  deadlock at cap 1 (no such call site exists; noted, not defended);
  the wait ceiling can refuse a legitimately long queue (a refusal
  beats an unbounded parked thread; instance-overridable).

## Files
- `agentcad/kernel/pool.py` — extended
- `tests/test_pool_fairness.py` — new (16 tests; flood-vs-quiet bound,
  cap, overflow wire type, local-mode oracle equivalence, 8×50 thread
  storm, run 5× flake-free)

## Notes
`make test` — 7002 passed, 51 skipped (12:41); non-passing were the documented families only: the pre-existing prd028 AC6 local solver timeout (skips on CI) and supervisor/share_isolation load flakes — 30/30 green in isolation.
