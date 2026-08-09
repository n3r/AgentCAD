# 0026 — v2 Wave 1 complete: kernel pool + tool-count test updates

- **Commit:** cbca865
- **Date:** 2026-08-09
- **Author:** Claude Fable 5

## Summary
Closes v2 Wave 1 by adding a multi-worker `KernelPool` (a drop-in for the single
`KernelClient`) that parallelizes multi-part rebuilds, plus config/CLI wiring
and updated tool-count assertions now that the v2 packs raise the surface from
17 to 25 tools.

## Changes
- **`KernelPool`** (`kernel/pool.py`): implements the same surface the service
  uses (`start`/`stop`/`alive`/`request(..., affinity=)`) over N `KernelClient`
  workers. Requests route by affinity (`hash(part_id) % N`, keeping a part on a
  warm worker) or round-robin when unkeyed; workers spawn lazily (only the first
  warmed on `start`); size 1 behaves exactly like a single client. Reports
  2.4–3.6x on batch builds.
- **Config** (`config.py`, `get_kernel_pool_size`): reads
  `AGENTCAD_KERNEL_POOL_SIZE` env, then a `kernel_pool_size` config key,
  defaulting to `min(3, cores // 3)` (memory, ~0.5 GB/worker, is the limit).
- **CLI wiring** (`cli.py`): `_build_service` picks `KernelClient` for size 1,
  else a `KernelPool(size=...)`.
- **Tool-count tests**: assertions loosened from `== 17` to `>= 25` (17 core +
  v2 packs) across `test_chat`, `test_mcp`, `test_server`, and `test_tools`;
  `test_mcp`/`test_tools` also assert the new tool names
  (`import_cad_file`, `solve_sketch`, `set_mate`, `list_materials`,
  `generate_drawing`, `analyze_part`) are present.

## Files
- `agentcad/kernel/pool.py` — new affinity-routed multi-worker pool
- `agentcad/config.py` — `get_kernel_pool_size` (env/config/heuristic default)
- `agentcad/cli.py` — select KernelClient vs KernelPool by size
- `tests/test_pool.py` — parallel byte-identical builds, affinity routing, size-1 equivalence, respawn-after-crash, service-with-pool
- `tests/test_chat.py`, `tests/test_mcp.py`, `tests/test_server.py`, `tests/test_tools.py` — tool-count assertions raised to ≥25 + new tool names

## Notes
Each underlying client still serializes its own in-flight request, so the win is
cross-part concurrency, not per-request speedup. Full suite: 129 passed, 1
skipped (FEM without the extra).
