# 0011 — Apply confirmed review findings (backend) and add user guide

- **Commit:** 3e92168
- **Date:** 2026-08-08
- **Author:** Claude Fable 5

## Summary
Backend hardening from a code review pass — input validation, atomic/robust
caching, race-free reads, export timeouts, chat-turn serialization, and a
same-origin/host-allowlist guard — plus a new end-user guide.

## Changes
- **`set_params` validation** (`core/service.py`): names are checked against the
  script's `PARAMS` spec *before* anything is written (unknown names raise with
  `{unknown, known}`; a broken script that won't load is rejected); a `null` value
  now removes an override instead of erroring. Nothing is written on bad input
  (spec §8). `core/tools.py` and `docs/agent-api.md` document the new contract.
- **Race-free reads** (`core/service.py`): `get_metrics`, `mesh_summary`, and the
  new `mesh_info` return data from the build *result* instead of re-reading the
  shared `_status` dict, so a concurrent delete can't `KeyError`. `_ensure_built`
  now returns `cache_key`; `ensure_mesh` delegates to `mesh_info`.
- **Cache integrity**: the metrics sidecar is written via `ProjectStore._atomic_write`;
  a corrupt sidecar (bad JSON / missing key) is unlinked and the part is rebuilt
  from the kernel rather than serving garbage.
- **Kernel/export** (`kernel/worker.py`): `_export_shape` writes to a temp file with
  the same suffix then `os.replace`s it, so a killed export never leaves a torn
  file; `export_part` passes a 300s kernel timeout for from-scratch rebuilds.
- **Negative-caching**: `_params_spec` caches a `None` result on `KernelError`, so a
  broken/hanging script is inspected at most once per content hash instead of on
  every read.
- **Events**: `project_changed` is now published on part create, delete, and
  script/label/material update (not just assembly edits).
- **Chat turns** (`agent/chat.py`): serialized per project with an `asyncio.Lock`
  (user message appended inside the lock) so concurrent `POST /api/chat` calls
  can't interleave histories and break tool_use/tool_result pairing;
  `_repair_history` injects synthetic error tool_results if a turn dies mid-loop;
  `chat_tool_result` events now carry the result JSON truncated to 2000 chars.
- **Origin guard** (`server/app.py`): `local_origin_guard` middleware plus a
  `/ws` check enforce a local Host allowlist (DNS-rebinding defense) and exact
  Origin match on browser requests (cross-origin CSRF defense); non-browser
  clients (no Origin) pass. `create_app` gains `extra_allowed_hosts`.
- **Docs**: new `docs/user-guide.md` (~350 lines) covering starting the app,
  workbench, sidebar, viewport, inspector, agent panel, examples, file layout,
  shortcuts, and troubleshooting.

## Files
- `agentcad/core/service.py` — set_params validation, race-free reads, atomic/robust cache, project_changed events, negative spec cache
- `agentcad/core/tools.py` — set_params tool description update
- `agentcad/kernel/worker.py` — atomic export via temp-file + os.replace
- `agentcad/server/app.py` — host allowlist + same-origin guard (HTTP + WS), mesh_info route
- `agentcad/agent/chat.py` — per-project turn lock, history repair, truncated tool-result event
- `docs/agent-api.md`, `docs/user-guide.md` — docs
- `tests/test_chat.py`, `tests/test_server.py`, `tests/test_service.py` — added coverage

## Notes
The origin guard is aimed at a localhost-only, unauthenticated API; it is not a
substitute for auth if the server is ever exposed. `extra_allowed_hosts` exists so
non-default binds can still be reached.
